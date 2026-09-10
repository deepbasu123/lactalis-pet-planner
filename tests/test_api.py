"""tests/test_api.py

TDD tests for the read API endpoints.  PET_LIVE is unset so all calls use
synthetic data produced by data_gen.generate() -- no Databricks workspace
needed.
"""
import json

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
