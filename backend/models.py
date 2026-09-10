"""backend/models.py

Pydantic response models for the read API endpoints.  Kept accurate but
not over-engineered: nested rows use dict[str, Any] because the field set
is already validated by the service layer and pinning every sub-field here
adds maintenance surface without adding safety.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class ConfigMeta(BaseModel):
    sku_count: int
    week_count: int
    total_production: float


class ConfigResponse(BaseModel):
    skus: list[dict[str, Any]]
    weeks: list[dict[str, Any]]
    parameters: list[dict[str, Any]]
    meta: ConfigMeta


class SupplyResponse(BaseModel):
    rows: list[dict[str, Any]]


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


# ---------------------------------------------------------------------------
# Edit / save / discard overlay request and response models
# ---------------------------------------------------------------------------

class EditRequest(BaseModel):
    sku_code: str
    week_key: str
    qty: float


class EditResponse(BaseModel):
    status: str
    sku_code: str
    week_key: str
    qty: float


class ResetWeekRequest(BaseModel):
    week_key: str


class SaveResponse(BaseModel):
    status: str
    saved: int  # rows written (or that would have been written in no-live mode)


class DiscardResponse(BaseModel):
    status: str
    cleared: int  # number of overlay entries removed


class RecalcResponse(BaseModel):
    supply: SupplyResponse
    production: ProductionResponse
    summary: SummaryResponse


# ---------------------------------------------------------------------------
# Genie proxy request model
# ---------------------------------------------------------------------------

class GenieAskRequest(BaseModel):
    question: str
    conversation_id: str | None = None


# ---------------------------------------------------------------------------
# PUT /api/parameters, PUT /api/weeks, PUT /api/skus
# ---------------------------------------------------------------------------

class PutParameterRequest(BaseModel):
    name: str
    value: float


class PutWeekRequest(BaseModel):
    week_key: str
    maintenance_type: str | None = None
    is_locked: bool | None = None
    note: str | None = None


class PutSKURequest(BaseModel):
    sku_code: str
    priority: int | None = None
    status: str | None = None


class PutResponse(BaseModel):
    status: str  # "ok"
