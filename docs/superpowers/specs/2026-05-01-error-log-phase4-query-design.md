# Error-log Phase 4 — Query API + Git Context Join (Design)

**Status**: Draft → 사용자 검토 후 implementation plan 작성 (`writing-plans`).

**Date**: 2026-05-01

**Goal**: error-log spec Phase 4 — Phase 3 가 채운 ErrorGroup / LogEvent 데이터를 사용자에게 노출. 3 GET endpoint (errors 목록, errors 상세 + git 컨텍스트 join + 직전 정상 SHA, logs raw 조회 + pg_trgm 풀텍스트). UI 없이 curl 만으로도 검증 가능한 핵심 가치 단계.

**선행**: pslog `main` = `0f1cb10` (Error-log Phase 3 PR #17 머지 직후). backend tests 256 baseline. 마이그레이션 신규 없음 — Phase 1 alembic 의 모든 모델 + 인덱스 (`idx_log_message_trgm` partial pg_trgm 포함) 활용.

---

## 1. Scope

본 phase 의 deliverable:

1. **`log_query_service`** 신규 — 5 함수 (list_groups, get_group_detail, list_logs, _find_previous_good_sha, _collect_git_context)
2. **3 GET endpoints** (모두 프로젝트 멤버 권한):
   - `GET /api/v1/projects/{id}/errors` — ErrorGroup 목록 + 필터 + offset/limit
   - `GET /api/v1/projects/{id}/errors/{group_id}` — 상세 + recent events + git 컨텍스트 + 직전 정상 SHA
   - `GET /api/v1/projects/{id}/logs` — LogEvent raw 조회 + pg_trgm 풀텍스트 (q 파라미터)
3. **Pydantic schemas** (`backend/app/schemas/log_query.py`): ErrorGroupSummary / ErrorGroupDetail / LogEventSummary / GitContextBundle / 작은 ref schemas

본 phase 가 **하지 않는** 것:
- `PATCH /errors/{group_id}` (사용자 status 전이) — Phase 5 UI 와 함께
- `GET /log-tokens` 목록 (현재 발급 시 응답에서 id 받음) — Phase 5 UI 와 함께
- `GET /log-health` (unknown SHA 비율) — Phase 5/6 통합
- `GET /log-summary` (Gemma 회고) — Phase 8
- Frontend (LogsPage / ErrorsPage / ErrorDetailPage / GitContextPanel / LogTokensPage) — Phase 5 통합 phase
- spike / regression 알림 — Phase 6 본편
- 마이그레이션 / 모델 변경

본 phase 머지 후 e2e 가능: app-chak logger.error → pslog DB ingest + fingerprint + group → curl `GET /errors` 로 그룹 목록 / `GET /errors/{id}` 로 상세 + git 컨텍스트 / `GET /logs?q=...` 로 메시지 검색.

---

## 2. Important Contracts

### 2.1. 페이지네이션 — offset/limit

모든 list endpoint:
- `?offset=0&limit=50` (errors) / `?offset=0&limit=100` (logs)
- 응답: `{items: [...], total: int}` — `total` 은 별도 COUNT(*) (필터 적용된 후)
- limit clamp: errors 1~200, logs 1~500
- v1 데이터셋 작아 offset 비용 무시. 후속 호소 시 cursor 도입.

### 2.2. 직전 정상 SHA 알고리즘

목적: ErrorGroup 의 `first_seen_version_sha` 가 처음 발생한 SHA. 그 직전 (시간순) 의 "정상" SHA = 같은 environment 에서 이 fingerprint 가 *없었던* 가장 최근 SHA.

**SQL (단일 query)**:

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
  AND le_target.id IS NULL  -- 이 SHA 에서 target_fingerprint 발생 안 함 = 정상
ORDER BY le.received_at DESC
LIMIT 1
```

`before_received_at` = `ErrorGroup.first_seen_at`. 결과 없으면 `None` (이 fingerprint 가 pslog 가 받은 모든 로그의 가장 처음부터 있었음).

`environment` 도 일치 — 다른 환경의 SHA 는 무관.

### 2.3. Git 컨텍스트 join (3 단일 SQL)

`get_group_detail` 내부 — recent events (최대 50건) 의 `version_sha` 집합 추출 후 3 IN 쿼리:

```python
shas = {
    e.version_sha for e in recent_events
    if e.version_sha != "unknown"
}

handoffs = await db.execute(
    select(Handoff).where(
        Handoff.project_id == project_id,
        Handoff.commit_sha.in_(shas),
    )
)
tasks = await db.execute(
    select(Task).where(
        Task.project_id == project_id,
        Task.last_commit_sha.in_(shas),
    )
    # archived 포함 (spec §4.2 — UI 가 (archived) 배지)
)
push_events = await db.execute(
    select(GitPushEvent).where(
        GitPushEvent.project_id == project_id,
        GitPushEvent.head_commit_sha.in_(shas),
    )
)
```

응답 구조 — nested `git_context` (single round-trip):

```json
{
  "group": {...},
  "recent_events": [...],
  "git_context": {
    "first_seen": {
      "handoffs": [{commit_sha, branch, author, pushed_at}],
      "tasks": [{external_id, title, status, last_commit_sha, archived_at}],
      "git_push_event": {head_commit_sha, branch, pusher, received_at} | null
    },
    "previous_good_sha": "abc123..." | null
  }
}
```

`first_seen` 이라는 key 이름이지만 실제로는 recent events 의 모든 SHA join 결과 — UI 가 first event 와 매칭. `unknown` SHA 또는 join 결과 0 의 빈 array — UI 가 "git 동기화 데이터 없음" 표시.

### 2.4. pg_trgm 풀텍스트

`GET /logs?q=substring`:

```sql
SELECT * FROM log_events
WHERE project_id = ?
  AND level >= 'WARNING'  -- pg_trgm 인덱스가 WARNING+ 만 (Phase 1)
  AND message ILIKE '%' || :q || '%'
ORDER BY received_at DESC
LIMIT ? OFFSET ?
```

`message gin_trgm_ops` 인덱스 (Phase 1 alembic) 가 자동으로 활용됨. `level >= WARNING` 필터는 인덱스 partial WHERE 와 매칭. INFO/DEBUG 는 인덱스 없어 풀스캔되므로 풀텍스트 불가 (의도된 — 디스크 절감, spec §4.1).

`q` 가 없으면 ILIKE 안 함, level 필터만.

### 2.5. 필터 v1 scope

**`GET /errors`**:
- `status: ErrorGroupStatus` (OPEN / RESOLVED / IGNORED / REGRESSED) — 다중 가능 시 후속, 본 phase 는 단일
- `since: datetime` (ISO 8601, last_seen_at >= since)

기본: 모든 status, since=None.

**`environment` 필터 미포함 (v1)** — `ErrorGroup` 자체엔 environment 컬럼 없음 (spec §4.1, 같은 fingerprint 가 여러 environment 에서 발생 가능). UI 호소 시 EXISTS subquery 또는 컬럼 추가 후속.

**`GET /logs`**:
- `level: LogLevel` (단일 또는 그 이상 — `level >= ?`)
- `since: datetime` (received_at >= since)
- `q: str` (pg_trgm 풀텍스트, level >= WARNING 자동 적용)

기본: 모든 level, since=None, q=None.

**미포함 필터** (후속): branch / assignee / version_sha / hostname / 다중 status 등.

### 2.6. 권한

- 모든 3 endpoint — **프로젝트 멤버** (`get_effective_role(db, user.id, project_id) is not None`)
- 비-멤버 → 404 (spec 패턴)
- VIEWER 도 조회 가능 — 에러 정보는 일반 사용자에게도 노출 가치 있음 (운영 투명성)

### 2.7. 응답 미포함 필드

`raw_content` 류 큰 필드는 spec §4.1 에 LogEvent 에 직접 없음 — 모든 필드 노출 OK. 단 `stack_trace` 가 매우 길 수 있어 `LogEventSummary` 는 short 버전 (stack_trace 제외), 상세에서만 포함:

- `LogEventSummary`: id / level / message (truncated) / version_sha / environment / hostname / emitted_at / received_at / fingerprint
- `LogEventDetail` (현재 phase 안 — 상세 endpoint 만들면 추가, v1 은 group_detail.recent_events 에서만 LogEventSummary)

상세 endpoint `GET /logs/{event_id}` 는 본 phase 미포함 (YAGNI — UI 가 호소 시 후속).

---

## 3. Backend Architecture

### 3.1. `log_query_service.py` 신규

```python
"""LogEvent / ErrorGroup 조회 + Git 컨텍스트 join.

설계서: 2026-05-01-error-log-phase4-query-design.md §2, §3
spec §6.2 의 데이터 흐름 그대로.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.error_group import ErrorGroup, ErrorGroupStatus
from app.models.git_push_event import GitPushEvent
from app.models.handoff import Handoff
from app.models.log_event import LogEvent, LogLevel
from app.models.task import Task

_RECENT_EVENTS_LIMIT = 50


async def list_groups(
    db: AsyncSession,
    *,
    project_id: UUID,
    status: ErrorGroupStatus | None = None,
    environment: str | None = None,
    since: datetime | None = None,
    offset: int = 0,
    limit: int = 50,
) -> tuple[list[ErrorGroup], int]:
    """ErrorGroup 목록. 필터 + offset/limit. last_seen_at desc."""
    base = select(ErrorGroup).where(ErrorGroup.project_id == project_id)
    count_base = select(func.count()).select_from(ErrorGroup).where(
        ErrorGroup.project_id == project_id
    )

    if status is not None:
        base = base.where(ErrorGroup.status == status)
        count_base = count_base.where(ErrorGroup.status == status)
    # environment 필터 미포함 — §2.5 참조 (ErrorGroup 컬럼 없음, v1 단순화)
    if since is not None:
        base = base.where(ErrorGroup.last_seen_at >= since)
        count_base = count_base.where(ErrorGroup.last_seen_at >= since)

    base = base.order_by(ErrorGroup.last_seen_at.desc()).offset(offset).limit(limit)

    rows = (await db.execute(base)).scalars().all()
    total = (await db.execute(count_base)).scalar_one()
    return list(rows), total
```

```python
async def _find_previous_good_sha(
    db: AsyncSession,
    *,
    project_id: UUID,
    environment: str,
    target_fingerprint: str,
    before_received_at: datetime,
) -> str | None:
    """직전 정상 SHA — §2.2 SQL."""
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
    """3 단일 SQL — handoffs / tasks / push_events join."""
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

    # spec §6.2: "first_seen" 영역의 git_push_event 는 단일 항목 (received_at 오름차순 첫 번째)
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

    # 최근 N events
    events_stmt = (
        select(LogEvent)
        .where(LogEvent.fingerprint == group.fingerprint)
        .where(LogEvent.project_id == project_id)
        .order_by(LogEvent.received_at.desc())
        .limit(_RECENT_EVENTS_LIMIT)
    )
    recent_events = (await db.execute(events_stmt)).scalars().all()

    # version_sha 집합 (unknown 제외)
    shas = {
        e.version_sha for e in recent_events
        if e.version_sha and e.version_sha != "unknown"
    }
    git_context = await _collect_git_context(
        db, project_id=project_id, version_shas=shas,
    )

    # 직전 정상 SHA — first event 의 environment 사용
    previous_good_sha: str | None = None
    if recent_events:
        first_event = min(recent_events, key=lambda e: e.received_at)
        # group.first_seen_at 이 정확한 시점 — first_event.received_at 이 더 정확하면 그것
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
    """
    base = select(LogEvent).where(LogEvent.project_id == project_id)
    count_base = select(func.count()).select_from(LogEvent).where(
        LogEvent.project_id == project_id
    )

    if level is not None:
        # level >= ? — enum 비교 (LogLevel 의 순서가 spec 과 일치 — DEBUG/INFO/WARNING/ERROR/CRITICAL)
        # SQLAlchemy enum 비교 — spec 패턴 단순화: level == ? 로 v1 (등호 매칭)
        base = base.where(LogEvent.level == level)
        count_base = count_base.where(LogEvent.level == level)
    if since is not None:
        base = base.where(LogEvent.received_at >= since)
        count_base = count_base.where(LogEvent.received_at >= since)
    if q:
        # pg_trgm 인덱스 활용 — level >= WARNING 자동 강제 (인덱스 partial WHERE)
        warning_or_higher = LogEvent.level.in_(
            [LogLevel.WARNING, LogLevel.ERROR, LogLevel.CRITICAL]
        )
        base = base.where(warning_or_higher).where(LogEvent.message.ilike(f"%{q}%"))
        count_base = count_base.where(warning_or_higher).where(
            LogEvent.message.ilike(f"%{q}%")
        )

    base = base.order_by(LogEvent.received_at.desc()).offset(offset).limit(limit)

    rows = (await db.execute(base)).scalars().all()
    total = (await db.execute(count_base)).scalar_one()
    return list(rows), total
```

### 3.2. Pydantic schemas (`backend/app/schemas/log_query.py`)

```python
"""log query API 의 Pydantic schemas.

설계서: 2026-05-01-error-log-phase4-query-design.md §3.2
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict


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


class ErrorGroupDetail(BaseModel):
    group: ErrorGroupSummary
    recent_events: list[LogEventSummary]
    git_context: GitContextWrapper
```

### 3.3. Endpoints

`backend/app/api/v1/endpoints/log_errors.py` (신규):

```python
"""GET /errors, GET /errors/{group_id} — ErrorGroup 조회.

설계서: 2026-05-01-error-log-phase4-query-design.md §2, §3
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
    ErrorGroupDetail, ErrorGroupListResponse, ErrorGroupSummary,
    GitContextBundle, GitContextWrapper, GitPushEventRef, HandoffRef,
    LogEventSummary, TaskRef,
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
    """ErrorGroup 목록. 멤버 누구나."""
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

`backend/app/api/v1/endpoints/log_logs.py` (신규):

```python
"""GET /logs — LogEvent raw 조회 + pg_trgm 풀텍스트.

설계서: 2026-05-01-error-log-phase4-query-design.md §2.4
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

### 3.4. Router 통합 (`backend/app/api/v1/router.py`)

```python
from app.api.v1.endpoints.log_errors import router as log_errors_router
from app.api.v1.endpoints.log_logs import router as log_logs_router

# 기존 include 들 다음:
api_v1_router.include_router(log_errors_router)
api_v1_router.include_router(log_logs_router)
```

### 3.5. 응답 미포함 / 단순화 결정

- **`environment` 필터 미포함 (`GET /errors`)** — ErrorGroup 자체엔 environment 컬럼 없음. v1 단순화. UI 호소 시 ErrorGroup 에 environment 컬럼 추가 또는 EXISTS join.
- **`level` 필터는 단일 값 매칭** (`level == ?`) — `level >= ?` 의미는 v1 미적용. spec 의 "level/시간/브랜치/메시지 검색" 과 일부 차이. 후속 호소 시 multi-select.
- **`q` 풀텍스트 자동 `level >= WARNING`** — pg_trgm partial index 활용. `q` 와 `level` 동시 지정 시 — 사용자가 explicit `level=INFO` 등 지정해도 `q` 가 있으면 강제 WARNING+ (인덱스 활용 + spec §4.1). 충돌 시 400 vs 무시 — **무시 + 200 + WARNING+ 결과** (단순).
- **상세 endpoint `GET /logs/{event_id}` 미포함** — UI 가 LogEventSummary 만으로 부족하면 후속 추가. v1 은 group_detail 의 recent_events 에서 LogEventSummary.

---

## 4. Test Plan

### 4.1. Backend 신규 (예상 +19 tests, 256 → 275)

**`test_log_query_service.py`** (11건):
- `list_groups` — 필터 없음 / status filter / since filter / pagination total 정확성 (4건)
- `get_group_detail` — 정상 path / unknown SHA 만 (git context 빈) / previous_good_sha 알고리즘 정확 / group 다른 project (None return) (4건)
- `list_logs` — level filter / since filter / q (pg_trgm) (3건)

**`test_log_errors_endpoint.py`** (5건):
- `GET /errors` 정상 + 비-멤버 404 + VIEWER 권한 OK (3건)
- `GET /errors/{group_id}` 정상 + group 다른 project 404 (2건)

**`test_log_logs_endpoint.py`** (3건):
- `GET /logs` 정상 / 비-멤버 404 / q 풀텍스트 (3건)

### 4.2. Frontend

본 phase backend only. 변경 없음.

### 4.3. e2e (사용자, PR 머지 전)

- pslog dev server + Phase 3 의 ErrorGroup 데이터 (이전 dogfooding 시 쌓인 것 또는 의도적 logger.error)
- curl `GET /errors` — 목록 응답 구조 + total count
- curl `GET /errors/{group_id}` — 상세 + git_context (handoff/task/push_event lookup) + previous_good_sha
- curl `GET /logs?q=KeyError` — pg_trgm 풀텍스트 동작
- curl `GET /logs?level=ERROR&since=...` — 시간 필터

---

## 5. Decision Log

- **Scope = A (3 endpoint full)**: spec §11 의 Phase 4 명세 그대로. PATCH 와 GET /log-tokens 목록은 Phase 5 UI 와 함께. log-health 는 Phase 5/6.
- **페이지네이션 = offset/limit (옵션 A)**: v1 데이터셋 작아 (project 당 group 수백~수천) offset 비용 무시. cursor 후속.
- **직전 정상 SHA = 단일 SQL (옵션 A)**: LEFT JOIN + IS NULL 패턴. 빠름. 2-step Python filter 대신.
- **필터 v1 = status / since (옵션 A)**: branch / assignee 등 multi-filter 후속. environment 도 v1 미포함 (ErrorGroup 컬럼 없음).
- **`q` 풀텍스트 = 단순 ILIKE (옵션 A)**: pg_trgm gin index 자동 활용. 별도 `/search` endpoint 없이 `/logs?q=...`. q 지정 시 자동 WARNING+ 강제.
- **응답 = nested git_context (옵션 A)**: single round-trip. UI 가 GitContextPanel 한 번에 렌더.
- **권한 = 프로젝트 멤버 (VIEWER 포함)**: 운영 투명성. PATCH 만 OWNER (Phase 5).
- **archived task 포함**: spec §4.2 — UI 가 (archived) 배지로 구분.
- **q + level 충돌**: 무시 + WARNING+ 강제 (단순). 400 reject 안 함.

---

## 6. Phase 5 / Phase 6 와의 관계 (참고)

본 phase 끝나면:
- **Phase 5** (UI): LogsPage 가 `/logs?q=...` 호출. ErrorsPage 가 `/errors?status=OPEN` 호출. ErrorDetailPage 가 `/errors/{id}` 호출 + GitContextPanel 렌더. + LogTokensPage / LogHealthBadge / PATCH /errors 호출 site.
- **Phase 6** (알림 본편): spike (메모리 카운터 + 30분 cooldown) + regression 알림. error_group_service.upsert 의 `transitioned_to_regression` 신호 사용. log_alert_service 에 `notify_spike` / `notify_regression` 추가.

---

## 7. Open Questions

본 phase 진입 전 답할 것 없음. 시각/사용자 검증 후 결정 항목:

1. **environment 필터 추가 여부 (post-Phase 5)** — UI 에서 environment 별 분리 호소 시 ErrorGroup 에 컬럼 추가 또는 EXISTS subquery. v1 미포함.
2. **`level >= ?` semantics (post-Phase 5)** — UI 에서 multi-level select 호소 시 `level >= ?` 또는 multi-select. v1 단일 값 매칭만.
3. **GET /logs/{event_id} 상세** — UI 가 LogEventSummary 만으로 부족 시 (예: stack_trace 표시) 추가. v1 미포함.
