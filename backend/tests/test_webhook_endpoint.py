"""POST /api/v1/webhooks/github e2e 테스트.

설계서: 2026-04-26-ai-task-automation-design.md §7.1, §8 (응답 정책)
- 401: 서명 검증 실패
- 200 + 경고 로그: 알 수 없는 repo (GitHub 재전송 방지)
- 200: 정상 + GitPushEvent INSERT
- 200: 중복 commit_sha (멱등성)
"""

import hashlib
import hmac
import json
import uuid
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import encrypt_secret
from app.models.git_push_event import GitPushEvent
from app.models.project import Project
from app.models.workspace import Workspace


FIXTURE = (Path(__file__).parent / "fixtures" / "github_push_payload.json").read_bytes()


@pytest.fixture()
async def client_with_db(async_session: AsyncSession, monkeypatch: pytest.MonkeyPatch):
    """pslog_FERNET_KEY + DB override 적용한 ASGI 클라이언트."""
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


async def _seed_project_with_secret(
    db: AsyncSession, repo_url: str, secret: str | None
) -> Project:
    ws = Workspace(name="ws", slug=f"ws-{uuid.uuid4().hex[:8]}")
    db.add(ws)
    await db.flush()
    proj = Project(
        workspace_id=ws.id,
        name="p",
        git_repo_url=repo_url,
        webhook_secret_encrypted=encrypt_secret(secret) if secret else None,
    )
    db.add(proj)
    await db.commit()
    await db.refresh(proj)
    return proj


def _sign(body: bytes, secret: str) -> str:
    mac = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={mac}"


async def test_webhook_valid_signature_returns_200(
    client_with_db, async_session: AsyncSession
):
    secret = "valid-secret"
    proj = await _seed_project_with_secret(
        async_session, "https://github.com/ardenspace/app-chak", secret
    )
    sig = _sign(FIXTURE, secret)
    res = await client_with_db.post(
        "/api/v1/webhooks/github",
        content=FIXTURE,
        headers={
            "X-Hub-Signature-256": sig,
            "X-GitHub-Event": "push",
            "Content-Type": "application/json",
        },
    )
    assert res.status_code == 200

    rows = (
        await async_session.execute(
            select(GitPushEvent).where(GitPushEvent.project_id == proj.id)
        )
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].head_commit_sha == json.loads(FIXTURE)["head_commit"]["id"]


async def test_webhook_invalid_signature_returns_401(
    client_with_db, async_session: AsyncSession
):
    proj = await _seed_project_with_secret(
        async_session, "https://github.com/ardenspace/app-chak", "real-secret"
    )
    bad_sig = _sign(FIXTURE, "wrong-secret")
    res = await client_with_db.post(
        "/api/v1/webhooks/github",
        content=FIXTURE,
        headers={"X-Hub-Signature-256": bad_sig, "X-GitHub-Event": "push"},
    )
    assert res.status_code == 401

    # body 미저장 — DB row 없어야
    rows = (
        await async_session.execute(
            select(GitPushEvent).where(GitPushEvent.project_id == proj.id)
        )
    ).scalars().all()
    assert len(rows) == 0


async def test_webhook_unknown_repo_returns_200(
    client_with_db, async_session: AsyncSession
):
    """알 수 없는 repo: 200 + 경고 로그 (GitHub 재전송 방지)."""
    # 다른 repo URL의 Project 만 있음
    await _seed_project_with_secret(
        async_session, "https://github.com/other/repo", "secret"
    )
    res = await client_with_db.post(
        "/api/v1/webhooks/github",
        content=FIXTURE,
        headers={"X-Hub-Signature-256": "sha256=anything", "X-GitHub-Event": "push"},
    )
    assert res.status_code == 200

    rows = (await async_session.execute(select(GitPushEvent))).scalars().all()
    assert len(rows) == 0


