# Databricks notebook source
# MAGIC %md
# MAGIC # PET medallion ETL (notebook)
# MAGIC Excel &rarr; Bronze &rarr; Silver &rarr; Gold. Plain notebook cells (DataFrame writes +
# MAGIC `spark.sql`) instead of a declarative pipeline; produces the identical Delta
# MAGIC tables. Bronze parses the latest uploaded workbook, Silver conforms + applies
# MAGIC the data-quality drops, Gold runs the shared `medallion.gold_sql` rule engine.

# COMMAND ----------
# openpyxl is provided by the job's serverless environment (see resources/pet_job.job.yml);
# declared there rather than via %pip so a Python restart never truncates this job.
import os
import sys
from pyspark.sql import functions as F

for _name, _default in [
    ("repo_root", ""),
    ("catalog", "main"),
    ("bronze_schema", "lactalis_pet_bronze"),
    ("silver_schema", "lactalis_pet_silver"),
    ("gold_schema", "lactalis_pet_gold"),
    ("volume_path", "/Volumes/main/lactalis_pet_bronze/landing/uploads"),
]:
    dbutils.widgets.text(_name, _default)

# The bundle syncs this notebook alongside the `medallion` package; repo_root
# (= ${workspace.file_path}) puts it on sys.path so the same engine imports.
REPO_ROOT = dbutils.widgets.get("repo_root")
if REPO_ROOT and REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

CATALOG = dbutils.widgets.get("catalog")
BRONZE = dbutils.widgets.get("bronze_schema")
SILVER = dbutils.widgets.get("silver_schema")
GOLD = dbutils.widgets.get("gold_schema")
VOLUME_PATH = dbutils.widgets.get("volume_path")

from medallion.parse_workbook import load_dataset
from medallion.gold_sql import Silver, supply_select, production_select

TABLES = ["sku", "week", "parameter", "demand", "plan_line", "opening_stock"]

# COMMAND ----------
# Bronze — parse the latest uploaded workbook (openpyxl) + ingest metadata

def _latest_workbook(path: str) -> str:
    xs = [
        os.path.join(path, f)
        for f in os.listdir(path)
        if f.lower().endswith(".xlsx") and not f.startswith("~$")
    ]
    if not xs:
        raise FileNotFoundError(f"No .xlsx workbook found under {path}")
    return max(xs, key=os.path.getmtime)

src_path = _latest_workbook(VOLUME_PATH)
src_name = os.path.basename(src_path)
dataset = load_dataset(src_path)
for _t in TABLES:
    (
        spark.createDataFrame(dataset[_t])
        .withColumn("_source_file", F.lit(src_name))
        .withColumn("_ingested_at", F.current_timestamp())
        .write.mode("overwrite").option("overwriteSchema", "true")
        .saveAsTable(f"{CATALOG}.{BRONZE}.bronze_{_t}")
    )
print("BRONZE written from", src_name)

# COMMAND ----------
# Silver — conform + data-quality drops (same rules as the former pipeline expectations)

def _silver_src(t: str):
    return spark.read.table(f"{CATALOG}.{BRONZE}.bronze_{t}").drop("_source_file", "_ingested_at")

# sku / week carried the drop-expectations; the rest were warn-only (pass through).
(
    _silver_src("sku")
    .filter("pack_size_ml IN (400, 500) AND max_cover_weeks > 0 AND status = 'Active'")
    .write.mode("overwrite").option("overwriteSchema", "true")
    .saveAsTable(f"{CATALOG}.{SILVER}.sku")
)
(
    _silver_src("week")
    .filter("horizon_index BETWEEN 1 AND 53 AND week_key IS NOT NULL")
    .write.mode("overwrite").option("overwriteSchema", "true")
    .saveAsTable(f"{CATALOG}.{SILVER}.week")
)
for _t in ["parameter", "demand", "plan_line", "opening_stock"]:
    _silver_src(_t).write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(f"{CATALOG}.{SILVER}.{_t}")
print("SILVER written")

# COMMAND ----------
# Gold — the rule engine, identical SQL to the app's interactive recompute

s = Silver(catalog=CATALOG, schema=SILVER)
_supply = supply_select(s, use_overlay=False)
spark.sql(f"CREATE OR REPLACE TABLE {CATALOG}.{GOLD}.projection AS {_supply}")
spark.sql(f"CREATE OR REPLACE TABLE {CATALOG}.{GOLD}.week_capacity AS {production_select(s, use_overlay=False)}")
spark.sql(
    f"CREATE OR REPLACE TABLE {CATALOG}.{GOLD}.summary AS "
    f"SELECT colour, COUNT(*) AS n FROM ({_supply}) g GROUP BY colour"
)
spark.sql(
    f"CREATE OR REPLACE TABLE {CATALOG}.{GOLD}.projection_snapshot AS "
    f"SELECT *, current_timestamp() AS run_ts FROM ({_supply}) g"
)
print("GOLD written")

# COMMAND ----------
# Summary — return row counts so the run output is observable

_silver_counts = {t: spark.table(f"{CATALOG}.{SILVER}.{t}").count() for t in TABLES}
_gold_proj = spark.table(f"{CATALOG}.{GOLD}.projection").count()
dbutils.notebook.exit(f"OK catalog={CATALOG} silver={_silver_counts} gold.projection={_gold_proj}")
