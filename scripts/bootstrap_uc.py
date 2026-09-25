"""scripts/bootstrap_uc.py

Create the medallion Unity Catalog objects that the pipeline and app depend on:
  * three schemas: <bronze>, <silver>, <gold>
  * a managed Volume <bronze>.landing  (Excel uploads land under uploads/)
  * the app-owned Silver table plan_overlay  (edits written by the app; NOT
    managed by the pipeline, so it must exist before the app runs)

Idempotent: every statement uses IF NOT EXISTS. Safe to re-run.

    .venv/bin/python -m scripts.bootstrap_uc --profile DEFAULT \
        --catalog main --warehouse-id <warehouse_id>
"""
from __future__ import annotations

import argparse
import sys

from scripts.seed_and_verify import make_client, run
from medallion.gold_sql import Silver

BRONZE = "lactalis_pet_bronze"
SILVER = "lactalis_pet_silver"
GOLD = "lactalis_pet_gold"

OVERLAY_DDL = (
    "scenario_id STRING, sku_code STRING, week_key STRING, "
    "planned_qty DOUBLE, edited_at TIMESTAMP"
)


def bootstrap(w, wid: str, catalog: str,
              bronze: str = BRONZE, silver: str = SILVER, gold: str = GOLD) -> None:
    for schema in (bronze, silver, gold):
        run(w, wid, f"CREATE SCHEMA IF NOT EXISTS `{catalog}`.`{schema}`")
        print(f"  schema ready: {catalog}.{schema}")

    run(w, wid, f"CREATE VOLUME IF NOT EXISTS `{catalog}`.`{bronze}`.`landing`")
    print(f"  volume ready: {catalog}.{bronze}.landing")

    s = Silver(catalog=catalog, schema=silver)
    run(w, wid, f"CREATE TABLE IF NOT EXISTS {s.t('plan_overlay')} ({OVERLAY_DDL})")
    print(f"  table ready: {catalog}.{silver}.plan_overlay")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", required=True)
    ap.add_argument("--catalog", required=True)
    ap.add_argument("--warehouse-id", required=True)
    ap.add_argument("--bronze-schema", default=BRONZE)
    ap.add_argument("--silver-schema", default=SILVER)
    ap.add_argument("--gold-schema", default=GOLD)
    args = ap.parse_args()

    w = make_client(args.profile)
    print("Bootstrapping medallion UC objects...")
    bootstrap(w, args.warehouse_id, args.catalog,
              args.bronze_schema, args.silver_schema, args.gold_schema)
    print("\nDONE ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())
