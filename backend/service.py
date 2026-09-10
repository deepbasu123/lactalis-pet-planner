"""backend/service.py

Service layer: runs the stock and capacity engines over a dataset (plus an
optional working-copy overlay) to produce the supply grid, the production
grid, and the summary that the API will serve.

Consumes:
  data_gen.generate()  ->  dict of DataFrames
  engine.project_sku / cover / severity / colour
  capacity.ceiling / week_flags / changeovers

Produces:
  build_supply(dataset, plan_overlay=None)  ->  pd.DataFrame
  build_production(dataset, plan_overlay=None)  ->  dict
  summary(supply_df, dataset, plan_overlay=None)  ->  dict
"""
from __future__ import annotations

from collections import defaultdict

import pandas as pd

from backend.engine import project_sku, cover, severity, colour
import backend.capacity as cap_engine


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_params(dataset: dict) -> dict:
    """Flatten the parameter DataFrame into a plain name -> float dict."""
    return dict(
        zip(dataset["parameter"]["name"], dataset["parameter"]["value"])
    )


def _sku_demand_list(
    demand_df: pd.DataFrame,
    sku_code: str,
    week_keys: list[str],
) -> list[float]:
    """Return per-week demand (max(forecast, sales_order)) for one SKU."""
    sub = demand_df[demand_df["sku_code"] == sku_code].set_index("week_key")
    return [
        float(max(sub.loc[wk, "forecast"], sub.loc[wk, "sales_order"]))
        for wk in week_keys
    ]


def _sku_receipts_list(
    plan_df: pd.DataFrame,
    sku_code: str,
    week_keys: list[str],
    plan_overlay: dict,
    col: str = "planned_qty",
) -> list[float]:
    """Return the per-week planned qty list for one SKU, overlay-adjusted."""
    sub = plan_df[plan_df["sku_code"] == sku_code].set_index("week_key")
    result = []
    for wk in week_keys:
        key = (sku_code, wk)
        if key in plan_overlay:
            result.append(float(plan_overlay[key]))
        else:
            result.append(float(sub.loc[wk, col]))
    return result


# ---------------------------------------------------------------------------
# build_supply
# ---------------------------------------------------------------------------

def build_supply(
    dataset: dict,
    plan_overlay: dict | None = None,
) -> pd.DataFrame:
    """
    Run the stock projection engine for all 11 SKUs x 52 weeks.

    Parameters
    ----------
    dataset : dict
        Output of data_gen.generate().
    plan_overlay : dict | None
        Optional dict keyed (sku_code, week_key) -> planned_qty.
        Overrides plan_line.planned_qty before projecting.

    Returns
    -------
    pd.DataFrame with columns:
        sku_code, week_key, horizon_index, opening, recv, prod, demand,
        raw, close, cover_weeks, severity, colour, display_value
    """
    if plan_overlay is None:
        plan_overlay = {}

    params = _get_params(dataset)
    qa_weeks = int(params["qa_hold_weeks"])
    demand_horizon = int(params["demand_horizon"])
    reaction_window = int(params["reaction_window"])

    sku_df = dataset["sku"]
    week_df = (
        dataset["week"]
        .sort_values("horizon_index")
        .reset_index(drop=True)
    )
    demand_df = dataset["demand"]
    plan_df = dataset["plan_line"]
    opening_df = dataset["opening_stock"]

    week_keys: list[str] = list(week_df["week_key"])
    horizon_indices: list[int] = list(week_df["horizon_index"].astype(int))
    n_weeks = len(week_keys)

    rows = []

    for _, sku_row in sku_df.iterrows():
        sku_code: str = sku_row["sku_code"]
        max_cover_raw: float = float(sku_row["max_cover_weeks"])
        max_cover_int: int = int(round(max_cover_raw)) if max_cover_raw > 0 else 0

        # Opening stock
        opening_ea = float(
            opening_df.loc[
                opening_df["sku_code"] == sku_code, "opening_ea"
            ].iat[0]
        )

        # Demand list (max of forecast, sales_order)
        demand_list = _sku_demand_list(demand_df, sku_code, week_keys)

        # Receipts list (planned_qty, overlay-adjusted)
        receipts_list = _sku_receipts_list(
            plan_df, sku_code, week_keys, plan_overlay
        )

        # Run the projection engine
        proj = project_sku(demand_list, receipts_list, opening_ea, qa_weeks)

        # Build per-week output rows
        for wi in range(n_weeks):
            wk = week_keys[wi]
            hi = horizon_indices[wi]
            p = proj[wi]

            # Forward demand window (next demand_horizon weeks; pad with 0)
            fwd = demand_list[wi + 1 : wi + 1 + demand_horizon]
            while len(fwd) < demand_horizon:
                fwd.append(0.0)

            # Demand over the max-cover window (next max_cover_int weeks)
            if max_cover_int > 0:
                dom = sum(demand_list[wi + 1 : wi + 1 + max_cover_int])
            else:
                dom = 0.0

            cov = cover(p["close"], fwd)
            sev = severity(
                p["close"],
                hi,           # weeks_until_shortfall = horizon_index
                fwd,
                dom,
                reaction_window,
            )
            col = colour(sev)
            disp = p["raw"] if p["raw"] < 0 else p["close"]

            rows.append({
                "sku_code":      sku_code,
                "week_key":      wk,
                "horizon_index": hi,
                "opening":       p["opening"],
                "recv":          p["recv"],
                "prod":          receipts_list[wi],
                "demand":        demand_list[wi],
                "raw":           p["raw"],
                "close":         p["close"],
                "cover_weeks":   cov,
                "severity":      sev,
                "colour":        col,
                "display_value": disp,
            })

    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# build_production
