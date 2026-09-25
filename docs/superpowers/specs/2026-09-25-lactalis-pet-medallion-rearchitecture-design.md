# Lactalis PET Line Planner — Medallion Re-Architecture (Design Spec)

**Date:** 2026-09-25
**Author:** deep.basu (with Isaac)
**Branch:** `feature/medallion-rearchitecture`
**Workspace:** `deep-test-1` (`deep_test_1_catalog`)
**Status:** Approved design → implementation

---

## 1. Goal

Move **all rule processing and table processing out of the app/UI and into backend
medallion tables (Bronze → Silver → Gold) computed by data pipelines.** The React +
FastAPI app becomes a thin serving layer: it reads pre-computed Gold, writes plan edits
to Silver, accepts Excel uploads, triggers/polls the pipeline, and proxies Genie. It
holds **zero business logic**. Users can upload the "PET Traffic Lights" workbook and
have it processed end-to-end by the pipeline.

Deploy a working copy to `deep-test-1` as a **new app `lactalis-pet-planner-v2`** with a
**fresh medallion schema**, leaving the current live `lactalis-pet-planner` untouched.

## 2. Current vs Target

**Current (engine-in-app):** `backend/engine.py` (stock projection), `backend/capacity.py`
(rules R1–R5), `backend/autofix.py`, and React helpers (`colours.ts`,
`productionGridHelpers.ts`) compute projections, traffic-light colours, and rule breaches
**live in the request path / browser**. Data sits in UC Delta but the *math* runs in the
app. `backend/excel_source.py` parses the real workbook, but at deploy time, in-process.

**Target (engine-in-medallion):** the projection + all 21 business rules are encoded as
**SQL in the Gold layer**, computed on Databricks compute (the Lakeflow pipeline for bulk;
the serverless SQL warehouse for interactive edits). The app reads Gold. Nothing computes
in the browser or the FastAPI process.

## 3. Verified technical foundation

- **Recursive CTE works on deep-test-1's serverless warehouse** and computes the
  floor-at-zero carryforward recurrence correctly (validated 2026-09-25:
  `100→70→30→40→20→95` for a hand-checked demand/receipt series). All numeric columns must
  be cast to `DOUBLE` consistently across the anchor and recursive terms
  (`CANNOT_MERGE_INCOMPATIBLE_DATA_TYPE` otherwise). → The projection engine is SQL, not a
  Python UDF.
- **Open verification (Unit A):** confirm a recursive CTE is allowed inside a persisted
  `CREATE VIEW`. If not, `gold.projection*` are materialized tables refreshed by (a) the
  pipeline on upload and (b) a warehouse `CREATE OR REPLACE TABLE … AS SELECT` after each
  edit — both server-side; app issues SQL only.

## 4. Medallion layout (`deep_test_1_catalog`, schema-per-layer)

### Volume
- `lactalis_pet_bronze.landing` — uploaded `.xlsx` files land under `uploads/`.

### Bronze — `lactalis_pet_bronze` (raw, as-ingested)
Parsed from the workbook **inside the pipeline** with openpyxl (the proven parsing logic
lifted out of `excel_source.py`). Long/raw shape + ingest metadata.
- `bronze_pet_cells` — (source_file, ingested_at, sheet, row, col, value) OR a
  semi-structured per-sheet raw capture. Preserves the source verbatim for lineage/audit.
- `bronze_snp_cells`, `bronze_mlor_cells` — same idea per source sheet.
  *(Exact bronze grain finalized in Unit B; principle: raw, unconformed, replayable.)*

### Silver — `lactalis_pet_silver` (conformed, typed, DQ-checked)
Same shape `excel_source.py` / `data_gen.py` produce today, as pipeline tables with
**expectations**:
- `sku` (sku_code, description, pack_size_ml, priority, status, shelf_life_days, mlor_days, max_cover_weeks)
- `week` (week_key, horizon_index, week_commencing, maintenance_type, is_locked, note)
- `parameter` (name, value, description) — capacity ceilings, qa_weeks, reaction_window, max_cover default, RULE-017 toggle
- `demand` (sku_code, week_key, forecast, sales_order, distr_demand_planned, distr_demand_tlb)
- `plan_line` (sku_code, week_key, planned_qty, orig_qty) — **pipeline-seeded baseline**
- `opening_stock` (sku_code, opening_ea)
- `plan_overlay` (scenario_id, sku_code, week_key, planned_qty, edited_at) — **app-writable edits** (not pipeline-managed)

**DQ expectations (ported from `excel_source.py` validations):** week alignment
PET↔SNP, pack size inferable (400/500), non-numeric cell detection, duplicate SNP `Total`
row detection, MLOR imputation flagged (not silently invented), active-SKU status.

### Gold — `lactalis_pet_gold` (the engine — all 21 rules as SQL)
- `effective_demand` — RULE-016 (≤4 wks: `GREATEST(sales_order, forecast)`; >4 wks: forecast) + RULE-017 (add distr demand, parameter-toggled)
- `projection` — per sku×week: opening, recv (RULE-008 2-wk lag on the **effective plan** = `COALESCE(overlay, plan_line)`), raw (RULE-009), close (RULE-010 `GREATEST(0, raw)`), forward cover, weeks_until_shortfall, severity 1–7, colour, is_lost_sale (RULE-011 3-wk window), over_cover/black (RULE-013/014/015 max-cover). Recursive-CTE projection.
- `week_capacity` — per week: packs_in_week, n_skus, total_produced, ceiling (RULE-003 400ml, RULE-004 500ml + changeover), R1 mixed-pack (RULE-001), R2 >3 SKUs (RULE-002), R3 over-ceiling, R4 maintenance (RULE-006), changeover flag (RULE-007), no_rule
- `summary` — traffic-light colour distribution, total/actionable/locked breaches, weekly production totals
- `projection_snapshot` — **materialized by the pipeline** each run (run_ts stamped) for Genie / AI-BI / lineage / history

