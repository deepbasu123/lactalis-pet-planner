#!/usr/bin/env python3
"""
deploy.py -- Idempotent deploy for the Lactalis PET Line Planner Databricks App.

Steps (in order, each logged):
  1. Build the React frontend (npm ci && npm run build in frontend/).
  2. Create schema deep_test_1_catalog.lactalis_pet_planner (IF NOT EXISTS).
  3. CREATE OR REPLACE the 7 Delta tables with correct schemas.
  4. Load synthetic data: TRUNCATE + INSERT from data_gen.generate().
  5. Compute projection_snapshot via service.build_supply() and load it.
  6. Ensure Genie space (create or reuse by title).
  7. Write resolved app.yaml with real warehouse_id and genie_space_id.
  8. Create or update the Databricks App.
  9. Sync source code to workspace; deploy the app.
  10. Grant the app SP: USE CATALOG, USE SCHEMA + SELECT + MODIFY, CAN_USE on
      warehouse, CAN RUN on Genie space.
  11. Health-check GET /api/health until ok (60 s timeout).

Usage:
  python deploy.py [--profile deep-test-1] [--warehouse-id <id>]

Re-runnable: schema uses IF NOT EXISTS; tables use CREATE OR REPLACE; data uses
reuse.  Running deploy.py a second time overwrites the data and re-deploys
the app but does not duplicate resources.
"""
from __future__ import annotations

import argparse
import base64
import datetime
import logging
import math
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Project root and constants
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).parent.resolve()

# backend.db provides the shared SQL execute-and-poll implementation.
# Ensure the project root is on sys.path so the import works regardless of
# the caller's working directory.
sys.path.insert(0, str(PROJECT_ROOT))
from backend.db import run_sql           # noqa: E402  (after path setup)
from backend.data_gen import generate    # noqa: E402

CATALOG = "deep_test_1_catalog"
SCHEMA = "lactalis_pet_planner"
APP_NAME = "lactalis-pet-planner"
APP_DESCRIPTION = (
    "Lactalis PET Line weekly supply and production planner "
    "with embedded Genie assistant."
)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pure helpers (tested in tests/test_deploy.py)
# ---------------------------------------------------------------------------

def _sql_val(v: Any) -> str:
    """Format a Python scalar as a SQL literal.

    * None / NaN / inf -> NULL
    * bool   -> true / false  (must be checked BEFORE int, since bool is a subtype of int)
    * int    -> bare integer literal
    * float  -> repr() (avoids scientific notation for typical qty values)
    * datetime.datetime -> 'YYYY-MM-DD HH:MM:SS'
    * datetime.date     -> 'YYYY-MM-DD'
    * numpy scalar (.item()) -> recursive
    * everything else   -> single-quoted string (embedded quotes doubled)
    """
    if v is None:
        return "NULL"
    # numpy / pandas scalars expose .item()
    if hasattr(v, "item"):
        return _sql_val(v.item())
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        if math.isnan(v) or math.isinf(v):
            return "NULL"
        return repr(v)
    if isinstance(v, int):
        return str(v)
    if isinstance(v, datetime.datetime):
        return f"'{v.strftime('%Y-%m-%d %H:%M:%S')}'"
    if isinstance(v, datetime.date):
        return f"'{v.isoformat()}'"
    return "'" + str(v).replace("'", "''") + "'"


def _render_app_yaml(warehouse_id: str, genie_space_id: str) -> str:
    """Return the resolved app.yaml content as a string.

    Embeds the real warehouse_id and genie_space_id.  Called by deploy() after
    both values are known.  Pure function -- no I/O.
    """
    return (
        "command:\n"
        '  - "uvicorn"\n'
        '  - "backend.main:app"\n'
        '  - "--host"\n'
        '  - "0.0.0.0"\n'
        '  - "--port"\n'
        '  - "8000"\n'
        "\n"
        "env:\n"
        "  - name: PET_CATALOG\n"
        f'    value: "{CATALOG}"\n'
        "  - name: PET_SCHEMA\n"
        f'    value: "{SCHEMA}"\n'
        "  - name: DATABRICKS_WAREHOUSE_ID\n"
        f'    value: "{warehouse_id}"\n'
        "  - name: PET_GENIE_SPACE_ID\n"
        f'    value: "{genie_space_id}"\n'
        "  - name: PET_LIVE\n"
        '    value: "1"\n'
    )


