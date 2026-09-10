"""tests/test_deploy.py

Unit tests for the pure helper functions in deploy.py.

No Databricks workspace, no live network calls.  All functions under test are
pure Python (no SDK imports at call time).

Covers:
  - _sql_val: Python scalar -> SQL literal
  - _render_app_yaml: resolved YAML contains expected keys and values
  - _table_ddl: DDL strings contain the right table name and column definitions

Note: Python resolves ``deploy`` as the ``deploy/`` package (create_genie).
We load deploy.py directly via importlib to avoid the name collision.
"""
from __future__ import annotations

import datetime
import importlib.util
import math
from pathlib import Path

import pytest

# Load deploy.py by its file path to avoid the deploy/ package collision.
_DEPLOY_PY = Path(__file__).parent.parent / "deploy.py"
_spec = importlib.util.spec_from_file_location("deploy_script", str(_DEPLOY_PY))
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

APP_NAME = _mod.APP_NAME
CATALOG = _mod.CATALOG
SCHEMA = _mod.SCHEMA
_sql_val = _mod._sql_val
_render_app_yaml = _mod._render_app_yaml
_table_ddl = _mod._table_ddl


# ---------------------------------------------------------------------------
# _sql_val
# ---------------------------------------------------------------------------

class TestSqlVal:
    def test_none_is_null(self):
        assert _sql_val(None) == "NULL"

    def test_nan_is_null(self):
        assert _sql_val(float("nan")) == "NULL"

    def test_inf_is_null(self):
        assert _sql_val(math.inf) == "NULL"
        assert _sql_val(-math.inf) == "NULL"

    def test_true_is_true(self):
        assert _sql_val(True) == "true"

    def test_false_is_false(self):
        assert _sql_val(False) == "false"

    def test_int(self):
        assert _sql_val(42) == "42"
        assert _sql_val(0) == "0"

    def test_float(self):
        result = _sql_val(3.14)
        # repr(3.14) produces a safe literal; must not be quoted
        assert "3.14" in result
        assert result[0] != "'"  # not a string literal

    def test_float_zero(self):
        assert _sql_val(0.0) == repr(0.0)

    def test_string_simple(self):
        assert _sql_val("hello") == "'hello'"

    def test_string_with_single_quote(self):
        # SQL injection safety: embedded single quotes must be doubled
        assert _sql_val("it's") == "'it''s'"

    def test_date(self):
        d = datetime.date(2026, 8, 24)
        assert _sql_val(d) == "'2026-08-24'"

    def test_datetime(self):
        dt = datetime.datetime(2026, 9, 10, 12, 30, 0)
        assert _sql_val(dt) == "'2026-09-10 12:30:00'"

    def test_bool_takes_precedence_over_int(self):
        # True == 1 in Python; must produce 'true', not '1'
        assert _sql_val(True) == "true"
        assert _sql_val(False) == "false"

    def test_large_int(self):
        # Typical qty values (up to a few million) must not become floats
        assert _sql_val(650_000) == "650000"

    def test_numpy_int_via_item(self):
        """numpy integers expose .item(); _sql_val must unwrap them."""
        try:
            import numpy as np
            v = np.int64(99)
            result = _sql_val(v)
            assert result == "99"
        except ImportError:
            pytest.skip("numpy not installed")

    def test_numpy_float_via_item(self):
        try:
            import numpy as np
            v = np.float64(1.5)
            result = _sql_val(v)
            assert "1.5" in result
        except ImportError:
            pytest.skip("numpy not installed")

    def test_numpy_bool_via_item(self):
        try:
            import numpy as np
            assert _sql_val(np.bool_(True)) == "true"
            assert _sql_val(np.bool_(False)) == "false"
        except ImportError:
            pytest.skip("numpy not installed")


# ---------------------------------------------------------------------------
# _render_app_yaml
# ---------------------------------------------------------------------------