async def test_webhook_missing_signature_returns_401(
    client_with_db, async_session: AsyncSession
):
    await _seed_project_with_secret(
        async_session, "https://github.com/ardenspace/app-chak", "secret"
    )
    res = await client_with_db.post(
        "/api/v1/webhooks/github",
        content=FIXTURE,
        headers={"X-GitHub-Event": "push"},
    )
    assert res.status_code == 401


async def test_webhook_duplicate_push_idempotent(
    client_with_db, async_session: AsyncSession
):
    secret = "valid-secret"
    proj = await _seed_project_with_secret(
        async_session, "https://github.com/ardenspace/app-chak", secret
    )
    sig = _sign(FIXTURE, secret)
    headers = {"X-Hub-Signature-256": sig, "X-GitHub-Event": "push"}

    res1 = await client_with_db.post(
        "/api/v1/webhooks/github", content=FIXTURE, headers=headers
    )
    res2 = await client_with_db.post(
        "/api/v1/webhooks/github", content=FIXTURE, headers=headers
    )
    assert res1.status_code == 200
    assert res2.status_code == 200

    rows = (
        await async_session.execute(
            select(GitPushEvent).where(GitPushEvent.project_id == proj.id)
        )
    ).scalars().all()
    assert len(rows) == 1


async def test_webhook_project_without_secret_returns_401(
    client_with_db, async_session: AsyncSession
):
    """git_repo_url은 매칭되지만 webhook_secret_encrypted 가 NULL → 401."""
    await _seed_project_with_secret(
        async_session, "https://github.com/ardenspace/app-chak", secret=None
    )
    res = await client_with_db.post(
        "/api/v1/webhooks/github",
        content=FIXTURE,
        headers={"X-Hub-Signature-256": "sha256=x", "X-GitHub-Event": "push"},
    )
    assert res.status_code == 401


async def test_webhook_decrypt_failure_returns_500(
    async_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
):
    """Fernet 마스터 키 회전 후 기존 webhook_secret_encrypted 복호화 실패 → 500.

    설계서 §8: DB/decrypt 실패는 500 (operator signal, GitHub 자동 재시도).
    이 path 는 로컬 키 mismatch 만 발생 — 일상적 4xx 와 분리.
    """
    import importlib
    import app.config
    import app.core.crypto

    # Step 1: encrypt 'real-secret' under master key A
    monkeypatch.setenv("pslog_FERNET_KEY", Fernet.generate_key().decode())
    importlib.reload(app.config)
    importlib.reload(app.core.crypto)
    from app.core.crypto import encrypt_secret as encrypt_a

    ws = Workspace(name="ws", slug=f"ws-{uuid.uuid4().hex[:8]}")
    async_session.add(ws)
    await async_session.flush()
    proj = Project(
        workspace_id=ws.id,
        name="p",
        git_repo_url="https://github.com/ardenspace/app-chak",
        webhook_secret_encrypted=encrypt_a("real-secret"),
    )
    async_session.add(proj)
    await async_session.commit()

    # Step 2: rotate master key to B, reload — old ciphertext now undecryptable
    monkeypatch.setenv("pslog_FERNET_KEY", Fernet.generate_key().decode())
    importlib.reload(app.config)
    importlib.reload(app.core.crypto)

    from app.main import app
    from app.database import get_db

    async def override_get_db():
        yield async_session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # any signature — request never reaches signature verification
        res = await client.post(
            "/api/v1/webhooks/github",
            content=FIXTURE,
            headers={"X-Hub-Signature-256": "sha256=anything", "X-GitHub-Event": "push"},
        )
    app.dependency_overrides.clear()

    assert res.status_code == 500
    # body 미저장 — decrypt 실패는 INSERT 전 단계
    rows = (
        await async_session.execute(
            select(GitPushEvent).where(GitPushEvent.project_id == proj.id)
        )
    ).scalars().all()
    assert len(rows) == 0


