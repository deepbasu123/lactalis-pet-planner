# Lactalis PET Line Planner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a Lactalis-branded Databricks App (React + FastAPI) that runs the PET line supply/production traffic-light planner, lets planners edit production, and answers questions through an embedded Genie chat, all backed by Unity Catalog.

**Architecture:** One deployable Databricks App. React (Vite) frontend built to static assets and served by FastAPI. FastAPI holds the stock + capacity engine (pandas) and proxies Genie. All durable data in Unity Catalog Delta; a computed `projection_snapshot` is written back so Genie can reason over stock health.

**Tech Stack:** Python 3.11, FastAPI, pandas, databricks-sdk, databricks-sql-connector, pytest; React 18 + Vite + TypeScript, vitest; Databricks Apps, Genie Conversation API, SQL warehouse.

**Spec:** `docs/superpowers/specs/2026-09-10-lactalis-pet-planner-design.md`

## Global Constraints

- Python 3.11 runtime (Databricks Apps). Pin all deps in `requirements.txt`.
- Catalog/schema: `deep_test_1_catalog.lactalis_pet_planner`. Databricks profile for local dev: `deep-test-1`.
- Brand hex: Lactalis blue `#004B85`, sky `#5BC5F2`, white background. Traffic-light hex are fixed in the spec colour table and owned server-side (engine emits colour names; frontend maps name to hex).
- Traffic-light colour names (canonical): `dark_blue`, `light_blue`, `green`, `amber`, `red`, `dark_red`, `black`.
- Engine defaults (open decisions): demand = max(forecast, sales_order); opening stock from `opening_stock`; ISO-8601 weeks; MLOR populated for all 11 SKUs; amber at <1 week cover; QA hold = 2 weeks. All read from the `parameter` table where a param exists.
- Databricks-native only. No general web calls from the app except the Lactalis logo asset and Genie/UC via SDK.
- Every finished component gets an independent verification pass (different model) before it is presented, per project rules.
- Repo: private `deepbasu123/lactalis-pet-planner`. Never commit tokens; secrets via env only.
- User-facing copy: no em dashes.
- Frequent commits; conventional commit messages; end every commit message with the Isaac co-author trailer.

---

## File Structure

```
lactalis-pet-planner/
├── app.yaml                         # Databricks App entrypoint + env
├── requirements.txt                 # pinned python deps
├── README.md
├── .gitignore
├── deploy.py                        # one-shot: objects, data, genie, app, grants, verify
├── backend/
│   ├── __init__.py
│   ├── main.py                      # FastAPI app; serves static frontend + /api
│   ├── config.py                    # env-driven settings (catalog, schema, warehouse, genie id)
│   ├── db.py                        # dual-mode UC access: read to DataFrame, MERGE writes
│   ├── data_gen.py                  # synthetic data generator (pure, seeded)
│   ├── engine.py                    # stock projection: SOH, cover, severity, colour (pure)
│   ├── capacity.py                  # capacity rules R1-R5, ceilings, changeovers (pure)
│   ├── service.py                   # orchestrates engine over base data + working copy
│   ├── genie.py                     # Genie Conversation API proxy
│   └── models.py                    # pydantic request/response models
├── tests/
│   ├── conftest.py                  # fixture: synthetic dataset as DataFrames
│   ├── test_data_gen.py
│   ├── test_engine.py
│   ├── test_capacity.py
│   ├── test_service.py
│   └── test_api.py
└── frontend/
    ├── index.html
    ├── package.json
    ├── vite.config.ts
    ├── src/
    │   ├── main.tsx
    │   ├── App.tsx                  # shell: header, tab nav
    │   ├── theme.css                # Lactalis design tokens
    │   ├── api.ts                   # typed fetch client
    │   ├── colours.ts               # colour-name -> hex map (+ vitest)
    │   ├── components/
    │   │   ├── Header.tsx
    │   │   ├── SupplyGrid.tsx       # hero traffic-light grid
    │   │   ├── ProductionGrid.tsx   # editable
    │   │   ├── WeekConfig.tsx
    │   │   ├── SkuPriority.tsx
    │   │   ├── Parameters.tsx
    │   │   ├── TrafficLightSummary.tsx
    │   │   └── GeniePanel.tsx
    │   └── colours.test.ts
    └── tsconfig.json
```

