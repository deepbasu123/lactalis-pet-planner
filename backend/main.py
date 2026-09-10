"""backend/main.py

FastAPI application for the Lactalis PET Line Planner.

Dataset loading
---------------
When PET_LIVE == "1" the dataset is built by reading the six UC tables via
db.read_table (blocking SDK calls). Otherwise data_gen.generate() produces a
fully deterministic synthetic dataset -- no workspace connection required.
The loaded dataset is cached at module level; call refresh_dataset() to
invalidate the cache (e.g. after a save that writes back to UC).

Blocking-call safety
--------------------
All endpoint functions are plain synchronous `def`, not `async def`.
FastAPI runs them in its default threadpool so blocking SDK calls in live mode
do not stall the event loop.

NaN / numpy serialization
--------------------------
pandas and numpy scalars (np.int64, np.float64) and float NaN must not reach
the JSON encoder.  _safe_val() converts numpy scalars via .item() and maps
NaN/inf to None.  _sanitize_records() applies this to every cell of a
DataFrame.  All numeric results are converted before Pydantic models are
instantiated so the JSON output is always clean.
"""
from __future__ import annotations

import datetime
import logging
import math
import os

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from backend.config import settings
from backend.models import (
    ConfigMeta,
    ConfigResponse,
    ProductionResponse,
    SummaryOrigVsPlan,
    SummaryResponse,
    SupplyResponse,
)

logger = logging.getLogger(__name__)

app = FastAPI(title="Lactalis PET Line Planner")


# ---------------------------------------------------------------------------
# Module-level dataset cache
# ---------------------------------------------------------------------------

_dataset: dict | None = None  # populated on first request


def _load_dataset() -> dict:
    """Build the dataset from Unity Catalog or synthetic data."""
    if os.environ.get("PET_LIVE") == "1":
        from backend import db
        logger.info("PET_LIVE=1: loading dataset from Unity Catalog")
        table_names = [
            "sku",
            "week",
            "parameter",
            "demand",
            "plan_line",
            "opening_stock",
        ]
        return {name: db.read_table(name) for name in table_names}

    from backend import data_gen
    return data_gen.generate()


def get_dataset() -> dict:
    """Return the cached dataset, loading and caching it on first call."""
    global _dataset
    if _dataset is None:
        _dataset = _load_dataset()
    return _dataset


def refresh_dataset() -> None:
    """Invalidate the cache so the next call to get_dataset() reloads."""
    global _dataset
    _dataset = None


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------

def _safe_val(v):
    """Convert a value to a JSON-safe Python scalar.

    * numpy scalars (.item()) are unwrapped to their Python equivalents.
    * float NaN / inf is mapped to None (JSON null).
    * datetime.date / datetime.datetime are serialized as ISO strings.
    * All other values are returned unchanged.
    """
    if v is None:
        return None
    # numpy / pandas scalar types expose a no-arg .item() method
    if hasattr(v, "item"):
        v = v.item()
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    if isinstance(v, (datetime.date, datetime.datetime)):
        return v.isoformat()
    return v


def _sanitize_records(df) -> list[dict]:
    """Convert a DataFrame to a list of JSON-safe plain Python dicts.

    Every cell is passed through _safe_val so no numpy scalar or NaN can
    reach the JSON encoder.
    """
    result = []
    for _, row in df.iterrows():
        result.append({col: _safe_val(val) for col, val in row.items()})
    return result


def _sanitize_flat_dict(d: dict) -> dict:
    """Apply _safe_val to every value in a flat dict."""
    return {k: _safe_val(v) for k, v in d.items()}


# ---------------------------------------------------------------------------
# Existing health endpoint
# ---------------------------------------------------------------------------

@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# GET /api/config
# ---------------------------------------------------------------------------

@app.get("/api/config", response_model=ConfigResponse)
def api_config() -> ConfigResponse:
    """Return SKU master, week horizon, parameters, and run metadata."""
    ds = get_dataset()

    skus = _sanitize_records(ds["sku"])
    weeks = _sanitize_records(ds["week"])
    parameters = _sanitize_records(ds["parameter"])

    total_production = float(ds["plan_line"]["planned_qty"].sum())

    return ConfigResponse(
        skus=skus,
        weeks=weeks,
        parameters=parameters,
        meta=ConfigMeta(
            sku_count=len(skus),
            week_count=len(weeks),
            total_production=total_production,
        ),
    )


# ---------------------------------------------------------------------------
# GET /api/supply
# ---------------------------------------------------------------------------

@app.get("/api/supply", response_model=SupplyResponse)
def api_supply() -> SupplyResponse:
    """Return the full supply projection grid with traffic-light colours."""
    from backend import service

    ds = get_dataset()
    supply_df = service.build_supply(ds)
    rows = _sanitize_records(supply_df)
    return SupplyResponse(rows=rows)


# ---------------------------------------------------------------------------
# GET /api/production
# ---------------------------------------------------------------------------

@app.get("/api/production", response_model=ProductionResponse)
def api_production() -> ProductionResponse:
    """Return the production grid with weekly totals and capacity flags."""
    from backend import service

    ds = get_dataset()
    prod = service.build_production(ds)

    # week_totals values come from `total += float(qty)` -- plain Python floats
    # but sanitize defensively
    week_totals: dict[str, float] = {
        k: float(_safe_val(v))
        for k, v in prod["week_totals"].items()
    }

    # week_flags values are plain Python bools and ints from cap_engine.week_flags()
    week_flags: dict[str, dict] = {
        wk: _sanitize_flat_dict(flags)
        for wk, flags in prod["week_flags"].items()
    }

    return ProductionResponse(
        rows=prod["rows"],
        week_totals=week_totals,
        week_flags=week_flags,
        changeovers=int(prod["changeovers"]),
    )


# ---------------------------------------------------------------------------
# GET /api/summary
# ---------------------------------------------------------------------------

@app.get("/api/summary", response_model=SummaryResponse)
def api_summary() -> SummaryResponse:
    """Return colour counts and original-vs-working-plan comparison."""
    from backend import service

    ds = get_dataset()
    supply_df = service.build_supply(ds)
    summ = service.summary(supply_df, ds)

    # value_counts() returns numpy int64 -- convert to int
    counts: dict[str, int] = {k: int(v) for k, v in summ["counts"].items()}

    # _project_colours uses defaultdict(int) += 1 so values are Python ints;
    # convert defensively anyway
    ovp = summ["original_vs_plan"]
    original: dict[str, int] = {k: int(v) for k, v in ovp["original"].items()}
    working: dict[str, int] = {k: int(v) for k, v in ovp["working"].items()}

    return SummaryResponse(
        counts=counts,
        original_vs_plan=SummaryOrigVsPlan(original=original, working=working),
    )


# ---------------------------------------------------------------------------
# Static frontend (only when built)
# ---------------------------------------------------------------------------

_dist_dir = os.path.join(os.path.dirname(__file__), "..", "frontend", "dist")
if os.path.isdir(_dist_dir):
    app.mount("/", StaticFiles(directory=_dist_dir, html=True), name="static")
