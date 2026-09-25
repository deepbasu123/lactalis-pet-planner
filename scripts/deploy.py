#!/usr/bin/env python3
"""scripts/deploy.py — one-command deploy for the medallion re-architecture.

Single entry point. Steps (each logged):
  1.  Bootstrap UC: 3 schemas (bronze/silver/gold), landing Volume, plan_overlay.
  2.  Bundle deploy: sync source + create/refresh the medallion ETL Job.
  3.  Seed: generate a synthetic workbook and upload it to the landing Volume
      (skipped when --data-file points at a real "PET Traffic Lights" workbook,
      which is uploaded instead — it flows through the identical notebooks).
  4.  Run the ETL job (notebooks: Bronze -> Silver -> Gold), poll to completion.
  5.  Build the React frontend (npm ci && npm run build).
  6.  Ensure the Genie space over Silver + Gold; write resolved app.yaml.
  7.  Create + deploy the Databricks App (thin serving layer).
  8.  Grant the app SP: USE CATALOG; USE SCHEMA+SELECT on 3 schemas + MODIFY on
      silver; READ/WRITE VOLUME; CAN_USE warehouse; CAN_MANAGE_RUN job; CAN_RUN Genie.
  9.  Health-check the app.

The rule engine is NOT here — it lives in medallion/gold_sql.py and runs in the
ETL notebooks (batch) and on the warehouse (interactive). This script only wires
infrastructure.

    .venv/bin/python -m scripts.deploy --profile DEFAULT --catalog <catalog> --warehouse-id <id>
"""
from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
import time
from pathlib import Path

from scripts.seed_and_verify import make_client
from scripts.bootstrap_uc import bootstrap, BRONZE, SILVER, GOLD
from scripts.seed_volume import upload_synth
from scripts.genie_medallion import ensure_space

PROJECT_ROOT = Path(__file__).resolve().parent.parent

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s  %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("deploy")

APP_DESCRIPTION = (
    "Lactalis PET Line weekly supply and production planner. Medallion backend "
    "(Bronze/Silver/Gold) computes every rule; the app only serves the result."
)


# ---------------------------------------------------------------------------
# app.yaml
# ---------------------------------------------------------------------------

def render_app_yaml(catalog: str, silver: str, gold: str, warehouse_id: str,
                    job_id: str, volume: str, genie_space_id: str) -> str:
    return (
        "command:\n"
        '  - "uvicorn"\n  - "backend.main:app"\n  - "--host"\n  - "0.0.0.0"\n  - "--port"\n  - "8000"\n'
        "\nenv:\n"
        f'  - name: PET_CATALOG\n    value: "{catalog}"\n'
        f'  - name: PET_SILVER_SCHEMA\n    value: "{silver}"\n'
        f'  - name: PET_GOLD_SCHEMA\n    value: "{gold}"\n'
        f'  - name: DATABRICKS_WAREHOUSE_ID\n    value: "{warehouse_id}"\n'
        f'  - name: PET_WAREHOUSE_ID\n    value: "{warehouse_id}"\n'
        f'  - name: PET_JOB_ID\n    value: "{job_id}"\n'
        f'  - name: PET_VOLUME\n    value: "{volume}"\n'
        f'  - name: PET_GENIE_SPACE_ID\n    value: "{genie_space_id}"\n'
    )


# ---------------------------------------------------------------------------
# Bundle helpers
# ---------------------------------------------------------------------------

def bundle(profile: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["databricks", "bundle", *args, "--target", "dev", "--profile", profile],
        cwd=str(PROJECT_ROOT), check=True, text=True, capture_output=True,
    )


def job_id_from_bundle(profile: str) -> str:
    """Resolve the deployed job id from `bundle summary --output json`."""
    out = subprocess.run(
        ["databricks", "bundle", "summary", "--target", "dev", "--profile", profile, "--output", "json"],
        cwd=str(PROJECT_ROOT), check=True, text=True, capture_output=True,
    ).stdout
    data = json.loads(out)
    jb = data.get("resources", {}).get("jobs", {}).get("pet_job", {})
    jid = jb.get("id")
    if not jid:
        raise RuntimeError("Could not resolve pet_job id from bundle summary")
    return str(jid)