---

## Task 1: Project scaffold

**Files:**
- Create: `requirements.txt`, `.gitignore`, `backend/__init__.py`, `backend/config.py`, `backend/main.py`, `tests/conftest.py`, `frontend/package.json`, `frontend/vite.config.ts`, `frontend/index.html`, `frontend/src/main.tsx`, `frontend/src/App.tsx`
- Test: `tests/test_health.py`

**Interfaces:**
- Produces: `backend.config.Settings` (attrs: `catalog: str`, `schema: str`, `warehouse_id: str`, `genie_space_id: str`, `host: str`); `backend.main.app` (FastAPI instance with `GET /api/health` returning `{"status": "ok"}`).

- [ ] **Step 1: Write the failing test**
```python
# tests/test_health.py
from fastapi.testclient import TestClient
from backend.main import app

def test_health_ok():
    client = TestClient(app)
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}
```
- [ ] **Step 2: Run to verify it fails** — `pytest tests/test_health.py -v` → FAIL (import error).
- [ ] **Step 3: Implement** `backend/config.py` (pydantic `BaseSettings` reading env `PET_CATALOG`, `PET_SCHEMA`, `DATABRICKS_WAREHOUSE_ID`, `PET_GENIE_SPACE_ID`, `DATABRICKS_HOST`, with local defaults `deep_test_1_catalog` / `lactalis_pet_planner`) and `backend/main.py` (create `app`, add `/api/health`, mount `frontend/dist` as static at `/` when it exists).
- [ ] **Step 4: Run to verify it passes** — `pytest tests/test_health.py -v` → PASS.
- [ ] **Step 5: Scaffold frontend** — `npm create vite@latest frontend -- --template react-ts` (or hand-write the listed files); `App.tsx` renders the header text "PET Line Planner" and an empty tab bar. `requirements.txt` pins: `fastapi==0.115.*`, `uvicorn[standard]==0.30.*`, `pandas==2.2.*`, `databricks-sdk==0.30.*`, `databricks-sql-connector==3.*`, `pydantic-settings==2.*`, `pytest==8.*`, `httpx==0.27.*`.
- [ ] **Step 6: Commit** — `git add -A && git commit -m "feat: scaffold backend + frontend, health endpoint"`.

---

## Task 2: Synthetic data generator

**Files:**
- Create: `backend/data_gen.py`, `tests/test_data_gen.py`, `tests/conftest.py`

**Interfaces:**
- Produces: `data_gen.generate(seed: int = 42) -> dict[str, pandas.DataFrame]` with keys `sku, week, parameter, demand, plan_line, opening_stock`. Column schemas exactly as the spec section 3. `conftest.py` exposes a `dataset` fixture = `generate()`.

- [ ] **Step 1: Write failing tests**
```python
# tests/test_data_gen.py
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
```
- [ ] **Step 2: Run to verify fail** — `pytest tests/test_data_gen.py -v`.
- [ ] **Step 3: Implement `generate()`** — build the 11 SKUs (spec section 4 table, MLOR values), 52 ISO weeks from 2026-W35 (compute Mondays, first 3 `is_locked=True`, seed one Full + one Partial maintenance week), the 10 parameters, per-SKU seasonal `forecast` (numpy RNG with `seed`) and `sales_order` (forecast +/- noise), `distr_*` columns, `plan_line` with `orig_qty` = a plausible SNP baseline and `planned_qty = orig_qty`, and `opening_stock` (about 2-3 weeks of early demand). Tune magnitudes so the traffic-light distribution spans all bands and the total lands near 22.76M.
- [ ] **Step 4: Run to verify pass** — `pytest tests/test_data_gen.py -v` → all PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat: synthetic PET dataset generator"`.

---

## Task 3: Stock projection engine (correctness core, rigorous TDD)

**Files:**
- Create: `backend/engine.py`, `tests/test_engine.py`

