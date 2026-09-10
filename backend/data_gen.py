"""backend/data_gen.py

Synthetic PET line dataset generator for the Lactalis PET Line Planner.

Returns an in-memory dict of pandas DataFrames suitable for tests, the
FastAPI service layer, and the calculation engine.  Every numeric sequence
is driven by a seeded numpy RNG so the output is fully deterministic for a
given seed value.
"""
from __future__ import annotations

import datetime
import math

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Static SKU master
# (sku_code, description, pack_size_ml, priority, shelf_life_days, mlor_days)
# MLOR source: spec section 4 for the first six SKUs; spec says the remaining
# five get plausible values of 180/90 so the over-cover (black) band fires.
# ---------------------------------------------------------------------------
_SKU_ROWS: list[tuple] = [
    ("60444",  "PAULS ZYMIL FLAV MILK CHOC 400ML",                        400, 1,  180, 43),
    ("61108",  "OAK UHT CHOCOLATE 500ML",                                  500, 2,  200, 90),
    ("61747",  "OAK UHT ICED COFFEE 500ML",                               500, 3,  180, 90),
    ("61924",  "OAK UHT STRAWBERRY 500ML",                                 500, 4,  180, 90),
    ("70526",  "OAK PLUS FLAVOURED MILK NAS CHOC 6x500ml",                500, 5,  200, 90),
    ("70535",  "OAK PLUS FLAVOURED MILK NAS VANILLA 6X500ML",             500, 6,  180, 90),
    ("228500", "PAULS PLUS CHOCOLATE Flavoured Milk 400ML",                400, 7,  180, 90),
    ("228510", "PAULS PLUS BANANA HONEY Flavoured Milk 400ML",             400, 8,  180, 90),
    ("230150", "OAK PLUS FLAVOURED MILK NAS SALTED CAR 6x500ml",          500, 9,  180, 90),
    ("230540", "Pauls PLUS SUMMER BERRIES Flavoured Milk 400mL",          400, 10, 180, 90),
    ("230550", "Pauls PLUS DOUBLE ESPRESSO CARAMEL Flavoured Milk 400mL", 400, 11, 180, 90),
]

# ---------------------------------------------------------------------------
# Base production volumes (EA / week) — calibrated so that:
#   sum = 450 000 EA/week, and
#   total orig_qty over 52 weeks (minus Full/Partial maintenance) ~ 22.7 M
# 500-ml OAK UHT lines are highest-volume; 400-ml Pauls flavoured milks lower.
# ---------------------------------------------------------------------------
_PLAN_BASE: dict[str, float] = {
    "60444":  24_000.0,
    "61108":  68_000.0,
    "61747":  62_000.0,
    "61924":  57_000.0,
    "70526":  53_000.0,
    "70535":  50_000.0,
    "228500": 23_000.0,
    "228510": 21_000.0,
    "230150": 46_000.0,
    "230540": 21_000.0,
    "230550": 25_000.0,
    # sum = 450 000
}

# Demand base runs slightly above plan to create natural demand pressure
_DEMAND_SCALE: float = 1.07

# ---------------------------------------------------------------------------
# Parameters (values from the Parameters screenshot)
# ---------------------------------------------------------------------------
_PARAM_ROWS: list[tuple] = [
    ("cap_400ml_1_2_sku",      650_000.0, "400ml line capacity for 1-2 SKUs per week"),
    ("cap_400ml_3_sku",        600_000.0, "400ml line capacity for 3 SKUs per week"),
    ("cap_500ml_changeover",   650_000.0, "500ml line capacity for the first week after a changeover"),
    ("cap_500ml_steady_1_2",   700_000.0, "500ml line steady-state capacity for 1-2 SKUs"),
    ("cap_500ml_3sku_penalty",  50_000.0, "Capacity penalty for running a third 500ml SKU per week"),
    ("qa_hold_weeks",               2.0,  "QA hold period in weeks before production is sellable"),
    ("max_cover_weeks",            16.0,  "Cover weeks ceiling; stock beyond this fires the black band"),
    ("target_cover_weeks",          3.0,  "Planner target stock cover weeks"),
    ("reaction_window",             3.0,  "Weeks within which a stockout fires dark-red"),
    ("demand_horizon",              4.0,  "Forward demand weeks used in cover calculation"),
]

# ---------------------------------------------------------------------------
# Week horizon
# ---------------------------------------------------------------------------
_HORIZON_START = datetime.date(2026, 8, 24)   # Monday of ISO 2026-W35
_N_WEEKS = 52
_LOCK_WEEKS = 3              # first 3 weeks are time-fence locked
_FULL_MAINT_INDEX = 20       # 1-based horizon_index — Full maintenance
_PARTIAL_MAINT_INDEX = 35    # 1-based horizon_index — Partial maintenance


