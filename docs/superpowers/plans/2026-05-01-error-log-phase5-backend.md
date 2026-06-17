# Error-log Phase 5 — Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Phase 5 UI 가 사용할 backend endpoint 3개 — PATCH /errors/{id} (status 전이), GET /log-tokens (목록), GET /log-health (unknown SHA 비율 등) — 추가.

**Architecture:** 기존 패턴 그대로. schema (Pydantic) + service (로직) + endpoint (FastAPI) 3 레이어. 권한은 A1 의 `require_project_member` 헬퍼 재사용. Phase 4 의 `log_query_service` / Phase 3 의 `error_group_service` 와 같은 디렉토리/네이밍.

**Tech Stack:** FastAPI 0.115+, PostgreSQL, SQLAlchemy 2.0 async, Pydantic v2.

---

## File Structure

| File | 역할 | new/modify |
|---|---|---|
| `backend/app/schemas/log_query.py` | + `ErrorGroupStatusUpdate` (PATCH 요청 body) | modify |
| `backend/app/schemas/log_token.py` | + `LogTokenSummary` / `LogTokenListResponse` (GET 응답) | modify |
| `backend/app/schemas/log_health.py` | LogHealthResponse + 컴포넌트 | **new** |
| `backend/app/services/error_group_service.py` | + `transition_status` (사용자 액션) | modify |
| `backend/app/services/log_health_service.py` | `compute_health` — 24h 윈도우 집계 | **new** |
| `backend/app/api/v1/endpoints/log_errors.py` | + `PATCH /errors/{group_id}` route | modify |
| `backend/app/api/v1/endpoints/log_tokens.py` | + `GET /log-tokens` route | modify |
| `backend/app/api/v1/endpoints/log_health.py` | `GET /log-health` route + router | **new** |
| `backend/app/api/v1/router.py` | log_health_router 등록 | modify |
| `tests/test_error_group_status_transition.py` | service 유닛 — 전이 매트릭스 | **new** |
| `tests/test_log_errors_patch_endpoint.py` | endpoint 통합 | **new** |
| `tests/test_log_tokens_list_endpoint.py` | endpoint 통합 | **new** |
| `tests/test_log_health_service.py` | service 유닛 | **new** |
| `tests/test_log_health_endpoint.py` | endpoint 통합 | **new** |

순효과: ~+800 LOC (test 절반). 마이그레이션 없음.

---

## 사용자 액션 status 전이 매트릭스 (PATCH 핵심 사양)

설계서 §4.1 의 다이어그램 + REGRESSED 보강:

| 현재 | 액션 | 다음 | 부수 효과 |
|---|---|---|---|
| OPEN | resolve | RESOLVED | `resolved_at=now`, `resolved_by_user_id`, `resolved_in_version_sha` (요청 body 또는 None) |
| OPEN | ignore | IGNORED | 없음 (event_count 는 계속 증가) |
| RESOLVED | reopen | OPEN | `resolved_at=NULL`, `resolved_by_user_id=NULL`, `resolved_in_version_sha=NULL` |
| IGNORED | unmute | OPEN | 없음 |
| REGRESSED | resolve | RESOLVED | OPEN→RESOLVED 와 동일 |
| REGRESSED | reopen | OPEN | RESOLVED→OPEN 과 동일 (resolved_* 필드 이미 NULL 일 수 있음, 안전 clear) |
| any | * | self | 400 Conflict — "이미 X 상태입니다" |
| OPEN | regressed | 400 | 자동 전이 (event 기반), 사용자 액션 아님 |
| RESOLVED | ignore | 400 | 직접 전이 없음 — reopen 후 ignore |
| IGNORED | resolve | 400 | 직접 전이 없음 |

PATCH 요청 body: `{"action": "resolve" | "ignore" | "reopen" | "unmute", "resolved_in_version_sha": "..." | null}`

action 기반이 status 기반보다 안전 — 사용자가 우연히 RESOLVED 같은 final state 를 직접 보낼 수 없음.

---

## Task 1: PATCH /errors/{group_id} — schema

**Files:**
- Modify: `backend/app/schemas/log_query.py` (append at end)

- [ ] **Step 1: 요청 body schema 추가**

`backend/app/schemas/log_query.py` 끝에 append:

```python
# ---- PATCH /errors/{id} ----

from typing import Literal


class ErrorGroupStatusUpdate(BaseModel):
    """PATCH /errors/{id} 요청 body. action 기반 (status 직접 X)."""
    model_config = ConfigDict(extra="forbid")
    action: Literal["resolve", "ignore", "reopen", "unmute"]
    resolved_in_version_sha: str | None = None  # action='resolve' 일 때만 의미.
```

`extra="forbid"` — 알 수 없는 필드 거부 (security in depth, log_ingest schema 와 같은 정책).

`Literal` action 타입 — Pydantic 이 자동 검증, 잘못된 action → 422.

- [ ] **Step 2: import 정리 확인**

`log_query.py` 상단의 import 가 `Literal` 을 직접 안 쓰면 추가:

기존 `from typing import ...` 가 있으면 `Literal` 추가, 없으면 `from typing import Literal` 라인 추가. 본 파일은 step 1 의 inline import 로 처리되므로 별도 수정 불필요 — 단, lint 가 file-top imports 를 요구하면 새 import 라인을 다른 import 들 옆으로 이동.

- [ ] **Step 3: ast parse 확인**

```bash
cd backend && ./venv/bin/python -c "from app.schemas.log_query import ErrorGroupStatusUpdate; print('OK')"
```
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add backend/app/schemas/log_query.py
git commit -m "feat(error-log/phase5-backend): ErrorGroupStatusUpdate 요청 schema (Task 1)"
```

---

## Task 2: error_group_service.transition_status — 서비스 로직 + 단위 테스트

**Files:**
- Modify: `backend/app/services/error_group_service.py` (append)
- Create: `backend/tests/test_error_group_status_transition.py`

- [ ] **Step 1: 실패 테스트 — 합법 전이 5종**

`tests/test_error_group_status_transition.py` 생성:

```python
"""ErrorGroup user-driven status transition unit tests.

설계서: 2026-04-26-error-log-design.md §4.1 전이 다이어그램.
"""

