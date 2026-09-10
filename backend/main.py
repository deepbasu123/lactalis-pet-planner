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
import uuid

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.staticfiles import StaticFiles

from backend.config import settings
from backend.models import (
    ConfigMeta,
    ConfigResponse,
    DiscardResponse,
    EditRequest,
    EditResponse,
    GenieAskRequest,
    ProductionResponse,
    RecalcResponse,
    ResetWeekRequest,
    SaveResponse,
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
# Per-session working-copy overlay
#
# In-process store (demo only -- resets on server restart).
# Keys: session_id str -> dict[(sku_code, week_key) -> planned_qty float]
# ---------------------------------------------------------------------------

_SESSION_OVERLAYS: dict[str, dict] = {}
_SESSION_COOKIE = "pet_session"


def _get_or_create_sid(request: Request, response: Response) -> str:
    """Return the session ID from the cookie, creating a fresh one if absent."""
    sid: str | None = request.cookies.get(_SESSION_COOKIE)
    if not sid:
        sid = str(uuid.uuid4())
        response.set_cookie(_SESSION_COOKIE, sid, httponly=True, samesite="lax")
    return sid


def _session_overlay(sid: str) -> dict:
    """Return (or create) the overlay dict for the given session."""
    if sid not in _SESSION_OVERLAYS:
        _SESSION_OVERLAYS[sid] = {}
    return _SESSION_OVERLAYS[sid]


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

    total_production = _safe_val(float(ds["plan_line"]["planned_qty"].sum()))

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
def api_supply(request: Request, response: Response) -> SupplyResponse:
    """Return the full supply projection grid with traffic-light colours."""
    from backend import service

    sid = _get_or_create_sid(request, response)
    overlay = _session_overlay(sid)
    ds = get_dataset()
    supply_df = service.build_supply(ds, plan_overlay=overlay)
    rows = _sanitize_records(supply_df)
    return SupplyResponse(rows=rows)


# ---------------------------------------------------------------------------
# GET /api/production
# ---------------------------------------------------------------------------

@app.get("/api/production", response_model=ProductionResponse)
def api_production(request: Request, response: Response) -> ProductionResponse:
    """Return the production grid with weekly totals and capacity flags."""
    from backend import service

    sid = _get_or_create_sid(request, response)
    overlay = _session_overlay(sid)
    ds = get_dataset()
    prod = service.build_production(ds, plan_overlay=overlay)

    # week_totals values come from `total += float(qty)` -- plain Python floats
    # but sanitize defensively.  Coerce to float first so _safe_val can catch
    # NaN; inverting to float(_safe_val(v)) would raise TypeError when v is NaN.
    week_totals: dict[str, float] = {
        k: _safe_val(float(v))
        for k, v in prod["week_totals"].items()
    }

    # week_flags values are plain Python bools and ints from cap_engine.week_flags()
    week_flags: dict[str, dict] = {
        wk: _sanitize_flat_dict(flags)
        for wk, flags in prod["week_flags"].items()
    }

    # Sanitize rows the same way as every other output: _safe_val on every cell.
    # prod["rows"] is built from plan_df iteration and float() coercions, so
    # values are typically plain Python, but defensive sanitization avoids
    # any numpy scalar leaking through if the service layer changes.
    rows = [_sanitize_flat_dict(row) for row in prod["rows"]]

    return ProductionResponse(
        rows=rows,
        week_totals=week_totals,
        week_flags=week_flags,
        changeovers=int(prod["changeovers"]),
    )


# ---------------------------------------------------------------------------
# GET /api/summary
# ---------------------------------------------------------------------------

@app.get("/api/summary", response_model=SummaryResponse)
def api_summary(request: Request, response: Response) -> SummaryResponse:
    """Return colour counts and original-vs-working-plan comparison."""
    from backend import service

    sid = _get_or_create_sid(request, response)
    overlay = _session_overlay(sid)
    ds = get_dataset()
    supply_df = service.build_supply(ds, plan_overlay=overlay)
    summ = service.summary(supply_df, ds, plan_overlay=overlay)

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
# POST /api/production/edit
# ---------------------------------------------------------------------------

@app.post("/api/production/edit", response_model=EditResponse)
def api_production_edit(
    body: EditRequest,
    request: Request,
    response: Response,
) -> EditResponse:
    """Apply one cell edit to the session overlay.

    Returns HTTP 409 if the target week is locked (RULE-020).
    Negative qty is clamped to 0; capacity/pack-size violations are detected
    by the engine (flagged via week_flags) but are NOT rejected here.
    """
    ds = get_dataset()
    week_df = ds["week"]

    row = week_df[week_df["week_key"] == body.week_key]
    if row.empty:
        raise HTTPException(status_code=404, detail=f"Week {body.week_key!r} not found")

    if bool(row["is_locked"].iat[0]):
        raise HTTPException(
            status_code=409,
            detail="Week is locked (time fence) and cannot be modified",
        )

    qty = max(0.0, float(body.qty))
    sid = _get_or_create_sid(request, response)
    overlay = _session_overlay(sid)
    overlay[(body.sku_code, body.week_key)] = qty

    return EditResponse(status="ok", sku_code=body.sku_code, week_key=body.week_key, qty=qty)


# ---------------------------------------------------------------------------
# POST /api/production/save
# ---------------------------------------------------------------------------

@app.post("/api/production/save", response_model=SaveResponse)
def api_production_save(request: Request, response: Response) -> SaveResponse:
    """Persist the session overlay.

    When PET_LIVE=="1": writes changed rows via db.merge_plan_lines() and then
    calls db.write_snapshot() with the freshly computed supply projection.
    When PET_LIVE is unset (demo / test mode): a no-op that just clears the
    overlay and reports how many rows *would* have been saved.
    """
    sid = _get_or_create_sid(request, response)
    overlay = _session_overlay(sid)
    n_rows = len(overlay)

    if os.environ.get("PET_LIVE") == "1":
        from backend import db, service
        ds = get_dataset()
        db.merge_plan_lines(overlay)
        supply_df = service.build_supply(ds, plan_overlay=overlay)
        db.write_snapshot(supply_df)
        refresh_dataset()

    # Always clear the overlay after a save attempt
    _SESSION_OVERLAYS.pop(sid, None)

    return SaveResponse(status="ok", saved=n_rows)


# ---------------------------------------------------------------------------
# POST /api/production/discard
# ---------------------------------------------------------------------------

@app.post("/api/production/discard", response_model=DiscardResponse)
def api_production_discard(request: Request, response: Response) -> DiscardResponse:
    """Clear all pending edits for this session."""
    sid = _get_or_create_sid(request, response)
    overlay = _session_overlay(sid)
    cleared = len(overlay)
    _SESSION_OVERLAYS.pop(sid, None)
    return DiscardResponse(status="ok", cleared=cleared)


# ---------------------------------------------------------------------------
# POST /api/production/reset-week
# ---------------------------------------------------------------------------

@app.post("/api/production/reset-week", response_model=DiscardResponse)
def api_production_reset_week(
    body: ResetWeekRequest,
    request: Request,
    response: Response,
) -> DiscardResponse:
    """Clear all overlay entries for one specific week."""
    sid = _get_or_create_sid(request, response)
    overlay = _session_overlay(sid)
    keys_to_remove = [k for k in overlay if k[1] == body.week_key]
    for k in keys_to_remove:
        del overlay[k]
    return DiscardResponse(status="ok", cleared=len(keys_to_remove))


# ---------------------------------------------------------------------------
# POST /api/recalc
# ---------------------------------------------------------------------------

@app.post("/api/recalc", response_model=RecalcResponse)
def api_recalc(request: Request, response: Response) -> RecalcResponse:
    """Recompute supply, production, and summary for the current session overlay."""
    from backend import service

    sid = _get_or_create_sid(request, response)
    overlay = _session_overlay(sid)
    ds = get_dataset()

    supply_df = service.build_supply(ds, plan_overlay=overlay)
    supply_rows = _sanitize_records(supply_df)

    prod = service.build_production(ds, plan_overlay=overlay)
    week_totals: dict[str, float] = {
        k: _safe_val(float(v)) for k, v in prod["week_totals"].items()
    }
    week_flags: dict[str, dict] = {
        wk: _sanitize_flat_dict(flags) for wk, flags in prod["week_flags"].items()
    }
    prod_rows = [_sanitize_flat_dict(row) for row in prod["rows"]]

    summ = service.summary(supply_df, ds, plan_overlay=overlay)
    counts: dict[str, int] = {k: int(v) for k, v in summ["counts"].items()}
    ovp = summ["original_vs_plan"]
    original: dict[str, int] = {k: int(v) for k, v in ovp["original"].items()}
    working: dict[str, int] = {k: int(v) for k, v in ovp["working"].items()}

    return RecalcResponse(
        supply=SupplyResponse(rows=supply_rows),
        production=ProductionResponse(
            rows=prod_rows,
            week_totals=week_totals,
            week_flags=week_flags,
            changeovers=int(prod["changeovers"]),
        ),
        summary=SummaryResponse(
            counts=counts,
            original_vs_plan=SummaryOrigVsPlan(original=original, working=working),
        ),
    )


# ---------------------------------------------------------------------------
# POST /api/genie/ask  -- start or continue a Genie conversation
# GET  /api/genie/poll -- fetch message status and response text
# ---------------------------------------------------------------------------

@app.post("/api/genie/ask")
def api_genie_ask(body: GenieAskRequest) -> dict:
    """Proxy a question to the configured Genie space.

    Starts a new conversation when conversation_id is absent; continues an
    existing one when it is supplied.  Returns {conversation_id, message_id}
    so the caller can poll for the answer.

    Returns HTTP 503 when PET_GENIE_SPACE_ID is not configured.
    """
    if not settings.genie_space_id:
        raise HTTPException(
            status_code=503,
            detail="Genie is not configured yet.",
        )
    from backend import genie
    return genie.ask(settings.genie_space_id, body.question, body.conversation_id)


@app.get("/api/genie/poll")
def api_genie_poll(conversation_id: str, message_id: str) -> dict:
    """Poll the status of a Genie message.

    Returns {status, text} and, when present, {sql}.

    Returns HTTP 503 when PET_GENIE_SPACE_ID is not configured.
    """
    if not settings.genie_space_id:
        raise HTTPException(
            status_code=503,
            detail="Genie is not configured yet.",
        )
    from backend import genie
    return genie.poll(settings.genie_space_id, conversation_id, message_id)


# ---------------------------------------------------------------------------
# Static frontend (only when built)
# ---------------------------------------------------------------------------

_dist_dir = os.path.join(os.path.dirname(__file__), "..", "frontend", "dist")
if os.path.isdir(_dist_dir):
    app.mount("/", StaticFiles(directory=_dist_dir, html=True), name="static")
