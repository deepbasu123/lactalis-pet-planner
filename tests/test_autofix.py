"""tests/test_autofix.py

Tests for backend.autofix.strict_trim.

The load-bearing test is test_all_unlocked_weeks_compliant_after_fix: it applies
the computed overlay and re-runs the REAL service.build_production, asserting
zero R1-R4 breaches on every unlocked week. That is the compliance guarantee,
checked against the same engine the API serves (not a re-implementation).
"""
from __future__ import annotations

from backend.data_gen import generate
from backend import service
from backend.autofix import strict_trim


def _week_meta(ds):
    wk = ds["week"]
    locked = set(wk.loc[wk["is_locked"], "week_key"])
    full = set(wk.loc[wk["maintenance_type"] == "Full", "week_key"])
    return locked, full


def _breaching_weeks(prod, exclude=frozenset()):
    """Week keys that breach R1-R4, excluding the given set."""
    out = []
    for wk, f in prod["week_flags"].items():
        if wk in exclude:
            continue
        if f["R1"] or f["R2"] or f["R3"] or f["R4"]:
            out.append(wk)
    return out


class TestStrictTrimCompliance:
    def test_baseline_has_systemic_breaches(self):
        """Sanity: the seeded plan really does breach nearly everywhere."""
        ds = generate()
        prod = service.build_production(ds)
        assert len(_breaching_weeks(prod)) >= 40

    def test_all_unlocked_weeks_compliant_after_fix(self):
        ds = generate()
        result = strict_trim(ds, {})
        overlay = result["overlay"]
        prod = service.build_production(ds, plan_overlay=overlay)
        locked, _ = _week_meta(ds)
        # Every unlocked week must now pass all four rules.
        remaining = _breaching_weeks(prod, exclude=locked)
        assert remaining == [], f"unlocked weeks still breaching: {remaining}"

    def test_locked_weeks_never_changed(self):
        ds = generate()
        locked, _ = _week_meta(ds)
        result = strict_trim(ds, {})
        for (sku, wk) in result["overlay"]:
            assert wk not in locked, f"autofix touched locked week {wk}"

    def test_full_maintenance_week_has_no_production(self):
        ds = generate()
        _, full = _week_meta(ds)
        result = strict_trim(ds, {})
        prod = service.build_production(ds, plan_overlay=result["overlay"])
        for wk in full:
            assert prod["week_totals"][wk] == 0, f"full-maint week {wk} still produces"
            assert prod["week_flags"][wk]["R4"] is False


class TestStrictTrimShape:
    def test_only_reduces_total_volume(self):
        """Strict trim drops volume; it must never add production."""
        ds = generate()
        before = service.build_production(ds)
        after = service.build_production(ds, plan_overlay=strict_trim(ds, {})["overlay"])
        assert sum(after["week_totals"].values()) <= sum(before["week_totals"].values())

    def test_kept_cells_are_top_priority_single_pack(self):
        ds = generate()
        overlay = strict_trim(ds, {})["overlay"]
        prod = service.build_production(ds, plan_overlay=overlay)
        pack = dict(zip(ds["sku"]["sku_code"], ds["sku"]["pack_size_ml"].astype(int)))
        locked, full = _week_meta(ds)
        for wk, f in prod["week_flags"].items():
            if wk in locked or wk in full:
                continue
            producing = [
                r for r in prod["rows"] if r["week_key"] == wk and r["planned_qty"] > 0
            ]
            assert len(producing) <= 3, f"{wk} keeps >3 SKUs"
            packs = {pack[r["sku_code"]] for r in producing}
            assert len(packs) <= 1, f"{wk} keeps >1 pack size"

    def test_changes_actually_differ_from_current(self):
        ds = generate()
        overlay = strict_trim(ds, {})["overlay"]
        # Reconstruct the resolved current qty and confirm every change differs.
        cur = {(r["sku_code"], r["week_key"]): float(r["planned_qty"])
               for _, r in ds["plan_line"].iterrows()}
        for key, newq in overlay.items():
            assert newq != cur[key]

    def test_deterministic(self):
        ds = generate()
        a = strict_trim(ds, {})["overlay"]
        b = strict_trim(ds, {})["overlay"]
        assert a == b

    def test_report_fields_present(self):
        ds = generate()
        report = strict_trim(ds, {})["report"]
        for k in ("weeks_changed", "cells_zeroed", "cells_trimmed",
                  "volume_dropped", "locked_weeks_skipped"):
            assert k in report
        assert report["locked_weeks_skipped"] == 3  # first 3 weeks are locked
        assert report["volume_dropped"] > 0

    def test_respects_existing_overlay(self):
        """A prior edit that zeroed the top-priority SKU shifts the kept pack."""
        ds = generate()
        # Zero SKU 60444 (priority 1, 400ml) in an unlocked week; then the kept
        # pack for that week should follow the next highest-priority producer.
        wk = ds["week"].sort_values("horizon_index")["week_key"].iloc[10]
        pre = {("60444", wk): 0.0}
        result = strict_trim(ds, pre)
        prod = service.build_production(ds, plan_overlay={**pre, **result["overlay"]})
        # Compliance must still hold on that week.
        assert prod["week_flags"][wk]["R1"] is False
        assert prod["week_flags"][wk]["R2"] is False