from datetime import datetime
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.error_group import ErrorGroup, ErrorGroupStatus
from app.models.project import Project
from app.models.user import User
from app.models.workspace import Workspace
from app.services import error_group_service


async def _seed_group_user(
    db: AsyncSession, *, status: ErrorGroupStatus = ErrorGroupStatus.OPEN,
) -> tuple[ErrorGroup, User]:
    user = User(
        email=f"u-{uuid4().hex[:8]}@x", name="u", password_hash="x",
    )
    db.add(user)
    await db.flush()
    ws = Workspace(name="w", slug=f"w-{uuid4().hex[:8]}")
    db.add(ws)
    await db.flush()
    proj = Project(workspace_id=ws.id, name="p")
    db.add(proj)
    await db.flush()
    now = datetime.utcnow()
    group = ErrorGroup(
        project_id=proj.id, fingerprint="fp",
        exception_class="ValueError", exception_message_sample="x",
        first_seen_at=now, first_seen_version_sha="a" * 40,
        last_seen_at=now, last_seen_version_sha="a" * 40,
        event_count=1, status=status,
    )
    db.add(group)
    await db.commit()
    await db.refresh(group)
    await db.refresh(user)
    return group, user


@pytest.mark.parametrize(
    "from_status,action,to_status",
    [
        (ErrorGroupStatus.OPEN, "resolve", ErrorGroupStatus.RESOLVED),
        (ErrorGroupStatus.OPEN, "ignore", ErrorGroupStatus.IGNORED),
        (ErrorGroupStatus.RESOLVED, "reopen", ErrorGroupStatus.OPEN),
        (ErrorGroupStatus.IGNORED, "unmute", ErrorGroupStatus.OPEN),
        (ErrorGroupStatus.REGRESSED, "resolve", ErrorGroupStatus.RESOLVED),
        (ErrorGroupStatus.REGRESSED, "reopen", ErrorGroupStatus.OPEN),
    ],
)
async def test_legal_transitions(
    async_session: AsyncSession,
    from_status: ErrorGroupStatus,
    action: str,
    to_status: ErrorGroupStatus,
):
    group, user = await _seed_group_user(async_session, status=from_status)
    updated = await error_group_service.transition_status(
        async_session, group, action=action, user_id=user.id,
        resolved_in_version_sha=None,
    )
    assert updated.status == to_status
```

- [ ] **Step 2: 실패 테스트 — 불법 전이 + audit 필드**

같은 파일에 추가:

```python
@pytest.mark.parametrize(
    "from_status,action",
    [
        (ErrorGroupStatus.RESOLVED, "ignore"),
        (ErrorGroupStatus.IGNORED, "resolve"),
        (ErrorGroupStatus.OPEN, "reopen"),     # 이미 OPEN
        (ErrorGroupStatus.RESOLVED, "resolve"),  # 이미 RESOLVED
        (ErrorGroupStatus.IGNORED, "ignore"),  # 이미 IGNORED
    ],
)
async def test_illegal_transitions_raise(
    async_session: AsyncSession,
    from_status: ErrorGroupStatus,
    action: str,
):
    group, user = await _seed_group_user(async_session, status=from_status)
    with pytest.raises(ValueError) as exc:
        await error_group_service.transition_status(
            async_session, group, action=action, user_id=user.id,
            resolved_in_version_sha=None,
        )
    assert "illegal transition" in str(exc.value).lower()


async def test_resolve_sets_audit_fields(async_session: AsyncSession):
    group, user = await _seed_group_user(async_session, status=ErrorGroupStatus.OPEN)
    sha = "f" * 40
    updated = await error_group_service.transition_status(
        async_session, group, action="resolve", user_id=user.id,
        resolved_in_version_sha=sha,
    )
    assert updated.status == ErrorGroupStatus.RESOLVED
    assert updated.resolved_at is not None
    assert updated.resolved_by_user_id == user.id
    assert updated.resolved_in_version_sha == sha


async def test_reopen_clears_audit_fields(async_session: AsyncSession):
    group, user = await _seed_group_user(async_session, status=ErrorGroupStatus.OPEN)
    sha = "f" * 40
    await error_group_service.transition_status(
        async_session, group, action="resolve", user_id=user.id,
        resolved_in_version_sha=sha,
    )
    assert group.resolved_at is not None  # state 확인

    await error_group_service.transition_status(
        async_session, group, action="reopen", user_id=user.id,
        resolved_in_version_sha=None,
    )
    assert group.status == ErrorGroupStatus.OPEN
    assert group.resolved_at is None
    assert group.resolved_by_user_id is None
    assert group.resolved_in_version_sha is None


async def test_resolve_without_sha_keeps_field_none(async_session: AsyncSession):
    """resolved_in_version_sha 미제공 — None 으로 저장 (선택적)."""
    group, user = await _seed_group_user(async_session, status=ErrorGroupStatus.OPEN)
    updated = await error_group_service.transition_status(
        async_session, group, action="resolve", user_id=user.id,
        resolved_in_version_sha=None,
    )
    assert updated.status == ErrorGroupStatus.RESOLVED
    assert updated.resolved_in_version_sha is None
    assert updated.resolved_at is not None  # audit 필드는 sha 없어도 채움
    assert updated.resolved_by_user_id == user.id
```

- [ ] **Step 3: 테스트 실행 — fail 확인**

```bash
cd backend && ./venv/bin/pytest tests/test_error_group_status_transition.py -q --no-header 2>&1 | tail -10
```
Expected: 모든 테스트 fail with `AttributeError: module 'app.services.error_group_service' has no attribute 'transition_status'`

- [ ] **Step 4: 구현 — `transition_status` 함수**

`backend/app/services/error_group_service.py` 에 append:

```python
# ---- 사용자 액션 기반 status 전이 ----

# 합법 전이 매트릭스: (현재 status, action) -> 다음 status
_LEGAL_TRANSITIONS: dict[tuple[ErrorGroupStatus, str], ErrorGroupStatus] = {
    (ErrorGroupStatus.OPEN, "resolve"): ErrorGroupStatus.RESOLVED,
    (ErrorGroupStatus.OPEN, "ignore"): ErrorGroupStatus.IGNORED,
    (ErrorGroupStatus.RESOLVED, "reopen"): ErrorGroupStatus.OPEN,
    (ErrorGroupStatus.IGNORED, "unmute"): ErrorGroupStatus.OPEN,
    (ErrorGroupStatus.REGRESSED, "resolve"): ErrorGroupStatus.RESOLVED,
    (ErrorGroupStatus.REGRESSED, "reopen"): ErrorGroupStatus.OPEN,
}