# ---------------------------------------------------------------------------

def build_production(
    dataset: dict,
    plan_overlay: dict | None = None,
) -> dict:
    """
    Aggregate production data and compute capacity flags per week.

    Returns
    -------
    dict with keys:
      rows        : list of {sku_code, week_key, planned_qty}
      week_totals : {week_key -> total planned_qty}
      week_flags  : {week_key -> capacity flag dict (R1, R2, R3, R4, no_rule, over)}
    """
    if plan_overlay is None:
        plan_overlay = {}

    params = _get_params(dataset)

    sku_df = dataset["sku"]
    week_df = (
        dataset["week"]
        .sort_values("horizon_index")
        .reset_index(drop=True)
    )
    plan_df = dataset["plan_line"]

    week_keys: list[str] = list(week_df["week_key"])

    sku_pack: dict[str, int] = dict(
        zip(sku_df["sku_code"], sku_df["pack_size_ml"].astype(int))
    )

    # Resolve each (sku_code, week_key) planned_qty with overlay
    qty_map: dict[tuple, float] = {}
    for _, row in plan_df.iterrows():
        sku_code = row["sku_code"]
        wk = row["week_key"]
        key = (sku_code, wk)
        if key in plan_overlay:
            qty_map[key] = float(plan_overlay[key])
        else:
            qty_map[key] = float(row["planned_qty"])

    # Rows list (JSON-serialisable)
    rows_list = [
        {"sku_code": sku_code, "week_key": wk, "planned_qty": qty}
        for (sku_code, wk), qty in qty_map.items()
    ]

    # Per-week aggregates
    week_totals: dict[str, float] = {}
    week_summary: dict[str, dict] = {}

    for wi, wk in enumerate(week_keys):
        maint = str(
            week_df.loc[week_df["week_key"] == wk, "maintenance_type"].iat[0]
        )
        hi = int(
            week_df.loc[week_df["week_key"] == wk, "horizon_index"].iat[0]
        )

        total = 0.0
        n_skus = 0
        packs_in_week: set[int] = set()

        for sku_code in sku_df["sku_code"]:
            qty = qty_map.get((sku_code, wk), 0.0)
            if qty > 0:
                total += qty
                n_skus += 1
                packs_in_week.add(sku_pack[sku_code])

        week_totals[wk] = total
        week_summary[wk] = {
            "total": total,
            "n_skus": n_skus,
            "packs": packs_in_week,
            "maint": maint,
            "hi": hi,
        }

    # Derive per-producing-week is_changeover (pack differs from previous
    # producing week).  Also build the list for capacity.changeovers().
    producing_weeks_for_count = []
    prev_pack: int | None = None
    changeover_week_keys: set[str] = set()

    for wk in week_keys:
        ws = week_summary[wk]
        if ws["total"] > 0 and ws["packs"]:
            # Use the minimum pack size as the canonical pack for this week
            this_pack = min(ws["packs"])
            producing_weeks_for_count.append(
                {"idx": ws["hi"], "pack": this_pack}
            )
            if prev_pack is not None and this_pack != prev_pack:
                changeover_week_keys.add(wk)
            prev_pack = this_pack

    total_changeovers = cap_engine.changeovers(producing_weeks_for_count)

    # Compute ceiling and week_flags per week
    week_flags_result: dict[str, dict] = {}
    for wk in week_keys:
        ws = week_summary[wk]
        total = ws["total"]
        n_skus = ws["n_skus"]
        packs = ws["packs"]
        maint = ws["maint"]
        is_co = wk in changeover_week_keys

        if n_skus > 0 and packs:
            pack_ml = min(packs)
            ceil_val = cap_engine.ceiling(pack_ml, n_skus, is_co, params)
        else:
            ceil_val = None

        wf = cap_engine.week_flags(
            week_lines=[],
            packs_in_week=packs,
            n_skus=n_skus,
            total=total,
            maint=maint,
            ceiling=ceil_val,
        )
        week_flags_result[wk] = wf

    return {
        "rows":            rows_list,
        "week_totals":     week_totals,
        "week_flags":      week_flags_result,
        "changeovers":     total_changeovers,
    }