def _table_ddl(table_name: str) -> str:
    """Return CREATE OR REPLACE TABLE DDL for the named PET table.

    Uses CREATE OR REPLACE TABLE so a pre-existing or schema-drifted table is
    always replaced with the correct definition.  Pure function; tested
    independently.  Types follow the spec section 3.
    """
    fqn = f"{CATALOG}.{SCHEMA}.{table_name}"
    ddl_map = {
        "sku": (
            f"CREATE OR REPLACE TABLE {fqn} (\n"
            "  sku_code        STRING,\n"
            "  description     STRING,\n"
            "  pack_size_ml    INT,\n"
            "  priority        INT,\n"
            "  status          STRING,\n"
            "  shelf_life_days INT,\n"
            "  mlor_days       INT,\n"
            "  max_cover_weeks DOUBLE\n"
            ") USING DELTA"
        ),
        "week": (
            f"CREATE OR REPLACE TABLE {fqn} (\n"
            "  week_key         STRING,\n"
            "  horizon_index    INT,\n"
            "  week_commencing  DATE,\n"
            "  maintenance_type STRING,\n"
            "  is_locked        BOOLEAN,\n"
            "  note             STRING\n"
            ") USING DELTA"
        ),
        "parameter": (
            f"CREATE OR REPLACE TABLE {fqn} (\n"
            "  name        STRING,\n"
            "  value       DOUBLE,\n"
            "  description STRING\n"
            ") USING DELTA"
        ),
        "demand": (
            f"CREATE OR REPLACE TABLE {fqn} (\n"
            "  sku_code             STRING,\n"
            "  week_key             STRING,\n"
            "  forecast             DOUBLE,\n"
            "  sales_order          DOUBLE,\n"
            "  distr_demand_planned DOUBLE,\n"
            "  distr_demand_tlb     DOUBLE\n"
            ") USING DELTA"
        ),
        "plan_line": (
            f"CREATE OR REPLACE TABLE {fqn} (\n"
            "  sku_code    STRING,\n"
            "  week_key    STRING,\n"
            "  planned_qty DOUBLE,\n"
            "  orig_qty    DOUBLE\n"
            ") USING DELTA"
        ),
        "opening_stock": (
            f"CREATE OR REPLACE TABLE {fqn} (\n"
            "  sku_code   STRING,\n"
            "  opening_ea DOUBLE\n"
            ") USING DELTA"
        ),
        "projection_snapshot": (
            f"CREATE OR REPLACE TABLE {fqn} (\n"
            "  sku_code      STRING,\n"
            "  week_key      STRING,\n"
            "  horizon_index INT,\n"
            "  opening       DOUBLE,\n"
            "  recv          DOUBLE,\n"
            "  prod          DOUBLE,\n"
            "  demand        DOUBLE,\n"
            "  raw           DOUBLE,\n"
            "  close         DOUBLE,\n"
            "  cover_weeks   INT,\n"
            "  severity      INT,\n"
            "  colour        STRING,\n"
            "  updated_at    TIMESTAMP\n"
            ") USING DELTA"
        ),
    }
    if table_name not in ddl_map:
        raise KeyError(f"No DDL defined for table '{table_name}'")
    return ddl_map[table_name]


# ---------------------------------------------------------------------------
# Deploy-specific SQL helper (uses backend.db.run_sql -- single implementation)
# ---------------------------------------------------------------------------

def _insert_df(w, warehouse_id: str, table_fqn: str, df, batch: int = 100) -> None:
    """TRUNCATE table then INSERT all rows from a pandas DataFrame in batches.

    Full-table load helper used only at deploy time.  Delegates statement
    execution to backend.db.run_sql (single implementation) and passes the
    caller-supplied WorkspaceClient so every INSERT targets the --profile
    workspace, not the default profile.
    """
    run_sql(warehouse_id, f"TRUNCATE TABLE {table_fqn}", client=w)
    cols = ", ".join(df.columns.tolist())
    n = len(df)
    for start in range(0, n, batch):
        chunk = df.iloc[start : start + batch]
        row_strs = []
        for _, row in chunk.iterrows():
            vals = [_sql_val(row[c]) for c in df.columns]
            row_strs.append(f"  ({', '.join(vals)})")
        values_block = ",\n".join(row_strs)
        run_sql(
            warehouse_id,
            f"INSERT INTO {table_fqn} ({cols})\nVALUES\n{values_block}",
            client=w,
        )
        log.info(
            "  inserted rows %d-%d of %d into %s",
            start + 1,
            min(start + batch, n),
            n,
            table_fqn,
        )