def run_job(w, job_id: str, timeout_s: int = 1800) -> None:
    run = w.jobs.run_now(job_id=int(job_id))
    run_id = run.run_id
    log.info("  ETL job run %s started", run_id)
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        r = w.jobs.get_run(run_id=run_id)
        life = r.state.life_cycle_state.value if (r.state and r.state.life_cycle_state) else "UNKNOWN"
        if life == "TERMINATED":
            result = r.state.result_state.value if r.state.result_state else "?"
            if result == "SUCCESS":
                log.info("  ETL job run COMPLETED")
                return
            raise RuntimeError(f"ETL job run ended in result_state {result}")
        if life in ("SKIPPED", "INTERNAL_ERROR"):
            raise RuntimeError(f"ETL job run ended in life_cycle_state {life}")
        time.sleep(10)
    raise RuntimeError("ETL job run timed out")


# ---------------------------------------------------------------------------
# App deploy (CLI sync — force-includes the built frontend that .gitignore hides)
# ---------------------------------------------------------------------------

_SYNC_INCLUDES = ("frontend/dist/**",)
_SYNC_EXCLUDES = (
    "node_modules", ".venv", "__pycache__", ".git", ".claude", ".claude-flow",
    ".agents", ".swarm", ".superpowers", "docs", "notebooks", "scripts", "tests",
    "resources", "*.pyc", "*.xlsx",
)


def sync_args(ws_path: str, profile: str) -> list[str]:
    args = ["databricks", "sync", ".", ws_path]
    for pat in _SYNC_INCLUDES:
        args += ["--include", pat]
    for pat in _SYNC_EXCLUDES:
        args += ["--exclude", pat]
    return args + ["--profile", profile]


def app_get(w, app_name: str) -> dict:
    return w.api_client.do("GET", f"/api/2.0/apps/{app_name}")


def deploy_app(w, app_name: str, profile: str) -> str:
    log.info("[app] create (or verify) %s", app_name)
    subprocess.run(["databricks", "apps", "create", app_name, "--description", APP_DESCRIPTION, "--profile", profile],
                   cwd=str(PROJECT_ROOT), capture_output=True, text=True)  # idempotent; ignore "exists"
    me = w.current_user.me().user_name
    ws_sync = f"/Users/{me}/{app_name}"
    ws_src = f"/Workspace/Users/{me}/{app_name}"
    w.workspace.mkdirs(ws_sync)
    log.info("[app] sync source -> %s", ws_src)
    subprocess.run(sync_args(ws_sync, profile), cwd=str(PROJECT_ROOT), check=True)
    log.info("[app] deploy")
    subprocess.run(["databricks", "apps", "deploy", app_name, "--source-code-path", ws_src, "--profile", profile],
                   cwd=str(PROJECT_ROOT), check=True)
    try:
        return app_get(w, app_name).get("url", "")
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Grants
# ---------------------------------------------------------------------------

def resolve_sp(w, app_name: str) -> str | None:
    try:
        a = app_get(w, app_name)
    except Exception as e:
        log.warning("  cannot read app record: %s", e)
        return None
    return a.get("service_principal_client_id") or a.get("service_principal_name")


def grant_all(w, app_name: str, catalog: str, warehouse_id: str, job_id: str,
              genie_space_id: str, volume: str) -> None:
    from databricks.sdk.service.catalog import PermissionsChange, Privilege, SecurableType
    from databricks.sdk.service.iam import AccessControlRequest, PermissionLevel

    sp = resolve_sp(w, app_name)
    if not sp:
        log.warning("  app SP not resolved; grant manually.")
        return
    log.info("[grants] app SP = %s", sp)

    def _uc(stype, full, privs):
        try:
            w.grants.update(securable_type=stype, full_name=full,
                            changes=[PermissionsChange(principal=sp, add=privs)])
            log.info("  granted %s on %s", [p.value for p in privs], full)
        except Exception as e:
            log.warning("  grant on %s failed: %s", full, e)

    _uc(SecurableType.CATALOG, catalog, [Privilege.USE_CATALOG])
    for sch in (BRONZE, SILVER, GOLD):
        privs = [Privilege.USE_SCHEMA, Privilege.SELECT]
        if sch == SILVER:
            privs.append(Privilege.MODIFY)  # app writes plan_overlay / edits
        _uc(SecurableType.SCHEMA, f"{catalog}.{sch}", privs)
    # Volume: app writes uploaded workbooks
    _uc(SecurableType.VOLUME, volume, [Privilege.READ_VOLUME, Privilege.WRITE_VOLUME])

    try:
        w.permissions.update(request_object_type="sql/warehouses", request_object_id=warehouse_id,
                             access_control_list=[AccessControlRequest(service_principal_name=sp, permission_level=PermissionLevel.CAN_USE)])
        log.info("  granted CAN_USE on warehouse")
    except Exception as e:
        log.warning("  warehouse grant failed: %s", e)

    try:
        w.api_client.do("PATCH", f"/api/2.0/permissions/jobs/{job_id}",
                        body={"access_control_list": [{"service_principal_name": sp, "permission_level": "CAN_MANAGE_RUN"}]})
        log.info("  granted CAN_MANAGE_RUN on ETL job")
    except Exception as e:
        log.warning("  job grant failed: %s", e)

    try:
        w.api_client.do("PATCH", f"/api/2.0/permissions/genie/{genie_space_id}",
                        body={"access_control_list": [{"service_principal_name": sp, "permission_level": "CAN_RUN"}]})
        log.info("  granted CAN_RUN on Genie space")
    except Exception as e:
        log.warning("  genie grant failed: %s", e)


