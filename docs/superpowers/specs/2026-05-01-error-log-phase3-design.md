# Error-log Phase 3 — Fingerprint + ErrorGroup + Reaper + B-lite Alert (Design)

**Status**: Draft → 사용자 검토 후 implementation plan 작성 (`writing-plans`).

**Date**: 2026-05-01

**Goal**: error-log spec Phase 3 (fingerprint + ErrorGroup) + Phase 2b 의 reaper 합본 + **B-lite scope** 의 신규 fingerprint Discord 알림 1종. Phase 2a 가 깐 raw LogEvent 인입 위에 그룹화 / 자동 status 전이 / 재발 감지 / 사용자 알림까지 — task-automation 의 핵심 가치 (에러 ↔ git 컨텍스트 추적) 의 그룹화 절반 완성.

**선행**: pslog `main` = `90cce78` (Error-log Phase 2a PR #16 머지 직후). backend tests 230 baseline. 마이그레이션 신규 없음 — Phase 1 alembic 이 모든 모델 + 인덱스 (`idx_log_unfingerprinted` partial 포함) 이미 포함.

---

## 1. Scope

본 phase 의 deliverable:

1. **`fingerprint_service`** 신규 — 결정적 SHA1 fingerprint 계산. spec §4.1 정규화 6 규칙 (절대경로→상대경로, line 제거, 메모리 주소 마스킹, 함수명 유지, framework/stdlib 스킵, 입력 포맷 + SHA1). stack_frames 비면 fallback.
2. **`error_group_service`** 신규 — `ErrorGroup` UPSERT + 자동 status 전이 (신규→OPEN, RESOLVED→REGRESSED). UNIQUE conflict catch + SAVEPOINT fallback (race-free).
3. **`fingerprint_processor`** 신규 — composition wrapper (fingerprint 계산 → group UPSERT → notify if new → fingerprinted_at = now + commit).
4. **`log_alert_service.notify_new_error`** 신규 (B-lite scope) — 신규 fingerprint 1회 Discord 알림. `notification_dispatcher` 통과 (Phase 6 의 disable 정책 자동 적용). cooldown 단순 (`last_alerted_new_at IS NULL` → 발송 + set).
5. **Ingest endpoint BackgroundTask 통합** — ingest 응답 후 ERROR↑ event 만 `BackgroundTasks.add_task(_process_log_event_in_new_session, event_id)` 큐. ingest_batch 시그니처에 `accepted_event_ids: list[UUID]` 추가.
6. **`log_fingerprint_reaper`** 신규 — 부팅 시 1회. `level >= ERROR AND fingerprinted_at IS NULL` chunked 회수 (100건/batch). lifespan hook 등록 (push_event_reaper 패턴).

본 phase 가 **하지 않는** 것:
- Spike / regression 알림 (Phase 6 — 알림 본편). spike 의 메모리 카운터 + 30분 cooldown burst 정책. regression 알림은 자동 status 전이는 본 phase 에서 (RESOLVED → REGRESSED) 발생 — 알림 발송만 후속.
- 사용자 액션 status 전이 (resolve / ignore / reopen) + PATCH `/errors/{group_id}` endpoint — Phase 5 (UI) 와 함께.
- 조회 endpoint (`GET /errors`, `GET /errors/{group_id}`) — Phase 4.
- log_health_service (unknown SHA 비율 모니터링) — Phase 5/6 통합.
- log_gc_service (파티션 DROP, RateLimitWindow GC) — Phase 7.
- Frontend — Phase 5 통합.

본 phase 머지 후 e2e 가능: app-chak 의 logger.error → pslog `LogEvent` INSERT → BackgroundTask 가 fingerprint + ErrorGroup UPSERT → 신규 fingerprint 면 Discord 알림 1회. dogfooding 즉시.

---

## 2. Important Contracts

### 2.1. fingerprint 정규화 6 규칙 (spec §4.1)

1. **절대경로 → 상대경로**: pslog `.env` 의 `APP_PROJECT_ROOT` (기본 `"backend/"`) 우선 strip. 매칭 안 되면 휴리스틱 — 첫 `backend/` 또는 `src/` segment 찾아 그 이전 strip. 그것도 안 되면 원본 그대로.
2. **line number 제거**: stack_frames 의 `lineno` fingerprint 입력에서 제외.
3. **메모리 주소 마스킹**: regex `0x[0-9a-f]+` → `0xADDR`. 함수명 + exception_message 양쪽 적용.
4. **함수명 유지**: `<lambda>`, `<listcomp>`, `<genexpr>` 도 stable 이라 포함.
5. **framework / stdlib frame 스킵**: 패턴 — `site-packages/`, `dist-packages/`, `lib/python\d`, `asyncio/`, `uvicorn/`, `_bootstrap.py`. 앱 frame 만 카운트해 top 5.
6. **입력 포맷 + SHA1**:
   ```
   <exception_class>|<rel_path1>:<func1>\n<rel_path2>:<func2>\n...
   ```
   `hashlib.sha1(input.encode()).hexdigest()` → 40자.

### 2.2. fingerprint Fallback (spec §7)

- stack_frames None 또는 빈 list → `SHA1(exception_class + "|" + (exception_message or "").split("\n")[0])`
- 정규화 후 앱 frame 0개 (모두 framework) → 가용한 frame top 5 로 정규화 (스킵 무시) — 그룹화 약하지만 ErrorGroup 만들어짐.

### 2.3. ErrorGroup UPSERT 패턴 — race-free

UNIQUE `(project_id, fingerprint)` 가 모델에 있음 (Phase 1 alembic).

**기본 흐름**:
1. `SELECT ... FOR UPDATE WHERE project_id=? AND fingerprint=?` → row 가 있으면 UPDATE 분기, 없으면 INSERT 분기.
2. UPDATE 분기: event_count++, last_seen_at/version_sha 갱신, exception_message_sample 갱신 (가장 최근). status 전이는 `RESOLVED → REGRESSED` 만 자동.
3. INSERT 분기: 신규 ErrorGroup (status=OPEN, first_seen_*=event, last_seen_*=event, event_count=1).

**race condition** (B1 학습 적용):
- 같은 group 의 동시 UPDATE: `with_for_update()` row lock 으로 직렬화.
- 신규 fingerprint 동시 INSERT: SELECT FOR UPDATE 가 row 없으면 lock 안 잡힘 → T1 / T2 둘 다 INSERT 시도 → UNIQUE violation. 해결: `try INSERT (savepoint) → IntegrityError catch → SELECT 후 UPDATE 분기 fallback`. Phase 2 의 `record_push_event` SAVEPOINT 패턴 재사용.

**자동 status 전이** (이 phase):
- 신규 → OPEN (INSERT 분기)
- RESOLVED → REGRESSED (UPDATE 분기, status 갱신)
- OPEN / REGRESSED / IGNORED → status 그대로 (event_count++, last_seen 갱신)

**사용자 액션 전이는 Phase 5 UI** — 본 phase 에 PATCH endpoint 없음.

### 2.4. fingerprint_processor composition

`fingerprint_processor.process(db, event)`:
1. `fingerprint = fingerprint_service.compute(...)` (event 의 exception_class / stack_frames / exception_message 사용)
2. `result = error_group_service.upsert(db, project_id=event.project_id, fingerprint=fingerprint, event=event)` — `GroupResult` 받음
3. `event.fingerprint = fingerprint` + `event.fingerprinted_at = datetime.utcnow()`
4. `await db.commit()` — group + event 변경 영속
5. `if result.is_new and result.group.last_alerted_new_at is None: await log_alert_service.notify_new_error(db, project_id, group, event)` — B-lite 알림

### 2.5. B-lite 알림 정책

**`notify_new_error`** (`log_alert_service`):
- 호출 조건 (caller — `fingerprint_processor`): `result.is_new == True` (신규 INSERT) AND `group.last_alerted_new_at IS NULL` (cooldown 1차 게이트)
- 함수 안 2차 검증: `group.last_alerted_new_at IS NULL` (race 회피 — 동시 신규 INSERT 의 다른 ingest 가 먼저 알림 발송할 수 있음)
- `notification_dispatcher.dispatch_discord_alert(db, project, content)` 호출 (Phase 6 자산) — disable 정책 자동 적용
- 알림 발송 직후 `group.last_alerted_new_at = datetime.utcnow()` + `db.commit()` — cooldown 마킹 영속
- spike / regression 알림 미포함 — Phase 6 본편

**메시지 포맷** (spec §6.3 패턴):
```
🆕 **새 에러** — KeyError
메시지: 'preference'
첫 발생: `abc1234` (production)
```

### 2.6. Ingest endpoint BackgroundTask 통합

**`ingest_batch` 시그니처 확장**:
- `(int, list[dict])` → `(int, list[dict], list[UUID])` — `accepted_event_ids: list[UUID]` 추가
- 기존 `insert_events` 가 events 를 INSERT 하면 SQLAlchemy 가 id 자동 채움 — `[e.id for e in accepted_log_events]` 로 추출.

**Endpoint** (`log_ingest.py`):
- `BackgroundTasks` 를 `Depends` 로 받음
- `ingest_batch` 호출 후 ERROR↑ event 만 추출 — `LogEvent.level IN (ERROR, CRITICAL)` 별도 SELECT 또는 ingest_batch 가 분리 return (시그니처 또 확장 — YAGNI). 단순화: ingest_batch 가 `accepted_event_ids` 만 return, endpoint 가 그 id 를 한 번 더 SELECT 해 level 분류:

```python
# endpoint 안에서:
accepted, rejected, accepted_ids = await ingest_batch(...)

if accepted_ids:
    error_ids = (await db.execute(
        select(LogEvent.id)
        .where(LogEvent.id.in_(accepted_ids))
        .where(LogEvent.level.in_([LogLevel.ERROR, LogLevel.CRITICAL]))
    )).scalars().all()
    for eid in error_ids:
        background_tasks.add_task(_process_log_event_in_new_session, eid)
```

(또 다른 옵션: ingest_batch 가 직접 분류해 `(int, list[dict], list[UUID])` 의 마지막을 ERROR↑ ids 만 — 더 깔끔. plan 단계에서 결정.)

**`_process_log_event_in_new_session`** (endpoint module 의 helper):

```python
async def _process_log_event_in_new_session(event_id: UUID) -> None:
    """BackgroundTask 진입점 — 자체 session, fingerprint_processor 호출."""
    try:
        async with AsyncSessionLocal() as db:
            event = await db.get(LogEvent, event_id)
            if event is None or event.fingerprinted_at is not None:
                return  # 회수된 이벤트 또는 이미 처리됨 (멱등)
            await fingerprint_processor.process(db, event)
    except Exception:
        logger.exception("background fingerprint processing failed for event %s", event_id)
```

Phase 4 의 `_run_sync_in_new_session` 패턴 그대로.

### 2.7. log_fingerprint_reaper (lifespan hook)

**책임**: 부팅 시 1회. `level >= ERROR AND fingerprinted_at IS NULL` 회수 — 컨테이너 재시작 / 크래시 / BackgroundTask silent fail 누락 회수.

**chunked 처리** (100건 / batch):
- `idx_log_unfingerprinted` partial index 사용 (Phase 1 alembic 이미 생성)
- `received_at ASC` 순 — 오래된 것부터 처리 (사용자 가치 우선순위)
- 각 event 마다 fresh session (Phase 4 학습 — poison session 회피)

**lifespan hook 등록** (`backend/app/main.py`):
```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    # ... 기존 startup ...
    try:
        await push_event_reaper.run_reaper_once()
    except Exception:
        logger.exception("push_event_reaper failed at startup")
    try:
        await log_fingerprint_reaper.run_reaper_once()
    except Exception:
        logger.exception("log_fingerprint_reaper failed at startup")
    yield
```

`reap_pending_events` 와 `run_reaper_once` 는 모두 부팅 1회 — 선후 무관 (다른 데이터). DB 연결 실패 시 try/except 로 startup 진행 보장.

### 2.8. APP_PROJECT_ROOT 처리

`backend/app/config.py` (또는 `.env` 환경변수) — `APP_PROJECT_ROOT: str = "backend/"`. fingerprint_service 의 `_normalize_path` 가 이 값을 우선 strip 시도. 매칭 안 되면 휴리스틱 (첫 `backend/` 또는 `src/` segment 기준).

향후 멀티 프로젝트 layout 다양성 대응 → Project 컬럼 추가 (후속 phase).

### 2.9. Failure modes (spec §7 + 본 phase 추가)

| 위치 | 케이스 | 대응 |
|---|---|---|
| BackgroundTask | fingerprint_processor 예외 | `_process_log_event_in_new_session` try/except → `logger.exception` swallow. event.fingerprinted_at NULL 그대로 → reaper 가 다음 부팅 시 회수 |
| fingerprint_service | stack_frames 비/모두 framework | spec §7 fallback (SHA1(class + "|" + message)) |
| error_group_service | UNIQUE conflict (동시 INSERT race) | IntegrityError catch + SAVEPOINT → SELECT + UPDATE fallback |
| log_alert_service | dispatcher 가 throw | Phase 6 dispatcher 가 자체 silent. alert_service 도 try/except wrapping |
| reaper | 단일 event poison | per-event fresh session — 다음 event 영향 없음 |
| reaper | DB 연결 실패 | lifespan try/except → 부팅은 진행 (push_event_reaper 와 같은 패턴) |
| BackgroundTask | event_id 가 reaper 와 동시 처리 | event.fingerprinted_at NOT NULL 체크 — 둘 중 하나만 처리 (idempotent) |

### 2.10. 멱등성 보장

- BackgroundTask + reaper 둘 다 같은 event 처리 시: `event.fingerprinted_at IS NOT NULL` 체크 → 이미 처리됐으면 skip.
- ErrorGroup UPSERT — UNIQUE conflict catch + UPDATE fallback.
- 같은 fingerprint 의 두 동시 처리: 두 ingest 의 BackgroundTask 가 같은 fingerprint event 처리 — `with_for_update()` 직렬화.

---

## 3. Backend Architecture

### 3.1. `fingerprint_service.py` 신규

`backend/app/services/fingerprint_service.py`:

```python
"""LogEvent 의 결정적 fingerprint 계산.

설계서: 2026-05-01-error-log-phase3-design.md §2.1, §2.2 (spec §4.1, §7)
"""
import hashlib
import re
from typing import Any

from app.config import settings


_MEMORY_ADDR_RE = re.compile(r"0x[0-9a-f]+")
_FRAMEWORK_PATTERNS = [
    "site-packages/",
    "dist-packages/",
    "asyncio/",
    "uvicorn/",
    "_bootstrap.py",
]
_FRAMEWORK_LIB_RE = re.compile(r"lib/python\d")


def _normalize_path(filename: str) -> str:
    """절대경로 → 상대경로. APP_PROJECT_ROOT env var 우선, 휴리스틱 fallback."""
    project_root = getattr(settings, "app_project_root", "backend/")
    # env var 우선 strip
    if project_root and project_root in filename:
        idx = filename.find(project_root)
        return filename[idx:]
    # 휴리스틱 — 첫 `backend/` 또는 `src/` segment
    for marker in ("backend/", "src/"):
        if marker in filename:
            idx = filename.find(marker)
            return filename[idx:]
    # 매칭 안 되면 원본
    return filename


def _is_framework_frame(filename: str) -> bool:
    """framework / stdlib frame 인지 판정."""
    if any(pattern in filename for pattern in _FRAMEWORK_PATTERNS):
        return True
    if _FRAMEWORK_LIB_RE.search(filename):
        return True
    return False


def _mask_memory_addresses(text: str) -> str:
    """`0x7f8a...` → `0xADDR`."""
    return _MEMORY_ADDR_RE.sub("0xADDR", text)


def compute(
    *,
    exception_class: str,
    stack_frames: list[dict[str, Any]] | None,
    exception_message: str | None = None,
) -> str:
    """결정적 fingerprint SHA1.

    정규화 6 규칙 (절대경로→상대, line 제거, 메모리 주소 마스킹, 함수명 유지,
    framework 스킵, 입력 포맷). stack_frames 비/모두 framework 면 fallback.
    """
    # Fallback 1 — stack_frames 없음
    if not stack_frames:
        msg_first = (exception_message or "").splitlines()[0] if exception_message else ""
        msg_first = _mask_memory_addresses(msg_first)
        return hashlib.sha1(
            f"{exception_class}|{msg_first}".encode("utf-8")
        ).hexdigest()

    # 앱 frame 만 추출
    app_frames = [
        f for f in stack_frames
        if not _is_framework_frame(f.get("filename", ""))
    ]

    # Fallback 2 — 모두 framework — 가용한 frame top 5 사용 (스킵 무시)
    frames_to_use = app_frames if app_frames else stack_frames

    top5 = frames_to_use[:5]

    # 입력 문자열 조립
    parts = []
    for frame in top5:
        rel_path = _normalize_path(frame.get("filename", ""))
        func_name = _mask_memory_addresses(frame.get("name", ""))
        parts.append(f"{rel_path}:{func_name}")

    input_str = f"{exception_class}|" + "\n".join(parts)
    return hashlib.sha1(input_str.encode("utf-8")).hexdigest()
```

### 3.2. `error_group_service.py` 신규

```python
"""ErrorGroup UPSERT + 자동 status 전이.

설계서: 2026-05-01-error-log-phase3-design.md §2.3
"""
import logging
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.error_group import ErrorGroup, ErrorGroupStatus
from app.models.log_event import LogEvent

logger = logging.getLogger(__name__)


@dataclass
class GroupResult:
    group: ErrorGroup
    is_new: bool
    transitioned_to_regression: bool


async def upsert(
    db: AsyncSession,
    *,
    project_id: UUID,
    fingerprint: str,
    event: LogEvent,
) -> GroupResult:
    """fingerprint 별 ErrorGroup UPSERT + 자동 status 전이.

    race condition: SELECT FOR UPDATE 로 같은 group 동시 UPDATE 직렬화.
    신규 INSERT race 는 SAVEPOINT + IntegrityError fallback.
    """
    # 1차 시도 — SELECT FOR UPDATE
    existing_stmt = (
        select(ErrorGroup)
        .where(
            ErrorGroup.project_id == project_id,
            ErrorGroup.fingerprint == fingerprint,
        )
        .with_for_update()
    )
    group = (await db.execute(existing_stmt)).scalar_one_or_none()

    if group is None:
        # INSERT 신규 — UNIQUE conflict race 가능 → SAVEPOINT
        new_group = ErrorGroup(
            project_id=project_id,
            fingerprint=fingerprint,
            exception_class=event.exception_class or "UnknownError",
            exception_message_sample=event.exception_message,
            first_seen_at=event.received_at,
            first_seen_version_sha=event.version_sha,
            last_seen_at=event.received_at,
            last_seen_version_sha=event.version_sha,
            event_count=1,
            status=ErrorGroupStatus.OPEN,
        )
        try:
            async with db.begin_nested():
                db.add(new_group)
                await db.flush()
            return GroupResult(group=new_group, is_new=True, transitioned_to_regression=False)
        except IntegrityError:
            # 다른 동시 호출이 먼저 INSERT — UPDATE 분기 fallback
            logger.info(
                "ErrorGroup UNIQUE conflict (race) for project=%s fingerprint=%s — fallback UPDATE",
                project_id, fingerprint,
            )
            await db.rollback()  # SAVEPOINT 후 outer tx 도 dirty 가능 — Phase 4 패턴
            # outer tx restart — 호출자 caller 가 처리 (간단화: 재SELECT + UPDATE)
            # 실제 구현에서는 outer tx 에 영향 없는 SAVEPOINT-only rollback 사용 가능 — plan 에서 정확히
            existing = (await db.execute(existing_stmt)).scalar_one_or_none()
            if existing is None:
                # 매우 드문 race — 여기 도달하면 retry 한 번 더. 단순히 IntegrityError 재발생
                raise
            group = existing  # fall through to UPDATE 분기

    # UPDATE 분기 — event_count++, last_seen 갱신, exception_message_sample 갱신, RESOLVED→REGRESSED
    transitioned = False
    if group.status == ErrorGroupStatus.RESOLVED:
        group.status = ErrorGroupStatus.REGRESSED
        transitioned = True
    # OPEN / REGRESSED / IGNORED → status 그대로

    group.last_seen_at = event.received_at
    group.last_seen_version_sha = event.version_sha
    group.event_count += 1
    if event.exception_message:
        group.exception_message_sample = event.exception_message

    await db.flush()
    return GroupResult(group=group, is_new=False, transitioned_to_regression=transitioned)
```

(SAVEPOINT 의 정확한 rollback 흐름은 implementation 단계에서 검증 — Phase 2 `record_push_event` 와 같은 패턴.)

### 3.3. `fingerprint_processor.py` 신규

```python
"""fingerprint composition wrapper — 계산 + group UPSERT + alert + 마킹.

설계서: 2026-05-01-error-log-phase3-design.md §2.4
"""
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.log_event import LogEvent
from app.services import error_group_service, fingerprint_service, log_alert_service


async def process(db: AsyncSession, event: LogEvent) -> None:
    """fingerprint 계산 → ErrorGroup UPSERT → 신규면 알림 → fingerprinted_at 마킹."""
    fingerprint = fingerprint_service.compute(
        exception_class=event.exception_class or "UnknownError",
        stack_frames=event.stack_frames,
        exception_message=event.exception_message,
    )

    result = await error_group_service.upsert(
        db, project_id=event.project_id, fingerprint=fingerprint, event=event,
    )

    event.fingerprint = fingerprint
    event.fingerprinted_at = datetime.utcnow()
    await db.commit()

    # B-lite — 신규 fingerprint 1회 알림
    if result.is_new:
        await log_alert_service.notify_new_error(
            db, project_id=event.project_id, group=result.group, event=event,
        )
```

### 3.4. `log_alert_service.py` 신규 (B-lite scope)

```python
"""Discord 알림 — 본 phase 는 신규 fingerprint 알림만 (B-lite).

설계서: 2026-05-01-error-log-phase3-design.md §2.5
spike / regression 은 Phase 6 본편에서 추가.
"""
import logging
from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.error_group import ErrorGroup
from app.models.log_event import LogEvent
from app.models.project import Project
from app.services import notification_dispatcher

logger = logging.getLogger(__name__)


async def notify_new_error(
    db: AsyncSession,
    *,
    project_id: UUID,
    group: ErrorGroup,
    event: LogEvent,
) -> None:
    """신규 fingerprint 1회 Discord 알림. cooldown — group당 1회만."""
    project = await db.get(Project, project_id)
    if project is None:
        return
    if group.last_alerted_new_at is not None:
        return  # 이미 알림 발송됨 (race 방지 — 동시 신규 INSERT 가능성)

    short_sha = (
        event.version_sha[:7] if event.version_sha != "unknown" else "unknown"
    )
    msg_first = (group.exception_message_sample or "").splitlines()[0][:200]
    content = (
        f"🆕 **새 에러** — {group.exception_class}\n"
        f"메시지: {msg_first}\n"
        f"첫 발생: `{short_sha}` ({event.environment})"
    )

    try:
        await notification_dispatcher.dispatch_discord_alert(db, project, content)
    except Exception:
        logger.exception(
            "Discord alert dispatch failed for new error group=%s", group.id,
        )
        return

    # 알림 발송 후 cooldown 마킹
    group.last_alerted_new_at = datetime.utcnow()
    await db.commit()
```

### 3.5. Ingest endpoint BackgroundTask 통합

`log_ingest_service.ingest_batch` 시그니처:
```python
async def ingest_batch(
    db: AsyncSession,
    *,
    token: LogIngestToken,
    payload_dict: dict[str, Any],
    dropped_since_last: int | None = None,
    now: datetime | None = None,
) -> tuple[int, list[dict], list[UUID]]:
    """... 기존 docstring ...

    Returns: (accepted_count, rejected_list, accepted_event_ids).
    accepted_event_ids — INSERT 된 LogEvent 의 id 리스트 (caller 가 BackgroundTask 큐).
    """
```

`backend/app/api/v1/endpoints/log_ingest.py`:

```python
@router.post("/log-ingest")
async def ingest_logs(
    request: Request,
    background_tasks: BackgroundTasks,
    authorization: str | None = Header(default=None),
    content_encoding: str | None = Header(default=None),
    x_pslog_dropped_since_last: int | None = Header(default=None, alias="X-pslog-Dropped-Since-Last"),
    db: AsyncSession = Depends(get_db),
):
    # ... 기존 토큰/gzip/json 처리 ...

    accepted, rejected, accepted_ids = await log_ingest_service.ingest_batch(
        db, token=token,
        payload_dict=payload,
        dropped_since_last=x_pslog_dropped_since_last,
    )

    # ERROR↑ event 만 BackgroundTask 큐
    if accepted_ids:
        from sqlalchemy import select
        from app.models.log_event import LogEvent, LogLevel
        error_stmt = (
            select(LogEvent.id)
            .where(LogEvent.id.in_(accepted_ids))
            .where(LogEvent.level.in_([LogLevel.ERROR, LogLevel.CRITICAL]))
        )
        error_ids = (await db.execute(error_stmt)).scalars().all()
        for eid in error_ids:
            background_tasks.add_task(_process_log_event_in_new_session, eid)

    # ... 기존 응답 ...
```

`_process_log_event_in_new_session` helper (같은 파일):

```python
async def _process_log_event_in_new_session(event_id: UUID) -> None:
    """BackgroundTask — 자체 session, fingerprint_processor 호출, 멱등."""
    from app.database import AsyncSessionLocal
    from app.services import fingerprint_processor

    try:
        async with AsyncSessionLocal() as db:
            event = await db.get(LogEvent, event_id)
            if event is None or event.fingerprinted_at is not None:
                return
            await fingerprint_processor.process(db, event)
    except Exception:
        logger.exception("background fingerprint processing failed for event %s", event_id)
```

### 3.6. `log_fingerprint_reaper.py` 신규

```python
"""부팅 시 미처리 LogEvent 회수 — fingerprint 처리.

설계서: 2026-05-01-error-log-phase3-design.md §2.7
"""
import logging
from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.log_event import LogEvent, LogLevel
from app.services import fingerprint_processor

logger = logging.getLogger(__name__)

REAPER_BATCH_SIZE = 100


async def run_reaper_once() -> None:
    """level >= ERROR AND fingerprinted_at IS NULL 회수. chunked 100건."""
    while True:
        # 별 session 으로 batch lookup
        async with AsyncSessionLocal() as lookup_db:
            stmt = (
                select(LogEvent.id)
                .where(LogEvent.level.in_([LogLevel.ERROR, LogLevel.CRITICAL]))
                .where(LogEvent.fingerprinted_at.is_(None))
                .order_by(LogEvent.received_at.asc())
                .limit(REAPER_BATCH_SIZE)
            )
            ids = (await lookup_db.execute(stmt)).scalars().all()

        if not ids:
            break

        for event_id in ids:
            try:
                async with AsyncSessionLocal() as inner_db:
                    event = await inner_db.get(LogEvent, event_id)
                    if event is None or event.fingerprinted_at is not None:
                        continue
                    await fingerprint_processor.process(inner_db, event)
            except Exception:
                logger.exception("reaper failed for log event %s", event_id)
```

`backend/app/main.py` lifespan 변경:

```python
from app.services import log_fingerprint_reaper

@asynccontextmanager
async def lifespan(app: FastAPI):
    # ... 기존 startup ...
    try:
        await reap_pending_events()  # push_event_reaper
    except Exception:
        logger.exception("push_event_reaper failed at startup")
    try:
        await log_fingerprint_reaper.run_reaper_once()
    except Exception:
        logger.exception("log_fingerprint_reaper failed at startup")
    yield
    # ... 기존 shutdown ...
```

### 3.7. config 변경

`backend/app/config.py`:

```python
class Settings(BaseSettings):
    # ... 기존 ...
    app_project_root: str = "backend/"
```

`.env.example` 에 `APP_PROJECT_ROOT="backend/"` 항목 추가 (pslog 측 환경변수 — app-chak 와 별개).

---

## 4. Test Plan

### 4.1. Backend 신규 (예상 +25 tests, 230 → 255)

Breakdown: 8 (fingerprint_service) + 6 (error_group_service) + 4 (fingerprint_processor) + 3 (log_alert_service) + 3 (log_fingerprint_reaper) + 1 (log_ingest_endpoint BackgroundTask 검증). `ingest_batch` 시그니처 변경에 따른 기존 테스트 갱신은 신규 카운트 변화 없음 (3-tuple unpacking 만 추가).


**`test_fingerprint_service.py`** (8건):
1. 정규화 — APP_PROJECT_ROOT env var 우선 strip
2. 정규화 — env var 없을 때 휴리스틱 (`backend/` 첫 segment)
3. 정규화 — 매칭 안 되는 path 원본 유지
4. line number 제거 — 같은 함수의 다른 line → 같은 fingerprint
5. 메모리 주소 마스킹 — `<object at 0x7f8a...>` 결정성
6. framework 스킵 — site-packages frame 추가해도 같은 fingerprint
7. 결정성 — exception class 다름/같음, 함수명 다름/같음
8. Fallback — stack_frames None / 모두 framework 두 케이스

**`test_error_group_service.py`** (6건):
1. 신규 INSERT (`is_new=True`, status=OPEN, first_seen 설정)
2. 기존 OPEN UPDATE (event_count++, last_seen 갱신)
3. RESOLVED → REGRESSED 전이 (`transitioned=True`)
4. IGNORED → status 그대로 (event_count++)
5. 동시 신규 INSERT race (UNIQUE conflict + SAVEPOINT fallback)
6. 같은 group 동시 UPDATE 직렬화 (with_for_update — B1 race test 패턴, 두 session)

**`test_fingerprint_processor.py`** (4건):
1. 정상 처리 — fingerprint 계산 + group UPSERT + fingerprinted_at = now + commit
2. 신규 group → notify_new_error 호출 (mock dispatcher)
3. 기존 group → notify_new_error 호출 안 함
4. fingerprint_service throw → caller (BackgroundTask) catch. event.fingerprinted_at NULL 유지

**`test_log_alert_service.py`** (3건):
1. last_alerted_new_at IS NULL → dispatcher 호출 + 마킹
2. last_alerted_new_at 이미 set → no-op
3. project not found → no-op

**`test_log_fingerprint_reaper.py`** (3건):
1. 부팅 시 unfingerprinted ERROR 회수 → fingerprint + group + 마킹
2. INFO/WARNING level 회수 안 함
3. chunked 처리 — 200건 → 100씩 2 batch

**`test_log_ingest_endpoint.py` 갱신** (기존 8 + 신규 1):
- `test_ingest_schedules_background_task_for_error_events_only` — ERROR/CRITICAL 만 큐 (mock BackgroundTasks)

**`test_log_ingest_service.py` 갱신**:
- `ingest_batch` 시그니처 변경 (`accepted_event_ids` 추가) — 기존 테스트 14건 시그니처 분해 패턴 갱신 (3-tuple unpacking 추가)

### 4.2. Frontend

본 phase 는 backend only. 변경 없음.

### 4.3. e2e (사용자, PR 머지 전)

- pslog dev server 재시작 → log_fingerprint_reaper 부팅 hook 정상 (logger 메시지 확인)
- app-chak 의 `.env` 의 `PSLOG_LOG_INGEST_TOKEN` 설정된 상태 (Phase 2a 머지 후 e2e 가능)
- 의도적 `logger.error("test new error")` from app-chak → pslog `log_events` INSERT 후 BackgroundTask 가 fingerprint + ErrorGroup UPSERT
- pslog DB 직접 SQL — `SELECT * FROM error_groups` → 1 row, status=OPEN, fingerprint 결정성 확인
- pslog Discord 채널 — 🆕 새 에러 알림 1회 확인
- 같은 에러 다시 던지기 → ErrorGroup event_count++, 알림 안 옴 (cooldown)
- 다른 에러 던지기 → 새 ErrorGroup + 새 알림

---

## 5. Decision Log

- **Scope = B-lite** (옵션 B-lite vs A vs B): A + 신규 fingerprint 1종 알림. 머지 즉시 사용자 가치 (Discord 알림) + scope 작음 + 채널 분리 결정 미루기 가능. spike / regression 은 Phase 6 본편 (메모리 카운터 + 30분 cooldown burst 정책 통합 디자인).
- **APP_PROJECT_ROOT** (옵션 A vs B vs C): pslog `.env` env var 우선 + 휴리스틱 fallback. 향후 멀티 프로젝트 layout 다양해지면 Project 컬럼 추가.
- **reaper 트리거** (옵션 A vs B vs C): lifespan hook 1회. push_event_reaper 패턴 그대로. cron 안 함 (BackgroundTask 안정 + reaper 회수로 충분).
- **자동 status 전이만 (사용자 액션 미포함)**: PATCH `/errors/{group_id}` (resolve/ignore/reopen) 은 Phase 5 UI 와 함께. 본 phase 는 RESOLVED → REGRESSED 자동 전이만.
- **알림 발송 시점 — commit 후**: fingerprint_processor 의 `db.commit()` 후 `notify_new_error` 호출. DB 일관 상태에서 발송 (Phase 6 학습). 알림 자체 commit (`last_alerted_new_at = now`) 은 alert_service 안에서.
- **race condition — UNIQUE conflict + SAVEPOINT**: Phase 2 `record_push_event` 패턴 재사용. SELECT FOR UPDATE 가 신규 INSERT race 못 잡음 → SAVEPOINT + IntegrityError fallback.
- **BackgroundTask + reaper 멱등성**: `event.fingerprinted_at IS NOT NULL` 체크 양쪽에서 — 둘 중 하나만 처리.
- **신규 알림 cooldown 패턴**: alert_service 가 `last_alerted_new_at = now()` + commit. 함수 진입 시 2차 체크 (`is None`) — race 방지.

---

## 6. Phase 4 / Phase 6 와의 관계 (참고)

본 phase 끝나면:
- **Phase 4** (`log_query_service` + `GET /errors`, `GET /errors/{group_id}`): ErrorGroup 데이터가 본 phase 에서 쌓이므로 조회 가치 있음. Handoff/Task join + 직전 정상 SHA 찾기 + pg_trgm 풀텍스트.
- **Phase 5** (UI): LogsPage / ErrorsPage / ErrorDetailPage / GitContextPanel / LogTokensPage. Phase 4 의 endpoint + 본 phase 의 group 데이터 소비.
- **Phase 6** (알림 본편): spike 메모리 카운터 + regression 알림 + cooldown burst 정책. 본 phase 의 B-lite (new alert) 위에 추가. error_group_service 의 `transitioned_to_regression` 신호를 Phase 6 alert_service 가 사용 → regression 알림 발송.

본 phase 의 dispatcher 통과 패턴 (Phase 6 task-automation 자산 재사용) — 채널 분리 (task-automation vs error-log) 는 사용자 노이즈 호소 시 후속.

---

## 7. Open Questions

본 phase 진입 전 답할 것 없음. 시각/사용자 검증 후 결정 항목 1건:

1. **알림 채널 분리 (post-Phase 6)** — 현재 `Project.discord_webhook_url` 1개. task-automation (push summary, sync 실패) + error-log (new error 등) 모두 같은 채널. 사용자 noise 호소 시 분리 webhook URL 컬럼 추가. Phase 6 본편 또는 후속 phase 에서 통합 디자인.
