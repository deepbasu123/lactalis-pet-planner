"""pipeline/pet_pipeline.py

Lakeflow Spark Declarative Pipeline for the Lactalis PET Line Planner medallion.

    UC Volume (.xlsx)  ->  BRONZE (raw parsed)  ->  SILVER (conformed + DQ)  ->  GOLD (rule engine)

The whole business-rule engine is imported from medallion.gold_sql (the single
source of truth also used by the app's interactive recompute). The pipeline runs
it on its own serverless compute for the batch/upload path; the app re-runs the
identical SQL on a serverless SQL warehouse for interactive edits.

Configuration (set as pipeline `configuration` keys):
    pet.catalog        default main
    pet.bronze_schema  default lactalis_pet_bronze
    pet.silver_schema  default lactalis_pet_silver
    pet.gold_schema    default lactalis_pet_gold
    pet.volume_path    default /Volumes/main/lactalis_pet_bronze/landing/uploads

Pipeline compute needs the `medallion` package importable and the `openpyxl` +
`pandas` libraries installed.
"""
from __future__ import annotations

import os
import sys
from functools import lru_cache

# The pipeline source executes as a notebook cell, so `__file__` is undefined.
# The DAB passes the synced bundle files root as pet.repo_root
# (= ${workspace.file_path}); put it on sys.path so the sibling `medallion`
# package imports cleanly. `spark` is provided by the pipeline runtime.
_REPO_ROOT = spark.conf.get("pet.repo_root", "")  # noqa: F821
if _REPO_ROOT and _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from pyspark import pipelines as dp
from pyspark.sql import functions as F

from medallion.parse_workbook import load_dataset
from medallion.gold_sql import Silver, supply_select, production_select

# spark is provided by the pipeline runtime
CATALOG = spark.conf.get("pet.catalog", "main")  # noqa: F821
BRONZE = spark.conf.get("pet.bronze_schema", "lactalis_pet_bronze")  # noqa: F821
SILVER = spark.conf.get("pet.silver_schema", "lactalis_pet_silver")  # noqa: F821
GOLD = spark.conf.get("pet.gold_schema", "lactalis_pet_gold")  # noqa: F821
VOLUME_PATH = spark.conf.get(  # noqa: F821
    "pet.volume_path",
    "/Volumes/main/lactalis_pet_bronze/landing/uploads",
)

_SILVER_REF = Silver(catalog=CATALOG, schema=SILVER)

_TABLES = ["sku", "week", "parameter", "demand", "plan_line", "opening_stock"]


def _latest_workbook() -> str:
    """Most-recently-modified .xlsx in the landing volume."""
    if not os.path.isdir(VOLUME_PATH):
        raise FileNotFoundError(f"Landing volume path not found: {VOLUME_PATH}")
    xlsx = [
        os.path.join(VOLUME_PATH, f)
        for f in os.listdir(VOLUME_PATH)
        if f.lower().endswith(".xlsx") and not f.startswith("~$")
    ]
    if not xlsx:
        raise FileNotFoundError(f"No .xlsx workbook found under {VOLUME_PATH}")
    return max(xlsx, key=os.path.getmtime)


@lru_cache(maxsize=1)
def _dataset():
    path = _latest_workbook()
    return os.path.basename(path), load_dataset(path)


def _bronze_df(table: str):
    source_file, ds = _dataset()
    sdf = spark.createDataFrame(ds[table])  # noqa: F821
    return (
        sdf.withColumn("_source_file", F.lit(source_file))
           .withColumn("_ingested_at", F.current_timestamp())
    )


# ---------------------------------------------------------------------------
# BRONZE — raw parsed workbook rows + ingest metadata
# ---------------------------------------------------------------------------
for _t in _TABLES:
    def _make_bronze(table: str):
        @dp.materialized_view(name=f"{CATALOG}.{BRONZE}.bronze_{table}")
        def _mv():
            return _bronze_df(table)
        return _mv
    _make_bronze(_t)


# ---------------------------------------------------------------------------
# SILVER — conformed, typed, data-quality-checked
# ---------------------------------------------------------------------------
def _silver_src(table: str):
    return spark.read.table(f"{CATALOG}.{BRONZE}.bronze_{table}").drop("_source_file", "_ingested_at")  # noqa: F821


@dp.materialized_view(name=f"{CATALOG}.{SILVER}.sku")
@dp.expect_all_or_drop({
    "pack_size_known": "pack_size_ml IN (400, 500)",
    "positive_cover": "max_cover_weeks > 0",
    "active_only": "status = 'Active'",
})
def silver_sku():
    return _silver_src("sku")


@dp.materialized_view(name=f"{CATALOG}.{SILVER}.week")
@dp.expect_all_or_drop({
    "valid_horizon": "horizon_index BETWEEN 1 AND 53",
    "has_week_key": "week_key IS NOT NULL",
})
def silver_week():
    return _silver_src("week")


@dp.materialized_view(name=f"{CATALOG}.{SILVER}.parameter")
@dp.expect_or_fail("param_has_value", "value IS NOT NULL")
def silver_parameter():
    return _silver_src("parameter")


@dp.materialized_view(name=f"{CATALOG}.{SILVER}.demand")
@dp.expect_all({
    "non_negative_forecast": "forecast >= 0",
    "non_negative_sales_order": "sales_order >= 0",
})
def silver_demand():
    return _silver_src("demand")


@dp.materialized_view(name=f"{CATALOG}.{SILVER}.plan_line")
@dp.expect_all({"non_negative_planned": "planned_qty >= 0"})
def silver_plan_line():
    return _silver_src("plan_line")


@dp.materialized_view(name=f"{CATALOG}.{SILVER}.opening_stock")
@dp.expect_all({"non_negative_opening": "opening_ea >= 0"})
def silver_opening_stock():
    return _silver_src("opening_stock")


# ---------------------------------------------------------------------------
# GOLD — the rule engine (medallion.gold_sql), batch path (no plan overlay)
# ---------------------------------------------------------------------------
def _read_silver_deps():
    """Force the pipeline dependency edges Gold -> Silver (Gold reads Silver by
    fully-qualified name via spark.sql; touching the tables here registers the
    dependency so ordering is correct)."""
    for t in _TABLES:
        spark.read.table(f"{CATALOG}.{SILVER}.{t}")  # noqa: F821


@dp.materialized_view(name=f"{CATALOG}.{GOLD}.projection")
def gold_projection():
    _read_silver_deps()
    return spark.sql(supply_select(_SILVER_REF, use_overlay=False))  # noqa: F821


@dp.materialized_view(name=f"{CATALOG}.{GOLD}.week_capacity")
def gold_week_capacity():
    _read_silver_deps()
    return spark.sql(production_select(_SILVER_REF, use_overlay=False))  # noqa: F821


@dp.materialized_view(name=f"{CATALOG}.{GOLD}.summary")
def gold_summary():
    _read_silver_deps()
    inner = supply_select(_SILVER_REF, use_overlay=False)
    return spark.sql(f"SELECT colour, COUNT(*) AS n FROM ({inner}) grid GROUP BY colour")  # noqa: F821


@dp.materialized_view(name=f"{CATALOG}.{GOLD}.projection_snapshot")
def gold_projection_snapshot():
    _read_silver_deps()
    return spark.sql(supply_select(_SILVER_REF, use_overlay=False)).withColumn(  # noqa: F821
        "run_ts", F.current_timestamp()
    )
