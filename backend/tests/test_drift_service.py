"""drift_service.reconcile — 멱등 open/resolve 코어 단위 테스트.

설계서: 2026-06-14-decision-truth-loop-design.md §5.4
"""

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.drift import Drift, DriftStatus, DriftType
from app.models.project import Project
from app.models.workspace import Workspace
from app.services import drift_service


async def _seed_project(db: AsyncSession) -> Project:
    ws = Workspace(name="ws", slug=f"ws-{uuid.uuid4().hex[:8]}")
    db.add(ws)
    await db.flush()
    proj = Project(workspace_id=ws.id, name="p")
    db.add(proj)
    await db.commit()
    await db.refresh(proj)
    return proj


async def _open_drifts(db, proj, type_):
    rows = (await db.execute(
        select(Drift).where(
            Drift.project_id == proj.id, Drift.type == type_,
            Drift.status == DriftStatus.OPEN,
        )
    )).scalars().all()
    return rows


async def test_reconcile_opens_then_autoresolves(async_session: AsyncSession):
    proj = await _seed_project(async_session)
    await drift_service.reconcile(
        async_session, project_id=proj.id, type_=DriftType.STATUS_CONTRADICTION,
        current=[drift_service.DriftItem(
            dedup_key="feat/x:task-007", branch="feat/x", external_id="task-007",
            detail="PLAN DONE인데 handoff 미완", commit_sha="a" * 40,
        )],
    )
    await async_session.commit()
    assert len(await _open_drifts(async_session, proj, DriftType.STATUS_CONTRADICTION)) == 1

    await drift_service.reconcile(
        async_session, project_id=proj.id, type_=DriftType.STATUS_CONTRADICTION,
        current=[],
    )
    await async_session.commit()
    assert len(await _open_drifts(async_session, proj, DriftType.STATUS_CONTRADICTION)) == 0


async def test_reconcile_idempotent_no_duplicate(async_session: AsyncSession):
    proj = await _seed_project(async_session)
    item = drift_service.DriftItem(
        dedup_key="feat/x", branch="feat/x", external_id=None,
        detail="결정 미승격: task-002", commit_sha="b" * 40,
    )
    for _ in range(3):
        await drift_service.reconcile(
            async_session, project_id=proj.id,
            type_=DriftType.DECISION_NOT_PROMOTED, current=[item],
        )
        await async_session.commit()
    rows = (await async_session.execute(
        select(Drift).where(Drift.project_id == proj.id,
                            Drift.type == DriftType.DECISION_NOT_PROMOTED)
    )).scalars().all()
    assert len(rows) == 1
    assert rows[0].status == DriftStatus.OPEN


async def test_reconcile_ignored_not_touched(async_session: AsyncSession):
    proj = await _seed_project(async_session)
    item = drift_service.DriftItem(
        dedup_key="feat/x:task-007", branch="feat/x", external_id="task-007",
        detail="모순", commit_sha="a" * 40,
    )
    await drift_service.reconcile(
        async_session, project_id=proj.id,
        type_=DriftType.STATUS_CONTRADICTION, current=[item],
    )
    await async_session.commit()
    # 사용자가 IGNORED 처리
    d = (await _open_drifts(async_session, proj, DriftType.STATUS_CONTRADICTION))[0]
    d.status = DriftStatus.IGNORED
    await async_session.commit()
    # 같은 위반 재감지 — IGNORED 유지, OPEN 으로 안 돌아감
    await drift_service.reconcile(
        async_session, project_id=proj.id,
        type_=DriftType.STATUS_CONTRADICTION, current=[item],
    )
    await async_session.commit()
    rows = (await async_session.execute(
        select(Drift).where(Drift.project_id == proj.id,
                            Drift.type == DriftType.STATUS_CONTRADICTION)
    )).scalars().all()
    assert len(rows) == 1
    assert rows[0].status == DriftStatus.IGNORED
