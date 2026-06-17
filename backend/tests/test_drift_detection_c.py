"""감지 C (태스크 미준비) — 브랜치 task 에 코드 들어왔는데 준비 산출물 누락.

설계서: 2026-06-17-pslog-workflow-design.md §7, §9.1-9.2
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

PLAN_DEEP = "## 태스크\n- [ ] [task-007] (deep) 결제 재시도 — @me — `x.py`\n"
PLAN_LIGHT = "## 태스크\n- [ ] [task-008] 오타 — @me\n"


async def _seed_project(db: AsyncSession) -> Project:
    ws = Workspace(name="ws", slug=f"ws-{uuid.uuid4().hex[:8]}")
    db.add(ws)
    await db.flush()
    proj = Project(workspace_id=ws.id, name="p",
                   git_repo_url="https://github.com/o/r")
    db.add(proj)
    await db.commit()
    await db.refresh(proj)
    return proj


def _ff(files: dict[str, str]):
    async def fetch_file(url, pat, sha, path):
        return files.get(path)
    return fetch_file


def _fc(changed: list[str]):
    async def fetch_compare(url, pat, base, head):
        return changed
    return fetch_compare


async def _open_c(db, project):
    rows = (await db.execute(
        select(Drift).where(
            Drift.project_id == project.id,
            Drift.type == DriftType.TASK_NOT_PREPARED,
        )
    )).scalars().all()
    return [r for r in rows if r.status == DriftStatus.OPEN]


@pytest.mark.asyncio
async def test_deep_missing_plan_opens_drift(async_session: AsyncSession):
    proj = await _seed_project(async_session)
    files = {proj.plan_path: PLAN_DEEP, "docs/tasks/task-007/spec.md": "# spec"}  # plan.md 없음
    newly = await drift_service.detect_task_not_prepared(
        async_session, project=proj, branch="feat/task-007-pay",
        head_sha="a" * 40, base_sha="b" * 40,
        fetch_file=_ff(files), fetch_compare=_fc(["backend/x.py"]),
    )
    await async_session.commit()
    assert len(newly) == 1
    assert "task-007" in newly[0].detail
    assert len(await _open_c(async_session, proj)) == 1


@pytest.mark.asyncio
async def test_deep_both_present_no_drift(async_session: AsyncSession):
    proj = await _seed_project(async_session)
    files = {proj.plan_path: PLAN_DEEP, "docs/tasks/task-007/spec.md": "# s", "docs/tasks/task-007/plan.md": "# p"}
    newly = await drift_service.detect_task_not_prepared(
        async_session, project=proj, branch="feat/task-007-pay",
        head_sha="a" * 40, base_sha="b" * 40, fetch_file=_ff(files), fetch_compare=_fc(["backend/x.py"]),
    )
    await async_session.commit()
    assert newly == []
    assert await _open_c(async_session, proj) == []


@pytest.mark.asyncio
async def test_light_missing_brief_opens_drift(async_session: AsyncSession):
    proj = await _seed_project(async_session)
    files = {proj.plan_path: PLAN_LIGHT}  # brief 없음
    newly = await drift_service.detect_task_not_prepared(
        async_session, project=proj, branch="feat/task-008-typo",
        head_sha="a" * 40, base_sha="b" * 40, fetch_file=_ff(files), fetch_compare=_fc(["README.md", "src/a.py"]),
    )
    await async_session.commit()
    assert len(newly) == 1


@pytest.mark.asyncio
async def test_no_code_change_no_drift(async_session: AsyncSession):
    proj = await _seed_project(async_session)
    files = {proj.plan_path: PLAN_LIGHT}
    # 변경 파일이 tasks_dir 안에만 있음 → 코드 안 들어옴
    newly = await drift_service.detect_task_not_prepared(
        async_session, project=proj, branch="feat/task-008-typo",
        head_sha="a" * 40, base_sha="b" * 40,
        fetch_file=_ff(files), fetch_compare=_fc(["docs/tasks/task-008/brief.md"]),
    )
    await async_session.commit()
    assert newly == []


@pytest.mark.asyncio
async def test_non_task_branch_no_drift(async_session: AsyncSession):
    proj = await _seed_project(async_session)
    files = {proj.plan_path: PLAN_LIGHT}
    newly = await drift_service.detect_task_not_prepared(
        async_session, project=proj, branch="chore/cleanup",
        head_sha="a" * 40, base_sha="b" * 40, fetch_file=_ff(files), fetch_compare=_fc(["src/a.py"]),
    )
    await async_session.commit()
    assert newly == []


@pytest.mark.asyncio
async def test_autoresolve_when_docs_added(async_session: AsyncSession):
    proj = await _seed_project(async_session)
    # 1차: brief 없음 → OPEN
    await drift_service.detect_task_not_prepared(
        async_session, project=proj, branch="feat/task-008-typo", head_sha="a" * 40, base_sha="b" * 40,
        fetch_file=_ff({proj.plan_path: PLAN_LIGHT}), fetch_compare=_fc(["src/a.py"]),
    )
    await async_session.commit()
    assert len(await _open_c(async_session, proj)) == 1
    # 2차: brief 생김 → 자동 RESOLVED
    await drift_service.detect_task_not_prepared(
        async_session, project=proj, branch="feat/task-008-typo", head_sha="c" * 40, base_sha="a" * 40,
        fetch_file=_ff({proj.plan_path: PLAN_LIGHT, "docs/tasks/task-008/brief.md": "# brief"}),
        fetch_compare=_fc(["src/b.py"]),
    )
    await async_session.commit()
    assert await _open_c(async_session, proj) == []
