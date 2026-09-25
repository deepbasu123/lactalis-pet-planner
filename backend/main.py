"""backend/main.py

FastAPI serving layer for the Lactalis PET Line Planner.

THIN BY DESIGN. This process contains no business logic. Every projection,
traffic-light colour, and capacity-rule breach is computed by the Gold SQL
engine (medallion.gold_sql) running on a serverless SQL warehouse. This module
only: runs that SQL, reads/writes Silver (the editable plan overlay), accepts
Excel uploads into a UC Volume and triggers the Lakeflow pipeline, and proxies
Genie. Edits recompute in ~1-2s on the warm warehouse.

Endpoints run as plain sync `def` so FastAPI executes them in its threadpool —
blocking warehouse calls never stall the event loop.
"""
from __future__ import annotations

import datetime
import io
import logging
import os

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.staticfiles import StaticFiles

from backend import warehouse
from backend.config import settings
from backend.models import (
    EditRequest,
    GenieAskRequest,
    MetaResponse,
    OkResponse,
    PipelineStatusResponse,
    ProductionResponse,
    PutParameterRequest,
    PutSkuRequest,
    PutWeekRequest,
    RecalcResponse,
    ResetWeekRequest,
    ScenarioRequest,
    SummaryOrigVsPlan,
    SummaryResponse,
    UploadResponse,
)
from medallion.gold_sql import production_select, summary_select, supply_select

logger = logging.getLogger(__name__)

app = FastAPI(title="Lactalis PET Line Planner (medallion)")


# ---------------------------------------------------------------------------
# Grid assembly — thin wrappers that run Gold SQL and shape the response
# ---------------------------------------------------------------------------

def _supply(scenario: str) -> list[dict]:
    return warehouse.run_sql(supply_select(warehouse.silver(), scenario=scenario))


def _production(scenario: str) -> ProductionResponse:
    rows = warehouse.run_sql(production_select(warehouse.silver(), scenario=scenario))
    week_totals = {r["week_key"]: float(r["total"]) for r in rows}
    week_flags = {
        r["week_key"]: {
            "R1": bool(r["r1"]), "R2": bool(r["r2"]), "R3": bool(r["r3"]),
            "R4": bool(r["r4"]), "no_rule": bool(r["no_rule"]),
            "over": int(r["over_units"]),
        }
        for r in rows
    }
    changeovers = sum(1 for r in rows if r["is_changeover"])
    plan_rows = warehouse.effective_plan_rows(scenario)
    return ProductionResponse(
        rows=plan_rows,
        week_totals=week_totals,
        week_flags=week_flags,
        changeovers=changeovers,
    )


def _summary(scenario: str) -> SummaryResponse:
    s = warehouse.silver()
    working_rows = warehouse.run_sql(summary_select(s, scenario=scenario))
    working = {r["colour"]: int(r["n"]) for r in working_rows}
    orig_sql = (
        "SELECT colour, COUNT(*) AS n FROM (\n"
        + supply_select(s, plan_col="orig_qty", use_overlay=False)
        + "\n) g GROUP BY colour"
    )
    orig_rows = warehouse.run_sql(orig_sql)
    original = {r["colour"]: int(r["n"]) for r in orig_rows}
    return SummaryResponse(
        counts=working,
        original_vs_plan=SummaryOrigVsPlan(original=original, working=working),
    )


def _grids(scenario: str, report: dict | None = None) -> RecalcResponse:
    # NOTE (perf follow-up): each edit runs supply + production + summary, and
    # summary re-runs the projection twice (working + baseline). With the
    # closed-form projection each is ~4s, so an edit is ~15s. A safe win is to
    # compute the working supply once and derive the working colour counts from
    # its `colour` field, computing only the baseline separately — it needs the
    # 3-4 tests that monkeypatch `_summary` updated in lockstep, so it is left
    # as a follow-up rather than destabilising the tested backend here.
    return RecalcResponse(
        supply=_supply(scenario),
        production=_production(scenario),
        summary=_summary(scenario),
        report=report,
    )


# ---------------------------------------------------------------------------
# Health + metadata
# ---------------------------------------------------------------------------

@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/api/meta", response_model=MetaResponse)
def api_meta() -> MetaResponse:
    """SKU master, week horizon and parameters — read straight from Silver."""
    s = warehouse.silver()
    return MetaResponse(
        skus=warehouse.run_sql(f"SELECT * FROM {s.t('sku')} ORDER BY priority"),
        weeks=warehouse.run_sql(f"SELECT * FROM {s.t('week')} ORDER BY horizon_index"),
        parameters=warehouse.run_sql(f"SELECT * FROM {s.t('parameter')} ORDER BY name"),
    )


# ---------------------------------------------------------------------------
# Read grids (Gold)
# ---------------------------------------------------------------------------

@app.get("/api/supply")
def api_supply(scenario: str = "working") -> list[dict]:
    return _supply(scenario)


@app.get("/api/production", response_model=ProductionResponse)
def api_production(scenario: str = "working") -> ProductionResponse:
    return _production(scenario)