class TestRenderAppYaml:
    @pytest.fixture
    def rendered(self) -> str:
        return _render_app_yaml(
            warehouse_id="abc123",
            genie_space_id="genie456",
        )

    def test_is_string(self, rendered):
        assert isinstance(rendered, str)

    def test_contains_uvicorn_command(self, rendered):
        assert "uvicorn" in rendered
        assert "backend.main:app" in rendered

    def test_contains_host_and_port(self, rendered):
        assert "--host" in rendered
        assert "0.0.0.0" in rendered
        assert "--port" in rendered
        assert "8000" in rendered

    def test_warehouse_id_present(self, rendered):
        assert "abc123" in rendered

    def test_genie_space_id_present(self, rendered):
        assert "genie456" in rendered

    def test_catalog_present(self, rendered):
        assert CATALOG in rendered

    def test_schema_present(self, rendered):
        assert SCHEMA in rendered

    def test_pet_live_is_1(self, rendered):
        assert 'PET_LIVE' in rendered
        assert '"1"' in rendered

    def test_env_vars_block_present(self, rendered):
        for name in (
            "PET_CATALOG",
            "PET_SCHEMA",
            "DATABRICKS_WAREHOUSE_ID",
            "PET_GENIE_SPACE_ID",
            "PET_LIVE",
        ):
            assert name in rendered, f"env var {name} missing from rendered app.yaml"

    def test_no_em_dash(self, rendered):
        assert "—" not in rendered

    def test_parseable_yaml(self, rendered):
        """The rendered string should be valid YAML."""
        try:
            import yaml
            parsed = yaml.safe_load(rendered)
            assert isinstance(parsed, dict)
            assert "command" in parsed
            assert "env" in parsed
        except ImportError:
            # PyYAML not installed; do a minimal structural check instead
            assert "command:" in rendered
            assert "env:" in rendered

    def test_different_ids_produce_different_content(self):
        a = _render_app_yaml("wh1", "g1")
        b = _render_app_yaml("wh2", "g2")
        assert a != b
        assert "wh1" in a and "wh1" not in b
        assert "g1" in a and "g1" not in b


# ---------------------------------------------------------------------------
# _table_ddl
# ---------------------------------------------------------------------------

class TestTableDdl:
    ALL_TABLES = [
        "sku", "week", "parameter", "demand",
        "plan_line", "opening_stock", "projection_snapshot",
    ]

    def test_returns_string_for_all_tables(self):
        for name in self.ALL_TABLES:
            ddl = _table_ddl(name)
            assert isinstance(ddl, str), f"_table_ddl('{name}') did not return a string"

    def test_unknown_table_raises_key_error(self):
        with pytest.raises(KeyError):
            _table_ddl("nonexistent_table")

    def test_ddl_contains_create_table_if_not_exists(self):
        for name in self.ALL_TABLES:
            ddl = _table_ddl(name).upper()
            assert "CREATE TABLE IF NOT EXISTS" in ddl, (
                f"DDL for '{name}' missing CREATE TABLE IF NOT EXISTS"
            )

    def test_ddl_contains_fully_qualified_name(self):
        for name in self.ALL_TABLES:
            ddl = _table_ddl(name)
            expected_fqn = f"{CATALOG}.{SCHEMA}.{name}"
            assert expected_fqn in ddl, (
                f"DDL for '{name}' missing FQN '{expected_fqn}'"
            )

    def test_ddl_uses_delta(self):
        for name in self.ALL_TABLES:
            ddl = _table_ddl(name).upper()
            assert "USING DELTA" in ddl, f"DDL for '{name}' missing USING DELTA"

    def test_sku_has_required_columns(self):
        ddl = _table_ddl("sku")
        for col in ("sku_code", "description", "pack_size_ml", "priority",
                    "status", "shelf_life_days", "mlor_days", "max_cover_weeks"):
            assert col in ddl, f"sku DDL missing column '{col}'"

    def test_week_has_required_columns(self):
        ddl = _table_ddl("week")
        for col in ("week_key", "horizon_index", "week_commencing",
                    "maintenance_type", "is_locked", "note"):
            assert col in ddl, f"week DDL missing column '{col}'"

    def test_week_commencing_is_date(self):
        ddl = _table_ddl("week").upper()
        assert "WEEK_COMMENCING" in ddl
        # DATE type must appear after the column name
        idx = ddl.index("WEEK_COMMENCING")
        fragment = ddl[idx:idx + 40]
        assert "DATE" in fragment, "week_commencing must be typed DATE"

    def test_plan_line_has_planned_qty_and_orig_qty(self):
        ddl = _table_ddl("plan_line")
        assert "planned_qty" in ddl
        assert "orig_qty" in ddl

    def test_projection_snapshot_has_colour_and_severity(self):
        ddl = _table_ddl("projection_snapshot")
        for col in ("colour", "severity", "cover_weeks", "updated_at"):
            assert col in ddl, f"projection_snapshot DDL missing column '{col}'"

    def test_projection_snapshot_updated_at_is_timestamp(self):
        ddl = _table_ddl("projection_snapshot").upper()
        idx = ddl.index("UPDATED_AT")
        fragment = ddl[idx:idx + 30]
        assert "TIMESTAMP" in fragment, "updated_at must be typed TIMESTAMP"

    def test_opening_stock_columns(self):
        ddl = _table_ddl("opening_stock")
        assert "sku_code" in ddl
        assert "opening_ea" in ddl

    def test_no_em_dash_in_any_ddl(self):
        for name in self.ALL_TABLES:
            assert "—" not in _table_ddl(name), (
                f"em dash found in DDL for '{name}'"
            )
