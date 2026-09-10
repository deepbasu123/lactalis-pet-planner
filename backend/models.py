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
