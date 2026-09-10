# Lactalis PET Line Planner — Design Spec

Date: 2026-09-10
Status: Approved design, pre-implementation
Owner: Deep Basu (Databricks SA)
Source material: NOVA_PET_Solution_Walkthrough_v14_1.docx, PET_Supply_Table_Calculation_Spec.docx, NOVA_UC3_PET_Rules_and_Sources_2.docx, plus 8 UI screenshots.

## 1. Goal

Rebuild the NOVA PET Line Planner (originally a Power Apps canvas app on Dataverse) as a single Databricks App for Lactalis Australia. It is a weekly SKU-by-week supply and production planner for the PET line (OAK / PAULS / Pauls Zymil flavoured milks, 400ml and 500ml). Planners view a traffic-light supply grid, edit production, and ask questions of the data through an embedded Genie chat.

Confirmed decisions:
- Framework: React (Vite) frontend + FastAPI backend, one deployable Databricks App.
- Data: synthesized to reproduce the 11 SKUs, priorities, pack sizes and parameters shown in the screenshots; realistic forecast/receipt/demand series.
- Genie: embedded chat panel in-app via the Conversation API, over the PET tables.
- Scope: full parity with the screenshots (five tabs) plus the supply grid and Genie.
- Workspace: `deep-test-1` profile. Catalog/schema: `deep_test_1_catalog.lactalis_pet`.
- Styling: light, Lactalis-branded (blue #004B85, sky #5BC5F2, white), sharp corners, generous whitespace, hosted Lactalis logo.
- Repo: private `deepbasu123/lactalis-pet-planner`.

## 2. Architecture

```
Single Databricks App (deep-test-1)
├── React (Vite) frontend  ── branded UI, editable grids, Genie panel (built to static, served by FastAPI)
│        │ REST/JSON
├── FastAPI backend  ── stock + capacity engine (Python); reads/writes UC via SQL warehouse
│        │ Databricks SDK statement execution
└── Unity Catalog: deep_test_1_catalog.lactalis_pet  ── base tables + projection_snapshot
         │
      Genie Space over PET tables  ── embedded chat via Conversation API
```

The app is one unit. All logic lives in the backend; all durable data lives in UC. The engine runs live in FastAPI (Python/pandas): it reads base tables, computes the projection on each recalc, returns the coloured grid to React, and writes a `projection_snapshot` table back to UC so Genie can reason about stock health and colours (not only raw production).

Alternatives rejected: pure SQL view (Lindley clamp awkward and slow to iterate), scheduled job (breaks the interactive edit-and-recolour loop).

## 3. Data model (UC Delta, `deep_test_1_catalog.lactalis_pet`)

### sku
| column | type | notes |
|---|---|---|
| sku_code | STRING | e.g. 61108 |
| description | STRING | e.g. OAK UHT CHOCOLATE 500ML |
| pack_size_ml | INT | 400 or 500 |
| priority | INT | 1 = highest (RULE-021) |
| status | STRING | Active |
| shelf_life_days | INT | |
| mlor_days | INT | minimum life on receipt |
| max_cover_weeks | DOUBLE | (shelf_life - mlor) / 7; populated for all 11 |

### week
| column | type | notes |
|---|---|---|
| week_key | STRING | e.g. 2026-W35 |
| horizon_index | INT | 1..52 |
| week_commencing | DATE | Monday of the ISO week |
| maintenance_type | STRING | None / Partial / Full |
| is_locked | BOOLEAN | time-fence lock (first 3 weeks) |
| note | STRING | |

### parameter
| column | type |
|---|---|
| name | STRING |
| value | DOUBLE |
| description | STRING |

Seed values (from the Parameters screenshot):
cap_400ml_1_2_sku=650000, cap_400ml_3_sku=600000, cap_500ml_changeover=650000, cap_500ml_steady_1_2=700000, cap_500ml_3sku_penalty=50000, qa_hold_weeks=2, max_cover_weeks=16, target_cover_weeks=3, reaction_window=3, demand_horizon=4.

### demand
| column | type | notes |
|---|---|---|
| sku_code | STRING | |
| week_key | STRING | |
| forecast | DOUBLE | |
| sales_order | DOUBLE | |
| distr_demand_planned | DOUBLE | carried for reference |
| distr_demand_tlb | DOUBLE | carried for reference |

### plan_line (editable)
| column | type | notes |
|---|---|---|
| sku_code | STRING | |
| week_key | STRING | |
| planned_qty | DOUBLE | working value edited in the UI |
| orig_qty | DOUBLE | SNP baseline for the Original vs Production comparison |

### opening_stock
| column | type |
|---|---|
| sku_code | STRING |
| opening_ea | DOUBLE |

### projection_snapshot (computed output, persisted for Genie)
| column | type | notes |
|---|---|---|
| sku_code | STRING | |
| week_key | STRING | |
| horizon_index | INT | |
| opening | DOUBLE | previous week close |
| recv | DOUBLE | QA-cleared arrival (production from 2 weeks earlier) |
| prod | DOUBLE | production planned this week |
| demand | DOUBLE | demand consumed this week |
| raw | DOUBLE | closePrev + recv - demand (can be negative) |
| close | DOUBLE | MAX(0, ...) clamped closing stock |
| cover_weeks | INT | 0..3+ forward-cover band |
| severity | INT | 1..7 |
| colour | STRING | dark_blue / light_blue / green / amber / red / dark_red / black |
| updated_at | TIMESTAMP | |

Scale: 11 SKUs x 52 weeks is about 570 rows per weekly table. MERGE on a SQL warehouse is instant.

## 4. The 11 SKUs (reproduced from screenshots; codes from the source docs)

| priority | sku_code | description | pack_size_ml |
|---|---|---|---|
| 1 | 60444 | PAULS ZYMIL FLAV MILK CHOC 400ML | 400 |
| 2 | 61108 | OAK UHT CHOCOLATE 500ML | 500 |
| 3 | 61747 | OAK UHT ICED COFFEE 500ML | 500 |
| 4 | 61924 | OAK UHT STRAWBERRY 500ML | 500 |
| 5 | 70526 | OAK PLUS FLAVOURED MILK NAS CHOC 6x500ml | 500 |
| 6 | 70535 | OAK PLUS FLAVOURED MILK NAS VANILLA 6X500ML | 500 |
| 7 | 228500 | PAULS PLUS CHOCOLATE Flavoured Milk 400ML | 400 |
| 8 | 228510 | PAULS PLUS BANANA HONEY Flavoured Milk 400ML | 400 |
| 9 | 230150 | OAK PLUS FLAVOURED MILK NAS SALTED CAR 6x500ml | 500 |
| 10 | 230540 | Pauls PLUS SUMMER BERRIES Flavoured Milk 400mL | 400 |
| 11 | 230550 | Pauls PLUS DOUBLE ESPRESSO CARAMEL Flavoured Milk 400mL | 400 |

MLOR reference from source (shelf_life / mlor / max_cover_weeks): 60444 180/43/19.6, 61108 200/90/15.7, 61747 180/90/12.9, 61924 180/90/12.9, 70526 200/90/15.7, 70535 180/90/12.9. The five 400ml Pauls SKUs with no source MLOR get plausible values (180/90/12.9) so the over-cover (black) band is demonstrable.

Weeks: 52 weeks starting 2026-W35 (w/c 2026-08-24) through roughly 2027-W29. First 3 weeks locked (time fence). Two maintenance weeks seeded (one Full, one Partial) so R4 is demonstrable. Total planned production tuned near the screenshot value (about 22.76M EA).

## 5. Calculation engine

### Stock on hand (left to right, per SKU)
```
demand(w)              = max(forecast(w), sales_order(w))       # open decision default
receipts_available(w)  = planned_qty(w - qa_hold_weeks)         # QA hold, default 2 weeks
raw(w)                 = close(w-1) + receipts_available(w) - demand(w)
close(w)               = max(0, raw(w))                          # no negative carry-over
```
Opening stock (week 1 close-previous) = `opening_stock.opening_ea`.

Cell display: show `raw` when negative (size of the gap), else `close`.

### Forward cover
Whole weeks of forward forecast that `close` covers, using the next `demand_horizon` weeks:
```
cover = 0 if close <= 0 or close < d1
        1 if close < d1 + d2
        2 if close < d1 + d2 + d3
        3 otherwise            # caps at 3
```

### Severity and colour
```
severity = 1  if close <= 0 and weeks_until <= reaction_window     # dark_red  (lost sale)
           2  if close <= 0                                        # red
           7  if max_cover>0 and close > demand_over_maxcover      # black     (over-cover)
           3  if close < d1                                        # amber
           4  if close < d1 + d2                                   # green
           5  if close < d1 + d2 + d3                              # light_blue
           6  otherwise                                            # dark_blue
```
Ordering: shortage tested first (a stock-out can never read as over-cover); over-cover tested before healthy bands.

Colour mapping:
| colour | hex | band |
|---|---|---|
| dark_blue | #1565C0 | cover > 3 wks |
| light_blue | #5BC5F2 | cover 2-3 wks |
| green | #2E9E5B | cover 1-2 wks |
| amber | #E8A317 | cover 0-1 wks |
| red | #E1382D | stock-out, > reaction_window out |
| dark_red | #8B1A1A | stock-out, <= reaction_window out |
| black | #2B2B2B | over-cover past MLOR window |

### Capacity rules (production side)
- R1: one pack size per week (more than one distinct pack size in a week = breach).
- R2: max 3 producing SKUs per week.
- R3: weekly total must not exceed the ceiling for that (pack size, SKU count, changeover state). Ceilings derived from the cap_* parameters: 400ml 650k (1-2 SKUs) / 600k (3 SKUs); 500ml 650k first week after a size changeover, 700k steady 1-2 SKUs, minus 50k for a 3rd SKU.
- R4: maintenance week (Full) = 0 capacity; any production there is a breach.
- R5: size changeovers counted across the horizon (reported, not enforced).
- Week locks: locked weeks cannot be edited (RULE-020).
- SKU priority: used to rank production preference when capacity is constrained (RULE-021).

### Open business decisions (defaults built; each flippable via parameter or config)
1. Demand = max(Forecast, Sales Order).
2. Opening stock = opening_stock table (SOH at week 1).
3. Week numbering = ISO-8601, shown next to week-commencing date.
4. MLOR populated for all 11 SKUs so black band fires.
5. Amber warning at < 1 week cover; recommendations top up toward target_cover_weeks.
6. Maintenance weeks seeded so R4 fires.

## 6. Backend (FastAPI)

Endpoints (JSON):
- `GET /api/config` — SKUs, weeks, parameters, capacity, run metadata.
- `GET /api/supply` — projection grid (supply table) with colours; supports a working-copy overlay.
- `GET /api/production` — production grid (plan_line) with weekly totals and capacity flags.
- `POST /api/production/edit` — update a working-copy cell (validates lock, pack-size, capacity).
- `POST /api/production/save` — MERGE changed rows to plan_line, recompute, write projection_snapshot.
- `POST /api/production/discard` and `POST /api/production/reset-week`.
- `GET /api/summary` — traffic-light colour counts and Original (SNP) vs Production Plan comparison.
- `PUT /api/parameters` and `PUT /api/weeks` and `PUT /api/skus` — edit config tables.
- `POST /api/genie/ask` and `GET /api/genie/poll` — proxy to the Genie Conversation API.
- `POST /api/recalc` — rerun the engine on the working copy.

Auth: dual-mode. Deployed, use the injected app service-principal OAuth. Local, use the developer profile. Reads/writes go through a SQL warehouse via the Databricks SDK statement execution API.

Working-copy model (mirrors the source app's Qty vs Orig): edits are held server-side per session; Save MERGEs changed rows to plan_line and updates orig on success; Discard/Reset restore from orig; every mutation triggers a recalc.

## 7. Frontend (React + Vite)

- Top bar: Lactalis logo (hosted SVG) left, "PET Line Planner" title, run reference and horizon length right. Lactalis blue on white.
- Hero: Supply (forecast) traffic-light grid, SKU rows x week columns, coloured cells, sticky SKU column and week header, horizontal scroll, week slider.
- Below/toggle: editable Production grid with a weekly total row on top, direct entry plus +/- step, Save / Discard / Reset-week, capacity validation banner.
- Tabs: Production Grid, Week Config, SKU Priority, Parameters, Traffic Light Summary (colour counts + Original vs Production comparison). Config tabs are editable tables.
- Genie panel docked on the side: branded chat, sample questions, streamed answers via poll.
- Light theme, sharp corners, larger fonts for numbers and product names (spec requirement).

## 8. Genie

Create a Genie Space over sku, week, demand, plan_line and projection_snapshot with curated instructions (domain glossary: SOH, cover weeks, QA hold, traffic-light colours) and sample questions. The app embeds a chat panel that calls start-conversation and polls messages through the backend proxy. The app SP is granted CAN RUN on the space.

## 9. Governance and deploy

- App SP grants: USE CATALOG + USE SCHEMA + SELECT + MODIFY on `deep_test_1_catalog.lactalis_pet`, CAN_USE on the warehouse, CAN RUN on the Genie space.
- One deploy script: create catalog objects, load synthetic data, create Genie space, deploy the app, apply grants, verify health.
- Repo: private `deepbasu123/lactalis-pet-planner`, env-driven config (catalog, schema, warehouse, genie space id, app name).

## 10. Verification (per project rules)

Before presenting finished pieces, run an independent verification pass (different model, likely Sonnet) on:
- The calc engine against a hand-worked example from the spec (the three-week lost-sale example, the QA-offset example, the Lindley closed-form numeric proof).
- The synthetic data's internal consistency (demand vs plan, colour distribution plausible, totals near the screenshot).
- Any Databricks API/config claims (Apps SP injection, Genie Conversation API shape, SQL statement execution, grants).

## 11. Out of scope (first build)
- SOH-weeks to 1 decimal and Days' Supply reporting rows (colour bands do not need them).
- Total Target Days Supply benchmark (no confirmed source).
- Side-by-side before/after layout mode (stacked only for now).
- Persisted violation acknowledgements (findings computed live).
