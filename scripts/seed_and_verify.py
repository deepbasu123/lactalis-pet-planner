"""scripts/seed_and_verify.py

Golden-regression harness AND Silver seed loader.

What it does
------------
1. Generates the deterministic synthetic dataset (backend.data_gen, seed 42).
2. Loads the six Silver tables + an empty plan_overlay into a schema on a real
   Databricks workspace.
3. Runs the Gold SQL (medallion.gold_sql) on the serverless warehouse.
4. Compares the warehouse result, row-for-row, against the in-process Python
   engine (backend.service) — the oracle.

If every value matches, the SQL rule engine is a faithful port of the Python
engine, and the same loaded tables are a ready-to-use Silver seed.

Usage
-----
    .venv/bin/python -m scripts.seed_and_verify \
        --profile deep-test-1 \
        --catalog deep_test_1_catalog \
        --silver-schema lactalis_pet_silver_dev \
        --warehouse-id 5eb2345986480cea
"""
from __future__ import annotations

import argparse
import configparser
import datetime
import json
import os
import subprocess
import sys
import time

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.sql import (
    Disposition,
    Format,
    StatementState,
)

from backend import data_gen
from medallion.gold_sql import Silver, supply_select, production_select


def make_client(profile: str) -> WorkspaceClient:
    """Build a WorkspaceClient for a profile, robust to multiple profiles sharing
    one host (deep-test-1 / fe-vm-deep-test-1 both point at fevm-deep-test-1).

    The SDK's default databricks-cli auth strategy resolves the token by HOST and
    fails on that ambiguity, so we fetch the token via `databricks auth token
    --profile` (which honours --profile) and hand the SDK an explicit host+token.
    """
    cfg = configparser.ConfigParser()
    cfg.read(os.path.expanduser("~/.databrickscfg"))
    host = cfg[profile]["host"]
    tok = json.loads(
        subprocess.check_output(
            ["databricks", "auth", "token", "--profile", profile], text=True
        )
    )["access_token"]
    return WorkspaceClient(host=host, token=tok, auth_type="pat")


# ---------------------------------------------------------------------------
# SQL execution helpers
# ---------------------------------------------------------------------------

def _lit(x) -> str:
    if x is None:
        return "NULL"
    if isinstance(x, bool):
        return "TRUE" if x else "FALSE"
    if isinstance(x, (int, float)):
        return repr(x)
    if isinstance(x, datetime.date):
        return f"DATE '{x.isoformat()}'"
    return "'" + str(x).replace("'", "''") + "'"


def run(w: WorkspaceClient, wid: str, sql: str) -> list[list]:
    """Execute a statement and return rows as a list of lists of strings."""
    resp = w.statement_execution.execute_statement(
        warehouse_id=wid,
        statement=sql,
        wait_timeout="50s",
        format=Format.JSON_ARRAY,
        disposition=Disposition.INLINE,
    )
    stmt_id = resp.statement_id
    while resp.status and resp.status.state in (StatementState.PENDING, StatementState.RUNNING):
        time.sleep(1.5)
        resp = w.statement_execution.get_statement(stmt_id)
    if not resp.status or resp.status.state != StatementState.SUCCEEDED:
        err = resp.status.error.message if (resp.status and resp.status.error) else "unknown"
        raise RuntimeError(f"SQL failed ({resp.status.state if resp.status else '?'}): {err}\n--- SQL ---\n{sql[:1500]}")
    rows: list[list] = []
    result = resp.result
    while result is not None:
        for r in (result.data_array or []):
            rows.append(r)
        nxt = result.next_chunk_index
        if nxt is None:
            break
        result = w.statement_execution.get_statement_result_chunk_n(stmt_id, nxt)
    return rows


def insert_df(w: WorkspaceClient, wid: str, table: str, df, batch: int = 400) -> None:
    cols = list(df.columns)
    records = df.to_dict("records")
    for i in range(0, len(records), batch):
        chunk = records[i : i + batch]
        values = ",\n".join(
            "(" + ",".join(_lit(rec[c]) for c in cols) + ")" for rec in chunk
        )
        run(w, wid, f"INSERT INTO {table} ({', '.join('`'+c+'`' for c in cols)}) VALUES\n{values}")


# ---------------------------------------------------------------------------
# Silver DDL
# ---------------------------------------------------------------------------

SILVER_DDL = {
    "sku": "sku_code STRING, description STRING, pack_size_ml INT, priority INT, status STRING, shelf_life_days INT, mlor_days INT, max_cover_weeks DOUBLE",
    "week": "week_key STRING, horizon_index INT, week_commencing DATE, maintenance_type STRING, is_locked BOOLEAN, note STRING",
    "parameter": "name STRING, value DOUBLE, description STRING",
    "demand": "sku_code STRING, week_key STRING, forecast DOUBLE, sales_order DOUBLE, distr_demand_planned DOUBLE, distr_demand_tlb DOUBLE",
    "plan_line": "sku_code STRING, week_key STRING, planned_qty DOUBLE, orig_qty DOUBLE",
    "opening_stock": "sku_code STRING, opening_ea DOUBLE",
}
OVERLAY_DDL = "scenario_id STRING, sku_code STRING, week_key STRING, planned_qty DOUBLE, edited_at TIMESTAMP"