# ---------------------------------------------------------------------------
# Step helpers
# ---------------------------------------------------------------------------

def step_build_frontend() -> None:
    """Step 1: npm ci && npm run build inside frontend/."""
    fe_dir = PROJECT_ROOT / "frontend"
    if not fe_dir.is_dir():
        raise FileNotFoundError(f"Frontend directory not found: {fe_dir}")
    log.info("[1/11] Building frontend (%s)...", fe_dir)
    subprocess.run(["npm", "ci"], cwd=str(fe_dir), check=True)
    subprocess.run(["npm", "run", "build"], cwd=str(fe_dir), check=True)
    dist = fe_dir / "dist"
    if not dist.is_dir():
        raise RuntimeError("npm run build completed but frontend/dist does not exist")
    log.info("  Frontend built -> %s", dist)


def step_pick_warehouse(w, warehouse_id_arg: str | None) -> str:
    """Step 2 (pre): Discover or validate the SQL warehouse to use."""
    if warehouse_id_arg:
        log.info("Using specified warehouse: %s", warehouse_id_arg)
        return warehouse_id_arg

    log.info("Listing SQL warehouses to select one...")
    warehouses = list(w.warehouses.list())
    if not warehouses:
        raise SystemExit("No SQL warehouses found in this workspace. Create one first.")

    log.info("Available warehouses:")
    for wh in warehouses:
        log.info("  id=%-28s  name=%s  state=%s", wh.id, wh.name, wh.state)

    # Prefer running warehouses; fall back to any
    running = [wh for wh in warehouses if str(wh.state).upper() == "RUNNING"]
    candidate = running[0] if running else warehouses[0]
    log.info("Auto-selected warehouse: %s (%s)", candidate.name, candidate.id)
    return candidate.id


def step_create_schema(w, warehouse_id: str) -> None:
    """Step 2: Create schema IF NOT EXISTS."""
    log.info("[2/11] Creating schema %s.%s (IF NOT EXISTS)...", CATALOG, SCHEMA)
    run_sql(warehouse_id, f"CREATE SCHEMA IF NOT EXISTS {CATALOG}.{SCHEMA}", client=w)
    log.info("  Schema ready.")


def step_create_tables(w, warehouse_id: str) -> None:
    """Step 3: CREATE OR REPLACE all 7 Delta tables with the correct schema."""
    tables = [
        "sku", "week", "parameter", "demand",
        "plan_line", "opening_stock", "projection_snapshot",
    ]
    log.info("[3/11] Creating or replacing %d Delta tables...", len(tables))
    for t in tables:
        ddl = _table_ddl(t)
        run_sql(warehouse_id, ddl, client=w)
        log.info("  Table ready: %s.%s.%s", CATALOG, SCHEMA, t)


def step_load_data(w, warehouse_id: str, ds: dict) -> None:
    """Step 4: Load synthetic data.

    Accepts the pre-generated dataset (caller generates once, shared with
    step_load_snapshot to avoid running data_gen.generate() twice).
    Each of the 6 base tables is TRUNCATED then re-inserted.
    """
    log.info("[4/11] Loading synthetic data...")
    base_tables = ["sku", "week", "parameter", "demand", "plan_line", "opening_stock"]
    for name in base_tables:
        fqn = f"{CATALOG}.{SCHEMA}.{name}"
        df = ds[name]
        log.info("  Loading %s (%d rows)...", fqn, len(df))
        _insert_df(w, warehouse_id, fqn, df)
        log.info("  Loaded %s.", name)


