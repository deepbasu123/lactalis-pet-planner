"""backend/models.py — request/response models for the thin serving layer.

Every response is data read from (or written to) Unity Catalog via the
warehouse. No business logic lives here.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel


# ---- reads ----------------------------------------------------------------

class MetaResponse(BaseModel):
    skus: list[dict[str, Any]]
    weeks: list[dict[str, Any]]
    parameters: list[dict[str, Any]]


class ProductionResponse(BaseModel):
    rows: list[dict[str, Any]]
    week_totals: dict[str, float]
    week_flags: dict[str, dict[str, Any]]
    changeovers: int


class SummaryOrigVsPlan(BaseModel):
    original: dict[str, int]
    working: dict[str, int]


class SummaryResponse(BaseModel):
    counts: dict[str, int]
    original_vs_plan: SummaryOrigVsPlan


class RecalcResponse(BaseModel):
    """Returned by edit / autofix — the three grids after a recompute."""
    supply: list[dict[str, Any]]
    production: ProductionResponse
    summary: SummaryResponse
    report: dict[str, Any] | None = None


# ---- writes ---------------------------------------------------------------

class EditRequest(BaseModel):
    sku_code: str
    week_key: str
    planned_qty: float
    scenario: str = "working"


class ScenarioRequest(BaseModel):
    scenario: str = "working"


class ResetWeekRequest(BaseModel):
    week_key: str
    scenario: str = "working"


class PutParameterRequest(BaseModel):
    name: str
    value: float


class PutWeekRequest(BaseModel):
    week_key: str
    maintenance_type: str | None = None
    is_locked: bool | None = None
    note: str | None = None


class PutSkuRequest(BaseModel):
    sku_code: str
    priority: int | None = None
    status: str | None = None


class OkResponse(BaseModel):
    ok: bool = True


class UploadResponse(BaseModel):
    run_id: str


class PipelineStatusResponse(BaseModel):
    state: str
    detail: dict[str, Any] | None = None


# ---- genie ----------------------------------------------------------------

class GenieAskRequest(BaseModel):
    question: str
    conversation_id: str | None = None