def health_check(w, app_name: str, timeout_s: int = 120) -> None:
    ok = {"RUNNING", "ACTIVE", "AVAILABLE"}
    err = {"ERROR", "FAILED", "CRASHED", "UNAVAILABLE"}
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            a = app_get(w, app_name)
            st = str((a.get("app_status") or {}).get("state", "")).upper()
            cs = str((a.get("compute_status") or {}).get("state", "")).upper()
            log.info("  app_state=%s compute=%s", st or "?", cs or "?")
            if st in ok:
                log.info("  app running: %s", a.get("url", ""))
                return
            if st in err:
                log.warning("  app in error state %s — check Apps console", st)
                return
        except Exception as e:
            log.info("  health poll: %s", e)
        time.sleep(6)
    log.warning("  health check timed out (app may still be starting)")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="Deploy the Lactalis PET medallion re-architecture.")
    ap.add_argument("--profile", required=True)
    ap.add_argument("--catalog", default="main")
    ap.add_argument("--warehouse-id", default="<warehouse_id>")
    ap.add_argument("--app-name", default="lactalis-pet-planner-v2")
    ap.add_argument("--data-file", default=None, help="real workbook to seed instead of synthetic")
    ap.add_argument("--skip-frontend-build", action="store_true")
    ap.add_argument("--skip-etl-run", action="store_true", help="reuse already-materialised gold")
    args = ap.parse_args()

    w = make_client(args.profile)
    catalog, wid = args.catalog, args.warehouse_id
    volume = f"{catalog}.{BRONZE}.landing"
    uploads = f"/Volumes/{catalog}/{BRONZE}/landing/uploads"

    log.info("=== Deploy: medallion PET planner -> %s / %s ===", args.profile, catalog)

    log.info("[1] bootstrap UC (schemas, volume, plan_overlay)")
    bootstrap(w, wid, catalog)

    log.info("[2] bundle deploy (ETL job)")
    bundle(args.profile, "deploy", "--var", f"catalog={catalog}")
    job_id = job_id_from_bundle(args.profile)
    log.info("  ETL job id = %s", job_id)

    if args.data_file:
        log.info("[3] upload real workbook %s", args.data_file)
        with open(args.data_file, "rb") as f:
            w.files.upload(f"{uploads}/{Path(args.data_file).name}", f, overwrite=True)
    else:
        log.info("[3] seed synthetic workbook -> volume")
        upload_synth(w, uploads)

    if args.skip_etl_run:
        log.info("[4] skip ETL job run")
    else:
        log.info("[4] run ETL job (Bronze->Silver->Gold)")
        run_job(w, job_id)

    if args.skip_frontend_build:
        log.info("[5] skip frontend build")
    else:
        log.info("[5] build frontend")
        subprocess.run(["npm", "ci"], cwd=str(PROJECT_ROOT / "frontend"), check=True)
        subprocess.run(["npm", "run", "build"], cwd=str(PROJECT_ROOT / "frontend"), check=True)

    log.info("[6] ensure Genie space + write app.yaml")
    genie_id = ensure_space(w, catalog, SILVER, GOLD, wid)
    (PROJECT_ROOT / "app.yaml").write_text(
        render_app_yaml(catalog, SILVER, GOLD, wid, job_id, volume, genie_id), encoding="utf-8")

    log.info("[7] deploy app")
    url = deploy_app(w, args.app_name, args.profile)

    log.info("[8] grants")
    grant_all(w, args.app_name, catalog, wid, job_id, genie_id, volume)

    log.info("[9] health check")
    health_check(w, args.app_name)

    log.info("=== Deploy complete ===  App: %s", url or "(see Apps console)")
    log.info("Genie space: %s | ETL job: %s", genie_id, job_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