**Interfaces:**
- Produces:
  - `engine.project_sku(demand: list[float], receipts: list[float], opening: float, qa_weeks: int = 2) -> list[dict]` returning per-week `{opening, recv, raw, close}` (receipts already indexed by production week; the function applies the N-2 shift internally by reading `receipts[i-qa_weeks]`).
  - `engine.cover(close: float, forward: list[float]) -> int` in 0..3.
  - `engine.severity(close: float, weeks_until_shortfall: int, forward: list[float], demand_over_maxcover: float, reaction_window: int) -> int` in 1..7.
  - `engine.colour(sev: int) -> str` mapping 1..7 to the canonical colour names.

- [ ] **Step 1: Write failing tests (spec worked examples verbatim)**
```python
# tests/test_engine.py
from backend.engine import project_sku, cover, severity, colour

def test_lost_sale_not_recovered():
    # spec 3-week example: demand 100/100/100, receipt 250 arrives week 3
    out = project_sku(demand=[100,100,100], receipts=[0,0,250], opening=0, qa_weeks=0)
    assert [round(w["close"]) for w in out] == [0, 0, 150]
    assert out[0]["raw"] == -100 and out[1]["raw"] == -100

def test_lindley_closed_form_matches_stepwise():
    # movements -5,+10,-3 from spec section 12 -> closes 0,10,7
    out = project_sku(demand=[5,0,3], receipts=[0,10,0], opening=0, qa_weeks=0)
    assert [round(w["close"]) for w in out] == [0, 10, 7]

def test_qa_two_week_offset():
    # production 500 entered in week 1 is available (recv) in week 3
    out = project_sku(demand=[0,0,0,0], receipts=[500,0,0,0], opening=0, qa_weeks=2)
    assert out[0]["recv"] == 0 and out[2]["recv"] == 500
    assert out[2]["close"] == 500

def test_cover_counts_whole_forward_weeks():
    assert cover(300, [50,200,400]) == 2       # covers 50 and 200, not the 400
    assert cover(0, [10,10,10]) == 0
    assert cover(10_000, [10,10,10]) == 3       # caps at 3

def test_severity_bands():
    # stock-out inside reaction window -> 1 (dark_red); beyond -> 2 (red)
    assert severity(0, 1, [10,10,10], 0, 3) == 1
    assert severity(0, 9, [10,10,10], 0, 3) == 2
    assert severity(5, 99, [10,10,10], 0, 3) == 3     # < next week -> amber
    assert severity(15, 99, [10,10,10], 0, 3) == 4    # covers 1 not 2 -> green

def test_severity_over_cover_black_beats_healthy():
    # close far exceeds demand across max-cover window -> 7 (black)
    assert severity(10_000, 99, [10,10,10], demand_over_maxcover=500, reaction_window=3) == 7

def test_colour_map():
    assert [colour(s) for s in [1,2,3,4,5,6,7]] == [
        "dark_red","red","amber","green","light_blue","dark_blue","black"]
```
- [ ] **Step 2: Run to verify fail** — `pytest tests/test_engine.py -v`.
- [ ] **Step 3: Implement engine** — `project_sku` iterates left to right: `recv = receipts[i-qa_weeks] if i-qa_weeks>=0 else 0`; `raw = close_prev + recv - demand[i]`; `close = max(0, raw)`; carry `close` forward. `cover` sums forward demand and counts whole weeks up to 3. `severity` follows the spec ordering exactly (stock-out first with reaction-window split, then over-cover, then amber/green/light_blue/dark_blue). `colour` is a dict lookup.
- [ ] **Step 4: Run to verify pass** — `pytest tests/test_engine.py -v` → all PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat: stock projection engine with spec worked-example tests"`.

---

## Task 4: Capacity rules engine

**Files:**
- Create: `backend/capacity.py`, `tests/test_capacity.py`

**Interfaces:**
- Produces:
  - `capacity.ceiling(pack_ml: int, n_skus: int, is_changeover_week: bool, params: dict) -> int` derived from the cap_* params.
  - `capacity.week_flags(week_lines: list[dict], packs_in_week: set[int], n_skus: int, total: float, maint: str, ceiling: int|None) -> dict` returning booleans `R1,R2,R3,R4,no_rule` plus `over` (units above ceiling).
  - `capacity.changeovers(producing_weeks: list[dict]) -> int` counting pack-size changes across ordered producing weeks.

