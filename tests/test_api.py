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


def _no_nan(text: str) -> bool:
    """Return True only if the raw response text contains no NaN literal."""
    return "NaN" not in text


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