**Rule → layer map** is the acceptance oracle (see §8). Colour bands (spec-exact ordering):
1 dark_red, 2 red, 3 amber, 4 green, 5 light_blue, 6 dark_blue, 7 black.

## 5. Data flows

**A. Upload / full refresh (pipeline):** app writes `.xlsx` to the Volume → triggers the
Lakeflow pipeline → landing → Bronze (openpyxl parse) → Silver (conform + expectations) →
Gold snapshot. UI shows live pipeline progress, then reads refreshed Gold.

**B. Interactive edit (warehouse, ~1–2s):** app writes the edited cell to
`silver.plan_overlay` (MERGE on the warehouse) → Gold `projection`/`week_capacity`/`summary`
recompute over `COALESCE(overlay, plan_line)` → app reads refreshed Gold. **Same rule SQL,
two callers.** Overlay-over-baseline gives free before/after comparison (FR-004). Save =
MERGE overlay into `plan_line.planned_qty` + clear overlay; Discard = delete overlay rows.

**Autofix (RULE-driven):** strict-trim (keep one pack size + top-3 priority SKUs, trim to
ceiling, respect locked/mandatory weeks RULE-020) expressed as a **Gold SQL routine**
staged into the overlay for review → Save/Discard.

## 6. Thin app + API

**Deleted from app/UI:** `engine.py`, `capacity.py`, the rule half of `autofix.py`,
React `colours.ts` + compute helpers. Colours/projections arrive pre-computed from Gold.

**FastAPI surface (thin, no business logic):**
- `GET /api/supply|production|summary` → read Gold views/tables
- `GET /api/skus|weeks|parameters` → read Silver
- `POST /api/plan/edit` → MERGE overlay, return refreshed Gold
- `POST /api/plan/save|discard` → commit/clear overlay
- `POST /api/upload` → write xlsx to Volume, trigger pipeline, return run id
- `GET /api/pipeline/status` → poll pipeline update
- `POST /api/autofix` → run Gold fix routine into overlay
- `GET /api/export` → xlsx/pdf (presentation only, reads Gold)
- Genie proxy endpoints (unchanged)

**Frontend:** preserve the polished Lactalis-branded planner (data is the hero, Genie is a
side panel). Add: an **Upload workbook** flow with live pipeline progress, and a small
**Architecture / medallion lineage** view. Read pre-computed Gold; no client-side math.
Run the UI design skills since the UI changes.

## 7. Deployment (DAB → deep-test-1)

**Databricks Asset Bundle** (`databricks.yml`) defines: the three schemas, the Volume, the
Lakeflow pipeline, the app `lactalis-pet-planner-v2`, an optional refresh job, and grants.
A thin seed/Genie script: generates a synthetic workbook in the real 3-sheet shape (or
uses `--data-file <real.xlsx>`), uploads it to the Volume, runs the pipeline, and points a
Genie space at the curated Gold/Silver tables. **Everything flows through the identical
medallion path** — synthetic and real alike.

App SP grants: USE CATALOG/SCHEMA + SELECT on all three schemas, MODIFY on
`silver.plan_overlay`/`plan_line`, WRITE on the Volume, CAN_USE on the warehouse,
CAN_MANAGE/RUN on the pipeline, CAN_RUN on the Genie space.

## 8. Testing (constant, every step)

- **Golden regression (the oracle):** the existing `tests/test_engine.py` /
  `tests/test_capacity.py` values become the truth table. Prove the SQL Gold output matches
  the old Python engine **number-for-number** on identical inputs — the re-arch changes
  *where* logic runs, not *what* it computes.
- **Pipeline DQ tests:** each Silver expectation has a passing + failing fixture.
- **Acceptance criteria AC-001…AC-010** as end-to-end SQL/API assertions (mixed-pack breach,
  3-vs-4 SKU count, N+2 availability, floor-at-zero, 3-wk lost-sale window, maintenance
  breach, mandatory-week retention, config-change-takes-effect, production total row,
  week-number + commencing date).
- **Thin API tests** (read Gold, write overlay, upload/trigger, poll).
- **Frontend tests** (rendering pre-computed Gold, upload flow states).
- **Every deliverable independently verified by a separate agent on a different model**
  before it is shown (CLAUDE.md rule).

## 9. Architecture diagram

Produced via the FE architecture-diagram skill: Volume → pipeline (Bronze→Silver→Gold) →
warehouse (interactive recompute) → thin app → Genie/AI-BI. Committed to the repo and shown.

## 10. Out of scope

APO/SAP write-back; UHT; multi-user concurrent-scenario isolation beyond `scenario_id`
scoping (single-user demo semantics acceptable).

## 11. Non-negotiables carried from CLAUDE.md

Databricks-native throughout; Python preferred; skills-first; no fabricated facts; every
output verified; nothing sent externally without approval.