@app.get("/api/summary", response_model=SummaryResponse)
def api_summary(scenario: str = "working") -> SummaryResponse:
    return _summary(scenario)


# ---------------------------------------------------------------------------
# Plan edits (write Silver overlay, recompute Gold)
# ---------------------------------------------------------------------------

@app.post("/api/plan/edit", response_model=RecalcResponse)
def api_plan_edit(body: EditRequest) -> RecalcResponse:
    """Write one cell to the overlay and return the recomputed grids.

    Rejects edits to locked (time-fence) weeks with 409 (RULE-020).
    """
    locked = warehouse.week_is_locked(body.week_key)
    if locked is None:
        raise HTTPException(status_code=404, detail=f"Week {body.week_key!r} not found")
    if locked:
        raise HTTPException(status_code=409, detail="Week is locked (time fence) and cannot be modified")

    qty = max(0.0, float(body.planned_qty))
    warehouse.overlay_merge(
        body.scenario,
        [{"sku_code": body.sku_code, "week_key": body.week_key, "planned_qty": qty}],
    )
    return _grids(body.scenario)


@app.post("/api/plan/save", response_model=OkResponse)
def api_plan_save(body: ScenarioRequest) -> OkResponse:
    """Commit the overlay into the baseline plan_line and clear the overlay."""
    warehouse.overlay_save(body.scenario)
    return OkResponse(ok=True)


@app.post("/api/plan/discard", response_model=OkResponse)
def api_plan_discard(body: ScenarioRequest) -> OkResponse:
    """Drop all pending overlay edits for the scenario."""
    warehouse.overlay_discard(body.scenario)
    return OkResponse(ok=True)


@app.post("/api/plan/reset-week", response_model=RecalcResponse)
def api_plan_reset_week(body: ResetWeekRequest) -> RecalcResponse:
    """Revert one week to the baseline plan (drop its overlay rows), recompute."""
    warehouse.overlay_discard_week(body.scenario, body.week_key)
    return _grids(body.scenario)


@app.post("/api/autofix", response_model=RecalcResponse)
def api_autofix(body: ScenarioRequest) -> RecalcResponse:
    """Stage strict-trim fixes into the overlay (rule maths from Gold SQL)."""
    from backend import autofix
    report = autofix.strict_trim(body.scenario)
    return _grids(body.scenario, report=report)


@app.post("/api/recalc", response_model=RecalcResponse)
def api_recalc_post(body: ScenarioRequest) -> RecalcResponse:
    return _grids(body.scenario)


@app.get("/api/recalc", response_model=RecalcResponse)
def api_recalc_get(scenario: str = "working") -> RecalcResponse:
    return _grids(scenario)


# ---------------------------------------------------------------------------
# Editable configuration (Silver writes -> recompute). RULE-005 configurability.
# ---------------------------------------------------------------------------

@app.put("/api/parameters", response_model=RecalcResponse)
def api_put_parameters(body: PutParameterRequest, scenario: str = "working") -> RecalcResponse:
    warehouse.update_parameter(body.name, body.value)
    return _grids(scenario)


@app.put("/api/weeks", response_model=RecalcResponse)
def api_put_weeks(body: PutWeekRequest, scenario: str = "working") -> RecalcResponse:
    fields: dict = {}
    if body.maintenance_type is not None:
        fields["maintenance_type"] = body.maintenance_type
    if body.is_locked is not None:
        fields["is_locked"] = body.is_locked
    if body.note is not None:
        fields["note"] = body.note
    warehouse.update_week(body.week_key, fields)
    return _grids(scenario)


@app.put("/api/skus", response_model=RecalcResponse)
def api_put_skus(body: PutSkuRequest, scenario: str = "working") -> RecalcResponse:
    fields: dict = {}
    if body.priority is not None:
        fields["priority"] = body.priority
    if body.status is not None:
        fields["status"] = body.status
    warehouse.update_sku(body.sku_code, fields)
    return _grids(scenario)


# ---------------------------------------------------------------------------
# Excel upload -> UC Volume -> Lakeflow pipeline
# ---------------------------------------------------------------------------

@app.post("/api/upload", response_model=UploadResponse)
def api_upload(file: UploadFile = File(...)) -> UploadResponse:
    """Land an uploaded workbook in the Volume and trigger the medallion ETL job."""
    if not settings.job_id:
        raise HTTPException(status_code=503, detail="ETL job is not configured yet.")
    cat, sch, vol = settings.volume.split(".", 2)
    stamp = datetime.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    safe_name = os.path.basename(file.filename or "upload.xlsx")
    path = f"/Volumes/{cat}/{sch}/{vol}/uploads/{stamp}_{safe_name}"

    w = warehouse.get_workspace_client()
    try:
        w.files.upload(path, io.BytesIO(file.file.read()), overwrite=True)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Volume upload failed")
        raise HTTPException(status_code=502, detail=f"Upload failed: {exc}") from exc
    try:
        run = w.jobs.run_now(job_id=int(settings.job_id))
    except Exception as exc:  # noqa: BLE001
        logger.exception("ETL job start failed")
        raise HTTPException(status_code=502, detail=f"ETL job start failed: {exc}") from exc
    return UploadResponse(run_id=str(run.run_id))


