# Phase 5 Follow-up B2 — UI Closure + Discord Sync-Failure Alert Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Phase 5b UI 잔여 2건 (TaskCard ⚠️ handoff missing 배지, GitEventList 모달 + reprocess 호출 site) + minimal Discord sync-failure 알림 1종을 한 PR 로 닫음. Phase 6 (체크/handoff/롤백 알림 3종 템플릿 + cooldown) 진입 전 사용자 가시성의 minimal viable layer.

**Architecture:** backend 는 신규 endpoint 1개 (`GET /git-events`) + TaskResponse 1 필드 (`handoff_missing`, `task_service` 가 in-memory annotate — 마이그레이션 X) + sync_service except 분기에 Discord alert ~10줄. frontend 는 기존 modal 스타일 (HandoffHistoryModal 패턴 — 검정 테두리 + 빨강 그림자, shadcn Dialog 아님) 그대로 매칭. handoff missing 판정은 backend SQL 1건 (`SELECT (project_id, commit_sha) FROM handoffs WHERE (project_id, commit_sha) IN (...)`) 으로 N+1 회피. Discord 알림은 기존 `discord_service.send_webhook` primitive 재사용, `Project.discord_webhook_url` 그대로 사용 (스키마 변경 X).

**Tech Stack:** FastAPI 0.115, SQLAlchemy 2.0 async, Pydantic v2, React 19 + TypeScript 5, TanStack Query, Tailwind, bun. backend tests: pytest + testcontainers PostgreSQL.

