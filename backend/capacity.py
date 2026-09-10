"""
Capacity rules engine for the Lactalis PET Line Planner.

Rules:
  R1 - only one pack size per week
  R2 - max 3 producing SKUs per week
  R3 - weekly total must not exceed the ceiling for that (pack, SKU count, changeover state)
  R4 - Full maintenance week: any production is a breach
  R5 - changeovers counted across the horizon (reported, not enforced)
"""
from __future__ import annotations


def ceiling(pack_ml: int, n_skus: int, is_changeover_week: bool, params: dict) -> int:
    """
    Return the production ceiling (units) for a given week.

    400 ml:
      base = cap_400ml_3_sku   if n_skus >= 3
           = cap_400ml_1_2_sku otherwise
      Changeover has no effect on 400 ml.

    500 ml:
      base = cap_500ml_changeover    if is_changeover_week
           = cap_500ml_steady_1_2   otherwise
      Then subtract cap_500ml_3sku_penalty if n_skus >= 3.
    """
    if pack_ml == 400:
        if n_skus >= 3:
            return int(params["cap_400ml_3_sku"])
        return int(params["cap_400ml_1_2_sku"])

    # 500 ml
    if is_changeover_week:
        base = int(params["cap_500ml_changeover"])
    else:
        base = int(params["cap_500ml_steady_1_2"])

    if n_skus >= 3:
        base -= int(params["cap_500ml_3sku_penalty"])

    return base


def week_flags(
    week_lines: list[dict],
    packs_in_week: set[int],
    n_skus: int,
    total: float,
    maint: str,
    ceiling: int | None,
) -> dict:
    """
    Evaluate capacity rule flags for a single week.

    Returns a dict with boolean keys R1, R2, R3, R4, no_rule and int key over.

    R1       - more than one distinct pack size this week
    R2       - more than 3 producing SKUs
    R3       - total production exceeds ceiling (ceiling must not be None)
    R4       - Full maintenance week with any production
    no_rule  - n_skus > 0 but no ceiling was determinable
    over     - units above ceiling when R3, else 0
    """
    r1 = len(packs_in_week) > 1
    r2 = n_skus > 3
    r3 = (ceiling is not None) and (total > ceiling)
    r4 = (maint == "Full") and (total > 0)
    no_rule = (n_skus > 0) and (ceiling is None)
    over = int(max(0, total - ceiling)) if r3 else 0

    return {
        "R1": r1,
        "R2": r2,
        "R3": r3,
        "R4": r4,
        "no_rule": no_rule,
        "over": over,
    }


def changeovers(producing_weeks: list[dict]) -> int:
    """
    Count pack-size changes across the ordered producing weeks.

    Each dict must have 'idx' (sort key) and 'pack' (pack size int).
    The first producing week is never counted as a changeover.
    """
    sorted_weeks = sorted(producing_weeks, key=lambda w: w["idx"])
    count = 0
    prev_pack = None
    for w in sorted_weeks:
        if prev_pack is not None and w["pack"] != prev_pack:
            count += 1
        prev_pack = w["pack"]
    return count