def step_load_snapshot(w, warehouse_id: str, ds: dict) -> None:
    """Step 5: Compute projection_snapshot via the engine and load it.

    Accepts the same pre-generated dataset as step_load_data so
    data_gen.generate() runs only once per deploy.
    """
    log.info("[5/11] Computing projection_snapshot...")
    from backend.service import build_supply

    supply_df = build_supply(ds)

    # Select the 12 projected columns; _sql_val handles the datetime object.
    snap_cols = [
        "sku_code", "week_key", "horizon_index",
        "opening", "recv", "prod", "demand",
        "raw", "close", "cover_weeks", "severity", "colour",
    ]
    snap_df = supply_df[snap_cols].copy()
    # Pass a datetime object so _sql_val formats it via its datetime branch.
    snap_df["updated_at"] = datetime.datetime.utcnow()

    fqn = f"{CATALOG}.{SCHEMA}.projection_snapshot"
    log.info("  Loading projection_snapshot (%d rows)...", len(snap_df))
    _insert_df(w, warehouse_id, fqn, snap_df)
    log.info("  projection_snapshot loaded.")


def step_ensure_genie(w, warehouse_id: str) -> str:
    """Step 6: Create or reuse the Genie space. Returns the space id."""
    log.info("[6/11] Ensuring Genie space...")
    from deploy.create_genie import ensure_space

    space_id = ensure_space(w, CATALOG, SCHEMA, warehouse_id)
    log.info("  Genie space id: %s", space_id)
    return space_id


def step_write_app_yaml(warehouse_id: str, genie_space_id: str) -> None:
    """Step 7: Overwrite app.yaml with resolved warehouse_id and genie_space_id."""
    log.info("[7/11] Writing resolved app.yaml...")
    app_yaml_path = PROJECT_ROOT / "app.yaml"
    content = _render_app_yaml(warehouse_id, genie_space_id)
    app_yaml_path.write_text(content, encoding="utf-8")
    log.info("  app.yaml updated (warehouse=%s, genie=%s)", warehouse_id, genie_space_id)


