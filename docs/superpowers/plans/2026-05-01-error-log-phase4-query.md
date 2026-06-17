# Error-log Phase 4 — Query API + Git Context Join Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Phase 3 가 채운 ErrorGroup / LogEvent 데이터를 사용자에게 노출 — `GET /errors`, `GET /errors/{group_id}` (git 컨텍스트 + 직전 정상 SHA), `GET /logs` (pg_trgm 풀텍스트). UI 없이 curl 만으로 검증 가능한 핵심 가치 단계.

**Architecture:** 6 task 분할 — Pydantic schemas → log_query_service.list_groups + count → log_query_service.get_group_detail (+ helpers _find_previous_good_sha + _collect_git_context) → log_query_service.list_logs → 3 endpoints + router → 최종 회귀/PR. 마이그레이션 신규 X. archived task 포함, pg_trgm partial index (Phase 1) 활용. Phase 6 / Phase 3 학습 패턴 (commit 후 알림 / SAVEPOINT race fix) 은 본 phase 미적용 — 순수 read-only 조회.

**Tech Stack:** FastAPI 0.115, SQLAlchemy 2.0 async (LEFT JOIN + IS NULL pattern, IN clause, ILIKE), Pydantic v2, pg_trgm (Phase 1 alembic 의 `idx_log_message_trgm` partial index 자동 활용), pytest + testcontainers PostgreSQL.

