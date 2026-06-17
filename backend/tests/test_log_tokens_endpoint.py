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
