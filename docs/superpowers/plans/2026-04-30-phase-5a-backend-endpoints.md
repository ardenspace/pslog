# Phase 5a — Backend Endpoints + 자동 Webhook 등록 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Phase 5 의 backend 절반 — 프로젝트별 git 설정 조회/수정 endpoint, 자동 webhook 등록 (GitHub API 호출 + secret 자동 생성), handoff 이력 조회, sync 실패 이벤트 수동 재처리, `GitPushEvent.before_commit_sha` 컬럼 추가 (commits_truncated base 정확화). Phase 5b 의 frontend (`ProjectGitSettings.tsx` 등) 가 호출할 API 가 모두 본 phase 에서 갖춰짐.

**Architecture:** 기존 `discord.py` endpoint 패턴 그대로 — `prefix="/projects"` 의 별도 router 파일 `git_settings.py`. 권한은 기존 `permission_service.get_effective_role` + `can_manage` (OWNER 전용 — PAT/webhook 같은 민감 설정) 재사용. 자동 webhook 등록은 `git_repo_service` 에 신규 함수 (Phase 4 의 `fetch_file/fetch_compare_files` 와 같은 위치 — GitHub API 호출 한 곳에 모음). `reprocess` endpoint 는 BackgroundTask 로 sync_service.process_event 재호출 (Phase 4 webhook endpoint 패턴 재사용).

**Tech Stack:** FastAPI 0.109, SQLAlchemy 2.0.25 async, Pydantic v2, httpx 0.26 (GitHub API), Phase 2 crypto (Fernet), Phase 4 sync_service. 외부 의존 추가 없음.

