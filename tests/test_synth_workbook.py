"""Round-trip: synth_workbook -> parse_workbook must reproduce data_gen.generate(42).

This proves the synthetic seed and the pipeline's Excel parser agree, so the
demo data that flows through Bronze->Silver->Gold is the same dataset the SQL
engine was validated against.
"""
import os
import tempfile

from backend import data_gen
from medallion.synth_workbook import write_synth_workbook
from medallion.parse_workbook import load_dataset


def _parsed():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "wb.xlsx")
        write_synth_workbook(path, seed=42)
        return load_dataset(path)


def test_sku_round_trip():
    ds = data_gen.generate(42)
    g = ds["sku"].set_index("sku_code").sort_index()
    parsed = _parsed()
    p = parsed["sku"].set_index("sku_code").sort_index()
    assert list(p.index) == list(g.index)
    for col in ["description", "pack_size_ml", "priority", "status",
                "shelf_life_days", "mlor_days", "max_cover_weeks"]:
        assert list(p[col]) == list(g[col]), f"sku.{col} mismatch"


def test_week_keys_round_trip():
    ds = data_gen.generate(42)
    parsed = _parsed()
    assert (list(parsed["week"].sort_values("horizon_index")["week_key"])
            == list(ds["week"].sort_values("horizon_index")["week_key"]))
    # Full maintenance survives via the MAINT marker; Partial is carried by qty only.
    full = parsed["week"][parsed["week"]["maintenance_type"] == "Full"]
    assert len(full) == 1 and int(full.iloc[0]["horizon_index"]) == 20


def test_demand_round_trip():
    ds = data_gen.generate(42)
    parsed = _parsed()
    g = ds["demand"].set_index(["sku_code", "week_key"]).sort_index()
    p = parsed["demand"].set_index(["sku_code", "week_key"]).sort_index()
    for col in ["forecast", "sales_order", "distr_demand_planned", "distr_demand_tlb"]:
        assert (abs(p[col] - g[col]) < 1e-6).all(), f"demand.{col} mismatch"


def test_plan_and_opening_round_trip():
    ds = data_gen.generate(42)
    parsed = _parsed()
    gp = ds["plan_line"].set_index(["sku_code", "week_key"]).sort_index()
    pp = parsed["plan_line"].set_index(["sku_code", "week_key"]).sort_index()
    for col in ["planned_qty", "orig_qty"]:
        assert (abs(pp[col] - gp[col]) < 1e-6).all(), f"plan.{col} mismatch"
    go = ds["opening_stock"].set_index("sku_code").sort_index()
    po = parsed["opening_stock"].set_index("sku_code").sort_index()
    assert (abs(po["opening_ea"] - go["opening_ea"]) < 1e-6).all()


def test_parameters_round_trip():
    ds = data_gen.generate(42)
    parsed = _parsed()
    assert (dict(zip(parsed["parameter"]["name"], parsed["parameter"]["value"]))
            == dict(zip(ds["parameter"]["name"], ds["parameter"]["value"])))