async def transition_status(
    db: AsyncSession,
    group: ErrorGroup,
    *,
    action: str,
    user_id: UUID,
    resolved_in_version_sha: str | None,
) -> ErrorGroup:
    """사용자 액션으로 ErrorGroup status 전이. 합법 전이만 허용.

    Raises:
        ValueError: 불법 전이 ((현재, action) tuple 이 _LEGAL_TRANSITIONS 에 없음).
    """
    key = (group.status, action)
    next_status = _LEGAL_TRANSITIONS.get(key)
    if next_status is None:
        raise ValueError(
            f"illegal transition: {group.status.value} + {action!r}"
        )

    group.status = next_status

    if next_status == ErrorGroupStatus.RESOLVED:
        group.resolved_at = datetime.utcnow()
        group.resolved_by_user_id = user_id
        group.resolved_in_version_sha = resolved_in_version_sha
    elif next_status == ErrorGroupStatus.OPEN and action == "reopen":
        # RESOLVED → OPEN 시 audit 필드 클리어.
        group.resolved_at = None
        group.resolved_by_user_id = None
        group.resolved_in_version_sha = None

    await db.commit()
    await db.refresh(group)
    return group
```

import 추가 확인 — 파일 상단에 이미 `from datetime import datetime` 있는지 확인. 없으면 import 추가.

- [ ] **Step 5: 테스트 실행 — pass 확인**

```bash
cd backend && ./venv/bin/pytest tests/test_error_group_status_transition.py -q --no-header 2>&1 | tail -5
```
Expected: `15 passed` (parametrize 6 + parametrize 5 + 3 단일 = 14, 정확히는 15 with all params)

전체 회귀:

```bash
cd backend && ./venv/bin/pytest tests/test_error_group_service.py -q --no-header 2>&1 | tail -5
```
Expected: 기존 통과 그대로 (회귀 0).

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/error_group_service.py backend/tests/test_error_group_status_transition.py
git commit -m "feat(error-log/phase5-backend): error_group_service.transition_status (Task 2)"
```

---

## Task 3: PATCH endpoint — log_errors.py + 통합 테스트

**Files:**
- Modify: `backend/app/api/v1/endpoints/log_errors.py` (append)
- Create: `backend/tests/test_log_errors_patch_endpoint.py`

- [ ] **Step 1: 실패 테스트 — endpoint happy path + 권한**

`tests/test_log_errors_patch_endpoint.py` 생성:

```python
"""PATCH /errors/{group_id} 통합 테스트.

설계서: 2026-04-26-error-log-design.md §4.1, 5.2
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


async def _seed(
    db: AsyncSession, *, role: WorkspaceRole = WorkspaceRole.OWNER,
    group_status: ErrorGroupStatus = ErrorGroupStatus.OPEN,
) -> tuple[User, Project, ErrorGroup]:
    user = User(
        email=f"u-{uuid.uuid4().hex[:8]}@x", name="u", password_hash="x",
    )
    db.add(user)
    await db.flush()
    ws = Workspace(name="w", slug=f"w-{uuid.uuid4().hex[:8]}")
    db.add(ws)
    await db.flush()
    proj = Project(workspace_id=ws.id, name="p")
    db.add(proj)
    await db.flush()
    db.add(ProjectMember(project_id=proj.id, user_id=user.id, role=role))
    now = datetime.utcnow()
    group = ErrorGroup(
        project_id=proj.id, fingerprint="fp",
        exception_class="ValueError", exception_message_sample="x",
        first_seen_at=now, first_seen_version_sha="a" * 40,
        last_seen_at=now, last_seen_version_sha="a" * 40,
        event_count=1, status=group_status,
    )
    db.add(group)
    await db.commit()
    await db.refresh(user)
    await db.refresh(proj)
    await db.refresh(group)
    return user, proj, group


def _auth(user: User) -> dict[str, str]:
    from app.services.auth_service import create_access_token
    tok = create_access_token({"sub": str(user.id)})
    return {"Authorization": f"Bearer {tok}"}


async def test_patch_resolve_ok(client_with_db, async_session: AsyncSession):
    user, proj, group = await _seed(async_session)
    sha = "f" * 40
    resp = await client_with_db.patch(
        f"/api/v1/projects/{proj.id}/errors/{group.id}",
        json={"action": "resolve", "resolved_in_version_sha": sha},
        headers=_auth(user),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "resolved"


async def test_patch_owner_required(client_with_db, async_session: AsyncSession):
    user, proj, group = await _seed(async_session, role=WorkspaceRole.EDITOR)
    resp = await client_with_db.patch(
        f"/api/v1/projects/{proj.id}/errors/{group.id}",
        json={"action": "resolve"},
        headers=_auth(user),
    )
    assert resp.status_code == 403


async def test_patch_non_member_404(client_with_db, async_session: AsyncSession):
    user, proj, group = await _seed(async_session)
    # 다른 user — proj 멤버 아님
    other = User(email="other@x", name="o", password_hash="x")
    async_session.add(other)
    await async_session.commit()
    await async_session.refresh(other)

    resp = await client_with_db.patch(
        f"/api/v1/projects/{proj.id}/errors/{group.id}",
        json={"action": "resolve"},
        headers=_auth(other),
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Project not found"


async def test_patch_illegal_transition_400(client_with_db, async_session: AsyncSession):
    user, proj, group = await _seed(async_session, group_status=ErrorGroupStatus.RESOLVED)
    resp = await client_with_db.patch(
        f"/api/v1/projects/{proj.id}/errors/{group.id}",
        json={"action": "ignore"},  # RESOLVED → IGNORED 직접 전이 X
        headers=_auth(user),
    )
    assert resp.status_code == 400
    assert "illegal transition" in resp.json()["detail"].lower()


async def test_patch_unknown_action_422(client_with_db, async_session: AsyncSession):
    user, proj, group = await _seed(async_session)
    resp = await client_with_db.patch(
        f"/api/v1/projects/{proj.id}/errors/{group.id}",
        json={"action": "delete"},
        headers=_auth(user),
    )
    assert resp.status_code == 422  # Pydantic Literal 검증


async def test_patch_extra_field_rejected(client_with_db, async_session: AsyncSession):
    user, proj, group = await _seed(async_session)
    resp = await client_with_db.patch(
        f"/api/v1/projects/{proj.id}/errors/{group.id}",
        json={"action": "resolve", "status": "resolved"},  # extra='forbid'
        headers=_auth(user),
    )
    assert resp.status_code == 422


async def test_patch_group_not_in_project_404(client_with_db, async_session: AsyncSession):
    user, proj, group = await _seed(async_session)
    # 다른 project 의 group_id 로 PATCH 시도
    other_proj = Project(workspace_id=proj.workspace_id, name="other")
    async_session.add(other_proj)
    await async_session.commit()
    await async_session.refresh(other_proj)
    async_session.add(ProjectMember(
        project_id=other_proj.id, user_id=user.id, role=WorkspaceRole.OWNER,
    ))
    await async_session.commit()

    resp = await client_with_db.patch(
        f"/api/v1/projects/{other_proj.id}/errors/{group.id}",  # group 은 첫 proj
        json={"action": "resolve"},
        headers=_auth(user),
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Error group not found"
```

