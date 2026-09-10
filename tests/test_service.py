from backend.data_gen import generate
from backend.service import build_supply, build_production, summary


def test_supply_dense_and_coloured():
    d = generate()
    s = build_supply(d)
    assert len(s) == 11 * 52
    assert set(s["colour"]).issubset({
        "dark_blue","light_blue","green","amber","red","dark_red","black"})


def test_display_shows_negative_when_short():
    d = generate()
    s = build_supply(d)
    shorts = s[s["raw"] < 0]
    assert (shorts["display_value"] == shorts["raw"]).all()


def test_edit_overlay_changes_supply():
    d = generate()
    base = build_supply(d)
    overlay = {("70526","2026-W40"): 500000}   # add production
    edited = build_supply(d, plan_overlay=overlay)
    # some later week's close should differ after the QA offset
    assert not base["close"].equals(edited["close"])


def test_summary_counts_all_bands_present():
    d = generate()
    s = build_supply(d)
    c = summary(s, d)["counts"]
    assert sum(c.values()) == 11 * 52


def test_distribution_spans_bands():
    """Regression: seed-42 grid must cover all 7 bands with a balanced distribution."""
    d = generate()
    s = build_supply(d)
    c = summary(s, d)["counts"]
    all_bands = {"dark_blue", "light_blue", "green", "amber", "red", "dark_red", "black"}
    for band in all_bands:
        assert c.get(band, 0) >= 1, f"band {band!r} is missing (count=0)"
    healthy = c.get("green", 0) + c.get("light_blue", 0) + c.get("dark_blue", 0)
    assert healthy >= 250, f"healthy count {healthy} < 250"
    assert c.get("green", 0) >= 90, f"green {c.get('green',0)} < 90"
    assert c.get("amber", 0) >= 30, f"amber {c.get('amber',0)} < 30"
    assert c.get("red", 0) >= 60, f"red {c.get('red',0)} < 60"
    assert c.get("dark_blue", 0) <= 210, f"dark_blue {c.get('dark_blue',0)} > 210"
    assert c.get("black", 0) <= 60, f"black {c.get('black',0)} > 60"