**선행 조건:**
- pslog `main` = `cd53696` (B1 PR #13 머지 직후), alembic head = `a1b2c3d4e5f6`
- backend tests baseline = **175 passing**
- frontend `bun run build` + `bun run lint` clean
- Python 3.12 venv (`backend/venv` symlink), `.env` 의 `pslog_FERNET_KEY` 존재
- spec: `docs/superpowers/specs/2026-05-01-phase-5-followup-b2-design.md`

**중요한 계약:**

- **`Task.handoff_missing`** 의미: `source = SYNCED_FROM_PLAN AND last_commit_sha IS NOT NULL AND archived_at IS NULL AND NOT EXISTS (SELECT 1 FROM handoffs WHERE project_id = t.project_id AND commit_sha = t.last_commit_sha)`. MANUAL / NULL last_commit_sha / archived = 항상 `false`.
- **Annotation 위치**: `task_service.get_task` (single) + `get_project_tasks` (multi) + `get_week_tasks` (cross-project multi). `create_task` / `update_task` 는 끝에서 `get_task(db, task.id)` 재호출하므로 자동 전파. 비-mapped Python 인스턴스 attribute (`task.handoff_missing = bool`) 로 쓰고 Pydantic `from_attributes=True` 가 읽음.
- **`GET /api/v1/projects/{id}/git-events`**: 멤버 누구나 (read), `failed_only=true` (기본), `limit` clamp 1~200. 응답은 `commits` JSON 등 큰 필드 제외 한 작은 summary.
- **Discord alert 트리거**: `process_event` 의 except 분기 끝, `event.error` commit 직후. `project.discord_webhook_url` set 일 때만. 알림 호출 자체는 `try/except` swallow — 메인 처리에 영향 없음. cooldown 없음 (event 당 except 1회 = 자연 1알림).
- **에러 정책**:
  - GET /git-events 비-멤버 → 404 (handoffs endpoint 패턴)
  - Discord alert 실패 → silent (`logger.exception` 후 swallow)
  - reprocess 토스트: 409 → "처리 중 — 잠시 후 다시 시도", 400 → "이미 성공", 기타 → `error.response?.data?.detail || error.message`
- **마이그레이션 / 모델 변경**: 없음. 모든 기존 컬럼 사용.

---

## File Structure

**수정 파일 (backend 소스):**
- `backend/app/schemas/task.py` — `TaskResponse` 에 `handoff_missing: bool = False` 추가
- `backend/app/services/task_service.py` — `_annotate_handoff_missing(db, tasks)` 헬퍼 + 3개 조회 함수 (`get_task`, `get_project_tasks`, `get_week_tasks`) 가 호출
- `backend/app/schemas/git_settings.py` — `GitEventSummary` Pydantic 신규 추가 (같은 파일 — 파일 수 늘리지 않음, git 도메인 묶음)
- `backend/app/api/v1/endpoints/git_settings.py` — `GET /git-events` 핸들러 추가
- `backend/app/services/sync_service.py` — except 분기에 Discord alert 호출 ~10줄

**수정 파일 (backend 테스트):**
- `backend/tests/test_task_service.py` (신규 파일 — 기존 회귀에 task_service 단독 파일 없음 → 작은 신규 파일) 또는 `backend/tests/test_sync_service.py` 의 끝에 (체크 후 결정)
- `backend/tests/test_git_settings_endpoint.py` — `GET /git-events` 회귀 3건
- `backend/tests/test_sync_service.py` — Discord alert 회귀 3건

**수정 파일 (frontend 소스):**
- `frontend/src/types/task.ts` — `Task.handoff_missing: boolean`
- `frontend/src/types/git.ts` — `GitEventSummary` 신규
- `frontend/src/services/api.ts` — `git.listGitEvents` method
- `frontend/src/hooks/useGithubSettings.ts` — `useFailedGitEvents` 훅 신규 + `useReprocessEvent` invalidate 추가
- `frontend/src/components/board/TaskCard.tsx` — ⚠️ 배지 1줄
- `frontend/src/components/sidebar/ProjectItem.tsx` — `useFailedGitEvents` 호출 + 메뉴 항목 + 모달 마운트

**신규 파일 (frontend):**
- `frontend/src/components/sidebar/GitEventListModal.tsx` — HandoffHistoryModal 패턴 매칭

**미변경:** alembic 마이그레이션 (없음), `Project` 모델, frontend 단위 테스트 인프라 (Phase 5b 그대로 미도입).

---

### Task 1: Backend — `TaskResponse.handoff_missing` 필드 + `_annotate_handoff_missing` 헬퍼

**Files:**
- Modify: `backend/app/schemas/task.py:42-65` (TaskResponse 에 1 필드)
- Modify: `backend/app/services/task_service.py:1-90` (헬퍼 + 3 조회 함수에 호출)
- Test (신규 파일): `backend/tests/test_task_service.py`

- [ ] **Step 1: Baseline 회귀 — 175 tests pass 확인**

```bash
cd /Users/arden/Documents/ardensdevspace/pslog/.worktrees/phase-5-followup-b2/backend
source venv/bin/activate
pytest -q 2>&1 | tail -3
```

Expected: `175 passed`. 베이스라인 다르면 stop.

- [ ] **Step 2: 기존 테스트 파일 확인 — task_service 단독 파일 존재 여부**

```bash
ls tests/test_task_service.py 2>/dev/null && echo "EXISTS" || echo "NEW FILE"
```

신규 파일로 작성 (없을 가능성 큼 — service 별 테스트 파일 패턴).

- [ ] **Step 3: 신규 failing test 작성**

`backend/tests/test_task_service.py` (신규):

```python
"""task_service handoff_missing annotation 회귀.

설계서: 2026-05-01-phase-5-followup-b2-design.md §2.1
"""

import uuid
from datetime import datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.handoff import Handoff
from app.models.project import Project
from app.models.task import Task, TaskSource, TaskStatus
from app.models.workspace import Workspace
from app.services import task_service
from app.schemas.task import TaskFilters


async def _seed_project(db: AsyncSession) -> Project:
    ws = Workspace(name="ws", slug=f"ws-{uuid.uuid4().hex[:8]}")
    db.add(ws)
    await db.flush()
    proj = Project(workspace_id=ws.id, name="p")
    db.add(proj)
    await db.commit()
    await db.refresh(proj)
    return proj


async def _seed_task(
    db: AsyncSession, project: Project, *,
    source: TaskSource = TaskSource.MANUAL,
    last_commit_sha: str | None = None,
    archived_at: datetime | None = None,
    external_id: str | None = None,
) -> Task:
    t = Task(
        project_id=project.id,
        title="t",
        status=TaskStatus.TODO,
        source=source,
        last_commit_sha=last_commit_sha,
        archived_at=archived_at,
        external_id=external_id,
    )
    db.add(t)
    await db.commit()
    await db.refresh(t)
    return t


async def _seed_handoff(db: AsyncSession, project: Project, *, commit_sha: str) -> Handoff:
    h = Handoff(
        project_id=project.id,
        branch="main",
        author_git_login="alice",
        commit_sha=commit_sha,
        pushed_at=datetime.utcnow(),
        parsed_tasks=[],
        free_notes={},
        raw_content="x",
    )
    db.add(h)
    await db.commit()
    await db.refresh(h)
    return h


async def test_handoff_missing_true_when_synced_task_has_no_handoff(
    async_session: AsyncSession,
):
    """SYNCED + last_commit_sha set + handoff 없음 → handoff_missing = true."""
    proj = await _seed_project(async_session)
    await _seed_task(
        async_session, proj,
        source=TaskSource.SYNCED_FROM_PLAN,
        last_commit_sha="a" * 40,
        external_id="task-001",
    )

    tasks = await task_service.get_project_tasks(
        async_session, proj.id, uuid.uuid4(), filters=None,
    )
    assert len(tasks) == 1
    assert tasks[0].handoff_missing is True


async def test_handoff_missing_false_when_handoff_exists(
    async_session: AsyncSession,
):
    """SYNCED + last_commit_sha set + handoff 존재 → handoff_missing = false."""
    proj = await _seed_project(async_session)
    sha = "b" * 40
    await _seed_task(
        async_session, proj,
        source=TaskSource.SYNCED_FROM_PLAN,
        last_commit_sha=sha,
        external_id="task-002",
    )
    await _seed_handoff(async_session, proj, commit_sha=sha)

    tasks = await task_service.get_project_tasks(
        async_session, proj.id, uuid.uuid4(), filters=None,
    )
    assert len(tasks) == 1
    assert tasks[0].handoff_missing is False


async def test_handoff_missing_false_for_excluded_cases(
    async_session: AsyncSession,
):
    """MANUAL / last_commit_sha NULL / archived task 는 항상 handoff_missing = false."""
    proj = await _seed_project(async_session)
    # case 1: MANUAL task with last_commit_sha set (이론상 안 발생하지만 가드)
    await _seed_task(
        async_session, proj,
        source=TaskSource.MANUAL,
        last_commit_sha="c" * 40,
    )
    # case 2: SYNCED 인데 last_commit_sha NULL
    await _seed_task(
        async_session, proj,
        source=TaskSource.SYNCED_FROM_PLAN,
        last_commit_sha=None,
        external_id="task-003",
    )
    # case 3: SYNCED 인데 archived
    await _seed_task(
        async_session, proj,
        source=TaskSource.SYNCED_FROM_PLAN,
        last_commit_sha="d" * 40,
        archived_at=datetime.utcnow(),
        external_id="task-004",
    )

    tasks = await task_service.get_project_tasks(
        async_session, proj.id, uuid.uuid4(), filters=None,
    )
    assert len(tasks) == 3
    for t in tasks:
        assert t.handoff_missing is False, f"task {t.title} should have handoff_missing=False"
```

- [ ] **Step 4: Verify failure**

```bash
pytest tests/test_task_service.py -v 2>&1 | tail -15
```

Expected: **3 FAIL** with `AttributeError: 'Task' object has no attribute 'handoff_missing'` 또는 `assert False is True` 등. fix 없이 fail 확인.

- [ ] **Step 5: TaskResponse 에 필드 추가**

`backend/app/schemas/task.py` 의 `TaskResponse` (line ~42, 기존 Phase 5b 추가 필드 4건 다음) 에 1 필드:

```python
class TaskResponse(BaseModel):
    id: UUID
    project_id: UUID
    title: str
    description: str | None
    status: TaskStatus
    due_date: date | None
    assignee_id: UUID | None
    reporter_id: UUID | None
    created_at: datetime
    updated_at: datetime
    assignee: UserBrief | None = None
    reporter: UserBrief | None = None
    # Phase 5b — frontend 가 source 배지 / git 연동 정보 표시 (Phase 1 모델 누락분 노출)
    source: TaskSource = TaskSource.MANUAL
    external_id: str | None = None
    last_commit_sha: str | None = None
    archived_at: datetime | None = None
    # Phase 5 follow-up B2 — handoff missing 배지 (TaskCard ⚠️)
    handoff_missing: bool = False

    model_config = {"from_attributes": True}
```

- [ ] **Step 6: `_annotate_handoff_missing` 헬퍼 + 3 조회 함수 호출 추가**

`backend/app/services/task_service.py` 변경. 파일 상단 imports 에 `Handoff` 추가:

```python
from app.models.handoff import Handoff
from app.models.task import Task, TaskSource
```

(`TaskSource` 가 기존에 없으면 추가)

함수 `_annotate_handoff_missing` 를 `_task_query()` 정의 직후에 추가:

```python
async def _annotate_handoff_missing(db: AsyncSession, tasks: list[Task]) -> None:
    """Task 인스턴스에 .handoff_missing (bool) 비-mapped attribute 를 붙임.

    설계서: 2026-05-01-phase-5-followup-b2-design.md §2.1
    조건: source=SYNCED + last_commit_sha not null + archived_at null + 매칭 handoff 없음.
    cross-project 안전 — get_week_tasks 처럼 여러 프로젝트 task 한 list 도 처리.
    """
    candidates = [
        (t.project_id, t.last_commit_sha)
        for t in tasks
        if t.source == TaskSource.SYNCED_FROM_PLAN
        and t.last_commit_sha is not None
        and t.archived_at is None
    ]
    if not candidates:
        for t in tasks:
            t.handoff_missing = False
        return

    project_ids = list({c[0] for c in candidates})
    shas = list({c[1] for c in candidates})
    result = await db.execute(
        select(Handoff.project_id, Handoff.commit_sha)
        .where(
            Handoff.project_id.in_(project_ids),
            Handoff.commit_sha.in_(shas),
        )
    )
    existing_pairs = {(row[0], row[1]) for row in result.all()}

    for t in tasks:
        t.handoff_missing = (
            t.source == TaskSource.SYNCED_FROM_PLAN
            and t.last_commit_sha is not None
            and t.archived_at is None
            and (t.project_id, t.last_commit_sha) not in existing_pairs
        )
```

`get_task` 변경:

```python
async def get_task(db: AsyncSession, task_id: UUID) -> Task | None:
    result = await db.execute(_task_query().where(Task.id == task_id))
    task = result.scalar_one_or_none()
    if task is not None:
        await _annotate_handoff_missing(db, [task])
    return task
```

`get_project_tasks` 변경 — 기존 함수 끝부분 (return 직전):

```python
async def get_project_tasks(
    db: AsyncSession,
    project_id: UUID,
    user_id: UUID,
    filters: TaskFilters | None = None,
) -> list[Task]:
    """프로젝트별 태스크 목록 (필터 지원)"""
    stmt = _task_query().where(Task.project_id == project_id)

    if filters:
        if filters.status:
            stmt = stmt.where(Task.status == filters.status)
        if filters.assignee_id:
            stmt = stmt.where(Task.assignee_id == filters.assignee_id)
        if filters.mine_only:
            stmt = stmt.where(Task.assignee_id == user_id)

    result = await db.execute(stmt)
    tasks = list(result.scalars().all())
    await _annotate_handoff_missing(db, tasks)
    return tasks
```

`get_week_tasks` 변경 — 기존 함수 끝부분:

```python
async def get_week_tasks(
    db: AsyncSession, user_id: UUID, week_start: date
) -> list[Task]:
    week_end = week_start + timedelta(days=7)
    result = await db.execute(
        _task_query()
        .where(Task.assignee_id == user_id)
        .where(
            or_(
                Task.due_date.is_(None),
                (Task.due_date >= week_start) & (Task.due_date < week_end),
            )
        )
        .order_by(Task.due_date.is_(None), Task.due_date)
    )
    tasks = list(result.scalars().all())
    await _annotate_handoff_missing(db, tasks)
    return tasks
```

- [ ] **Step 7: Verify pass**

```bash
pytest tests/test_task_service.py -v 2>&1 | tail -10
```

Expected: **3 passed**.

- [ ] **Step 8: 회귀 — 기존 task / sync_service / endpoint 모든 테스트**

```bash
pytest -q 2>&1 | tail -5
```

Expected: `178 passed` (175 baseline + 3 new). 다른 회귀 없어야 함. 만약 기존 task endpoint 테스트가 깨지면 stop — `from_attributes=True` 가 비-mapped attribute 를 못 읽는 경우 `getattr(task, 'handoff_missing', False)` 폴백 필요할 수 있음.

- [ ] **Step 9: Commit**

```bash
git add backend/app/schemas/task.py backend/app/services/task_service.py backend/tests/test_task_service.py
git commit -m "$(cat <<'EOF'
feat(b2): TaskResponse.handoff_missing — task_service annotate

- _annotate_handoff_missing 헬퍼 — list[Task] 입력, (project_id, commit_sha) 단일 query 로 N+1 회피
- get_task / get_project_tasks / get_week_tasks 가 호출 (create/update 는 get_task 경유 자동 전파)
- TaskResponse.handoff_missing: bool = False (Pydantic from_attributes 가 비-mapped attribute 읽음)
- 회귀 3건: SYNCED+handoff 없음 / SYNCED+handoff 존재 / MANUAL·NULL·archived 제외 케이스

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Backend — `GET /git-events` endpoint + `GitEventSummary` schema

**Files:**
- Modify: `backend/app/schemas/git_settings.py` (GitEventSummary 신규 클래스)
- Modify: `backend/app/api/v1/endpoints/git_settings.py` (GET /git-events 핸들러)
- Modify: `backend/tests/test_git_settings_endpoint.py` (회귀 3건)

- [ ] **Step 1: Failing tests 작성**

`backend/tests/test_git_settings_endpoint.py` 끝에 추가 (이미 import 되어있는 GitPushEvent 활용):

```python
# ---------------------------------------------------------------------------
# B2: GET /git-events — failed events 조회
# ---------------------------------------------------------------------------


async def test_list_git_events_returns_only_failed(
    client_with_db, async_session: AsyncSession
):
    """failed_only=true (기본) 이면 processed_at NOT NULL AND error NOT NULL 만 반환."""
    user, proj = await _seed_user_project(async_session)
    now = datetime.utcnow()
    # success
    async_session.add(GitPushEvent(
        project_id=proj.id, branch="main", head_commit_sha="a" * 40,
        commits=[], commits_truncated=False, pusher="alice",
        received_at=now, processed_at=now, error=None,
    ))
    # in-flight (processed_at NULL)
    async_session.add(GitPushEvent(
        project_id=proj.id, branch="main", head_commit_sha="b" * 40,
        commits=[], commits_truncated=False, pusher="alice",
        received_at=now,
    ))
    # failed
    async_session.add(GitPushEvent(
        project_id=proj.id, branch="feature/x", head_commit_sha="c" * 40,
        commits=[], commits_truncated=False, pusher="bob",
        received_at=now, processed_at=now, error="MalformedHandoffError: bad",
    ))
    await async_session.commit()

    token = _auth_token(user)
    res = await client_with_db.get(
        f"/api/v1/projects/{proj.id}/git-events",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200
    items = res.json()
    assert len(items) == 1
    assert items[0]["head_commit_sha"] == "c" * 40
    assert items[0]["error"] == "MalformedHandoffError: bad"
    assert items[0]["branch"] == "feature/x"
    # commits / before_commit_sha 는 응답 미포함
    assert "commits" not in items[0]
    assert "before_commit_sha" not in items[0]


async def test_list_git_events_404_for_non_member(
    client_with_db, async_session: AsyncSession
):
    user, proj = await _seed_user_project(async_session)
    other = User(
        email=f"o-{uuid.uuid4().hex[:8]}@example.com",
        name="bob",
        password_hash="x",
    )
    async_session.add(other)
    await async_session.commit()
    await async_session.refresh(other)

    token = _auth_token(other)
    res = await client_with_db.get(
        f"/api/v1/projects/{proj.id}/git-events",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 404


async def test_list_git_events_limit_clamped_to_max(
    client_with_db, async_session: AsyncSession
):
    """limit > 200 도 200 으로 clamp — 422 안 남."""
    user, proj = await _seed_user_project(async_session)
    token = _auth_token(user)
    res = await client_with_db.get(
        f"/api/v1/projects/{proj.id}/git-events?limit=99999",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200
```

- [ ] **Step 2: Verify failure**

```bash
pytest tests/test_git_settings_endpoint.py -k "list_git_events" -v 2>&1 | tail -10
```

Expected: **3 FAIL** (404 — endpoint 미존재).

- [ ] **Step 3: `GitEventSummary` schema 추가**

`backend/app/schemas/git_settings.py` 끝에 추가:

```python
class GitEventSummary(BaseModel):
    """GET /git-events 응답 — failed event list 용 작은 summary.

    설계서: 2026-05-01-phase-5-followup-b2-design.md §2.3
    commits / before_commit_sha 등 큰 필드 제외 (UI 불필요).
    """
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    branch: str
    head_commit_sha: str
    pusher: str
    received_at: datetime
    processed_at: datetime | None
    error: str | None
```

- [ ] **Step 4: endpoint 핸들러 추가**

`backend/app/api/v1/endpoints/git_settings.py` 의 import 에 `GitEventSummary` 추가:

```python
from app.schemas.git_settings import (
    GitEventSummary,
    GitSettingsResponse,
    GitSettingsUpdate,
    HandoffSummary,
    ReprocessResponse,
    WebhookRegisterResponse,
)
```

`reprocess_git_event` 핸들러 위 (또는 적당한 위치) 에 신규 핸들러 추가:

```python
@router.get(
    "/{project_id}/git-events",
    response_model=list[GitEventSummary],
)
async def list_git_events(
    project_id: UUID,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
    failed_only: bool = True,
    limit: int = 50,
):
    """프로젝트의 git push event list — v1 은 failed only 가 의미 있는 case.

    설계서: 2026-05-01-phase-5-followup-b2-design.md §2.3
    """
    project = await project_service.get_project(db, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    role = await get_effective_role(db, user.id, project_id)
    if role is None:
        raise HTTPException(status_code=404, detail="Project not found")

    limit = max(1, min(limit, 200))

    stmt = (
        select(GitPushEvent)
        .where(GitPushEvent.project_id == project_id)
        .order_by(GitPushEvent.received_at.desc(), GitPushEvent.id.desc())
        .limit(limit)
    )
    if failed_only:
        stmt = stmt.where(
            GitPushEvent.processed_at.is_not(None),
            GitPushEvent.error.is_not(None),
        )

    rows = (await db.execute(stmt)).scalars().all()
    return [
        GitEventSummary(
            id=e.id,
            branch=e.branch,
            head_commit_sha=e.head_commit_sha,
            pusher=e.pusher,
            received_at=e.received_at,
            processed_at=e.processed_at,
            error=e.error,
        )
        for e in rows
    ]
```

- [ ] **Step 5: Verify pass**

```bash
pytest tests/test_git_settings_endpoint.py -k "list_git_events" -v 2>&1 | tail -10
```

Expected: **3 passed**.

- [ ] **Step 6: 회귀**

```bash
pytest -q 2>&1 | tail -3
```

Expected: `181 passed` (175 baseline + 3 Task 1 + 3 Task 2).

- [ ] **Step 7: Commit**

```bash
git add backend/app/schemas/git_settings.py backend/app/api/v1/endpoints/git_settings.py backend/tests/test_git_settings_endpoint.py
git commit -m "$(cat <<'EOF'
feat(b2): GET /api/v1/projects/{id}/git-events — failed events list

- GitEventSummary Pydantic (작은 응답, commits/before_commit_sha 제외)
- failed_only=true (기본) — processed_at NOT NULL AND error NOT NULL
- limit clamp 1~200 (handoffs endpoint 패턴)
- 멤버 누구나 read, 비-멤버 404
- 회귀 3건: failed only filter / 비-멤버 404 / limit clamp

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Backend — Discord sync-failure 알림 (`process_event` except)

**Files:**
- Modify: `backend/app/services/sync_service.py` (except 분기 끝에 ~10줄)
- Modify: `backend/tests/test_sync_service.py` (회귀 3건)

- [ ] **Step 1: Failing tests 작성**

`backend/tests/test_sync_service.py` 끝에 추가 (Task 3B race test 다음):

```python
# ---------------------------------------------------------------------------
# B2: Discord sync-failure 알림 — process_event except 분기
# ---------------------------------------------------------------------------


async def test_discord_alert_called_on_failure_with_webhook_url(
    async_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
):
    """failure path + Project.discord_webhook_url set → discord_service.send_webhook 1회 호출."""
    proj = await _seed_project(async_session)
    proj.discord_webhook_url = "https://discord.com/api/webhooks/1/abc"
    await async_session.commit()
    await async_session.refresh(proj)

    head = "f" * 40
    event = await _seed_event(
        async_session, proj, head_sha=head,
        commits=[{"id": head, "modified": ["PLAN.md"], "added": []}],
    )

    async def boom_fetch(repo, pat, sha, path):
        raise RuntimeError("github 502")

    async def fake_compare(repo, pat, base, head_sha):  # noqa: ARG001
        return ["PLAN.md"]

    sent: list[tuple[str, str]] = []

    async def fake_send(content, webhook_url):
        sent.append((content, webhook_url))

    import app.services.discord_service as discord_mod
    monkeypatch.setattr(discord_mod, "send_webhook", fake_send)

    await process_event(
        async_session, event, fetch_file=boom_fetch, fetch_compare=fake_compare,
    )

    assert len(sent) == 1
    content, url = sent[0]
    assert url == "https://discord.com/api/webhooks/1/abc"
    assert "pslog sync 실패" in content
    assert proj.name in content
    assert event.branch in content
    assert head[:7] in content
    assert "RuntimeError" in content


async def test_discord_alert_skipped_when_webhook_url_missing(
    async_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
):
    """failure path + Project.discord_webhook_url IS NULL → send_webhook 호출 안 함."""
    proj = await _seed_project(async_session)
    # discord_webhook_url 미설정 (default None)
    head = "e" * 40
    event = await _seed_event(
        async_session, proj, head_sha=head,
        commits=[{"id": head, "modified": ["PLAN.md"], "added": []}],
    )

    async def boom_fetch(repo, pat, sha, path):
        raise RuntimeError("github 502")

    async def fake_compare(repo, pat, base, head_sha):  # noqa: ARG001
        return ["PLAN.md"]

    sent: list = []

    async def fake_send(content, webhook_url):
        sent.append((content, webhook_url))

    import app.services.discord_service as discord_mod
    monkeypatch.setattr(discord_mod, "send_webhook", fake_send)

    await process_event(
        async_session, event, fetch_file=boom_fetch, fetch_compare=fake_compare,
    )

    assert len(sent) == 0
    await async_session.refresh(event)
    assert event.error is not None  # failure recorded


async def test_discord_alert_not_called_on_success_path(
    async_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
):
    """success path + discord_webhook_url set → send_webhook 호출 안 함 (실패 시에만 알림)."""
    proj = await _seed_project(async_session)
    proj.discord_webhook_url = "https://discord.com/api/webhooks/1/abc"
    await async_session.commit()

    head = "d" * 40
    event = await _seed_event(
        async_session, proj, head_sha=head,
        commits=[{"id": head, "modified": ["PLAN.md"], "added": []}],
    )

    async def fake_fetch_file(repo, pat, sha, path):
        return "## 태스크\n\n- [ ] [task-001] T — @alice"

    async def fake_compare(repo, pat, base, head_sha):  # noqa: ARG001
        return ["PLAN.md"]

    sent: list = []

    async def fake_send(content, webhook_url):
        sent.append((content, webhook_url))

    import app.services.discord_service as discord_mod
    monkeypatch.setattr(discord_mod, "send_webhook", fake_send)

    await process_event(
        async_session, event,
        fetch_file=fake_fetch_file, fetch_compare=fake_compare,
    )

    assert len(sent) == 0
    await async_session.refresh(event)
    assert event.error is None  # success
```

- [ ] **Step 2: Verify failure**

```bash
pytest tests/test_sync_service.py -k "discord_alert" -v 2>&1 | tail -10
```

Expected: **첫 두 테스트 FAIL** (`assert 0 == 1` — fix 없이 send_webhook 호출 안 함). 세 번째 (`not_called_on_success_path`) 는 PASS — 현재도 호출 안 함. 첫 두 fail 확인.

- [ ] **Step 3: Implement — except 분기 끝에 알림 호출 추가**

`backend/app/services/sync_service.py` 의 `process_event` 함수 except 분기. 기존 except 끝부분 (autoflush 복원 직후):

기존:
```python
    except Exception as exc:
        # I-2 fix: _process_inner 내부에서 예외 발생 시 세션이 poisoned 상태일 수 있음.
        # rollback → SQLAlchemy 가 pending/new 객체를 identity map 에서 자동 제거.
        # event 는 persistent 상태로 남음. autoflush=False 로 commit 전 autoflush 유발 방지.
        logger.exception("sync failed for event %s", event_id)
        try:
            await db.rollback()
        except Exception:
            pass
        db.sync_session.autoflush = False
        now = datetime.utcnow()
        error_msg = f"{type(exc).__name__}: {exc}"
        event.processed_at = now
        event.error = error_msg
        await db.commit()
        db.sync_session.autoflush = True
```

변경 (except 끝에 알림 호출 추가):

```python
    except Exception as exc:
        # I-2 fix: _process_inner 내부에서 예외 발생 시 세션이 poisoned 상태일 수 있음.
        # rollback → SQLAlchemy 가 pending/new 객체를 identity map 에서 자동 제거.
        # event 는 persistent 상태로 남음. autoflush=False 로 commit 전 autoflush 유발 방지.
        logger.exception("sync failed for event %s", event_id)
        try:
            await db.rollback()
        except Exception:
            pass
        db.sync_session.autoflush = False
        now = datetime.utcnow()
        error_msg = f"{type(exc).__name__}: {exc}"
        event.processed_at = now
        event.error = error_msg
        await db.commit()
        db.sync_session.autoflush = True

        # B2: Discord sync-failure 알림 (minimal — cooldown 없음, 기존 webhook URL 재사용).
        # 알림 실패는 메인 처리에 영향 없게 swallow. event 당 except 1회 = 자연 1알림.
        if project.discord_webhook_url:
            from app.services import discord_service
            try:
                content = (
                    f"⚠️ **pslog sync 실패** — {project.name}\n"
                    f"branch: `{event.branch}`\n"
                    f"commit: `{event.head_commit_sha[:7]}`\n"
                    f"error: ```{error_msg[:500]}```"
                )
                await discord_service.send_webhook(content, project.discord_webhook_url)
            except Exception:
                logger.exception("Failed to send Discord alert for event %s", event_id)
```

`from app.services import discord_service` 는 함수 안 import — circular import 회피 (sync_service ↔ discord_service 직접 의존성 없는 게 깔끔).

- [ ] **Step 4: Verify pass**

```bash
pytest tests/test_sync_service.py -k "discord_alert" -v 2>&1 | tail -10
```

Expected: **3 passed**.

- [ ] **Step 5: 회귀**

```bash
pytest -q 2>&1 | tail -3
```

Expected: `184 passed` (175 baseline + 3 Task 1 + 3 Task 2 + 3 Task 3).

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/sync_service.py backend/tests/test_sync_service.py
git commit -m "$(cat <<'EOF'
feat(b2): Discord sync-failure 알림 (minimal, no cooldown)

- process_event except 분기 끝에 fire-and-forget Discord 알림
- Project.discord_webhook_url set 인 경우만, 알림 실패 silent swallow
- 메시지: project name + branch + short SHA + error 500자 trim
- cooldown 없음 — event 당 except 1회 = 자연 1알림 (Phase 6 에서 cooldown + 3 템플릿)
- 회귀 3건: webhook url set 시 호출 / NULL 시 skip / success path 시 미호출

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Frontend — 타입 + API service

**Files:**
- Modify: `frontend/src/types/task.ts` (Task 인터페이스에 1 필드)
- Modify: `frontend/src/types/git.ts` (GitEventSummary 신규)
- Modify: `frontend/src/services/api.ts` (git.listGitEvents method)

- [ ] **Step 1: 변경 적용**

`frontend/src/types/task.ts` 의 `Task` 인터페이스 끝에 1 필드 추가:

```typescript
export interface Task {
  // ... 기존 필드
  source: TaskSource;
  external_id: string | null;
  last_commit_sha: string | null;
  archived_at: string | null;
  // Phase 5 follow-up B2 — handoff missing 배지 (TaskCard ⚠️)
  handoff_missing: boolean;
}
```

`frontend/src/types/git.ts` 끝에 신규 인터페이스:

```typescript
export interface GitEventSummary {
  id: string;
  branch: string;
  head_commit_sha: string;
  pusher: string;
  received_at: string;
  processed_at: string | null;
  error: string | null;
}
```

`frontend/src/services/api.ts` 의 `git` 그룹 (line ~109-120) 에 method 1개 추가:

```typescript
git: {
  getSettings: (projectId: string) =>
    apiClient.get<GitSettings>(`/projects/${projectId}/git-settings`),
  updateSettings: (projectId: string, data: GitSettingsUpdate) =>
    apiClient.patch<GitSettings>(`/projects/${projectId}/git-settings`, data),
  registerWebhook: (projectId: string) =>
    apiClient.post<WebhookRegisterResponse>(`/projects/${projectId}/git-settings/webhook`),
  listHandoffs: (projectId: string, params?: { branch?: string; limit?: number }) =>
    apiClient.get<HandoffSummary[]>(`/projects/${projectId}/handoffs`, { params }),
  listGitEvents: (
    projectId: string,
    params?: { failed_only?: boolean; limit?: number },
  ) =>
    apiClient.get<GitEventSummary[]>(`/projects/${projectId}/git-events`, { params }),
  reprocessEvent: (projectId: string, eventId: string) =>
    apiClient.post<ReprocessResponse>(`/projects/${projectId}/git-events/${eventId}/reprocess`),
},
```

`api.ts` 상단 imports 에 `GitEventSummary` 추가 (`import type { ... } from '@/types';` 또는 `from '@/types/git'` — 기존 패턴 따라).

`frontend/src/types/index.ts` 가 re-export 한다면 GitEventSummary 도 export. 확인:

```bash
grep "GitEventSummary\|HandoffSummary" frontend/src/types/index.ts
```

`HandoffSummary` 가 re-export 돼있으면 같은 위치에 `GitEventSummary` 도 추가.

- [ ] **Step 2: TypeScript build 검증**

```bash
cd /Users/arden/Documents/ardensdevspace/pslog/.worktrees/phase-5-followup-b2/frontend
bun run build 2>&1 | tail -10
```

Expected: build clean. 만약 `Task.handoff_missing` 추가로 다른 컴포넌트가 깨진다면 (interface 가 strict) — `handoff_missing` 이 optional 이 아니라 required 라 mock data 만드는 곳에서 fail. 그런 곳 있으면 mock 에 `handoff_missing: false` 추가.

```bash
grep -rn "task: Task = {" frontend/src/ 2>/dev/null
grep -rn "as Task" frontend/src/ 2>/dev/null | head -5
```

확인 후 필요시 보완.

- [ ] **Step 3: Lint**

```bash
bun run lint 2>&1 | tail -10
```

Expected: 신규 위배 0 (Phase 5b 의 8 pre-existing 위배는 그대로 — out of scope).

- [ ] **Step 4: Commit**

```bash
cd /Users/arden/Documents/ardensdevspace/pslog/.worktrees/phase-5-followup-b2
git add frontend/src/types/task.ts frontend/src/types/git.ts frontend/src/types/index.ts frontend/src/services/api.ts
git commit -m "$(cat <<'EOF'
feat(b2/frontend): types + api.ts — Task.handoff_missing + GitEventSummary

- Task 인터페이스에 handoff_missing: boolean
- GitEventSummary 신규 (id/branch/sha/pusher/received_at/processed_at/error)
- api.ts git.listGitEvents method (failed_only + limit query params)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Frontend — `useFailedGitEvents` hook + invalidate 갱신

**Files:**
- Modify: `frontend/src/hooks/useGithubSettings.ts` (신규 훅 + 기존 invalidate 갱신)

- [ ] **Step 1: 변경 적용**

`frontend/src/hooks/useGithubSettings.ts` 끝에 신규 훅 추가 + 기존 `useReprocessEvent` 의 onSuccess 에 invalidate 추가.

기존 `useReprocessEvent` 변경:

```typescript
export function useReprocessEvent(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (eventId: string) =>
      api.git.reprocessEvent(projectId, eventId).then((r) => r.data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['projects', projectId, 'handoffs'] });
      // B2: failed git-events 도 refetch (재처리 후 list 갱신)
      queryClient.invalidateQueries({ queryKey: ['projects', projectId, 'git-events', 'failed'] });
    },
  });
}
```

신규 훅 추가 (파일 끝):

```typescript
// B2 — failed git push events list (TaskCard ⚠️ 와는 별도, 모달 + ProjectItem badge 용)
export function useFailedGitEvents(projectId: string | null) {
  return useQuery({
    queryKey: ['projects', projectId, 'git-events', 'failed'],
    queryFn: () =>
      api.git
        .listGitEvents(projectId!, { failed_only: true, limit: 50 })
        .then((r) => r.data),
    enabled: !!projectId,
    staleTime: 30_000,
  });
}
```

- [ ] **Step 2: TypeScript build + lint**

```bash
cd frontend && bun run build 2>&1 | tail -5 && bun run lint 2>&1 | tail -5
```

Expected: build clean, lint 신규 위배 0.

- [ ] **Step 3: Commit**

```bash
cd /Users/arden/Documents/ardensdevspace/pslog/.worktrees/phase-5-followup-b2
git add frontend/src/hooks/useGithubSettings.ts
git commit -m "$(cat <<'EOF'
feat(b2/frontend): useFailedGitEvents + useReprocessEvent invalidate

- useFailedGitEvents(projectId) — TanStack Query, staleTime 30s
- useReprocessEvent.onSuccess 에 git-events 'failed' invalidate 추가 (재처리 후 list 자동 갱신)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: Frontend — `TaskCard.tsx` ⚠️ 배지

**Files:**
- Modify: `frontend/src/components/board/TaskCard.tsx` (1 조건부 렌더링)

- [ ] **Step 1: 변경 적용**

`frontend/src/components/board/TaskCard.tsx` 의 `<h4>` 안 (PLAN 배지 다음) 에 1줄 추가:

```tsx
<h4 className="font-bold text-xs sm:text-sm break-words">
  {task.title}
  {task.source === TASK_SOURCE.SYNCED_FROM_PLAN && (
    <span
      className="ml-1 rounded bg-blue-100 px-1.5 py-0.5 text-[10px] font-medium text-blue-700"
      title="PLAN.md 에서 자동 동기화된 태스크"
    >
      PLAN
    </span>
  )}
  {task.handoff_missing && (
    <span
      className="ml-1 rounded bg-yellow-100 px-1.5 py-0.5 text-[10px] font-medium text-yellow-800"
      title="이 commit 의 handoff 기록이 없습니다 — 작업 기록 빠짐"
    >
      ⚠️ 기록 빠짐
    </span>
  )}
</h4>
```

스타일은 기존 PLAN 배지의 형식 그대로 매칭 (color 만 yellow). archived task 는 카드 자체가 안 보이므로 추가 가드 불필요 (backend annotation 도 archived → false 처리).

- [ ] **Step 2: TypeScript build + lint**

```bash
cd frontend && bun run build 2>&1 | tail -5 && bun run lint 2>&1 | tail -5
```

Expected: clean.

- [ ] **Step 3: Commit**

```bash
cd /Users/arden/Documents/ardensdevspace/pslog/.worktrees/phase-5-followup-b2
git add frontend/src/components/board/TaskCard.tsx
git commit -m "$(cat <<'EOF'
feat(b2/frontend): TaskCard ⚠️ handoff missing 배지

- task.handoff_missing 이 true 면 "⚠️ 기록 빠짐" 노란 배지 (PLAN 배지 옆)
- tooltip 으로 "이 commit 의 handoff 기록이 없습니다" 설명
- backend 가 SYNCED + last_commit_sha set + handoff 없음 + not archived 만 true 로 계산

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: Frontend — `GitEventListModal.tsx` 신규

**Files:**
- Create: `frontend/src/components/sidebar/GitEventListModal.tsx`

- [ ] **Step 1: 신규 파일 작성**

`frontend/src/components/sidebar/GitEventListModal.tsx`:

```tsx
import { useFailedGitEvents, useReprocessEvent } from '@/hooks/useGithubSettings';
import type { GitEventSummary } from '@/types/git';

interface GitEventListModalProps {
  projectId: string;
  open: boolean;
  onClose: () => void;
}

export function GitEventListModal({ projectId, open, onClose }: GitEventListModalProps) {
  const { data: events, isLoading } = useFailedGitEvents(open ? projectId : null);
  const reprocess = useReprocessEvent(projectId);

  if (!open) return null;

  const handleReprocess = async (eventId: string) => {
    try {
      await reprocess.mutateAsync(eventId);
      // onSuccess invalidate 가 자동 refetch — 토스트 자리는 후속 (현재 없음)
    } catch (err: unknown) {
      // 토스트 system 미도입 — alert 로 대체 (Phase 5b 패턴)
      const error = err as { response?: { status?: number; data?: { detail?: string } }; message?: string };
      const status = error.response?.status;
      const detail = error.response?.data?.detail;
      if (status === 409) {
        alert('처리 중입니다 — 잠시 후 다시 시도해 주세요');
      } else if (status === 400) {
        alert('이미 성공적으로 처리되었습니다');
      } else {
        alert(detail || error.message || '재처리 실패');
      }
    }
  };

  return (
    <div
      className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-3 sm:p-4"
      onClick={onClose}
    >
      <div
        className="bg-white border-2 border-black shadow-[8px_8px_0px_0px_rgba(244,0,4,1)] p-4 sm:p-6 w-full max-w-2xl max-h-[90vh] overflow-auto"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 className="font-black text-base sm:text-lg mb-4">⚠️ Sync 실패 이벤트</h2>

        {isLoading ? (
          <p className="text-sm text-muted-foreground py-4">불러오는 중...</p>
        ) : !events || events.length === 0 ? (
          <p className="text-sm text-muted-foreground py-4">
            실패한 sync 이벤트가 없습니다.
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs sm:text-sm">
              <thead className="border-b-2 border-black">
                <tr className="font-bold">
                  <th className="text-left py-2 pr-2">시각</th>
                  <th className="text-left py-2 pr-2">브랜치</th>
                  <th className="text-left py-2 pr-2">commit</th>
                  <th className="text-left py-2 pr-2">error</th>
                  <th className="text-left py-2">동작</th>
                </tr>
              </thead>
              <tbody>
                {events.map((e) => (
                  <EventRow
                    key={e.id}
                    event={e}
                    onReprocess={handleReprocess}
                    isPending={reprocess.isPending && reprocess.variables === e.id}
                  />
                ))}
              </tbody>
            </table>
          </div>
        )}

        <div className="flex justify-end mt-4">
          <button
            type="button"
            className="px-3 py-1.5 text-xs font-medium border-2 border-black hover:bg-yellow-50 transition-colors"
            onClick={onClose}
          >
            닫기
          </button>
        </div>
      </div>
    </div>
  );
}

interface EventRowProps {
  event: GitEventSummary;
  onReprocess: (eventId: string) => void;
  isPending: boolean;
}

function EventRow({ event, onReprocess, isPending }: EventRowProps) {
  const date = new Date(event.received_at);
  const timeStr = `${String(date.getMonth() + 1).padStart(2, '0')}/${String(date.getDate()).padStart(2, '0')} ${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`;
  const errorOneLine = (event.error || '').split('\n')[0].slice(0, 50);
  return (
    <tr className="border-b border-gray-200">
      <td className="py-2 pr-2 whitespace-nowrap">{timeStr}</td>
      <td className="py-2 pr-2 break-all">{event.branch}</td>
      <td className="py-2 pr-2 font-mono">{event.head_commit_sha.slice(0, 7)}</td>
      <td className="py-2 pr-2 text-red-700" title={event.error || ''}>{errorOneLine}</td>
      <td className="py-2">
        <button
          type="button"
          disabled={isPending}
          onClick={() => onReprocess(event.id)}
          className="px-2 py-1 text-[11px] font-medium border-2 border-black bg-white hover:bg-yellow-50 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
        >
          {isPending ? '처리 중...' : '재처리'}
        </button>
      </td>
    </tr>
  );
}
```

스타일은 `HandoffHistoryModal` 의 검정 테두리 + 빨강 그림자 패턴 그대로 매칭. shadcn/ui Dialog 가 아닌 custom modal — codebase 컨벤션. `EventRow` sub-component 로 분리해서 hook (없지만 향후 확장 대비) 충돌 회피 + 가독성.

- [ ] **Step 2: TypeScript build + lint**

```bash
cd frontend && bun run build 2>&1 | tail -5 && bun run lint 2>&1 | tail -10
```

Expected: clean. lint 가 `react-hooks/set-state-in-effect` 등 위배 잡으면 — 본 컴포넌트는 useEffect 가 없으므로 문제 없을 것. 만약 다른 규칙 위배 발견되면 stop + 보완.

- [ ] **Step 3: Commit**

```bash
cd /Users/arden/Documents/ardensdevspace/pslog/.worktrees/phase-5-followup-b2
git add frontend/src/components/sidebar/GitEventListModal.tsx
git commit -m "$(cat <<'EOF'
feat(b2/frontend): GitEventListModal — failed events list + reprocess

- HandoffHistoryModal 패턴 매칭 (검정 테두리 + 빨강 그림자, custom modal — shadcn Dialog X)
- 빈 상태 / loading / data 4 state, 행마다 [재처리] 버튼
- onError 분기: 409 → "처리 중", 400 → "이미 성공", 기타 → detail 또는 message
- alert 로 토스트 대체 (Phase 5b 패턴 — 토스트 system 미도입)
- EventRow sub-component 분리 (가독성 + 향후 확장 대비)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 8: Frontend — `ProjectItem.tsx` count badge + 메뉴 항목 + 모달 mount

**Files:**
- Modify: `frontend/src/components/sidebar/ProjectItem.tsx`

- [ ] **Step 1: 변경 적용**

`frontend/src/components/sidebar/ProjectItem.tsx` 변경:

상단 imports 에 추가:
```tsx
import { GitEventListModal } from '@/components/sidebar/GitEventListModal';
import { useFailedGitEvents } from '@/hooks/useGithubSettings';
```

컴포넌트 안 state 에 추가:
```tsx
const [isGitEventOpen, setGitEventOpen] = useState(false);
```

`useFailedGitEvents` 호출 (isOwner 체크 위쪽 또는 직후, OWNER 만 fetch — 비-OWNER 는 메뉴 자체 미노출이므로 데이터 불필요):

```tsx
const { data: failedEvents } = useFailedGitEvents(isOwner ? project.id : null);
const failedCount = failedEvents?.length ?? 0;
```

기존 dropdown trigger 에 `failedCount > 0` 일 때 빨간 점 추가:

```tsx
<button
  type="button"
  className="absolute right-1 top-1/2 -translate-y-1/2 opacity-0 group-hover:opacity-100 transition-opacity p-1 text-xs font-bold hover:bg-yellow-100 rounded"
  onClick={(e) => {
    e.stopPropagation();
    setMenuOpen((prev) => !prev);
  }}
>
  ···
  {failedCount > 0 && (
    <span
      className="absolute top-0 right-0 w-1.5 h-1.5 bg-red-500 rounded-full"
      aria-label={`${failedCount}건 sync 실패`}
    />
  )}
</button>
```

dropdown menu 안 (Handoff 이력 항목 아래) 에 새 메뉴 항목 추가 — `failedCount > 0` 일 때만:

```tsx
{failedCount > 0 && (
  <button
    type="button"
    className="w-full text-left px-3 py-1.5 text-xs font-medium hover:bg-yellow-50 transition-colors text-yellow-800"
    onClick={(e) => {
      e.stopPropagation();
      setMenuOpen(false);
      setGitEventOpen(true);
    }}
  >
    ⚠️ Sync 실패 ({failedCount})
  </button>
)}
```

기존 ProjectGitSettingsModal / HandoffHistoryModal mount 옆에 추가:

```tsx
<GitEventListModal
  projectId={project.id}
  open={isGitEventOpen}
  onClose={() => setGitEventOpen(false)}
/>
```

- [ ] **Step 2: TypeScript build + lint**

```bash
cd frontend && bun run build 2>&1 | tail -5 && bun run lint 2>&1 | tail -10
```

Expected: clean. 신규 lint 위배 0.

- [ ] **Step 3: Commit**

```bash
cd /Users/arden/Documents/ardensdevspace/pslog/.worktrees/phase-5-followup-b2
git add frontend/src/components/sidebar/ProjectItem.tsx
git commit -m "$(cat <<'EOF'
feat(b2/frontend): ProjectItem — sync 실패 count badge + 메뉴 항목 + 모달 mount

- useFailedGitEvents(OWNER 만 fetch — 비-OWNER 는 메뉴 자체 미노출)
- dropdown trigger 의 ··· 우상단 빨간 점 (failedCount > 0)
- dropdown 메뉴 항목 "⚠️ Sync 실패 (N)" — failedCount > 0 일 때만 노출
- GitEventListModal mount

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 9: 최종 회귀 + handoff + PR

- [ ] **Step 1: 전체 backend 회귀**

```bash
cd /Users/arden/Documents/ardensdevspace/pslog/.worktrees/phase-5-followup-b2/backend
source venv/bin/activate
pytest -q 2>&1 | tail -3
```

Expected: `184 passed` (175 baseline + 3 Task 1 + 3 Task 2 + 3 Task 3).

- [ ] **Step 2: Frontend build + lint**

```bash
cd ../frontend
bun run build 2>&1 | tail -5
bun run lint 2>&1 | tail -10
```

Expected: build clean, lint 신규 위배 0 (Phase 5b 의 8 pre-existing 위배는 그대로).

- [ ] **Step 3: handoff 갱신**

`handoffs/main.md` 상단 (가장 최근 항목 위) 에 새 섹션:

```markdown
## 2026-05-01 (Phase 5 follow-up B2)

- [x] **B2 — UI Closure + Discord sync-failure 알림** — 브랜치 `feature/phase-5-followup-b2-ui`
  - [x] **TaskCard ⚠️ 기록 빠짐 배지**: backend `TaskResponse.handoff_missing` (task_service annotate, N+1 회피 — 단일 query). SYNCED + last_commit_sha set + handoff 없음 + not archived 만 true. frontend 1줄 조건부 렌더링.
  - [x] **GitEventListModal**: 신규 모달 (HandoffHistoryModal 패턴), 행마다 [재처리] 버튼. backend 신규 endpoint `GET /git-events?failed_only=true`. `useReprocessEvent` 호출 site (B1 에서 만든 훅이 이번에 wired up).
  - [x] **ProjectItem 메뉴 + count badge**: dropdown trigger ··· 우상단 빨간 점 (failedCount > 0), 메뉴 항목 "⚠️ Sync 실패 (N)" — 0건 시 항목 자체 숨김. OWNER 만 fetch.
  - [x] **Discord sync-failure 알림 (minimal)**: `process_event` except 끝에서 fire-and-forget. `Project.discord_webhook_url` set 인 경우만, 알림 실패 silent. cooldown 없음 — Phase 6 의 cooldown + 3 템플릿은 별개 phase.
  - [x] **검증**: backend **184 tests pass** (175 B1 baseline + 9 신규: handoff_missing 3 + git-events endpoint 3 + Discord alert 3). frontend `bun run build` clean, `bun run lint` 8 pre-existing (out of scope). **시각 검증은 사용자 dev server 직접** (PR 본문 체크리스트).

### 마지막 커밋

- pslog: `<sha> docs(handoff+spec+plan): Phase 5 follow-up B2 완료 + Phase 6 다음 할 일`
- 브랜치 base: `cd53696` (main, B1 PR #13 머지 직후)

### 다음 (Phase 6 — Discord 알림 통합 본편)

- [ ] **`discord_service` 확장 — 3 템플릿**:
  - 체크 변경 알림 (PLAN 의 `[ ]` → `[x]` 변화 사용자별 요약)
  - handoff 누락 경고 (일정 시간 경과 후 handoff 없으면 알림)
  - 롤백 알림 (PLAN 에서 task `[x]` → `[ ]` 회귀)
- [ ] **`sync_service` 가 알림 트리거** (DB 변경 후 fire-and-forget BackgroundTask)
- [ ] **cooldown 정책** (spec §8 — 3회 연속 실패 시 disable, burst 차단)
- [ ] **알림 종류별 on/off** (선택 — Project 설정 1 컬럼)

### 블로커

없음

### 메모 (2026-05-01 B2 추가)

- **handoff_missing annotate 패턴**: SQLAlchemy `column_property` 또는 select 의 EXISTS 라벨 대신 — list[Task] fetch 후 별도 query 1건으로 매칭 + 비-mapped Python 인스턴스 attribute 로 set. Pydantic `from_attributes=True` 가 attribute 를 mapped 여부 무관하게 읽음. cross-project (week tasks) 에서도 안전 — `Handoff.project_id.in_(...)` + `Handoff.commit_sha.in_(...)` 후 `(project_id, commit_sha) in existing_pairs` 로 정확 매칭.
- **GitEventListModal 패턴 결정**: codebase 가 shadcn `Dialog` 가 아닌 custom modal (HandoffHistoryModal — 검정 테두리 + 빨강 그림자) 컨벤션. 이번에도 같은 스타일 매칭. 토스트 system 도 미도입 → `alert()` 로 대체 (Phase 5b 패턴 그대로). 향후 toast 도입 시 일괄 교체.
- **Discord 알림 minimal**: `discord_service.send_webhook(content, url)` primitive 그대로 재사용. except 끝의 ~10줄 + `from app.services import discord_service` (함수 안 import) 로 circular 회피. cooldown 없어서 burst (예: PAT 만료로 webhook 10건 연속 실패) 시 10 알림 — Phase 6 에서 닫음.
- **count badge UX 결정**: dropdown 외부 (trigger 옆 빨간 점) + dropdown 내부 (메뉴 항목 카운트). 두 지점 다 표시해서 발견성 강화. 0건 시 둘 다 숨김.
- **next 할 일은 Phase 6** (Discord 3 템플릿 + cooldown). B2 가 sync-failure 1 alert 만 깔아둠 — failure 외 success-flow 알림은 Phase 6 본편.
```

- [ ] **Step 4: handoff + plan + spec commit**

```bash
cd /Users/arden/Documents/ardensdevspace/pslog/.worktrees/phase-5-followup-b2
git add handoffs/main.md docs/superpowers/plans/2026-05-01-phase-5-followup-b2-ui.md
# spec 은 이미 별도 commit 됨 (fabc5a4) — 재 add 안 해도 됨
git commit -m "$(cat <<'EOF'
docs(handoff+plan): Phase 5 follow-up B2 완료 + Phase 6 다음 할 일

- handoffs/main.md 에 2026-05-01 B2 섹션 추가 (UI 잔여 + Discord sync-failure alert, 184 tests)
- docs/superpowers/plans/2026-05-01-phase-5-followup-b2-ui.md 신규 (구현 plan 보존)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 5: Push + PR**

```bash
git push -u origin feature/phase-5-followup-b2-ui
gh pr create \
  --title "feat(b2): Phase 5 follow-up — TaskCard handoff missing 배지 + GitEventList 모달 + Discord sync-failure alert" \
  --body "$(cat <<'EOF'
## Summary

Phase 5b UI 잔여 2건 + Discord sync-failure 알림 minimal 1종을 한 PR 로 닫음. Phase 6 (Discord 알림 본편 — 3 템플릿 + cooldown) 진입 전 사용자 가시성의 minimal viable layer.

- **TaskCard ⚠️ 기록 빠짐 배지**: backend `TaskResponse.handoff_missing` (task_service annotate, N+1 회피). SYNCED + last_commit_sha set + handoff 없음 + not archived 만 true. frontend 1줄 조건부 렌더링.
- **GitEventListModal**: 신규 모달 (HandoffHistoryModal 패턴), 행마다 [재처리] 버튼. backend 신규 endpoint `GET /api/v1/projects/{id}/git-events?failed_only=true`. B1 에서 만들어둔 `useReprocessEvent` 훅이 이번에 wired up.
- **ProjectItem count badge**: dropdown trigger 우상단 빨간 점 + 메뉴 항목 "⚠️ Sync 실패 (N)" — 0건 시 항목 자체 숨김. OWNER 만 fetch.
- **Discord sync-failure 알림 (minimal)**: `process_event` except 끝에서 fire-and-forget Discord 알림. `Project.discord_webhook_url` set 인 경우만, 알림 실패 silent. cooldown 없음 — Phase 6 가 cooldown + 3 템플릿 본편 처리.

## Test plan

- [x] backend **184 tests pass** (175 B1 baseline + 9 신규: handoff_missing 3 + git-events endpoint 3 + Discord alert 3)
- [x] frontend `bun run build` clean
- [x] frontend `bun run lint` 신규 위배 0 (Phase 5b 의 8 pre-existing 그대로)
- [ ] 시각 검증 — 사용자 dev server 직접:
  - TaskCard ⚠️ 배지 표시 (SYNCED + handoff 없는 task) / 미표시 (MANUAL, handoff 있음, archived)
  - ProjectItem dropdown trigger 빨간 점 (failure 0건 vs 1건+ 양쪽)
  - 메뉴 항목 "⚠️ Sync 실패 (N)" 노출 (0건 시 숨김)
  - 모달 4 state (loading / empty / data / error)
  - 재처리 버튼 — pending 상태 + onSuccess 자동 refetch + 409/400 onError 분기 alert
- [ ] e2e — 의도적 실패 시나리오 (잘못된 PAT 등) 로 webhook → Discord 알림 도착 + 모달 row 등장 + reprocess

## 다음 (Phase 6 — Discord 알림 본편)

체크 변경 / handoff 누락 / 롤백 알림 3종 템플릿 + cooldown 정책 + 알림 on/off (선택). 본 PR 의 sync-failure alert 가 4번째 종류로 자연 합류 가능.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Self-Review Pass

**1. Spec coverage** — spec 섹션 1-7 vs plan tasks 매핑:

| Spec 항목 | Plan task |
|---|---|
| §2.1 handoff_missing 정의 | Task 1 (annotate 헬퍼 + 조건 4건) |
| §2.2 TaskResponse 필드 | Task 1 (Pydantic 1줄) |
| §2.3 GET /git-events endpoint | Task 2 (스키마 + 핸들러 + 3 테스트) |
| §2.4 Discord sync-failure 알림 | Task 3 (except 끝 ~10줄 + 3 테스트) |
| §3.1 frontend 타입 + API | Task 4 |
| §3.2 hook (useFailedGitEvents + invalidate) | Task 5 |
| §3.3 TaskCard ⚠️ 배지 | Task 6 |
| §3.4 GitEventListModal | Task 7 |
| §3.5 ProjectItem count badge + 메뉴 + 모달 mount | Task 8 |
| §3.6 lint/build 게이트 | 각 task 의 build/lint step + Task 9 |
| §3.7 시각 검증 | Task 9 PR 본문 체크리스트 |
| §5 test plan | Task 1-3 의 backend 9건 + Task 9 회귀 |

전 항목 task 매핑 완료. 누락 없음.

**2. Placeholder scan** — `<sha>` (Task 9 handoff 의 의도된 자리표시) 외 placeholder 없음. "TBD/TODO/implement later" 0.

**3. Type / signature consistency**:
- Backend: `Task.handoff_missing: bool` ↔ `TaskResponse.handoff_missing: bool = False` ↔ `t.handoff_missing = bool` (assignment) ↔ frontend `Task.handoff_missing: boolean` — 일관.
- `GitEventSummary` 필드 (id/branch/head_commit_sha/pusher/received_at/processed_at/error) backend ↔ frontend 동일.
- API path: backend `GET /api/v1/projects/{id}/git-events` ↔ frontend `apiClient.get('/projects/${projectId}/git-events')` (apiClient base path 가 `/api/v1` 이라고 가정 — 기존 패턴).
- Hook ↔ component: `useFailedGitEvents` returns `{data: GitEventSummary[]}`, modal 과 ProjectItem 모두 `data?.length` 사용 — 일관.
- Discord alert content: spec 의 `⚠️ **pslog sync 실패** — {project.name}` 포맷 ↔ Task 3 의 implementation ↔ Task 3 test 의 assertions (`"pslog sync 실패" in content`, `proj.name in content`, `event.branch in content`, `head[:7] in content`, `"RuntimeError" in content`) — 일관.

**4. 의존 순서 검토**:
- Task 1 (TaskResponse + annotate) 는 frontend Task 6 (TaskCard) 이 의존. Task 1 → Task 6 순.
- Task 2 (GET /git-events) 는 frontend Task 5 (useFailedGitEvents hook) 이 의존. Task 2 → Task 5 순.
- Task 3 (Discord alert) 는 독립.
- Task 4 (frontend types) 는 Task 5/6/7/8 의 의존. Task 4 → Task 5 → Task 7 → Task 8.
- 현재 plan 순서 (1→2→3→4→5→6→7→8→9) 는 의존 순서 만족. backend 끝낸 후 frontend, 그 안에서도 types → hook → component → integration.

**5. 테스트 결정성**:
- handoff_missing 회귀 (Task 1): 단일 query, 결정적.
- GET /git-events (Task 2): 단순 endpoint 호출, 결정적.
- Discord alert (Task 3): `monkeypatch.setattr(discord_service, "send_webhook", fake)` — race 없음, 결정적.

문제 없음. 진행 가능.