- [ ] **Step 2: 테스트 실행 — fail 확인**

```bash
cd backend && ./venv/bin/pytest tests/test_log_errors_patch_endpoint.py -q --no-header 2>&1 | tail -10
```
Expected: 7 fail with 405 Method Not Allowed.

- [ ] **Step 3: 구현 — PATCH endpoint**

`backend/app/api/v1/endpoints/log_errors.py` 의 import 영역에 추가:

```python
from app.models.error_group import ErrorGroup, ErrorGroupStatus
from app.schemas.log_query import (
    ...,
    ErrorGroupStatusUpdate,
)
from app.services import error_group_service, log_query_service
```

같은 파일 끝에 PATCH 엔드포인트 append:

```python
@router.patch(
    "/{project_id}/errors/{group_id}",
    response_model=ErrorGroupSummary,
)
async def patch_error_status(
    project_id: UUID,
    group_id: UUID,
    update: ErrorGroupStatusUpdate,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
    _role: WorkspaceRole = Depends(require_project_member(
        min_role=WorkspaceRole.OWNER,
        hide_existence=True,
        denied_detail="Owner only",
    )),
):
    """ErrorGroup status 전이 (resolve/ignore/reopen/unmute, OWNER 전용).

    설계서: 2026-04-26-error-log-design.md §4.1 전이 다이어그램.
    """
    group = await db.get(ErrorGroup, group_id)
    if group is None or group.project_id != project_id:
        raise HTTPException(status_code=404, detail="Error group not found")

    try:
        updated = await error_group_service.transition_status(
            db, group,
            action=update.action,
            user_id=user.id,
            resolved_in_version_sha=update.resolved_in_version_sha,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return ErrorGroupSummary.model_validate(updated)
```

import 정리:
- 기존 `log_errors.py` 의 from-imports 에 `CurrentUser`, `ErrorGroupStatus`, `WorkspaceRole`, `ErrorGroupStatusUpdate`, `error_group_service` 가 있는지 확인하고 누락분 추가.
- 이미 있는 import (예: `from app.dependencies import CurrentUser`) 가 있으면 중복 추가하지 말고 그대로 유지.

- [ ] **Step 4: 테스트 실행 — pass 확인**

```bash
cd backend && ./venv/bin/pytest tests/test_log_errors_patch_endpoint.py -q --no-header 2>&1 | tail -5
```
Expected: `7 passed`

회귀:

```bash
cd backend && ./venv/bin/pytest tests/test_log_errors_endpoint.py tests/test_log_query_service.py -q --no-header 2>&1 | tail -5
```
Expected: 기존 통과.

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/v1/endpoints/log_errors.py backend/tests/test_log_errors_patch_endpoint.py
git commit -m "feat(error-log/phase5-backend): PATCH /errors/{id} status 전이 endpoint (Task 3)"
```

---

## Task 4: GET /log-tokens — schema + endpoint + 통합 테스트

**Files:**
- Modify: `backend/app/schemas/log_token.py` (append)
- Modify: `backend/app/api/v1/endpoints/log_tokens.py` (append)
- Create: `backend/tests/test_log_tokens_list_endpoint.py`

- [ ] **Step 1: 응답 schema 추가**

`backend/app/schemas/log_token.py` 끝에 append:

```python
class LogTokenSummary(BaseModel):
    """GET /log-tokens 목록 항목. **secret 은 절대 노출 금지** (response_model 로 컴파일 시 보장)."""
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str
    rate_limit_per_minute: int
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None


class LogTokenListResponse(BaseModel):
    items: list[LogTokenSummary]
```

import 확인 — 파일 상단의 `from pydantic import BaseModel, ConfigDict` / `from uuid import UUID` / `from datetime import datetime` 누락분 추가.

`secret_hash` 필드는 schema 에 없음 — FastAPI `response_model` 이 강제로 dict-필터, 모델 instance 의 `secret_hash` 가 응답에서 제외됨.

- [ ] **Step 2: 실패 테스트 — endpoint happy path + 권한**

`tests/test_log_tokens_list_endpoint.py` 생성:

```python
"""GET /log-tokens 통합 테스트."""

import uuid
from datetime import datetime

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


async def _seed(
    db: AsyncSession, *, role: WorkspaceRole = WorkspaceRole.OWNER, n_tokens: int = 2,
    n_revoked: int = 0,
) -> tuple[User, Project, list[LogIngestToken]]:
    user = User(email=f"u-{uuid.uuid4().hex[:8]}@x", name="u", password_hash="x")
    db.add(user)
    await db.flush()
    ws = Workspace(name="w", slug=f"w-{uuid.uuid4().hex[:8]}")
    db.add(ws)
    await db.flush()
    proj = Project(workspace_id=ws.id, name="p")
    db.add(proj)
    await db.flush()
    db.add(ProjectMember(project_id=proj.id, user_id=user.id, role=role))

    tokens = []
    for i in range(n_tokens):
        t = LogIngestToken(
            project_id=proj.id, name=f"tok-{i}",
            secret_hash="$2b$12$test", rate_limit_per_minute=600,
        )
        if i < n_revoked:
            t.revoked_at = datetime.utcnow()
        db.add(t)
        tokens.append(t)
    await db.commit()
    await db.refresh(user)
    await db.refresh(proj)
    for t in tokens:
        await db.refresh(t)
    return user, proj, tokens


