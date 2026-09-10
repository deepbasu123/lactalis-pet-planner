"""tests/test_api.py

TDD tests for the read API endpoints.  PET_LIVE is unset so all calls use
synthetic data produced by data_gen.generate() -- no Databricks workspace
needed.
"""
import json
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from backend.main import app

CANONICAL_COLOURS = {
    "dark_blue",
    "light_blue",
    "green",
    "amber",
    "red",
    "dark_red",
    "black",
}

_client = TestClient(app)

_JSON_NATIVE = (int, float, str, bool, type(None))


def _no_nan(text: str) -> bool:
    """Return True only if the raw response text contains no NaN literal."""
    return "NaN" not in text


def _all_json_native(obj) -> bool:
    """Recursively verify every leaf value is a JSON-native Python type.

    Catches non-NaN numpy scalars (e.g. numpy.int64) that _no_nan() misses
    because they serialise to a valid number string rather than 'NaN'.
    """
    if isinstance(obj, dict):
        return all(_all_json_native(v) for v in obj.values())
    if isinstance(obj, list):
        return all(_all_json_native(item) for item in obj)
    return isinstance(obj, _JSON_NATIVE)


# ---------------------------------------------------------------------------
# /api/config
# ---------------------------------------------------------------------------

class TestConfig:
    def test_status_200(self):
        r = _client.get("/api/config")
        assert r.status_code == 200

    def test_eleven_skus(self):
        r = _client.get("/api/config")
        data = r.json()
        assert len(data["skus"]) == 11

    def test_fiftytwo_weeks(self):
        r = _client.get("/api/config")
        data = r.json()
        assert len(data["weeks"]) == 52

    def test_ten_parameters(self):
        r = _client.get("/api/config")
        data = r.json()
        assert len(data["parameters"]) == 10

    def test_no_nan(self):
        r = _client.get("/api/config")
        assert _no_nan(r.text)


# ---------------------------------------------------------------------------
# /api/supply
# ---------------------------------------------------------------------------

class TestSupply:
    def test_status_200(self):
        r = _client.get("/api/supply")
        assert r.status_code == 200

    def test_572_cells(self):
        r = _client.get("/api/supply")
        data = r.json()
        assert len(data["rows"]) == 572

    def test_every_cell_has_canonical_colour(self):
        r = _client.get("/api/supply")
        data = r.json()
        for cell in data["rows"]:
            assert cell["colour"] in CANONICAL_COLOURS, (
                f"Unexpected colour: {cell['colour']!r}"
            )

    def test_no_nan(self):
        r = _client.get("/api/supply")
        assert _no_nan(r.text)


# ---------------------------------------------------------------------------
# /api/summary
# ---------------------------------------------------------------------------

class TestSummary:
    def test_status_200(self):
        r = _client.get("/api/summary")
        assert r.status_code == 200

    def test_colour_counts_sum_to_572(self):
        r = _client.get("/api/summary")
        data = r.json()
        total = sum(data["counts"].values())
        assert total == 572

    def test_no_nan(self):
        r = _client.get("/api/summary")
        assert _no_nan(r.text)


# ---------------------------------------------------------------------------
# /api/production
# ---------------------------------------------------------------------------

class TestProduction:
    def test_status_200(self):
        r = _client.get("/api/production")
        assert r.status_code == 200

    def test_week_totals_length_52(self):
        r = _client.get("/api/production")
        data = r.json()
        assert len(data["week_totals"]) == 52

    def test_no_nan(self):
        r = _client.get("/api/production")
        assert _no_nan(r.text)

    def test_rows_contain_only_json_native_types(self):
        """Catch non-NaN numpy scalars that _no_nan misses (e.g. numpy.int64)."""
        r = _client.get("/api/production")
        data = r.json()
        for i, row in enumerate(data["rows"]):
            assert _all_json_native(row), (
                f"Row {i} contains a non-JSON-native value: {row}"
            )


# ---------------------------------------------------------------------------
# Edit / save / discard overlay endpoints
# ---------------------------------------------------------------------------

# Dedicated TestClient so the session cookie persists across all edit-test
# requests without touching the shared _client used by the read-only tests.
_edit_client = TestClient(app)

# Locked weeks (horizon_index 1-3 -> 2026-W35, 2026-W36, 2026-W37)
_LOCKED_WEEK = "2026-W35"
# Non-locked weeks used across the four tests (choose different weeks so
# edits from one test do not cascade into another test's supply assertions).
_WEEK_A = "2026-W38"   # used by test_edit_overlay_changes_supply
_WEEK_B = "2026-W40"   # used by test_discard_clears_overlay
_WEEK_C = "2026-W41"   # used by test_save_clears_overlay_nolive
_SKU = "61108"          # OAK UHT CHOCOLATE 500ML -- non-shortage SKU, high volume


