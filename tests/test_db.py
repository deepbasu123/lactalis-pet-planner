# Unit tests for _merge_sql — the only pure, network-free function in db.py.
# All other functions (read_table, merge_plan_lines, write_snapshot) require a
# live SQL warehouse and are exercised at deploy time, not here.

from backend.db import _merge_sql


# ── table target ────────────────────────────────────────────────────────────

def test_merge_sql_targets_plan_line():
    sql = _merge_sql([{"sku_code": "61108", "week_key": "2026-W35", "planned_qty": 1000.0}])
    assert "deep_test_1_catalog.lactalis_pet.plan_line" in sql


# ── required MERGE keywords ──────────────────────────────────────────────────

def test_merge_sql_contains_merge():
    sql = _merge_sql([{"sku_code": "61108", "week_key": "2026-W35", "planned_qty": 1000.0}])
    assert "MERGE" in sql


def test_merge_sql_when_matched_update():
    sql = _merge_sql([{"sku_code": "61108", "week_key": "2026-W35", "planned_qty": 1000.0}])
    assert "WHEN MATCHED THEN UPDATE" in sql


def test_merge_sql_when_not_matched_insert():
    sql = _merge_sql([{"sku_code": "61108", "week_key": "2026-W35", "planned_qty": 1000.0}])
    assert "WHEN NOT MATCHED THEN INSERT" in sql


# ── numeric formatting (no surrounding quotes) ───────────────────────────────

def test_merge_sql_numeric_planned_qty_not_quoted():
    sql = _merge_sql([{"sku_code": "61108", "week_key": "2026-W35", "planned_qty": 1234.5}])
    assert "1234.5" in sql
    assert "'1234.5'" not in sql


def test_merge_sql_numeric_orig_qty_not_quoted():
    sql = _merge_sql([{"sku_code": "61108", "week_key": "2026-W35",
                       "planned_qty": 500.0, "orig_qty": 480.0}])
    assert "480.0" in sql
    assert "'480.0'" not in sql


def test_merge_sql_missing_orig_qty_becomes_null():
    sql = _merge_sql([{"sku_code": "61108", "week_key": "2026-W35", "planned_qty": 100.0}])
    assert "NULL" in sql


# ── string key quoting ───────────────────────────────────────────────────────

def test_merge_sql_sku_code_is_quoted():
    sql = _merge_sql([{"sku_code": "61108", "week_key": "2026-W35", "planned_qty": 1000.0}])
    assert "'61108'" in sql


def test_merge_sql_week_key_is_quoted():
    sql = _merge_sql([{"sku_code": "61108", "week_key": "2026-W35", "planned_qty": 1000.0}])
    assert "'2026-W35'" in sql


# ── SQL-injection safety: single quotes doubled ───────────────────────────────

def test_merge_sql_escapes_single_quote_in_sku():
    sql = _merge_sql([{"sku_code": "O'Brien", "week_key": "2026-W35", "planned_qty": 0.0}])
    assert "O''Brien" in sql


def test_merge_sql_escapes_single_quote_in_week_key():
    sql = _merge_sql([{"sku_code": "61108", "week_key": "bad'key", "planned_qty": 0.0}])
    assert "bad''key" in sql


# ── multi-row: all rows land in the SQL ─────────────────────────────────────

def test_merge_sql_multi_row():
    rows = [
        {"sku_code": "61108", "week_key": "2026-W35", "planned_qty": 100.0},
        {"sku_code": "61747", "week_key": "2026-W36", "planned_qty": 200.0},
    ]
    sql = _merge_sql(rows)
    assert "'61108'" in sql
    assert "'61747'" in sql
    assert "'2026-W35'" in sql
    assert "'2026-W36'" in sql