def _auth(user: User) -> dict[str, str]:
    from app.services.auth_service import create_access_token
    tok = create_access_token({"sub": str(user.id)})
    return {"Authorization": f"Bearer {tok}"}


async def test_list_tokens_active_only_default(
    client_with_db, async_session: AsyncSession,
):
    user, proj, _ = await _seed(async_session, n_tokens=3, n_revoked=1)
    resp = await client_with_db.get(
        f"/api/v1/projects/{proj.id}/log-tokens",
        headers=_auth(user),
    )
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 2  # 1 revoked 제외, default include_revoked=False
    assert all(item["revoked_at"] is None for item in items)


async def test_list_tokens_include_revoked(client_with_db, async_session: AsyncSession):
    user, proj, _ = await _seed(async_session, n_tokens=3, n_revoked=1)
    resp = await client_with_db.get(
        f"/api/v1/projects/{proj.id}/log-tokens?include_revoked=true",
        headers=_auth(user),
    )
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 3


async def test_list_tokens_no_secret_leaked(
    client_with_db, async_session: AsyncSession,
):
    user, proj, _ = await _seed(async_session)
    resp = await client_with_db.get(
        f"/api/v1/projects/{proj.id}/log-tokens",
        headers=_auth(user),
    )
    assert resp.status_code == 200
    body = resp.json()
    serialized = repr(body)
    assert "secret_hash" not in serialized
    assert "$2b$" not in serialized


async def test_list_tokens_owner_required(
    client_with_db, async_session: AsyncSession,
):
    user, proj, _ = await _seed(async_session, role=WorkspaceRole.EDITOR)
    resp = await client_with_db.get(
        f"/api/v1/projects/{proj.id}/log-tokens",
        headers=_auth(user),
    )
    assert resp.status_code == 403  # 멤버지만 OWNER 미달 → 403 (2단계 정책)


async def test_list_tokens_non_member_404(
    client_with_db, async_session: AsyncSession,
):
    user, proj, _ = await _seed(async_session)
    other = User(email="o@x", name="o", password_hash="x")
    async_session.add(other)
    await async_session.commit()
    await async_session.refresh(other)

    resp = await client_with_db.get(
        f"/api/v1/projects/{proj.id}/log-tokens",
        headers=_auth(other),
    )
    assert resp.status_code == 404
```

- [ ] **Step 3: 테스트 실행 — fail 확인**

```bash
cd backend && ./venv/bin/pytest tests/test_log_tokens_list_endpoint.py -q --no-header 2>&1 | tail -10
```
Expected: 5 fail with 405.

- [ ] **Step 4: 구현 — GET endpoint**

`backend/app/api/v1/endpoints/log_tokens.py` 의 import 추가:

```python
from sqlalchemy import select
from app.schemas.log_token import (
    LogTokenCreate, LogTokenListResponse, LogTokenResponse,
    LogTokenRevokedResponse, LogTokenSummary,
)
```

같은 파일 끝에 GET endpoint append:

```python
@router.get(
    "/{project_id}/log-tokens",
    response_model=LogTokenListResponse,
)
async def list_log_tokens(
    project_id: UUID,
    db: AsyncSession = Depends(get_db),
    include_revoked: bool = False,
    _role: WorkspaceRole = Depends(require_project_member(
        min_role=WorkspaceRole.OWNER,
        hide_existence=True,
        denied_detail="Owner only",
    )),
):
    """프로젝트의 LogIngestToken 목록 (OWNER 전용).

    secret 은 응답에 절대 포함 X (response_model 강제 필터).
    include_revoked=true 시 revoked 포함, default False.
    """
    stmt = select(LogIngestToken).where(LogIngestToken.project_id == project_id)
    if not include_revoked:
        stmt = stmt.where(LogIngestToken.revoked_at.is_(None))
    stmt = stmt.order_by(LogIngestToken.created_at.desc())

    rows = (await db.execute(stmt)).scalars().all()
    return LogTokenListResponse(
        items=[LogTokenSummary.model_validate(t) for t in rows],
    )
```

- [ ] **Step 5: 테스트 실행 — pass 확인**

```bash
cd backend && ./venv/bin/pytest tests/test_log_tokens_list_endpoint.py tests/test_log_tokens_endpoint.py -q --no-header 2>&1 | tail -5
```
Expected: 5 pass + 4 기존 통과.

- [ ] **Step 6: Commit**

```bash
git add backend/app/schemas/log_token.py backend/app/api/v1/endpoints/log_tokens.py backend/tests/test_log_tokens_list_endpoint.py
git commit -m "feat(error-log/phase5-backend): GET /log-tokens 목록 endpoint (Task 4)"
```

---

## Task 5: log_health schema + service + 단위 테스트

**Files:**
- Create: `backend/app/schemas/log_health.py`
- Create: `backend/app/services/log_health_service.py`
- Create: `backend/tests/test_log_health_service.py`

- [ ] **Step 1: schema 작성**

`backend/app/schemas/log_health.py` 생성:

```python
"""log-health API 의 Pydantic schemas.

설계서: 2026-04-26-error-log-design.md §7 (Health 표).
"""

from pydantic import BaseModel


class LogHealthResponse(BaseModel):
    """24h 윈도우 헬스 메트릭. cron 또는 사용자 GET 호출.

    unknown_sha_ratio = (version_sha == 'unknown' 이벤트 수) / (전체 이벤트 수)
    clock_drift_count = abs(received_at - emitted_at) > 1h 인 이벤트 수
    total_events = 24h 내 LogEvent 총 수
    """
    total_events_24h: int
    unknown_sha_count_24h: int
    unknown_sha_ratio_24h: float  # 0.0 ~ 1.0
    clock_drift_count_24h: int
    threshold_unknown_ratio: float = 0.05  # 설계서: > 5% 시 경고
