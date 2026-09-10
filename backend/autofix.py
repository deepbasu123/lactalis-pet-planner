"""backend/autofix.py

Strict-trim rule resolver for the Lactalis PET Line Planner.

Given the dataset and the current session overlay, compute a new overlay that
makes every UNLOCKED week satisfy the capacity rules R1-R4 by strict trimming:

  * R4  Full-maintenance week      -> zero all production that week.
  * R1  one pack size per week     -> keep the pack size of the highest-priority
                                      producing SKU; zero SKUs of any other pack.
  * R2  at most 3 producing SKUs   -> keep the top 3 by priority (a lower priority
                                      number ranks higher); zero the rest.
  * R3  weekly total <= ceiling    -> trim the lowest-priority kept SKU(s) down
                                      until the week's total fits the ceiling.

Excess production is DROPPED, not reallocated. Locked weeks (time-fence) are
left untouched; their breaches, if any, are reported but never changed.

The result is a dict of overlay changes keyed (sku_code, week_key) -> new_qty,
containing ONLY the cells whose quantity actually changed. Applying it to the
session overlay and re-running service.build_production yields zero R1-R4
breaches on every unlocked week (asserted in tests/test_autofix.py, which
checks compliance against the real service, not a re-implementation here).
"""
from __future__ import annotations

import backend.capacity as cap_engine

MAX_SKUS_PER_WEEK = 3


def _params(dataset: dict) -> dict:
    """Flatten the parameter DataFrame into a name -> value dict."""
    return dict(zip(dataset["parameter"]["name"], dataset["parameter"]["value"]))


def _resolved_qty_map(dataset: dict, overlay: dict) -> dict[tuple, float]:
    """plan_line.planned_qty with the session overlay applied."""
    qty: dict[tuple, float] = {}
    for _, row in dataset["plan_line"].iterrows():
        qty[(row["sku_code"], row["week_key"])] = float(row["planned_qty"])
    for key, val in overlay.items():
        qty[key] = float(val)
    return qty


def _changeover_weeks(
    target: dict[tuple, float],
    week_keys: list[str],
    all_skus: list[str],
    pack: dict[str, int],
) -> set[str]:
    """Weeks where the (min) pack size differs from the previous producing week.

    Mirrors service.build_production exactly: each producing week's canonical
    pack is min(packs producing that week); the first producing week is never a
    changeover.
    """
    changeover: set[str] = set()
    prev_pack: int | None = None
    for wk in week_keys:
        packs = {pack[s] for s in all_skus if target[(s, wk)] > 0}
        if not packs:
            continue
        this_pack = min(packs)
        if prev_pack is not None and this_pack != prev_pack:
            changeover.add(wk)
        prev_pack = this_pack
    return changeover


def strict_trim(dataset: dict, overlay: dict | None = None) -> dict:
    """Compute a rules-compliant overlay by strict trimming.

    Parameters
    ----------
    dataset : dict
        Output of data_gen.generate() (or the live UC-backed equivalent).
    overlay : dict | None
        Current session overlay keyed (sku_code, week_key) -> planned_qty.

    Returns
    -------
    dict with keys:
      overlay : {(sku_code, week_key): new_qty}  -- only the CHANGED cells
      report  : {weeks_changed, cells_zeroed, cells_trimmed, volume_dropped,
                 locked_weeks_skipped}
    """
    if overlay is None:
        overlay = {}

    params = _params(dataset)
    sku_df = dataset["sku"]
    week_df = (
        dataset["week"].sort_values("horizon_index").reset_index(drop=True)
    )

    priority = dict(zip(sku_df["sku_code"], sku_df["priority"].astype(int)))
    pack = dict(zip(sku_df["sku_code"], sku_df["pack_size_ml"].astype(int)))
    all_skus: list[str] = list(sku_df["sku_code"])

    week_keys: list[str] = list(week_df["week_key"])
    is_locked = dict(zip(week_df["week_key"], week_df["is_locked"].astype(bool)))
    maint = dict(zip(week_df["week_key"], week_df["maintenance_type"].astype(str)))

    cur = _resolved_qty_map(dataset, overlay)
    target = dict(cur)

    weeks_changed: set[str] = set()
    cells_zeroed = 0
    cells_trimmed = 0
    volume_dropped = 0.0
    locked_weeks_skipped = 0

    # Pass 1: R4 / R1 / R2 -- pick a single pack and at most 3 SKUs per week.
    for wk in week_keys:
        if is_locked[wk]:
            locked_weeks_skipped += 1
            continue

        producing = [s for s in all_skus if target[(s, wk)] > 0]
        if not producing:
            continue

        # R4: full maintenance -> zero everything this week.
        if maint[wk] == "Full":
            for s in producing:
                volume_dropped += target[(s, wk)]
                target[(s, wk)] = 0.0
                cells_zeroed += 1
            weeks_changed.add(wk)
            continue

        # R1: keep the pack of the highest-priority producing SKU.
        top_sku = min(producing, key=lambda s: priority[s])
        kept_pack = pack[top_sku]

        # R2: among that pack's SKUs, keep the top 3 by priority.
        kept_candidates = sorted(
            (s for s in producing if pack[s] == kept_pack),
            key=lambda s: priority[s],
        )
        keep = set(kept_candidates[:MAX_SKUS_PER_WEEK])

        for s in producing:
            if s not in keep:
                volume_dropped += target[(s, wk)]
                target[(s, wk)] = 0.0
                cells_zeroed += 1
        weeks_changed.add(wk)

    # Pass 2: R3 -- trim to the ceiling, using the changeover state of the
    # trimmed single-pack schedule (computed the same way as the service).
    changeover = _changeover_weeks(target, week_keys, all_skus, pack)
    for wk in week_keys:
        if is_locked[wk] or maint[wk] == "Full":
            continue
        kept = sorted(
            (s for s in all_skus if target[(s, wk)] > 0),
            key=lambda s: priority[s],
        )
        if not kept:
            continue
        pack_ml = min(pack[s] for s in kept)
        ceil_val = cap_engine.ceiling(pack_ml, len(kept), wk in changeover, params)
        total = sum(target[(s, wk)] for s in kept)
        if total <= ceil_val:
            continue
        over = total - ceil_val
        # Trim the lowest-priority kept SKUs first.
        for s in reversed(kept):
            if over <= 0:
                break
            q = target[(s, wk)]
            cut = min(q, over)
            target[(s, wk)] = q - cut
            over -= cut
            volume_dropped += cut
            cells_trimmed += 1
        weeks_changed.add(wk)

    # Only emit cells whose quantity actually changed.
    changes = {key: q for key, q in target.items() if q != cur[key]}

    report = {
        "weeks_changed": len(weeks_changed),
        "cells_zeroed": cells_zeroed,
        "cells_trimmed": cells_trimmed,
        "volume_dropped": volume_dropped,
        "locked_weeks_skipped": locked_weeks_skipped,
    }
    return {"overlay": changes, "report": report}
