"""backend/warehouse.py

The backend's only connection to Databricks compute. Runs SQL on a serverless
SQL warehouse and returns typed rows. The rule engine itself lives in
medallion.gold_sql; this module just executes it and reads/writes Silver.

Dual-mode auth
--------------
* Databricks App runtime: WorkspaceClient() picks up the injected service
  principal credentials automatically.
* Local dev: set DATABRICKS_CONFIG_PROFILE. Because DEFAULT and
  fe-vm-DEFAULT share one host (the SDK's databricks-cli auth strategy
  can't disambiguate by host), we fetch the token via `databricks auth token
  --profile` and hand the SDK an explicit host+token.

Result typing
-------------
The Statement Execution API returns every value as a string. run_sql() coerces
each cell back to bool/int/float/None using the result manifest's column types,
so the API emits real JSON types (a colour is a string, a severity is an int, a
breach flag is a bool) — the frontend does no parsing or math.
"""
from __future__ import annotations

import configparser
import json
import os
import subprocess
import time
from functools import lru_cache
from typing import Any

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.sql import Disposition, Format, StatementState

from backend.config import settings
from medallion.gold_sql import Silver

_INT_TYPES = {"INT", "LONG", "SHORT", "BYTE", "INTEGER", "BIGINT", "SMALLINT", "TINYINT"}
_FLOAT_TYPES = {"FLOAT", "DOUBLE", "DECIMAL", "REAL"}


@lru_cache(maxsize=1)
def get_workspace_client() -> WorkspaceClient:
    """Return a cached WorkspaceClient (App SP creds, or a local profile)."""
    profile = os.environ.get("DATABRICKS_CONFIG_PROFILE")
    if profile:
        cfg = configparser.ConfigParser()
        cfg.read(os.path.expanduser("~/.databrickscfg"))
        host = cfg[profile]["host"]
        token = json.loads(
            subprocess.check_output(
                ["databricks", "auth", "token", "--profile", profile], text=True
            )
        )["access_token"]
        return WorkspaceClient(host=host, token=token, auth_type="pat")
    return WorkspaceClient()


def silver() -> Silver:
    return Silver(catalog=settings.catalog, schema=settings.silver_schema)


def gold() -> Silver:
    return Silver(catalog=settings.catalog, schema=settings.gold_schema)


def _coerce(value: Any, type_name: str | None):
    if value is None:
        return None
    t = (type_name or "").upper()
    if t == "BOOLEAN":
        return str(value).lower() == "true"
    if t in _INT_TYPES:
        try:
            return int(value)
        except (TypeError, ValueError):
            return int(float(value))
    if t in _FLOAT_TYPES:
        return float(value)
    return value  # STRING / DATE / TIMESTAMP stay as-is


def run_sql(sql: str) -> list[dict]:
    """Execute a statement on the configured warehouse; return typed dict rows."""
    w = get_workspace_client()
    resp = w.statement_execution.execute_statement(
        warehouse_id=settings.effective_warehouse_id,
        statement=sql,
        wait_timeout="50s",
        format=Format.JSON_ARRAY,
        disposition=Disposition.INLINE,
    )
    stmt_id = resp.statement_id
    while resp.status and resp.status.state in (StatementState.PENDING, StatementState.RUNNING):
        time.sleep(1.0)
        resp = w.statement_execution.get_statement(stmt_id)
    if not resp.status or resp.status.state != StatementState.SUCCEEDED:
        msg = resp.status.error.message if (resp.status and resp.status.error) else "unknown"
        raise RuntimeError(f"warehouse SQL failed: {msg}")

    cols = [c.name for c in (resp.manifest.schema.columns or [])]
    types = [c.type_name.value if c.type_name else None for c in (resp.manifest.schema.columns or [])]

    out: list[dict] = []
    result = resp.result
    while result is not None:
        for raw in (result.data_array or []):
            out.append({cols[i]: _coerce(raw[i], types[i]) for i in range(len(cols))})
        nxt = result.next_chunk_index
        if nxt is None:
            break
        result = w.statement_execution.get_statement_result_chunk_n(stmt_id, nxt)
    return out


def execute(sql: str) -> None:
    """Run a statement (MERGE/DELETE/UPDATE) that returns no rows we need."""
    run_sql(sql)


# ---------------------------------------------------------------------------
# Small non-rule data reads/writes (rule logic stays in medallion.gold_sql)
# ---------------------------------------------------------------------------

