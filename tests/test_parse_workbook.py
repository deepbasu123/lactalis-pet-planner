"""Unit tests for the medallion Excel parser."""
import datetime
import os
import tempfile

import pytest

from medallion.parse_workbook import (
    infer_pack_size_ml,
    iso_week_key,
    load_dataset,
)
from medallion.synth_workbook import write_synth_workbook


def test_infer_pack_size():
    assert infer_pack_size_ml("PAULS ZYMIL FLAV MILK CHOC 400ML") == 400
    assert infer_pack_size_ml("OAK PLUS FLAVOURED MILK NAS CHOC 6x500ml") == 500
    with pytest.raises(ValueError):
        infer_pack_size_ml("MYSTERY MILK NO SIZE")


def test_iso_week_key():
    assert iso_week_key(datetime.date(2026, 8, 24)) == "2026-W35"


def test_load_shapes_and_schema():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "wb.xlsx")
        write_synth_workbook(path, seed=42)
        ds = load_dataset(path)
    assert len(ds["sku"]) == 11
    assert len(ds["week"]) == 52
    assert len(ds["demand"]) == 11 * 52
    assert len(ds["plan_line"]) == 11 * 52
    assert len(ds["opening_stock"]) == 11
    assert set(ds["sku"].columns) == {
        "sku_code", "description", "pack_size_ml", "priority", "status",
        "shelf_life_days", "mlor_days", "max_cover_weeks",
    }
    assert set(ds["demand"].columns) == {
        "sku_code", "week_key", "forecast", "sales_order",
        "distr_demand_planned", "distr_demand_tlb",
    }
    # every SKU resolves to a 400 or 500 ml pack
    assert set(ds["sku"]["pack_size_ml"]).issubset({400, 500})