```

`dropped_count_total` 은 v1 에서 미지원 — `X-pslog-Dropped-Since-Last` 헤더 저장 인프라 미구현. 추후 PR 에서 추가.

- [ ] **Step 2: 실패 테스트 — service 단위**

`tests/test_log_health_service.py` 생성:

```python
"""log_health_service.compute_health 단위 테스트."""

import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.log_event import LogEvent, LogLevel
from app.models.project import Project
from app.models.workspace import Workspace
from app.services import log_health_service


async def _seed_project(db: AsyncSession) -> Project:
    ws = Workspace(name="w", slug=f"w-{uuid.uuid4().hex[:8]}")
    db.add(ws)
    await db.flush()
    proj = Project(workspace_id=ws.id, name="p")
    db.add(proj)
    await db.commit()
    await db.refresh(proj)
    return proj


def _evt(
    proj_id, *, version_sha: str = "a" * 40, level: LogLevel = LogLevel.INFO,
    emitted_offset_minutes: float = 0.0, received_offset_minutes: float = 0.0,
) -> LogEvent:
    """현재 시각 기준 received_at, emitted_at 을 분 단위 offset 으로 조정."""
    now = datetime.utcnow()
    return LogEvent(
        project_id=proj_id,
        level=level, message="x", logger_name="l",
        version_sha=version_sha, environment="prod", hostname="h",
        emitted_at=now + timedelta(minutes=emitted_offset_minutes),
        received_at=now + timedelta(minutes=received_offset_minutes),
    )


async def test_compute_health_empty(async_session: AsyncSession):
    proj = await _seed_project(async_session)
    health = await log_health_service.compute_health(async_session, project_id=proj.id)
    assert health["total_events_24h"] == 0
    assert health["unknown_sha_count_24h"] == 0
    assert health["unknown_sha_ratio_24h"] == 0.0
    assert health["clock_drift_count_24h"] == 0


async def test_compute_health_unknown_ratio(async_session: AsyncSession):
    proj = await _seed_project(async_session)
    # 4 known + 1 unknown = 20% ratio
    for _ in range(4):
        async_session.add(_evt(proj.id, version_sha="a" * 40))
    async_session.add(_evt(proj.id, version_sha="unknown"))
    await async_session.commit()

    health = await log_health_service.compute_health(async_session, project_id=proj.id)
    assert health["total_events_24h"] == 5
    assert health["unknown_sha_count_24h"] == 1
    assert abs(health["unknown_sha_ratio_24h"] - 0.2) < 1e-9


async def test_compute_health_clock_drift(async_session: AsyncSession):
    proj = await _seed_project(async_session)
    # 1 정상 + 1 시계 어긋남 (received - emitted = 90분)
    async_session.add(_evt(proj.id, emitted_offset_minutes=0, received_offset_minutes=0))
    async_session.add(_evt(proj.id, emitted_offset_minutes=-90, received_offset_minutes=0))
    await async_session.commit()

    health = await log_health_service.compute_health(async_session, project_id=proj.id)
    assert health["total_events_24h"] == 2
    assert health["clock_drift_count_24h"] == 1


async def test_compute_health_excludes_old(async_session: AsyncSession):
    """24h 보다 오래된 이벤트는 카운트 제외."""
    proj = await _seed_project(async_session)
    # 25h 전 — 제외
    async_session.add(_evt(proj.id, emitted_offset_minutes=-25*60, received_offset_minutes=-25*60))
    # 1h 전 — 포함
    async_session.add(_evt(proj.id, emitted_offset_minutes=-60, received_offset_minutes=-60))
    await async_session.commit()

    health = await log_health_service.compute_health(async_session, project_id=proj.id)
    assert health["total_events_24h"] == 1


async def test_compute_health_isolated_per_project(async_session: AsyncSession):
    """다른 프로젝트의 이벤트는 카운트 안 함."""
    proj_a = await _seed_project(async_session)
    proj_b = await _seed_project(async_session)
    async_session.add(_evt(proj_a.id))
    async_session.add(_evt(proj_b.id, version_sha="unknown"))
    async_session.add(_evt(proj_b.id, version_sha="unknown"))
    await async_session.commit()

    health_a = await log_health_service.compute_health(async_session, project_id=proj_a.id)
    health_b = await log_health_service.compute_health(async_session, project_id=proj_b.id)
    assert health_a["total_events_24h"] == 1
    assert health_a["unknown_sha_count_24h"] == 0
    assert health_b["total_events_24h"] == 2
    assert health_b["unknown_sha_count_24h"] == 2
```

- [ ] **Step 3: 테스트 실행 — fail 확인**

```bash
cd backend && ./venv/bin/pytest tests/test_log_health_service.py -q --no-header 2>&1 | tail -10
```
Expected: 5 fail with `ImportError: cannot import name 'log_health_service'`.

- [ ] **Step 4: 구현 — service**

`backend/app/services/log_health_service.py` 생성:

```python
"""log-health 메트릭 계산.

설계서: 2026-04-26-error-log-design.md §7 Health 표.
"""

from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.log_event import LogEvent


_WINDOW = timedelta(hours=24)
_DRIFT_THRESHOLD = timedelta(hours=1)


async def compute_health(
    db: AsyncSession, *, project_id: UUID,
) -> dict[str, int | float]:
    """24h 윈도우 헬스 메트릭 계산.

    Returns dict (LogHealthResponse 와 동일 키):
      total_events_24h, unknown_sha_count_24h, unknown_sha_ratio_24h,
      clock_drift_count_24h.
    """
    now = datetime.utcnow()
    window_start = now - _WINDOW

    # 단일 SQL — 3 집계 동시.
    drift_seconds = _DRIFT_THRESHOLD.total_seconds()
    stmt = select(
        func.count().label("total"),
        func.count().filter(LogEvent.version_sha == "unknown").label("unknown"),
        func.count().filter(
            func.abs(
                func.extract("epoch", LogEvent.received_at - LogEvent.emitted_at)
            ) > drift_seconds
        ).label("drift"),
    ).where(
        LogEvent.project_id == project_id,
        LogEvent.received_at >= window_start,
    )

    row = (await db.execute(stmt)).one()
    total = int(row.total or 0)
    unknown = int(row.unknown or 0)
    drift = int(row.drift or 0)

    ratio = unknown / total if total > 0 else 0.0

    return {
        "total_events_24h": total,
        "unknown_sha_count_24h": unknown,
        "unknown_sha_ratio_24h": ratio,
        "clock_drift_count_24h": drift,
    }