class TestEditOverlay:
    """TDD tests for the working-copy overlay endpoints."""

    def test_edit_overlay_changes_supply(self):
        """Edit a non-locked cell to 0; GET /api/supply should show prod=0 and
        change the closing stock of a downstream week (QA hold = 2 weeks).

        The engine applies qa_hold_weeks=2: production from W38 (horizon_index=4)
        is received as `recv` in W40 (horizon_index=6).  The edited week's own
        `close` is unaffected; the first visible change is two weeks downstream.
        """
        # Baseline supply for SKU 61108 indexed by week_key
        rows_before = {
            row["week_key"]: row
            for row in _edit_client.get("/api/supply").json()["rows"]
            if row["sku_code"] == _SKU
        }
        assert rows_before[_WEEK_A]["prod"] != 0.0, (
            "Pre-condition: baseline planned_qty for W38 should be non-zero"
        )

        # Edit WEEK_A (2026-W38) to zero
        r_edit = _edit_client.post("/api/production/edit", json={
            "sku_code": _SKU, "week_key": _WEEK_A, "qty": 0.0,
        })
        assert r_edit.status_code == 200

        # GET /api/supply should reflect the overlay
        rows_after = {
            row["week_key"]: row
            for row in _edit_client.get("/api/supply").json()["rows"]
            if row["sku_code"] == _SKU
        }

        # The edited cell shows prod=0 (overlay applied)
        assert rows_after[_WEEK_A]["prod"] == 0.0

        # Downstream close changes: W38 production (qa_hold=2) lands in W40.
        # W40 close must differ from baseline because recv is now 0 there.
        _QA_DOWNSTREAM = "2026-W40"
        assert rows_after[_QA_DOWNSTREAM]["close"] != rows_before[_QA_DOWNSTREAM]["close"]

    def test_edit_locked_week_returns_409(self):
        """Editing a locked week must return HTTP 409."""
        r = _edit_client.post("/api/production/edit", json={
            "sku_code": _SKU, "week_key": _LOCKED_WEEK, "qty": 999.0,
        })
        assert r.status_code == 409

    def test_discard_clears_overlay(self):
        """After discard, GET /api/supply returns baseline close for the edited cell."""
        # Get the baseline close for WEEK_B using the clean shared client
        # (different session, no overlay).
        baseline_rows = _client.get("/api/supply").json()["rows"]
        cell_baseline = next(
            row for row in baseline_rows
            if row["sku_code"] == _SKU and row["week_key"] == _WEEK_B
        )

        # Edit WEEK_B to zero on the edit client
        _edit_client.post("/api/production/edit", json={
            "sku_code": _SKU, "week_key": _WEEK_B, "qty": 0.0,
        })

        # Discard the entire session overlay
        r_discard = _edit_client.post("/api/production/discard")
        assert r_discard.status_code == 200

        # Supply should now match the clean baseline close for WEEK_B
        rows_after = _edit_client.get("/api/supply").json()["rows"]
        cell_after = next(
            row for row in rows_after
            if row["sku_code"] == _SKU and row["week_key"] == _WEEK_B
        )
        assert cell_after["close"] == cell_baseline["close"]

    def test_save_clears_overlay_nolive(self):
        """Save (PET_LIVE unset) returns success with saved>=1 and clears the overlay."""
        # Edit WEEK_C to zero
        _edit_client.post("/api/production/edit", json={
            "sku_code": _SKU, "week_key": _WEEK_C, "qty": 0.0,
        })

        # Save
        r_save = _edit_client.post("/api/production/save")
        assert r_save.status_code == 200
        data = r_save.json()
        assert data["saved"] >= 1

        # After save the overlay is cleared -- supply should match the clean baseline
        baseline_rows = _client.get("/api/supply").json()["rows"]
        rows_after = _edit_client.get("/api/supply").json()["rows"]

        cell_baseline = next(
            row for row in baseline_rows
            if row["sku_code"] == _SKU and row["week_key"] == _WEEK_C
        )
        cell_after = next(
            row for row in rows_after
            if row["sku_code"] == _SKU and row["week_key"] == _WEEK_C
        )
        assert cell_after["close"] == cell_baseline["close"]


# ---------------------------------------------------------------------------
# PUT /api/parameters, PUT /api/weeks, PUT /api/skus
# ---------------------------------------------------------------------------

# Use a dedicated client so session overlays from the edit tests are isolated.
_put_client = TestClient(app)


