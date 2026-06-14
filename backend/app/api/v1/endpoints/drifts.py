"""GET /projects/{id}/drifts, PATCH /projects/{id}/drifts/{drift_id}.

설계서: 2026-06-14-decision-truth-loop-design.md §5.5
프로젝트 멤버 권한 (목록=VIEWER, 상태변경=EDITOR 이상).
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_project_member
from app.database import get_db
from app.models.drift import Drift, DriftStatus
from app.models.workspace import WorkspaceRole
from app.schemas.drift import DriftListOut, DriftOut, DriftPatchIn

router = APIRouter(prefix="/projects", tags=["drifts"])


@router.get("/{project_id}/drifts", response_model=DriftListOut)
async def list_drifts(
    project_id: UUID,
    db: AsyncSession = Depends(get_db),
    status: DriftStatus | None = Query(default=None),
    _role: WorkspaceRole = Depends(require_project_member(hide_existence=True)),
):
    """드리프트 목록. status 필터 옵션. 멤버 누구나 (VIEWER 포함)."""
    base = select(Drift).where(Drift.project_id == project_id)
    if status is not None:
        base = base.where(Drift.status == status)
    rows = (await db.execute(base.order_by(Drift.opened_at.desc()))).scalars().all()
    total = (await db.execute(
        select(func.count()).select_from(base.subquery())
    )).scalar_one()
    return DriftListOut(
        items=[DriftOut.model_validate(r) for r in rows], total=total
    )


@router.patch("/{project_id}/drifts/{drift_id}", response_model=DriftOut)
async def patch_drift(
    project_id: UUID,
    drift_id: UUID,
    body: DriftPatchIn,
    db: AsyncSession = Depends(get_db),
    _role: WorkspaceRole = Depends(
        require_project_member(min_role=WorkspaceRole.EDITOR, hide_existence=True)
    ),
):
    """드리프트 수동 상태 변경: ignore(의도된 불일치) / reopen."""
    drift = await db.get(Drift, drift_id)
    if drift is None or drift.project_id != project_id:
        raise HTTPException(status_code=404, detail="Drift not found")
    if body.action == "ignore":
        drift.status = DriftStatus.IGNORED
    elif body.action == "reopen":
        drift.status = DriftStatus.OPEN
        drift.resolved_at = None
    else:
        raise HTTPException(status_code=400, detail="Unknown action")
    await db.commit()
    await db.refresh(drift)
    return DriftOut.model_validate(drift)
