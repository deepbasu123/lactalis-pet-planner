"""
Unity Catalog data-access layer for the Lactalis PET Line Planner.

Auth strategy (dual-mode):
  - Deployed on Databricks Apps: WorkspaceClient() resolves the injected
    service-principal environment variables (DATABRICKS_HOST,
    DATABRICKS_CLIENT_ID, DATABRICKS_CLIENT_SECRET) automatically.
  - Local dev: set DATABRICKS_CONFIG_PROFILE=deep-test-1 before running the
    server; WorkspaceClient() picks up that profile from ~/.databrickscfg.

All reads and writes use the SQL Statement Execution API against
settings.warehouse_id. The PET tables are ~570 rows so inline results fit.
wait_timeout is capped at 30 s per project note; if the statement is still
running after that, we poll every 2 s until it completes.

The WorkspaceClient is constructed lazily (first call to _client()), so
``import backend.db`` never triggers a network connection.
"""

from __future__ import annotations

import time
from typing import Any

import pandas as pd

from backend.config import settings


# ── lazy auth singleton ──────────────────────────────────────────────────────

_ws_client = None  # module-level holder; assigned inside _client()


def _client():
    """Return a cached WorkspaceClient, constructing it on first call."""
    global _ws_client
    if _ws_client is None:
        from databricks.sdk import WorkspaceClient  # deferred import
        _ws_client = WorkspaceClient()
    return _ws_client


# ── helpers ──────────────────────────────────────────────────────────────────

def _fqn(name: str) -> str:
    """Return the fully-qualified table name: catalog.schema.name."""
    return f"{settings.catalog}.{settings.schema}.{name}"


def _quote_str(val: str) -> str:
    """Wrap val in single quotes, doubling any embedded single quotes."""
    return "'" + val.replace("'", "''") + "'"


def _fmt_num(val: Any) -> str:
    """
    Format a numeric value for safe SQL inclusion.

    Returns ``NULL`` for None / NaN, otherwise the bare float repr (no quotes).
    Using repr(float(...)) avoids scientific notation on typical qty values
    and never adds surrounding quotes, so the result is injection-safe for
    numeric columns.
    """
    if val is None:
        return "NULL"
    try:
        if pd.isna(val):
            return "NULL"
    except (TypeError, ValueError):
        pass
    return repr(float(val))


def _fmt_val(val: Any) -> str:
    """Generic SQL literal formatter for write_snapshot rows."""
    try:
        if pd.isna(val):
            return "NULL"
    except (TypeError, ValueError):
        pass
    if isinstance(val, bool):
        return "true" if val else "false"
    if isinstance(val, (int, float)):
        return _fmt_num(val)
    return _quote_str(str(val))


def _run_sql(sql: str):
    """
    Execute a SQL statement via the Statement Execution API.

    Blocks for up to 30 s inline (wait_timeout). If still PENDING or RUNNING
    after that, polls every 2 s until the statement reaches a terminal state.
    Raises RuntimeError on failure.
    """
    from databricks.sdk.service.sql import StatementState

    w = _client()
    resp = w.statement_execution.execute_statement(
        warehouse_id=settings.warehouse_id,
        statement=sql,
        wait_timeout="30s",
    )
    while resp.status.state in (StatementState.PENDING, StatementState.RUNNING):
        time.sleep(2)
        resp = w.statement_execution.get_statement(resp.statement_id)

    if resp.status.state != StatementState.SUCCEEDED:
        raise RuntimeError(
            f"SQL statement failed ({resp.status.state}): {resp.status.error}"
        )
    return resp


# ── public interface ─────────────────────────────────────────────────────────

def read_table(name: str) -> pd.DataFrame:
    """
    SELECT * from the fully-qualified table and return as a DataFrame.

    Column types from the manifest are used to coerce numeric columns; string
    columns are left as-is.
    """
    resp = _run_sql(f"SELECT * FROM {_fqn(name)}")
    cols = [c.name for c in resp.manifest.schema.columns]
    rows = resp.result.data_array or []
    df = pd.DataFrame(rows, columns=cols)
    for col in cols:
        df[col] = pd.to_numeric(df[col], errors="ignore")
    return df


