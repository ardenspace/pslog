"""drifts endpoint 통합 테스트.

설계서: 2026-06-14-decision-truth-loop-design.md §5.5
"""

import uuid
from datetime import datetime

import pytest
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.drift import Drift, DriftStatus, DriftType
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


def _make_drift(proj: Project, *, status=DriftStatus.OPEN) -> Drift:
    return Drift(
        project_id=proj.id, type=DriftType.STATUS_CONTRADICTION, status=status,
        branch="feat/x", external_id="task-007", dedup_key="feat/x:task-007",
        detail="PLAN DONE인데 handoff 미완", opened_at=datetime.utcnow(),
    )


async def test_list_drifts_open_filter(client_with_db, async_session: AsyncSession):
    user, proj = await _seed_user_project(async_session)
    async_session.add(_make_drift(proj))
    await async_session.commit()

    token = _auth_token(user)
    res = await client_with_db.get(
        f"/api/v1/projects/{proj.id}/drifts?status=open",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["total"] == 1
    assert data["items"][0]["external_id"] == "task-007"
    assert data["items"][0]["status"] == "open"


async def test_patch_drift_ignore(client_with_db, async_session: AsyncSession):
    user, proj = await _seed_user_project(async_session)
    drift = _make_drift(proj)
    async_session.add(drift)
    await async_session.commit()
    await async_session.refresh(drift)

    token = _auth_token(user)
    res = await client_with_db.patch(
        f"/api/v1/projects/{proj.id}/drifts/{drift.id}",
        json={"action": "ignore"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200
    assert res.json()["status"] == "ignored"

    row = (await async_session.execute(
        select(Drift).where(Drift.id == drift.id)
    )).scalar_one()
    await async_session.refresh(row)
    assert row.status == DriftStatus.IGNORED


async def test_patch_drift_unknown_action_400(client_with_db, async_session: AsyncSession):
    user, proj = await _seed_user_project(async_session)
    drift = _make_drift(proj)
    async_session.add(drift)
    await async_session.commit()
    await async_session.refresh(drift)

    token = _auth_token(user)
    res = await client_with_db.patch(
        f"/api/v1/projects/{proj.id}/drifts/{drift.id}",
        json={"action": "frobnicate"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 400