**선행 조건:**
- pslog `main` = `0f1cb10` (Error-log Phase 3 PR #17 머지 직후)
- alembic head = `7c6e0c9bb915` (Phase 1 의 모든 모델 + 인덱스 + Phase 6 컬럼 포함)
- backend tests baseline = **256 passing**
- Phase 3 가 채운 ErrorGroup 데이터 활용 (테스트는 별도 시드)
- spec: `docs/superpowers/specs/2026-05-01-error-log-phase4-query-design.md`

**중요한 계약:**

- **3 GET endpoints**: 모두 프로젝트 멤버 권한 (VIEWER 포함, 운영 투명성). 비-멤버 → 404 (handoffs / git-events 패턴). 비-존재 group/event → 404.
- **페이지네이션 = offset/limit**: 응답 `{items, total}`. errors limit 1~200, logs limit 1~500.
- **직전 정상 SHA SQL** (단일 쿼리, LEFT JOIN + IS NULL):
  ```sql
  SELECT DISTINCT le.version_sha
  FROM log_events le
  LEFT JOIN log_events le_target ON (
      le_target.project_id = le.project_id
      AND le_target.environment = le.environment
      AND le_target.version_sha = le.version_sha
      AND le_target.fingerprint = :target_fingerprint
  )
  WHERE le.project_id = :project_id
    AND le.environment = :environment
    AND le.received_at < :before_received_at
    AND le.version_sha != 'unknown'
    AND le_target.id IS NULL
  ORDER BY le.received_at DESC
  LIMIT 1
  ```
- **Git 컨텍스트 join** (3 단일 SQL): handoffs / tasks (archived 포함) / git_push_events `WHERE commit_sha IN (recent_event_shas - {"unknown"})`. push_events 는 received_at 오름차순 첫 번째 (spec §6.2 "first_seen").
- **`q` 풀텍스트**: `WHERE level IN (WARNING/ERROR/CRITICAL) AND message ILIKE '%q%'`. `idx_log_message_trgm` partial gin (Phase 1) 자동 활용. q + level 동시 지정 시 q 우선 — WARNING+ 강제, level 무시 (단순화).
- **archived task 포함** (spec §4.2): UI 가 후속 (archived) 배지 표시.
- **environment 필터 v1 미포함**: ErrorGroup 자체엔 environment 컬럼 없음 (spec §4.1). 후속 호소 시 EXISTS subquery 또는 컬럼 추가.
- **`level` 필터 v1**: 단일 값 매칭 (`level == ?`). multi-level / `level >= ?` 후속.
- **`raw_content` / `stack_trace` 등 큰 필드**: `LogEventSummary` 에서 제외. 향후 `GET /logs/{event_id}` detail endpoint 추가 시 포함.
- **에러 정책**:
  - 비-멤버 / 비-존재 → 404
  - q 길이 1자 → 422 (Pydantic min_length=2)
  - limit / offset 범위 위배 → 422 (Pydantic ge/le)
  - DB 실패 → FastAPI 기본 500

---

## File Structure

**신규 파일 (소스)**:
- `backend/app/schemas/log_query.py` — `HandoffRef / TaskRef / GitPushEventRef / GitContextBundle / GitContextWrapper / ErrorGroupSummary / ErrorGroupListResponse / LogEventSummary / LogEventListResponse / ErrorGroupDetail` (10 클래스)
- `backend/app/services/log_query_service.py` — `list_groups / _find_previous_good_sha / _collect_git_context / get_group_detail / list_logs` (5 함수)
- `backend/app/api/v1/endpoints/log_errors.py` — `GET /errors`, `GET /errors/{group_id}`
- `backend/app/api/v1/endpoints/log_logs.py` — `GET /logs`

**신규 파일 (테스트)**:
- `backend/tests/test_log_query_service.py` (11건)
- `backend/tests/test_log_errors_endpoint.py` (5건)
- `backend/tests/test_log_logs_endpoint.py` (3건)

**수정 파일**:
- `backend/app/api/v1/router.py` — log_errors_router + log_logs_router 마운트

**미변경**:
- alembic (Phase 1 의 모든 모델 + idx_log_message_trgm partial 인덱스 활용)
- 모델 (LogEvent / ErrorGroup / Handoff / Task / GitPushEvent 그대로)
- frontend (Phase 5 LogsPage / ErrorsPage / ErrorDetailPage 가 본 endpoint 호출)

---

### Task 1: Pydantic schemas (`log_query.py`)

**Files:**
- Create: `backend/app/schemas/log_query.py`

- [ ] **Step 1: Baseline 확인**

```bash
cd /Users/arden/Documents/ardensdevspace/pslog/.worktrees/error-log-phase4-query/backend
source venv/bin/activate
pytest -q 2>&1 | tail -3
```

Expected: `256 passed`. 다르면 STOP.

- [ ] **Step 2: `log_query.py` 작성**

`backend/app/schemas/log_query.py` 신규:

```python
"""log query API 의 Pydantic schemas.

설계서: 2026-05-01-error-log-phase4-query-design.md §3.2
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


# ---- Git context refs ----

class HandoffRef(BaseModel):
    """git 컨텍스트의 handoff lookup."""
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    commit_sha: str
    branch: str
    author_git_login: str
    pushed_at: datetime


class TaskRef(BaseModel):
    """git 컨텍스트의 task lookup. archived 포함."""
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    external_id: str | None
    title: str
    status: str
    last_commit_sha: str | None
    archived_at: datetime | None


class GitPushEventRef(BaseModel):
    """git 컨텍스트의 push event lookup."""
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    head_commit_sha: str
    branch: str
    pusher: str
    received_at: datetime


class GitContextBundle(BaseModel):
    handoffs: list[HandoffRef]
    tasks: list[TaskRef]
    git_push_event: GitPushEventRef | None


class GitContextWrapper(BaseModel):
    first_seen: GitContextBundle
    previous_good_sha: str | None


# ---- ErrorGroup ----

class ErrorGroupSummary(BaseModel):
    """GET /errors 목록 항목."""
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    fingerprint: str
    exception_class: str
    exception_message_sample: str | None
    event_count: int
    status: str  # ErrorGroupStatus enum value
    first_seen_at: datetime
    first_seen_version_sha: str
    last_seen_at: datetime
    last_seen_version_sha: str


class ErrorGroupListResponse(BaseModel):
    items: list[ErrorGroupSummary]
    total: int


# ---- LogEvent ----

class LogEventSummary(BaseModel):
    """LogEvent 응답 항목 (stack_trace 제외 — 길이 제한)."""
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    level: str  # LogLevel enum value
    message: str
    logger_name: str
    version_sha: str
    environment: str
    hostname: str
    emitted_at: datetime
    received_at: datetime
    fingerprint: str | None
    exception_class: str | None
    exception_message: str | None


class LogEventListResponse(BaseModel):
    items: list[LogEventSummary]
    total: int


# ---- 상세 ----

class ErrorGroupDetail(BaseModel):
    group: ErrorGroupSummary
    recent_events: list[LogEventSummary]
    git_context: GitContextWrapper
```

- [ ] **Step 3: import 검증**

```bash
cd backend && source venv/bin/activate
python -c "from app.schemas.log_query import (HandoffRef, TaskRef, GitPushEventRef, GitContextBundle, GitContextWrapper, ErrorGroupSummary, ErrorGroupListResponse, LogEventSummary, LogEventListResponse, ErrorGroupDetail); print('ok')"
```

Expected: `ok`.

- [ ] **Step 4: 회귀**

```bash
pytest -q 2>&1 | tail -3
```

Expected: `256 passed`. 영향 없음.

- [ ] **Step 5: Commit**

```bash
cd /Users/arden/Documents/ardensdevspace/pslog/.worktrees/error-log-phase4-query
git add backend/app/schemas/log_query.py
git commit -m "$(cat <<'EOF'
feat(error-log/phase4): Pydantic schemas (log_query)

- ErrorGroupSummary / ErrorGroupListResponse / ErrorGroupDetail
- LogEventSummary / LogEventListResponse
- GitContextBundle / GitContextWrapper
- HandoffRef / TaskRef / GitPushEventRef (작은 lookup refs)
- 모두 from_attributes=True (ORM round-trip)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: `log_query_service.list_groups` + helpers

**Files:**
- Create: `backend/app/services/log_query_service.py` (list_groups 만 — helpers 는 Task 3)
- Create: `backend/tests/test_log_query_service.py` (4건 — list_groups)

- [ ] **Step 1: Failing tests 작성**

`backend/tests/test_log_query_service.py` 신규:

```python
"""log_query_service 단위 테스트.

설계서: 2026-05-01-error-log-phase4-query-design.md §3.1
"""

import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.error_group import ErrorGroup, ErrorGroupStatus
from app.models.log_event import LogEvent, LogLevel
from app.models.project import Project
from app.models.workspace import Workspace
from app.services import log_query_service


async def _seed_project(db: AsyncSession) -> Project:
    ws = Workspace(name="ws", slug=f"ws-{uuid.uuid4().hex[:8]}")
    db.add(ws)
    await db.flush()
    proj = Project(workspace_id=ws.id, name="p")
    db.add(proj)
    await db.commit()
    await db.refresh(proj)
    return proj


def _make_group(
    proj: Project, *, fingerprint: str = "fp-1",
    status: ErrorGroupStatus = ErrorGroupStatus.OPEN,
    last_seen_at: datetime | None = None,
) -> ErrorGroup:
    now = datetime.utcnow()
    return ErrorGroup(
        project_id=proj.id, fingerprint=fingerprint,
        exception_class="KeyError", exception_message_sample="x",
        first_seen_at=now, first_seen_version_sha="a" * 40,
        last_seen_at=last_seen_at or now, last_seen_version_sha="a" * 40,
        event_count=1, status=status,
    )


# ---- list_groups ----

async def test_list_groups_returns_all_when_no_filter(async_session: AsyncSession):
    """필터 없음 — 모든 group + total 반환."""
    proj = await _seed_project(async_session)
    g1 = _make_group(proj, fingerprint="fp-a")
    g2 = _make_group(proj, fingerprint="fp-b", status=ErrorGroupStatus.RESOLVED)
    async_session.add_all([g1, g2])
    await async_session.commit()

    rows, total = await log_query_service.list_groups(
        async_session, project_id=proj.id,
    )
    assert total == 2
    assert len(rows) == 2


async def test_list_groups_filter_by_status(async_session: AsyncSession):
    """status=OPEN 필터."""
    proj = await _seed_project(async_session)
    g_open = _make_group(proj, fingerprint="fp-a", status=ErrorGroupStatus.OPEN)
    g_resolved = _make_group(proj, fingerprint="fp-b", status=ErrorGroupStatus.RESOLVED)
    async_session.add_all([g_open, g_resolved])
    await async_session.commit()

    rows, total = await log_query_service.list_groups(
        async_session, project_id=proj.id, status=ErrorGroupStatus.OPEN,
    )
    assert total == 1
    assert rows[0].fingerprint == "fp-a"


async def test_list_groups_filter_by_since(async_session: AsyncSession):
    """since=... 필터 (last_seen_at >= since)."""
    proj = await _seed_project(async_session)
    cutoff = datetime(2026, 5, 1, 10, 0)
    old = _make_group(proj, fingerprint="fp-old", last_seen_at=datetime(2026, 4, 30, 23, 0))
    new = _make_group(proj, fingerprint="fp-new", last_seen_at=datetime(2026, 5, 1, 11, 0))
    async_session.add_all([old, new])
    await async_session.commit()

    rows, total = await log_query_service.list_groups(
        async_session, project_id=proj.id, since=cutoff,
    )
    assert total == 1
    assert rows[0].fingerprint == "fp-new"


async def test_list_groups_pagination_total_correct(async_session: AsyncSession):
    """offset/limit — total 은 전체, items 는 limit 만큼."""
    proj = await _seed_project(async_session)
    for i in range(5):
        async_session.add(_make_group(proj, fingerprint=f"fp-{i}"))
    await async_session.commit()

    rows, total = await log_query_service.list_groups(
        async_session, project_id=proj.id, offset=0, limit=2,
    )
    assert total == 5
    assert len(rows) == 2

    rows2, total2 = await log_query_service.list_groups(
        async_session, project_id=proj.id, offset=4, limit=2,
    )
    assert total2 == 5
    assert len(rows2) == 1  # 5 - 4 = 1
```

- [ ] **Step 2: Verify failure**

```bash
pytest tests/test_log_query_service.py -v 2>&1 | tail -15
```

Expected: 4 FAIL with `ImportError: cannot import name 'log_query_service'`.

- [ ] **Step 3: `log_query_service.py` 의 `list_groups` 구현**

`backend/app/services/log_query_service.py` 신규:

```python
"""LogEvent / ErrorGroup 조회 + Git 컨텍스트 join.

설계서: 2026-05-01-error-log-phase4-query-design.md §3.1
spec §6.2 의 데이터 흐름 그대로.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.error_group import ErrorGroup, ErrorGroupStatus
from app.models.log_event import LogEvent, LogLevel


async def list_groups(
    db: AsyncSession,
    *,
    project_id: UUID,
    status: ErrorGroupStatus | None = None,
    since: datetime | None = None,
    offset: int = 0,
    limit: int = 50,
) -> tuple[list[ErrorGroup], int]:
    """ErrorGroup 목록. 필터 + offset/limit. last_seen_at desc.

    environment 필터 미포함 (v1) — ErrorGroup 자체엔 environment 컬럼 없음.
    """
    base = select(ErrorGroup).where(ErrorGroup.project_id == project_id)
    count_base = select(func.count()).select_from(ErrorGroup).where(
        ErrorGroup.project_id == project_id
    )

    if status is not None:
        base = base.where(ErrorGroup.status == status)
        count_base = count_base.where(ErrorGroup.status == status)
    if since is not None:
        base = base.where(ErrorGroup.last_seen_at >= since)
        count_base = count_base.where(ErrorGroup.last_seen_at >= since)

    base = base.order_by(ErrorGroup.last_seen_at.desc()).offset(offset).limit(limit)

    rows = (await db.execute(base)).scalars().all()
    total = (await db.execute(count_base)).scalar_one()
    return list(rows), total
```

- [ ] **Step 4: Verify pass + 회귀**

```bash
pytest tests/test_log_query_service.py -v 2>&1 | tail -15
pytest -q 2>&1 | tail -3
```

Expected: 4 신규 PASS, 전체 `260 passed` (256 + 4).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/log_query_service.py backend/tests/test_log_query_service.py
git commit -m "$(cat <<'EOF'
feat(error-log/phase4): log_query_service.list_groups + count

- offset/limit + total — count 는 별도 SELECT (필터 적용된 후)
- 필터: status / since (last_seen_at >= since). environment 미포함 (v1)
- order_by last_seen_at desc
- 회귀 4건: 필터 없음 / status filter / since filter / pagination total 정확성

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: `log_query_service.get_group_detail` + helpers (`_find_previous_good_sha` + `_collect_git_context`)

**Files:**
- Modify: `backend/app/services/log_query_service.py` (3 함수 추가)
- Modify: `backend/tests/test_log_query_service.py` (4건 추가)

- [ ] **Step 1: Failing tests 작성**

`backend/tests/test_log_query_service.py` 끝에 추가:

```python
# ---- get_group_detail ----

from app.models.git_push_event import GitPushEvent
from app.models.handoff import Handoff
from app.models.task import Task, TaskSource, TaskStatus


def _make_log_event(
    proj: Project, *, fingerprint: str = "fp-1", version_sha: str = "a" * 40,
    environment: str = "production", received_at: datetime | None = None,
) -> LogEvent:
    return LogEvent(
        project_id=proj.id, level=LogLevel.ERROR,
        message="boom", logger_name="app.x", version_sha=version_sha,
        environment=environment, hostname="h",
        emitted_at=datetime.utcnow(), received_at=received_at or datetime.utcnow(),
        exception_class="KeyError", exception_message="x",
        fingerprint=fingerprint, fingerprinted_at=datetime.utcnow(),
    )


async def test_get_group_detail_normal_path_with_git_context(async_session: AsyncSession):
    """정상 path — group + recent events + git context (handoff/task/push_event) 채워짐."""
    proj = await _seed_project(async_session)
    sha = "a" * 40
    group = _make_group(proj, fingerprint="fp-1")
    event = _make_log_event(proj, fingerprint="fp-1", version_sha=sha)
    handoff = Handoff(
        project_id=proj.id, branch="main",
        author_git_login="alice", commit_sha=sha,
        pushed_at=datetime.utcnow(), parsed_tasks=[], free_notes={},
        raw_content="x",
    )
    task = Task(
        project_id=proj.id, title="T",
        source=TaskSource.SYNCED_FROM_PLAN, external_id="task-001",
        status=TaskStatus.DONE, last_commit_sha=sha,
    )
    push = GitPushEvent(
        project_id=proj.id, branch="main", head_commit_sha=sha,
        commits=[], commits_truncated=False, pusher="alice",
        received_at=datetime.utcnow(), processed_at=datetime.utcnow(),
    )
    async_session.add_all([group, event, handoff, task, push])
    await async_session.commit()
    await async_session.refresh(group)

    detail = await log_query_service.get_group_detail(
        async_session, project_id=proj.id, group_id=group.id,
    )
    assert detail is not None
    assert detail["group"].id == group.id
    assert len(detail["recent_events"]) == 1
    git_ctx = detail["git_context"]
    assert len(git_ctx["first_seen"]["handoffs"]) == 1
    assert len(git_ctx["first_seen"]["tasks"]) == 1
    assert git_ctx["first_seen"]["git_push_event"] is not None
    assert git_ctx["previous_good_sha"] is None  # 다른 fingerprint 의 SHA 없음


async def test_get_group_detail_unknown_sha_only(async_session: AsyncSession):
    """모든 events 의 version_sha == 'unknown' → git context 빈."""
    proj = await _seed_project(async_session)
    group = _make_group(proj, fingerprint="fp-2")
    event = _make_log_event(proj, fingerprint="fp-2", version_sha="unknown")
    async_session.add_all([group, event])
    await async_session.commit()
    await async_session.refresh(group)

    detail = await log_query_service.get_group_detail(
        async_session, project_id=proj.id, group_id=group.id,
    )
    assert detail is not None
    git_ctx = detail["git_context"]
    assert git_ctx["first_seen"]["handoffs"] == []
    assert git_ctx["first_seen"]["tasks"] == []
    assert git_ctx["first_seen"]["git_push_event"] is None


async def test_get_group_detail_previous_good_sha_algorithm(async_session: AsyncSession):
    """직전 정상 SHA — 같은 environment 의 다른 fingerprint SHA 가 가장 최근 정상."""
    proj = await _seed_project(async_session)
    target_fp = "fp-target"
    other_fp = "fp-other"

    # Old: 다른 fingerprint 의 event (정상 SHA — target_fp 발생 안 함)
    good_event = _make_log_event(
        proj, fingerprint=other_fp, version_sha="g" * 40,
        environment="production",
        received_at=datetime(2026, 5, 1, 9, 0),  # before target
    )

    # New: target_fp 의 첫 발생
    target_event = _make_log_event(
        proj, fingerprint=target_fp, version_sha="t" * 40,
        environment="production",
        received_at=datetime(2026, 5, 1, 10, 0),
    )

    group = _make_group(proj, fingerprint=target_fp)
    group.first_seen_at = datetime(2026, 5, 1, 10, 0)

    async_session.add_all([good_event, target_event, group])
    await async_session.commit()
    await async_session.refresh(group)

    detail = await log_query_service.get_group_detail(
        async_session, project_id=proj.id, group_id=group.id,
    )
    assert detail is not None
    assert detail["git_context"]["previous_good_sha"] == "g" * 40


async def test_get_group_detail_returns_none_for_other_project(async_session: AsyncSession):
    """다른 project 의 group_id → None."""
    proj_a = await _seed_project(async_session)
    proj_b = await _seed_project(async_session)
    group = _make_group(proj_b, fingerprint="fp-x")
    async_session.add(group)
    await async_session.commit()
    await async_session.refresh(group)

    detail = await log_query_service.get_group_detail(
        async_session, project_id=proj_a.id, group_id=group.id,
    )
    assert detail is None
```

- [ ] **Step 2: Verify failure**

```bash
pytest tests/test_log_query_service.py -k "get_group_detail" -v 2>&1 | tail -10
```

Expected: 4 FAIL — `AttributeError: ... has no attribute 'get_group_detail'`.

- [ ] **Step 3: Implement helpers + `get_group_detail`**

`backend/app/services/log_query_service.py` 끝에 추가 (imports 도 갱신):

```python
# 파일 상단 imports 에 추가:
from app.models.git_push_event import GitPushEvent
from app.models.handoff import Handoff
from app.models.task import Task

_RECENT_EVENTS_LIMIT = 50


async def _find_previous_good_sha(
    db: AsyncSession,
    *,
    project_id: UUID,
    environment: str,
    target_fingerprint: str,
    before_received_at: datetime,
) -> str | None:
    """직전 정상 SHA — LEFT JOIN + IS NULL 패턴.

    설계서 §2.2.
    같은 environment 에서 target_fingerprint 가 *없었던* 가장 최근 SHA.
    """
    le = LogEvent.__table__.alias("le")
    le_target = LogEvent.__table__.alias("le_target")
    stmt = (
        select(le.c.version_sha).distinct()
        .select_from(
            le.outerjoin(
                le_target,
                (le_target.c.project_id == le.c.project_id)
                & (le_target.c.environment == le.c.environment)
                & (le_target.c.version_sha == le.c.version_sha)
                & (le_target.c.fingerprint == target_fingerprint),
            )
        )
        .where(
            le.c.project_id == project_id,
            le.c.environment == environment,
            le.c.received_at < before_received_at,
            le.c.version_sha != "unknown",
            le_target.c.id.is_(None),
        )
        .order_by(le.c.received_at.desc())
        .limit(1)
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def _collect_git_context(
    db: AsyncSession,
    *,
    project_id: UUID,
    version_shas: set[str],
) -> dict:
    """3 단일 SQL — handoffs / tasks (archived 포함) / push_events join.

    push_event 는 received_at 오름차순 첫 번째 (spec §6.2 'first_seen').
    """
    if not version_shas:
        return {"handoffs": [], "tasks": [], "git_push_event": None}

    handoffs = (await db.execute(
        select(Handoff).where(
            Handoff.project_id == project_id,
            Handoff.commit_sha.in_(version_shas),
        )
    )).scalars().all()

    tasks = (await db.execute(
        select(Task).where(
            Task.project_id == project_id,
            Task.last_commit_sha.in_(version_shas),
        )
        # archived 포함 (spec §4.2)
    )).scalars().all()

    push_events = (await db.execute(
        select(GitPushEvent).where(
            GitPushEvent.project_id == project_id,
            GitPushEvent.head_commit_sha.in_(version_shas),
        )
    )).scalars().all()

    first_push = sorted(push_events, key=lambda e: e.received_at)[0] if push_events else None

    return {
        "handoffs": list(handoffs),
        "tasks": list(tasks),
        "git_push_event": first_push,
    }


async def get_group_detail(
    db: AsyncSession,
    *,
    project_id: UUID,
    group_id: UUID,
) -> dict | None:
    """ErrorGroup 상세 + recent events + git 컨텍스트 + 직전 정상 SHA.

    None — group 미존재 또는 다른 project 소속.
    """
    group = await db.get(ErrorGroup, group_id)
    if group is None or group.project_id != project_id:
        return None

    events_stmt = (
        select(LogEvent)
        .where(LogEvent.fingerprint == group.fingerprint)
        .where(LogEvent.project_id == project_id)
        .order_by(LogEvent.received_at.desc())
        .limit(_RECENT_EVENTS_LIMIT)
    )
    recent_events = (await db.execute(events_stmt)).scalars().all()

    shas = {
        e.version_sha for e in recent_events
        if e.version_sha and e.version_sha != "unknown"
    }
    git_context = await _collect_git_context(
        db, project_id=project_id, version_shas=shas,
    )

    previous_good_sha: str | None = None
    if recent_events:
        first_event = min(recent_events, key=lambda e: e.received_at)
        previous_good_sha = await _find_previous_good_sha(
            db,
            project_id=project_id,
            environment=first_event.environment,
            target_fingerprint=group.fingerprint,
            before_received_at=group.first_seen_at,
        )

    return {
        "group": group,
        "recent_events": list(recent_events),
        "git_context": {
            "first_seen": git_context,
            "previous_good_sha": previous_good_sha,
        },
    }
```

- [ ] **Step 4: Verify pass + 회귀**

```bash
pytest tests/test_log_query_service.py -v 2>&1 | tail -15
pytest -q 2>&1 | tail -3
```

Expected: 4 신규 PASS, 전체 `264 passed` (260 + 4).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/log_query_service.py backend/tests/test_log_query_service.py
git commit -m "$(cat <<'EOF'
feat(error-log/phase4): get_group_detail + _find_previous_good_sha + _collect_git_context

- _find_previous_good_sha: LEFT JOIN + IS NULL — 같은 env 의 target_fp 없는 가장 최근 SHA
- _collect_git_context: 3 단일 SQL (handoffs/tasks/push_events IN), archived task 포함, push_events 첫 번째
- get_group_detail: group + recent 50 events + git_context (nested) + previous_good_sha
- 회귀 4건: 정상 path / unknown SHA / previous_good_sha 알고리즘 / 다른 project None

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: `log_query_service.list_logs`

**Files:**
- Modify: `backend/app/services/log_query_service.py` (list_logs 추가)
- Modify: `backend/tests/test_log_query_service.py` (3건 추가)

- [ ] **Step 1: Failing tests 작성**

`backend/tests/test_log_query_service.py` 끝에 추가:

```python
# ---- list_logs ----

async def test_list_logs_filter_by_level(async_session: AsyncSession):
    """level=ERROR 필터 (단일 값 매칭)."""
    proj = await _seed_project(async_session)
    e_error = _make_log_event(proj, fingerprint="fp-1")
    e_error.level = LogLevel.ERROR
    e_warning = _make_log_event(proj, fingerprint="fp-2")
    e_warning.level = LogLevel.WARNING
    async_session.add_all([e_error, e_warning])
    await async_session.commit()

    rows, total = await log_query_service.list_logs(
        async_session, project_id=proj.id, level=LogLevel.ERROR,
    )
    assert total == 1
    assert rows[0].level == LogLevel.ERROR


async def test_list_logs_filter_by_since(async_session: AsyncSession):
    """since 필터 (received_at >= since)."""
    proj = await _seed_project(async_session)
    cutoff = datetime(2026, 5, 1, 10, 0)
    old_e = _make_log_event(
        proj, fingerprint="fp-old",
        received_at=datetime(2026, 4, 30, 23, 0),
    )
    new_e = _make_log_event(
        proj, fingerprint="fp-new",
        received_at=datetime(2026, 5, 1, 11, 0),
    )
    async_session.add_all([old_e, new_e])
    await async_session.commit()

    rows, total = await log_query_service.list_logs(
        async_session, project_id=proj.id, since=cutoff,
    )
    assert total == 1
    assert rows[0].fingerprint == "fp-new"


async def test_list_logs_q_full_text_filters_to_warning_and_above(
    async_session: AsyncSession,
):
    """q 풀텍스트 — level >= WARNING 자동 강제 + ILIKE."""
    proj = await _seed_project(async_session)

    # WARNING + matching message
    e1 = _make_log_event(proj, fingerprint="fp-1")
    e1.level = LogLevel.WARNING
    e1.message = "this is a special_marker thing"

    # ERROR + matching message
    e2 = _make_log_event(proj, fingerprint="fp-2")
    e2.level = LogLevel.ERROR
    e2.message = "another special_marker here"

    # INFO + matching message — should be excluded (level < WARNING)
    e3 = _make_log_event(proj, fingerprint="fp-3")
    e3.level = LogLevel.INFO
    e3.message = "info with special_marker"

    # WARNING + non-matching — should be excluded (no q match)
    e4 = _make_log_event(proj, fingerprint="fp-4")
    e4.level = LogLevel.WARNING
    e4.message = "completely different"

    async_session.add_all([e1, e2, e3, e4])
    await async_session.commit()

    rows, total = await log_query_service.list_logs(
        async_session, project_id=proj.id, q="special_marker",
    )
    assert total == 2
    msgs = {r.message for r in rows}
    assert msgs == {
        "this is a special_marker thing",
        "another special_marker here",
    }
```

- [ ] **Step 2: Verify failure**

```bash
pytest tests/test_log_query_service.py -k "list_logs" -v 2>&1 | tail -10
```

Expected: 3 FAIL — `AttributeError: ... has no attribute 'list_logs'`.

- [ ] **Step 3: Implement `list_logs`**

`backend/app/services/log_query_service.py` 끝에 추가:

```python
async def list_logs(
    db: AsyncSession,
    *,
    project_id: UUID,
    level: LogLevel | None = None,
    since: datetime | None = None,
    q: str | None = None,
    offset: int = 0,
    limit: int = 100,
) -> tuple[list[LogEvent], int]:
    """LogEvent 조회. level/since/q 필터, received_at desc.

    q: pg_trgm ILIKE — Phase 1 alembic 의 idx_log_message_trgm partial (level >= WARNING) 활용.
    q 지정 시 자동 level >= WARNING 강제 (인덱스 partial WHERE 매칭).
    """
    base = select(LogEvent).where(LogEvent.project_id == project_id)
    count_base = select(func.count()).select_from(LogEvent).where(
        LogEvent.project_id == project_id
    )

    if q:
        # pg_trgm 인덱스 활용 — level >= WARNING 자동 강제
        warning_or_higher = LogEvent.level.in_(
            [LogLevel.WARNING, LogLevel.ERROR, LogLevel.CRITICAL]
        )
        base = base.where(warning_or_higher).where(LogEvent.message.ilike(f"%{q}%"))
        count_base = count_base.where(warning_or_higher).where(
            LogEvent.message.ilike(f"%{q}%")
        )
    elif level is not None:
        # q 가 없을 때만 level 단일 매칭 적용. q + level 동시 시 q 우선 (단순화)
        base = base.where(LogEvent.level == level)
        count_base = count_base.where(LogEvent.level == level)

    if since is not None:
        base = base.where(LogEvent.received_at >= since)
        count_base = count_base.where(LogEvent.received_at >= since)

    base = base.order_by(LogEvent.received_at.desc()).offset(offset).limit(limit)

    rows = (await db.execute(base)).scalars().all()
    total = (await db.execute(count_base)).scalar_one()
    return list(rows), total
```

- [ ] **Step 4: Verify pass + 회귀**

```bash
pytest tests/test_log_query_service.py -v 2>&1 | tail -20
pytest -q 2>&1 | tail -3
```

Expected: 3 신규 PASS, 전체 `267 passed` (264 + 3).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/log_query_service.py backend/tests/test_log_query_service.py
git commit -m "$(cat <<'EOF'
feat(error-log/phase4): log_query_service.list_logs (pg_trgm 풀텍스트)

- level / since / q 필터, received_at desc, offset/limit
- q 지정 시 자동 level >= WARNING 강제 (idx_log_message_trgm partial 활용)
- q + level 동시: q 우선 (단순화)
- 회귀 3건: level filter / since filter / q 풀텍스트 (WARNING+ 자동, ILIKE)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: 3 GET endpoints (`log_errors.py` + `log_logs.py`) + router 마운트

**Files:**
- Create: `backend/app/api/v1/endpoints/log_errors.py` (GET /errors, GET /errors/{group_id})
- Create: `backend/app/api/v1/endpoints/log_logs.py` (GET /logs)
- Modify: `backend/app/api/v1/router.py` (2 routers 마운트)
- Create: `backend/tests/test_log_errors_endpoint.py` (5건)
- Create: `backend/tests/test_log_logs_endpoint.py` (3건)

- [ ] **Step 1: Failing tests 작성**

`backend/tests/test_log_errors_endpoint.py` 신규:

```python
"""log-errors endpoint 통합 테스트.

설계서: 2026-05-01-error-log-phase4-query-design.md §3.3
"""

import uuid
from datetime import datetime

import pytest
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.error_group import ErrorGroup, ErrorGroupStatus
from app.models.project import Project, ProjectMember
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceRole


@pytest.fixture()
async def client_with_db(async_session: AsyncSession, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("pslog_FERNET_KEY", Fernet.generate_key().decode())
    import importlib
    import app.config
    importlib.reload(app.config)
    import app.core.crypto
    importlib.reload(app.core.crypto)

    from app.main import app
    from app.database import get_db

    async def override_get_db():
        yield async_session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


async def _seed_user_project(
    db: AsyncSession, role: WorkspaceRole = WorkspaceRole.OWNER,
) -> tuple[User, Project]:
    user = User(
        email=f"u-{uuid.uuid4().hex[:8]}@example.com",
        name="alice", password_hash="x",
    )
    db.add(user)
    await db.flush()
    ws = Workspace(name="ws", slug=f"ws-{uuid.uuid4().hex[:8]}")
    db.add(ws)
    await db.flush()
    proj = Project(workspace_id=ws.id, name="p")
    db.add(proj)
    await db.flush()
    db.add(ProjectMember(project_id=proj.id, user_id=user.id, role=role))
    await db.commit()
    await db.refresh(user)
    await db.refresh(proj)
    return user, proj


def _auth_token(user: User) -> str:
    from app.services.auth_service import create_access_token
    return create_access_token({"sub": str(user.id)})


def _make_group(proj: Project, *, fingerprint: str = "fp-1") -> ErrorGroup:
    now = datetime.utcnow()
    return ErrorGroup(
        project_id=proj.id, fingerprint=fingerprint,
        exception_class="KeyError", exception_message_sample="x",
        first_seen_at=now, first_seen_version_sha="a" * 40,
        last_seen_at=now, last_seen_version_sha="a" * 40,
        event_count=1, status=ErrorGroupStatus.OPEN,
    )


async def test_list_errors_normal(client_with_db, async_session: AsyncSession):
    """GET /errors 정상 — 멤버, items + total."""
    user, proj = await _seed_user_project(async_session)
    g = _make_group(proj)
    async_session.add(g)
    await async_session.commit()

    token = _auth_token(user)
    res = await client_with_db.get(
        f"/api/v1/projects/{proj.id}/errors",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["total"] == 1
    assert len(body["items"]) == 1
    assert body["items"][0]["fingerprint"] == "fp-1"


async def test_list_errors_404_for_non_member(
    client_with_db, async_session: AsyncSession,
):
    """비-멤버 → 404."""
    user, proj = await _seed_user_project(async_session)
    other = User(
        email=f"o-{uuid.uuid4().hex[:8]}@example.com",
        name="bob", password_hash="x",
    )
    async_session.add(other)
    await async_session.commit()
    await async_session.refresh(other)

    token = _auth_token(other)
    res = await client_with_db.get(
        f"/api/v1/projects/{proj.id}/errors",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 404


async def test_list_errors_viewer_can_access(client_with_db, async_session: AsyncSession):
    """VIEWER 권한 — 조회 가능."""
    user, proj = await _seed_user_project(async_session, role=WorkspaceRole.VIEWER)
    g = _make_group(proj)
    async_session.add(g)
    await async_session.commit()

    token = _auth_token(user)
    res = await client_with_db.get(
        f"/api/v1/projects/{proj.id}/errors",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200


async def test_get_error_detail_normal(client_with_db, async_session: AsyncSession):
    """GET /errors/{group_id} 정상 — group + recent_events + git_context."""
    user, proj = await _seed_user_project(async_session)
    g = _make_group(proj)
    async_session.add(g)
    await async_session.commit()
    await async_session.refresh(g)

    token = _auth_token(user)
    res = await client_with_db.get(
        f"/api/v1/projects/{proj.id}/errors/{g.id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["group"]["id"] == str(g.id)
    assert body["recent_events"] == []  # 시드한 LogEvent 없음
    assert body["git_context"]["first_seen"]["handoffs"] == []
    assert body["git_context"]["previous_good_sha"] is None


async def test_get_error_detail_404_for_other_project(
    client_with_db, async_session: AsyncSession,
):
    """다른 project 의 group_id → 404."""
    user, proj_a = await _seed_user_project(async_session)
    _, proj_b = await _seed_user_project(async_session)
    g = _make_group(proj_b)
    async_session.add(g)
    await async_session.commit()
    await async_session.refresh(g)

    token = _auth_token(user)
    res = await client_with_db.get(
        f"/api/v1/projects/{proj_a.id}/errors/{g.id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 404
```

`backend/tests/test_log_logs_endpoint.py` 신규:

```python
"""log-logs endpoint 통합 테스트.

설계서: 2026-05-01-error-log-phase4-query-design.md §3.3
"""

import uuid
from datetime import datetime

import pytest
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.log_event import LogEvent, LogLevel
from app.models.project import Project, ProjectMember
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceRole


@pytest.fixture()
async def client_with_db(async_session: AsyncSession, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("pslog_FERNET_KEY", Fernet.generate_key().decode())
    import importlib
    import app.config
    importlib.reload(app.config)
    import app.core.crypto
    importlib.reload(app.core.crypto)

    from app.main import app
    from app.database import get_db

    async def override_get_db():
        yield async_session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


async def _seed_user_project(
    db: AsyncSession, role: WorkspaceRole = WorkspaceRole.OWNER,
) -> tuple[User, Project]:
    user = User(
        email=f"u-{uuid.uuid4().hex[:8]}@example.com",
        name="alice", password_hash="x",
    )
    db.add(user)
    await db.flush()
    ws = Workspace(name="ws", slug=f"ws-{uuid.uuid4().hex[:8]}")
    db.add(ws)
    await db.flush()
    proj = Project(workspace_id=ws.id, name="p")
    db.add(proj)
    await db.flush()
    db.add(ProjectMember(project_id=proj.id, user_id=user.id, role=role))
    await db.commit()
    await db.refresh(user)
    await db.refresh(proj)
    return user, proj


def _auth_token(user: User) -> str:
    from app.services.auth_service import create_access_token
    return create_access_token({"sub": str(user.id)})


def _make_log_event(proj: Project, *, level: LogLevel = LogLevel.ERROR, message: str = "boom") -> LogEvent:
    return LogEvent(
        project_id=proj.id, level=level,
        message=message, logger_name="app.x", version_sha="a" * 40,
        environment="production", hostname="h",
        emitted_at=datetime.utcnow(), received_at=datetime.utcnow(),
        exception_class="KeyError", exception_message="x",
    )


async def test_list_logs_normal(client_with_db, async_session: AsyncSession):
    """GET /logs 정상 — items + total."""
    user, proj = await _seed_user_project(async_session)
    e = _make_log_event(proj)
    async_session.add(e)
    await async_session.commit()

    token = _auth_token(user)
    res = await client_with_db.get(
        f"/api/v1/projects/{proj.id}/logs",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["total"] == 1
    assert len(body["items"]) == 1


async def test_list_logs_404_for_non_member(
    client_with_db, async_session: AsyncSession,
):
    user, proj = await _seed_user_project(async_session)
    other = User(
        email=f"o-{uuid.uuid4().hex[:8]}@example.com",
        name="bob", password_hash="x",
    )
    async_session.add(other)
    await async_session.commit()
    await async_session.refresh(other)

    token = _auth_token(other)
    res = await client_with_db.get(
        f"/api/v1/projects/{proj.id}/logs",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 404


async def test_list_logs_q_full_text(client_with_db, async_session: AsyncSession):
    """q 풀텍스트 — level >= WARNING 자동."""
    user, proj = await _seed_user_project(async_session)
    e1 = _make_log_event(proj, level=LogLevel.ERROR, message="contains needle here")
    e2 = _make_log_event(proj, level=LogLevel.INFO, message="info with needle")  # 제외 — INFO
    e3 = _make_log_event(proj, level=LogLevel.ERROR, message="no match")  # 제외 — q 매칭 X
    async_session.add_all([e1, e2, e3])
    await async_session.commit()

    token = _auth_token(user)
    res = await client_with_db.get(
        f"/api/v1/projects/{proj.id}/logs",
        params={"q": "needle"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["total"] == 1
    assert body["items"][0]["message"] == "contains needle here"
```

- [ ] **Step 2: Verify failure**

```bash
cd backend && source venv/bin/activate
pytest tests/test_log_errors_endpoint.py tests/test_log_logs_endpoint.py -v 2>&1 | tail -15
```

Expected: 8 FAIL (5 + 3) — endpoint 404.

- [ ] **Step 3: Endpoint 구현**

`backend/app/api/v1/endpoints/log_errors.py` 신규:

```python
"""GET /errors, GET /errors/{group_id} — ErrorGroup 조회.

설계서: 2026-05-01-error-log-phase4-query-design.md §3.3
프로젝트 멤버 권한 (VIEWER 포함).
"""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import CurrentUser
from app.models.error_group import ErrorGroupStatus
from app.schemas.log_query import (
    ErrorGroupDetail,
    ErrorGroupListResponse,
    ErrorGroupSummary,
    GitContextBundle,
    GitContextWrapper,
    GitPushEventRef,
    HandoffRef,
    LogEventSummary,
    TaskRef,
)
from app.services import log_query_service, project_service
from app.services.permission_service import get_effective_role

router = APIRouter(prefix="/projects", tags=["log-errors"])


@router.get(
    "/{project_id}/errors",
    response_model=ErrorGroupListResponse,
)
async def list_errors(
    project_id: UUID,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
    status: ErrorGroupStatus | None = None,
    since: datetime | None = None,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
):
    """ErrorGroup 목록. 멤버 누구나 (VIEWER 포함)."""
    project = await project_service.get_project(db, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    role = await get_effective_role(db, user.id, project_id)
    if role is None:
        raise HTTPException(status_code=404, detail="Project not found")

    rows, total = await log_query_service.list_groups(
        db, project_id=project_id,
        status=status, since=since, offset=offset, limit=limit,
    )
    return ErrorGroupListResponse(
        items=[ErrorGroupSummary.model_validate(r) for r in rows],
        total=total,
    )


@router.get(
    "/{project_id}/errors/{group_id}",
    response_model=ErrorGroupDetail,
)
async def get_error_detail(
    project_id: UUID,
    group_id: UUID,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
):
    """ErrorGroup 상세 + recent events + git 컨텍스트 + 직전 정상 SHA."""
    project = await project_service.get_project(db, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    role = await get_effective_role(db, user.id, project_id)
    if role is None:
        raise HTTPException(status_code=404, detail="Project not found")

    detail = await log_query_service.get_group_detail(
        db, project_id=project_id, group_id=group_id,
    )
    if detail is None:
        raise HTTPException(status_code=404, detail="Error group not found")

    git_ctx = detail["git_context"]
    return ErrorGroupDetail(
        group=ErrorGroupSummary.model_validate(detail["group"]),
        recent_events=[LogEventSummary.model_validate(e) for e in detail["recent_events"]],
        git_context=GitContextWrapper(
            first_seen=GitContextBundle(
                handoffs=[HandoffRef.model_validate(h) for h in git_ctx["first_seen"]["handoffs"]],
                tasks=[TaskRef.model_validate(t) for t in git_ctx["first_seen"]["tasks"]],
                git_push_event=(
                    GitPushEventRef.model_validate(git_ctx["first_seen"]["git_push_event"])
                    if git_ctx["first_seen"]["git_push_event"] else None
                ),
            ),
            previous_good_sha=git_ctx["previous_good_sha"],
        ),
    )
```

`backend/app/api/v1/endpoints/log_logs.py` 신규:

```python
"""GET /logs — LogEvent raw 조회 + pg_trgm 풀텍스트.

설계서: 2026-05-01-error-log-phase4-query-design.md §3.3
"""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import CurrentUser
from app.models.log_event import LogLevel
from app.schemas.log_query import LogEventListResponse, LogEventSummary
from app.services import log_query_service, project_service
from app.services.permission_service import get_effective_role

router = APIRouter(prefix="/projects", tags=["log-logs"])


@router.get(
    "/{project_id}/logs",
    response_model=LogEventListResponse,
)
async def list_logs(
    project_id: UUID,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
    level: LogLevel | None = None,
    since: datetime | None = None,
    q: str | None = Query(default=None, min_length=2, max_length=200),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
):
    """LogEvent 목록. q (풀텍스트) 사용 시 자동 level >= WARNING 필터."""
    project = await project_service.get_project(db, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    role = await get_effective_role(db, user.id, project_id)
    if role is None:
        raise HTTPException(status_code=404, detail="Project not found")

    rows, total = await log_query_service.list_logs(
        db, project_id=project_id,
        level=level, since=since, q=q, offset=offset, limit=limit,
    )
    return LogEventListResponse(
        items=[LogEventSummary.model_validate(r) for r in rows],
        total=total,
    )
```

- [ ] **Step 4: Router 마운트**

`backend/app/api/v1/router.py` 변경:

```python
# 기존 imports 다음에 추가:
from app.api.v1.endpoints.log_errors import router as log_errors_router
from app.api.v1.endpoints.log_logs import router as log_logs_router

# 기존 include 들 다음 (log_ingest_router 다음 권장):
api_v1_router.include_router(log_errors_router)
api_v1_router.include_router(log_logs_router)
```

- [ ] **Step 5: Verify pass + 회귀**

```bash
cd backend && source venv/bin/activate
pytest tests/test_log_errors_endpoint.py tests/test_log_logs_endpoint.py -v 2>&1 | tail -15
pytest -q 2>&1 | tail -3
```

Expected: 8 신규 PASS, 전체 `275 passed` (267 + 8).

- [ ] **Step 6: Commit**

```bash
cd /Users/arden/Documents/ardensdevspace/pslog/.worktrees/error-log-phase4-query
git add backend/app/api/v1/endpoints/log_errors.py backend/app/api/v1/endpoints/log_logs.py backend/app/api/v1/router.py backend/tests/test_log_errors_endpoint.py backend/tests/test_log_logs_endpoint.py
git commit -m "$(cat <<'EOF'
feat(error-log/phase4): 3 GET endpoints — /errors, /errors/{id}, /logs

- log_errors.py: GET /errors (목록 + status/since), GET /errors/{group_id} (상세 + nested git_context)
- log_logs.py: GET /logs (level/since/q + pg_trgm partial 자동 활용)
- 권한: 프로젝트 멤버 (VIEWER 포함, 운영 투명성)
- 404: 비-멤버 / 비-존재 group / 다른 project group
- 422: q min_length=2, limit ge/le, offset ge=0 (Pydantic 자동)
- 회귀 8건: errors 5 (정상 / 비-멤버 404 / VIEWER OK / 상세 정상 / 다른 project 404), logs 3 (정상 / 비-멤버 404 / q 풀텍스트)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: 최종 회귀 + handoff + PR

- [ ] **Step 1: 전체 backend 회귀**

```bash
cd /Users/arden/Documents/ardensdevspace/pslog/.worktrees/error-log-phase4-query/backend
source venv/bin/activate
pytest -q 2>&1 | tail -3
```

Expected: `275 passed` (256 baseline + 19 신규: 11 service + 5 errors endpoint + 3 logs endpoint).

- [ ] **Step 2: Frontend 영향 없음** — 변경 없음, skip.

- [ ] **Step 3: handoff 갱신**

`handoffs/main.md` 상단에 새 섹션:

```markdown
## 2026-05-01 (Error-log Phase 4 — Query API + Git Context Join)

- [x] **Error-log Phase 4** — 브랜치 `feature/error-log-phase4-query`
  - [x] **`log_query_service`**: 5 함수 — list_groups (status/since 필터, offset/limit + total) / get_group_detail (group + recent 50 events + nested git_context + previous_good_sha) / list_logs (level/since/q + pg_trgm) / _find_previous_good_sha (LEFT JOIN + IS NULL 패턴) / _collect_git_context (3 단일 SQL — handoffs/tasks/push_events).
  - [x] **`GET /api/v1/projects/{id}/errors`**: ErrorGroup 목록 + 필터 (status / since) + offset/limit. 멤버 권한 (VIEWER 포함).
  - [x] **`GET /api/v1/projects/{id}/errors/{group_id}`**: 상세 + recent events + git 컨텍스트 (nested first_seen + previous_good_sha). 다른 project 404.
  - [x] **`GET /api/v1/projects/{id}/logs`**: LogEvent raw + level/since/q. q 지정 시 자동 level >= WARNING 강제 (Phase 1 의 idx_log_message_trgm partial 활용). q + level 동시 시 q 우선.
  - [x] **archived task 포함** (spec §4.2): UI 가 후속 (archived) 배지 표시.
  - [x] **마이그레이션 신규 없음** — Phase 1 alembic 의 모든 모델 + 인덱스 활용.
  - [x] **검증**: backend **275 tests pass** (256 baseline + 19 신규: 11 service + 5 errors endpoint + 3 logs endpoint).

### 마지막 커밋

- pslog: `<sha> docs(handoff+plan): Error-log Phase 4 완료 + Phase 5/6 다음 할 일`
- 브랜치 base: `0f1cb10` (main, Error-log Phase 3 PR #17 머지 직후)

### 다음 (Phase 5 — UI)

- LogsPage / ErrorsPage / ErrorDetailPage / GitContextPanel / LogTokensPage / LogHealthBadge
- PATCH /errors/{group_id} (사용자 status 전이 — resolve/ignore/reopen) + 권한 OWNER
- GET /log-tokens 목록 endpoint (UI 가 호출)
- GET /log-health (unknown SHA 비율 모니터링)

또는 Phase 6 (알림 본편 — spike/regression) — 사용자 dogfooding 후 결정.

### 블로커

없음

### 메모 (2026-05-01 Error-log Phase 4 추가)

- **직전 정상 SHA = LEFT JOIN + IS NULL 패턴**: 단일 SQL 로 같은 environment 의 target_fp 가 발생 안 했던 가장 최근 SHA 찾음. SQLAlchemy `__table__.alias()` 로 self-join. 2-step Python filter 대비 효율 ↑.
- **Git 컨텍스트 3 단일 SQL**: `IN (version_shas)` 로 handoffs/tasks/push_events bulk fetch. nested 응답 1 round-trip. archived task 포함 (spec §4.2 — UI 가 후속 (archived) 배지).
- **pg_trgm partial index 활용**: `idx_log_message_trgm WHERE level >= WARNING` (Phase 1 alembic). q 지정 시 자동 WARNING+ 강제 — 인덱스 partial WHERE 매칭. q + level 동시 시 q 우선 (단순화).
- **environment 필터 v1 미포함**: ErrorGroup 자체엔 environment 컬럼 없음 (같은 fingerprint 가 여러 env 발생 가능). 후속 호소 시 EXISTS subquery 또는 컬럼 추가.
- **VIEWER 권한 조회 가능**: 운영 투명성 — 에러 정보는 일반 사용자에게도 노출 가치 있음. PATCH (Phase 5) 만 OWNER.
- **previous_good_sha 의 environment**: first_event 의 environment 사용. ErrorGroup 자체엔 env 컬럼 없으므로 events 에서 추출.
- **`__table__.alias()` 패턴**: SQLAlchemy 2.0 의 self-join 표준 패턴. ORM model 이 아닌 table 객체를 alias 해서 사용. raw SQL 안 쓰고도 LEFT JOIN + IS NULL 가능.
- **next 가능 옵션**: Phase 5 (UI 통합 — 대규모 frontend phase) 또는 Phase 6 (알림 본편). dogfooding 으로 사용자 우선순위 평가.
```

- [ ] **Step 4: handoff + plan commit**

```bash
cd /Users/arden/Documents/ardensdevspace/pslog/.worktrees/error-log-phase4-query
git add handoffs/main.md docs/superpowers/plans/2026-05-01-error-log-phase4-query.md
git commit -m "$(cat <<'EOF'
docs(handoff+plan): Error-log Phase 4 완료 + Phase 5/6 다음 할 일

- handoffs/main.md 에 2026-05-01 Error-log Phase 4 섹션 추가 (275 tests)
- docs/superpowers/plans/2026-05-01-error-log-phase4-query.md 신규 (구현 plan 보존)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 5: Push + PR**

```bash
git push -u origin feature/error-log-phase4-query
gh pr create \
  --title "feat(error-log/phase4): query API + git context join + pg_trgm 풀텍스트" \
  --body "$(cat <<'EOF'
## Summary

error-log spec Phase 4 — Phase 3 가 채운 ErrorGroup / LogEvent 데이터를 사용자에게 노출. 3 GET endpoint (errors 목록, errors 상세 + git 컨텍스트 + 직전 정상 SHA, logs raw + pg_trgm 풀텍스트). UI 없이 curl 만으로 검증 가능한 핵심 가치 단계.

- **\`log_query_service\`** 5 함수: list_groups / get_group_detail / list_logs / _find_previous_good_sha / _collect_git_context
- **\`GET /errors\`**: 목록 + status/since 필터 + offset/limit + total
- **\`GET /errors/{group_id}\`**: 상세 + recent 50 events + nested git_context (handoffs/tasks/push_events) + previous_good_sha
- **\`GET /logs\`**: level/since/q 필터, q 지정 시 자동 level >= WARNING (pg_trgm partial 활용)
- **권한**: 프로젝트 멤버 (VIEWER 포함, 운영 투명성)
- **archived task 포함** (spec §4.2)
- **마이그레이션 신규 없음** — Phase 1 alembic 의 모든 모델 + 인덱스 활용
- **environment 필터 v1 미포함** (ErrorGroup 컬럼 없음)

## Test plan

- [x] backend **275 tests pass** (256 baseline + 19 신규: 11 service + 5 errors endpoint + 3 logs endpoint)
- [ ] e2e — 사용자 직접 (Phase 3 의 dogfooding 데이터 활용 또는 의도적 logger.error 후):
  - curl \`GET /errors\` — 목록 응답 + total
  - curl \`GET /errors/{id}\` — 상세 + git_context (handoff/task/push_event) + previous_good_sha
  - curl \`GET /logs?q=KeyError\` — pg_trgm 풀텍스트 동작
  - curl \`GET /logs?level=ERROR\` — level 필터

## 다음 (Phase 5 또는 Phase 6)

- Phase 5 (UI): LogsPage / ErrorsPage / ErrorDetailPage / GitContextPanel / LogTokensPage / LogHealthBadge + PATCH /errors/{id} (사용자 status 전이) + GET /log-tokens 목록 + GET /log-health
- Phase 6 (알림 본편): spike (메모리 카운터) + regression 알림. error_group_service.upsert 의 transitioned_to_regression 신호 사용.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Self-Review Pass

**1. Spec coverage** — spec §1-§7 vs plan tasks 매핑:

| Spec 항목 | Plan task |
|---|---|
| §3.1 log_query_service 5 함수 | Task 2 (list_groups) + Task 3 (get_group_detail + 2 helpers) + Task 4 (list_logs) |
| §3.2 Pydantic schemas | Task 1 |
| §3.3 3 endpoints | Task 5 |
| §2.1 페이지네이션 offset/limit | Task 5 (Pydantic ge/le) + Task 2/4 (service 시그니처) |
| §2.2 직전 정상 SHA SQL | Task 3 (`_find_previous_good_sha`) |
| §2.3 Git 컨텍스트 3 SQL | Task 3 (`_collect_git_context`) |
| §2.4 pg_trgm 풀텍스트 | Task 4 (list_logs 의 q 파라미터) |
| §2.5 필터 v1 (status/since/level/since/q) | Task 2 + 4 |
| §2.6 권한 (멤버, VIEWER 포함) | Task 5 |
| §2.7 응답 미포함 (stack_trace) | Task 1 (LogEventSummary 에 제외) |
| §3.4 router 통합 | Task 5 |

**2. Placeholder scan** — `<sha>` 만 (Task 6 handoff commit 후 자리표시). "TBD/TODO" 0.

**3. Type / signature consistency**:
- `list_groups(db, *, project_id, status?, since?, offset, limit) -> tuple[list[ErrorGroup], int]` — Task 2 ↔ Task 5 endpoint 호출 일관
- `get_group_detail(db, *, project_id, group_id) -> dict | None` — Task 3 ↔ Task 5 일관
- `list_logs(db, *, project_id, level?, since?, q?, offset, limit) -> tuple[list[LogEvent], int]` — Task 4 ↔ Task 5 일관
- `_find_previous_good_sha(db, *, project_id, environment, target_fingerprint, before_received_at) -> str | None` — Task 3 ↔ get_group_detail 호출 일관
- `_collect_git_context(db, *, project_id, version_shas) -> dict` — Task 3 ↔ get_group_detail 호출 일관
- Pydantic `ErrorGroupSummary / ErrorGroupDetail / LogEventSummary / GitContextWrapper / GitContextBundle / HandoffRef / TaskRef / GitPushEventRef` — Task 1 정의 ↔ Task 5 endpoint 사용 일관

**4. 의존 순서**:
- Task 1 (schemas) → Task 2 (list_groups, schemas 사용 X — 단 Task 5 의 endpoint 가 사용) → Task 3 (get_group_detail) → Task 4 (list_logs) → Task 5 (endpoint, Task 1 + 2 + 3 + 4 모두 사용) → Task 6 (PR)

**5. 테스트 결정성**:
- list_groups / list_logs — 단순 SQL, 결정적
- get_group_detail — git context join 결정적
- _find_previous_good_sha — LEFT JOIN + IS NULL, 결정적
- endpoint — client_with_db fixture, 결정적

**6. spec 의 §4.1 test count (19) vs plan task 별 합계**:
- Task 2: 4 (list_groups)
- Task 3: 4 (get_group_detail)
- Task 4: 3 (list_logs)
- Task 5: 5 (errors endpoint) + 3 (logs endpoint) = 8
- 합계: 19 ✓

**7. spec 의 결정 사항 모두 반영**:
- offset/limit ✓
- 단일 SQL 직전 정상 SHA ✓
- nested git_context 응답 ✓
- VIEWER 권한 ✓
- archived task 포함 ✓
- environment 필터 v1 미포함 ✓
- q + level 동시 시 q 우선 ✓
- 마이그레이션 신규 없음 ✓

문제 없음. 진행 가능.
