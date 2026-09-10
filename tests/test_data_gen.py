from backend.data_gen import generate


def test_eleven_skus_with_expected_codes():
    d = generate()
    assert len(d["sku"]) == 11
    assert set(d["sku"]["sku_code"]) == {
        "60444","61108","61747","61924","70526","70535",
        "228500","228510","230150","230540","230550"}
    assert set(d["sku"]["pack_size_ml"]) == {400, 500}


def test_52_week_horizon_from_w35():
    d = generate()
    assert len(d["week"]) == 52
    first = d["week"].sort_values("horizon_index").iloc[0]
    assert first["week_key"] == "2026-W35"
    assert str(first["week_commencing"]) == "2026-08-24"


def test_demand_and_plan_are_dense():
    d = generate()
    assert len(d["demand"]) == 11 * 52
    assert len(d["plan_line"]) == 11 * 52


def test_parameters_seeded():
    d = generate()
    p = dict(zip(d["parameter"]["name"], d["parameter"]["value"]))
    assert p["cap_400ml_1_2_sku"] == 650000
    assert p["qa_hold_weeks"] == 2
    assert p["target_cover_weeks"] == 3


def test_total_production_near_screenshot():
    d = generate()
    total = d["plan_line"]["orig_qty"].sum()
    assert 20_000_000 <= total <= 26_000_000  # screenshot ~22.76M


def test_deterministic():
    a, b = generate(7), generate(7)
    assert a["demand"].equals(b["demand"])