async def test_webhook_non_push_event_returns_200_ignored(
    client_with_db, async_session: AsyncSession
):
    """X-GitHub-Event != 'push' → 200 ACK + status=ignored. (GitHub ping 등)"""
    res = await client_with_db.post(
        "/api/v1/webhooks/github",
        content=FIXTURE,
        headers={"X-GitHub-Event": "ping"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body == {"status": "ignored", "event": "ping"}

    # DB 미저장
    rows = (await async_session.execute(select(GitPushEvent))).scalars().all()
    assert len(rows) == 0


async def test_webhook_triggers_background_sync(
    client_with_db, async_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
):
    """webhook 정상 처리 → BackgroundTasks 가 sync_service.process_event 호출."""
    secret = "valid-secret"
    proj = await _seed_project_with_secret(
        async_session, "https://github.com/ardenspace/app-chak", secret
    )
    sig = _sign(FIXTURE, secret)

    called_with: list[str] = []

    async def fake_run_sync(event_id):
        called_with.append(str(event_id))

    import app.api.v1.endpoints.webhooks as webhooks_module
    monkeypatch.setattr(webhooks_module, "_run_sync_in_new_session", fake_run_sync)

    res = await client_with_db.post(
        "/api/v1/webhooks/github",
        content=FIXTURE,
        headers={"X-Hub-Signature-256": sig, "X-GitHub-Event": "push"},
    )
    assert res.status_code == 200
    # ASGITransport awaits BackgroundTasks before returning
    assert len(called_with) == 1


# --- pull_request 이벤트 (결정 미승격 A) ---

def _pr_body(repo_url: str, action: str = "opened") -> bytes:
    return (
        '{"action":"%s",'
        '"repository":{"id":1,"full_name":"ardenspace/app-chak","html_url":"%s"},'
        '"pull_request":{"number":7,'
        '"head":{"ref":"feat/x","sha":"%s"},'
        '"base":{"ref":"main","sha":"%s"}}}'
        % (action, repo_url, "a" * 40, "b" * 40)
    ).encode()


async def test_pr_webhook_valid_triggers_drift_a(
    client_with_db, async_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
):
    secret = "valid-secret"
    repo = "https://github.com/ardenspace/app-chak"
    await _seed_project_with_secret(async_session, repo, secret)
    body = _pr_body(repo)
    sig = _sign(body, secret)

    called: list[tuple] = []

    async def fake_run_drift_a(project_id, branch, head_sha, base_sha):
        called.append((branch, head_sha, base_sha))

    import app.api.v1.endpoints.webhooks as webhooks_module
    monkeypatch.setattr(webhooks_module, "_run_drift_a", fake_run_drift_a)

    res = await client_with_db.post(
        "/api/v1/webhooks/github",
        content=body,
        headers={"X-Hub-Signature-256": sig, "X-GitHub-Event": "pull_request"},
    )
    assert res.status_code == 200
    assert res.json()["status"] == "pr_received"
    assert called == [("feat/x", "a" * 40, "b" * 40)]


async def test_pr_webhook_bad_signature_401(
    client_with_db, async_session: AsyncSession
):
    repo = "https://github.com/ardenspace/app-chak"
    await _seed_project_with_secret(async_session, repo, "real-secret")
    body = _pr_body(repo)
    res = await client_with_db.post(
        "/api/v1/webhooks/github",
        content=body,
        headers={"X-Hub-Signature-256": _sign(body, "wrong"),
                 "X-GitHub-Event": "pull_request"},
    )
    assert res.status_code == 401


async def test_pr_webhook_ignored_action(
    client_with_db, async_session: AsyncSession
):
    """action=closed 등은 평가 안 함 — 200 + ignored_action."""
    repo = "https://github.com/ardenspace/app-chak"
    secret = "valid-secret"
    await _seed_project_with_secret(async_session, repo, secret)
    body = _pr_body(repo, action="closed")
    res = await client_with_db.post(
        "/api/v1/webhooks/github",
        content=body,
        headers={"X-Hub-Signature-256": _sign(body, secret),
                 "X-GitHub-Event": "pull_request"},
    )
    assert res.status_code == 200
    assert res.json()["status"] == "ignored_action"