def _iso_week_key(d: datetime.date) -> str:
    """Format a date as its ISO week string, e.g. '2026-W35'."""
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def generate(seed: int = 42) -> dict[str, pd.DataFrame]:
    """Generate the full synthetic PET dataset.

    Parameters
    ----------
    seed:
        Integer seed for numpy's default_rng.  Identical seeds produce
        byte-for-byte identical DataFrames.

    Returns
    -------
    dict with keys: sku, week, parameter, demand, plan_line, opening_stock.
    """
    rng = np.random.default_rng(seed)

    # ------------------------------------------------------------------
    # 1. SKU table
    # ------------------------------------------------------------------
    sku_records = []
    for code, desc, pack_ml, pri, shelf, mlor in _SKU_ROWS:
        max_cover = round((shelf - mlor) / 7.0, 1)
        sku_records.append({
            "sku_code":        code,
            "description":     desc,
            "pack_size_ml":    pack_ml,
            "priority":        pri,
            "status":          "Active",
            "shelf_life_days": shelf,
            "mlor_days":       mlor,
            "max_cover_weeks": max_cover,
        })
    sku_df = pd.DataFrame(sku_records)

    # ------------------------------------------------------------------
    # 2. Week table (52 ISO weeks starting 2026-W35)
    # ------------------------------------------------------------------
    week_records = []
    for i in range(_N_WEEKS):
        monday = _HORIZON_START + datetime.timedelta(weeks=i)
        hi = i + 1
        if hi == _FULL_MAINT_INDEX:
            mtype = "Full"
        elif hi == _PARTIAL_MAINT_INDEX:
            mtype = "Partial"
        else:
            mtype = "None"
        week_records.append({
            "week_key":         _iso_week_key(monday),
            "horizon_index":    hi,
            "week_commencing":  monday,
            "maintenance_type": mtype,
            "is_locked":        hi <= _LOCK_WEEKS,
            "note":             "",
        })
    week_df = pd.DataFrame(week_records)
    week_keys: list[str] = [r["week_key"] for r in week_records]

    # ------------------------------------------------------------------
    # 3. Parameter table (10 rows, values from screenshot)
    # ------------------------------------------------------------------
    param_df = pd.DataFrame([
        {"name": n, "value": v, "description": d}
        for n, v, d in _PARAM_ROWS
    ])

    # ------------------------------------------------------------------
    # 4. Demand table  (11 SKUs x 52 weeks = 572 rows)
    #
    # forecast = base * seasonality * noise
    # sales_order close to forecast
    # Seasonality: mild sine wave peaking mid-winter (around week 26).
    # ------------------------------------------------------------------
    demand_records = []
    for code, *_ in _SKU_ROWS:
        demand_base = _PLAN_BASE[code] * _DEMAND_SCALE
        for wi, wk in enumerate(week_keys):
            # gentle winter peak (phase shift so peak is ~week 26 of the 52)
            season_factor = 1.0 + 0.08 * math.sin(
                2 * math.pi * wi / 52.0 + math.pi
            )
            noise = 1.0 + 0.06 * (float(rng.random()) - 0.5)    # +-3%
            forecast = round(demand_base * season_factor * noise)
            so_noise = 1.0 + 0.04 * (float(rng.random()) - 0.5) # +-2%
            sales_order = round(forecast * so_noise)
            demand_records.append({
                "sku_code":             code,
                "week_key":             wk,
                "forecast":             float(forecast),
                "sales_order":          float(sales_order),
                "distr_demand_planned": float(round(forecast * 0.95)),
                "distr_demand_tlb":     float(round(forecast * 0.88)),
            })
    demand_df = pd.DataFrame(demand_records)

    # ------------------------------------------------------------------
    # 5. Plan line  (SNP baseline; orig_qty == planned_qty initially)
    #
    # Deliberate realism:
    #   - Full maintenance week  -> 0 production for all SKUs
    #   - Partial maintenance    -> 50% of base
    #   - Normal weeks           -> base * random [0.88, 1.12] factor
    # This produces a mix of over-production and under-production vs demand,
    # giving the engine plenty of amber/red/dark-blue cells to colour later.
    # ------------------------------------------------------------------
    plan_records = []
    for code, *_ in _SKU_ROWS:
        base = _PLAN_BASE[code]
        for wi, wk in enumerate(week_keys):
            hi = wi + 1
            if hi == _FULL_MAINT_INDEX:
                qty = 0.0
            elif hi == _PARTIAL_MAINT_INDEX:
                qty = float(round(base * 0.5))
            else:
                factor = float(rng.uniform(0.88, 1.12))
                qty = float(round(base * factor))
            plan_records.append({
                "sku_code":    code,
                "week_key":    wk,
                "planned_qty": qty,
                "orig_qty":    qty,
            })
    plan_df = pd.DataFrame(plan_records)

    # ------------------------------------------------------------------
    # 6. Opening stock  (~2.5 weeks of early forecast per SKU)
    # ------------------------------------------------------------------
    first_wk = week_keys[0]
    opening_records = []
    for code, *_ in _SKU_ROWS:
        first_fcst = float(
            demand_df.loc[
                (demand_df["sku_code"] == code) & (demand_df["week_key"] == first_wk),
                "forecast",
            ].iat[0]
        )
        opening_records.append({
            "sku_code":   code,
            "opening_ea": float(round(first_fcst * 2.5)),
        })
    opening_df = pd.DataFrame(opening_records)

    return {
        "sku":           sku_df,
        "week":          week_df,
        "parameter":     param_df,
        "demand":        demand_df,
        "plan_line":     plan_df,
        "opening_stock": opening_df,
    }
