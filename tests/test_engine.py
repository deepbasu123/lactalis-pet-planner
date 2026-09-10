from backend.engine import project_sku, cover, severity, colour


def test_lost_sale_not_recovered():
    # spec 3-week example: demand 100/100/100, receipt 250 arrives week 3
    out = project_sku(demand=[100, 100, 100], receipts=[0, 0, 250], opening=0, qa_weeks=0)
    assert [round(w["close"]) for w in out] == [0, 0, 150]
    assert out[0]["raw"] == -100 and out[1]["raw"] == -100


def test_lindley_closed_form_matches_stepwise():
    # movements -5,+10,-3 from spec section 12 -> closes 0,10,7
    out = project_sku(demand=[5, 0, 3], receipts=[0, 10, 0], opening=0, qa_weeks=0)
    assert [round(w["close"]) for w in out] == [0, 10, 7]


def test_qa_two_week_offset():
    # production 500 entered in week 1 is available (recv) in week 3
    out = project_sku(demand=[0, 0, 0, 0], receipts=[500, 0, 0, 0], opening=0, qa_weeks=2)
    assert out[0]["recv"] == 0 and out[2]["recv"] == 500
    assert out[2]["close"] == 500


def test_cover_counts_whole_forward_weeks():
    assert cover(300, [50, 200, 400]) == 2       # covers 50 and 200, not the 400
    assert cover(0, [10, 10, 10]) == 0
    assert cover(10_000, [10, 10, 10]) == 3       # caps at 3


def test_severity_bands():
    # stock-out inside reaction window -> 1 (dark_red); beyond -> 2 (red)
    assert severity(0, 1, [10, 10, 10], 0, 3) == 1
    assert severity(0, 9, [10, 10, 10], 0, 3) == 2
    assert severity(5, 99, [10, 10, 10], 0, 3) == 3     # < next week -> amber
    assert severity(15, 99, [10, 10, 10], 0, 3) == 4    # covers 1 not 2 -> green


def test_severity_over_cover_black_beats_healthy():
    # close far exceeds demand across max-cover window -> 7 (black)
    assert severity(10_000, 99, [10, 10, 10], demand_over_maxcover=500, reaction_window=3) == 7


def test_colour_map():
    assert [colour(s) for s in [1, 2, 3, 4, 5, 6, 7]] == [
        "dark_red", "red", "amber", "green", "light_blue", "dark_blue", "black"]