**선행 조건:**
- pslog main, alembic head = `274c0ed55105` (Phase 4 머지 완료, PR #10)
- Phase 2 crypto + Phase 4 git_repo_service / sync_service 정상 동작
- Python 3.12.13 venv (`backend/venv`), `.env` 의 `pslog_FERNET_KEY` 존재
- 137 tests baseline (Phase 1+2+3+4)

**중요한 계약:**

- **권한 (설계서 §9 + 기존 pslog 패턴)**:
  - GET `/git-settings`, GET `/handoffs` → Project 멤버 누구나 (`get_effective_role` 결과 not None)
  - PATCH `/git-settings`, POST `/git-settings/webhook`, POST `/git-events/{id}/reprocess` → **OWNER 전용** (`can_manage`). PAT / secret / sync 트리거는 민감.
- **`pslog_PUBLIC_URL` 환경변수 추가**: webhook 등록 시 GitHub 가 callback 할 pslog 의 외부 URL (e.g. `https://pslog.example.com`). `config.py` 에 필드 추가, `.env` 에 명시. webhook url = `f"{pslog_PUBLIC_URL}/api/v1/webhooks/github"`.
- **`GitPushEvent.before_commit_sha` 컬럼 추가**:
  - 새 alembic revision: 컬럼 + CHECK 제약 (40자 hex full or NULL) — `Task.last_commit_sha` 와 같은 패턴
  - `record_push_event` 가 `payload.before` 를 저장
  - `sync_service._collect_changed_files` 가 `event.before_commit_sha` 우선 사용 (있으면), 없으면 기존 fallback
  - 기존 데이터 (Phase 2/4 기간의 GitPushEvent) 는 NULL — fallback path 그대로 동작
- **자동 webhook 등록 흐름**:
  1. Project 의 `git_repo_url` + `github_pat_encrypted` 둘 다 있어야 — 없으면 400
  2. PAT decrypt (실패 시 500)
  3. GitHub `GET /repos/{owner}/{repo}/hooks` 로 기존 webhook 조회
  4. pslog callback url (= `f"{pslog_PUBLIC_URL}/api/v1/webhooks/github"`) 와 같은 url 의 hook 검색
  5. 새 secret 생성 (`generate_webhook_secret()`) → encrypt → `Project.webhook_secret_encrypted` 저장
  6. 기존 hook 있으면 `PATCH /hooks/{id}` (config.secret 업데이트), 없으면 `POST /hooks`
  7. 응답: `{webhook_id, registered: true, was_existing: bool}`
  - 본 phase 는 webhook 등록 후 수신 endpoint 가 이미 동작 (Phase 2). e2e 검증은 사용자 환경에서.
- **재처리 (`POST /git-events/{id}/reprocess`)**:
  - 대상 event 의 `processed_at` 가 None 이거나 `error` 가 set 이어야 함 — 정상 처리 완료된 event 는 400
  - `processed_at = None`, `error = None` 으로 reset → BackgroundTask 로 sync_service.process_event 호출 (Phase 4 webhook endpoint 의 `_run_sync_in_new_session` 재사용)
- **handoff 이력 조회 (`GET /handoffs`)**:
  - query param: `branch` (선택, 없으면 전체), `limit` (기본 50, 최대 200), `before_commit_sha` (선택, 페이지네이션)
  - response: `[{id, branch, author_git_login, commit_sha, pushed_at, parsed_tasks_count}]` — `raw_content` / `free_notes` / `parsed_tasks` 본체는 별도 detail endpoint (Phase 5 후속 또는 Phase 6 — 현재는 list 만)
  - 정렬: `pushed_at DESC`
- **에러 정책**:
  - 404: project 미존재 또는 멤버 아님
  - 403: 권한 부족 (OWNER 가 아닌 사용자가 PATCH/POST 호출)
  - 400: 검증 실패 (예: webhook 등록인데 PAT/repo_url 미설정, reprocess 인데 이미 처리 성공)
  - 500: GitHub API 5xx, Fernet 복호화 실패, DB 오류

---

## File Structure

**신규 파일 (소스):**
- `backend/alembic/versions/<auto>_phase5a_before_commit_sha.py` — `GitPushEvent.before_commit_sha` 컬럼 + CHECK
- `backend/app/api/v1/endpoints/git_settings.py` — 5개 endpoint (GET/PATCH git-settings, POST webhook, GET handoffs, POST reprocess)
- `backend/app/schemas/git_settings.py` — Pydantic 요청/응답 모델
- `backend/app/services/github_hook_service.py` — GitHub Hooks API (`list_hooks`, `create_or_update_hook`) — git_repo_service 와 분리 (책임 분리)

**신규 파일 (테스트):**
- `backend/tests/test_phase5a_migration.py` — `before_commit_sha` 컬럼 회귀
- `backend/tests/test_git_settings_endpoint.py` — 5개 endpoint e2e + 권한
- `backend/tests/test_github_hook_service.py` — Hooks API 단위 (httpx mock)
- `backend/tests/fixtures/github_hooks_payload.json` — `GET /hooks` 응답 샘플

**수정 파일:**
- `backend/app/models/git_push_event.py` — `before_commit_sha: bytes | None` 필드
- `backend/app/services/github_webhook_service.py` — `record_push_event` 가 `payload.before` 저장
- `backend/app/services/sync_service.py` — `_collect_changed_files` 가 `event.before_commit_sha` 우선 사용
- `backend/app/schemas/webhook.py` — `GitHubPushPayload.before` 이미 존재 — 변경 없음
- `backend/app/config.py` — `pslog_public_url: str` 필드 추가
- `backend/app/api/v1/router.py` — `git_settings_router` 추가
- `backend/app/api/v1/endpoints/webhooks.py` — `_run_sync_in_new_session` 을 export 가능 형태로 (또는 git_settings.py 에서 재정의 — 단순화)
- `backend/tests/test_github_webhook_service.py` — `before_commit_sha` 검증 1건 추가
- `backend/tests/test_sync_service.py` — `event.before_commit_sha` 우선 사용 검증 1건 추가
- `.env` — `pslog_PUBLIC_URL` 추가

---

## Self-Review Notes

작성 후 self-review 항목:
- 설계서 §5.2 신규 endpoint 6항목 → 본 phase 5개 (Brief 는 Phase 7) 매핑
- 설계서 §9 권한 → `can_manage` (OWNER) 일관 적용
- 설계서 §11 Phase 5 정의 — UI 외 자동 webhook 등록 부분 → Task 5
- handoff 메모 (Phase 4): `before_commit_sha` 컬럼 추가 → Task 1, 2
- 기존 패턴 일관성: discord.py / projects.py 의 router prefix + permission 호출 패턴 따름

---

## Task 0: 브랜치 + venv 검증

- [ ] **Step 1: 브랜치 생성**

```bash
cd /Users/arden/Documents/ardensdevspace/pslog
git checkout main && git pull --ff-only origin main
git checkout -b feature/phase-5a-backend-endpoints
```

Expected: main HEAD = `44590c6` (Phase 4 머지).

- [ ] **Step 2: venv + .env 검증**

```bash
cd backend
source venv/bin/activate
python -c "from app.config import settings; print(bool(settings.pslog_fernet_key))"
pytest -q 2>&1 | tail -3
```

Expected: `True`, 137 tests pass.

---

## Task 1: alembic — `GitPushEvent.before_commit_sha` 컬럼

**Files:**
- Create: `backend/alembic/versions/<auto>_phase5a_before_commit_sha.py`
- Modify: `backend/app/models/git_push_event.py`
- Create: `backend/tests/test_phase5a_migration.py`

- [ ] **Step 1: 모델에 필드 추가**

`backend/app/models/git_push_event.py` — 기존 `head_commit_sha` 다음에 추가:

```python
    # Phase 5a — commits_truncated base 정확화 (webhook payload 의 `before` 필드 보존)
    before_commit_sha: Mapped[str | None] = mapped_column(default=None)
```

CHECK 제약은 alembic 에서 별도 추가 (40자 hex 또는 NULL).

- [ ] **Step 2: alembic autogenerate**

```bash
cd /Users/arden/Documents/ardensdevspace/pslog/backend
source venv/bin/activate
alembic revision --autogenerate -m "phase5a: gitpushevent before_commit_sha column"
```

생성된 파일을 다음으로 보정 (autogen 이 CHECK 제약 빼먹을 수 있음):

```python
def upgrade() -> None:
    op.add_column('git_push_events', sa.Column('before_commit_sha', sa.String(), nullable=True))
    op.create_check_constraint(
        'ck_git_push_event_before_sha_hex',
        'git_push_events',
        "before_commit_sha IS NULL OR before_commit_sha ~ '^[0-9a-f]{40}$'",
    )


def downgrade() -> None:
    op.drop_constraint('ck_git_push_event_before_sha_hex', 'git_push_events', type_='check')
    op.drop_column('git_push_events', 'before_commit_sha')
```

`down_revision` 은 자동으로 `274c0ed55105` (Phase 4 head) 이어야 함.

- [ ] **Step 3: 회귀 테스트 작성**

Create `backend/tests/test_phase5a_migration.py`:

```python
"""Phase 5a 마이그레이션 회귀 — GitPushEvent.before_commit_sha 컬럼."""

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.git_push_event import GitPushEvent
from app.models.project import Project
from app.models.workspace import Workspace


async def _seed_project(db: AsyncSession) -> Project:
    ws = Workspace(name="ws", slug=f"ws-{uuid.uuid4().hex[:8]}")
    db.add(ws)
    await db.flush()
    proj = Project(workspace_id=ws.id, name="p")
    db.add(proj)
    await db.commit()
    await db.refresh(proj)
    return proj


async def test_existing_events_have_null_before_sha(async_session: AsyncSession):
    """기존 GitPushEvent 데이터는 NULL 보존."""
    proj = await _seed_project(async_session)
    event = GitPushEvent(
        project_id=proj.id,
        branch="main",
        head_commit_sha="a" * 40,
        commits=[],
        commits_truncated=False,
        pusher="alice",
    )
    async_session.add(event)
    await async_session.commit()
    await async_session.refresh(event)
    assert event.before_commit_sha is None


async def test_before_sha_accepts_40_char_hex(async_session: AsyncSession):
    proj = await _seed_project(async_session)
    event = GitPushEvent(
        project_id=proj.id,
        branch="main",
        head_commit_sha="a" * 40,
        before_commit_sha="b" * 40,
        commits=[],
        commits_truncated=False,
        pusher="alice",
    )
    async_session.add(event)
    await async_session.commit()
    await async_session.refresh(event)
    assert event.before_commit_sha == "b" * 40


async def test_before_sha_check_rejects_invalid_hex(async_session: AsyncSession):
    """CHECK 제약 — non-hex 또는 길이 != 40 reject."""
    proj = await _seed_project(async_session)
    event = GitPushEvent(
        project_id=proj.id,
        branch="main",
        head_commit_sha="a" * 40,
        before_commit_sha="not-hex-shasha",  # 잘못된 형식
        commits=[],
        commits_truncated=False,
        pusher="alice",
    )
    async_session.add(event)
    with pytest.raises(IntegrityError):
        await async_session.commit()


async def test_migration_added_column_to_git_push_events_table(async_session: AsyncSession):
    result = await async_session.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'git_push_events' AND column_name = 'before_commit_sha'"
        )
    )
    row = result.first()
    assert row is not None
    assert row[0] == "before_commit_sha"
```

- [ ] **Step 4: 회귀 실행**

```bash
pytest tests/test_phase5a_migration.py -v
pytest tests/test_git_push_event_model.py -v 2>&1 || true
pytest -q
```

Expected: 4 신규 tests pass, 기존 137 무회귀 → 141 total.

- [ ] **Step 5: Commit**

```bash
git add backend/alembic/versions/*phase5a* backend/app/models/git_push_event.py backend/tests/test_phase5a_migration.py
git commit -m "feat(phase5a): GitPushEvent.before_commit_sha 컬럼 + CHECK + 회귀 테스트"
```

---

## Task 2: webhook record + sync_service 가 `before_commit_sha` 활용

**Files:**
- Modify: `backend/app/services/github_webhook_service.py` — `record_push_event` 가 `payload.before` 저장
- Modify: `backend/app/services/sync_service.py` — `_collect_changed_files` 가 `event.before_commit_sha` 우선
- Modify: `backend/tests/test_github_webhook_service.py` — 검증 추가
- Modify: `backend/tests/test_sync_service.py` — 검증 추가

- [ ] **Step 1: github_webhook_service 수정**

`backend/app/services/github_webhook_service.py` 의 `record_push_event` 안에서 `GitPushEvent(...)` 생성 부분에 `before_commit_sha` 추가:

```python
    event = GitPushEvent(
        project_id=project.id,
        branch=payload.branch,
        head_commit_sha=payload.head_commit.id,
        before_commit_sha=payload.before if payload.before and len(payload.before) == 40 else None,
        commits=payload.to_commits_json(),
        commits_truncated=len(payload.commits) >= GITHUB_WEBHOOK_COMMITS_CAP,
        pusher=payload.pusher.name,
    )
```

(`payload.before` 가 `0000...0000` (force-push initial push) 일 수 있음. 길이는 40 이지만 hex `0` 만이라 CHECK 통과. 정상 push 는 정상 sha. 별도 분기 없이 그대로 저장.)

수정: `0` 만 40자 도 hex 이므로 CHECK 통과. 즉 위 `len == 40` 체크면 충분.

- [ ] **Step 2: github_webhook_service 회귀 테스트 추가**

`backend/tests/test_github_webhook_service.py` 끝에 추가:

```python
async def test_record_push_event_stores_before_commit_sha(async_session: AsyncSession):
    proj = await _seed_workspace_with_project(
        async_session, "https://github.com/ardenspace/app-chak"
    )
    payload = _payload(head_id="a" * 40)
    # _payload helper 가 만든 raw 의 before 는 fixture 의 'before' (40 zeros)
    event = await record_push_event(async_session, proj, payload)
    assert event is not None
    assert event.before_commit_sha == payload.before
    assert event.before_commit_sha == "0" * 40  # fixture 의 값
```

(`_payload` 와 `_seed_workspace_with_project` 는 이미 정의됨 — 같은 파일에서 재사용.)

- [ ] **Step 3: sync_service `_collect_changed_files` 수정**

`backend/app/services/sync_service.py` 의 `_collect_changed_files` 안의 base 결정 로직을 다음으로 교체 (truncated 분기 부분):

```python
    base = event.before_commit_sha
    if base is None:
        base = project.last_synced_commit_sha
    if base is None and event.commits:
        base = event.commits[-1].get("id") or event.head_commit_sha
    if base is None:
        base = event.head_commit_sha
```

(우선순위: `event.before_commit_sha` → `project.last_synced_commit_sha` → `commits[-1].id` → `head_commit_sha`. 첫 번째가 가장 정확.)

- [ ] **Step 4: sync_service 회귀 테스트 추가**

`backend/tests/test_sync_service.py` 끝에 추가:

```python
async def test_collect_changed_files_uses_before_sha_when_truncated(async_session: AsyncSession):
    """commits_truncated 시 before_commit_sha 가 base 로 사용됨 (Phase 5a 보강)."""
    proj = await _seed_project(async_session)
    captured: dict[str, str] = {}

    async def fake_compare(repo_url, pat, base, head):
        captured["base"] = base
        captured["head"] = head
        return ["PLAN.md"]

    async def fake_fetch_file(repo_url, pat, sha, path):
        return None  # PLAN 변경됐다고만 알려주고 fetch 는 404 → silent skip

    event = await _seed_event(
        async_session, proj,
        head_sha="b" * 40,
        commits_truncated=True,
        commits=[{"id": "c" * 40, "modified": []}],  # commits[-1] 은 "c"*40
    )
    event.before_commit_sha = "a" * 40
    await async_session.commit()
    await async_session.refresh(event)

    await process_event(
        async_session, event,
        fetch_file=fake_fetch_file, fetch_compare=fake_compare,
    )

    assert captured["base"] == "a" * 40  # before_commit_sha 우선
    assert captured["head"] == "b" * 40
```

- [ ] **Step 5: 실행**

```bash
pytest tests/test_github_webhook_service.py tests/test_sync_service.py -v
pytest -q
```

Expected: 신규 2건 pass, 142+ tests total, 회귀 0.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/github_webhook_service.py \
        backend/app/services/sync_service.py \
        backend/tests/test_github_webhook_service.py \
        backend/tests/test_sync_service.py
git commit -m "feat(phase5a): record_push_event 가 before_commit_sha 저장, sync 가 base 우선 사용"
```

---

## Task 3: GET `/git-settings` endpoint

**Files:**
- Create: `backend/app/api/v1/endpoints/git_settings.py`
- Create: `backend/app/schemas/git_settings.py`
- Modify: `backend/app/api/v1/router.py`
- Create: `backend/tests/test_git_settings_endpoint.py`

- [ ] **Step 1: 스키마 작성**

Create `backend/app/schemas/git_settings.py`:

```python
"""git-settings endpoint Pydantic 스키마.

설계서: 2026-04-26-ai-task-automation-design.md §5.2
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class GitSettingsResponse(BaseModel):
    """GET /git-settings — PAT 평문은 절대 응답에 포함 안 함."""

    model_config = ConfigDict(from_attributes=True)

    git_repo_url: str | None
    git_default_branch: str
    plan_path: str
    handoff_dir: str
    last_synced_commit_sha: str | None
    has_webhook_secret: bool        # 평문 노출 안 함 — 등록 여부만
    has_github_pat: bool
    public_webhook_url: str         # pslog 측 callback url (사용자가 GitHub UI 에서 수동 등록 시 필요)


class GitSettingsUpdate(BaseModel):
    """PATCH /git-settings — 모든 필드 optional (부분 갱신)."""

    model_config = ConfigDict(extra="forbid")

    git_repo_url: str | None = None
    git_default_branch: str | None = None
    plan_path: str | None = None
    handoff_dir: str | None = None
    github_pat: str | None = Field(default=None, min_length=1)  # 평문 입력 — 즉시 Fernet encrypt


class WebhookRegisterResponse(BaseModel):
    """POST /git-settings/webhook"""

    model_config = ConfigDict(extra="forbid")

    webhook_id: int
    was_existing: bool
    public_webhook_url: str


class HandoffSummary(BaseModel):
    """GET /handoffs 의 항목 — raw_content / parsed_tasks / free_notes 본체 제외 (목록용)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    branch: str
    author_git_login: str
    commit_sha: str
    pushed_at: datetime
    parsed_tasks_count: int = 0


class ReprocessResponse(BaseModel):
    """POST /git-events/{id}/reprocess"""

    event_id: UUID
    status: str  # "queued"
```

- [ ] **Step 2: config 에 pslog_public_url 추가**

`backend/app/config.py` 의 `Settings` 클래스에 추가:

```python
    # Phase 5a — webhook callback URL (GitHub 가 호출할 외부 URL, e.g. Cloudflare Tunnel)
    pslog_public_url: str = "http://localhost:8000"
```

기본값이 있으므로 `.env` 변경 없이도 import 정상 (단 자동 webhook 등록은 localhost 로 등록되므로 prod 에선 명시 필수).

- [ ] **Step 3: Failing endpoint 테스트 작성**

Create `backend/tests/test_git_settings_endpoint.py`:

```python
"""git-settings endpoint e2e 테스트.

설계서: 2026-04-26-ai-task-automation-design.md §5.2, §9
"""

import uuid
from datetime import datetime

import pytest
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import encrypt_secret
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
    """기존 pslog 패턴 — JWT 발급."""
    from app.services.auth_service import create_access_token

    return create_access_token({"sub": str(user.id)})


async def test_get_git_settings_returns_current_state(
    client_with_db, async_session: AsyncSession
):
    user, proj = await _seed_user_project(async_session)
    token = _auth_token(user)
    res = await client_with_db.get(
        f"/api/v1/projects/{proj.id}/git-settings",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["git_repo_url"] is None
    assert body["plan_path"] == "PLAN.md"
    assert body["handoff_dir"] == "handoffs/"
    assert body["has_webhook_secret"] is False
    assert body["has_github_pat"] is False
    assert "public_webhook_url" in body


async def test_get_git_settings_404_for_non_member(
    client_with_db, async_session: AsyncSession
):
    """프로젝트 멤버 아니면 404."""
    user, proj = await _seed_user_project(async_session)

    # 다른 user 만들고 ProjectMember 등록 안 함
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
        f"/api/v1/projects/{proj.id}/git-settings",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 404


async def test_get_git_settings_redacts_secrets(
    client_with_db, async_session: AsyncSession
):
    """webhook_secret_encrypted / github_pat_encrypted 평문은 절대 응답 안 됨."""
    user, proj = await _seed_user_project(async_session)
    proj.github_pat_encrypted = encrypt_secret("ghp_super_secret_token")
    proj.webhook_secret_encrypted = encrypt_secret("super-shared-secret")
    await async_session.commit()
    await async_session.refresh(proj)

    token = _auth_token(user)
    res = await client_with_db.get(
        f"/api/v1/projects/{proj.id}/git-settings",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["has_github_pat"] is True
    assert body["has_webhook_secret"] is True
    # 어떤 secret 도 응답에 없어야
    assert "github_pat" not in body
    assert "github_pat_encrypted" not in body
    assert "webhook_secret" not in body
    assert "webhook_secret_encrypted" not in body
    raw_text = res.text
    assert "ghp_super_secret_token" not in raw_text
    assert "super-shared-secret" not in raw_text
```

- [ ] **Step 4: Run — 실패 (404, endpoint 없음)**

```bash
pytest tests/test_git_settings_endpoint.py -v
```

- [ ] **Step 5: endpoint 작성**

Create `backend/app/api/v1/endpoints/git_settings.py`:

```python
"""git-settings endpoints — 프로젝트별 git 연동 설정 조회/수정.

설계서: 2026-04-26-ai-task-automation-design.md §5.2, §9

- GET    /projects/{id}/git-settings           — 현재 설정 (멤버)
- PATCH  /projects/{id}/git-settings           — 설정 수정 (OWNER, Task 4)
- POST   /projects/{id}/git-settings/webhook   — GitHub webhook 자동 등록 (OWNER, Task 5)
- GET    /projects/{id}/handoffs               — handoff 이력 (멤버, Task 6)
- POST   /projects/{id}/git-events/{id}/reprocess — 수동 재처리 (OWNER, Task 7)
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.dependencies import CurrentUser
from app.schemas.git_settings import GitSettingsResponse
from app.services import project_service
from app.services.permission_service import get_effective_role

router = APIRouter(prefix="/projects", tags=["git-settings"])


def _public_webhook_url() -> str:
    base = settings.pslog_public_url.rstrip("/")
    return f"{base}/api/v1/webhooks/github"


@router.get("/{project_id}/git-settings", response_model=GitSettingsResponse)
async def get_git_settings(
    project_id: UUID,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
):
    project = await project_service.get_project(db, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    role = await get_effective_role(db, user.id, project_id)
    if role is None:
        raise HTTPException(status_code=404, detail="Project not found")

    return GitSettingsResponse(
        git_repo_url=project.git_repo_url,
        git_default_branch=project.git_default_branch,
        plan_path=project.plan_path,
        handoff_dir=project.handoff_dir,
        last_synced_commit_sha=project.last_synced_commit_sha,
        has_webhook_secret=project.webhook_secret_encrypted is not None,
        has_github_pat=project.github_pat_encrypted is not None,
        public_webhook_url=_public_webhook_url(),
    )
```

- [ ] **Step 6: router 에 등록**

`backend/app/api/v1/router.py` 에 import + include 추가:

```python
from app.api.v1.endpoints.git_settings import router as git_settings_router
# ...
api_v1_router.include_router(git_settings_router)
```

- [ ] **Step 7: Run — pass (3 tests)**

```bash
pytest tests/test_git_settings_endpoint.py -v
```

- [ ] **Step 8: Commit**

```bash
git add backend/app/api/v1/endpoints/git_settings.py \
        backend/app/schemas/git_settings.py \
        backend/app/api/v1/router.py \
        backend/app/config.py \
        backend/tests/test_git_settings_endpoint.py
git commit -m "feat(phase5a): GET /git-settings + 스키마 + pslog_public_url 설정"
```

---

## Task 4: PATCH `/git-settings` endpoint

**Files:**
- Modify: `backend/app/api/v1/endpoints/git_settings.py`
- Modify: `backend/tests/test_git_settings_endpoint.py`

- [ ] **Step 1: PATCH 테스트 추가**

`backend/tests/test_git_settings_endpoint.py` 끝에 추가:

```python
async def test_patch_git_settings_owner_can_update(
    client_with_db, async_session: AsyncSession
):
    user, proj = await _seed_user_project(async_session)
    token = _auth_token(user)
    res = await client_with_db.patch(
        f"/api/v1/projects/{proj.id}/git-settings",
        json={
            "git_repo_url": "https://github.com/ardenspace/app-chak",
            "plan_path": "docs/PLAN.md",
            "github_pat": "ghp_new_token_value",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["git_repo_url"] == "https://github.com/ardenspace/app-chak"
    assert body["plan_path"] == "docs/PLAN.md"
    assert body["has_github_pat"] is True
    # PAT 평문은 응답에 없음
    assert "ghp_new_token_value" not in res.text

    # DB 확인 — PAT 가 Fernet encrypt 된 상태로 저장
    await async_session.refresh(proj)
    assert proj.github_pat_encrypted is not None
    from app.core.crypto import decrypt_secret
    assert decrypt_secret(proj.github_pat_encrypted) == "ghp_new_token_value"


async def test_patch_git_settings_partial_update_preserves_others(
    client_with_db, async_session: AsyncSession
):
    """body 에 없는 필드는 보존."""
    user, proj = await _seed_user_project(async_session)
    proj.git_repo_url = "https://github.com/old/repo"
    proj.plan_path = "PLAN.md"
    await async_session.commit()

    token = _auth_token(user)
    res = await client_with_db.patch(
        f"/api/v1/projects/{proj.id}/git-settings",
        json={"plan_path": "docs/PLAN.md"},  # only plan_path
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200
    await async_session.refresh(proj)
    assert proj.git_repo_url == "https://github.com/old/repo"
    assert proj.plan_path == "docs/PLAN.md"


async def test_patch_git_settings_403_for_non_owner(
    client_with_db, async_session: AsyncSession
):
    """OWNER 가 아닌 멤버 (EDITOR) 는 PATCH 403."""
    user, proj = await _seed_user_project(async_session, role=WorkspaceRole.EDITOR)
    token = _auth_token(user)
    res = await client_with_db.patch(
        f"/api/v1/projects/{proj.id}/git-settings",
        json={"plan_path": "x.md"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 403


async def test_patch_git_settings_404_for_non_member(
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
    res = await client_with_db.patch(
        f"/api/v1/projects/{proj.id}/git-settings",
        json={"plan_path": "x.md"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 404
```

- [ ] **Step 2: PATCH endpoint 추가**

`backend/app/api/v1/endpoints/git_settings.py` 끝에 추가:

```python
from app.core.crypto import encrypt_secret
from app.models.workspace import WorkspaceRole
from app.schemas.git_settings import GitSettingsUpdate
from app.services.permission_service import can_manage


@router.patch("/{project_id}/git-settings", response_model=GitSettingsResponse)
async def patch_git_settings(
    project_id: UUID,
    update: GitSettingsUpdate,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
):
    project = await project_service.get_project(db, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    role = await get_effective_role(db, user.id, project_id)
    if role is None:
        raise HTTPException(status_code=404, detail="Project not found")
    if not can_manage(role):
        raise HTTPException(status_code=403, detail="Owner only")

    # 부분 갱신 — body 에 명시된 필드만
    data = update.model_dump(exclude_unset=True)
    for key in ("git_repo_url", "git_default_branch", "plan_path", "handoff_dir"):
        if key in data:
            setattr(project, key, data[key])
    if "github_pat" in data and data["github_pat"]:
        project.github_pat_encrypted = encrypt_secret(data["github_pat"])

    await db.commit()
    await db.refresh(project)

    return GitSettingsResponse(
        git_repo_url=project.git_repo_url,
        git_default_branch=project.git_default_branch,
        plan_path=project.plan_path,
        handoff_dir=project.handoff_dir,
        last_synced_commit_sha=project.last_synced_commit_sha,
        has_webhook_secret=project.webhook_secret_encrypted is not None,
        has_github_pat=project.github_pat_encrypted is not None,
        public_webhook_url=_public_webhook_url(),
    )
```

- [ ] **Step 3: Run — pass (7 tests)**

```bash
pytest tests/test_git_settings_endpoint.py -v
```

Expected: 3 기존 + 4 신규 = 7 pass.

- [ ] **Step 4: Commit**

```bash
git add backend/app/api/v1/endpoints/git_settings.py backend/tests/test_git_settings_endpoint.py
git commit -m "feat(phase5a): PATCH /git-settings (OWNER, PAT Fernet encrypt 저장)"
```

---

## Task 5: 자동 webhook 등록 — `github_hook_service` + POST endpoint

**Files:**
- Create: `backend/app/services/github_hook_service.py`
- Create: `backend/tests/test_github_hook_service.py`
- Create: `backend/tests/fixtures/github_hooks_payload.json`
- Modify: `backend/app/api/v1/endpoints/git_settings.py`
- Modify: `backend/tests/test_git_settings_endpoint.py`

- [ ] **Step 1: GET hooks 응답 fixture**

Create `backend/tests/fixtures/github_hooks_payload.json`:

```json
[
  {
    "id": 12345678,
    "type": "Repository",
    "name": "web",
    "active": true,
    "events": ["push"],
    "config": {
      "url": "https://pslog.example.com/api/v1/webhooks/github",
      "content_type": "json",
      "insecure_ssl": "0"
    },
    "updated_at": "2026-04-29T12:00:00Z",
    "created_at": "2026-04-29T12:00:00Z"
  }
]
```

- [ ] **Step 2: Failing test (Hooks API 단위)**

Create `backend/tests/test_github_hook_service.py`:

```python
"""github_hook_service — GitHub Hooks API 단위 테스트.

설계서: 2026-04-26-ai-task-automation-design.md §5.3 (자동 webhook 등록), §9
"""

import json
from pathlib import Path

import httpx
import pytest

from app.services.github_hook_service import (
    create_hook,
    list_hooks,
    update_hook,
)


_REPO = "https://github.com/ardenspace/app-chak"
_HOOKS_FIXTURE = (
    Path(__file__).parent / "fixtures" / "github_hooks_payload.json"
).read_text()


async def test_list_hooks_returns_array(monkeypatch: pytest.MonkeyPatch):
    captured: dict[str, object] = {}

    async def fake_send(self, request: httpx.Request, **_kwargs):
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        return httpx.Response(status_code=200, json=json.loads(_HOOKS_FIXTURE))

    monkeypatch.setattr(httpx.AsyncClient, "send", fake_send)
    hooks = await list_hooks(_REPO, "ghp_abc")
    assert isinstance(hooks, list)
    assert len(hooks) == 1
    assert hooks[0]["id"] == 12345678
    assert "/repos/ardenspace/app-chak/hooks" in captured["url"]
    assert captured["headers"]["authorization"] == "token ghp_abc"


async def test_create_hook_posts_correct_body(monkeypatch: pytest.MonkeyPatch):
    captured: dict[str, object] = {}

    async def fake_send(self, request: httpx.Request, **_kwargs):
        captured["url"] = str(request.url)
        captured["method"] = request.method
        captured["body"] = request.content
        return httpx.Response(
            status_code=201,
            json={
                "id": 99999,
                "config": {"url": "https://pslog.example.com/api/v1/webhooks/github"},
            },
        )

    monkeypatch.setattr(httpx.AsyncClient, "send", fake_send)
    hook = await create_hook(
        _REPO,
        "ghp_abc",
        callback_url="https://pslog.example.com/api/v1/webhooks/github",
        secret="my-secret",
    )
    assert hook["id"] == 99999
    assert captured["method"] == "POST"
    assert "/repos/ardenspace/app-chak/hooks" in captured["url"]
    body = json.loads(captured["body"])
    assert body["name"] == "web"
    assert body["active"] is True
    assert body["events"] == ["push"]
    assert body["config"]["url"] == "https://pslog.example.com/api/v1/webhooks/github"
    assert body["config"]["secret"] == "my-secret"
    assert body["config"]["content_type"] == "json"


async def test_update_hook_patches_secret(monkeypatch: pytest.MonkeyPatch):
    captured: dict[str, object] = {}

    async def fake_send(self, request: httpx.Request, **_kwargs):
        captured["url"] = str(request.url)
        captured["method"] = request.method
        captured["body"] = request.content
        return httpx.Response(
            status_code=200,
            json={"id": 12345678, "config": {"url": "https://pslog.example.com/api/v1/webhooks/github"}},
        )

    monkeypatch.setattr(httpx.AsyncClient, "send", fake_send)
    hook = await update_hook(
        _REPO, "ghp_abc",
        hook_id=12345678,
        callback_url="https://pslog.example.com/api/v1/webhooks/github",
        secret="rotated-secret",
    )
    assert hook["id"] == 12345678
    assert captured["method"] == "PATCH"
    assert "/repos/ardenspace/app-chak/hooks/12345678" in captured["url"]
    body = json.loads(captured["body"])
    assert body["config"]["secret"] == "rotated-secret"


async def test_create_hook_5xx_raises(monkeypatch: pytest.MonkeyPatch):
    async def fake_send(self, request: httpx.Request, **_kwargs):
        return httpx.Response(status_code=502, json={"message": "Bad Gateway"})

    monkeypatch.setattr(httpx.AsyncClient, "send", fake_send)
    with pytest.raises(httpx.HTTPStatusError):
        await create_hook(_REPO, "ghp_abc", callback_url="x", secret="y")
```

- [ ] **Step 3: Run — ImportError**

- [ ] **Step 4: github_hook_service 구현**

Create `backend/app/services/github_hook_service.py`:

```python
"""GitHub Hooks API 클라이언트.

설계서: 2026-04-26-ai-task-automation-design.md §5.3 (ProjectGitSettings 자동 webhook 등록), §9

GitHub API:
  GET    /repos/{owner}/{repo}/hooks
  POST   /repos/{owner}/{repo}/hooks
  PATCH  /repos/{owner}/{repo}/hooks/{hook_id}

PAT 인증 필수 — GitHub 가 hooks 관리에 PAT 권한 (admin:repo_hook) 요구.
"""

from typing import Any

import httpx

from app.services.git_repo_service import _auth_headers, _parse_repo, _raise_for_status


_GITHUB_API = "https://api.github.com"


async def list_hooks(
    repo_url: str,
    pat: str,
    *,
    timeout: float = 30.0,
) -> list[dict[str, Any]]:
    """GET /repos/{owner}/{repo}/hooks → hooks 배열."""
    owner, repo = _parse_repo(repo_url)
    url = f"{_GITHUB_API}/repos/{owner}/{repo}/hooks"
    request = httpx.Request("GET", url, headers=_auth_headers(pat))
    async with httpx.AsyncClient(timeout=timeout) as client:
        res = await client.send(request)
    _raise_for_status(res, request)
    return res.json()


async def create_hook(
    repo_url: str,
    pat: str,
    *,
    callback_url: str,
    secret: str,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """POST /repos/{owner}/{repo}/hooks — push 이벤트 webhook 생성."""
    owner, repo = _parse_repo(repo_url)
    url = f"{_GITHUB_API}/repos/{owner}/{repo}/hooks"
    body = {
        "name": "web",
        "active": True,
        "events": ["push"],
        "config": {
            "url": callback_url,
            "content_type": "json",
            "secret": secret,
            "insecure_ssl": "0",
        },
    }
    request = httpx.Request("POST", url, headers=_auth_headers(pat), json=body)
    async with httpx.AsyncClient(timeout=timeout) as client:
        res = await client.send(request)
    _raise_for_status(res, request)
    return res.json()


async def update_hook(
    repo_url: str,
    pat: str,
    *,
    hook_id: int,
    callback_url: str,
    secret: str,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """PATCH /repos/{owner}/{repo}/hooks/{hook_id} — config.secret 갱신."""
    owner, repo = _parse_repo(repo_url)
    url = f"{_GITHUB_API}/repos/{owner}/{repo}/hooks/{hook_id}"
    body = {
        "active": True,
        "events": ["push"],
        "config": {
            "url": callback_url,
            "content_type": "json",
            "secret": secret,
            "insecure_ssl": "0",
        },
    }
    request = httpx.Request("PATCH", url, headers=_auth_headers(pat), json=body)
    async with httpx.AsyncClient(timeout=timeout) as client:
        res = await client.send(request)
    _raise_for_status(res, request)
    return res.json()
```

- [ ] **Step 5: Run hook service tests — pass (4 tests)**

```bash
pytest tests/test_github_hook_service.py -v
```

- [ ] **Step 6: webhook 등록 endpoint 테스트 추가**

`backend/tests/test_git_settings_endpoint.py` 끝에 추가:

```python
async def test_post_webhook_creates_new_hook(
    client_with_db, async_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
):
    """git_repo_url + PAT 갖춘 프로젝트 → 자동 webhook 등록."""
    user, proj = await _seed_user_project(async_session)
    proj.git_repo_url = "https://github.com/ardenspace/app-chak"
    proj.github_pat_encrypted = encrypt_secret("ghp_test_token")
    await async_session.commit()

    # github_hook_service mock — list 빈 배열 후 create 호출됨
    import app.services.github_hook_service as hook_mod

    captured: dict[str, object] = {}

    async def fake_list_hooks(repo_url, pat):
        return []

    async def fake_create_hook(repo_url, pat, *, callback_url, secret):
        captured["pat"] = pat
        captured["callback_url"] = callback_url
        captured["secret"] = secret
        return {"id": 77777, "config": {"url": callback_url}}

    monkeypatch.setattr(hook_mod, "list_hooks", fake_list_hooks)
    monkeypatch.setattr(hook_mod, "create_hook", fake_create_hook)

    token = _auth_token(user)
    res = await client_with_db.post(
        f"/api/v1/projects/{proj.id}/git-settings/webhook",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["webhook_id"] == 77777
    assert body["was_existing"] is False
    assert body["public_webhook_url"].endswith("/api/v1/webhooks/github")

    # PAT decrypt 후 hook service 에 전달됐는지
    assert captured["pat"] == "ghp_test_token"
    assert captured["callback_url"].endswith("/api/v1/webhooks/github")

    # webhook_secret_encrypted 가 새로 생성됨
    await async_session.refresh(proj)
    assert proj.webhook_secret_encrypted is not None
    from app.core.crypto import decrypt_secret
    decrypted = decrypt_secret(proj.webhook_secret_encrypted)
    assert decrypted == captured["secret"]


async def test_post_webhook_updates_existing_hook(
    client_with_db, async_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
):
    """같은 callback url 의 hook 이 있으면 PATCH (재생성 안 함)."""
    user, proj = await _seed_user_project(async_session)
    proj.git_repo_url = "https://github.com/ardenspace/app-chak"
    proj.github_pat_encrypted = encrypt_secret("ghp_test_token")
    await async_session.commit()

    import app.services.github_hook_service as hook_mod

    callback_called: dict[str, bool] = {"create": False, "update": False}

    async def fake_list_hooks(repo_url, pat):
        return [
            {
                "id": 12345678,
                "config": {"url": "http://localhost:8000/api/v1/webhooks/github"},
            }
        ]

    async def fake_create_hook(*args, **kwargs):
        callback_called["create"] = True
        return {"id": -1}

    async def fake_update_hook(repo_url, pat, *, hook_id, callback_url, secret):
        callback_called["update"] = True
        return {"id": hook_id, "config": {"url": callback_url}}

    monkeypatch.setattr(hook_mod, "list_hooks", fake_list_hooks)
    monkeypatch.setattr(hook_mod, "create_hook", fake_create_hook)
    monkeypatch.setattr(hook_mod, "update_hook", fake_update_hook)

    token = _auth_token(user)
    res = await client_with_db.post(
        f"/api/v1/projects/{proj.id}/git-settings/webhook",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["webhook_id"] == 12345678
    assert body["was_existing"] is True
    assert callback_called["update"] is True
    assert callback_called["create"] is False


async def test_post_webhook_400_when_repo_or_pat_missing(
    client_with_db, async_session: AsyncSession
):
    user, proj = await _seed_user_project(async_session)
    # git_repo_url / PAT 모두 None
    token = _auth_token(user)
    res = await client_with_db.post(
        f"/api/v1/projects/{proj.id}/git-settings/webhook",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 400


async def test_post_webhook_403_for_non_owner(
    client_with_db, async_session: AsyncSession
):
    user, proj = await _seed_user_project(async_session, role=WorkspaceRole.EDITOR)
    proj.git_repo_url = "https://github.com/ardenspace/app-chak"
    proj.github_pat_encrypted = encrypt_secret("ghp_test_token")
    await async_session.commit()

    token = _auth_token(user)
    res = await client_with_db.post(
        f"/api/v1/projects/{proj.id}/git-settings/webhook",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 403
```

- [ ] **Step 7: webhook 등록 endpoint 추가**

`backend/app/api/v1/endpoints/git_settings.py` 끝에 추가:

```python
import logging

from cryptography.fernet import InvalidToken

from app.core.crypto import decrypt_secret, generate_webhook_secret
from app.schemas.git_settings import WebhookRegisterResponse
from app.services import github_hook_service

logger = logging.getLogger(__name__)


@router.post(
    "/{project_id}/git-settings/webhook",
    response_model=WebhookRegisterResponse,
)
async def register_webhook(
    project_id: UUID,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
):
    """GitHub repo 에 push webhook 자동 등록 (또는 갱신).

    - 같은 callback url 의 hook 이 있으면 PATCH (config.secret 갱신)
    - 없으면 POST (신규 등록)
    - 새 webhook_secret 항상 생성 — 기존 secret 무효화 (regenerate 의 부수 효과)
    """
    project = await project_service.get_project(db, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    role = await get_effective_role(db, user.id, project_id)
    if role is None:
        raise HTTPException(status_code=404, detail="Project not found")
    if not can_manage(role):
        raise HTTPException(status_code=403, detail="Owner only")

    if not project.git_repo_url:
        raise HTTPException(status_code=400, detail="git_repo_url 미설정")
    if project.github_pat_encrypted is None:
        raise HTTPException(status_code=400, detail="GitHub PAT 미설정")

    try:
        pat = decrypt_secret(project.github_pat_encrypted)
    except InvalidToken:
        logger.error("PAT 복호화 실패 — Fernet 마스터 키 mismatch project=%s", project_id)
        raise HTTPException(status_code=500, detail="PAT 복호화 실패")

    callback_url = _public_webhook_url()
    new_secret = generate_webhook_secret()

    existing_hooks = await github_hook_service.list_hooks(project.git_repo_url, pat)
    matching = next(
        (h for h in existing_hooks if h.get("config", {}).get("url") == callback_url),
        None,
    )

    if matching is not None:
        hook = await github_hook_service.update_hook(
            project.git_repo_url, pat,
            hook_id=matching["id"],
            callback_url=callback_url,
            secret=new_secret,
        )
        was_existing = True
    else:
        hook = await github_hook_service.create_hook(
            project.git_repo_url, pat,
            callback_url=callback_url,
            secret=new_secret,
        )
        was_existing = False

    # 새 secret 저장
    from app.core.crypto import encrypt_secret as _encrypt
    project.webhook_secret_encrypted = _encrypt(new_secret)
    await db.commit()

    return WebhookRegisterResponse(
        webhook_id=hook["id"],
        was_existing=was_existing,
        public_webhook_url=callback_url,
    )
```

- [ ] **Step 8: Run all git_settings tests — pass (11 tests)**

```bash
pytest tests/test_git_settings_endpoint.py tests/test_github_hook_service.py -v
```

- [ ] **Step 9: Commit**

```bash
git add backend/app/services/github_hook_service.py \
        backend/app/api/v1/endpoints/git_settings.py \
        backend/tests/test_github_hook_service.py \
        backend/tests/test_git_settings_endpoint.py \
        backend/tests/fixtures/github_hooks_payload.json
git commit -m "feat(phase5a): 자동 webhook 등록 (github_hook_service + POST /git-settings/webhook)"
```

---

## Task 6: GET `/handoffs` — handoff 이력 조회

**Files:**
- Modify: `backend/app/api/v1/endpoints/git_settings.py`
- Modify: `backend/tests/test_git_settings_endpoint.py`

- [ ] **Step 1: handoff 이력 조회 테스트 추가**

`backend/tests/test_git_settings_endpoint.py` 끝에 추가:

```python
from datetime import datetime, timedelta

from app.models.handoff import Handoff


async def test_get_handoffs_returns_summary_list(
    client_with_db, async_session: AsyncSession
):
    user, proj = await _seed_user_project(async_session)
    now = datetime.utcnow()
    h1 = Handoff(
        project_id=proj.id,
        branch="main",
        author_git_login="alice",
        commit_sha="a" * 40,
        pushed_at=now,
        parsed_tasks=[{"external_id": "task-001", "checked": True}],
        free_notes={"last_commit": "x"},
        raw_content="raw",
    )
    h2 = Handoff(
        project_id=proj.id,
        branch="feature/login",
        author_git_login="bob",
        commit_sha="b" * 40,
        pushed_at=now - timedelta(hours=1),
        parsed_tasks=[
            {"external_id": "t-1"},
            {"external_id": "t-2"},
        ],
        free_notes={},
        raw_content="raw",
    )
    async_session.add_all([h1, h2])
    await async_session.commit()

    token = _auth_token(user)
    res = await client_with_db.get(
        f"/api/v1/projects/{proj.id}/handoffs",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200
    items = res.json()
    assert len(items) == 2
    # pushed_at desc — h1 이 더 최근
    assert items[0]["commit_sha"] == "a" * 40
    assert items[0]["parsed_tasks_count"] == 1
    assert items[1]["commit_sha"] == "b" * 40
    assert items[1]["parsed_tasks_count"] == 2
    # raw_content 본체는 응답에 없음
    assert "raw_content" not in items[0]
    assert "raw" not in res.text


async def test_get_handoffs_filters_by_branch(
    client_with_db, async_session: AsyncSession
):
    user, proj = await _seed_user_project(async_session)
    now = datetime.utcnow()
    async_session.add_all([
        Handoff(
            project_id=proj.id, branch="main", author_git_login="a",
            commit_sha="1" * 40, pushed_at=now, parsed_tasks=[], free_notes={},
        ),
        Handoff(
            project_id=proj.id, branch="feature/x", author_git_login="b",
            commit_sha="2" * 40, pushed_at=now, parsed_tasks=[], free_notes={},
        ),
    ])
    await async_session.commit()

    token = _auth_token(user)
    res = await client_with_db.get(
        f"/api/v1/projects/{proj.id}/handoffs?branch=feature/x",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200
    items = res.json()
    assert len(items) == 1
    assert items[0]["branch"] == "feature/x"


async def test_get_handoffs_404_for_non_member(
    client_with_db, async_session: AsyncSession
):
    user, proj = await _seed_user_project(async_session)
    other = User(
        email=f"o-{uuid.uuid4().hex[:8]}@example.com", name="bob", password_hash="x"
    )
    async_session.add(other)
    await async_session.commit()
    await async_session.refresh(other)

    token = _auth_token(other)
    res = await client_with_db.get(
        f"/api/v1/projects/{proj.id}/handoffs",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 404


async def test_get_handoffs_limit_clamped_to_max(
    client_with_db, async_session: AsyncSession
):
    """limit > 200 도 200 으로 clamp."""
    user, proj = await _seed_user_project(async_session)
    token = _auth_token(user)
    res = await client_with_db.get(
        f"/api/v1/projects/{proj.id}/handoffs?limit=99999",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200  # 422 가 아니어야 — 서버에서 clamp
```

- [ ] **Step 2: handoff list endpoint 추가**

`backend/app/api/v1/endpoints/git_settings.py` 끝에 추가:

```python
from sqlalchemy import select

from app.models.handoff import Handoff
from app.schemas.git_settings import HandoffSummary


@router.get("/{project_id}/handoffs", response_model=list[HandoffSummary])
async def list_handoffs(
    project_id: UUID,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
    branch: str | None = None,
    limit: int = 50,
):
    project = await project_service.get_project(db, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    role = await get_effective_role(db, user.id, project_id)
    if role is None:
        raise HTTPException(status_code=404, detail="Project not found")

    limit = max(1, min(limit, 200))

    stmt = (
        select(Handoff)
        .where(Handoff.project_id == project_id)
        .order_by(Handoff.pushed_at.desc())
        .limit(limit)
    )
    if branch is not None:
        stmt = stmt.where(Handoff.branch == branch)

    rows = (await db.execute(stmt)).scalars().all()
    return [
        HandoffSummary(
            id=h.id,
            branch=h.branch,
            author_git_login=h.author_git_login,
            commit_sha=h.commit_sha,
            pushed_at=h.pushed_at,
            parsed_tasks_count=len(h.parsed_tasks or []),
        )
        for h in rows
    ]
```

- [ ] **Step 3: Run — pass (15 tests on git_settings_endpoint)**

```bash
pytest tests/test_git_settings_endpoint.py -v
```

- [ ] **Step 4: Commit**

```bash
git add backend/app/api/v1/endpoints/git_settings.py backend/tests/test_git_settings_endpoint.py
git commit -m "feat(phase5a): GET /handoffs — handoff 이력 (branch 필터 + limit clamp)"
```

---

## Task 7: POST `/git-events/{id}/reprocess` — 수동 재처리

**Files:**
- Modify: `backend/app/api/v1/endpoints/git_settings.py`
- Modify: `backend/tests/test_git_settings_endpoint.py`

- [ ] **Step 1: 재처리 endpoint 테스트**

`backend/tests/test_git_settings_endpoint.py` 끝에 추가:

```python
from app.models.git_push_event import GitPushEvent


async def test_reprocess_resets_event_and_queues_sync(
    client_with_db, async_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
):
    """processed_at + error reset 후 BackgroundTask 로 sync 호출 큐."""
    user, proj = await _seed_user_project(async_session)
    event = GitPushEvent(
        project_id=proj.id,
        branch="main",
        head_commit_sha="a" * 40,
        commits=[],
        commits_truncated=False,
        pusher="alice",
        processed_at=datetime.utcnow(),
        error="MalformedHandoffError: bad header",
    )
    async_session.add(event)
    await async_session.commit()
    await async_session.refresh(event)

    # background sync 호출 mock
    called: list[str] = []

    async def fake_run(event_id):
        called.append(str(event_id))

    import app.api.v1.endpoints.webhooks as webhooks_module
    monkeypatch.setattr(webhooks_module, "_run_sync_in_new_session", fake_run)

    token = _auth_token(user)
    res = await client_with_db.post(
        f"/api/v1/projects/{proj.id}/git-events/{event.id}/reprocess",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["event_id"] == str(event.id)
    assert body["status"] == "queued"

    await async_session.refresh(event)
    assert event.processed_at is None
    assert event.error is None
    assert len(called) == 1


async def test_reprocess_400_when_already_succeeded(
    client_with_db, async_session: AsyncSession
):
    """processed_at 이 set 이고 error 가 None (성공 처리됨) → 400."""
    user, proj = await _seed_user_project(async_session)
    event = GitPushEvent(
        project_id=proj.id, branch="main", head_commit_sha="a" * 40,
        commits=[], commits_truncated=False, pusher="alice",
        processed_at=datetime.utcnow(),
        error=None,  # 성공
    )
    async_session.add(event)
    await async_session.commit()
    await async_session.refresh(event)

    token = _auth_token(user)
    res = await client_with_db.post(
        f"/api/v1/projects/{proj.id}/git-events/{event.id}/reprocess",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 400


async def test_reprocess_404_for_event_in_different_project(
    client_with_db, async_session: AsyncSession
):
    """다른 프로젝트의 event id 를 자기 프로젝트 path 로 호출 → 404."""
    user, proj = await _seed_user_project(async_session)
    other_user, other_proj = await _seed_user_project(async_session)
    event = GitPushEvent(
        project_id=other_proj.id, branch="main", head_commit_sha="a" * 40,
        commits=[], commits_truncated=False, pusher="alice",
    )
    async_session.add(event)
    await async_session.commit()
    await async_session.refresh(event)

    token = _auth_token(user)
    res = await client_with_db.post(
        f"/api/v1/projects/{proj.id}/git-events/{event.id}/reprocess",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 404


async def test_reprocess_403_for_non_owner(
    client_with_db, async_session: AsyncSession
):
    user, proj = await _seed_user_project(async_session, role=WorkspaceRole.EDITOR)
    event = GitPushEvent(
        project_id=proj.id, branch="main", head_commit_sha="a" * 40,
        commits=[], commits_truncated=False, pusher="alice",
        processed_at=datetime.utcnow(), error="x",
    )
    async_session.add(event)
    await async_session.commit()
    await async_session.refresh(event)

    token = _auth_token(user)
    res = await client_with_db.post(
        f"/api/v1/projects/{proj.id}/git-events/{event.id}/reprocess",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 403
```

- [ ] **Step 2: reprocess endpoint 추가**

`backend/app/api/v1/endpoints/git_settings.py` 끝에 추가:

```python
from fastapi import BackgroundTasks

from app.models.git_push_event import GitPushEvent
from app.schemas.git_settings import ReprocessResponse


@router.post(
    "/{project_id}/git-events/{event_id}/reprocess",
    response_model=ReprocessResponse,
)
async def reprocess_git_event(
    project_id: UUID,
    event_id: UUID,
    background_tasks: BackgroundTasks,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
):
    project = await project_service.get_project(db, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    role = await get_effective_role(db, user.id, project_id)
    if role is None:
        raise HTTPException(status_code=404, detail="Project not found")
    if not can_manage(role):
        raise HTTPException(status_code=403, detail="Owner only")

    event = await db.get(GitPushEvent, event_id)
    if event is None or event.project_id != project_id:
        raise HTTPException(status_code=404, detail="Event not found")

    if event.processed_at is not None and event.error is None:
        raise HTTPException(
            status_code=400,
            detail="Event already processed successfully — nothing to reprocess",
        )

    event.processed_at = None
    event.error = None
    await db.commit()

    # BackgroundTask 로 sync 큐 — Phase 4 webhook endpoint 의 _run_sync_in_new_session 재사용
    from app.api.v1.endpoints.webhooks import _run_sync_in_new_session
    background_tasks.add_task(_run_sync_in_new_session, event_id)

    return ReprocessResponse(event_id=event_id, status="queued")
```

- [ ] **Step 3: Run — pass**

```bash
pytest tests/test_git_settings_endpoint.py -v
```

Expected: 19 tests pass (15 기존 + 4 신규).

- [ ] **Step 4: Commit**

```bash
git add backend/app/api/v1/endpoints/git_settings.py backend/tests/test_git_settings_endpoint.py
git commit -m "feat(phase5a): POST /git-events/{id}/reprocess — 사용자 수동 재처리"
```

---

## Task 8: 회귀 + handoff + PR

**Files:**
- Modify: `handoffs/main.md`

- [ ] **Step 1: 전체 회귀**

```bash
cd /Users/arden/Documents/ardensdevspace/pslog/backend
source venv/bin/activate
pytest -v --tb=short 2>&1 | tail -10
```

Expected: 137 (Phase 1+2+3+4) + 4 (Task 1 migration) + 2 (Task 2 before_sha 활용) + 4 (Task 5 hook service) + 19 (git_settings endpoint) = **166 tests pass**, 회귀 0.

- [ ] **Step 2: 누락 점검**

설계서 §5.2 endpoint 6항목 매핑:
- [x] POST `/webhooks/github` → Phase 2
- [x] GET `/git-settings` → Task 3
- [x] PATCH `/git-settings` → Task 4
- [x] GET `/handoffs` → Task 6
- [ ] GET `/brief` → Phase 7 (Gemma)
- [x] POST `/git-events/{id}/reprocess` → Task 7

설계서 §9 보안:
- [x] PAT Fernet 암호화 + 응답 redacted (Task 3, 4)
- [x] webhook secret 자동 생성 + Fernet (Task 5)
- [x] `can_manage` 권한 체크 (PATCH/POST 전체)

handoff 메모 (Phase 4):
- [x] before_commit_sha 컬럼 + commits_truncated base 정확화 → Task 1, 2

- [ ] **Step 3: 부팅 smoke**

```bash
python -c "from app.main import app; print('startup OK')"
```

- [ ] **Step 4: handoff 갱신**

`handoffs/main.md` 상단에 Phase 5a 섹션 추가:

```markdown
## 2026-04-30 (Phase 5a)

- [x] **Phase 5a 완료** — Backend endpoints + 자동 webhook 등록 (브랜치 `feature/phase-5a-backend-endpoints`)
  - [x] `GitPushEvent.before_commit_sha` 컬럼 + CHECK 제약 (alembic, commits_truncated base 정확화) — `record_push_event` 가 payload.before 저장, `sync_service._collect_changed_files` 가 우선 사용
  - [x] `GET /api/v1/projects/{id}/git-settings` — repo URL / plan_path / handoff_dir / has_webhook_secret / has_github_pat / public_webhook_url. 평문 secret 절대 노출 안 함.
  - [x] `PATCH /api/v1/projects/{id}/git-settings` (OWNER) — 부분 갱신, github_pat 입력 시 즉시 Fernet encrypt
  - [x] `POST /api/v1/projects/{id}/git-settings/webhook` (OWNER) — `github_hook_service` 가 GitHub Hooks API 호출 (`list_hooks` / `create_hook` / `update_hook`). 같은 callback url 의 hook 있으면 PATCH, 없으면 POST. 새 webhook_secret 매번 생성 (regenerate)
  - [x] `GET /api/v1/projects/{id}/handoffs?branch=...&limit=...` — `Handoff` 목록 (raw_content 제외, parsed_tasks_count 만), pushed_at desc, limit clamp 200
  - [x] `POST /api/v1/projects/{id}/git-events/{event_id}/reprocess` (OWNER) — 처리 실패 이벤트 reset + Phase 4 의 `_run_sync_in_new_session` 재사용
  - [x] `pslog_public_url` settings 추가 (config.py + .env)
  - [x] **166 tests passing** (Phase 1+2+3+4 137 + Phase 5a 신규 29)

### 마지막 커밋

- pslog: `<sha> docs(handoff): Phase 5a 완료 + Phase 5b 다음 할 일`
- 브랜치 base: `44590c6` (main, Phase 4 머지 직후)

### 다음 (Phase 5b — Frontend UI)

- [ ] `frontend/src/services/githubApi.ts` — git-settings / handoffs / reprocess axios 호출
- [ ] `frontend/src/hooks/useGithubSettings.ts` — TanStack Query 훅
- [ ] `frontend/src/pages/ProjectGitSettings.tsx` — repo URL / PAT / plan_path / handoff_dir 입력 폼 + "Webhook 등록" 버튼
- [ ] `frontend/src/pages/HandoffHistory.tsx` — 브랜치별 이력 + 재처리 버튼
- [ ] `frontend/src/components/TaskCard.tsx` 수정 — `source` 배지 (`MANUAL` / `SYNCED_FROM_PLAN`) + handoff 누락 ⚠️ 표시 (TaskCard 가 보여야 할 정보 — Phase 5b 에서 백엔드 `last_handoff_at` 같은 필드 필요한지 검토)
- [ ] dev server 띄우고 수동 검증 (CLAUDE.md 시스템 가이드)

### 블로커

없음

### 메모 (2026-04-30 Phase 5a 추가)

- **`pslog_public_url` 기본값 localhost**: prod 배포 시 Cloudflare Tunnel URL 로 환경변수 override 필수. 자동 webhook 등록이 localhost 로 callback 등록하면 GitHub 가 호출 못 함 — 수동 검증 시 주의.
- **PAT 권한 범위**: GitHub PAT 는 `admin:repo_hook` 스코프 필요 (자동 webhook 등록용). 사용자 UI 에 "PAT 발급 시 admin:repo_hook 권한 체크" 안내 필요 (Phase 5b ProjectGitSettings 도움말 텍스트).
- **webhook 자동 등록 = secret regenerate**: 매번 새 secret 생성. 기존 webhook 이 있으면 PATCH 로 secret 갱신 — 즉 "재등록" 버튼이 사실상 "secret rotate" 효과. Phase 5b UI 에 명시.
- **`record_push_event` 의 `before` 저장**: GitHub `before` 가 `0000...0000` (40 zeros, force-push initial) 일 수 있음. 길이 40 hex 라 CHECK 통과. sync_service 는 그걸 base 로 Compare API 호출 → GitHub 가 적절히 처리 (전체 ahead).
- **reprocess 정책**: `processed_at IS NOT NULL AND error IS NULL` 이면 400 (이미 성공). 그 외엔 reset → BackgroundTask. `processed_at IS NULL` 케이스 (sync 진행 중) 도 허용 — 사용자가 의도적으로 재처리할 수 있고, processed_at 가드 + Handoff UNIQUE 가 race 흡수.
- **Phase 5b 진입 전 검토**: TaskCard 의 "handoff 누락 ⚠️" 표시 — 어떤 데이터로 판정? 가능한 답: (a) Project 별로 마지막 push 의 head_sha 이후 N일 동안 handoff 없으면 ⚠️, (b) Task 가 `last_commit_sha` 가 set 인데 그 commit 의 handoff 가 없으면 ⚠️. 본 phase 는 데이터 노출 안 함 — Phase 5b 시작 시 결정.

---
```

- [ ] **Step 5: handoff commit + push + PR**

```bash
git add handoffs/main.md
git commit -m "docs(handoff): Phase 5a 완료 + Phase 5b 다음 할 일"
git add docs/superpowers/plans/2026-04-30-phase-5a-backend-endpoints.md
git commit -m "docs(plan): Phase 5a plan"

git push -u origin feature/phase-5a-backend-endpoints
gh pr create --title "feat: Phase 5a — Backend endpoints + 자동 webhook 등록" --body "$(cat <<'EOF'
## Summary

Phase 5 의 backend 절반 — frontend (`ProjectGitSettings.tsx` 등) 가 호출할 API 가 모두 갖춰짐.

- `GitPushEvent.before_commit_sha` 컬럼 + CHECK (commits_truncated base 정확화 — Phase 4 fallback 메모 반영)
- `GET /git-settings` — 평문 secret 절대 노출 안 함 (`has_*` boolean 만)
- `PATCH /git-settings` (OWNER) — github_pat 즉시 Fernet encrypt
- `POST /git-settings/webhook` (OWNER) — `github_hook_service` 가 GitHub Hooks API 호출. 기존 hook 있으면 PATCH (secret regenerate), 없으면 POST. 매번 새 webhook_secret 생성
- `GET /handoffs` — 브랜치별 필터 + limit clamp, `raw_content` 제외 (목록용)
- `POST /git-events/{id}/reprocess` (OWNER) — Phase 4 의 `_run_sync_in_new_session` 재사용 + processed_at/error reset

## Architecture decisions

- **`github_hook_service` 분리**: `git_repo_service` (Contents/Compare) 와 다른 책임 (admin:repo_hook 권한 + write 동작). 같은 `_auth_headers`/`_parse_repo`/`_raise_for_status` 헬퍼 재사용.
- **권한**: 모든 PATCH/POST → OWNER (`can_manage`). PAT/secret/sync 트리거는 민감.
- **`pslog_public_url` 기본값**: `http://localhost:8000` — prod 는 환경변수 override.

## Migration

- alembic head: `274c0ed55105` → `<new sha>` (`GitPushEvent.before_commit_sha` 컬럼 + CHECK)
- 회귀 테스트: 기존 데이터 NULL 보존, hex CHECK 검증, 컬럼 존재 검증

## Test plan

- [x] `pytest tests/test_phase5a_migration.py tests/test_git_settings_endpoint.py tests/test_github_hook_service.py -v` — Phase 5a 신규 27건 pass
- [x] `pytest tests/test_github_webhook_service.py tests/test_sync_service.py -v` — before_sha 회귀 2건 + 기존 보존
- [x] `pytest -v` — Phase 1+2+3+4+5a = **166 tests pass**, 회귀 0
- [x] 설계서 §5.2 endpoint 5/6 매핑 (Brief 는 Phase 7)
- [x] 설계서 §9 보안: PAT redacted, OWNER 전용 권한, webhook_secret regenerate

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Phase 5a 완료 기준 (Acceptance)

- [ ] alembic head 가 새 revision (`GitPushEvent.before_commit_sha` 추가) 으로 진행됨
- [ ] `record_push_event` 가 `payload.before` 저장 (40자 hex 만)
- [ ] `sync_service._collect_changed_files` 가 `event.before_commit_sha` 우선 사용
- [ ] GET `/git-settings` 가 멤버에게 200, 비멤버 404, 평문 secret 노출 0
- [ ] PATCH `/git-settings` 가 OWNER 만 200, EDITOR 403, 비멤버 404, github_pat 즉시 Fernet encrypt 저장
- [ ] POST `/git-settings/webhook` 이 기존 hook 있으면 PATCH, 없으면 POST. webhook_secret 매번 regenerate
- [ ] GET `/handoffs?branch=...&limit=...` 가 pushed_at desc, raw_content 제외, limit clamp 200
- [ ] POST `/git-events/{id}/reprocess` 가 processed_at/error reset + BackgroundTask 큐. 이미 성공 처리됐으면 400
- [ ] Phase 1+2+3+4 회귀 0 — 기존 137 tests 모두 pass
- [ ] PR 생성됨, 사용자 검토 단계