# Map Lakeflow Job run states to the RUNNING/COMPLETED/FAILED the UI expects.
_JOB_LIFECYCLE = {
    "PENDING": "RUNNING", "RUNNING": "RUNNING", "TERMINATING": "RUNNING",
    "QUEUED": "RUNNING", "WAITING_FOR_RETRY": "RUNNING",
    "SKIPPED": "FAILED", "INTERNAL_ERROR": "FAILED",
}


@app.get("/api/pipeline/status", response_model=PipelineStatusResponse)
def api_pipeline_status(run_id: str) -> PipelineStatusResponse:
    """Status of an ETL job run, mapped to RUNNING/COMPLETED/FAILED for the UI."""
    if not settings.job_id:
        raise HTTPException(status_code=503, detail="ETL job is not configured yet.")
    w = warehouse.get_workspace_client()
    try:
        run = w.jobs.get_run(run_id=int(run_id))
    except Exception as exc:  # unknown/invalid run_id -> 404, not an opaque 500
        raise HTTPException(status_code=404, detail=f"No ETL run '{run_id}' found.") from exc
    st = run.state
    life = st.life_cycle_state.value if (st and st.life_cycle_state) else "UNKNOWN"
    result = st.result_state.value if (st and st.result_state) else None
    if life == "TERMINATED":
        state = "COMPLETED" if result == "SUCCESS" else "FAILED"
    else:
        state = _JOB_LIFECYCLE.get(life, life)
    return PipelineStatusResponse(state=state, detail={"life_cycle_state": life, "result_state": result})


# ---------------------------------------------------------------------------
# Export (presentation only — feeds Gold rows into the existing builders)
# ---------------------------------------------------------------------------

@app.get("/api/export")
def api_export(format: str = "xlsx", scenario: str = "working"):
    import pandas as pd
    from fastapi import Response

    from backend import export as export_mod

    s = warehouse.silver()
    sku_df = pd.DataFrame(warehouse.run_sql(f"SELECT * FROM {s.t('sku')} ORDER BY priority"))
    week_df = pd.DataFrame(warehouse.run_sql(f"SELECT * FROM {s.t('week')} ORDER BY horizon_index"))
    param_df = pd.DataFrame(warehouse.run_sql(f"SELECT * FROM {s.t('parameter')} ORDER BY name"))
    plan_df = pd.DataFrame(warehouse.effective_plan_rows(scenario))
    if not plan_df.empty:
        plan_df["orig_qty"] = plan_df["planned_qty"]
    dataset = {"sku": sku_df, "week": week_df, "parameter": param_df, "plan_line": plan_df}

    supply_df = pd.DataFrame(_supply(scenario))
    prod_model = _production(scenario)
    prod = {
        "rows": prod_model.rows,
        "week_totals": prod_model.week_totals,
        "week_flags": prod_model.week_flags,
        "changeovers": prod_model.changeovers,
    }
    summ_model = _summary(scenario)
    summ = {
        "counts": summ_model.counts,
        "original_vs_plan": {
            "original": summ_model.original_vs_plan.original,
            "working": summ_model.original_vs_plan.working,
        },
    }

    stamp = datetime.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    if format == "pdf":
        content = export_mod.build_pdf_export(dataset, supply_df, prod, summ)
        return Response(
            content=content,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="pet_report_{stamp}.pdf"'},
        )
    content = export_mod.build_excel_export(dataset, supply_df, prod, summ)
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="pet_export_{stamp}.xlsx"'},
    )


# ---------------------------------------------------------------------------
# Genie proxy
# ---------------------------------------------------------------------------

@app.post("/api/genie/ask")
def api_genie_ask(body: GenieAskRequest) -> dict:
    if not settings.genie_space_id:
        raise HTTPException(status_code=503, detail="Genie is not configured yet.")
    try:
        from backend import genie
        return genie.ask(settings.genie_space_id, body.question, body.conversation_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Genie ask failed: %s", exc)
        raise HTTPException(status_code=502, detail="Genie could not answer right now.") from exc


@app.get("/api/genie/poll")
def api_genie_poll(conversation_id: str, message_id: str) -> dict:
    if not settings.genie_space_id:
        raise HTTPException(status_code=503, detail="Genie is not configured yet.")
    try:
        from backend import genie
        return genie.poll(settings.genie_space_id, conversation_id, message_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Genie poll failed: %s", exc)
        raise HTTPException(status_code=502, detail="Genie could not answer right now.") from exc


# ---------------------------------------------------------------------------
# Static frontend (only when built)
# ---------------------------------------------------------------------------

_dist_dir = os.path.join(os.path.dirname(__file__), "..", "frontend", "dist")
if os.path.isdir(_dist_dir):
    app.mount("/", StaticFiles(directory=_dist_dir, html=True), name="static")