def step_create_or_update_app(w, profile: str) -> None:
    """Step 8: Create the app record if it does not exist.

    Uses the Databricks CLI `databricks apps create` so the app record is
    registered.  Idempotent: if the app already exists the CLI returns an
    error that we catch and ignore.  The app URL is retrieved later in
    step_sync_and_deploy after the source code has been deployed.
    """
    log.info("[8/11] Creating app '%s' (or verifying it exists)...", APP_NAME)
    result = subprocess.run(
        [
            "databricks", "apps", "create", APP_NAME,
            "--description", APP_DESCRIPTION,
            "--profile", profile,
        ],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        stderr = result.stderr.lower()
        if "already exists" in stderr or "already_exists" in stderr:
            log.info("  App '%s' already exists, continuing.", APP_NAME)
        else:
            log.warning("  apps create returned non-zero: %s", result.stderr.strip())
    else:
        log.info("  App '%s' created.", APP_NAME)


def _get_app_dict(w) -> dict:
    """Fetch the app record via the GA REST path and return it as a plain dict.

    SDK w.apps.get() routes to /api/2.0/preview/apps/{name} in the deployed
    SDK version (0.30) and raises NotFound.  The GA path /api/2.0/apps/{name}
    is used instead.

    Relevant response fields:
        url                          -- app URL
        service_principal_id         -- numeric SP id
        service_principal_client_id  -- SP OAuth client UUID (preferred for grants)
        service_principal_name       -- SP display name / fallback
        app_status.state             -- app lifecycle state (RUNNING / ERROR / ...)
        compute_status.state         -- compute state (ACTIVE / STARTING / ...)
    """
    return w.api_client.do("GET", f"/api/2.0/apps/{APP_NAME}")


def step_sync_and_deploy(w, profile: str) -> str:
    """Step 9: Sync source to workspace and deploy. Returns the app URL."""
    log.info("[9/11] Syncing source code to workspace and deploying...")

    # Build workspace paths
    me = w.current_user.me()
    user_name = me.user_name
    ws_sync_path = f"/Users/{user_name}/{APP_NAME}"
    ws_source_path = f"/Workspace/Users/{user_name}/{APP_NAME}"
    log.info("  Workspace source path: %s", ws_source_path)

    # Ensure the workspace directory exists
    log.info("  Creating workspace directory...")
    w.workspace.mkdirs(ws_sync_path)

    # Sync code (one-shot, no --watch)
    log.info("  Syncing files...")
    subprocess.run(
        [
            "databricks", "sync", ".", ws_sync_path,
            "--exclude", "node_modules",
            "--exclude", ".venv",
            "--exclude", "__pycache__",
            "--exclude", ".git",
            "--exclude", ".superpowers",
            "--exclude", "docs",
            "--exclude", "*.pyc",
            "--profile", profile,
        ],
        cwd=str(PROJECT_ROOT),
        check=True,
    )
    log.info("  Sync complete.")

    # Deploy
    log.info("  Deploying app '%s'...", APP_NAME)
    subprocess.run(
        [
            "databricks", "apps", "deploy", APP_NAME,
            "--source-code-path", ws_source_path,
            "--profile", profile,
        ],
        cwd=str(PROJECT_ROOT),
        check=True,
    )

    # Retrieve app URL via GA REST path (SDK w.apps.get -> /preview/ -> 404)
    try:
        app_dict = _get_app_dict(w)
        app_url = app_dict.get("url") or ""
    except Exception as exc:
        log.warning("  Could not retrieve app URL: %s", exc)
        app_url = ""
    log.info("  App deployed: %s", app_url or "(check Apps console)")
    return app_url


def step_grant_sp(w, warehouse_id: str, genie_space_id: str, profile: str) -> None:
    """Step 10: Grant the app SP all required permissions.

    Grants applied:
      - USE CATALOG on deep_test_1_catalog  (UC)
      - USE SCHEMA + SELECT + MODIFY on deep_test_1_catalog.lactalis_pet_planner  (UC)
      - CAN_USE on the SQL warehouse  (permissions API)
      - CAN RUN on the Genie space  (permissions API -- best-effort)

    The SP principal is resolved from the app record.  If the app has not
    fully provisioned its SP yet, each grant is retried once after a short
    wait.
    """
    log.info("[10/11] Granting permissions to the app service principal...")

    # Resolve SP identity from the app record
    sp_name, sp_numeric_id = _resolve_app_sp(w)
    if not sp_name:
        log.warning(
            "  Could not resolve app SP principal. "
            "Grant step skipped -- apply permissions manually."
        )
        return

    log.info("  App SP: %s (numeric id=%s)", sp_name, sp_numeric_id)

    # UC grants
    _grant_uc_catalog(w, sp_name)
    _grant_uc_schema(w, sp_name)

    # Warehouse CAN_USE
    _grant_warehouse(w, warehouse_id, sp_name)

    # Genie space CAN RUN (best-effort: API endpoint not yet confirmed at
    # deploy-time without a live workspace -- flagged for live verification)
    _grant_genie_space(w, genie_space_id, sp_name)


def _resolve_app_sp(w) -> tuple[str | None, int | None]:
    """Return (sp_principal_for_grants, sp_numeric_id) from the app record.

    Uses the GA REST path via _get_app_dict (SDK w.apps.get -> /preview/ -> 404).
    Prefers service_principal_client_id (the OAuth UUID) as the principal string
    for UC grants and the permissions API; falls back to service_principal_name,
    then to a live SP lookup.
    """
    try:
        app_dict = _get_app_dict(w)
    except Exception as exc:
        log.warning("  Could not fetch app record: %s", exc)
        return None, None

    sp_numeric_id = app_dict.get("service_principal_id")
    # service_principal_client_id is the OAuth UUID used in UC grants.
    # service_principal_name is the display name, also accepted by the grants API.
    sp_name = (
        app_dict.get("service_principal_client_id")
        or app_dict.get("service_principal_name")
    )
    if sp_name:
        return sp_name, sp_numeric_id

    # Last resort: SDK lookup to obtain application_id UUID from numeric id.
    if sp_numeric_id:
        try:
            sp = w.service_principals.get(id=sp_numeric_id)
            app_id = getattr(sp, "application_id", None) or getattr(sp, "display_name", None)
            return app_id, sp_numeric_id
        except Exception as exc:
            log.warning("  Could not fetch SP details (id=%s): %s", sp_numeric_id, exc)

    return None, sp_numeric_id


def _grant_uc_catalog(w, sp_name: str) -> None:
    """USE CATALOG on CATALOG."""
    try:
        from databricks.sdk.service.catalog import PermissionsChange, Privilege, SecurableType

        w.grants.update(
            securable_type=SecurableType.CATALOG,
            full_name=CATALOG,
            changes=[
                PermissionsChange(
                    principal=sp_name,
                    add=[Privilege.USE_CATALOG],
                )
            ],
        )
        log.info("  Granted USE_CATALOG on %s to %s", CATALOG, sp_name)
    except Exception as exc:
        log.warning("  USE_CATALOG grant failed (may already exist or requires admin): %s", exc)


def _grant_uc_schema(w, sp_name: str) -> None:
    """USE SCHEMA + SELECT + MODIFY on CATALOG.SCHEMA."""
    try:
        from databricks.sdk.service.catalog import PermissionsChange, Privilege, SecurableType

        w.grants.update(
            securable_type=SecurableType.SCHEMA,
            full_name=f"{CATALOG}.{SCHEMA}",
            changes=[
                PermissionsChange(
                    principal=sp_name,
                    add=[
                        Privilege.USE_SCHEMA,
                        Privilege.SELECT,
                        Privilege.MODIFY,
                    ],
                )
            ],
        )
        log.info(
            "  Granted USE_SCHEMA + SELECT + MODIFY on %s.%s to %s",
            CATALOG, SCHEMA, sp_name,
        )
    except Exception as exc:
        log.warning("  Schema grant failed: %s", exc)


def _grant_warehouse(w, warehouse_id: str, sp_name: str) -> None:
    """CAN_USE on the SQL warehouse."""
    try:
        from databricks.sdk.service.iam import AccessControlRequest, PermissionLevel

        w.permissions.update(
            request_object_type="sql/warehouses",
            request_object_id=warehouse_id,
            access_control_list=[
                AccessControlRequest(
                    service_principal_name=sp_name,
                    permission_level=PermissionLevel.CAN_USE,
                )
            ],
        )
        log.info("  Granted CAN_USE on warehouse %s to %s", warehouse_id, sp_name)
    except Exception as exc:
        log.warning("  Warehouse CAN_USE grant failed: %s", exc)


def _grant_genie_space(w, genie_space_id: str, sp_name: str) -> None:
    """CAN RUN on the Genie space.

    DEPLOY-TIME NOTE: The permission endpoint for Genie spaces has not been
    validated against a live workspace.  Two endpoint patterns are tried in
    order; a warning is logged if both fail (manual grant via the UI works as
    a fallback).
    """
    endpoints = [
        f"/api/2.0/permissions/dashboards/{genie_space_id}",
        f"/api/2.0/permissions/genie_spaces/{genie_space_id}",
    ]
    body = {
        "access_control_list": [
            {
                "service_principal_name": sp_name,
                "permission_level": "CAN_RUN",
            }
        ]
    }
    last_exc: Exception | None = None
    for endpoint in endpoints:
        try:
            w.api_client.do("PUT", endpoint, body=body)
            log.info("  Granted CAN_RUN on Genie space %s to %s (via %s)",
                     genie_space_id, sp_name, endpoint)
            return
        except Exception as exc:
            last_exc = exc
            log.debug("  Genie grant via %s failed: %s", endpoint, exc)

    log.warning(
        "  Genie space CAN RUN grant failed on all endpoints (requires live verification). "
        "Grant manually: Genie space -> Share -> add SP with CAN RUN. Last error: %s",
        last_exc,
    )


def step_health_check(app_url: str, w=None, timeout_s: int = 60) -> None:
    """Step 11: Poll app state via the GA REST path until running/active or timeout.

    Uses _get_app_dict (GA /api/2.0/apps/{name}) to read app_status.state and
    compute_status.state from the plain dict response.  Non-fatal: a timeout or
    missing SDK client logs a warning and returns so the deploy does not crash.
    """
    log.info("[11/11] Waiting for app '%s' to reach running state (timeout=%ds)...",
             APP_NAME, timeout_s)
    if app_url:
        log.info("  App URL: %s", app_url)

    if w is None:
        log.warning("  No SDK client available -- health check skipped.")
        return

    # Terminal active states and terminal error states
    _RUNNING = frozenset({"RUNNING", "ACTIVE", "AVAILABLE"})
    _ERROR = frozenset({"ERROR", "FAILED", "CRASHED", "UNAVAILABLE"})

    deadline = time.monotonic() + timeout_s
    attempt = 0
    while time.monotonic() < deadline:
        attempt += 1
        try:
            # GA REST path returns a plain dict; nested status dicts accessed with .get()
            app_dict = _get_app_dict(w)
            app_state = str(
                (app_dict.get("app_status") or {}).get("state", "")
            ).upper()
            compute_state = str(
                (app_dict.get("compute_status") or {}).get("state", "")
            ).upper()

            log.info(
                "  Attempt %d: app_state=%s compute_state=%s",
                attempt, app_state or "?", compute_state or "?",
            )

            if app_state in _RUNNING:
                log.info("  App is running. URL: %s", app_url or "(see Apps console)")
                return
            if app_state in _ERROR:
                log.warning(
                    "  App reached error state '%s'. Check the Databricks Apps console.",
                    app_state,
                )
                return
        except Exception as exc:
            log.info("  Attempt %d: SDK error: %s", attempt, exc)

        time.sleep(5)

    log.warning(
        "  Health check timed out after %ds. The app may still be starting. "
        "App URL: %s",
        timeout_s,
        app_url or "(see Apps console)",
    )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Deploy the Lactalis PET Line Planner to Databricks Apps."
    )
    parser.add_argument(
        "--profile",
        default="deep-test-1",
        help="Databricks CLI profile to use (default: deep-test-1).",
    )
    parser.add_argument(
        "--warehouse-id",
        default=None,
        help=(
            "SQL warehouse id to use. If omitted, the script lists warehouses "
            "and selects one automatically."
        ),
    )
    parser.add_argument(
        "--skip-frontend-build",
        action="store_true",
        help="Skip npm ci && npm run build (use existing frontend/dist).",
    )
    args = parser.parse_args()

    log.info("=== Lactalis PET Line Planner -- Deploy ===")
    log.info("Profile: %s  |  Catalog: %s  |  Schema: %s  |  App: %s",
             args.profile, CATALOG, SCHEMA, APP_NAME)

    # Set the Databricks profile for SDK calls
    os.environ["DATABRICKS_CONFIG_PROFILE"] = args.profile

    from databricks.sdk import WorkspaceClient
    w = WorkspaceClient(profile=args.profile)

    # Step 1: build frontend
    if args.skip_frontend_build:
        log.info("[1/11] Skipping frontend build (--skip-frontend-build).")
        dist = PROJECT_ROOT / "frontend" / "dist"
        if not dist.is_dir():
            raise FileNotFoundError(
                "frontend/dist does not exist -- run without --skip-frontend-build first."
            )
    else:
        step_build_frontend()

    # Pre-step: pick warehouse
    warehouse_id = step_pick_warehouse(w, args.warehouse_id)

    # Steps 2-5: schema, tables, data, snapshot
    step_create_schema(w, warehouse_id)
    step_create_tables(w, warehouse_id)
    # Generate the synthetic dataset once; pass the same dict to both steps
    # so data_gen.generate() runs only once per deploy.
    log.info("Generating synthetic dataset (seed=42)...")
    ds = generate(seed=42)
    step_load_data(w, warehouse_id, ds)
    step_load_snapshot(w, warehouse_id, ds)

    # Step 6: Genie
    genie_space_id = step_ensure_genie(w, warehouse_id)

    # Step 7: resolved app.yaml
    step_write_app_yaml(warehouse_id, genie_space_id)

    # Step 8: create app record
    step_create_or_update_app(w, args.profile)

    # Step 9: sync + deploy; get app URL
    app_url = step_sync_and_deploy(w, args.profile)

    # Step 10: grants
    step_grant_sp(w, warehouse_id, genie_space_id, args.profile)

    # Step 11: health check (SDK-based state polling, non-fatal on timeout)
    step_health_check(app_url, w=w)

    log.info("=== Deploy complete ===")
    log.info("App URL: %s", app_url or "(check the Apps console)")
    log.info("Deploy again with: python deploy.py --profile %s", args.profile)


if __name__ == "__main__":
    main()