```

- [ ] **Step 5: 테스트 실행 — pass 확인**

```bash
cd backend && ./venv/bin/pytest tests/test_log_health_service.py -q --no-header 2>&1 | tail -5
```
Expected: `5 passed`.

- [ ] **Step 6: Commit**

```bash
git add backend/app/schemas/log_health.py backend/app/services/log_health_service.py backend/tests/test_log_health_service.py
git commit -m "feat(error-log/phase5-backend): log_health_service.compute_health 24h 윈도우 (Task 5)"
```

---

## Task 6: GET /log-health endpoint + router 등록 + 통합 테스트

**Files:**
- Create: `backend/app/api/v1/endpoints/log_health.py`
- Modify: `backend/app/api/v1/router.py`
- Create: `backend/tests/test_log_health_endpoint.py`

- [ ] **Step 1: 실패 테스트 — endpoint 통합**

`tests/test_log_health_endpoint.py` 생성:

```python
"""GET /log-health 통합 테스트."""

import uuid
from datetime import datetime, timedelta

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


async def _seed(
    db: AsyncSession, *, role: WorkspaceRole = WorkspaceRole.VIEWER,
    n_known: int = 0, n_unknown: int = 0,
) -> tuple[User, Project]:
    user = User(email=f"u-{uuid.uuid4().hex[:8]}@x", name="u", password_hash="x")
    db.add(user)
    await db.flush()
    ws = Workspace(name="w", slug=f"w-{uuid.uuid4().hex[:8]}")
    db.add(ws)
    await db.flush()
    proj = Project(workspace_id=ws.id, name="p")
    db.add(proj)
    await db.flush()
    db.add(ProjectMember(project_id=proj.id, user_id=user.id, role=role))

    now = datetime.utcnow()
    for _ in range(n_known):
        db.add(LogEvent(
            project_id=proj.id, level=LogLevel.INFO, message="x",
            logger_name="l", version_sha="a" * 40, environment="prod",
            hostname="h", emitted_at=now, received_at=now,
        ))
    for _ in range(n_unknown):
        db.add(LogEvent(
            project_id=proj.id, level=LogLevel.INFO, message="x",
            logger_name="l", version_sha="unknown", environment="prod",
            hostname="h", emitted_at=now, received_at=now,
        ))
    await db.commit()
    await db.refresh(user)
    await db.refresh(proj)
    return user, proj


def _auth(user: User) -> dict[str, str]:
    from app.services.auth_service import create_access_token
    tok = create_access_token({"sub": str(user.id)})
    return {"Authorization": f"Bearer {tok}"}