# ---------------------------------------------------------------------------
# summary
# ---------------------------------------------------------------------------

def _project_colours(
    sku_df: pd.DataFrame,
    week_keys: list[str],
    horizon_indices: list[int],
    demand_df: pd.DataFrame,
    plan_df: pd.DataFrame,
    opening_df: pd.DataFrame,
    params: dict,
    plan_overlay: dict,
    use_orig: bool,
) -> dict[str, int]:
    """Run the engine and tally colour counts for a given qty column."""
    qa_weeks = int(params["qa_hold_weeks"])
    demand_horizon = int(params["demand_horizon"])
    reaction_window = int(params["reaction_window"])

    colour_counts: dict[str, int] = defaultdict(int)

    col_name = "orig_qty" if use_orig else "planned_qty"
    overlay = {} if use_orig else plan_overlay

    for _, sku_row in sku_df.iterrows():
        sku_code: str = sku_row["sku_code"]
        max_cover_raw: float = float(sku_row["max_cover_weeks"])
        max_cover_int: int = int(round(max_cover_raw)) if max_cover_raw > 0 else 0

        opening_ea = float(
            opening_df.loc[
                opening_df["sku_code"] == sku_code, "opening_ea"
            ].iat[0]
        )

        demand_list = _sku_demand_list(demand_df, sku_code, week_keys)
        receipts_list = _sku_receipts_list(
            plan_df, sku_code, week_keys, overlay, col=col_name
        )

        proj = project_sku(demand_list, receipts_list, opening_ea, qa_weeks)

        for wi in range(len(week_keys)):
            hi = horizon_indices[wi]
            p = proj[wi]

            fwd = demand_list[wi + 1 : wi + 1 + demand_horizon]
            while len(fwd) < demand_horizon:
                fwd.append(0.0)

            if max_cover_int > 0:
                dom = sum(demand_list[wi + 1 : wi + 1 + max_cover_int])
            else:
                dom = 0.0

            sev = severity(p["close"], hi, fwd, dom, reaction_window)
            col = colour(sev)
            colour_counts[col] += 1

    return dict(colour_counts)


def summary(
    supply_df: pd.DataFrame,
    dataset: dict,
    plan_overlay: dict | None = None,
) -> dict:
    """
    Summarise colour counts across the supply grid.

    Parameters
    ----------
    supply_df : pd.DataFrame
        Result of build_supply(dataset, plan_overlay).
    dataset : dict
        Full dataset from data_gen.generate().
    plan_overlay : dict | None
        Same overlay passed to build_supply (applied for the working column).

    Returns
    -------
    dict with keys:
      counts           : {colour_name -> count} from supply_df
      original_vs_plan : {'original': {...}, 'working': {...}}
    """
    if plan_overlay is None:
        plan_overlay = {}

    params = _get_params(dataset)

    sku_df = dataset["sku"]
    week_df = (
        dataset["week"]
        .sort_values("horizon_index")
        .reset_index(drop=True)
    )
    demand_df = dataset["demand"]
    plan_df = dataset["plan_line"]
    opening_df = dataset["opening_stock"]

    week_keys: list[str] = list(week_df["week_key"])
    horizon_indices: list[int] = list(week_df["horizon_index"].astype(int))

    # Colour counts directly from the already-projected supply_df
    counts: dict[str, int] = supply_df["colour"].value_counts().to_dict()

    # Compute original (orig_qty) and working (planned_qty + overlay) counts
    original_counts = _project_colours(
        sku_df, week_keys, horizon_indices,
        demand_df, plan_df, opening_df,
        params, plan_overlay, use_orig=True,
    )
    working_counts = _project_colours(
        sku_df, week_keys, horizon_indices,
        demand_df, plan_df, opening_df,
        params, plan_overlay, use_orig=False,
    )

    return {
        "counts": counts,
        "original_vs_plan": {
            "original": original_counts,
            "working":  working_counts,
        },
    }