- [ ] **Step 1: Write failing tests**
```python
# tests/test_capacity.py
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
```
- [ ] **Step 2: Run to verify fail** — `pytest tests/test_capacity.py -v`.
- [ ] **Step 3: Implement** the three functions per the spec capacity section.
- [ ] **Step 4: Run to verify pass**.
- [ ] **Step 5: Commit** — `git commit -m "feat: capacity rules engine (R1-R5)"`.

---

## Task 5: Service layer (engine over a dataset + working copy)

**Files:**
- Create: `backend/service.py`, `tests/test_service.py`

**Interfaces:**
- Consumes: `data_gen.generate` shape; `engine.*`; `capacity.*`.
- Produces:
  - `service.build_supply(dataset: dict, plan_overlay: dict|None=None) -> pandas.DataFrame` with columns `sku_code, week_key, horizon_index, opening, recv, prod, demand, raw, close, cover_weeks, severity, colour, display_value`.
  - `service.build_production(dataset, plan_overlay=None) -> dict` with `rows` (per SKU per week planned qty), `week_totals`, and `week_flags` per week.
  - `service.summary(supply_df, dataset, plan_overlay=None) -> dict` with `counts` (per colour) and `original_vs_plan` (colour counts for orig vs working).

- [ ] **Step 1: Write failing tests**
```python
# tests/test_service.py
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
```
- [ ] **Step 2: Run to verify fail**.
- [ ] **Step 3: Implement** `service.py`: join demand/plan/opening per SKU, order by horizon, call `project_sku`, compute cover/severity/colour, `display_value = raw if raw<0 else close`; apply `plan_overlay` (dict keyed by `(sku_code, week_key)`) before projecting; production builder aggregates weekly totals + capacity flags; summary tallies colours and compares orig vs working.
- [ ] **Step 4: Run to verify pass**.
- [ ] **Step 5: Commit** — `git commit -m "feat: service layer over engine with working-copy overlay"`.

---

## Task 6: UC data access (dual-mode)

**Files:**
- Create: `backend/db.py`; Test: `tests/test_db.py` (unit-tests SQL builders only; live calls are integration, run manually)

**Interfaces:**
- Produces:
  - `db.read_table(name: str) -> pandas.DataFrame`.
  - `db.merge_plan_lines(rows: list[dict]) -> int` (MERGE into `plan_line` on sku_code+week_key, returns rows written).
  - `db.write_snapshot(df: pandas.DataFrame) -> None` (overwrite `projection_snapshot`).
  - `db._merge_sql(rows) -> str` (pure, tested).
- Auth: use `databricks.sdk.WorkspaceClient()` which resolves the injected SP env when deployed and the `deep-test-1` profile locally (via `DATABRICKS_CONFIG_PROFILE`). Statement execution against `Settings.warehouse_id`.

- [ ] **Step 1: Write failing test** for `_merge_sql` (asserts it targets `deep_test_1_catalog.lactalis_pet_planner.plan_line`, contains `MERGE`, `WHEN MATCHED`, and escapes numeric values). 
- [ ] **Step 2: Run to verify fail**.
- [ ] **Step 3: Implement** read via `databricks-sql-connector` (cursor -> `fetchall_arrow().to_pandas()`), MERGE/overwrite via SDK `statement_execution.execute_statement(wait_timeout="30s")` then poll per project note (wait 5-50s then GET). Build SQL with parameter-safe numeric formatting.
- [ ] **Step 4: Run to verify pass**.
- [ ] **Step 5: Commit** — `git commit -m "feat: dual-mode Unity Catalog data access"`.

---

## Task 7: Read API endpoints

**Files:**
- Modify: `backend/main.py`; Create: `backend/models.py`; Test: `tests/test_api.py`
- Data source: a module-level dataset loader that uses `db.read_table` when `PET_LIVE=1`, else `data_gen.generate()` (so tests and local dev run without UC).

**Interfaces:**
- Produces: `GET /api/config`, `GET /api/supply`, `GET /api/production`, `GET /api/summary` returning JSON built by `service.*`. Response models in `models.py`.

