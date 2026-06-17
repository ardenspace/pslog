# Error-log Phase 3 — Fingerprint + ErrorGroup + Reaper + B-lite Alert Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Phase 2a 가 깐 raw LogEvent 인입 위에 그룹화 / 자동 status 전이 / 신규 에러 Discord 알림까지 — 사용자가 pslog 의 에러 추적 가치를 즉시 체감할 수 있는 layer 구축.

**Architecture:** 7 task 분할 — config + fingerprint_service → error_group_service → log_alert_service (B-lite) → fingerprint_processor (composition) → ingest endpoint BackgroundTask 통합 → log_fingerprint_reaper + lifespan hook → 최종 회귀/PR. 마이그레이션 신규 X (Phase 1 alembic 이 모든 모델 + `idx_log_unfingerprinted` partial 포함). B1/B2/Phase6 학습 적용 — `with_for_update` 직렬화 / SAVEPOINT + IntegrityError fallback / fresh session per event / commit 후 알림 / `db.refresh` rollback expire 회피.

**Tech Stack:** FastAPI 0.115 (BackgroundTasks), SQLAlchemy 2.0 async (with_for_update / begin_nested), Pydantic v2, hashlib SHA1, pytest + testcontainers PostgreSQL.

**선행 조건:**
- pslog `main` = `90cce78` (Error-log Phase 2a PR #16 머지 직후)
- alembic head = `7c6e0c9bb915` (Phase 6 컬럼 + Phase 1 모든 error-log 모델 + `idx_log_unfingerprinted` partial 포함)
- backend tests baseline = **230 passing**
- Phase 6 의 `notification_dispatcher` 자산 활용 (disable 정책 자동 적용)
- spec: `docs/superpowers/specs/2026-05-01-error-log-phase3-design.md`

**중요한 계약:**

- **fingerprint 정규화 6 규칙** (spec §4.1): 절대경로→상대경로 (`APP_PROJECT_ROOT` env var 우선, `backend/`/`src/` 휴리스틱 fallback) / line 제거 / 메모리 주소 마스킹 (`0x[0-9a-f]+` → `0xADDR`) / 함수명 유지 / framework·stdlib 스킵 (`site-packages/`, `dist-packages/`, `lib/python\d`, `asyncio/`, `uvicorn/`, `_bootstrap.py`) / 입력 포맷 `<class>|<rel:func>\n...` SHA1.
- **fingerprint Fallback**: stack_frames None → `SHA1(class + "|" + msg 첫 줄)`. 모두 framework → 가용 frame top 5 (스킵 무시).
- **ErrorGroup race-free UPSERT**: `with_for_update()` 로 같은 group 동시 UPDATE 직렬화 + 신규 INSERT 의 UNIQUE conflict 는 SAVEPOINT (`begin_nested()`) + `IntegrityError` catch + SELECT fallback (Phase 2 record_push_event 패턴 재사용).
- **자동 status 전이만**: 신규 → OPEN, RESOLVED → REGRESSED. OPEN/REGRESSED/IGNORED → 그대로 (event_count++, last_seen 갱신). 사용자 액션 (resolve/ignore/reopen) 은 Phase 5 UI.
- **B-lite 알림 cooldown**: `last_alerted_new_at IS NULL` → 발송 + 마킹. group 당 1회. spike/regression 은 Phase 6 본편.
- **알림 발송 시점 = commit 후**: fingerprint_processor 의 `db.commit()` (group + event 영속) → notify_new_error → alert_service 가 자체 commit (`last_alerted_new_at = now`).
- **BackgroundTask + reaper 멱등**: 양쪽에서 `event.fingerprinted_at IS NOT NULL` 체크 → 둘 중 하나만 처리.
- **ingest_batch 시그니처 변경**: `(int, list[dict])` → `(int, list[dict], list[UUID])`. 기존 호출자 (endpoint, tests) 분해 패턴 갱신.
- **ERROR↑ 만 BackgroundTask 큐**: endpoint 가 INSERT 된 LogEvent 중 `level IN (ERROR, CRITICAL)` 만 한 번 더 SELECT 후 `add_task` (별도 트랜잭션 — ingest 응답 fast-path 보장).
- **에러 정책**:
  - BackgroundTask 예외 → silent log (event.fingerprinted_at NULL 유지 → reaper 회수)
  - reaper 단일 event poison → fresh session per event (Phase 4 학습)
  - alert_service dispatcher throw → silent (Phase 6 dispatcher 가 자체 silent + alert_service 도 try/except)

---

## File Structure

**신규 파일 (소스)**:
- `backend/app/services/fingerprint_service.py` — `compute(...)` + helpers (`_normalize_path`, `_is_framework_frame`, `_mask_memory_addresses`)
- `backend/app/services/error_group_service.py` — `upsert(...)` + `GroupResult` dataclass
- `backend/app/services/log_alert_service.py` — `notify_new_error(...)` (B-lite scope)
- `backend/app/services/fingerprint_processor.py` — `process(db, event)` composition
- `backend/app/services/log_fingerprint_reaper.py` — `run_reaper_once()`

**신규 파일 (테스트)**:
- `backend/tests/test_fingerprint_service.py` (8건)
- `backend/tests/test_error_group_service.py` (6건)
- `backend/tests/test_log_alert_service.py` (3건)
- `backend/tests/test_fingerprint_processor.py` (4건)
- `backend/tests/test_log_fingerprint_reaper.py` (3건)

**수정 파일 (소스)**:
- `backend/app/config.py` — `app_project_root: str = "backend/"` 추가
- `backend/.env` — `APP_PROJECT_ROOT="backend/"` 항목 추가 (선택, default 동작)
- `backend/app/services/log_ingest_service.py` — `ingest_batch` 시그니처 확장 (`accepted_event_ids: list[UUID]` 추가)
- `backend/app/api/v1/endpoints/log_ingest.py` — `BackgroundTasks` Depends + ERROR↑ 분류 + `_process_log_event_in_new_session` helper
- `backend/app/main.py` — lifespan 에 `log_fingerprint_reaper.run_reaper_once()` 추가

**수정 파일 (테스트)**:
- `backend/tests/test_log_ingest_service.py` — `ingest_batch` 호출 분해 패턴 갱신 (3-tuple unpack), 기존 테스트 수 변화 X
- `backend/tests/test_log_ingest_endpoint.py` — BackgroundTask 큐 검증 신규 1건

**미변경**:
- alembic (Phase 1 에 모든 모델 + 인덱스 포함)
- frontend (Phase 5 LogsPage / ErrorsPage 가 본 데이터 소비)

---

### Task 1: `fingerprint_service` + config

**Files:**
- Modify: `backend/app/config.py` (Settings 에 1 필드)
- Create: `backend/app/services/fingerprint_service.py`
- Create: `backend/tests/test_fingerprint_service.py` (8건)

- [ ] **Step 1: Baseline 확인**

```bash
cd /Users/arden/Documents/ardensdevspace/pslog/.worktrees/error-log-phase3-fingerprint/backend
source venv/bin/activate
pytest -q 2>&1 | tail -3
```

Expected: `230 passed`. 다르면 STOP.

- [ ] **Step 2: config 에 `app_project_root` 추가**

`backend/app/config.py` — 기존 `Settings` 클래스 안에 1 줄 추가 (적당한 위치):

```python
    # Phase 3 — fingerprint 정규화: 절대경로→상대경로 strip 시 prefix
    app_project_root: str = "backend/"
```

(기존 Settings 클래스 패턴 따름. `BaseSettings` 가 자동으로 env var `APP_PROJECT_ROOT` 읽음.)

- [ ] **Step 3: Failing tests 작성**

`backend/tests/test_fingerprint_service.py` 신규:

```python
"""fingerprint_service 단위 테스트.

설계서: 2026-05-01-error-log-phase3-design.md §2.1, §2.2
"""

import hashlib
import pytest

from app.services import fingerprint_service


# ---- 정규화 — 경로 ----

def test_normalize_path_uses_env_var(monkeypatch: pytest.MonkeyPatch):
    """APP_PROJECT_ROOT env var 우선 strip."""
    monkeypatch.setattr(fingerprint_service.settings, "app_project_root", "myapp/")
    result = fingerprint_service._normalize_path("/Users/dev/myapp/routers/x.py")
    assert result == "myapp/routers/x.py"


def test_normalize_path_falls_back_to_heuristic(monkeypatch: pytest.MonkeyPatch):
    """env var 매칭 안 되면 휴리스틱 (`backend/` 첫 segment)."""
    monkeypatch.setattr(fingerprint_service.settings, "app_project_root", "nonexistent/")
    result = fingerprint_service._normalize_path("/Users/dev/app-chak/backend/routers/x.py")
    assert result == "backend/routers/x.py"


def test_normalize_path_keeps_unmatched_original():
    """env var / 휴리스틱 둘 다 안 맞으면 원본 그대로."""
    result = fingerprint_service._normalize_path("/var/log/random.py")
    assert result == "/var/log/random.py"


# ---- fingerprint 결정성 ----

def test_compute_same_func_different_line_same_fingerprint():
    """같은 함수, 다른 line → 같은 fingerprint (line 제거)."""
    frames_a = [{"filename": "/app/backend/x.py", "lineno": 10, "name": "do_thing"}]
    frames_b = [{"filename": "/app/backend/x.py", "lineno": 25, "name": "do_thing"}]
    fp_a = fingerprint_service.compute(
        exception_class="ValueError", stack_frames=frames_a,
    )
    fp_b = fingerprint_service.compute(
        exception_class="ValueError", stack_frames=frames_b,
    )
    assert fp_a == fp_b
    assert len(fp_a) == 40  # SHA1 hex


def test_compute_memory_address_masked():
    """함수명 안의 0x... → 같은 fingerprint."""
    frames_a = [{"filename": "/app/backend/x.py", "lineno": 1, "name": "<lambda at 0x7f8a1234>"}]
    frames_b = [{"filename": "/app/backend/x.py", "lineno": 1, "name": "<lambda at 0xdeadbeef>"}]
    fp_a = fingerprint_service.compute(
        exception_class="ValueError", stack_frames=frames_a,
    )
    fp_b = fingerprint_service.compute(
        exception_class="ValueError", stack_frames=frames_b,
    )
    assert fp_a == fp_b


def test_compute_framework_frames_skipped():
    """site-packages frame 추가해도 같은 fingerprint (앱 frame top 5 만)."""
    app_frame = {"filename": "/app/backend/x.py", "lineno": 1, "name": "do_thing"}
    framework_frame = {
        "filename": "/usr/lib/python3.12/site-packages/uvicorn/server.py",
        "lineno": 50, "name": "serve",
    }
    fp_app_only = fingerprint_service.compute(
        exception_class="ValueError", stack_frames=[app_frame],
    )
    fp_with_framework = fingerprint_service.compute(
        exception_class="ValueError", stack_frames=[framework_frame, app_frame],
    )
    assert fp_app_only == fp_with_framework


def test_compute_class_change_changes_fingerprint():
    """exception class 다름 → 다른 fingerprint."""
    frames = [{"filename": "/app/backend/x.py", "lineno": 1, "name": "do_thing"}]
    fp_value = fingerprint_service.compute(
        exception_class="ValueError", stack_frames=frames,
    )
    fp_key = fingerprint_service.compute(
        exception_class="KeyError", stack_frames=frames,
    )
    assert fp_value != fp_key


# ---- Fallback ----

def test_compute_fallback_when_no_stack_frames():
    """stack_frames None → SHA1(class + "|" + message 첫 줄)."""
    fp = fingerprint_service.compute(
        exception_class="KeyError", stack_frames=None,
        exception_message="'preference'\nstack...",
    )
    expected = hashlib.sha1(b"KeyError|'preference'").hexdigest()
    assert fp == expected


def test_compute_fallback_when_all_framework():
    """모두 framework frame → 가용한 frame top 5 사용 (스킵 무시)."""
    framework_frames = [
        {"filename": "/usr/lib/python3.12/site-packages/asyncio/runners.py",
         "lineno": 50, "name": "run"},
        {"filename": "/usr/lib/python3.12/site-packages/uvicorn/main.py",
         "lineno": 100, "name": "main"},
    ]
    fp = fingerprint_service.compute(
        exception_class="RuntimeError", stack_frames=framework_frames,
    )
    # framework 만 있어도 fingerprint 가 만들어짐 (None fallback 아닌 정규화 경로)
    assert len(fp) == 40
    # 같은 framework frames 면 결정적
    fp2 = fingerprint_service.compute(
        exception_class="RuntimeError", stack_frames=framework_frames,
    )
    assert fp == fp2
```

- [ ] **Step 4: Verify failure**

```bash
pytest tests/test_fingerprint_service.py -v 2>&1 | tail -15
```

Expected: 8 FAIL with `ImportError: cannot import name 'fingerprint_service'`.

- [ ] **Step 5: `fingerprint_service.py` 구현**

`backend/app/services/fingerprint_service.py` 신규:

```python
"""LogEvent 의 결정적 fingerprint 계산.

설계서: 2026-05-01-error-log-phase3-design.md §2.1, §2.2 (spec §4.1, §7)
정규화 6 규칙 — 절대경로→상대 / line 제거 / 메모리 주소 마스킹 / 함수명 유지 / framework 스킵 / SHA1.
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
    if project_root and project_root in filename:
        idx = filename.find(project_root)
        return filename[idx:]
    for marker in ("backend/", "src/"):
        if marker in filename:
            idx = filename.find(marker)
            return filename[idx:]
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

    설계서 §2.1 정규화 6 규칙 + §2.2 fallback.
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

    # Fallback 2 — 모두 framework — 가용 frame top 5 (스킵 무시)
    frames_to_use = app_frames if app_frames else stack_frames
    top5 = frames_to_use[:5]

    # 입력 문자열 조립 (line 제거 + 메모리 주소 마스킹)
    parts = []
    for frame in top5:
        rel_path = _normalize_path(frame.get("filename", ""))
        func_name = _mask_memory_addresses(frame.get("name", ""))
        parts.append(f"{rel_path}:{func_name}")

    input_str = f"{exception_class}|" + "\n".join(parts)
    return hashlib.sha1(input_str.encode("utf-8")).hexdigest()
```

- [ ] **Step 6: Verify pass + 회귀**

```bash
pytest tests/test_fingerprint_service.py -v 2>&1 | tail -15
pytest -q 2>&1 | tail -3
```

Expected: 8 신규 PASS, 전체 `238 passed` (230 + 8).

- [ ] **Step 7: Commit**

```bash
cd /Users/arden/Documents/ardensdevspace/pslog/.worktrees/error-log-phase3-fingerprint
git add backend/app/config.py backend/app/services/fingerprint_service.py backend/tests/test_fingerprint_service.py
git commit -m "$(cat <<'EOF'
feat(error-log/phase3): fingerprint_service + APP_PROJECT_ROOT config

- _normalize_path: APP_PROJECT_ROOT env var 우선 + backend/ src/ 휴리스틱 fallback
- _is_framework_frame: site-packages / dist-packages / asyncio / uvicorn / _bootstrap / lib/pythonN 스킵
- _mask_memory_addresses: 0x[0-9a-f]+ → 0xADDR (함수명 + message 양쪽)
- compute: 정규화 6 규칙 + SHA1 + fallback 2 케이스 (None / 모두 framework)
- 회귀 8건: 정규화 3 (env var/휴리스틱/매칭 X) + 결정성 4 (line/memory/framework/class) + fallback 1

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: `error_group_service` (UPSERT + race-free)

**Files:**
- Create: `backend/app/services/error_group_service.py`
- Create: `backend/tests/test_error_group_service.py` (6건)

- [ ] **Step 1: Failing tests 작성**

`backend/tests/test_error_group_service.py` 신규:

```python
"""error_group_service 단위 테스트.

설계서: 2026-05-01-error-log-phase3-design.md §2.3
"""

import asyncio
import uuid
from datetime import datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.error_group import ErrorGroup, ErrorGroupStatus
from app.models.log_event import LogEvent, LogLevel
from app.models.project import Project
from app.models.workspace import Workspace
from app.services import error_group_service


async def _seed_project(db: AsyncSession) -> Project:
    ws = Workspace(name="ws", slug=f"ws-{uuid.uuid4().hex[:8]}")
    db.add(ws)
    await db.flush()
    proj = Project(workspace_id=ws.id, name="p")
    db.add(proj)
    await db.commit()
    await db.refresh(proj)
    return proj


def _make_event(proj: Project, *, version_sha: str = "a" * 40, msg: str = "boom") -> LogEvent:
    return LogEvent(
        project_id=proj.id,
        level=LogLevel.ERROR,
        message=msg,
        logger_name="app.test",
        version_sha=version_sha,
        environment="production",
        hostname="h",
        emitted_at=datetime.utcnow(),
        received_at=datetime.utcnow(),
        exception_class="KeyError",
        exception_message=msg,
    )


async def test_upsert_creates_new_group(async_session: AsyncSession):
    """신규 fingerprint → INSERT, is_new=True, status=OPEN."""
    proj = await _seed_project(async_session)
    event = _make_event(proj, version_sha="a" * 40)
    async_session.add(event)
    await async_session.flush()

    result = await error_group_service.upsert(
        async_session, project_id=proj.id, fingerprint="fp-1", event=event,
    )
    await async_session.commit()

    assert result.is_new is True
    assert result.transitioned_to_regression is False
    assert result.group.status == ErrorGroupStatus.OPEN
    assert result.group.fingerprint == "fp-1"
    assert result.group.event_count == 1
    assert result.group.first_seen_version_sha == "a" * 40
    assert result.group.last_seen_version_sha == "a" * 40


async def test_upsert_updates_existing_open_group(async_session: AsyncSession):
    """기존 OPEN → event_count++, last_seen 갱신, status 그대로."""
    proj = await _seed_project(async_session)
    # 1차 INSERT
    event1 = _make_event(proj, version_sha="a" * 40)
    async_session.add(event1)
    await async_session.flush()
    await error_group_service.upsert(
        async_session, project_id=proj.id, fingerprint="fp-1", event=event1,
    )
    await async_session.commit()

    # 2차 같은 fingerprint
    event2 = _make_event(proj, version_sha="b" * 40, msg="boom2")
    async_session.add(event2)
    await async_session.flush()
    result = await error_group_service.upsert(
        async_session, project_id=proj.id, fingerprint="fp-1", event=event2,
    )
    await async_session.commit()

    assert result.is_new is False
    assert result.transitioned_to_regression is False
    assert result.group.status == ErrorGroupStatus.OPEN
    assert result.group.event_count == 2
    assert result.group.last_seen_version_sha == "b" * 40
    assert result.group.first_seen_version_sha == "a" * 40  # 변동 X
    assert result.group.exception_message_sample == "boom2"  # 가장 최근


async def test_upsert_transitions_resolved_to_regressed(async_session: AsyncSession):
    """기존 RESOLVED → REGRESSED 전이, transitioned=True."""
    proj = await _seed_project(async_session)
    # 시드 RESOLVED group
    group = ErrorGroup(
        project_id=proj.id, fingerprint="fp-1",
        exception_class="KeyError", exception_message_sample="old",
        first_seen_at=datetime.utcnow(), first_seen_version_sha="a" * 40,
        last_seen_at=datetime.utcnow(), last_seen_version_sha="a" * 40,
        event_count=5, status=ErrorGroupStatus.RESOLVED,
        resolved_at=datetime.utcnow(),
    )
    async_session.add(group)
    await async_session.commit()

    event = _make_event(proj, version_sha="c" * 40, msg="reopened")
    async_session.add(event)
    await async_session.flush()
    result = await error_group_service.upsert(
        async_session, project_id=proj.id, fingerprint="fp-1", event=event,
    )
    await async_session.commit()

    assert result.is_new is False
    assert result.transitioned_to_regression is True
    assert result.group.status == ErrorGroupStatus.REGRESSED
    assert result.group.event_count == 6


async def test_upsert_ignored_stays_ignored(async_session: AsyncSession):
    """기존 IGNORED → status 그대로, event_count++."""
    proj = await _seed_project(async_session)
    group = ErrorGroup(
        project_id=proj.id, fingerprint="fp-1",
        exception_class="KeyError", exception_message_sample="old",
        first_seen_at=datetime.utcnow(), first_seen_version_sha="a" * 40,
        last_seen_at=datetime.utcnow(), last_seen_version_sha="a" * 40,
        event_count=3, status=ErrorGroupStatus.IGNORED,
    )
    async_session.add(group)
    await async_session.commit()

    event = _make_event(proj, version_sha="d" * 40)
    async_session.add(event)
    await async_session.flush()
    result = await error_group_service.upsert(
        async_session, project_id=proj.id, fingerprint="fp-1", event=event,
    )
    await async_session.commit()

    assert result.is_new is False
    assert result.transitioned_to_regression is False
    assert result.group.status == ErrorGroupStatus.IGNORED
    assert result.group.event_count == 4


async def test_upsert_unique_conflict_falls_back_to_update(async_session: AsyncSession):
    """동시 신규 INSERT race — UNIQUE conflict catch + SELECT fallback."""
    proj = await _seed_project(async_session)

    # 1차 호출 (정상 INSERT)
    event1 = _make_event(proj, version_sha="a" * 40)
    async_session.add(event1)
    await async_session.flush()
    await error_group_service.upsert(
        async_session, project_id=proj.id, fingerprint="fp-race", event=event1,
    )
    await async_session.commit()

    # 2차 호출 — 같은 fingerprint, 같은 session — UNIQUE 가 catch 안 되고
    # 단순 SELECT 로 기존 group 발견 + UPDATE 분기.
    # 진정한 race 검증은 두 session 으로 — 다음 테스트에서.
    event2 = _make_event(proj, version_sha="b" * 40)
    async_session.add(event2)
    await async_session.flush()
    result = await error_group_service.upsert(
        async_session, project_id=proj.id, fingerprint="fp-race", event=event2,
    )
    await async_session.commit()

    assert result.is_new is False
    assert result.group.event_count == 2


async def test_upsert_concurrent_with_for_update_serializes(
    async_session: AsyncSession, upgraded_db,
):
    """같은 group 의 동시 UPDATE — with_for_update 직렬화 (B1 패턴).

    두 별도 session, asyncio.Event 로 T1 의 lock 보유 동안 T2 진입 강제.
    """
    proj = await _seed_project(async_session)
    # 시드 OPEN group
    group = ErrorGroup(
        project_id=proj.id, fingerprint="fp-conc",
        exception_class="KeyError", exception_message_sample="x",
        first_seen_at=datetime.utcnow(), first_seen_version_sha="a" * 40,
        last_seen_at=datetime.utcnow(), last_seen_version_sha="a" * 40,
        event_count=0, status=ErrorGroupStatus.OPEN,
    )
    async_session.add(group)
    await async_session.commit()
    project_id = proj.id

    dsn = upgraded_db["async_url"]
    engine_a = create_async_engine(dsn, echo=False)
    engine_b = create_async_engine(dsn, echo=False)
    maker_a = async_sessionmaker(engine_a, expire_on_commit=False)
    maker_b = async_sessionmaker(engine_b, expire_on_commit=False)

    inside_a = asyncio.Event()
    release = asyncio.Event()

    async def runner_t1():
        async with maker_a() as db:
            event = _make_event(proj, version_sha="a" * 40)
            db.add(event)
            await db.flush()
            # custom upsert that blocks after lock acquire (mimic real lock duration)
            from sqlalchemy import select as _select
            from sqlalchemy.exc import IntegrityError as _IE  # noqa: F401
            stmt = (
                _select(ErrorGroup)
                .where(ErrorGroup.project_id == project_id, ErrorGroup.fingerprint == "fp-conc")
                .with_for_update()
            )
            grp = (await db.execute(stmt)).scalar_one()
            grp.event_count += 1
            inside_a.set()
            await release.wait()  # T1 lock 보유 시뮬
            await db.commit()

    async def runner_t2():
        await inside_a.wait()
        await asyncio.sleep(0.05)  # T2 가 lock 대기 진입 보장
        async with maker_b() as db:
            event = _make_event(proj, version_sha="b" * 40)
            db.add(event)
            await db.flush()
            await error_group_service.upsert(
                db, project_id=project_id, fingerprint="fp-conc", event=event,
            )
            await db.commit()

    async def releaser():
        await inside_a.wait()
        await asyncio.sleep(0.3)
        release.set()

    try:
        await asyncio.gather(runner_t1(), runner_t2(), releaser())
    finally:
        await engine_a.dispose()
        await engine_b.dispose()

    # 둘 다 commit — event_count 0 + 1 (T1) + 1 (T2) = 2
    await async_session.refresh(group)
    assert group.event_count == 2
```

- [ ] **Step 2: Verify failure**

```bash
pytest tests/test_error_group_service.py -v 2>&1 | tail -15
```

Expected: 6 FAIL — service 미구현.

- [ ] **Step 3: `error_group_service.py` 구현**

`backend/app/services/error_group_service.py` 신규:

```python
"""ErrorGroup UPSERT + 자동 status 전이.

설계서: 2026-05-01-error-log-phase3-design.md §2.3
race-free: with_for_update + IntegrityError SAVEPOINT fallback (Phase 2 record_push_event 패턴).
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


def _select_for_update(project_id: UUID, fingerprint: str):
    return (
        select(ErrorGroup)
        .where(
            ErrorGroup.project_id == project_id,
            ErrorGroup.fingerprint == fingerprint,
        )
        .with_for_update()
    )


def _apply_update(group: ErrorGroup, event: LogEvent) -> bool:
    """기존 group 에 event 반영. transitioned_to_regression 반환."""
    transitioned = False
    if group.status == ErrorGroupStatus.RESOLVED:
        group.status = ErrorGroupStatus.REGRESSED
        transitioned = True
    group.last_seen_at = event.received_at
    group.last_seen_version_sha = event.version_sha
    group.event_count += 1
    if event.exception_message:
        group.exception_message_sample = event.exception_message
    return transitioned


async def upsert(
    db: AsyncSession,
    *,
    project_id: UUID,
    fingerprint: str,
    event: LogEvent,
) -> GroupResult:
    """fingerprint 별 ErrorGroup UPSERT + 자동 status 전이.

    1차: SELECT FOR UPDATE — 있으면 UPDATE 분기.
    2차 (없으면): SAVEPOINT INSERT — UNIQUE conflict catch 시 SELECT + UPDATE fallback.
    """
    stmt = _select_for_update(project_id, fingerprint)
    group = (await db.execute(stmt)).scalar_one_or_none()

    if group is not None:
        transitioned = _apply_update(group, event)
        await db.flush()
        return GroupResult(group=group, is_new=False, transitioned_to_regression=transitioned)

    # 신규 INSERT — UNIQUE conflict race 가능 → SAVEPOINT
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
        logger.info(
            "ErrorGroup UNIQUE conflict (race) for project=%s fingerprint=%s — fallback UPDATE",
            project_id, fingerprint,
        )
        # SAVEPOINT rollback 후 SELECT — 이번엔 다른 동시 caller 의 INSERT 가 보임
        existing = (await db.execute(_select_for_update(project_id, fingerprint))).scalar_one_or_none()
        if existing is None:
            # 매우 드문 race — SAVEPOINT 가 rollback 됐는데 SELECT 도 없음 → 재발생
            raise
        transitioned = _apply_update(existing, event)
        await db.flush()
        return GroupResult(group=existing, is_new=False, transitioned_to_regression=transitioned)
```

- [ ] **Step 4: Verify pass + 회귀**

```bash
pytest tests/test_error_group_service.py -v 2>&1 | tail -15
pytest -q 2>&1 | tail -3
```

Expected: 6 PASS (concurrent test 의 race 검증 deterministic). 전체 `244 passed` (238 + 6).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/error_group_service.py backend/tests/test_error_group_service.py
git commit -m "$(cat <<'EOF'
feat(error-log/phase3): error_group_service — UPSERT + 자동 status 전이

- with_for_update 로 같은 group 동시 UPDATE 직렬화 (B1 race fix 패턴)
- 신규 INSERT race: SAVEPOINT (begin_nested) + IntegrityError catch + SELECT fallback (Phase 2 record_push_event 패턴)
- 자동 전이: 신규→OPEN, RESOLVED→REGRESSED, OPEN/REGRESSED/IGNORED 그대로
- GroupResult dataclass: (group, is_new, transitioned_to_regression)
- 회귀 6건: 신규 INSERT / OPEN UPDATE / REGRESSED 전이 / IGNORED 그대로 / UNIQUE conflict fallback / 동시 UPDATE 직렬화

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: `log_alert_service` (B-lite — notify_new_error)

**Files:**
- Create: `backend/app/services/log_alert_service.py`
- Create: `backend/tests/test_log_alert_service.py` (3건)

- [ ] **Step 1: Failing tests 작성**

`backend/tests/test_log_alert_service.py` 신규:

```python
"""log_alert_service 단위 테스트 (B-lite — notify_new_error).

설계서: 2026-05-01-error-log-phase3-design.md §2.5, §3.4
"""

import uuid
from datetime import datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.error_group import ErrorGroup, ErrorGroupStatus
from app.models.log_event import LogEvent, LogLevel
from app.models.project import Project
from app.models.workspace import Workspace
from app.services import log_alert_service


async def _seed(
    db: AsyncSession,
    *,
    discord_webhook_url: str | None = "https://discord.com/api/webhooks/1/abc",
    last_alerted_new_at: datetime | None = None,
) -> tuple[Project, ErrorGroup, LogEvent]:
    ws = Workspace(name="ws", slug=f"ws-{uuid.uuid4().hex[:8]}")
    db.add(ws)
    await db.flush()
    proj = Project(
        workspace_id=ws.id, name="p",
        discord_webhook_url=discord_webhook_url,
    )
    db.add(proj)
    await db.flush()

    group = ErrorGroup(
        project_id=proj.id, fingerprint="fp-1",
        exception_class="KeyError", exception_message_sample="'preference'",
        first_seen_at=datetime.utcnow(), first_seen_version_sha="a" * 40,
        last_seen_at=datetime.utcnow(), last_seen_version_sha="a" * 40,
        event_count=1, status=ErrorGroupStatus.OPEN,
        last_alerted_new_at=last_alerted_new_at,
    )
    db.add(group)

    event = LogEvent(
        project_id=proj.id, level=LogLevel.ERROR,
        message="boom", logger_name="app.x",
        version_sha="abcdef1234567890" * 2 + "ab" * 4,  # 40자
        environment="production", hostname="h",
        emitted_at=datetime.utcnow(), received_at=datetime.utcnow(),
        exception_class="KeyError", exception_message="'preference'",
    )
    db.add(event)
    await db.commit()
    await db.refresh(proj)
    await db.refresh(group)
    await db.refresh(event)
    return proj, group, event


async def test_notify_new_error_dispatches_and_marks(
    async_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
):
    """last_alerted_new_at IS NULL → dispatcher 호출 + 마킹."""
    proj, group, event = await _seed(async_session)

    sent: list[tuple] = []
    async def fake_dispatch(db, project, content):
        sent.append((project.id, content))

    import app.services.notification_dispatcher as dispatcher_mod
    monkeypatch.setattr(dispatcher_mod, "dispatch_discord_alert", fake_dispatch)

    await log_alert_service.notify_new_error(
        async_session, project_id=proj.id, group=group, event=event,
    )

    assert len(sent) == 1
    project_id, content = sent[0]
    assert project_id == proj.id
    assert "🆕" in content
    assert "KeyError" in content
    assert "production" in content

    await async_session.refresh(group)
    assert group.last_alerted_new_at is not None


async def test_notify_new_error_skipped_when_already_alerted(
    async_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
):
    """last_alerted_new_at 이미 set → no-op."""
    proj, group, event = await _seed(
        async_session, last_alerted_new_at=datetime.utcnow(),
    )

    sent: list = []
    async def fake_dispatch(db, project, content):
        sent.append((project.id, content))

    import app.services.notification_dispatcher as dispatcher_mod
    monkeypatch.setattr(dispatcher_mod, "dispatch_discord_alert", fake_dispatch)

    await log_alert_service.notify_new_error(
        async_session, project_id=proj.id, group=group, event=event,
    )

    assert sent == []  # cooldown — 호출 안 함


async def test_notify_new_error_skipped_when_project_missing(
    async_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
):
    """project not found → no-op."""
    sent: list = []
    async def fake_dispatch(db, project, content):
        sent.append((project.id, content))

    import app.services.notification_dispatcher as dispatcher_mod
    monkeypatch.setattr(dispatcher_mod, "dispatch_discord_alert", fake_dispatch)

    fake_group = ErrorGroup(
        project_id=uuid.uuid4(),  # 존재 X
        fingerprint="fp-x",
        exception_class="KeyError", exception_message_sample="x",
        first_seen_at=datetime.utcnow(), first_seen_version_sha="a" * 40,
        last_seen_at=datetime.utcnow(), last_seen_version_sha="a" * 40,
        event_count=1, status=ErrorGroupStatus.OPEN,
    )
    fake_event = LogEvent(
        project_id=uuid.uuid4(), level=LogLevel.ERROR,
        message="x", logger_name="app", version_sha="a" * 40,
        environment="production", hostname="h",
        emitted_at=datetime.utcnow(), received_at=datetime.utcnow(),
    )

    await log_alert_service.notify_new_error(
        async_session, project_id=uuid.uuid4(), group=fake_group, event=fake_event,
    )

    assert sent == []
```

- [ ] **Step 2: Verify failure**

```bash
pytest tests/test_log_alert_service.py -v 2>&1 | tail -10
```

Expected: 3 FAIL — service 미구현.

- [ ] **Step 3: `log_alert_service.py` 구현**

`backend/app/services/log_alert_service.py` 신규:

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
    """신규 fingerprint 1회 Discord 알림. cooldown — group당 1회만.

    notification_dispatcher 통과 (Phase 6 disable 정책 자동 적용).
    """
    project = await db.get(Project, project_id)
    if project is None:
        return
    if group.last_alerted_new_at is not None:
        return  # race 방지 — 동시 신규 INSERT 의 다른 caller 가 먼저 알림 가능

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

    group.last_alerted_new_at = datetime.utcnow()
    await db.commit()
```

- [ ] **Step 4: Verify pass + 회귀**

```bash
pytest tests/test_log_alert_service.py -v 2>&1 | tail -10
pytest -q 2>&1 | tail -3
```

Expected: 3 PASS, 전체 `247 passed` (244 + 3).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/log_alert_service.py backend/tests/test_log_alert_service.py
git commit -m "$(cat <<'EOF'
feat(error-log/phase3): log_alert_service.notify_new_error (B-lite)

- 신규 fingerprint 1회 Discord 알림
- notification_dispatcher 경유 — Phase 6 disable 정책 자동 적용
- cooldown: last_alerted_new_at IS NULL 1차 게이트 + race 방지 2차 체크
- 발송 후 last_alerted_new_at = now + commit
- 메시지: 🆕 새 에러 — {class}, 메시지 첫 줄 200자, 첫 발생 short SHA + environment
- spike/regression 은 Phase 6 본편 추후 추가 예정
- 회귀 3건: 정상 알림 / cooldown skip / project 없음 skip

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: `fingerprint_processor` (composition wrapper)

**Files:**
- Create: `backend/app/services/fingerprint_processor.py`
- Create: `backend/tests/test_fingerprint_processor.py` (4건)

- [ ] **Step 1: Failing tests 작성**

`backend/tests/test_fingerprint_processor.py` 신규:

```python
"""fingerprint_processor 단위 테스트.

설계서: 2026-05-01-error-log-phase3-design.md §2.4, §3.3
"""

import uuid
from datetime import datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.error_group import ErrorGroup, ErrorGroupStatus
from app.models.log_event import LogEvent, LogLevel
from app.models.project import Project
from app.models.workspace import Workspace
from app.services import fingerprint_processor


async def _seed(db: AsyncSession) -> tuple[Project, LogEvent]:
    ws = Workspace(name="ws", slug=f"ws-{uuid.uuid4().hex[:8]}")
    db.add(ws)
    await db.flush()
    proj = Project(workspace_id=ws.id, name="p")
    db.add(proj)
    await db.flush()
    event = LogEvent(
        project_id=proj.id, level=LogLevel.ERROR,
        message="boom", logger_name="app.x", version_sha="a" * 40,
        environment="production", hostname="h",
        emitted_at=datetime.utcnow(), received_at=datetime.utcnow(),
        exception_class="KeyError", exception_message="'preference'",
        stack_frames=[
            {"filename": "/app/backend/x.py", "lineno": 10, "name": "do_thing"},
        ],
    )
    db.add(event)
    await db.commit()
    await db.refresh(proj)
    await db.refresh(event)
    return proj, event


async def test_process_normal_path(
    async_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
):
    """fingerprint 계산 + group UPSERT + fingerprinted_at 마킹 + commit."""
    proj, event = await _seed(async_session)

    notifications: list = []
    async def fake_notify(db, *, project_id, group, event):
        notifications.append((project_id, group.id))

    import app.services.log_alert_service as alert_mod
    monkeypatch.setattr(alert_mod, "notify_new_error", fake_notify)

    await fingerprint_processor.process(async_session, event)

    await async_session.refresh(event)
    assert event.fingerprint is not None
    assert len(event.fingerprint) == 40
    assert event.fingerprinted_at is not None

    # 신규 group 만들어짐
    groups = (await async_session.execute(
        select(ErrorGroup).where(ErrorGroup.project_id == proj.id)
    )).scalars().all()
    assert len(groups) == 1
    assert groups[0].fingerprint == event.fingerprint

    # 신규 → notify 호출
    assert len(notifications) == 1


async def test_process_existing_group_no_notify(
    async_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
):
    """기존 group → notify_new_error 호출 안 함."""
    proj, event1 = await _seed(async_session)

    notifications: list = []
    async def fake_notify(db, *, project_id, group, event):
        notifications.append((project_id, group.id))

    import app.services.log_alert_service as alert_mod
    monkeypatch.setattr(alert_mod, "notify_new_error", fake_notify)

    # 1차 — 신규 → notify 1회
    await fingerprint_processor.process(async_session, event1)
    assert len(notifications) == 1

    # 2차 — 같은 fingerprint event
    event2 = LogEvent(
        project_id=proj.id, level=LogLevel.ERROR,
        message="boom2", logger_name="app.x", version_sha="b" * 40,
        environment="production", hostname="h",
        emitted_at=datetime.utcnow(), received_at=datetime.utcnow(),
        exception_class="KeyError", exception_message="'preference'",
        stack_frames=[
            {"filename": "/app/backend/x.py", "lineno": 10, "name": "do_thing"},
        ],
    )
    async_session.add(event2)
    await async_session.commit()
    await async_session.refresh(event2)

    await fingerprint_processor.process(async_session, event2)

    # 기존 group → notify 호출 안 함 (count 1 그대로)
    assert len(notifications) == 1


async def test_process_handles_no_stack_frames(async_session: AsyncSession):
    """stack_frames 없는 event → fallback fingerprint 동작."""
    ws = Workspace(name="ws", slug=f"ws-{uuid.uuid4().hex[:8]}")
    async_session.add(ws)
    await async_session.flush()
    proj = Project(workspace_id=ws.id, name="p")
    async_session.add(proj)
    await async_session.flush()
    event = LogEvent(
        project_id=proj.id, level=LogLevel.ERROR,
        message="boom", logger_name="app.x", version_sha="a" * 40,
        environment="production", hostname="h",
        emitted_at=datetime.utcnow(), received_at=datetime.utcnow(),
        exception_class="KeyError", exception_message="'preference'",
        stack_frames=None,
    )
    async_session.add(event)
    await async_session.commit()
    await async_session.refresh(event)

    await fingerprint_processor.process(async_session, event)
    await async_session.refresh(event)

    assert event.fingerprint is not None
    assert event.fingerprinted_at is not None


async def test_process_uses_unknown_class_when_missing(async_session: AsyncSession):
    """exception_class 가 None 이면 'UnknownError' 로 처리."""
    ws = Workspace(name="ws", slug=f"ws-{uuid.uuid4().hex[:8]}")
    async_session.add(ws)
    await async_session.flush()
    proj = Project(workspace_id=ws.id, name="p")
    async_session.add(proj)
    await async_session.flush()
    event = LogEvent(
        project_id=proj.id, level=LogLevel.ERROR,
        message="boom", logger_name="app.x", version_sha="a" * 40,
        environment="production", hostname="h",
        emitted_at=datetime.utcnow(), received_at=datetime.utcnow(),
        exception_class=None,  # 명시적으로 None
        exception_message="something",
        stack_frames=None,
    )
    async_session.add(event)
    await async_session.commit()
    await async_session.refresh(event)

    await fingerprint_processor.process(async_session, event)
    await async_session.refresh(event)
    assert event.fingerprint is not None

    groups = (await async_session.execute(
        select(ErrorGroup).where(ErrorGroup.project_id == proj.id)
    )).scalars().all()
    assert len(groups) == 1
    assert groups[0].exception_class == "UnknownError"
```

- [ ] **Step 2: Verify failure**

```bash
pytest tests/test_fingerprint_processor.py -v 2>&1 | tail -10
```

Expected: 4 FAIL — service 미구현.

- [ ] **Step 3: `fingerprint_processor.py` 구현**

`backend/app/services/fingerprint_processor.py` 신규:

```python
"""fingerprint composition wrapper — 계산 + group UPSERT + alert + 마킹.

설계서: 2026-05-01-error-log-phase3-design.md §2.4, §3.3
"""

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.log_event import LogEvent
from app.services import error_group_service, fingerprint_service, log_alert_service


async def process(db: AsyncSession, event: LogEvent) -> None:
    """fingerprint 계산 → ErrorGroup UPSERT → fingerprinted_at 마킹 + commit → 신규면 알림.

    설계서 §2.4 — commit 후 알림 (Phase 6 학습: DB 일관 상태에서 발송).
    """
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

- [ ] **Step 4: Verify pass + 회귀**

```bash
pytest tests/test_fingerprint_processor.py -v 2>&1 | tail -10
pytest -q 2>&1 | tail -3
```

Expected: 4 PASS, 전체 `251 passed` (247 + 4).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/fingerprint_processor.py backend/tests/test_fingerprint_processor.py
git commit -m "$(cat <<'EOF'
feat(error-log/phase3): fingerprint_processor — composition wrapper

- fingerprint_service.compute → error_group_service.upsert → fingerprinted_at 마킹 → commit → 신규면 alert
- Phase 6 학습: commit 후 알림 (DB 일관 상태)
- exception_class None → 'UnknownError'
- 회귀 4건: 정상 / 기존 group no-notify / no stack_frames fallback / class None → UnknownError

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Ingest endpoint BackgroundTask 통합

**Files:**
- Modify: `backend/app/services/log_ingest_service.py` (`ingest_batch` 시그니처 확장)
- Modify: `backend/app/api/v1/endpoints/log_ingest.py` (BackgroundTasks Depends + helper)
- Modify: `backend/tests/test_log_ingest_service.py` (3-tuple unpack 갱신)
- Modify: `backend/tests/test_log_ingest_endpoint.py` (신규 1건)

- [ ] **Step 1: Failing test 추가 + 기존 테스트 시그니처 갱신**

`backend/tests/test_log_ingest_endpoint.py` 끝에 추가:

```python
async def test_ingest_schedules_background_task_for_error_events_only(
    client_with_db, async_session: AsyncSession,
):
    """ERROR/CRITICAL event 만 BackgroundTask 큐. INFO/WARNING 은 큐 안 됨."""
    proj, token, secret = await _seed_token(async_session)
    bearer = f"Bearer {token.id}.{secret}"

    # 3 events: INFO / ERROR / CRITICAL
    events = [_valid_event() for _ in range(3)]
    events[0]["level"] = "INFO"
    events[1]["level"] = "ERROR"
    events[2]["level"] = "CRITICAL"

    scheduled: list = []

    # patch fingerprint_processor.process (BackgroundTask 가 호출)
    from unittest.mock import AsyncMock, patch

    async def fake_process(db, event):
        scheduled.append(event.id)

    with patch(
        "app.services.fingerprint_processor.process",
        side_effect=fake_process,
    ):
        res = await client_with_db.post(
            "/api/v1/log-ingest",
            json={"events": events},
            headers={"Authorization": bearer},
        )

    assert res.status_code == 200
    assert res.json()["accepted"] == 3
    # FastAPI BackgroundTasks 는 응답 후 실행 — TestClient 는 동기적으로 마무리
    # ERROR + CRITICAL 만 큐 — INFO 는 안 됨
    assert len(scheduled) == 2
```

`backend/tests/test_log_ingest_service.py` 의 기존 테스트들 — `ingest_batch` 호출 부분의 unpack 패턴 갱신:

```python
# Before:
accepted, rejected = await log_ingest_service.ingest_batch(
    async_session, token=token, payload_dict={"events": events},
    dropped_since_last=None,
)

# After:
accepted, rejected, accepted_ids = await log_ingest_service.ingest_batch(
    async_session, token=token, payload_dict={"events": events},
    dropped_since_last=None,
)
```

영향 받는 테스트: `test_ingest_batch_partial_success`, `test_ingest_batch_dropped_header_logs_warning`. 두 곳 모두 unpack 갱신.

- [ ] **Step 2: Verify failure (시그니처 변경 전)**

```bash
cd backend && source venv/bin/activate
pytest tests/test_log_ingest_endpoint.py::test_ingest_schedules_background_task_for_error_events_only -v 2>&1 | tail -10
```

Expected: FAIL — 신규 endpoint 가 BackgroundTask 안 큐 (또는 시그니처 불일치).

`pytest tests/test_log_ingest_service.py -v 2>&1 | tail -10` — unpack 변경한 2 테스트가 fail (ValueError: too many/few values to unpack).

- [ ] **Step 3: `ingest_batch` 시그니처 확장**

`backend/app/services/log_ingest_service.py` 의 `ingest_batch`:

```python
async def ingest_batch(
    db: AsyncSession,
    *,
    token: LogIngestToken,
    payload_dict: dict[str, Any],
    dropped_since_last: int | None = None,
    now: datetime | None = None,
) -> tuple[int, list[dict], list[UUID]]:
    """end-to-end: rate limit → validate (partial) → insert → commit.

    Returns: (accepted_count, rejected_list, accepted_event_ids).
    """
    # ... 기존 본문 그대로 ...
    # 마지막 return 변경:
    accepted_ids = [e.id for e in accepted]
    return len(accepted), rejected, accepted_ids
```

(`accepted` 가 LogEvent 인스턴스 list — `db.flush()` 후 SQLAlchemy 가 id 채움. 시그니처와 return 만 변경.)

- [ ] **Step 4: Endpoint BackgroundTasks 통합**

`backend/app/api/v1/endpoints/log_ingest.py` 변경:

```python
from fastapi import BackgroundTasks
from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.log_event import LogEvent, LogLevel


async def _process_log_event_in_new_session(event_id) -> None:
    """BackgroundTask — 자체 session, fingerprint_processor 호출, 멱등."""
    from app.services import fingerprint_processor

    try:
        async with AsyncSessionLocal() as db:
            event = await db.get(LogEvent, event_id)
            if event is None or event.fingerprinted_at is not None:
                return
            await fingerprint_processor.process(db, event)
    except Exception:
        logger.exception("background fingerprint processing failed for event %s", event_id)


@router.post("/log-ingest")
async def ingest_logs(
    request: Request,
    background_tasks: BackgroundTasks,
    authorization: str | None = Header(default=None),
    content_encoding: str | None = Header(default=None),
    x_pslog_dropped_since_last: int | None = Header(default=None, alias="X-pslog-Dropped-Since-Last"),
    db: AsyncSession = Depends(get_db),
):
    """... 기존 docstring ..."""
    body = await request.body()

    if content_encoding == "gzip":
        try:
            body = gzip.decompress(body)
        except Exception:
            raise HTTPException(status_code=400, detail="gzip decode failed")

    try:
        payload = json.loads(body)
    except Exception:
        raise HTTPException(status_code=400, detail="invalid JSON body")

    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="payload must be a JSON object")

    key_id, secret = await log_ingest_service.parse_token(authorization)
    token = await log_ingest_service.verify_token(db, key_id, secret)

    try:
        accepted, rejected, accepted_ids = await log_ingest_service.ingest_batch(
            db, token=token,
            payload_dict=payload,
            dropped_since_last=x_pslog_dropped_since_last,
        )
    except HTTPException:
        raise
    except Exception:
        logger.exception("log-ingest unexpected error")
        raise HTTPException(status_code=500, detail="Internal error")

    # ERROR↑ event 만 BackgroundTask 큐 — Phase 3 fingerprint 처리 trigger
    if accepted_ids:
        error_stmt = (
            select(LogEvent.id)
            .where(LogEvent.id.in_(accepted_ids))
            .where(LogEvent.level.in_([LogLevel.ERROR, LogLevel.CRITICAL]))
        )
        error_ids = (await db.execute(error_stmt)).scalars().all()
        for eid in error_ids:
            background_tasks.add_task(_process_log_event_in_new_session, eid)

    if accepted == 0 and rejected:
        return JSONResponse(
            status_code=400,
            content={"accepted": 0, "rejected": rejected},
        )
    return {"accepted": accepted, "rejected": rejected}
```

- [ ] **Step 5: Verify pass + 회귀**

```bash
pytest tests/test_log_ingest_endpoint.py -v 2>&1 | tail -15
pytest tests/test_log_ingest_service.py -v 2>&1 | tail -15
pytest -q 2>&1 | tail -3
```

Expected: 신규 1 PASS, 기존 시그니처 갱신 2건 다시 PASS, 전체 `252 passed` (251 + 1).

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/log_ingest_service.py backend/app/api/v1/endpoints/log_ingest.py backend/tests/test_log_ingest_service.py backend/tests/test_log_ingest_endpoint.py
git commit -m "$(cat <<'EOF'
feat(error-log/phase3): ingest endpoint BackgroundTask — fingerprint 처리 trigger

- ingest_batch 시그니처: (int, list[dict]) → (int, list[dict], list[UUID])
  - accepted_event_ids 추가 — caller (endpoint) 가 BackgroundTask 큐
- ingest endpoint: BackgroundTasks Depends + ERROR↑ 분류 SELECT + add_task
- _process_log_event_in_new_session: 자체 session, 멱등 (fingerprinted_at 체크), Phase 4 fresh-session 패턴
- 기존 ingest_batch 테스트 2건 시그니처 갱신 (3-tuple unpack)
- 회귀 1건: ERROR/CRITICAL 만 큐, INFO/WARNING 안 됨

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: `log_fingerprint_reaper` + lifespan hook

**Files:**
- Create: `backend/app/services/log_fingerprint_reaper.py`
- Modify: `backend/app/main.py` (lifespan 에 reaper 추가)
- Create: `backend/tests/test_log_fingerprint_reaper.py` (3건)

- [ ] **Step 1: Failing tests 작성**

`backend/tests/test_log_fingerprint_reaper.py` 신규:

```python
"""log_fingerprint_reaper 단위 테스트.

설계서: 2026-05-01-error-log-phase3-design.md §2.7, §3.6
"""

import uuid
from datetime import datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.error_group import ErrorGroup
from app.models.log_event import LogEvent, LogLevel
from app.models.project import Project
from app.models.workspace import Workspace
from app.services import log_fingerprint_reaper


async def _seed_unfingerprinted_events(
    async_session: AsyncSession, *, count: int, level: LogLevel,
) -> Project:
    ws = Workspace(name="ws", slug=f"ws-{uuid.uuid4().hex[:8]}")
    async_session.add(ws)
    await async_session.flush()
    proj = Project(workspace_id=ws.id, name="p")
    async_session.add(proj)
    await async_session.flush()
    for i in range(count):
        async_session.add(LogEvent(
            project_id=proj.id, level=level,
            message=f"boom-{i}", logger_name="app.x", version_sha="a" * 40,
            environment="production", hostname="h",
            emitted_at=datetime.utcnow(), received_at=datetime.utcnow(),
            exception_class="KeyError", exception_message=f"k-{i}",
            fingerprint=None, fingerprinted_at=None,
        ))
    await async_session.commit()
    await async_session.refresh(proj)
    return proj


async def test_reaper_processes_unfingerprinted_errors(async_session: AsyncSession):
    """ERROR↑ unfingerprinted → fingerprint + group + 마킹."""
    proj = await _seed_unfingerprinted_events(async_session, count=3, level=LogLevel.ERROR)

    await log_fingerprint_reaper.run_reaper_once()

    # 모두 처리됨
    rows = (await async_session.execute(
        select(LogEvent).where(LogEvent.project_id == proj.id)
    )).scalars().all()
    assert all(e.fingerprint is not None for e in rows)
    assert all(e.fingerprinted_at is not None for e in rows)

    # 같은 fingerprint (같은 logger / class / no stack_frames → fallback) — 1 group
    groups = (await async_session.execute(
        select(ErrorGroup).where(ErrorGroup.project_id == proj.id)
    )).scalars().all()
    assert len(groups) == 1
    assert groups[0].event_count == 3


async def test_reaper_skips_info_level(async_session: AsyncSession):
    """INFO/WARNING level 은 회수 안 함 (filter)."""
    proj = await _seed_unfingerprinted_events(async_session, count=2, level=LogLevel.WARNING)

    await log_fingerprint_reaper.run_reaper_once()

    rows = (await async_session.execute(
        select(LogEvent).where(LogEvent.project_id == proj.id)
    )).scalars().all()
    # 회수 안 함 — fingerprint 그대로 NULL
    assert all(e.fingerprint is None for e in rows)
    assert all(e.fingerprinted_at is None for e in rows)


async def test_reaper_chunked_processes_large_backlog(
    async_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
):
    """200건 backlog → 100/batch 로 2 batch 처리."""
    proj = await _seed_unfingerprinted_events(async_session, count=200, level=LogLevel.ERROR)

    # batch_size 작게 — 테스트 빠르게
    monkeypatch.setattr(log_fingerprint_reaper, "REAPER_BATCH_SIZE", 100)

    await log_fingerprint_reaper.run_reaper_once()

    rows = (await async_session.execute(
        select(LogEvent).where(LogEvent.project_id == proj.id)
    )).scalars().all()
    assert all(e.fingerprinted_at is not None for e in rows)
```

- [ ] **Step 2: Verify failure**

```bash
pytest tests/test_log_fingerprint_reaper.py -v 2>&1 | tail -10
```

Expected: 3 FAIL — service 미구현.

- [ ] **Step 3: `log_fingerprint_reaper.py` 구현**

`backend/app/services/log_fingerprint_reaper.py` 신규:

```python
"""부팅 시 미처리 LogEvent 회수 — fingerprint 처리.

설계서: 2026-05-01-error-log-phase3-design.md §2.7, §3.6
chunked 100건/batch — 큰 backlog 안전. fresh session per event (Phase 4 학습).
"""

import logging

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.log_event import LogEvent, LogLevel
from app.services import fingerprint_processor

logger = logging.getLogger(__name__)

REAPER_BATCH_SIZE = 100


async def run_reaper_once() -> None:
    """level >= ERROR AND fingerprinted_at IS NULL 회수.

    `idx_log_unfingerprinted` partial index 사용 (Phase 1 alembic 이미 생성).
    received_at ASC — 오래된 것 우선.
    """
    while True:
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

- [ ] **Step 4: lifespan hook 등록**

`backend/app/main.py` 의 `lifespan` 함수 — 기존 `reap_pending_events` 처리 직후, `start_weekly_scheduler` 시작 전 (yield 전) 에 추가:

```python
# 파일 상단 import 에 추가:
from app.services import log_fingerprint_reaper
```

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    # ... 기존 push_event_reaper 처리 (line 22-48) ...

    # Phase 3 — log_fingerprint_reaper: 미처리 ERROR↑ LogEvent 회수
    try:
        await log_fingerprint_reaper.run_reaper_once()
    except Exception:
        logger.exception("log_fingerprint_reaper failed at startup")

    # Startup: 주간 리포트 스케줄러 시작
    scheduler_task = asyncio.create_task(start_weekly_scheduler())
    yield
    # Shutdown: 스케줄러 정리
    scheduler_task.cancel()
```

- [ ] **Step 5: Verify pass + 회귀**

```bash
pytest tests/test_log_fingerprint_reaper.py -v 2>&1 | tail -10
pytest -q 2>&1 | tail -3
```

Expected: 3 PASS, 전체 `255 passed` (252 + 3).

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/log_fingerprint_reaper.py backend/app/main.py backend/tests/test_log_fingerprint_reaper.py
git commit -m "$(cat <<'EOF'
feat(error-log/phase3): log_fingerprint_reaper + lifespan hook

- 부팅 시 1회 회수 — level >= ERROR AND fingerprinted_at IS NULL
- chunked 100/batch — idx_log_unfingerprinted partial index 사용 (Phase 1 alembic)
- received_at ASC — 오래된 것 우선
- fresh session per event (Phase 4 학습 — poison session 회피)
- main.py lifespan: push_event_reaper 직후, scheduler 시작 전 등록
- 회귀 3건: 정상 회수 / INFO/WARNING skip / chunked 200건 처리

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: 최종 회귀 + handoff + PR

- [ ] **Step 1: 전체 backend 회귀**

```bash
cd /Users/arden/Documents/ardensdevspace/pslog/.worktrees/error-log-phase3-fingerprint/backend
source venv/bin/activate
pytest -q 2>&1 | tail -3
```

Expected: `255 passed` (230 baseline + 25 신규: 8 fingerprint + 6 group + 3 alert + 4 processor + 1 endpoint + 3 reaper).

- [ ] **Step 2: Frontend 영향 없음** — frontend 변경 없음, skip.

- [ ] **Step 3: handoff 갱신**

`handoffs/main.md` 상단에 새 섹션:

```markdown
## 2026-05-01 (Error-log Phase 3 — Fingerprint + ErrorGroup + Reaper + B-lite Alert)

- [x] **Error-log Phase 3** — 브랜치 `feature/error-log-phase3-fingerprint`
  - [x] **`fingerprint_service`**: 결정적 SHA1 (정규화 6 규칙 — APP_PROJECT_ROOT env var + 휴리스틱 / line 제거 / 메모리 주소 마스킹 / 함수명 유지 / framework 스킵 / 입력 포맷). Fallback: stack_frames None 또는 모두 framework.
  - [x] **`error_group_service`**: ErrorGroup UPSERT + 자동 status 전이 (신규→OPEN, RESOLVED→REGRESSED). race-free — `with_for_update` (B1 패턴) + UNIQUE conflict SAVEPOINT (Phase 2 record_push_event 패턴).
  - [x] **`log_alert_service.notify_new_error`** (B-lite scope): 신규 fingerprint 1회 Discord 알림. notification_dispatcher 통과 (Phase 6 disable 정책 자동). cooldown — last_alerted_new_at IS NULL.
  - [x] **`fingerprint_processor`**: composition (fingerprint → group → fingerprinted_at + commit → 신규면 alert).
  - [x] **Ingest endpoint BackgroundTask 통합**: ingest_batch 시그니처에 accepted_event_ids 추가. endpoint 가 ERROR↑ 분류 후 add_task. _process_log_event_in_new_session helper (자체 session + 멱등).
  - [x] **`log_fingerprint_reaper` + lifespan hook**: 부팅 시 1회, chunked 100/batch, idx_log_unfingerprinted partial index 사용. push_event_reaper 패턴.
  - [x] **마이그레이션 신규 없음** — Phase 1 alembic 이 모든 모델 + 인덱스 (`idx_log_unfingerprinted` partial 포함) 이미 포함.
  - [x] **검증**: backend **255 tests pass** (230 baseline + 25 신규: 8 fingerprint + 6 group + 3 alert + 4 processor + 1 endpoint + 3 reaper).

### 마지막 커밋

- pslog: `<sha> docs(handoff+plan): Error-log Phase 3 완료 + Phase 4 다음 할 일`
- 브랜치 base: `90cce78` (main, Error-log Phase 2a PR #16 머지 직후)

### 다음 (Error-log Phase 4 — 조회 API + Git 컨텍스트 join)

본 phase 가 ErrorGroup 데이터 채워줌 — Phase 4 가 사용자 노출 (조회):
- `log_query_service` + `GET /errors`, `GET /errors/{group_id}` (Handoff/Task/GitPushEvent join)
- 직전 정상 SHA 찾기 알고리즘
- 풀텍스트 검색 endpoint (pg_trgm, message gin_trgm_ops 인덱스 Phase 1 에 이미)
- 핵심 가치 전달 — UI 없이 API 만으로도 curl 검증 가능

또는 Phase 5 (UI) — Phase 4 + 5 같이 묶음 가능.

### 블로커

없음

### 메모 (2026-05-01 Error-log Phase 3 추가)

- **race-free UPSERT 패턴 (조합)**: `with_for_update()` 가 같은 group 동시 UPDATE 직렬화, 신규 INSERT race 는 `begin_nested() + IntegrityError catch + SELECT fallback` (Phase 2 record_push_event 패턴 재사용). 두 mechanism 가 다른 case 커버 — 같이 쓰면 race-free.
- **commit 후 알림 패턴 (Phase 6 학습 적용)**: fingerprint_processor 가 db.commit() 후 notify_new_error 호출. DB 일관 상태에서 발송. alert_service 자체도 commit 마킹 — `last_alerted_new_at = now`.
- **BackgroundTask + reaper 멱등 패턴**: 양쪽에서 `event.fingerprinted_at IS NOT NULL` 체크. 둘 중 하나만 처리. reaper 의 chunked 100/batch + idx partial index 가 large backlog 안전.
- **fresh session per event (Phase 4 학습)**: reaper 와 BackgroundTask 모두 같은 패턴 — 단일 event poison 이 다음 event 처리에 영향 없음.
- **fingerprint 정규화 결정성**: line 제거 + 메모리 주소 마스킹 + framework 스킵 — 같은 버그를 다른 group 으로 분리 안 하고, 다른 버그를 같은 group 으로 합치지 않음. spec §4.1 의 균형점.
- **Fallback 의도**: stack_frames None 또는 모두 framework 라도 ErrorGroup 만들어짐 — fingerprint 약하지만 사용자 가시화 (사고 가능). spec §7.
- **B-lite alert scope**: 신규 fingerprint 1종만. spike (메모리 카운터 + 30분 cooldown) / regression (자동 transition 알림) 은 Phase 6 본편. error_group_service.upsert 가 `transitioned_to_regression` 신호 이미 return — Phase 6 alert_service 가 사용 예정.
- **마이그레이션 신규 없음 학습 (Phase 6 / Phase 2a 와 같은 패턴)**: Phase 1 통합 alembic 이 모든 모델/인덱스 포함. error-log 의 본 phase 도 schema 변경 0 — 순수 service/endpoint 레이어.
- **next 가능 옵션**: Phase 4 (조회 + git join) 또는 Phase 4+5 (조회 + UI) 묶음. Phase 6 알림 본편 (spike/regression) 은 사용자 dogfooding 후 결정.
```

- [ ] **Step 4: handoff + plan + spec commit**

```bash
cd /Users/arden/Documents/ardensdevspace/pslog/.worktrees/error-log-phase3-fingerprint
git add handoffs/main.md docs/superpowers/plans/2026-05-01-error-log-phase3-fingerprint.md
git commit -m "$(cat <<'EOF'
docs(handoff+plan): Error-log Phase 3 완료 + Phase 4 다음 할 일

- handoffs/main.md 에 2026-05-01 Error-log Phase 3 섹션 추가 (255 tests, B-lite alert)
- docs/superpowers/plans/2026-05-01-error-log-phase3-fingerprint.md 신규 (구현 plan 보존)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 5: Push + PR**

```bash
git push -u origin feature/error-log-phase3-fingerprint
gh pr create \
  --title "feat(error-log/phase3): fingerprint + ErrorGroup + reaper + B-lite alert" \
  --body "$(cat <<'EOF'
## Summary

error-log spec Phase 3 (fingerprint + ErrorGroup) + Phase 2b 의 reaper 합본 + **B-lite scope** 의 신규 fingerprint Discord 알림 1종. Phase 2a 가 깐 raw LogEvent 인입 위에 그룹화 / 자동 status 전이 / 재발 감지 / 사용자 알림까지.

- **\`fingerprint_service\`**: 결정적 SHA1 (정규화 6 규칙 spec §4.1) + fallback 2종
- **\`error_group_service\`**: race-free UPSERT (with_for_update + SAVEPOINT IntegrityError fallback) + 자동 status 전이 (신규→OPEN, RESOLVED→REGRESSED)
- **\`log_alert_service.notify_new_error\`** (B-lite): 신규 fingerprint 1회 Discord 알림. notification_dispatcher 통과 (Phase 6 disable 정책 자동). spike/regression 은 Phase 6 본편.
- **\`fingerprint_processor\`**: composition wrapper (Phase 6 학습 — commit 후 알림)
- **Ingest endpoint BackgroundTask 통합**: ERROR↑ 만 큐 (ingest_batch 시그니처 확장)
- **\`log_fingerprint_reaper\`**: 부팅 시 1회, chunked 100/batch, lifespan hook
- **마이그레이션 신규 없음** — Phase 1 alembic 이 모든 모델 + idx_log_unfingerprinted partial 포함

## Test plan

- [x] backend **255 tests pass** (230 baseline + 25 신규)
- [x] race fix 패턴 검증 — same group 동시 UPDATE 직렬화 (deterministic asyncio.Event 두 session)
- [ ] e2e — 사용자 직접:
  - app-chak 의 \`logger.error("test")\` → pslog DB 의 log_events INSERT + fingerprint 자동 처리
  - pslog DB 의 error_groups → 1 row, status=OPEN, fingerprint 결정성 SQL 확인
  - pslog Discord 채널 — 🆕 새 에러 알림 1회 도착
  - 같은 에러 다시 → event_count++, 알림 안 옴 (cooldown)
  - 다른 에러 → 새 group + 새 알림

## 다음 (Phase 4 — 조회 API + Git 컨텍스트 join)

\`log_query_service\` + \`GET /errors\`, \`GET /errors/{id}\` (Handoff/Task/GitPushEvent join) + 직전 정상 SHA 찾기 + pg_trgm 풀텍스트.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Self-Review Pass

**1. Spec coverage** — spec §1-§7 vs plan tasks 매핑:

| Spec 항목 | Plan task |
|---|---|
| §2.1 fingerprint 정규화 6 규칙 | Task 1 (8 tests) |
| §2.2 fingerprint Fallback 2 종 | Task 1 (8 tests 안에 포함) |
| §2.3 ErrorGroup UPSERT + race-free | Task 2 (6 tests) |
| §2.4 fingerprint_processor composition | Task 4 (4 tests) |
| §2.5 B-lite notify_new_error | Task 3 (3 tests) |
| §2.6 Ingest BackgroundTask 통합 | Task 5 (1 신규 + 시그니처 갱신) |
| §2.7 log_fingerprint_reaper + lifespan | Task 6 (3 tests) |
| §2.8 APP_PROJECT_ROOT 처리 | Task 1 (config 변경 포함) |
| §2.9 Failure modes | Task 1-6 분산 — silent log / fresh session / SAVEPOINT fallback / 멱등 체크 모두 구현 |
| §2.10 멱등성 보장 | Task 5 의 _process helper + Task 6 의 reaper 양쪽에서 fingerprinted_at 체크 |

**2. Placeholder scan** — `<sha>` 만 (Task 7 handoff commit 후 자리표시). "TBD/TODO" 0.

**3. Type / signature consistency**:
- `fingerprint_service.compute(*, exception_class, stack_frames, exception_message=None) -> str` — Task 1 정의 ↔ Task 4 fingerprint_processor 호출 일관
- `error_group_service.upsert(db, *, project_id, fingerprint, event) -> GroupResult` — Task 2 정의 ↔ Task 4 호출 일관
- `GroupResult(group, is_new, transitioned_to_regression)` — Task 2 정의 ↔ Task 4 사용 일관
- `log_alert_service.notify_new_error(db, *, project_id, group, event) -> None` — Task 3 정의 ↔ Task 4 호출 일관
- `fingerprint_processor.process(db, event) -> None` — Task 4 정의 ↔ Task 5 endpoint helper 호출 + Task 6 reaper 호출 일관
- `log_fingerprint_reaper.run_reaper_once() -> None` — Task 6 정의 ↔ Task 6 lifespan 호출 일관
- `log_ingest_service.ingest_batch(...) -> tuple[int, list[dict], list[UUID]]` — Task 5 시그니처 변경 ↔ endpoint + 기존 테스트 갱신 일관

**4. 의존 순서**:
- Task 1 (fingerprint_service) → Task 2 (error_group_service, 독립) → Task 3 (alert_service, 독립) → Task 4 (processor, 1+2+3 사용) → Task 5 (endpoint integration, 4 사용) → Task 6 (reaper, 4 사용) → Task 7 (PR)
- Task 2/3 은 Task 1 후 병렬 가능 (의존 없음). 본 plan 은 순차로 — implementer 1명 가정.

**5. 테스트 결정성**:
- fingerprint 정규화 — 결정적 SHA1
- group UPSERT — same session 직렬, 별 session 동시는 with_for_update 직렬화 (deterministic)
- alert — monkeypatch dispatcher
- processor — monkeypatch alert
- endpoint — patch fingerprint_processor.process
- reaper — chunked 200건 → 100씩 2 batch (monkeypatch BATCH_SIZE)

**6. 학습 적용 확인**:
- B1 학습: with_for_update — Task 2 ✓
- Phase 2 학습: SAVEPOINT IntegrityError fallback — Task 2 ✓
- Phase 4 학습: fresh session per event — Task 5/6 의 BackgroundTask helper + reaper ✓
- Phase 6 학습: commit 후 알림 (DB 일관 상태) — Task 4 fingerprint_processor ✓

문제 없음. 진행 가능.