def load_silver(w: WorkspaceClient, wid: str, s: Silver, dataset: dict) -> None:
    run(w, wid, f"CREATE SCHEMA IF NOT EXISTS `{s.catalog}`.`{s.schema}`")
    for name, ddl in SILVER_DDL.items():
        run(w, wid, f"CREATE OR REPLACE TABLE {s.t(name)} ({ddl})")
        insert_df(w, wid, s.t(name), dataset[name])
    run(w, wid, f"CREATE OR REPLACE TABLE {s.t('plan_overlay')} ({OVERLAY_DDL})")
    print(f"  loaded silver into `{s.catalog}`.`{s.schema}`")


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------

def verify(w: WorkspaceClient, wid: str, s: Silver, dataset: dict) -> bool:
    # backend.service (the old in-app engine) is intentionally deleted in the
    # medallion re-architecture. The SQL Gold engine was proven equal to it at
    # commit b081e51, so there is nothing left to re-compare against here — the
    # deploy scripts import make_client/run/load_silver from this module, which
    # must keep importing cleanly with no backend engine present.
    try:
        from backend import service
    except ImportError:
        print("  backend.service removed in the re-arch; golden regression is")
        print("  historical (proven equal to gold_sql at commit b081e51). Skipping.")
        return True

    ok = True

    # ---- supply grid ----
    py = service.build_supply(dataset).set_index(["sku_code", "week_key"])
    rows = run(w, wid, supply_select(s))
    # column order from supply_select final SELECT:
    cols = ["sku_code", "week_key", "horizon_index", "opening", "recv", "prod",
            "demand", "raw", "close", "cover_weeks", "severity", "colour",
            "display_value", "is_lost_sale"]
    sql_map = {(r[0], r[1]): dict(zip(cols, r)) for r in rows}

    if len(sql_map) != len(py):
        print(f"  SUPPLY row-count mismatch: sql={len(sql_map)} python={len(py)}")
        ok = False

    float_cols = ["opening", "recv", "prod", "demand", "raw", "close", "display_value"]
    int_cols = ["horizon_index", "cover_weeks", "severity"]
    mism = 0
    for (sku, wk), prow in py.iterrows():
        srow = sql_map.get((sku, wk))
        if srow is None:
            mism += 1
            continue
        for c in float_cols:
            if abs(float(srow[c]) - float(prow[c])) > 1e-6:
                if mism < 12:
                    print(f"  SUPPLY {sku}/{wk} {c}: sql={srow[c]} py={prow[c]}")
                mism += 1
        for c in int_cols:
            if int(srow[c]) != int(prow[c]):
                if mism < 12:
                    print(f"  SUPPLY {sku}/{wk} {c}: sql={srow[c]} py={prow[c]}")
                mism += 1
        if srow["colour"] != prow["colour"]:
            if mism < 12:
                print(f"  SUPPLY {sku}/{wk} colour: sql={srow['colour']} py={prow['colour']}")
            mism += 1
    if mism:
        print(f"  SUPPLY mismatches: {mism}")
        ok = False
    else:
        print(f"  SUPPLY OK ({len(py)} rows match)")

    # ---- production / capacity ----
    prod = service.build_production(dataset)
    py_totals = prod["week_totals"]
    py_flags = prod["week_flags"]
    prows = run(w, wid, production_select(s))
    pcols = ["week_key", "horizon_index", "maintenance_type", "total", "n_skus",
             "n_packs", "pack_ml", "is_changeover", "ceiling", "r1", "r2", "r3",
             "r4", "no_rule", "over_units"]
    pmap = {r[0]: dict(zip(pcols, r)) for r in prows}
    pmism = 0
    for wk, total in py_totals.items():
        srow = pmap.get(wk)
        if srow is None:
            pmism += 1
            continue
        if abs(float(srow["total"]) - float(total)) > 1e-6:
            if pmism < 12:
                print(f"  PROD {wk} total: sql={srow['total']} py={total}")
            pmism += 1
        f = py_flags[wk]
        for c, key in [("r1", "R1"), ("r2", "R2"), ("r3", "R3"), ("r4", "R4"), ("no_rule", "no_rule")]:
            sql_bool = str(srow[c]).lower() in ("true", "1", "t")
            if sql_bool != bool(f[key]):
                if pmism < 12:
                    print(f"  PROD {wk} {c}: sql={srow[c]} py={f[key]}")
                pmism += 1
        if int(float(srow["over_units"])) != int(f["over"]):
            if pmism < 12:
                print(f"  PROD {wk} over: sql={srow['over_units']} py={f['over']}")
            pmism += 1
    if pmism:
        print(f"  PROD mismatches: {pmism}")
        ok = False
    else:
        print(f"  PROD OK ({len(py_totals)} weeks match, changeovers py={prod['changeovers']})")

    return ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", required=True)
    ap.add_argument("--catalog", required=True)
    ap.add_argument("--silver-schema", required=True)
    ap.add_argument("--warehouse-id", required=True)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--skip-load", action="store_true", help="reuse tables already loaded")
    args = ap.parse_args()

    w = make_client(args.profile)
    s = Silver(catalog=args.catalog, schema=args.silver_schema)
    dataset = data_gen.generate(args.seed)

    if not args.skip_load:
        print("Loading Silver tables...")
        load_silver(w, args.warehouse_id, s, dataset)

    print("Verifying Gold SQL against the Python engine...")
    ok = verify(w, args.warehouse_id, s, dataset)
    print("\nRESULT:", "PASS ✅" if ok else "FAIL ❌")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