- [ ] **Step 1: Write failing tests** (TestClient): `/api/config` returns 11 skus + 52 weeks + 10 params; `/api/supply` returns 572 cells each with a `colour`; `/api/summary` counts sum to 572; `/api/production` has a `week_totals` list of length 52.
- [ ] **Step 2: Run to verify fail**.
- [ ] **Step 3: Implement** endpoints delegating to `service`, defaulting to the synthetic dataset when `PET_LIVE` unset.
- [ ] **Step 4: Run to verify pass**.
- [ ] **Step 5: Commit** — `git commit -m "feat: read API (config, supply, production, summary)"`.

---

## Task 8: Edit / save / discard endpoints

**Files:**
- Modify: `backend/main.py`, `backend/models.py`; Test: `tests/test_api.py`
- Working copy: an in-process per-session overlay dict keyed by session id (cookie), holding `(sku_code, week_key) -> planned_qty`.

**Interfaces:**
- Produces: `POST /api/production/edit` (body: sku_code, week_key, qty; rejects if week is locked or edit would introduce a 2nd pack size), `POST /api/production/save` (MERGE overlay to UC via `db.merge_plan_lines`, then `db.write_snapshot`, clears overlay), `POST /api/production/discard`, `POST /api/production/reset-week`, `POST /api/recalc`.

- [ ] **Step 1: Write failing tests**: edit then GET supply reflects the overlay; edit on a locked week returns 409; discard clears the overlay; save (with `PET_LIVE` unset) is a no-op success that clears the overlay.
- [ ] **Step 2: Run to verify fail**.
- [ ] **Step 3: Implement** overlay store + endpoints + validation (lock check against `week`, pack-size check against `sku`).
- [ ] **Step 4: Run to verify pass**.
- [ ] **Step 5: Commit** — `git commit -m "feat: production edit/save/discard/reset endpoints"`.

---

## Task 9: Genie proxy

**Files:**
- Create: `backend/genie.py`; Modify: `backend/main.py`; Test: `tests/test_api.py`

**Interfaces:**
- Produces: `genie.ask(space_id, question, conversation_id=None) -> dict` (start or continue a conversation via SDK), `genie.poll(space_id, conversation_id, message_id) -> dict` (returns status + text + any attached SQL/result). Endpoints `POST /api/genie/ask`, `GET /api/genie/poll`.

- [ ] **Step 1: Write failing test** with a mocked `WorkspaceClient.genie` verifying `ask` returns `{conversation_id, message_id}` and `poll` returns `{status, text}`.
- [ ] **Step 2: Run to verify fail**.
- [ ] **Step 3: Implement** using `databricks-sdk` genie APIs (`w.genie.start_conversation` / `create_message` / `get_message`), guarded so a missing `PET_GENIE_SPACE_ID` returns a friendly 503.
- [ ] **Step 4: Run to verify pass**.
- [ ] **Step 5: Commit** — `git commit -m "feat: Genie conversation proxy"`.

---

## Task 10: Frontend shell + Lactalis theme

**Files:**
- Modify: `frontend/src/App.tsx`, `frontend/src/main.tsx`; Create: `frontend/src/theme.css`, `frontend/src/api.ts`, `frontend/src/colours.ts`, `frontend/src/colours.test.ts`, `frontend/src/components/Header.tsx`

**Interfaces:**
- Consumes: `/api/config`, `/api/supply`, etc via `api.ts` typed client.
- Produces: `colours.ts` exporting `COLOUR_HEX: Record<ColourName, string>` matching the spec table; `App` renders `Header` + tab nav (Production Grid, Week Config, SKU Priority, Parameters, Traffic Light Summary) with the supply grid as the default hero above the active tab.