class TestPutEndpoints:
    """TDD tests for config PUT endpoints (PET_LIVE unset: in-memory mutation)."""

    def teardown_method(self, method):
        """Reset the module-level dataset cache after each test so mutations
        from one test do not bleed into subsequent tests or into the read-only
        TestConfig / TestSummary test expectations."""
        from backend.main import refresh_dataset
        refresh_dataset()

    def test_put_parameter_updates_value(self):
        """PUT /api/parameters -> GET /api/config shows the new value."""
        name = "cap_400ml_1_2_sku"
        new_value = 999999.0

        r = _put_client.put("/api/parameters", json={"name": name, "value": new_value})
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

        config = _put_client.get("/api/config").json()
        param = next(p for p in config["parameters"] if p["name"] == name)
        assert param["value"] == new_value

    def test_put_parameter_unknown_name_returns_404(self):
        """PUT /api/parameters with an unknown parameter name returns HTTP 404."""
        r = _put_client.put("/api/parameters", json={"name": "nonexistent_param", "value": 1.0})
        assert r.status_code == 404

    def test_put_week_is_locked_reflected_in_config(self):
        """PUT /api/weeks is_locked change -> GET /api/config shows updated value."""
        # Week 2026-W38 is not locked by default (horizon_index=4, outside time fence)
        week_key = "2026-W38"
        r = _put_client.put("/api/weeks", json={"week_key": week_key, "is_locked": True})
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

        config = _put_client.get("/api/config").json()
        week = next(w for w in config["weeks"] if w["week_key"] == week_key)
        assert week["is_locked"] is True

    def test_put_week_unknown_key_returns_404(self):
        """PUT /api/weeks with an unknown week_key returns HTTP 404."""
        r = _put_client.put("/api/weeks", json={"week_key": "9999-W99"})
        assert r.status_code == 404

    def test_put_sku_priority_reflected_in_config(self):
        """PUT /api/skus priority change -> GET /api/config shows updated value."""
        sku_code = "61108"
        r = _put_client.put("/api/skus", json={"sku_code": sku_code, "priority": 99})
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

        config = _put_client.get("/api/config").json()
        sku = next(s for s in config["skus"] if s["sku_code"] == sku_code)
        assert sku["priority"] == 99

    def test_put_sku_unknown_code_returns_404(self):
        """PUT /api/skus with an unknown sku_code returns HTTP 404."""
        r = _put_client.put("/api/skus", json={"sku_code": "NOSUCHSKU"})
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# C2 regression: save overlay conversion (live mode)
# ---------------------------------------------------------------------------

