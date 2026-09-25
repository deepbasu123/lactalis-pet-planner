"""backend/autofix.py

Strict-trim rule resolver — now warehouse-backed and engine-free.

The autofix decides WHICH SKUs to keep/trim (a planner heuristic), but every
rule value it relies on (the changeover-aware capacity ceiling, RULE-003/004)
is computed by the Gold SQL engine (medallion.gold_sql.production_select), not
re-implemented here. The chosen fix is written to silver.plan_overlay as data.

Two passes, mirroring the original engine:
  Pass 1 (R4/R1/R2): per unlocked week, zero full-maintenance production, keep
    the highest-priority producing SKU's pack size, and keep at most the top-3
    SKUs by priority; the rest are dropped to 0.
  Pass 2 (R3): re-read the Gold capacity of the trimmed schedule and trim the
    lowest-priority kept SKUs by exactly the SQL-computed over-ceiling amount.

Locked (time-fence) weeks are never touched.
"""
from __future__ import annotations

from backend import warehouse
from medallion.gold_sql import production_select

MAX_SKUS_PER_WEEK = 3


def _meta():
    s = warehouse.silver()
    skus = warehouse.run_sql(f"SELECT sku_code, priority, pack_size_ml FROM {s.t('sku')}")
    weeks = warehouse.run_sql(
        f"SELECT week_key, horizon_index, is_locked, maintenance_type "
        f"FROM {s.t('week')} ORDER BY horizon_index"
    )
    return skus, weeks


def strict_trim(scenario: str = "working") -> dict:
    """Compute + persist a rules-compliant overlay. Returns the change report."""
    skus, weeks = _meta()
    priority = {r["sku_code"]: int(r["priority"]) for r in skus}
    pack = {r["sku_code"]: int(r["pack_size_ml"]) for r in skus}
    all_skus = [r["sku_code"] for r in skus]
    week_order = [r["week_key"] for r in weeks]
    is_locked = {r["week_key"]: bool(r["is_locked"]) for r in weeks}
    maint = {r["week_key"]: str(r["maintenance_type"]) for r in weeks}

    plan_rows = warehouse.effective_plan_rows(scenario)
    qty = {(r["sku_code"], r["week_key"]): float(r["planned_qty"]) for r in plan_rows}

    weeks_changed: set[str] = set()
    cells_zeroed = cells_trimmed = 0
    volume_dropped = 0.0
    locked_skipped = 0
    pass1: dict[tuple, float] = {}

    # Pass 1 — pack/count/maintenance selection (no rule maths, just priority).
    for wk in week_order:
        if is_locked[wk]:
            locked_skipped += 1
            continue
        producing = [s for s in all_skus if qty.get((s, wk), 0.0) > 0]
        if not producing:
            continue
        if maint[wk] == "Full":
            for s in producing:
                volume_dropped += qty[(s, wk)]
                qty[(s, wk)] = 0.0
                pass1[(s, wk)] = 0.0
                cells_zeroed += 1
            weeks_changed.add(wk)
            continue
        top = min(producing, key=lambda s: priority[s])
        kept_pack = pack[top]
        candidates = sorted((s for s in producing if pack[s] == kept_pack), key=lambda s: priority[s])
        keep = set(candidates[:MAX_SKUS_PER_WEEK])
        for s in producing:
            if s not in keep:
                volume_dropped += qty[(s, wk)]
                qty[(s, wk)] = 0.0
                pass1[(s, wk)] = 0.0
                cells_zeroed += 1
        weeks_changed.add(wk)

    if pass1:
        warehouse.overlay_merge(
            scenario,
            [{"sku_code": s, "week_key": wk, "planned_qty": q} for (s, wk), q in pass1.items()],
        )

    # Pass 2 — trim to the Gold-computed ceiling of the trimmed schedule.
    prod = warehouse.run_sql(production_select(warehouse.silver(), scenario=scenario))
    over_by_week = {r["week_key"]: float(r["over_units"]) for r in prod}
    pass2: dict[tuple, float] = {}
    for wk in week_order:
        if is_locked[wk] or maint[wk] == "Full":
            continue
        over = over_by_week.get(wk, 0.0)
        if over <= 0:
            continue
        kept = sorted((s for s in all_skus if qty.get((s, wk), 0.0) > 0), key=lambda s: priority[s])
        for s in reversed(kept):
            if over <= 0:
                break
            q = qty[(s, wk)]
            cut = min(q, over)
            qty[(s, wk)] = q - cut
            over -= cut
            volume_dropped += cut
            cells_trimmed += 1
            pass2[(s, wk)] = qty[(s, wk)]
        weeks_changed.add(wk)

    if pass2:
        warehouse.overlay_merge(
            scenario,
            [{"sku_code": s, "week_key": wk, "planned_qty": q} for (s, wk), q in pass2.items()],
        )

    return {
        "weeks_changed": len(weeks_changed),
        "cells_zeroed": cells_zeroed,
        "cells_trimmed": cells_trimmed,
        "volume_dropped": volume_dropped,
        "locked_weeks_skipped": locked_skipped,
    }
