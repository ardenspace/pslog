"""git-settings endpoint e2e 테스트.

설계서: 2026-04-26-ai-task-automation-design.md §5.2, §9
"""

import asyncio
import uuid

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
        f"/api/v1/projects/{proj.id}/git-settings",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 404


async def test_get_git_settings_redacts_secrets(
    client_with_db, async_session: AsyncSession
):
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
    assert "github_pat" not in body
    assert "github_pat_encrypted" not in body
    assert "webhook_secret" not in body
    assert "webhook_secret_encrypted" not in body
    raw_text = res.text
    assert "ghp_super_secret_token" not in raw_text
    assert "super-shared-secret" not in raw_text


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
    assert "ghp_new_token_value" not in res.text

    await async_session.refresh(proj)
    assert proj.github_pat_encrypted is not None
    from app.core.crypto import decrypt_secret
    assert decrypt_secret(proj.github_pat_encrypted) == "ghp_new_token_value"


async def test_patch_git_settings_partial_update_preserves_others(
    client_with_db, async_session: AsyncSession
):
    user, proj = await _seed_user_project(async_session)
    proj.git_repo_url = "https://github.com/old/repo"
    proj.plan_path = "PLAN.md"
    await async_session.commit()

    token = _auth_token(user)
    res = await client_with_db.patch(
        f"/api/v1/projects/{proj.id}/git-settings",
        json={"plan_path": "docs/PLAN.md"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200
    await async_session.refresh(proj)
    assert proj.git_repo_url == "https://github.com/old/repo"
    assert proj.plan_path == "docs/PLAN.md"


async def test_patch_git_settings_403_for_non_owner(
    client_with_db, async_session: AsyncSession
):
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


async def test_post_webhook_creates_new_hook(
    client_with_db, async_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
):
    user, proj = await _seed_user_project(async_session)
    proj.git_repo_url = "https://github.com/ardenspace/app-chak"
    proj.github_pat_encrypted = encrypt_secret("ghp_test_token")
    await async_session.commit()

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

    assert captured["pat"] == "ghp_test_token"
    assert captured["callback_url"].endswith("/api/v1/webhooks/github")

    await async_session.refresh(proj)
    assert proj.webhook_secret_encrypted is not None
    from app.core.crypto import decrypt_secret
    decrypted = decrypt_secret(proj.webhook_secret_encrypted)
    assert decrypted == captured["secret"]


async def test_post_webhook_updates_existing_hook(
    client_with_db, async_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
):
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
    # 로컬 .env 의 PSLOG_PUBLIC_URL 이 뭐든 fake_list_hooks 의 config.url 과 맞게 고정
    monkeypatch.setattr(
        "app.api.v1.endpoints.git_settings.settings.pslog_public_url",
        "http://localhost:8000",
    )

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


# ---------------------------------------------------------------------------
# Task 6: GET /handoffs — handoff 이력 조회
# ---------------------------------------------------------------------------

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
    """limit > 200 도 200 으로 clamp — 422 안 남."""
    user, proj = await _seed_user_project(async_session)
    token = _auth_token(user)
    res = await client_with_db.get(
        f"/api/v1/projects/{proj.id}/handoffs?limit=99999",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200


# ---------------------------------------------------------------------------
# Task 7: POST /git-events/{id}/reprocess — 수동 재처리
# ---------------------------------------------------------------------------

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
    """processed_at set + error None (성공 처리) → 400."""
    user, proj = await _seed_user_project(async_session)
    event = GitPushEvent(
        project_id=proj.id, branch="main", head_commit_sha="a" * 40,
        commits=[], commits_truncated=False, pusher="alice",
        processed_at=datetime.utcnow(),
        error=None,
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
    _, other_proj = await _seed_user_project(async_session)
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


# ---------------------------------------------------------------------------
# code review permission audit: non-member 404 tests
# ---------------------------------------------------------------------------

async def test_post_webhook_404_for_non_member(
    client_with_db, async_session: AsyncSession
):
    user, proj = await _seed_user_project(async_session)
    proj.git_repo_url = "https://github.com/ardenspace/app-chak"
    proj.github_pat_encrypted = encrypt_secret("ghp_test_token")
    await async_session.commit()

    other = User(
        email=f"o-{uuid.uuid4().hex[:8]}@example.com",
        name="bob",
        password_hash="x",
    )
    async_session.add(other)
    await async_session.commit()
    await async_session.refresh(other)

    token = _auth_token(other)
    res = await client_with_db.post(
        f"/api/v1/projects/{proj.id}/git-settings/webhook",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 404


async def test_reprocess_404_for_non_member(
    client_with_db, async_session: AsyncSession
):
    user, proj = await _seed_user_project(async_session)
    event = GitPushEvent(
        project_id=proj.id, branch="main", head_commit_sha="a" * 40,
        commits=[], commits_truncated=False, pusher="alice",
        processed_at=datetime.utcnow(), error="x",
    )
    async_session.add(event)
    await async_session.commit()
    await async_session.refresh(event)

    other = User(
        email=f"o-{uuid.uuid4().hex[:8]}@example.com",
        name="bob",
        password_hash="x",
    )
    async_session.add(other)
    await async_session.commit()
    await async_session.refresh(other)

    token = _auth_token(other)
    res = await client_with_db.post(
        f"/api/v1/projects/{proj.id}/git-events/{event.id}/reprocess",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 404


async def test_reprocess_409_when_still_in_flight(
    client_with_db, async_session: AsyncSession
):
    """processed_at IS NULL (= 초기 BackgroundTask 가 아직 처리 중) 인 event 의 reprocess 는 409.
    User 가 webhook 직후 BackgroundTask 가 끝나기 전 클릭한 case 차단 (B1 / I-4 layer 1)."""
    user, proj = await _seed_user_project(async_session)
    event = GitPushEvent(
        project_id=proj.id, branch="main", head_commit_sha="a" * 40,
        commits=[], commits_truncated=False, pusher="alice",
        processed_at=None,  # 아직 처리 중
        error=None,
    )
    async_session.add(event)
    await async_session.commit()
    await async_session.refresh(event)

    token = _auth_token(user)
    res = await client_with_db.post(
        f"/api/v1/projects/{proj.id}/git-events/{event.id}/reprocess",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 409
    detail = res.json()["detail"].lower()
    assert "still" in detail or "processing" in detail


# ---------------------------------------------------------------------------
# B1 / I-2: register_webhook SELECT FOR UPDATE 재진입 가드
# ---------------------------------------------------------------------------


async def test_concurrent_register_webhook_serializes(
    async_session: AsyncSession, monkeypatch: pytest.MonkeyPatch, upgraded_db,
):
    """두 OWNER 가 동시에 register_webhook 호출 → row lock 으로 직렬화.

    fix 없으면 둘 다 list_hooks → 둘 다 'no matching' → create_hook 2번 호출됨
    (GitHub 측에 중복 webhook + DB 의 secret 은 마지막 writer 만 매치 → 첫 hook 영구 무효).

    fix 후: 후행 caller 는 권한 검증 직후 db.refresh(..., with_for_update=...) 에서
    선행 final commit 까지 대기 → list_hooks 결과에 선행이 만든 hook 이 보임 → update_hook 분기.
    """
    from cryptography.fernet import Fernet
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    monkeypatch.setenv("PSLOG_FERNET_KEY", Fernet.generate_key().decode())
    import importlib
    import app.config as _config_mod
    importlib.reload(_config_mod)
    import app.core.crypto as _crypto_mod
    importlib.reload(_crypto_mod)

    user, proj = await _seed_user_project(async_session)
    proj.git_repo_url = "https://github.com/ardenspace/app-chak"
    proj.github_pat_encrypted = _crypto_mod.encrypt_secret("ghp_test_token")
    await async_session.commit()
    project_id = proj.id

    list_calls = {"n": 0}
    create_calls = {"n": 0}
    update_calls = {"n": 0}
    stored_hook: dict[str, object] = {}
    t1_inside_list = asyncio.Event()
    release = asyncio.Event()

    async def slow_list_hooks(repo, pat):  # noqa: ARG001
        list_calls["n"] += 1
        if list_calls["n"] == 1:
            t1_inside_list.set()
            await release.wait()
            return []
        return [stored_hook] if stored_hook else []

    async def fake_create_hook(repo, pat, *, callback_url, secret):  # noqa: ARG001
        create_calls["n"] += 1
        h = {"id": 88888, "config": {"url": callback_url}}
        stored_hook.update(h)
        return h

    async def fake_update_hook(repo, pat, *, hook_id, callback_url, secret):  # noqa: ARG001
        update_calls["n"] += 1
        return {"id": hook_id, "config": {"url": callback_url}}

    import app.services.github_hook_service as hook_mod
    monkeypatch.setattr(hook_mod, "list_hooks", slow_list_hooks)
    monkeypatch.setattr(hook_mod, "create_hook", fake_create_hook)
    monkeypatch.setattr(hook_mod, "update_hook", fake_update_hook)

    # 같은 per-test DB 에 별도 engine 두 개 — 두 독립 세션이 row lock 경쟁하도록.
    dsn = upgraded_db["async_url"]
    engine_a = create_async_engine(dsn, echo=False)
    engine_b = create_async_engine(dsn, echo=False)
    maker_a = async_sessionmaker(engine_a, expire_on_commit=False)
    maker_b = async_sessionmaker(engine_b, expire_on_commit=False)

    from app.api.v1.endpoints.git_settings import register_webhook

    async def runner(maker):
        async with maker() as db:
            await register_webhook(project_id=project_id, db=db)

    async def releaser():
        # T1 이 list_hooks 까지 들어간 시점에 T2 도 entry FOR UPDATE 에서 대기 중이도록 시간 둠.
        await t1_inside_list.wait()
        await asyncio.sleep(0.4)
        release.set()

    try:
        # T1 먼저 시작해 lock 획득. T2 는 약간 후에 시작해 entry 에서 대기.
        t1 = asyncio.create_task(runner(maker_a))
        await asyncio.sleep(0.05)
        t2 = asyncio.create_task(runner(maker_b))
        rel = asyncio.create_task(releaser())
        await asyncio.gather(t1, t2, rel)
    finally:
        await engine_a.dispose()
        await engine_b.dispose()

    # 핵심: create_hook 정확히 1번, update_hook 정확히 1번.
    # fix 없으면 create_hook 2번 (둘 다 'no matching' 분기) + update_hook 0번.
    assert create_calls["n"] == 1, (
        f"create_hook called {create_calls['n']} times — "
        "FOR UPDATE 가 register_webhook 에 적용되지 않음"
    )
    assert update_calls["n"] == 1


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


# ---------------------------------------------------------------------------
# Phase 6: GitSettings 응답 확장 + POST /discord-reset
# ---------------------------------------------------------------------------


async def test_get_git_settings_includes_discord_status(
    client_with_db, async_session: AsyncSession
):
    """GET /git-settings 응답에 discord_enabled / discord_disabled_at / discord_consecutive_failures 포함."""
    user, proj = await _seed_user_project(async_session)
    proj.discord_webhook_url = "https://discord.com/api/webhooks/1/abc"
    await async_session.commit()

    token = _auth_token(user)
    res = await client_with_db.get(
        f"/api/v1/projects/{proj.id}/git-settings",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["discord_enabled"] is True
    assert body["discord_disabled_at"] is None
    assert body["discord_consecutive_failures"] == 0


async def test_discord_reset_clears_counter_and_disabled_at(
    client_with_db, async_session: AsyncSession
):
    """POST /discord-reset (OWNER) → counter 0 + disabled_at NULL."""
    user, proj = await _seed_user_project(async_session)
    proj.discord_webhook_url = "https://discord.com/api/webhooks/1/abc"
    proj.discord_consecutive_failures = 3
    proj.discord_disabled_at = datetime.utcnow()
    await async_session.commit()

    token = _auth_token(user)
    res = await client_with_db.post(
        f"/api/v1/projects/{proj.id}/git-settings/discord-reset",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["discord_enabled"] is True
    assert body["discord_disabled_at"] is None
    assert body["discord_consecutive_failures"] == 0

    await async_session.refresh(proj)
    assert proj.discord_consecutive_failures == 0
    assert proj.discord_disabled_at is None


async def test_discord_reset_403_for_non_owner(
    client_with_db, async_session: AsyncSession
):
    user, proj = await _seed_user_project(async_session, role=WorkspaceRole.EDITOR)
    token = _auth_token(user)
    res = await client_with_db.post(
        f"/api/v1/projects/{proj.id}/git-settings/discord-reset",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 403


async def test_discord_reset_404_for_non_member(
    client_with_db, async_session: AsyncSession
):
    user, proj = await _seed_user_project(async_session)
    other = User(
        email=f"o-{uuid.uuid4().hex[:8]}@example.com", name="bob", password_hash="x",
    )
    async_session.add(other)
    await async_session.commit()
    await async_session.refresh(other)

    token = _auth_token(other)
    res = await client_with_db.post(
        f"/api/v1/projects/{proj.id}/git-settings/discord-reset",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 404