class TestSaveOverlayConversion:
    """C2: POST /api/production/save must convert overlay dict to a rows list.

    In live mode (PET_LIVE=1) the endpoint previously passed the overlay dict
    directly to db.merge_plan_lines(), which iterates it as if it were a list
    of row dicts -- this caused a TypeError.

    Strategy: edit endpoints run WITHOUT PET_LIVE so the synthetic dataset is
    loaded into the module cache.  PET_LIVE=1 is set only immediately before
    the save call so the live-mode branch executes with the cached synthetic
    data and mocked db functions -- no Databricks workspace needed.
    """

    def setup_method(self, _method):
        """Clear dataset cache and session overlays before each test."""
        import os
        os.environ.pop("PET_LIVE", None)  # ensure synthetic mode during edit
        from backend.main import refresh_dataset, _SESSION_OVERLAYS
        refresh_dataset()
        _SESSION_OVERLAYS.clear()

    def teardown_method(self, _method):
        """Restore clean state and unset PET_LIVE so other tests are unaffected."""
        import os
        os.environ.pop("PET_LIVE", None)
        from backend.main import refresh_dataset, _SESSION_OVERLAYS
        refresh_dataset()
        _SESSION_OVERLAYS.clear()

    # Reusable empty DataFrame matching the snapshot column shape
    _EMPTY_DF = pd.DataFrame(columns=[
        "sku_code", "week_key", "horizon_index", "opening", "recv",
        "prod", "demand", "raw", "close", "cover_weeks", "severity", "colour",
    ])

    def test_save_builds_rows_list_for_merge(self, monkeypatch):
        """Overlay dict is converted to [{sku_code, week_key, planned_qty}] for merge."""
        save_client = TestClient(app)

        # Edit WITHOUT PET_LIVE -> loads synthetic dataset into cache
        r_edit = save_client.post("/api/production/edit", json={
            "sku_code": "61108", "week_key": "2026-W38", "qty": 500.0,
        })
        assert r_edit.status_code == 200

        # Switch to live mode ONLY for the save call; dataset is already cached
        monkeypatch.setenv("PET_LIVE", "1")
        mock_merge = MagicMock()

        with patch("backend.db.merge_plan_lines", mock_merge), \
             patch("backend.db.write_snapshot"), \
             patch("backend.service.build_supply", return_value=self._EMPTY_DF):
            r_save = save_client.post("/api/production/save")

        assert r_save.status_code == 200
        assert r_save.json()["saved"] == 1

        # merge_plan_lines must have been called with a LIST (not a dict)
        mock_merge.assert_called_once()
        rows_arg = mock_merge.call_args[0][0]
        assert isinstance(rows_arg, list), (
            f"merge_plan_lines must receive a list, got {type(rows_arg).__name__}"
        )
        assert len(rows_arg) == 1
        row = rows_arg[0]
        assert isinstance(row, dict)
        assert row["sku_code"] == "61108"
        assert row["week_key"] == "2026-W38"
        assert row["planned_qty"] == pytest.approx(500.0)

    def test_empty_overlay_does_not_call_merge(self, monkeypatch):
        """With no pending edits, merge_plan_lines must NOT be called at all."""
        save_client = TestClient(app)

        # Load synthetic dataset into cache (no edit needed, just hit /api/config)
        save_client.get("/api/config")

        # Switch to live mode; overlay is empty
        monkeypatch.setenv("PET_LIVE", "1")
        mock_merge = MagicMock()

        with patch("backend.db.merge_plan_lines", mock_merge), \
             patch("backend.db.write_snapshot"), \
             patch("backend.service.build_supply", return_value=self._EMPTY_DF):
            r_save = save_client.post("/api/production/save")

        assert r_save.status_code == 200
        mock_merge.assert_not_called()

    def test_save_multi_row_overlay_all_rows_present(self, monkeypatch):
        """Multiple overlay entries all land in the rows list."""
        save_client = TestClient(app)

        # Edit two different cells WITHOUT PET_LIVE
        save_client.post("/api/production/edit", json={
            "sku_code": "61108", "week_key": "2026-W38", "qty": 100.0,
        })
        save_client.post("/api/production/edit", json={
            "sku_code": "61747", "week_key": "2026-W39", "qty": 200.0,
        })

        monkeypatch.setenv("PET_LIVE", "1")
        mock_merge = MagicMock()

        with patch("backend.db.merge_plan_lines", mock_merge), \
             patch("backend.db.write_snapshot"), \
             patch("backend.service.build_supply", return_value=self._EMPTY_DF):
            r_save = save_client.post("/api/production/save")

        assert r_save.status_code == 200
        rows_arg = mock_merge.call_args[0][0]
        assert isinstance(rows_arg, list)
        assert len(rows_arg) == 2
        keys = {(r["sku_code"], r["week_key"]) for r in rows_arg}
        assert ("61108", "2026-W38") in keys
        assert ("61747", "2026-W39") in keys


# ---------------------------------------------------------------------------
# POST /api/production/autofix
# ---------------------------------------------------------------------------

def _locked_week_keys(client) -> set[str]:
    cfg = client.get("/api/config").json()
    return {w["week_key"] for w in cfg["weeks"] if w["is_locked"]}


class TestAutoFix:
    def test_returns_ok_with_changes_and_report(self):
        client = TestClient(app)
        r = client.post("/api/production/autofix")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert isinstance(body["changed"], list)
        assert len(body["changed"]) > 0
        for k in ("weeks_changed", "cells_zeroed", "volume_dropped",
                  "locked_weeks_skipped"):
            assert k in body["report"]

    def test_unlocked_weeks_compliant_after_autofix(self):
        """After autofix, GET /api/production shows no R1-R4 breach off-lock."""
        client = TestClient(app)
        locked = _locked_week_keys(client)
        client.post("/api/production/autofix")
        prod = client.get("/api/production").json()
        offenders = [
            wk for wk, f in prod["week_flags"].items()
            if wk not in locked and (f["R1"] or f["R2"] or f["R3"] or f["R4"])
        ]
        assert offenders == [], f"unlocked weeks still breaching: {offenders}"

    def test_autofix_writes_to_overlay_then_discard_restores(self):
        client = TestClient(app)
        client.post("/api/production/autofix")
        # The overlay now holds the changes; discard should clear them all.
        r_discard = client.post("/api/production/discard")
        assert r_discard.status_code == 200
        assert r_discard.json()["cleared"] > 0

    def test_does_not_touch_locked_weeks(self):
        client = TestClient(app)
        locked = _locked_week_keys(client)
        changed = client.post("/api/production/autofix").json()["changed"]
        for cell in changed:
            assert cell["week_key"] not in locked
