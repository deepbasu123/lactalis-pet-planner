"""tests/test_api_live.py

End-to-end tests of the serving layer against real Silver on deep-test-1.
Gated on PET_IT=1. Proves the thin API serves the SQL engine correctly and
that edits recompute through the warehouse.
"""
import os

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("PET_IT") != "1",
    reason="live integration test; set PET_IT=1 (needs deep-test-1 + silver_dev)",
)

_SCENARIO = "api_it"


@pytest.fixture(scope="module", autouse=True)
def _cfg():
    from backend import warehouse
    from backend.config import settings
    settings.catalog = "deep_test_1_catalog"
    settings.silver_schema = "lactalis_pet_silver_dev"
    settings.warehouse_id = "5eb2345986480cea"
    os.environ.setdefault("DATABRICKS_CONFIG_PROFILE", "deep-test-1")
    warehouse.get_workspace_client.cache_clear()
    warehouse.overlay_discard(_SCENARIO)
    yield
    warehouse.overlay_discard(_SCENARIO)


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient
    from backend.main import app
    return TestClient(app)


def test_supply_has_572_rows(client):
    r = client.get("/api/supply")
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 572
    assert all(row["colour"] in {
        "dark_blue", "light_blue", "green", "amber", "red", "dark_red", "black"
    } for row in body)


def test_summary_counts_sum_to_572(client):
    body = client.get("/api/summary").json()
    assert sum(body["counts"].values()) == 572


def test_production_has_52_weeks(client):
    body = client.get("/api/production").json()
    assert len(body["week_totals"]) == 52
    assert len(body["rows"]) == 572  # 11 SKUs x 52 weeks


def test_api_supply_matches_direct_gold_sql(client):
    """The endpoint must serve exactly what the Gold SQL returns (no mangling)."""
    from backend import warehouse
    from medallion.gold_sql import supply_select
    direct = warehouse.run_sql(supply_select(warehouse.silver()))
    api = client.get("/api/supply").json()
    dkey = {(r["sku_code"], r["week_key"]): r for r in direct}
    akey = {(r["sku_code"], r["week_key"]): r for r in api}
    assert dkey.keys() == akey.keys()
    mism = [k for k in dkey if abs(dkey[k]["close"] - akey[k]["close"]) > 1e-6
            or dkey[k]["colour"] != akey[k]["colour"]]
    assert mism == []


def test_edit_recomputes_and_reflects_in_supply(client):
    # pick an unlocked, currently-producing cell and zero it
    sku, week = "61108", "2026-W40"
    resp = client.post("/api/plan/edit", json={
        "sku_code": sku, "week_key": week, "planned_qty": 0.0, "scenario": _SCENARIO,
    })
    assert resp.status_code == 200
    supply = resp.json()["supply"]
    cell = next(r for r in supply if r["sku_code"] == sku and r["week_key"] == week)
    assert cell["prod"] == 0.0
    client.post("/api/plan/discard", json={"scenario": _SCENARIO})


def test_locked_week_edit_rejected(client):
    # weeks 1-3 are time-fence locked in the seeded data
    weeks = client.get("/api/meta").json()["weeks"]
    locked = next(w["week_key"] for w in weeks if w["is_locked"])
    resp = client.post("/api/plan/edit", json={
        "sku_code": "61108", "week_key": locked, "planned_qty": 1.0, "scenario": _SCENARIO,
    })
    assert resp.status_code == 409
