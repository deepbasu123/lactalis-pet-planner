from backend.capacity import ceiling, week_flags, changeovers

P = {"cap_400ml_1_2_sku":650000,"cap_400ml_3_sku":600000,
     "cap_500ml_changeover":650000,"cap_500ml_steady_1_2":700000,
     "cap_500ml_3sku_penalty":50000}

def test_ceilings_from_params():
    assert ceiling(400, 2, False, P) == 650000
    assert ceiling(400, 3, False, P) == 600000
    assert ceiling(500, 2, False, P) == 700000
    assert ceiling(500, 2, True,  P) == 650000
    assert ceiling(500, 3, False, P) == 650000   # 700000 - 50000

def test_r1_two_pack_sizes_flags():
    f = week_flags([], packs_in_week={400,500}, n_skus=2, total=100, maint="None", ceiling=650000)
    assert f["R1"] is True

def test_r2_more_than_three_skus():
    f = week_flags([], {400}, n_skus=4, total=100, maint="None", ceiling=650000)
    assert f["R2"] is True

def test_r3_over_ceiling():
    f = week_flags([], {400}, n_skus=2, total=700000, maint="None", ceiling=650000)
    assert f["R3"] is True and f["over"] == 50000

def test_r4_production_in_full_maintenance():
    f = week_flags([], {400}, n_skus=1, total=100, maint="Full", ceiling=650000)
    assert f["R4"] is True

def test_changeover_count():
    weeks = [{"idx":1,"pack":400},{"idx":2,"pack":400},{"idx":3,"pack":500},{"idx":4,"pack":400}]
    assert changeovers(weeks) == 2
