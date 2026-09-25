"""tests/test_autofix.py

Live compliance test for the warehouse-backed strict-trim autofix. Gated on
PET_IT=1 (needs deep-test-1 + the seeded lactalis_pet_silver_dev schema).

The meaningful assertion: after autofix, every UNLOCKED week satisfies the
capacity rules (R1-R4 all false, no over-ceiling) as judged by the Gold SQL
engine itself — not a Python re-implementation.
"""
import os

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("PET_IT") != "1",
    reason="live integration test; set PET_IT=1 (needs deep-test-1 + silver_dev)",
)

_SCENARIO = "autofix_it"


@pytest.fixture(scope="module", autouse=True)
def _cfg():
    from backend import warehouse
    from backend.config import settings
    settings.catalog = "deep_test_1_catalog"
    settings.silver_schema = "lactalis_pet_silver_dev"
    settings.warehouse_id = "5eb2345986480cea"
    os.environ.setdefault("DATABRICKS_CONFIG_PROFILE", "deep-test-1")
    warehouse.get_workspace_client.cache_clear()
    warehouse.overlay_discard(_SCENARIO)
    yield
    warehouse.overlay_discard(_SCENARIO)


def test_autofix_clears_unlocked_capacity_breaches():
    from backend import autofix, warehouse
    from medallion.gold_sql import production_select

    autofix.strict_trim(_SCENARIO)

    weeks = warehouse.run_sql(
        f"SELECT week_key, is_locked FROM {warehouse.silver().t('week')}"
    )
    locked = {w["week_key"] for w in weeks if w["is_locked"]}

    prod = warehouse.run_sql(production_select(warehouse.silver(), scenario=_SCENARIO))
    offenders = [
        r["week_key"] for r in prod
        if r["week_key"] not in locked
        and (r["r1"] or r["r2"] or r["r3"] or r["r4"])
    ]
    assert offenders == [], f"unlocked weeks still breach after autofix: {offenders}"
