"""감지 B (상태 모순) — 저장된 Handoff.parsed_tasks vs Task.status.

설계서: 2026-06-14-decision-truth-loop-design.md §5.3
"""

import uuid
from datetime import datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.drift import Drift, DriftStatus, DriftType
from app.models.handoff import Handoff
from app.models.project import Project
from app.models.task import Task, TaskSource, TaskStatus
from app.models.workspace import Workspace
from app.services import drift_service


async def _seed(db: AsyncSession) -> Project:
    ws = Workspace(name="ws", slug=f"ws-{uuid.uuid4().hex[:8]}")
    db.add(ws)
    await db.flush()
    proj = Project(workspace_id=ws.id, name="p")
    db.add(proj)
    await db.flush()
    return proj


async def _open_b(db, proj):
    return (await db.execute(
        select(Drift).where(
            Drift.project_id == proj.id,
            Drift.type == DriftType.STATUS_CONTRADICTION,
            Drift.status == DriftStatus.OPEN,
        )
    )).scalars().all()


async def test_detect_b_contradiction(async_session: AsyncSession):
    proj = await _seed(async_session)
    async_session.add(Task(
        project_id=proj.id, title="t7", source=TaskSource.SYNCED_FROM_PLAN,
        external_id="task-007", status=TaskStatus.DONE,
    ))
    async_session.add(Handoff(
        project_id=proj.id, branch="feat/x", author_git_login="alice",
        commit_sha="a" * 40, pushed_at=datetime.utcnow(), raw_content="...",
        parsed_tasks=[{"external_id": "task-007", "checked": False, "extra": ""}],
        free_notes={},
    ))
    await async_session.commit()

    await drift_service.detect_status_contradictions(
        async_session, project_id=proj.id, branch="feat/x", commit_sha="a" * 40,
    )
    await async_session.commit()

    rows = await _open_b(async_session, proj)
    assert len(rows) == 1
    assert rows[0].external_id == "task-007"


async def test_detect_b_no_contradiction(async_session: AsyncSession):
    proj = await _seed(async_session)
    async_session.add(Task(
        project_id=proj.id, title="t7", source=TaskSource.SYNCED_FROM_PLAN,
        external_id="task-007", status=TaskStatus.DONE,
    ))
    async_session.add(Handoff(
        project_id=proj.id, branch="feat/x", author_git_login="alice",
        commit_sha="a" * 40, pushed_at=datetime.utcnow(), raw_content="...",
        parsed_tasks=[{"external_id": "task-007", "checked": True, "extra": ""}],
        free_notes={},
    ))
    await async_session.commit()
    await drift_service.detect_status_contradictions(
        async_session, project_id=proj.id, branch="feat/x", commit_sha="a" * 40,
    )
    await async_session.commit()
    assert len(await _open_b(async_session, proj)) == 0
