"""tests/test_api.py

Unit tests for the thin serving layer. The warehouse boundary
(backend.warehouse) is mocked, so these run offline and assert response
SHAPING + validation, not the SQL maths (that is proven separately by the SQL
golden regression and by tests/test_api_live.py against real Silver).
"""
import pytest
from fastapi.testclient import TestClient

import backend.main as m
from backend import warehouse

client = TestClient(m.app)

_SUPPLY_ROW = {
    "sku_code": "60444", "week_key": "2026-W35", "horizon_index": 1,
    "opening": 100.0, "recv": 0.0, "prod": 24000.0, "demand": 23000.0,
    "raw": 77000.0, "close": 77000.0, "cover_weeks": 3, "severity": 6,
    "colour": "dark_blue", "display_value": 77000.0, "is_lost_sale": False,
}
_PROD_ROW = {
    "week_key": "2026-W35", "horizon_index": 1, "maintenance_type": "None",
    "total": 450000.0, "n_skus": 11, "n_packs": 2, "pack_ml": 400,
    "is_changeover": False, "ceiling": 650000.0, "r1": True, "r2": True,
    "r3": False, "r4": False, "no_rule": False, "over_units": 0,
}
_PLAN_ROWS = [{"sku_code": "60444", "week_key": "2026-W35", "planned_qty": 24000.0}]
_COLOUR_ROWS = [{"colour": "dark_blue", "n": 400}, {"colour": "red", "n": 172}]


def test_health_ok():
    assert client.get("/api/health").json() == {"status": "ok"}


def test_meta_shape(monkeypatch):
    monkeypatch.setattr(warehouse, "run_sql", lambda sql: [{"a": 1}])
    r = client.get("/api/meta")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"skus", "weeks", "parameters"}


def test_supply_returns_bare_list(monkeypatch):
    monkeypatch.setattr(warehouse, "run_sql", lambda sql: [_SUPPLY_ROW])
    r = client.get("/api/supply")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, list)
    assert body[0]["colour"] == "dark_blue"
    assert body[0]["severity"] == 6


def test_production_maps_flags_and_counts_changeovers(monkeypatch):
    rows = [
        dict(_PROD_ROW),
        {**_PROD_ROW, "week_key": "2026-W36", "is_changeover": True, "r3": True, "over_units": 5000},
    ]
    monkeypatch.setattr(warehouse, "run_sql", lambda sql: rows)
    monkeypatch.setattr(warehouse, "effective_plan_rows", lambda scenario: _PLAN_ROWS)
    r = client.get("/api/production")
    assert r.status_code == 200
    body = r.json()
    assert body["changeovers"] == 1
    assert set(body["week_flags"]["2026-W35"]) == {"R1", "R2", "R3", "R4", "no_rule", "over"}
    assert body["week_flags"]["2026-W36"]["R3"] is True
    assert body["week_flags"]["2026-W36"]["over"] == 5000
    assert body["week_totals"]["2026-W35"] == 450000.0
    assert body["rows"] == _PLAN_ROWS


def test_summary_shape(monkeypatch):
    monkeypatch.setattr(warehouse, "run_sql", lambda sql: _COLOUR_ROWS)
    r = client.get("/api/summary")
    assert r.status_code == 200
    body = r.json()
    assert body["counts"]["dark_blue"] == 400
    assert set(body["original_vs_plan"]) == {"original", "working"}


def test_edit_rejects_locked_week(monkeypatch):
    monkeypatch.setattr(warehouse, "week_is_locked", lambda wk: True)
    r = client.post("/api/plan/edit", json={
        "sku_code": "60444", "week_key": "2026-W35", "planned_qty": 5.0,
    })
    assert r.status_code == 409


def test_edit_404_unknown_week(monkeypatch):
    monkeypatch.setattr(warehouse, "week_is_locked", lambda wk: None)
    r = client.post("/api/plan/edit", json={
        "sku_code": "60444", "week_key": "2099-W99", "planned_qty": 5.0,
    })
    assert r.status_code == 404


def test_edit_clamps_negative_and_merges_then_recomputes(monkeypatch):
    captured = {}
    monkeypatch.setattr(warehouse, "week_is_locked", lambda wk: False)
    monkeypatch.setattr(warehouse, "overlay_merge", lambda scenario, changes: captured.update(scenario=scenario, changes=changes))
    monkeypatch.setattr(m, "_supply", lambda scenario: [_SUPPLY_ROW])
    monkeypatch.setattr(m, "_production", lambda scenario: m.ProductionResponse(rows=_PLAN_ROWS, week_totals={"2026-W35": 1.0}, week_flags={}, changeovers=0))
    monkeypatch.setattr(m, "_summary", lambda scenario: m.SummaryResponse(counts={"green": 572}, original_vs_plan=m.SummaryOrigVsPlan(original={}, working={})))
    r = client.post("/api/plan/edit", json={
        "sku_code": "60444", "week_key": "2026-W40", "planned_qty": -99.0, "scenario": "working",
    })
    assert r.status_code == 200
    assert captured["changes"][0]["planned_qty"] == 0.0  # negative clamped
    body = r.json()
    assert isinstance(body["supply"], list)
    assert "production" in body and "summary" in body