def _merge_sql(rows: list[dict]) -> str:
    """
    Build the MERGE SQL string to upsert plan_line rows.

    This is a pure function: no network calls, fully unit-testable.

    Match key: sku_code AND week_key (formatted as single-quoted string
    literals with any embedded single quotes doubled -- safe against
    SQL injection even though production sku_code/week_key values are
    known-safe tokens).

    planned_qty and orig_qty are formatted as bare numeric literals (no
    quotes). Missing orig_qty becomes NULL; on MATCHED rows, NULL triggers
    COALESCE so the existing value is preserved.
    """
    if not rows:
        raise ValueError("rows must not be empty")

    select_clauses: list[str] = []
    for row in rows:
        sku = _quote_str(str(row["sku_code"]))
        wk = _quote_str(str(row["week_key"]))
        planned = _fmt_num(row["planned_qty"])
        orig = _fmt_num(row.get("orig_qty"))
        select_clauses.append(
            f"  SELECT {sku} AS sku_code, {wk} AS week_key,"
            f" {planned} AS planned_qty, {orig} AS orig_qty"
        )

    source = "\n  UNION ALL\n".join(select_clauses)
    table = _fqn("plan_line")

    return (
        f"MERGE INTO {table} AS t\n"
        f"USING (\n"
        f"{source}\n"
        f") AS s\n"
        f"ON t.sku_code = s.sku_code AND t.week_key = s.week_key\n"
        f"WHEN MATCHED THEN UPDATE SET\n"
        f"  t.planned_qty = s.planned_qty,\n"
        f"  t.orig_qty = COALESCE(s.orig_qty, t.orig_qty)\n"
        f"WHEN NOT MATCHED THEN INSERT (sku_code, week_key, planned_qty, orig_qty)\n"
        f"  VALUES (s.sku_code, s.week_key, s.planned_qty, s.orig_qty)"
    )


def merge_plan_lines(rows: list[dict]) -> int:
    """
    MERGE rows into plan_line on sku_code + week_key.

    Updates planned_qty unconditionally; updates orig_qty only when the
    supplied row includes it (NULL triggers COALESCE to keep the stored value).

    Returns the count of rows written (len(rows); MERGE does not return a
    per-row count from the Execution API).
    """
    sql = _merge_sql(rows)
    _run_sql(sql)
    return len(rows)


def update_parameter(name: str, value: float) -> None:
    """UPDATE parameter SET value=<value> WHERE name=<name>."""
    table = _fqn("parameter")
    safe_name = _quote_str(name)
    safe_value = _fmt_num(value)
    _run_sql(f"UPDATE {table} SET value = {safe_value} WHERE name = {safe_name}")


def update_week(week_key: str, fields: dict) -> None:
    """UPDATE week SET <fields> WHERE week_key=<week_key>.

    Only maintenance_type, is_locked, and note are writable.
    """
    if not fields:
        return
    table = _fqn("week")
    safe_key = _quote_str(week_key)
    set_parts: list[str] = []
    for k, v in fields.items():
        if k == "maintenance_type":
            set_parts.append(f"maintenance_type = {_quote_str(str(v))}")
        elif k == "is_locked":
            val = "true" if v else "false"
            set_parts.append(f"is_locked = {val}")
        elif k == "note":
            set_parts.append(f"note = {_quote_str(str(v))}")
    if set_parts:
        _run_sql(
            f"UPDATE {table} SET {', '.join(set_parts)} WHERE week_key = {safe_key}"
        )


def update_sku(sku_code: str, fields: dict) -> None:
    """UPDATE sku SET <fields> WHERE sku_code=<sku_code>.

    Only priority and status are writable.
    """
    if not fields:
        return
    table = _fqn("sku")
    safe_code = _quote_str(sku_code)
    set_parts: list[str] = []
    for k, v in fields.items():
        if k == "priority":
            set_parts.append(f"priority = {repr(int(v))}")
        elif k == "status":
            set_parts.append(f"status = {_quote_str(str(v))}")
    if set_parts:
        _run_sql(
            f"UPDATE {table} SET {', '.join(set_parts)} WHERE sku_code = {safe_code}"
        )


def write_snapshot(df: pd.DataFrame) -> None:
    """
    Overwrite projection_snapshot with the contents of df.

    Adds an updated_at timestamp column then issues TRUNCATE + INSERT so
    that Genie sees up-to-date projection data after every save. The table
    must already exist (created by the deploy script).
    """
    import datetime

    df = df.copy()
    df["updated_at"] = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

    table = _fqn("projection_snapshot")
    _run_sql(f"TRUNCATE TABLE {table}")

    col_list = ", ".join(df.columns.tolist())
    batch_size = 100
    for start in range(0, len(df), batch_size):
        batch = df.iloc[start : start + batch_size]
        row_literals: list[str] = []
        for _, row in batch.iterrows():
            vals = [_fmt_val(row[col]) for col in df.columns]
            row_literals.append(f"({', '.join(vals)})")
        values_block = ",\n  ".join(row_literals)
        _run_sql(f"INSERT INTO {table} ({col_list})\nVALUES\n  {values_block}")