async def test_health_viewer_can_read(client_with_db, async_session: AsyncSession):
    """log-health 는 모든 멤버 (VIEWER 포함) 읽기 가능 — 운영 투명성."""
    user, proj = await _seed(async_session, role=WorkspaceRole.VIEWER, n_known=3, n_unknown=1)
    resp = await client_with_db.get(
        f"/api/v1/projects/{proj.id}/log-health",
        headers=_auth(user),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_events_24h"] == 4
    assert body["unknown_sha_count_24h"] == 1
    assert abs(body["unknown_sha_ratio_24h"] - 0.25) < 1e-9


async def test_health_non_member_404(client_with_db, async_session: AsyncSession):
    user, proj = await _seed(async_session)
    other = User(email="o@x", name="o", password_hash="x")
    async_session.add(other)
    await async_session.commit()
    await async_session.refresh(other)

    resp = await client_with_db.get(
        f"/api/v1/projects/{proj.id}/log-health",
        headers=_auth(other),
    )
    assert resp.status_code == 404


async def test_health_empty_zero_ratio(client_with_db, async_session: AsyncSession):
    user, proj = await _seed(async_session)
    resp = await client_with_db.get(
        f"/api/v1/projects/{proj.id}/log-health",
        headers=_auth(user),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_events_24h"] == 0
    assert body["unknown_sha_ratio_24h"] == 0.0
```

- [ ] **Step 2: 테스트 실행 — fail 확인**

```bash
cd backend && ./venv/bin/pytest tests/test_log_health_endpoint.py -q --no-header 2>&1 | tail -10
```
Expected: 3 fail with 404 (라우터 미등록).

- [ ] **Step 3: endpoint 작성**

`backend/app/api/v1/endpoints/log_health.py` 생성:

```python
"""GET /log-health — unknown SHA 비율 + clock drift + 24h 송신량.

설계서: 2026-04-26-error-log-design.md §7 Health 표.
멤버 누구나 (VIEWER 포함, 운영 투명성).
"""

from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_project_member
from app.database import get_db
from app.models.workspace import WorkspaceRole
from app.schemas.log_health import LogHealthResponse
from app.services import log_health_service

router = APIRouter(prefix="/projects", tags=["log-health"])


@router.get(
    "/{project_id}/log-health",
    response_model=LogHealthResponse,
)
async def get_log_health(
    project_id: UUID,
    db: AsyncSession = Depends(get_db),
    _role: WorkspaceRole = Depends(require_project_member(hide_existence=True)),
):
    """24h 윈도우 LogEvent 헬스 메트릭. 멤버 누구나."""
    metrics = await log_health_service.compute_health(db, project_id=project_id)
    return LogHealthResponse(**metrics)
```

- [ ] **Step 4: router 등록**

`backend/app/api/v1/router.py` 수정:

import 추가:
```python
from app.api.v1.endpoints.log_health import router as log_health_router
```

include 추가 (다른 log_* 와 인접 위치):
```python
api_v1_router.include_router(log_health_router)
```

- [ ] **Step 5: 테스트 실행 — pass 확인**

```bash
cd backend && ./venv/bin/pytest tests/test_log_health_endpoint.py -q --no-header 2>&1 | tail -5
```
Expected: `3 passed`.

전체 회귀:

```bash
cd backend && ./venv/bin/pytest -q --no-header 2>&1 | tail -5
```
Expected: 275 (기존) + 5 (Task 2) + 7 (Task 3) + 5 (Task 4) + 5 (Task 5) + 3 (Task 6) = **300 passed**.

(parametrize 가 늘어날 수 있어 실측 fluctuation ±5 허용; 핵심은 모든 기존 테스트 회귀 0.)

- [ ] **Step 6: Commit**

```bash
git add backend/app/api/v1/endpoints/log_health.py backend/app/api/v1/router.py backend/tests/test_log_health_endpoint.py
git commit -m "feat(error-log/phase5-backend): GET /log-health endpoint (Task 6)"
```

---

## Task 7: PR + handoff 업데이트

- [ ] **Step 1: 최종 회귀 한 번 더**

```bash
cd backend && ./venv/bin/pytest -q --no-header 2>&1 | tail -5
```
Expected: 모두 통과.

- [ ] **Step 2: handoffs/main.md 헤드 업데이트**

`handoffs/main.md` 파일 최상단에 새 entry 추가 (기존 entry 들 위에):

```markdown
## 2026-05-01 (저녁) — Error-log Phase 5 Backend

- [x] PATCH /errors/{group_id} — status 전이 (resolve/ignore/reopen/unmute), OWNER 전용, action 기반 (status 직접 X)
- [x] GET /log-tokens — 토큰 목록, OWNER 전용, secret 절대 비노출, include_revoked 필터
- [x] GET /log-health — unknown SHA 비율 + clock drift + 24h 송신량, 멤버 누구나 (VIEWER 포함)
- [x] error_group_service.transition_status — _LEGAL_TRANSITIONS 매트릭스 + audit 필드 (resolved_at / resolved_by / resolved_in_version_sha) 자동 채움/클리어
- [x] log_health_service.compute_health — 단일 SQL 3 집계 (total / unknown / drift)

### 다음 (Phase 5 Frontend Errors — sub-phase 2)

- [ ] ErrorsPage / ErrorDetailPage / GitContextPanel 등 — 별도 PR
- [ ] PATCH endpoint 사용한 status 전이 UI (resolve/ignore/reopen 버튼)
- [ ] LogHealthBadge — 헤더의 ⚠️ 표시 (unknown_sha_ratio_24h > 0.05 시)
```

- [ ] **Step 3: branch push + PR 생성**

```bash
git add handoffs/main.md
git commit -m "docs(handoff): Error-log Phase 5 Backend 완료 + 다음 sub-phase 안내"
git push -u origin feature/error-log-phase5-backend
```

PR body:

```markdown
## Summary

Phase 5 UI 가 사용할 backend endpoint 3개 추가. UI 본편 (ErrorsPage/ErrorDetailPage 등) 은 다음 sub-phase PR.

| Endpoint | 용도 | 권한 |
|---|---|---|
| `PATCH /api/v1/projects/{id}/errors/{group_id}` | ErrorGroup status 전이 (resolve/ignore/reopen/unmute) | OWNER |
| `GET /api/v1/projects/{id}/log-tokens` | LogIngestToken 목록 (secret 미노출) | OWNER |
| `GET /api/v1/projects/{id}/log-health` | unknown SHA 비율 + clock drift + 24h 총량 | 멤버 (VIEWER 포함) |

## Spec

설계서: `docs/superpowers/specs/2026-04-26-error-log-design.md` §4.1 (status 다이어그램), §5.2 (API 목록), §7 (health 표).

## 핵심 구현 결정

- **action 기반 PATCH**: 사용자가 status 를 직접 보낼 수 없음 (Pydantic Literal `"resolve"|"ignore"|"reopen"|"unmute"` 만 허용). RESOLVED 같은 final state 우연히 보낼 위험 회피.
- **`_LEGAL_TRANSITIONS` 매트릭스**: 합법 전이 dict. 불법 전이 → ValueError → 400. 불일치 (이미 같은 status) 도 unsupported 로 묶어 reject.
- **audit 필드 자동 관리**: RESOLVED 진입 시 resolved_at/resolved_by/resolved_in_version_sha 채움, OPEN reopen 시 모두 NULL.
- **secret 절대 비노출**: `LogTokenSummary` schema 에 `secret_hash` 미정의 → FastAPI response_model 이 강제 필터.
- **log-health 권한 = 멤버 누구나**: 운영 투명성. log query API (Phase 4) 와 동일 정책.
- **24h 윈도우 단일 SQL**: `func.count().filter(...)` 로 3 집계 (total / unknown / drift) 한 번에. clock drift = abs(received - emitted) > 1h.

## 테스트

- 새 unit + 통합 테스트 ~25개 (전이 매트릭스 parametrize 11 + 권한 경로 + extra='forbid' + secret 누설 방지 + 24h 윈도우 컷오프).
- 회귀: 기존 275 테스트 통과 그대로.

## Test plan

- [ ] PATCH 합법 전이 6종 + 불법 전이 5종 + audit 필드 set/clear
- [ ] PATCH OWNER 권한 / 비-멤버 404 / extra 필드 거부
- [ ] GET /log-tokens active-only default + include_revoked + secret 비노출 + OWNER 권한
- [ ] GET /log-health VIEWER 가능 + 비-멤버 404 + 빈 프로젝트 zero ratio
- [ ] 전체 backend pytest 통과
```

- [ ] **Step 4: review + 머지 후 cleanup**

PR 머지 후:

```bash
# parent 에서
cd /Users/arden/Documents/ardensdevspace/pslog
git checkout main
git pull --ff-only
git worktree remove .worktrees/error-log-phase5-backend
git branch -d feature/error-log-phase5-backend
```

---

## Self-review checklist (writer 자체)

- [x] 모든 spec 항목 (PATCH errors / GET log-tokens / GET log-health) coverage 있음
- [x] placeholder ("TBD" / "implement later") 없음
- [x] 타입 일관성 — `ErrorGroupStatus` enum 만 사용, `transition_status` 시그니처 호출부와 일치
- [x] 모든 Step 에 actual code or actual command
- [x] 모든 task 끝에 commit step
- [x] 작은 task 5개 + PR 1개 = 6 commit, mid-PR 회귀 가능
