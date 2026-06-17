"""log-errors endpoint 통합 테스트.

설계서: 2026-05-01-error-log-phase4-query-design.md §3.3
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


def _make_group(proj: Project, *, fingerprint: str = "fp-1") -> ErrorGroup:
    now = datetime.utcnow()
    return ErrorGroup(
        project_id=proj.id, fingerprint=fingerprint,
        exception_class="KeyError", exception_message_sample="x",
        first_seen_at=now, first_seen_version_sha="a" * 40,
        last_seen_at=now, last_seen_version_sha="a" * 40,
        event_count=1, status=ErrorGroupStatus.OPEN,
    )


async def test_list_errors_normal(client_with_db, async_session: AsyncSession):
    """GET /errors 정상 — 멤버, items + total."""
    user, proj = await _seed_user_project(async_session)
    g = _make_group(proj)
    async_session.add(g)
    await async_session.commit()

    token = _auth_token(user)
    res = await client_with_db.get(
        f"/api/v1/projects/{proj.id}/errors",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["total"] == 1
    assert len(body["items"]) == 1
    assert body["items"][0]["fingerprint"] == "fp-1"


async def test_list_errors_404_for_non_member(
    client_with_db, async_session: AsyncSession,
):
    """비-멤버 → 404."""
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
        f"/api/v1/projects/{proj.id}/errors",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 404


async def test_list_errors_viewer_can_access(client_with_db, async_session: AsyncSession):
    """VIEWER 권한 — 조회 가능."""
    user, proj = await _seed_user_project(async_session, role=WorkspaceRole.VIEWER)
    g = _make_group(proj)
    async_session.add(g)
    await async_session.commit()

    token = _auth_token(user)
    res = await client_with_db.get(
        f"/api/v1/projects/{proj.id}/errors",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200


async def test_get_error_detail_normal(client_with_db, async_session: AsyncSession):
    """GET /errors/{group_id} 정상 — group + recent_events + git_context."""
    user, proj = await _seed_user_project(async_session)
    g = _make_group(proj)
    async_session.add(g)
    await async_session.commit()
    await async_session.refresh(g)

    token = _auth_token(user)
    res = await client_with_db.get(
        f"/api/v1/projects/{proj.id}/errors/{g.id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["group"]["id"] == str(g.id)
    assert body["recent_events"] == []  # 시드한 LogEvent 없음
    assert body["git_context"]["first_seen"]["handoffs"] == []
    assert body["git_context"]["previous_good_sha"] is None


async def test_get_error_detail_404_for_other_project(
    client_with_db, async_session: AsyncSession,
):
    """다른 project 의 group_id → 404."""
    user, proj_a = await _seed_user_project(async_session)
    _, proj_b = await _seed_user_project(async_session)
    g = _make_group(proj_b)
    async_session.add(g)
    await async_session.commit()
    await async_session.refresh(g)

    token = _auth_token(user)
    res = await client_with_db.get(
        f"/api/v1/projects/{proj_a.id}/errors/{g.id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 404
