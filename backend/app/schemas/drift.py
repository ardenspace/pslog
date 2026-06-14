"""Drift API 요청/응답 스키마.

설계서: 2026-06-14-decision-truth-loop-design.md §5.5
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.drift import DriftStatus, DriftType


class DriftOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    type: DriftType
    status: DriftStatus
    branch: str
    external_id: str | None
    detail: str
    opened_at: datetime
    resolved_at: datetime | None


class DriftListOut(BaseModel):
    items: list[DriftOut]
    total: int


class DriftPatchIn(BaseModel):
    action: str   # "ignore" | "reopen"