def test_save_calls_overlay_save(monkeypatch):
    called = {}
    monkeypatch.setattr(warehouse, "overlay_save", lambda scenario: called.setdefault("s", scenario))
    r = client.post("/api/plan/save", json={"scenario": "working"})
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert called["s"] == "working"


def test_discard_calls_overlay_discard(monkeypatch):
    called = {}
    monkeypatch.setattr(warehouse, "overlay_discard", lambda scenario: called.setdefault("s", scenario))
    r = client.post("/api/plan/discard", json={"scenario": "working"})
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert called["s"] == "working"


def test_autofix_runs_and_returns_report(monkeypatch):
    import backend.autofix as af
    monkeypatch.setattr(af, "strict_trim", lambda scenario: {"weeks_changed": 3, "cells_zeroed": 10})
    monkeypatch.setattr(m, "_supply", lambda scenario: [])
    monkeypatch.setattr(m, "_production", lambda scenario: m.ProductionResponse(rows=[], week_totals={}, week_flags={}, changeovers=0))
    monkeypatch.setattr(m, "_summary", lambda scenario: m.SummaryResponse(counts={}, original_vs_plan=m.SummaryOrigVsPlan(original={}, working={})))
    r = client.post("/api/autofix", json={"scenario": "working"})
    assert r.status_code == 200
    assert r.json()["report"]["weeks_changed"] == 3


def test_upload_503_without_job(monkeypatch):
    monkeypatch.setattr(m.settings, "job_id", "")
    r = client.post("/api/upload", files={"file": ("x.xlsx", b"data", "application/octet-stream")})
    assert r.status_code == 503


def _stub_grids(monkeypatch):
    monkeypatch.setattr(m, "_supply", lambda scenario: [_SUPPLY_ROW])
    monkeypatch.setattr(m, "_production", lambda scenario: m.ProductionResponse(rows=[], week_totals={}, week_flags={}, changeovers=0))
    monkeypatch.setattr(m, "_summary", lambda scenario: m.SummaryResponse(counts={}, original_vs_plan=m.SummaryOrigVsPlan(original={}, working={})))


def test_reset_week_discards_week_overlay_and_recomputes(monkeypatch):
    called = {}
    monkeypatch.setattr(warehouse, "overlay_discard_week", lambda scenario, wk: called.update(s=scenario, wk=wk))
    _stub_grids(monkeypatch)
    r = client.post("/api/plan/reset-week", json={"week_key": "2026-W40", "scenario": "working"})
    assert r.status_code == 200
    assert called == {"s": "working", "wk": "2026-W40"}
    assert isinstance(r.json()["supply"], list)


def test_recalc_returns_grids(monkeypatch):
    _stub_grids(monkeypatch)
    r = client.post("/api/recalc", json={"scenario": "working"})
    assert r.status_code == 200
    assert {"supply", "production", "summary"} <= set(r.json())
    r2 = client.get("/api/recalc")
    assert r2.status_code == 200


def test_put_parameter_updates_silver_and_recomputes(monkeypatch):
    called = {}
    monkeypatch.setattr(warehouse, "update_parameter", lambda name, value: called.update(name=name, value=value))
    _stub_grids(monkeypatch)
    r = client.put("/api/parameters", json={"name": "cap_400ml_3_sku", "value": 590000.0})
    assert r.status_code == 200
    assert called == {"name": "cap_400ml_3_sku", "value": 590000.0}


def test_put_week_updates_only_provided_fields(monkeypatch):
    captured = {}
    monkeypatch.setattr(warehouse, "update_week", lambda wk, fields: captured.update(wk=wk, fields=fields))
    _stub_grids(monkeypatch)
    r = client.put("/api/weeks", json={"week_key": "2026-W40", "maintenance_type": "Full"})
    assert r.status_code == 200
    assert captured["fields"] == {"maintenance_type": "Full"}  # is_locked/note omitted


def test_put_sku_updates_priority(monkeypatch):
    captured = {}
    monkeypatch.setattr(warehouse, "update_sku", lambda code, fields: captured.update(code=code, fields=fields))
    _stub_grids(monkeypatch)
    r = client.put("/api/skus", json={"sku_code": "60444", "priority": 2})
    assert r.status_code == 200
    assert captured["fields"] == {"priority": 2}


def test_coerce_types():
    assert warehouse._coerce("true", "BOOLEAN") is True
    assert warehouse._coerce("false", "BOOLEAN") is False
    assert warehouse._coerce("42", "INT") == 42
    assert warehouse._coerce("3.5", "DOUBLE") == 3.5
    assert warehouse._coerce(None, "DOUBLE") is None
    assert warehouse._coerce("dark_red", "STRING") == "dark_red"