def _q(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def effective_plan_rows(scenario: str) -> list[dict]:
    """[{sku_code, week_key, planned_qty}] = overlay over baseline (not rule logic)."""
    s = silver()
    sql = f"""SELECT p.sku_code, p.week_key,
       COALESCE(o.planned_qty, p.planned_qty) AS planned_qty
FROM {s.t('plan_line')} p
LEFT JOIN (
  SELECT sku_code, week_key, planned_qty FROM {s.t('plan_overlay')}
  WHERE scenario_id = {_q(scenario)}
) o ON o.sku_code = p.sku_code AND o.week_key = p.week_key"""
    return run_sql(sql)


def week_is_locked(week_key: str) -> bool | None:
    s = silver()
    rows = run_sql(f"SELECT is_locked FROM {s.t('week')} WHERE week_key = {_q(week_key)}")
    if not rows:
        return None
    return bool(rows[0]["is_locked"])


def overlay_merge(scenario: str, changes: list[dict]) -> None:
    """MERGE a batch of {sku_code, week_key, planned_qty} into plan_overlay."""
    if not changes:
        return
    s = silver()
    values = ",\n".join(
        f"({_q(scenario)}, {_q(c['sku_code'])}, {_q(c['week_key'])}, "
        f"CAST({float(c['planned_qty'])} AS DOUBLE), current_timestamp())"
        for c in changes
    )
    sql = f"""MERGE INTO {s.t('plan_overlay')} t
USING (SELECT * FROM VALUES
{values}
AS v(scenario_id, sku_code, week_key, planned_qty, edited_at)) src
ON t.scenario_id = src.scenario_id AND t.sku_code = src.sku_code AND t.week_key = src.week_key
WHEN MATCHED THEN UPDATE SET t.planned_qty = src.planned_qty, t.edited_at = src.edited_at
WHEN NOT MATCHED THEN INSERT (scenario_id, sku_code, week_key, planned_qty, edited_at)
  VALUES (src.scenario_id, src.sku_code, src.week_key, src.planned_qty, src.edited_at)"""
    execute(sql)


def overlay_discard(scenario: str) -> None:
    s = silver()
    execute(f"DELETE FROM {s.t('plan_overlay')} WHERE scenario_id = {_q(scenario)}")


def overlay_discard_week(scenario: str, week_key: str) -> None:
    """Revert one week to the baseline plan by dropping its overlay rows."""
    s = silver()
    execute(
        f"DELETE FROM {s.t('plan_overlay')} "
        f"WHERE scenario_id = {_q(scenario)} AND week_key = {_q(week_key)}"
    )


def update_parameter(name: str, value: float) -> None:
    s = silver()
    execute(f"UPDATE {s.t('parameter')} SET value = CAST({float(value)} AS DOUBLE) WHERE name = {_q(name)}")


def update_week(week_key: str, fields: dict) -> None:
    """Update editable week fields. Column names are fixed (not user-supplied)."""
    if not fields:
        return
    sets = []
    for col, val in fields.items():
        if col == "is_locked":
            sets.append(f"is_locked = {'TRUE' if val else 'FALSE'}")
        elif col in ("maintenance_type", "note"):
            sets.append(f"{col} = {_q(str(val))}")
    if not sets:
        return
    s = silver()
    execute(f"UPDATE {s.t('week')} SET {', '.join(sets)} WHERE week_key = {_q(week_key)}")


def update_sku(sku_code: str, fields: dict) -> None:
    """Update editable SKU fields. Column names are fixed (not user-supplied)."""
    if not fields:
        return
    sets = []
    for col, val in fields.items():
        if col == "priority":
            sets.append(f"priority = {int(val)}")
        elif col == "status":
            sets.append(f"status = {_q(str(val))}")
    if not sets:
        return
    s = silver()
    execute(f"UPDATE {s.t('sku')} SET {', '.join(sets)} WHERE sku_code = {_q(sku_code)}")


def overlay_save(scenario: str) -> None:
    """Commit the overlay into the baseline plan_line, then clear the overlay."""
    s = silver()
    execute(f"""MERGE INTO {s.t('plan_line')} t
USING (SELECT sku_code, week_key, planned_qty FROM {s.t('plan_overlay')}
       WHERE scenario_id = {_q(scenario)}) src
ON t.sku_code = src.sku_code AND t.week_key = src.week_key
WHEN MATCHED THEN UPDATE SET t.planned_qty = src.planned_qty""")
    overlay_discard(scenario)
