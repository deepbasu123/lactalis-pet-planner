# Unit tests for db.py pure functions.
# read_table and write_snapshot are exercised here via mocks that simulate the
# all-string result format returned by the SQL Statement Execution API in live
# mode.  _merge_sql is a pure function with no mock needed.

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from backend.db import _merge_sql


# ── table target ────────────────────────────────────────────────────────────

def test_merge_sql_targets_plan_line():
    sql = _merge_sql([{"sku_code": "61108", "week_key": "2026-W35", "planned_qty": 1000.0}])
    assert "deep_test_1_catalog.lactalis_pet_planner.plan_line" in sql


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


# ── read_table: manifest-type coercion (C1 regression guard) ────────────────
#
# These tests simulate the SQL Statement Execution API response where every
# value is a string.  They verify that read_table coerces columns to their
# declared manifest type rather than leaving them as raw strings.

def _make_mock_response(col_specs: list[tuple[str, str]], values: list[str]):
    """Build a mock statement-exec response with all-string data_array."""
    mock_columns = []
    for name, type_name in col_specs:
        col = MagicMock()
        col.name = name
        # type_name is a plain string here, matching how _col_type_str handles
        # both the SDK enum (.value) and raw strings (test / fallback path).
        col.type_name = type_name
        mock_columns.append(col)

    resp = MagicMock()
    resp.manifest.schema.columns = mock_columns
    resp.result.data_array = [values]
    return resp


class TestReadTableTypeCoercion:
    """C1: read_table must coerce all-string statement-exec results by manifest type."""

    def _load(self, col_specs, values):
        """Patch _run_sql and call read_table("week")."""
        from backend.db import read_table
        mock_resp = _make_mock_response(col_specs, values)
        with patch("backend.db._run_sql", return_value=mock_resp):
            return read_table("week")

    def test_boolean_false_string_coerced_to_bool(self):
        df = self._load(
            [("is_locked", "BOOLEAN"), ("week_key", "STRING")],
            ["false", "2026-W35"],
        )
        val = df["is_locked"].iloc[0]
        # Must be actual bool False, not the string "false" (bool("false") is True)
        assert val is False or val == False
        assert not isinstance(val, str), (
            "is_locked must not be a string -- bool('false') is True and breaks lock check"
        )

    def test_boolean_true_string_coerced_to_bool(self):
        df = self._load([("is_locked", "BOOLEAN")], ["true"])
        val = df["is_locked"].iloc[0]
        assert val is True or val == True
        assert not isinstance(val, str)

    def test_string_sku_code_stays_str(self):
        df = self._load(
            [("sku_code", "STRING"), ("is_locked", "BOOLEAN")],
            ["60444", "false"],
        )
        val = df["sku_code"].iloc[0]
        assert val == "60444"
        assert isinstance(val, str), (
            "sku_code must stay a string so overlay keys match"
        )

    def test_int_column_coerced_to_integer_dtype(self):
        df = self._load([("horizon_index", "INT")], ["5"])
        assert pd.api.types.is_integer_dtype(df["horizon_index"]), (
            "INT column should have integer dtype"
        )
        assert df["horizon_index"].iloc[0] == 5

    def test_long_column_coerced_to_integer_dtype(self):
        df = self._load([("big_col", "LONG")], ["1000000"])
        assert pd.api.types.is_integer_dtype(df["big_col"])

    def test_double_column_coerced_to_float_dtype(self):
        df = self._load([("planned_qty", "DOUBLE")], ["1234.5"])
        assert pd.api.types.is_float_dtype(df["planned_qty"]), (
            "DOUBLE column should have float dtype"
        )
        assert df["planned_qty"].iloc[0] == pytest.approx(1234.5)

    def test_all_four_types_together(self):
        """BOOLEAN + STRING + INT + DOUBLE all coerced correctly in one call."""
        col_specs = [
            ("is_locked", "BOOLEAN"),
            ("sku_code", "STRING"),
            ("horizon_index", "INT"),
            ("planned_qty", "DOUBLE"),
        ]
        values = ["false", "60444", "3", "999.0"]
        df = self._load(col_specs, values)

        # BOOLEAN -> real bool False (not the string "false")
        assert df["is_locked"].iloc[0] == False  # noqa: E712
        assert not isinstance(df["is_locked"].iloc[0], str)

        # STRING -> stays "60444" (not int 60444)
        assert df["sku_code"].iloc[0] == "60444"
        assert isinstance(df["sku_code"].iloc[0], str)

        # INT -> integer dtype
        assert pd.api.types.is_integer_dtype(df["horizon_index"])
        assert df["horizon_index"].iloc[0] == 3

        # DOUBLE -> float dtype
        assert pd.api.types.is_float_dtype(df["planned_qty"])
        assert df["planned_qty"].iloc[0] == pytest.approx(999.0)


# ── write_snapshot: only table columns inserted (C3 regression guard) ────────

class TestWriteSnapshotColumns:
    """C3: write_snapshot must not INSERT display_value or other non-DDL columns."""

    def test_display_value_excluded_from_insert(self):
        """build_supply's display_value column must be dropped before INSERT."""
        df = pd.DataFrame({
            "sku_code": ["61108"],
            "week_key": ["2026-W35"],
            "horizon_index": [1],
            "opening": [1000.0],
            "recv": [500.0],
            "prod": [800.0],
            "demand": [700.0],
            "raw": [600.0],
            "close": [600.0],
            "cover_weeks": [3],
            "severity": [1],
            "colour": ["green"],
            "display_value": ["1,000"],  # Extra col NOT in projection_snapshot DDL
        })

        sql_calls: list[str] = []

        def capture(sql: str):
            sql_calls.append(sql)

        from backend.db import write_snapshot
        with patch("backend.db._run_sql", side_effect=capture):
            write_snapshot(df)

        insert_sqls = [s for s in sql_calls if s.startswith("INSERT")]
        assert insert_sqls, "Expected at least one INSERT statement"

        for sql in insert_sqls:
            assert "display_value" not in sql, (
                f"display_value must not appear in INSERT; got:\n{sql}"
            )

    def test_all_snapshot_cols_present_in_insert(self):
        """Every projection_snapshot DDL column (including updated_at) must be inserted."""
        df = pd.DataFrame({
            "sku_code": ["61108"],
            "week_key": ["2026-W35"],
            "horizon_index": [1],
            "opening": [1000.0],
            "recv": [500.0],
            "prod": [800.0],
            "demand": [700.0],
            "raw": [600.0],
            "close": [600.0],
            "cover_weeks": [3],
            "severity": [1],
            "colour": ["green"],
            "display_value": ["1,000"],
        })

        sql_calls: list[str] = []

        from backend.db import write_snapshot
        with patch("backend.db._run_sql", side_effect=lambda s: sql_calls.append(s)):
            write_snapshot(df)

        insert_sql = next(s for s in sql_calls if s.startswith("INSERT"))
        for required_col in [
            "sku_code", "week_key", "horizon_index", "opening", "recv",
            "prod", "demand", "raw", "close", "cover_weeks",
            "severity", "colour", "updated_at",
        ]:
            assert required_col in insert_sql, (
                f"Expected column '{required_col}' in INSERT but not found"
            )
