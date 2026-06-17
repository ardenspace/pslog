# Error-log Phase 2a — Ingest Endpoint + Token API Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** error-log spec Phase 2 의 첫 sub-phase — app-chak 의 pslog_log_handler 가 pslog 에 batch HTTP push 할 수 있도록 토큰 발급/폐기 API + ingest endpoint + rate limit 도입. 본 phase 머지 즉시 e2e 동작 (LogEvent 가 pslog DB 에 들어옴, fingerprint=NULL).

**Architecture:** 8 task 분할 — Pydantic schemas → service 의 6 함수 (4 task: parse_token+verify_token / check_rate_limit / validate_event+insert_events / ingest_batch) → token API (POST/DELETE) → ingest endpoint + router wiring → 최종 회귀/PR. 모델/마이그레이션 신규 X (Phase 1 alembic 이 모든 컬럼 포함). bcrypt cost 12 + asyncio.to_thread 로 event loop block 회피. partial validation 200 + rejected list. token별 rate_limit_per_minute (기본 600) + RateLimitWindow PostgreSQL UPSERT.

**Tech Stack:** FastAPI 0.115, SQLAlchemy 2.0 async, Pydantic v2, bcrypt < 4 (이미 passlib 가 의존), pytest + testcontainers PostgreSQL.

**선행 조건:**
- pslog `main` = `7e51c20` (Phase 6 PR #15 머지 직후)
- alembic head = `7c6e0c9bb915` (Phase 6 컬럼 포함)
- backend tests baseline = **198 passing**
- LogIngestToken / RateLimitWindow / LogEvent 모델 + 컬럼 모두 Phase 1 에 포함됨
- spec: `docs/superpowers/specs/2026-05-01-error-log-phase2-ingest-design.md`

**중요한 계약:**

- **토큰 형식**: `<key_id>.<secret>` — `key_id` = `LogIngestToken.id` (UUID 36자), `secret` = `secrets.token_urlsafe(32)` (256-bit base64 ~43자). DB 에는 `bcrypt(secret, rounds=12)` hash 만 저장. 발급 응답에 평문 `f"{key_id}.{secret}"` 1회만.
- **bcrypt async wrapping**: `await asyncio.to_thread(bcrypt.checkpw, secret.encode(), hash.encode())` — event loop block 회피. cost 12 (~250ms).
- **timing attack 회피**: 401 detail 모두 `"Invalid token"` 통일 (key_id lookup fail / bcrypt fail / revoked 모두). key_id lookup fail 시 bcrypt 호출 안 함 (fast-path — timing 차이 노출되지만 비용 절감 우선).
- **token.project_id 강제**: ingest 시 클라이언트 입력의 project 무시, `token.project_id` 만 사용 (security — 다른 project 의 LogEvent INSERT 차단).
- **Partial success**: 페이로드 N건 중 일부만 invalid → 200 + `{accepted, rejected: [{index, reason}]}`. 모두 invalid → 400. payload 자체 깨짐 (gzip / JSON / events 키 없음) → 400.
- **Rate limit**: `RateLimitWindow` PRIMARY KEY `(project_id, token_id, window_start)` — 분 truncate. PostgreSQL UPSERT. `event_count + batch_size > token.rate_limit_per_minute` → 429 + `Retry-After: <seconds_until_next_minute>` 헤더.
- **Soft delete (revoked_at)**: DELETE /log-tokens 가 `revoked_at = now()` 설정. hard delete 안 함 (FK 보존). 이미 revoked 시 400.
- **last_used_at**: verify_token 성공 시 in-memory 갱신, ingest_batch 의 commit 으로 영속. 같은 트랜잭션.
- **X-pslog-Dropped-Since-Last 헤더**: 받으면 `logger.warning` 만. 본 phase 는 집계 안 함.

---

## File Structure

**신규 파일 (소스)**:
- `backend/app/schemas/log_ingest.py` — `StackFrame / LogEventInput / IngestPayload / IngestResponse / RejectedEvent` Pydantic
- `backend/app/schemas/log_token.py` — `LogTokenCreate / LogTokenResponse / LogTokenRevokedResponse` Pydantic
- `backend/app/services/log_ingest_service.py` — 6 함수 (parse_token / verify_token / check_rate_limit / validate_event / insert_events / ingest_batch)
- `backend/app/api/v1/endpoints/log_ingest.py` — POST `/log-ingest` 핸들러
- `backend/app/api/v1/endpoints/log_tokens.py` — POST/DELETE `/projects/{id}/log-tokens` 핸들러

**신규 파일 (테스트)**:
- `backend/tests/test_log_ingest_service.py` (8건)
- `backend/tests/test_log_ingest_endpoint.py` (8건)
- `backend/tests/test_log_tokens_endpoint.py` (4건)

**수정 파일**:
- `backend/app/api/v1/router.py` — log_ingest_router + log_tokens_router 마운트

**미변경**:
- alembic (Phase 1 에 모든 컬럼 포함)
- 모델 (LogIngestToken / RateLimitWindow / LogEvent 그대로)
- requirements.txt (bcrypt 이미 있음 — `bcrypt<4` 핀, passlib 가 사용)
- frontend (Phase 5 LogTokensPage 에서 통합)

---

### Task 1: Pydantic schemas (log_ingest + log_token)

**Files:**
- Create: `backend/app/schemas/log_ingest.py`
- Create: `backend/app/schemas/log_token.py`

- [ ] **Step 1: Baseline 확인**

```bash
cd /Users/arden/Documents/ardensdevspace/pslog/.worktrees/error-log-phase2-ingest/backend
source venv/bin/activate
pytest -q 2>&1 | tail -3
```

Expected: `198 passed`. 다르면 STOP.

- [ ] **Step 2: `log_ingest.py` 작성**

`backend/app/schemas/log_ingest.py` 신규:

```python
"""log-ingest 의 wire format Pydantic schemas.

설계서: 2026-05-01-error-log-phase2-ingest-design.md §3.5
DB 컬럼명 = wire 키 이름 (LogEvent 모델 1:1).
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class StackFrame(BaseModel):
    model_config = ConfigDict(extra="forbid")
    filename: str
    lineno: int
    name: str


class LogEventInput(BaseModel):
    """단일 log event — handler 가 보내는 wire format."""
    model_config = ConfigDict(extra="forbid")

    level: str  # DEBUG/INFO/WARNING/ERROR/CRITICAL — service 가 LogLevel enum 변환
    message: str
    logger_name: str
    version_sha: str
    environment: str
    hostname: str
    emitted_at: datetime

    # 에러 전용 (선택)
    exception_class: str | None = None
    exception_message: str | None = None
    stack_trace: str | None = None
    stack_frames: list[StackFrame] | None = None

    # 컨텍스트 (선택)
    user_id_external: str | None = None
    request_id: str | None = None
    extra: dict[str, Any] | None = None


class IngestPayload(BaseModel):
    """배치 페이로드. events 는 raw dict — service 가 per-event validate (partial success)."""
    model_config = ConfigDict(extra="forbid")
    events: list[dict[str, Any]] = Field(min_length=1)


class RejectedEvent(BaseModel):
    index: int
    reason: str


class IngestResponse(BaseModel):
    accepted: int
    rejected: list[RejectedEvent]
```

- [ ] **Step 3: `log_token.py` 작성**

`backend/app/schemas/log_token.py` 신규:

```python
"""log-tokens API 의 Pydantic schemas.

설계서: 2026-05-01-error-log-phase2-ingest-design.md §3.5
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class LogTokenCreate(BaseModel):
    """POST /log-tokens 요청 — name 필수, rate_limit 선택."""
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    rate_limit_per_minute: int | None = Field(default=None, ge=1, le=10000)


class LogTokenResponse(BaseModel):
    """POST /log-tokens 응답 — token 평문 1회 노출 (이후 secret_hash 만)."""
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str
    token: str  # 평문 <key_id>.<secret> — 응답 1회만
    rate_limit_per_minute: int
    created_at: datetime


class LogTokenRevokedResponse(BaseModel):
    """DELETE /log-tokens/{id} 응답."""
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    revoked_at: datetime
```

- [ ] **Step 4: import 검증**

```bash
cd backend && source venv/bin/activate
python -c "from app.schemas.log_ingest import StackFrame, LogEventInput, IngestPayload, RejectedEvent, IngestResponse; print('ok')"
python -c "from app.schemas.log_token import LogTokenCreate, LogTokenResponse, LogTokenRevokedResponse; print('ok')"
```

Expected: `ok` 두 번.

- [ ] **Step 5: 회귀 (스키마만 추가 — 영향 없음 보장)**

```bash
pytest -q 2>&1 | tail -3
```

Expected: `198 passed`.

- [ ] **Step 6: Commit**

```bash
cd /Users/arden/Documents/ardensdevspace/pslog/.worktrees/error-log-phase2-ingest
git add backend/app/schemas/log_ingest.py backend/app/schemas/log_token.py
git commit -m "$(cat <<'EOF'
feat(error-log/phase2a): Pydantic schemas (log_ingest + log_token)

- LogEventInput: wire format 단일 event (extra="forbid", DB 컬럼 1:1)
- IngestPayload: 배치 (events: list[dict] — partial validate 가능하도록 raw)
- RejectedEvent / IngestResponse: partial success 응답
- LogTokenCreate / LogTokenResponse / LogTokenRevokedResponse: token API

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: `log_ingest_service` — `parse_token` + `verify_token`

**Files:**
- Create: `backend/app/services/log_ingest_service.py`
- Create: `backend/tests/test_log_ingest_service.py` (Task 2-5 누적)

- [ ] **Step 1: Failing tests 작성** (Task 2 분량)

`backend/tests/test_log_ingest_service.py` 신규:

```python
"""log_ingest_service 단위 테스트.

설계서: 2026-05-01-error-log-phase2-ingest-design.md §3.1
"""

import asyncio
import uuid
from datetime import datetime, timedelta

import bcrypt
import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.log_ingest_token import LogIngestToken
from app.models.workspace import Workspace
from app.models.project import Project
from app.services import log_ingest_service


async def _seed_project_and_token(
    db: AsyncSession,
    *,
    secret: str = "test-secret-256bit",
    revoked: bool = False,
    rate_limit_per_minute: int = 600,
) -> tuple[Project, LogIngestToken, str]:
    """Workspace + Project + LogIngestToken 시드. 반환: (project, token, secret_hash 평문)."""
    ws = Workspace(name="ws", slug=f"ws-{uuid.uuid4().hex[:8]}")
    db.add(ws)
    await db.flush()
    proj = Project(workspace_id=ws.id, name="p")
    db.add(proj)
    await db.flush()

    secret_hash = bcrypt.hashpw(secret.encode(), bcrypt.gensalt(rounds=4)).decode()  # cost 4 = test 빠름
    token = LogIngestToken(
        project_id=proj.id,
        name="test-token",
        secret_hash=secret_hash,
        rate_limit_per_minute=rate_limit_per_minute,
    )
    if revoked:
        token.revoked_at = datetime.utcnow()
    db.add(token)
    await db.commit()
    await db.refresh(proj)
    await db.refresh(token)
    return proj, token, secret


# ---- parse_token ----

async def test_parse_token_no_header():
    """Authorization 헤더 None → 401."""
    with pytest.raises(HTTPException) as exc:
        await log_ingest_service.parse_token(None)
    assert exc.value.status_code == 401
    assert exc.value.detail == "Invalid token"


async def test_parse_token_no_dot_separator():
    """Bearer 다음에 . 분리자 없음 → 401."""
    with pytest.raises(HTTPException) as exc:
        await log_ingest_service.parse_token("Bearer just-secret-no-dot")
    assert exc.value.status_code == 401


async def test_parse_token_invalid_uuid():
    """key_id 가 UUID 아님 → 401."""
    with pytest.raises(HTTPException) as exc:
        await log_ingest_service.parse_token("Bearer notauuid.somesecret")
    assert exc.value.status_code == 401


async def test_parse_token_valid():
    """Bearer <uuid>.<secret> → (uuid_obj, secret_str)."""
    key_id = uuid.uuid4()
    secret = "the-secret"
    parsed_id, parsed_secret = await log_ingest_service.parse_token(
        f"Bearer {key_id}.{secret}"
    )
    assert parsed_id == key_id
    assert parsed_secret == secret


# ---- verify_token ----

async def test_verify_token_lookup_fail(async_session: AsyncSession):
    """key_id 가 DB 에 없음 → 401."""
    with pytest.raises(HTTPException) as exc:
        await log_ingest_service.verify_token(async_session, uuid.uuid4(), "any-secret")
    assert exc.value.status_code == 401


async def test_verify_token_revoked(async_session: AsyncSession):
    """revoked_at set → 401."""
    proj, token, secret = await _seed_project_and_token(async_session, revoked=True)
    with pytest.raises(HTTPException) as exc:
        await log_ingest_service.verify_token(async_session, token.id, secret)
    assert exc.value.status_code == 401


async def test_verify_token_bcrypt_fail(async_session: AsyncSession):
    """잘못된 secret → 401."""
    proj, token, secret = await _seed_project_and_token(async_session)
    with pytest.raises(HTTPException) as exc:
        await log_ingest_service.verify_token(async_session, token.id, "wrong-secret")
    assert exc.value.status_code == 401


async def test_verify_token_success_and_last_used(async_session: AsyncSession):
    """정상 verify → token 반환 + last_used_at 갱신."""
    proj, token, secret = await _seed_project_and_token(async_session)
    assert token.last_used_at is None

    verified = await log_ingest_service.verify_token(async_session, token.id, secret)
    assert verified.id == token.id
    assert verified.last_used_at is not None
    # in-memory 갱신 (caller 가 commit) — DB 영속은 ingest_batch 가 처리
```

(현재 8 테스트 추가 — Task 2 만의 service 함수 2개 검증)

- [ ] **Step 2: Verify failure**

```bash
pytest tests/test_log_ingest_service.py -v 2>&1 | tail -15
```

Expected: 모두 FAIL with `ImportError: cannot import name 'log_ingest_service'`.

- [ ] **Step 3: `log_ingest_service.py` skeleton + 두 함수 구현**

`backend/app/services/log_ingest_service.py` 신규:

```python
"""log-ingest 서비스 — 토큰 검증 / rate limit / batch INSERT.

설계서: 2026-05-01-error-log-phase2-ingest-design.md §3.1
"""

import asyncio
import logging
from datetime import datetime
from uuid import UUID

import bcrypt
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.log_ingest_token import LogIngestToken

logger = logging.getLogger(__name__)


def _invalid_token() -> HTTPException:
    """timing attack 회피용 통일 401 — 사유 구분 안 함."""
    return HTTPException(status_code=401, detail="Invalid token")


async def parse_token(authorization_header: str | None) -> tuple[UUID, str]:
    """Bearer <key_id>.<secret> → (key_id_uuid, secret).

    형식 깨짐 → 401. key_id UUID parse fail → 401.
    """
    if not authorization_header or not authorization_header.startswith("Bearer "):
        raise _invalid_token()
    raw = authorization_header[len("Bearer "):]
    if "." not in raw:
        raise _invalid_token()
    # key_id 와 secret 분리 — secret 안의 . 도 허용 (key_id 는 UUID 라 . 없음)
    key_id_str, _, secret = raw.partition(".")
    if not secret:
        raise _invalid_token()
    try:
        key_id = UUID(key_id_str)
    except (ValueError, AttributeError):
        raise _invalid_token()
    return key_id, secret


async def verify_token(db: AsyncSession, key_id: UUID, secret: str) -> LogIngestToken:
    """key_id lookup → bcrypt verify → last_used_at 갱신 (in-memory).

    실패 시 401 (사유 구분 안 함). 성공 시 token 반환.
    DB commit 은 caller (ingest_batch) 가 묶음.
    """
    token = await db.get(LogIngestToken, key_id)
    if token is None:
        raise _invalid_token()
    if token.revoked_at is not None:
        raise _invalid_token()

    # bcrypt 동기 — async endpoint 에서 event loop block 회피
    is_valid = await asyncio.to_thread(
        bcrypt.checkpw,
        secret.encode("utf-8"),
        token.secret_hash.encode("utf-8"),
    )
    if not is_valid:
        raise _invalid_token()

    token.last_used_at = datetime.utcnow()
    return token
```

- [ ] **Step 4: Verify pass + 회귀**

```bash
pytest tests/test_log_ingest_service.py -v 2>&1 | tail -15
pytest -q 2>&1 | tail -3
```

Expected: 신규 8 PASS, 전체 `206 passed` (198 + 8).

- [ ] **Step 5: Commit**

```bash
cd /Users/arden/Documents/ardensdevspace/pslog/.worktrees/error-log-phase2-ingest
git add backend/app/services/log_ingest_service.py backend/tests/test_log_ingest_service.py
git commit -m "$(cat <<'EOF'
feat(error-log/phase2a): log_ingest_service — parse_token + verify_token

- parse_token: Bearer <key_id>.<secret> 파싱 (UUID validate, partition by 첫 .)
- verify_token: key_id lookup → bcrypt.checkpw via asyncio.to_thread → last_used_at 갱신 (in-memory)
- timing attack 회피: 401 detail 모두 "Invalid token" 통일
- 회귀 8건: parse 4건 (no-header, no-dot, invalid-uuid, valid) + verify 4건 (lookup-fail, revoked, bcrypt-fail, success)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: `log_ingest_service` — `check_rate_limit` (RateLimitWindow UPSERT)

**Files:**
- Modify: `backend/app/services/log_ingest_service.py` (check_rate_limit 함수 추가)
- Modify: `backend/tests/test_log_ingest_service.py` (4건 추가)

- [ ] **Step 1: Failing tests 작성**

`backend/tests/test_log_ingest_service.py` 끝에 추가:

```python
# ---- check_rate_limit ----

async def test_check_rate_limit_first_call_inserts_window(async_session: AsyncSession):
    """첫 호출 → RateLimitWindow row INSERT, event_count == batch_size."""
    proj, token, _ = await _seed_project_and_token(async_session, rate_limit_per_minute=600)
    now = datetime(2026, 5, 1, 10, 30, 45)

    await log_ingest_service.check_rate_limit(
        async_session, project_id=proj.id, token=token, batch_size=5, now=now,
    )

    from app.models.rate_limit_window import RateLimitWindow
    expected_window = datetime(2026, 5, 1, 10, 30, 0)  # 분 truncate
    row = await async_session.get(
        RateLimitWindow, (proj.id, token.id, expected_window)
    )
    assert row is not None
    assert row.event_count == 5


async def test_check_rate_limit_same_minute_accumulates(async_session: AsyncSession):
    """같은 분 재호출 → event_count 누적."""
    proj, token, _ = await _seed_project_and_token(async_session, rate_limit_per_minute=600)
    now = datetime(2026, 5, 1, 10, 30, 12)

    await log_ingest_service.check_rate_limit(
        async_session, project_id=proj.id, token=token, batch_size=5, now=now,
    )
    await log_ingest_service.check_rate_limit(
        async_session, project_id=proj.id, token=token, batch_size=3,
        now=datetime(2026, 5, 1, 10, 30, 47),  # 같은 분
    )

    from app.models.rate_limit_window import RateLimitWindow
    row = await async_session.get(
        RateLimitWindow, (proj.id, token.id, datetime(2026, 5, 1, 10, 30, 0))
    )
    assert row.event_count == 8


async def test_check_rate_limit_exceeds_raises_429(async_session: AsyncSession):
    """event_count > limit → 429 + Retry-After 헤더 (최대 60)."""
    proj, token, _ = await _seed_project_and_token(async_session, rate_limit_per_minute=10)
    now = datetime(2026, 5, 1, 10, 30, 30)

    with pytest.raises(HTTPException) as exc:
        await log_ingest_service.check_rate_limit(
            async_session, project_id=proj.id, token=token, batch_size=11, now=now,
        )
    assert exc.value.status_code == 429
    assert exc.value.detail == "Rate limit exceeded"
    # Retry-After: 30초 남음 (60초 - 30초 경과)
    retry_after = int(exc.value.headers["Retry-After"])
    assert 1 <= retry_after <= 60


async def test_check_rate_limit_next_minute_new_row(async_session: AsyncSession):
    """다음 분 호출 → 새 RateLimitWindow row (event_count = batch_size)."""
    proj, token, _ = await _seed_project_and_token(async_session, rate_limit_per_minute=600)

    await log_ingest_service.check_rate_limit(
        async_session, project_id=proj.id, token=token, batch_size=5,
        now=datetime(2026, 5, 1, 10, 30, 30),
    )
    await log_ingest_service.check_rate_limit(
        async_session, project_id=proj.id, token=token, batch_size=3,
        now=datetime(2026, 5, 1, 10, 31, 5),  # 다음 분
    )

    from app.models.rate_limit_window import RateLimitWindow
    row1 = await async_session.get(
        RateLimitWindow, (proj.id, token.id, datetime(2026, 5, 1, 10, 30, 0))
    )
    row2 = await async_session.get(
        RateLimitWindow, (proj.id, token.id, datetime(2026, 5, 1, 10, 31, 0))
    )
    assert row1.event_count == 5
    assert row2.event_count == 3
```

- [ ] **Step 2: Verify failure**

```bash
pytest tests/test_log_ingest_service.py -k "rate_limit" -v 2>&1 | tail -10
```

Expected: 4 FAIL with `AttributeError: module 'log_ingest_service' has no attribute 'check_rate_limit'`.

- [ ] **Step 3: `check_rate_limit` 구현**

`backend/app/services/log_ingest_service.py` 끝에 추가 (imports 도 같이 갱신):

```python
# 파일 상단 imports 에 추가:
from sqlalchemy.dialects.postgresql import insert as pg_insert
from app.models.rate_limit_window import RateLimitWindow


async def check_rate_limit(
    db: AsyncSession,
    *,
    project_id: UUID,
    token: LogIngestToken,
    batch_size: int,
    now: datetime,
) -> None:
    """RateLimitWindow UPSERT (분 truncate). limit 초과 시 429.

    PostgreSQL ON CONFLICT DO UPDATE pattern — 단일 SQL.
    """
    window_start = now.replace(second=0, microsecond=0)

    stmt = pg_insert(RateLimitWindow).values(
        project_id=project_id,
        token_id=token.id,
        window_start=window_start,
        event_count=batch_size,
    ).on_conflict_do_update(
        index_elements=["project_id", "token_id", "window_start"],
        set_={"event_count": RateLimitWindow.event_count + batch_size},
    ).returning(RateLimitWindow.event_count)

    result = await db.execute(stmt)
    new_count = result.scalar_one()

    if new_count > token.rate_limit_per_minute:
        # 다음 분까지 남은 초 (최대 60)
        next_minute = window_start.replace(second=0, microsecond=0) + timedelta(minutes=1)
        seconds_remaining = max(1, int((next_minute - now).total_seconds()))
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded",
            headers={"Retry-After": str(seconds_remaining)},
        )
```

`from datetime import datetime, timedelta` import 도 추가.

- [ ] **Step 4: Verify pass + 회귀**

```bash
pytest tests/test_log_ingest_service.py -v 2>&1 | tail -15
pytest -q 2>&1 | tail -3
```

Expected: 신규 4 PASS, 전체 `210 passed` (206 + 4).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/log_ingest_service.py backend/tests/test_log_ingest_service.py
git commit -m "$(cat <<'EOF'
feat(error-log/phase2a): log_ingest_service — check_rate_limit (PostgreSQL UPSERT)

- RateLimitWindow PRIMARY KEY (project_id, token_id, window_start) — 분 truncate
- pg_insert + ON CONFLICT DO UPDATE — 단일 SQL UPSERT, RETURNING event_count
- limit 초과 → 429 + Retry-After 헤더 (next minute 까지 남은 초, 최대 60)
- 회귀 4건: 첫 호출 INSERT / 같은 분 누적 / limit 초과 429 / 다음 분 새 row

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: `log_ingest_service` — `validate_event` + `insert_events`

**Files:**
- Modify: `backend/app/services/log_ingest_service.py` (validate_event + insert_events 추가)
- Modify: `backend/tests/test_log_ingest_service.py` (4건 추가)

- [ ] **Step 1: Failing tests 작성**

`backend/tests/test_log_ingest_service.py` 끝에 추가:

```python
# ---- validate_event ----

def _valid_event_dict() -> dict:
    """테스트용 정상 event dict — 모든 필수 필드 포함."""
    return {
        "level": "ERROR",
        "message": "test error",
        "logger_name": "app.test",
        "version_sha": "a" * 40,
        "environment": "production",
        "hostname": "host-1",
        "emitted_at": "2026-05-01T10:30:00Z",
    }


def test_validate_event_valid_returns_log_event():
    """정상 event dict → (LogEvent, None)."""
    proj_id = uuid.uuid4()
    log_event, rejection = log_ingest_service.validate_event(
        _valid_event_dict(), index=0, project_id=proj_id,
    )
    assert log_event is not None
    assert rejection is None
    assert log_event.project_id == proj_id
    assert log_event.message == "test error"
    assert log_event.version_sha == "a" * 40


def test_validate_event_unknown_version_sha_ok():
    """version_sha == 'unknown' 정상."""
    d = _valid_event_dict()
    d["version_sha"] = "unknown"
    log_event, rejection = log_ingest_service.validate_event(d, 0, uuid.uuid4())
    assert log_event is not None
    assert rejection is None


def test_validate_event_short_sha_rejected():
    """version_sha short SHA → reject."""
    d = _valid_event_dict()
    d["version_sha"] = "abc1234"  # 7자, 40자 아님
    log_event, rejection = log_ingest_service.validate_event(d, 5, uuid.uuid4())
    assert log_event is None
    assert rejection == {"index": 5, "reason": "version_sha format invalid"}


def test_validate_event_extra_field_rejected():
    """Pydantic schema 위배 (extra='forbid') → reject."""
    d = _valid_event_dict()
    d["unknown_field"] = "boom"
    log_event, rejection = log_ingest_service.validate_event(d, 2, uuid.uuid4())
    assert log_event is None
    assert rejection["index"] == 2
    assert "unknown_field" in rejection["reason"] or "extra" in rejection["reason"].lower()


def test_validate_event_oversized_extra_rejected():
    """extra > 4KB → reject."""
    d = _valid_event_dict()
    d["extra"] = {"k": "x" * 5000}  # > 4KB
    log_event, rejection = log_ingest_service.validate_event(d, 1, uuid.uuid4())
    assert log_event is None
    assert rejection["index"] == 1
    assert "extra" in rejection["reason"].lower()


# ---- insert_events ----

async def test_insert_events_batch_inserts_with_null_fingerprint(async_session: AsyncSession):
    """batch INSERT → 모든 row 의 fingerprint=NULL."""
    proj, token, _ = await _seed_project_and_token(async_session)
    from app.models.log_event import LogEvent, LogLevel

    events = [
        LogEvent(
            project_id=proj.id,
            level=LogLevel.ERROR,
            message=f"msg-{i}",
            logger_name="app.test",
            version_sha="a" * 40,
            environment="production",
            hostname="host-1",
            emitted_at=datetime.utcnow(),
        )
        for i in range(5)
    ]

    inserted = await log_ingest_service.insert_events(async_session, events)
    assert inserted == 5

    from sqlalchemy import select
    rows = (await async_session.execute(
        select(LogEvent).where(LogEvent.project_id == proj.id)
    )).scalars().all()
    assert len(rows) == 5
    for row in rows:
        assert row.fingerprint is None
        assert row.fingerprinted_at is None
```

- [ ] **Step 2: Verify failure**

```bash
pytest tests/test_log_ingest_service.py -k "validate_event or insert_events" -v 2>&1 | tail -15
```

Expected: 6 FAIL with `AttributeError: ... has no attribute 'validate_event' ...`.

- [ ] **Step 3: `validate_event` + `insert_events` 구현**

`backend/app/services/log_ingest_service.py` 끝에 추가 (imports 도 갱신):

```python
# 파일 상단 imports 에 추가:
import json as _json
import re
from typing import Any
from pydantic import ValidationError
from app.models.log_event import LogEvent, LogLevel
from app.schemas.log_ingest import LogEventInput


_VERSION_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_EXTRA_MAX_BYTES = 4 * 1024  # 4KB


def validate_event(
    event_dict: dict[str, Any], index: int, project_id: UUID,
) -> tuple[LogEvent | None, dict | None]:
    """단일 event dict 검증 — Pydantic + version_sha 형식 + extra 크기.

    valid → (LogEvent, None). invalid → (None, {"index": index, "reason": "..."}).
    """
    # Pydantic schema validate
    try:
        parsed = LogEventInput.model_validate(event_dict)
    except ValidationError as e:
        # 첫 에러 메시지 사용 (간결)
        first = e.errors()[0]
        loc = ".".join(str(x) for x in first["loc"])
        msg = first["msg"]
        return None, {"index": index, "reason": f"{loc}: {msg}"}

    # version_sha 형식
    if parsed.version_sha != "unknown" and not _VERSION_SHA_RE.match(parsed.version_sha):
        return None, {"index": index, "reason": "version_sha format invalid"}

    # extra 크기 (JSON 직렬화 후 byte 수)
    if parsed.extra is not None:
        extra_bytes = len(_json.dumps(parsed.extra).encode("utf-8"))
        if extra_bytes > _EXTRA_MAX_BYTES:
            return None, {"index": index, "reason": f"extra exceeds {_EXTRA_MAX_BYTES} bytes"}

    # LogLevel 정규화 (대소문자 무관)
    try:
        level = LogLevel(parsed.level.lower())
    except ValueError:
        return None, {"index": index, "reason": f"level invalid: {parsed.level}"}

    log_event = LogEvent(
        project_id=project_id,
        level=level,
        message=parsed.message,
        logger_name=parsed.logger_name,
        version_sha=parsed.version_sha,
        environment=parsed.environment,
        hostname=parsed.hostname,
        emitted_at=parsed.emitted_at,
        exception_class=parsed.exception_class,
        exception_message=parsed.exception_message,
        stack_trace=parsed.stack_trace,
        stack_frames=[f.model_dump() for f in parsed.stack_frames] if parsed.stack_frames else None,
        user_id_external=parsed.user_id_external,
        request_id=parsed.request_id,
        extra=parsed.extra,
    )
    return log_event, None


async def insert_events(db: AsyncSession, events: list[LogEvent]) -> int:
    """batch INSERT — fingerprint=NULL (Phase 3 의 fingerprint_service 가 처리).

    단일 트랜잭션. flush 만 (commit 은 caller).
    """
    db.add_all(events)
    await db.flush()
    return len(events)
```

- [ ] **Step 4: Verify pass + 회귀**

```bash
pytest tests/test_log_ingest_service.py -v 2>&1 | tail -20
pytest -q 2>&1 | tail -3
```

Expected: 신규 6 PASS, 전체 `216 passed` (210 + 6).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/log_ingest_service.py backend/tests/test_log_ingest_service.py
git commit -m "$(cat <<'EOF'
feat(error-log/phase2a): log_ingest_service — validate_event + insert_events

- validate_event: Pydantic + version_sha 형식 (40 hex 또는 'unknown') + extra 4KB 한도
- LogLevel 정규화 (대소문자 무관)
- insert_events: batch INSERT, fingerprint=NULL (Phase 3 fingerprint_service 가 후속 처리)
- 회귀 6건: validate 5건 (valid / unknown sha / short sha / extra 필드 / extra 4KB+) + insert 1건

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: `log_ingest_service` — `ingest_batch` (composition)

**Files:**
- Modify: `backend/app/services/log_ingest_service.py` (ingest_batch 추가)
- Modify: `backend/tests/test_log_ingest_service.py` (2건 추가)

- [ ] **Step 1: Failing tests 작성**

`backend/tests/test_log_ingest_service.py` 끝에 추가:

```python
# ---- ingest_batch ----

async def test_ingest_batch_partial_success(async_session: AsyncSession, caplog):
    """10 events 중 8 valid 2 invalid → accepted=8, rejected=2건. DB 8 행."""
    proj, token, _ = await _seed_project_and_token(async_session)

    events = [_valid_event_dict() for _ in range(10)]
    events[2]["version_sha"] = "abc"  # short SHA reject
    events[7]["unknown_field"] = "x"  # extra field reject

    accepted, rejected = await log_ingest_service.ingest_batch(
        async_session, token=token,
        payload_dict={"events": events},
        dropped_since_last=None,
    )

    assert accepted == 8
    assert len(rejected) == 2
    rejected_indices = {r["index"] for r in rejected}
    assert rejected_indices == {2, 7}

    # DB 8 행
    from sqlalchemy import select
    from app.models.log_event import LogEvent
    rows = (await async_session.execute(
        select(LogEvent).where(LogEvent.project_id == proj.id)
    )).scalars().all()
    assert len(rows) == 8

    # last_used_at 갱신 (commit 됨)
    await async_session.refresh(token)
    assert token.last_used_at is not None


async def test_ingest_batch_dropped_header_logs_warning(
    async_session: AsyncSession, caplog,
):
    """X-pslog-Dropped-Since-Last 받으면 logger.warning."""
    import logging
    proj, token, _ = await _seed_project_and_token(async_session)

    with caplog.at_level(logging.WARNING, logger="app.services.log_ingest_service"):
        await log_ingest_service.ingest_batch(
            async_session, token=token,
            payload_dict={"events": [_valid_event_dict()]},
            dropped_since_last=42,
        )

    assert any(
        "dropped" in record.message.lower() and "42" in record.message
        for record in caplog.records
    )
```

- [ ] **Step 2: Verify failure**

```bash
pytest tests/test_log_ingest_service.py -k "ingest_batch" -v 2>&1 | tail -10
```

Expected: 2 FAIL with `AttributeError: ... has no attribute 'ingest_batch'`.

- [ ] **Step 3: `ingest_batch` 구현**

`backend/app/services/log_ingest_service.py` 끝에 추가:

```python
async def ingest_batch(
    db: AsyncSession,
    *,
    token: LogIngestToken,
    payload_dict: dict[str, Any],
    dropped_since_last: int | None = None,
    now: datetime | None = None,
) -> tuple[int, list[dict]]:
    """end-to-end: rate limit → validate (partial) → insert → commit.

    Returns: (accepted_count, rejected_list).
    payload_dict 의 events 리스트가 비어있거나 형식 깨졌을 때는 caller (endpoint) 가 400 처리.
    """
    if dropped_since_last is not None and dropped_since_last > 0:
        logger.warning(
            "log_ingest token=%s dropped %d events since last batch",
            token.id, dropped_since_last,
        )

    events_raw = payload_dict.get("events")
    if not isinstance(events_raw, list) or not events_raw:
        # caller 가 400 매핑 — 빈/잘못된 events
        raise HTTPException(status_code=400, detail="events list required and non-empty")

    now = now or datetime.utcnow()

    # rate limit — 전체 batch_size 기준
    await check_rate_limit(
        db, project_id=token.project_id, token=token,
        batch_size=len(events_raw), now=now,
    )

    # per-event validate (partial success)
    accepted: list[LogEvent] = []
    rejected: list[dict] = []
    for index, event_dict in enumerate(events_raw):
        log_event, rejection = validate_event(event_dict, index, token.project_id)
        if log_event is not None:
            accepted.append(log_event)
        else:
            rejected.append(rejection)

    if accepted:
        await insert_events(db, accepted)

    # token.last_used_at + RateLimitWindow + LogEvent batch 모두 commit
    await db.commit()

    return len(accepted), rejected
```

- [ ] **Step 4: Verify pass + 회귀**

```bash
pytest tests/test_log_ingest_service.py -v 2>&1 | tail -25
pytest -q 2>&1 | tail -3
```

Expected: 신규 2 PASS, 전체 `218 passed` (216 + 2).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/log_ingest_service.py backend/tests/test_log_ingest_service.py
git commit -m "$(cat <<'EOF'
feat(error-log/phase2a): log_ingest_service — ingest_batch composition

- end-to-end: rate limit → per-event validate (partial) → batch INSERT → commit
- X-pslog-Dropped-Since-Last 헤더 받으면 logger.warning
- events 리스트 비어있거나 형식 깨짐 → caller 가 400 매핑하도록 HTTPException raise
- token.last_used_at + RateLimitWindow + LogEvent 모두 같은 트랜잭션 commit
- 회귀 2건: partial success (10 중 8 accepted) / dropped header logger.warning

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: Token API endpoints (POST + DELETE `/log-tokens`)

**Files:**
- Create: `backend/app/api/v1/endpoints/log_tokens.py`
- Create: `backend/tests/test_log_tokens_endpoint.py`

- [ ] **Step 1: Failing tests 작성**

`backend/tests/test_log_tokens_endpoint.py` 신규:

```python
"""log-tokens endpoint 통합 테스트.

설계서: 2026-05-01-error-log-phase2-ingest-design.md §3.3, §3.4
"""

import uuid
from datetime import datetime

import bcrypt
import pytest
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.log_ingest_token import LogIngestToken
from app.models.project import Project, ProjectMember
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceRole


@pytest.fixture()
async def client_with_db(async_session: AsyncSession, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PSLOG_FERNET_KEY", Fernet.generate_key().decode())
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
    db: AsyncSession,
    role: WorkspaceRole = WorkspaceRole.OWNER,
) -> tuple[User, Project]:
    user = User(
        email=f"u-{uuid.uuid4().hex[:8]}@example.com",
        name="alice",
        password_hash="x",
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


async def test_create_log_token_owner(
    client_with_db, async_session: AsyncSession,
):
    """POST /log-tokens (OWNER) → 201 + 평문 token (UUID.secret 형식) + bcrypt 검증 가능."""
    user, proj = await _seed_user_project(async_session)
    token = _auth_token(user)

    res = await client_with_db.post(
        f"/api/v1/projects/{proj.id}/log-tokens",
        json={"name": "test-token"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 201
    body = res.json()
    assert body["name"] == "test-token"
    assert body["rate_limit_per_minute"] == 600  # 기본값

    # 평문 token 형식: <uuid>.<secret>
    plain = body["token"]
    key_id_str, _, secret = plain.partition(".")
    assert key_id_str == body["id"]
    assert len(secret) > 30  # ~43자 base64

    # DB 의 secret_hash 가 평문 secret 으로 verify 가능
    db_token = await async_session.get(LogIngestToken, uuid.UUID(body["id"]))
    assert db_token is not None
    assert bcrypt.checkpw(secret.encode(), db_token.secret_hash.encode())
    # 평문은 어디에도 없음
    assert db_token.secret_hash != secret


async def test_create_log_token_403_for_non_owner(
    client_with_db, async_session: AsyncSession,
):
    """POST /log-tokens (EDITOR) → 403."""
    user, proj = await _seed_user_project(async_session, role=WorkspaceRole.EDITOR)
    token = _auth_token(user)
    res = await client_with_db.post(
        f"/api/v1/projects/{proj.id}/log-tokens",
        json={"name": "x"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 403


async def test_revoke_log_token_owner(
    client_with_db, async_session: AsyncSession,
):
    """DELETE /log-tokens/{id} (OWNER) → 200 + revoked_at set, DB 도 갱신."""
    user, proj = await _seed_user_project(async_session)
    db_token = LogIngestToken(
        project_id=proj.id,
        name="x",
        secret_hash=bcrypt.hashpw(b"s", bcrypt.gensalt(rounds=4)).decode(),
    )
    async_session.add(db_token)
    await async_session.commit()
    await async_session.refresh(db_token)

    token = _auth_token(user)
    res = await client_with_db.delete(
        f"/api/v1/projects/{proj.id}/log-tokens/{db_token.id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["id"] == str(db_token.id)
    assert body["revoked_at"] is not None

    await async_session.refresh(db_token)
    assert db_token.revoked_at is not None


async def test_revoke_log_token_already_revoked_400(
    client_with_db, async_session: AsyncSession,
):
    """이미 revoked 된 token 재 DELETE → 400."""
    user, proj = await _seed_user_project(async_session)
    db_token = LogIngestToken(
        project_id=proj.id,
        name="x",
        secret_hash=bcrypt.hashpw(b"s", bcrypt.gensalt(rounds=4)).decode(),
        revoked_at=datetime.utcnow(),
    )
    async_session.add(db_token)
    await async_session.commit()
    await async_session.refresh(db_token)

    token = _auth_token(user)
    res = await client_with_db.delete(
        f"/api/v1/projects/{proj.id}/log-tokens/{db_token.id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 400
```

- [ ] **Step 2: Verify failure**

```bash
pytest tests/test_log_tokens_endpoint.py -v 2>&1 | tail -10
```

Expected: 4 FAIL — 404 from missing endpoint.

- [ ] **Step 3: Endpoint 구현**

`backend/app/api/v1/endpoints/log_tokens.py` 신규:

```python
"""log-tokens API endpoints — OWNER 전용 토큰 발급/폐기.

설계서: 2026-05-01-error-log-phase2-ingest-design.md §3.3, §3.4
"""

import secrets
from datetime import datetime
from uuid import UUID

import bcrypt
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import CurrentUser
from app.models.log_ingest_token import LogIngestToken
from app.schemas.log_token import (
    LogTokenCreate,
    LogTokenResponse,
    LogTokenRevokedResponse,
)
from app.services import project_service
from app.services.permission_service import can_manage, get_effective_role


router = APIRouter(prefix="/projects", tags=["log-tokens"])


@router.post(
    "/{project_id}/log-tokens",
    response_model=LogTokenResponse,
    status_code=201,
)
async def create_log_token(
    project_id: UUID,
    data: LogTokenCreate,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
):
    """토큰 발급 — 응답에 평문 token 1회만, DB 에는 bcrypt(secret) 만."""
    project = await project_service.get_project(db, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    role = await get_effective_role(db, user.id, project_id)
    if role is None:
        raise HTTPException(status_code=404, detail="Project not found")
    if not can_manage(role):
        raise HTTPException(status_code=403, detail="Owner only")

    # 256-bit secret + bcrypt cost 12
    secret = secrets.token_urlsafe(32)
    secret_hash = bcrypt.hashpw(
        secret.encode("utf-8"), bcrypt.gensalt(rounds=12),
    ).decode("utf-8")

    token = LogIngestToken(
        project_id=project_id,
        name=data.name,
        secret_hash=secret_hash,
        rate_limit_per_minute=data.rate_limit_per_minute or 600,
    )
    db.add(token)
    await db.commit()
    await db.refresh(token)

    return LogTokenResponse(
        id=token.id,
        name=token.name,
        token=f"{token.id}.{secret}",  # 평문 — 응답 1회만
        rate_limit_per_minute=token.rate_limit_per_minute,
        created_at=token.created_at,
    )


@router.delete(
    "/{project_id}/log-tokens/{token_id}",
    response_model=LogTokenRevokedResponse,
)
async def revoke_log_token(
    project_id: UUID,
    token_id: UUID,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
):
    """토큰 폐기 — soft delete (revoked_at = now)."""
    project = await project_service.get_project(db, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    role = await get_effective_role(db, user.id, project_id)
    if role is None:
        raise HTTPException(status_code=404, detail="Project not found")
    if not can_manage(role):
        raise HTTPException(status_code=403, detail="Owner only")

    token = await db.get(LogIngestToken, token_id)
    if token is None or token.project_id != project_id:
        raise HTTPException(status_code=404, detail="Token not found")
    if token.revoked_at is not None:
        raise HTTPException(status_code=400, detail="Token already revoked")

    token.revoked_at = datetime.utcnow()
    await db.commit()
    await db.refresh(token)

    return LogTokenRevokedResponse(id=token.id, revoked_at=token.revoked_at)
```

- [ ] **Step 4: Router 마운트**

`backend/app/api/v1/router.py` 수정 — import 추가 + include_router:

```python
from app.api.v1.endpoints.log_tokens import router as log_tokens_router

# ... 기존 include 들 ...
api_v1_router.include_router(log_tokens_router)
```

- [ ] **Step 5: Verify pass + 회귀**

```bash
cd backend && source venv/bin/activate
pytest tests/test_log_tokens_endpoint.py -v 2>&1 | tail -10
pytest -q 2>&1 | tail -3
```

Expected: 신규 4 PASS, 전체 `222 passed` (218 + 4).

- [ ] **Step 6: Commit**

```bash
cd /Users/arden/Documents/ardensdevspace/pslog/.worktrees/error-log-phase2-ingest
git add backend/app/api/v1/endpoints/log_tokens.py backend/app/api/v1/router.py backend/tests/test_log_tokens_endpoint.py
git commit -m "$(cat <<'EOF'
feat(error-log/phase2a): POST /log-tokens + DELETE /log-tokens (OWNER)

- POST: secrets.token_urlsafe(32) + bcrypt cost 12 — 평문 <key_id>.<secret> 응답 1회만
- DELETE: soft delete (revoked_at = now), 이미 revoked 면 400, 다른 project token 404
- 권한: OWNER 전용 (can_manage), 비-OWNER 403, 비-멤버 404
- 회귀 4건: 정상 발급 / EDITOR 403 / 정상 폐기 / 이미 revoked 400

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: POST `/log-ingest` endpoint + router wiring

**Files:**
- Create: `backend/app/api/v1/endpoints/log_ingest.py`
- Modify: `backend/app/api/v1/router.py` (log_ingest_router include)
- Create: `backend/tests/test_log_ingest_endpoint.py` (8건)

- [ ] **Step 1: Failing tests 작성**

`backend/tests/test_log_ingest_endpoint.py` 신규:

```python
"""log-ingest endpoint 통합 테스트.

설계서: 2026-05-01-error-log-phase2-ingest-design.md §3.2
"""

import gzip
import json
import uuid
from datetime import datetime
from unittest.mock import patch

import bcrypt
import pytest
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.log_event import LogEvent
from app.models.log_ingest_token import LogIngestToken
from app.models.project import Project
from app.models.workspace import Workspace


@pytest.fixture()
async def client_with_db(async_session: AsyncSession, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PSLOG_FERNET_KEY", Fernet.generate_key().decode())
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


async def _seed_token(
    db: AsyncSession,
    *,
    secret: str = "test-secret-256bit-base64-urlsafe-fake",
    revoked: bool = False,
    rate_limit_per_minute: int = 600,
) -> tuple[Project, LogIngestToken, str]:
    ws = Workspace(name="ws", slug=f"ws-{uuid.uuid4().hex[:8]}")
    db.add(ws)
    await db.flush()
    proj = Project(workspace_id=ws.id, name="p")
    db.add(proj)
    await db.flush()

    secret_hash = bcrypt.hashpw(secret.encode(), bcrypt.gensalt(rounds=4)).decode()
    token = LogIngestToken(
        project_id=proj.id,
        name="test",
        secret_hash=secret_hash,
        rate_limit_per_minute=rate_limit_per_minute,
    )
    if revoked:
        token.revoked_at = datetime.utcnow()
    db.add(token)
    await db.commit()
    await db.refresh(proj)
    await db.refresh(token)
    return proj, token, secret


def _valid_event() -> dict:
    return {
        "level": "ERROR",
        "message": "boom",
        "logger_name": "app.x",
        "version_sha": "a" * 40,
        "environment": "production",
        "hostname": "h1",
        "emitted_at": "2026-05-01T10:30:00Z",
    }


async def test_ingest_normal_200(client_with_db, async_session: AsyncSession):
    """정상 ingest → 200 + accepted/rejected."""
    proj, token, secret = await _seed_token(async_session)
    bearer = f"Bearer {token.id}.{secret}"
    res = await client_with_db.post(
        "/api/v1/log-ingest",
        json={"events": [_valid_event(), _valid_event()]},
        headers={"Authorization": bearer},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["accepted"] == 2
    assert body["rejected"] == []

    from sqlalchemy import select
    rows = (await async_session.execute(
        select(LogEvent).where(LogEvent.project_id == proj.id)
    )).scalars().all()
    assert len(rows) == 2


async def test_ingest_all_invalid_400(client_with_db, async_session: AsyncSession):
    """모든 event invalid → 400 + rejected list."""
    proj, token, secret = await _seed_token(async_session)
    bearer = f"Bearer {token.id}.{secret}"
    bad = _valid_event()
    bad["version_sha"] = "abc"
    res = await client_with_db.post(
        "/api/v1/log-ingest",
        json={"events": [bad, bad]},
        headers={"Authorization": bearer},
    )
    assert res.status_code == 400
    body = res.json()
    assert body["accepted"] == 0
    assert len(body["rejected"]) == 2


async def test_ingest_gzip_body(client_with_db, async_session: AsyncSession):
    """Content-Encoding: gzip 정상 처리."""
    proj, token, secret = await _seed_token(async_session)
    bearer = f"Bearer {token.id}.{secret}"
    raw = json.dumps({"events": [_valid_event()]}).encode("utf-8")
    compressed = gzip.compress(raw)
    res = await client_with_db.post(
        "/api/v1/log-ingest",
        content=compressed,
        headers={
            "Authorization": bearer,
            "Content-Encoding": "gzip",
            "Content-Type": "application/json",
        },
    )
    assert res.status_code == 200
    assert res.json()["accepted"] == 1


async def test_ingest_gzip_decode_fail_400(client_with_db, async_session: AsyncSession):
    """잘못된 gzip byte → 400."""
    proj, token, secret = await _seed_token(async_session)
    bearer = f"Bearer {token.id}.{secret}"
    res = await client_with_db.post(
        "/api/v1/log-ingest",
        content=b"not-gzip-data",
        headers={
            "Authorization": bearer,
            "Content-Encoding": "gzip",
            "Content-Type": "application/json",
        },
    )
    assert res.status_code == 400


async def test_ingest_auth_failures_401(client_with_db, async_session: AsyncSession):
    """인증 실패 4 case 모두 401."""
    proj, token, secret = await _seed_token(async_session)
    payload = {"events": [_valid_event()]}

    # 1. Authorization 헤더 없음
    res = await client_with_db.post("/api/v1/log-ingest", json=payload)
    assert res.status_code == 401

    # 2. 형식 깨짐 (분리자 . 없음)
    res = await client_with_db.post(
        "/api/v1/log-ingest", json=payload,
        headers={"Authorization": "Bearer noseparator"},
    )
    assert res.status_code == 401

    # 3. 잘못된 secret (bcrypt fail)
    res = await client_with_db.post(
        "/api/v1/log-ingest", json=payload,
        headers={"Authorization": f"Bearer {token.id}.wrong-secret"},
    )
    assert res.status_code == 401

    # 4. revoked token
    proj2, token2, secret2 = await _seed_token(async_session, revoked=True)
    res = await client_with_db.post(
        "/api/v1/log-ingest", json=payload,
        headers={"Authorization": f"Bearer {token2.id}.{secret2}"},
    )
    assert res.status_code == 401


async def test_ingest_rate_limit_429(client_with_db, async_session: AsyncSession):
    """rate limit 초과 → 429 + Retry-After 헤더."""
    proj, token, secret = await _seed_token(async_session, rate_limit_per_minute=2)
    bearer = f"Bearer {token.id}.{secret}"
    res = await client_with_db.post(
        "/api/v1/log-ingest",
        json={"events": [_valid_event() for _ in range(5)]},  # batch_size 5 > limit 2
        headers={"Authorization": bearer},
    )
    assert res.status_code == 429
    assert "Retry-After" in res.headers
    assert int(res.headers["Retry-After"]) >= 1


async def test_ingest_payload_malformed_400(client_with_db, async_session: AsyncSession):
    """JSON parse fail → 400 / events 키 없음 → 400."""
    proj, token, secret = await _seed_token(async_session)
    bearer = f"Bearer {token.id}.{secret}"

    # JSON parse fail
    res = await client_with_db.post(
        "/api/v1/log-ingest",
        content=b"not-json{",
        headers={"Authorization": bearer, "Content-Type": "application/json"},
    )
    assert res.status_code == 400

    # events 키 없음
    res = await client_with_db.post(
        "/api/v1/log-ingest",
        json={"other": []},
        headers={"Authorization": bearer},
    )
    assert res.status_code == 400


async def test_ingest_db_failure_500(client_with_db, async_session: AsyncSession):
    """insert_events 가 raise → 500."""
    proj, token, secret = await _seed_token(async_session)
    bearer = f"Bearer {token.id}.{secret}"

    async def boom(*args, **kwargs):
        raise RuntimeError("db down")

    with patch("app.services.log_ingest_service.insert_events", side_effect=boom):
        res = await client_with_db.post(
            "/api/v1/log-ingest",
            json={"events": [_valid_event()]},
            headers={"Authorization": bearer},
        )
    assert res.status_code == 500
```

- [ ] **Step 2: Verify failure**

```bash
pytest tests/test_log_ingest_endpoint.py -v 2>&1 | tail -15
```

Expected: 모두 FAIL (404 — endpoint 미존재).

- [ ] **Step 3: Endpoint 구현**

`backend/app/api/v1/endpoints/log_ingest.py` 신규:

```python
"""POST /log-ingest — 외부 (app-chak) 가 호출하는 log batch ingest.

설계서: 2026-05-01-error-log-phase2-ingest-design.md §3.2
"""

import gzip
import json
import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services import log_ingest_service

logger = logging.getLogger(__name__)

router = APIRouter(tags=["log-ingest"])


@router.post("/log-ingest")
async def ingest_logs(
    request: Request,
    authorization: str | None = Header(default=None),
    content_encoding: str | None = Header(default=None),
    x_pslog_dropped_since_last: int | None = Header(default=None, alias="X-pslog-Dropped-Since-Last"),
    db: AsyncSession = Depends(get_db),
):
    """외부 앱이 로그 batch 를 push.

    응답:
    - 200: 정상 또는 부분 성공 (accepted, rejected)
    - 400: gzip / JSON parse fail / events 키 없음 / 모든 event invalid
    - 401: 인증 실패 (사유 구분 안 함, timing attack 회피)
    - 429: rate limit 초과 (Retry-After 헤더)
    - 500: DB 쓰기 실패
    """
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

    # 토큰 검증 (HTTPException 401 raise 시 그대로 propagate)
    key_id, secret = await log_ingest_service.parse_token(authorization)
    token = await log_ingest_service.verify_token(db, key_id, secret)

    # ingest_batch 가 rate limit + validate + insert + commit 처리
    try:
        accepted, rejected = await log_ingest_service.ingest_batch(
            db, token=token,
            payload_dict=payload,
            dropped_since_last=x_pslog_dropped_since_last,
        )
    except HTTPException:
        # rate limit 429 / events 빈 list 400 등 그대로 propagate
        raise
    except Exception:
        logger.exception("log-ingest unexpected error")
        raise HTTPException(status_code=500, detail="Internal error")

    # 모두 invalid → 400
    if accepted == 0 and rejected:
        return JSONResponse(
            status_code=400,
            content={"accepted": 0, "rejected": rejected},
        )

    return {"accepted": accepted, "rejected": rejected}
```

- [ ] **Step 4: Router 마운트**

`backend/app/api/v1/router.py` 에 추가:

```python
from app.api.v1.endpoints.log_ingest import router as log_ingest_router

# ... 기존 include 들 ...
api_v1_router.include_router(log_ingest_router)
```

- [ ] **Step 5: Verify pass + 회귀**

```bash
cd backend && source venv/bin/activate
pytest tests/test_log_ingest_endpoint.py -v 2>&1 | tail -15
pytest -q 2>&1 | tail -3
```

Expected: 신규 8 PASS, 전체 `230 passed` (222 + 8).

- [ ] **Step 6: Commit**

```bash
cd /Users/arden/Documents/ardensdevspace/pslog/.worktrees/error-log-phase2-ingest
git add backend/app/api/v1/endpoints/log_ingest.py backend/app/api/v1/router.py backend/tests/test_log_ingest_endpoint.py
git commit -m "$(cat <<'EOF'
feat(error-log/phase2a): POST /log-ingest endpoint

- gzip 지원 (Content-Encoding: gzip), 잘못된 byte → 400
- JSON parse fail → 400, events 키 없음 → 400
- parse_token + verify_token → 인증 실패 401 (모두 "Invalid token")
- ingest_batch 가 rate limit 429 / partial validate / batch INSERT 처리
- 모든 event invalid → 400 + rejected list, 부분/전체 성공 → 200
- DB 쓰기 fail → 500 (logger.exception)
- 회귀 8건: 정상 / 모두 invalid 400 / gzip OK / gzip fail 400 / 인증 401 / rate 429 / payload malformed 400 / DB fail 500

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 8: 최종 회귀 + handoff + PR

- [ ] **Step 1: 전체 backend 회귀**

```bash
cd /Users/arden/Documents/ardensdevspace/pslog/.worktrees/error-log-phase2-ingest/backend
source venv/bin/activate
pytest -q 2>&1 | tail -3
```

Expected: `230 passed` (198 baseline + 14 service + 4 token + 8 ingest endpoint = 14 new in service file? 풀어보면: parse 4 + verify 4 + rate_limit 4 + validate 5 + insert 1 + ingest_batch 2 = 20 service tests. 4 token + 8 ingest = 12 endpoint. 합계 32 신규. 198 + 32 = 230. ✓).

- [ ] **Step 2: Frontend 영향 없음 확인** (frontend 변경 없음 — skip)

- [ ] **Step 3: handoff 갱신**

`handoffs/main.md` 상단에 새 섹션:

```markdown
## 2026-05-01 (Error-log Phase 2a — Ingest endpoint + Token API)

- [x] **Error-log Phase 2a — Ingest endpoint + Token API** — 브랜치 `feature/error-log-phase2-ingest`
  - [x] **Pydantic schemas**: `LogEventInput / IngestPayload / RejectedEvent / IngestResponse / StackFrame` (log_ingest), `LogTokenCreate / LogTokenResponse / LogTokenRevokedResponse` (log_token). 모두 `extra="forbid"`.
  - [x] **`log_ingest_service` (6 함수)**: parse_token (Bearer <key_id>.<secret>) / verify_token (asyncio.to_thread bcrypt + last_used_at 갱신) / check_rate_limit (PostgreSQL UPSERT, 분 truncate, 429 + Retry-After) / validate_event (Pydantic + version_sha 형식 + extra 4KB) / insert_events (batch INSERT, fingerprint=NULL) / ingest_batch (composition + commit).
  - [x] **POST `/api/v1/log-ingest`**: gzip 지원, partial success 200 + rejected list, 모든 invalid 400, 인증 401 (timing attack 회피 — 모두 "Invalid token"), rate 429 + Retry-After, DB fail 500.
  - [x] **POST `/api/v1/projects/{id}/log-tokens`** (OWNER): `secrets.token_urlsafe(32)` + bcrypt cost 12, 평문 token 응답 1회만.
  - [x] **DELETE `/api/v1/projects/{id}/log-tokens/{id}`** (OWNER): soft delete (revoked_at = now), 이미 revoked 400, 다른 project token 404.
  - [x] **마이그레이션 신규 없음** — Phase 1 alembic 이 모든 컬럼 (`LogIngestToken / RateLimitWindow / LogEvent + rate_limit_per_minute`) 이미 포함.
  - [x] **검증**: backend **230 tests pass** (198 baseline + 32 신규: 14 service + 4 token + 8 ingest endpoint + 6 validate). app-chak handler 가 미사용 상태로 대기 중 (`PSLOG_LOG_ENDPOINT` 비어있음) — 본 phase 머지 즉시 e2e 가능 (토큰 발급 → app-chak `.env` 설정 → 자동 활성).

### 마지막 커밋

- pslog: `<sha> docs(handoff+plan): Error-log Phase 2a 완료 + Phase 2b/3 다음 할 일`
- 브랜치 base: `7e51c20` (main, Phase 6 PR #15 머지 직후)

### 다음 (Error-log Phase 2b / Phase 3)

- **Phase 2b** (운영 인프라): `log_fingerprint_reaper` (부팅 시 `fingerprinted_at IS NULL` 회수 — Phase 3 의 fingerprint_service 와 같이) + `log_health_service` (hourly cron — unknown SHA 비율 / 시계 어긋남 추적). reaper 는 Phase 3 의존성 — Phase 3 와 묶음 권장.
- **Phase 3** (fingerprint + ErrorGroup): `fingerprint_service` (예외 → 결정적 fingerprint) + `error_group_service` (UPSERT + 신규/spike/regression 감지 + cooldown). ingest endpoint 가 BackgroundTask 로 fingerprint 처리 trigger. 핵심 가치 (에러 그룹화) 전달.

### 블로커

없음

### 메모 (2026-05-01 Error-log Phase 2a 추가)

- **bcrypt cost 12 + asyncio.to_thread 패턴**: 동기 bcrypt.checkpw 를 async endpoint 에서 호출 시 event loop block — `await asyncio.to_thread(bcrypt.checkpw, ...)` 로 wrapping. test 에선 cost 4 사용 (속도 — `bcrypt.gensalt(rounds=4)`).
- **timing attack 회피 — fast-path 선택**: 401 detail 모두 "Invalid token" 통일했지만 key_id lookup fail 시 bcrypt 호출 안 함 (~250ms 응답 시간 차이 노출). dummy bcrypt 호출은 비용 / 복잡도 비효율 — v1 는 fast-path 우선. 보안 호소 시 후속 보강.
- **`secrets.token_urlsafe(32)` for secret 생성**: 256-bit (32 bytes) base64 url-safe (~43자). DB 에는 bcrypt(secret, cost=12) 만 저장.
- **Soft delete (revoked_at)**: past LogEvent / RateLimitWindow 의 FK 보존. hard delete 안 함. revoked 토큰은 verify_token 가 401 반환.
- **token.project_id 강제 사용** (security): ingest 시 클라이언트가 다른 project 의 LogEvent INSERT 못 하게 token 의 project_id 만 사용. 외부 input 무시.
- **PostgreSQL UPSERT pattern (RateLimitWindow)**: `pg_insert(...).on_conflict_do_update(index_elements=[...], set_={"event_count": Model.event_count + N}).returning(...)` — 단일 SQL, race-free.
- **Partial validation 200 + rejected list**: 페이로드 N건 중 일부만 invalid → 200 + `{accepted, rejected}`. spec §6.1 "나머지는 정상 처리" 직접 매칭. app-chak handler 의 batch 에서 1건 corrupt 돼도 N-1건 살림.
- **gzip middleware 미사용**: FastAPI 기본 GZip middleware 는 응답 압축만 처리. 요청 body decompress 는 endpoint 가 직접 `gzip.decompress(body)` — 명시적이고 단순.
- **마이그레이션 신규 없음 학습**: Phase 1 의 통합 alembic (`c4dee7f06004`) 이 task-automation + error-log 의 모든 모델/컬럼을 한 번에 추가. error-log 본 phase 는 schema 변경 0 — 순수 service/endpoint 레이어.
- **next 가능 옵션**: Phase 3 (fingerprint + ErrorGroup) 진입. ingest endpoint 에 BackgroundTask 추가해 fingerprint 처리 trigger. log_fingerprint_reaper 도 같이 묶음.
```

- [ ] **Step 4: handoff + plan + spec commit**

```bash
cd /Users/arden/Documents/ardensdevspace/pslog/.worktrees/error-log-phase2-ingest
git add handoffs/main.md docs/superpowers/plans/2026-05-01-error-log-phase2-ingest.md
git commit -m "$(cat <<'EOF'
docs(handoff+plan): Error-log Phase 2a 완료 + Phase 2b/3 다음 할 일

- handoffs/main.md 에 2026-05-01 Error-log Phase 2a 섹션 추가 (ingest + token API, 230 tests)
- docs/superpowers/plans/2026-05-01-error-log-phase2-ingest.md 신규 (구현 plan 보존)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 5: Push + PR**

```bash
git push -u origin feature/error-log-phase2-ingest
gh pr create \
  --title "feat(error-log/phase2a): ingest endpoint + token API + rate limit" \
  --body "$(cat <<'EOF'
## Summary

error-log spec (\`2026-04-26-error-log-design.md\`) 의 Phase 2 본편을 두 sub-phase 로 분할한 첫 번째 — **2a: 토큰 API + ingest endpoint + rate limit**. app-chak 의 Phase 0 handler 가 미사용 상태로 대기 중 — 본 phase 머지 즉시 e2e 동작.

- **Pydantic schemas** (log_ingest + log_token) — 모두 \`extra="forbid"\`
- **\`log_ingest_service\` 6 함수**: parse_token / verify_token (asyncio.to_thread bcrypt) / check_rate_limit (PostgreSQL UPSERT) / validate_event (Pydantic + sha format + 4KB extra) / insert_events / ingest_batch
- **POST /log-ingest**: gzip 지원, partial success 200 + rejected list, 인증 401 (timing attack 회피), rate 429 + Retry-After
- **POST /log-tokens** (OWNER): secrets.token_urlsafe(32) + bcrypt cost 12, 평문 token 응답 1회만
- **DELETE /log-tokens/{id}** (OWNER): soft delete (revoked_at)
- **token.project_id 강제** (security): 외부 input 무시
- **마이그레이션 신규 없음** — Phase 1 alembic 이 모든 컬럼 포함

## Test plan

- [x] backend **230 tests pass** (198 baseline + 32 신규: 20 service + 4 token + 8 ingest)
- [ ] e2e — 사용자 직접:
  - curl POST /log-tokens 로 토큰 발급 → 평문 받기
  - app-chak \`.env\` 에 \`PSLOG_LOG_INGEST_TOKEN\` + \`PSLOG_LOG_ENDPOINT\` 설정 → app-chak 재시작
  - 의도적 \`logger.error("test")\` → pslog DB 의 log_events 테이블에 INSERT 확인
  - gzip 압축 batch + plain JSON batch 둘 다 동작 검증

## 다음 (Phase 2b / Phase 3)

- Phase 2b: log_fingerprint_reaper + log_health_service (Phase 3 와 같이 묶음 권장)
- Phase 3: fingerprint + ErrorGroup (핵심 가치 — 에러 그룹화 + 신규/spike/regression 감지)

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Self-Review Pass

**1. Spec coverage** — spec §1-4 vs plan tasks 매핑:

| Spec 항목 | Plan task |
|---|---|
| §3.1 6 함수 (parse_token / verify_token / check_rate_limit / validate_event / insert_events / ingest_batch) | Tasks 2-5 |
| §3.2 POST /log-ingest (gzip / 401 / 429 / 400 / 500) | Task 7 |
| §3.3 POST /log-tokens (OWNER, secrets + bcrypt 12) | Task 6 |
| §3.4 DELETE /log-tokens (soft delete, 400 already revoked, 404) | Task 6 |
| §3.5 Pydantic schemas | Task 1 |
| §4.1 service 8 tests | Task 2-5 (parse 4 + verify 4 + rate_limit 4 + validate 5 + insert 1 + ingest_batch 2 = 20 — spec 의 "8건" 보다 많음. 이유: rate_limit 과 validate_event 가 분기 많음 — 분리 작성. spec 의 의도 (각 함수 충분히 검증) 충족.) |
| §4.2 endpoint 8 tests | Task 7 (8건 정확) |
| §4.3 token API 4 tests | Task 6 (4건 정확) |
| §4.4 e2e | PR 본문 체크리스트 |

**2. Placeholder scan** — `<sha>` 만 (Task 8 handoff commit 후 자리표시). "TBD/TODO" 0.

**3. Type / signature consistency**:
- `parse_token(header) -> tuple[UUID, str]` — Task 2 정의 ↔ Task 7 endpoint 호출 (`key_id, secret = await parse_token(authorization)`) — 일관
- `verify_token(db, key_id, secret) -> LogIngestToken` — 일관
- `check_rate_limit(db, *, project_id, token, batch_size, now) -> None` — keyword-only 인자, 모든 호출 매칭
- `validate_event(event_dict, index, project_id) -> tuple[LogEvent | None, dict | None]` — Task 4 정의 ↔ Task 5 ingest_batch 호출 일관
- `insert_events(db, events) -> int` — 일관
- `ingest_batch(db, *, token, payload_dict, dropped_since_last, now=None) -> tuple[int, list[dict]]` — Task 5 정의 ↔ Task 7 endpoint 호출 일관 (now 생략 — 기본 datetime.utcnow())
- `LogTokenCreate.rate_limit_per_minute: int | None` ↔ endpoint 의 `data.rate_limit_per_minute or 600` — None 처리 일관
- `LogIngestToken.project_id` 사용 (외부 input 무시) — Task 5 ingest_batch + Task 4 validate_event 양쪽 일관

**4. 의존 순서**:
- Task 1 (schemas) → Task 2 (parse/verify, schemas import) → Task 3 (rate limit) → Task 4 (validate/insert, schema 사용) → Task 5 (ingest_batch composition) → Task 6 (token API, schema 사용) → Task 7 (ingest endpoint, service 사용) → Task 8 (PR)
- 현재 순서 의존 만족.

**5. 테스트 결정성**:
- 모든 service test: 결정적 (asyncio.to_thread bcrypt 도 단일 thread, race 없음)
- check_rate_limit: 시각 명시 (datetime(2026, 5, 1, ...)) — flaky 없음
- endpoint test: client_with_db fixture — 결정적

**6. b1/b2/Phase6 학습 적용**:
- expire_on_commit=False 가정 (test conftest 에 이미 있음 — 별도 처리 불필요)
- rollback path 없음 (본 phase 는 외부 인증 없는 endpoint, ORM expire 함정 미발생)
- alembic migration 없음 (Phase 1 통합 — autogenerate 위협 없음)

문제 없음. 진행 가능.
