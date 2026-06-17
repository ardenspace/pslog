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
