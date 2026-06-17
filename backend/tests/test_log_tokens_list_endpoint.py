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
