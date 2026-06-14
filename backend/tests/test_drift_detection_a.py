"""감지 A (결정 미승격) — handoff ### 결정 마커 + DECISIONS.md diff.

설계서: 2026-06-14-decision-truth-loop-design.md §5.3
fetch_file / fetch_compare 주입으로 GitHub 호출 없이 검증.
"""

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.drift import Drift, DriftStatus, DriftType
from app.models.project import Project
from app.models.workspace import Workspace
from app.services import drift_service

HANDOFF_UNPROMOTED = """# Handoff: feat/x — @alice

## 2026-06-14
- [x] task-001

### 결정
- [task-002] 캐시 TTL 5→15분 — 부하 감소
"""

HANDOFF_PROMOTED = """# Handoff: feat/x — @alice

## 2026-06-14
- [x] task-001

### 결정
- [task-002] 캐시 TTL 5→15분 — 부하 감소 → DECISIONS
"""

HANDOFF_NO_DECISIONS = """# Handoff: feat/x — @alice

## 2026-06-14
- [x] task-001
"""


async def _seed(db: AsyncSession) -> Project:
    ws = Workspace(name="ws", slug=f"ws-{uuid.uuid4().hex[:8]}")
    db.add(ws)
    await db.flush()
    proj = Project(workspace_id=ws.id, name="p",
                   git_repo_url="https://github.com/o/r")
    db.add(proj)
    await db.commit()
    await db.refresh(proj)
    return proj


async def _open_a(db, proj):
    return (await db.execute(
        select(Drift).where(
            Drift.project_id == proj.id,
            Drift.type == DriftType.DECISION_NOT_PROMOTED,
            Drift.status == DriftStatus.OPEN,
        )
    )).scalars().all()


async def test_detect_a_unpromoted_opens_drift(async_session: AsyncSession):
    proj = await _seed(async_session)

    async def fake_fetch_file(url, pat, sha, path):
        return HANDOFF_UNPROMOTED

    async def fake_fetch_compare(url, pat, base, head):
        return ["backend/x.py"]

    await drift_service.detect_unpromoted_decisions(
        async_session, project=proj, branch="feat/x",
        head_sha="a" * 40, base_sha="b" * 40,
        fetch_file=fake_fetch_file, fetch_compare=fake_fetch_compare,
    )
    await async_session.commit()
    rows = await _open_a(async_session, proj)
    assert len(rows) == 1
    assert "task-002" in rows[0].detail


async def test_detect_a_promoted_and_decisions_changed_no_drift(async_session: AsyncSession):
    proj = await _seed(async_session)

    async def fake_fetch_file(url, pat, sha, path):
        return HANDOFF_PROMOTED

    async def fake_fetch_compare(url, pat, base, head):
        return ["DECISIONS.md"]

    await drift_service.detect_unpromoted_decisions(
        async_session, project=proj, branch="feat/x",
        head_sha="a" * 40, base_sha="b" * 40,
        fetch_file=fake_fetch_file, fetch_compare=fake_fetch_compare,
    )
    await async_session.commit()
    assert len(await _open_a(async_session, proj)) == 0


async def test_detect_a_promoted_but_decisions_untouched_opens_drift(async_session: AsyncSession):
    proj = await _seed(async_session)

    async def fake_fetch_file(url, pat, sha, path):
        return HANDOFF_PROMOTED

    async def fake_fetch_compare(url, pat, base, head):
        return ["backend/x.py"]  # DECISIONS.md 안 바뀜

    await drift_service.detect_unpromoted_decisions(
        async_session, project=proj, branch="feat/x",
        head_sha="a" * 40, base_sha="b" * 40,
        fetch_file=fake_fetch_file, fetch_compare=fake_fetch_compare,
    )
    await async_session.commit()
    rows = await _open_a(async_session, proj)
    assert len(rows) == 1
    assert "DECISIONS.md 변경 없음" in rows[0].detail


async def test_detect_a_no_decisions_no_drift(async_session: AsyncSession):
    proj = await _seed(async_session)

    async def fake_fetch_file(url, pat, sha, path):
        return HANDOFF_NO_DECISIONS

    async def fake_fetch_compare(url, pat, base, head):
        return []

    await drift_service.detect_unpromoted_decisions(
        async_session, project=proj, branch="feat/x",
        head_sha="a" * 40, base_sha="b" * 40,
        fetch_file=fake_fetch_file, fetch_compare=fake_fetch_compare,
    )
    await async_session.commit()
    assert len(await _open_a(async_session, proj)) == 0