- [ ] **Step 1: Write failing vitest** — `colours.test.ts` asserts `COLOUR_HEX.dark_blue === "#1565C0"` and all seven names map to the spec hex.
- [ ] **Step 2: Run to verify fail** — `npm run test`.
- [ ] **Step 3: Implement** `theme.css` (design tokens: `--lac-blue:#004B85; --lac-sky:#5BC5F2; --bg:#ffffff;` light theme, sharp corners, system sans stack, larger number font), `Header.tsx` (hosted Lactalis logo SVG left, title, run/horizon right), tab nav, `colours.ts`, `api.ts`.
- [ ] **Step 4: Run to verify pass**; then `npm run build` succeeds.
- [ ] **Step 5: Visual verify** with fe-specialized-agents:web-devloop-tester: run Vite dev server + backend, confirm the header shows the Lactalis logo on white, blue chrome, five tabs render, no console errors.
- [ ] **Step 6: Commit** — `git commit -m "feat: frontend shell + Lactalis theme"`.

---

## Task 11: Supply traffic-light grid (hero)

**Files:**
- Create: `frontend/src/components/SupplyGrid.tsx`

**Interfaces:**
- Consumes: `GET /api/supply`, `GET /api/config`, `COLOUR_HEX`.
- Produces: `<SupplyGrid />` rendering SKU rows x week columns; each cell filled with `COLOUR_HEX[colour]`, text = formatted `display_value` (negative shown in a shortage cell), sticky first column + sticky week header, horizontal scroll, a week-range slider, white text on dark fills for contrast.

- [ ] **Step 1: Implement** the component (data grid hand-built for full colour control; memoised rows).
- [ ] **Step 2: Visual verify** with web-devloop-tester: grid matches the screenshot 7 (coloured DARK_BLUE/GREEN/AMBER/RED cells across weeks), SKU names legible, sticky header works, slider narrows the window.
- [ ] **Step 3: Commit** — `git commit -m "feat: supply traffic-light grid"`.

---

## Task 12: Editable production grid

**Files:**
- Create: `frontend/src/components/ProductionGrid.tsx`

**Interfaces:**
- Consumes: `/api/production`, `/api/production/edit|save|discard|reset-week`.
- Produces: editable grid with a weekly-total row on top, direct entry + `+/-` step buttons, locked weeks read-only with a lock badge, a capacity-validation banner (green "All validation rules pass" or red breach list), and a Save / Discard / Reset-week bar. After each edit it refetches supply so the hero grid recolours.

- [ ] **Step 1: Implement** the component + optimistic edit then refetch.
- [ ] **Step 2: Visual verify** with web-devloop-tester: edit a cell, total updates, supply grid recolours, locked week rejects edit with a message, Save shows a success toast, Discard restores.
- [ ] **Step 3: Commit** — `git commit -m "feat: editable production grid"`.

---

## Task 13: Config tabs + Traffic Light Summary

**Files:**
- Create: `frontend/src/components/WeekConfig.tsx`, `SkuPriority.tsx`, `Parameters.tsx`, `TrafficLightSummary.tsx`; Modify: `backend/main.py` (`PUT /api/parameters`, `PUT /api/weeks`, `PUT /api/skus`)

**Interfaces:**
- Consumes: `/api/config`, `/api/summary`, the new PUT endpoints.
- Produces: three editable tables matching screenshots 2-4 (Parameters with RULE-005 caption; SKU Priority with RULE-021 caption; Week Config with RULE-006/RULE-020 caption), and `TrafficLightSummary` matching screenshot 1 (Green/Dark Blue/Amber/Red/Dark Red/Black count tiles + Original (SNP) vs Production Plan change table).

- [ ] **Step 1: Backend** — add PUT endpoints (write via `db` when live, else update the in-memory dataset) with tests in `tests/test_api.py`; run to pass.
- [ ] **Step 2: Implement** the four components.
- [ ] **Step 3: Visual verify** with web-devloop-tester against screenshots 1-4: captions present, counts correct, editing a parameter then recalc changes the summary.
- [ ] **Step 4: Commit** — `git commit -m "feat: config tabs + traffic light summary"`.

---

## Task 14: Genie chat panel

**Files:**
- Create: `frontend/src/components/GeniePanel.tsx`

**Interfaces:**
- Consumes: `/api/genie/ask`, `/api/genie/poll`.
- Produces: a docked, Lactalis-branded chat panel with sample-question chips ("Which SKUs go red before week 45?", "Total production for OAK 500ml SKUs?"), a message list, and poll-until-complete with a typing indicator; renders any returned SQL/result table compactly.

