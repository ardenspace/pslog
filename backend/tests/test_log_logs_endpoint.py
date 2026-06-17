"""log-logs endpoint 통합 테스트.

설계서: 2026-05-01-error-log-phase4-query-design.md §3.3
"""

import uuid
from datetime import datetime

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


async def _seed_user_project(
    db: AsyncSession, role: WorkspaceRole = WorkspaceRole.OWNER,
) -> tuple[User, Project]:
    user = User(
        email=f"u-{uuid.uuid4().hex[:8]}@example.com",
        name="alice", password_hash="x",
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


def _make_log_event(proj: Project, *, level: LogLevel = LogLevel.ERROR, message: str = "boom") -> LogEvent:
    return LogEvent(
        project_id=proj.id, level=level,
        message=message, logger_name="app.x", version_sha="a" * 40,
        environment="production", hostname="h",
        emitted_at=datetime.utcnow(), received_at=datetime.utcnow(),
        exception_class="KeyError", exception_message="x",
    )


async def test_list_logs_normal(client_with_db, async_session: AsyncSession):
    """GET /logs 정상 — items + total."""
    user, proj = await _seed_user_project(async_session)
    e = _make_log_event(proj)
    async_session.add(e)
    await async_session.commit()

    token = _auth_token(user)
    res = await client_with_db.get(
        f"/api/v1/projects/{proj.id}/logs",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["total"] == 1
    assert len(body["items"]) == 1


async def test_list_logs_404_for_non_member(
    client_with_db, async_session: AsyncSession,
):
    user, proj = await _seed_user_project(async_session)
    other = User(
        email=f"o-{uuid.uuid4().hex[:8]}@example.com",
        name="bob", password_hash="x",
    )
    async_session.add(other)
    await async_session.commit()
    await async_session.refresh(other)

    token = _auth_token(other)
    res = await client_with_db.get(
        f"/api/v1/projects/{proj.id}/logs",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 404


async def test_list_logs_q_full_text(client_with_db, async_session: AsyncSession):
    """q 풀텍스트 — level >= WARNING 자동."""
    user, proj = await _seed_user_project(async_session)
    e1 = _make_log_event(proj, level=LogLevel.ERROR, message="contains needle here")
    e2 = _make_log_event(proj, level=LogLevel.INFO, message="info with needle")  # 제외 — INFO
    e3 = _make_log_event(proj, level=LogLevel.ERROR, message="no match")  # 제외 — q 매칭 X
    async_session.add_all([e1, e2, e3])
    await async_session.commit()

    token = _auth_token(user)
    res = await client_with_db.get(
        f"/api/v1/projects/{proj.id}/logs",
        params={"q": "needle"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["total"] == 1
    assert body["items"][0]["message"] == "contains needle here"