- [ ] **Step 1: Implement** the panel + polling loop.
- [ ] **Step 2: Visual verify** with web-devloop-tester (with `PET_GENIE_SPACE_ID` set once Task 15 exists): ask a sample question, see a grounded answer; without a space id, panel shows a friendly disabled state.
- [ ] **Step 3: Commit** — `git commit -m "feat: embedded Genie chat panel"`.

---

## Task 15: Genie Space provisioning

**Files:**
- Create: `deploy/create_genie.py` (imported by `deploy.py`)

**Interfaces:**
- Produces: `create_genie.ensure_space(client, catalog, schema, warehouse_id) -> str` (space id). Idempotent: reuse by title if present. Uses the corrected Genie space API shape from project notes (warehouse_id/title top-level; serialized_space a minimal JSON string; tables patched via `data_sources.tables[].identifier`, snake_case, sorted alphabetically).

- [ ] **Step 1: Implement** creation over `sku, week, demand, plan_line, projection_snapshot` with curated instructions (glossary: SOH, cover weeks, QA hold, colour meanings) and note that sample questions are UI-only.
- [ ] **Step 2: Manual verify** — run against `deep-test-1`, then test via start-conversation + poll that a known question returns sensible SQL. Record the space id.
- [ ] **Step 3: Commit** — `git commit -m "feat: Genie space provisioning script"`.

---

## Task 16: Packaging, deploy script, grants

**Files:**
- Create: `app.yaml`, `deploy.py`, `README.md`

**Interfaces:**
- `app.yaml`: command `uvicorn backend.main:app --host 0.0.0.0 --port 8000`; env for catalog/schema/warehouse/genie id; build step note for the frontend (`npm ci && npm run build` producing `frontend/dist`).
- `deploy.py` (idempotent, ordered): create catalog/schema/tables, load synthetic data, ensure Genie space, create/deploy the app, apply SP grants (USE CATALOG/SCHEMA, SELECT, MODIFY on the schema; CAN_USE on the warehouse; CAN RUN on the Genie space), then health-check the app URL and print it.

- [ ] **Step 1: Implement** `deploy.py` using the databricks CLI/SDK; follow the fe-databricks-tools databricks-apps skill for the app create/deploy specifics at execution time.
- [ ] **Step 2: Deploy to `deep-test-1`**; confirm data row counts, app returns `/api/health` ok, SP has grants, Genie answers in-app.
- [ ] **Step 3: Commit** — `git commit -m "feat: app packaging, deploy script, grants"`.

---

## Task 17: End-to-end verification + GitHub

**Files:** Modify: `README.md`

- [ ] **Step 1: Full pytest + vitest green**; `deploy.py` clean run.
- [ ] **Step 2: Independent verification pass** (different model, likely Sonnet): re-derive the engine worked examples, sanity-check the synthetic data distribution and total, and validate the Databricks claims (Apps SP injection, Genie API shape, grants). Reconcile any flags before presenting.
- [ ] **Step 3: Screenshot parity check** against the 8 references (five tabs, supply grid, summary).
- [ ] **Step 4: Create private repo `deepbasu123/lactalis-pet-planner`, push** (surface the Isaac Review tip before pushing). PR description, if any, ends with the Isaac attribution line.
- [ ] **Step 5: Final commit** — `git commit -m "docs: README + run/deploy instructions"`.

---

## Self-Review

**Spec coverage:** data model (T2,T6), engine SOH/QA/no-carry (T3), cover/severity/colour (T3), capacity R1-R5 (T4), working-copy edit/save (T5,T8), read APIs (T7), Genie (T9,T14,T15), five tabs + supply + summary (T10-T13), branding (T10-T11), governance/grants + deploy (T16), verification (T17), out-of-scope items excluded. All spec sections map to a task.

**Placeholder scan:** no TBD/TODO; each code step has real content or a concrete component contract; frontend tasks specify exact files, props, and named visual checks rather than "make it work".

**Type consistency:** `plan_overlay` keyed by `(sku_code, week_key)` used identically in T5 and T8; colour names identical across engine (T3), service (T5), and `COLOUR_HEX` (T10); `project_sku` signature stable across T3 and T5.
