"""PATCH /errors/{group_id} 통합 테스트.

설계서: 2026-04-26-error-log-design.md §4.1, 5.2
"""

import asyncio
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
    assert body["resolved_at"] is not None
    assert body["resolved_in_version_sha"] == sha
    assert body["resolved_by_user_id"] == str(user.id)


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


# ---------------------------------------------------------------------------
# I-1: PATCH row lock — 동시 OWNER 요청 직렬화
# ---------------------------------------------------------------------------


async def test_concurrent_patch_serializes(
    async_session: AsyncSession, monkeypatch: pytest.MonkeyPatch, upgraded_db,
):
    """두 OWNER 가 동시에 PATCH (resolve vs ignore) → row lock 으로 직렬화.

    T1 이 lock 보유 중 T2 진입 → T2 는 T1 commit 후 읽은 상태로 전이 시도.
    T1 이 OPEN→RESOLVED 완료하면 T2 가 읽는 status 는 RESOLVED → ignore 불법 전이 → 400.
    최종 DB 상태: status == RESOLVED, resolved_at NOT NULL (T1 에 의해).
    """
    from cryptography.fernet import Fernet as _Fernet
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    monkeypatch.setenv("pslog_FERNET_KEY", _Fernet.generate_key().decode())
    import importlib
    import app.config as _config_mod
    importlib.reload(_config_mod)
    import app.core.crypto as _crypto_mod
    importlib.reload(_crypto_mod)

    # seed 데이터를 async_session(shared connection) 으로 생성 후 upgraded_db DSN 으로 검증
    user, proj, group = await _seed(async_session)
    project_id = proj.id
    group_id = group.id
    user_id = user.id

    # T1 이 transition_status 안에서 lock 보유 중 T2 가 SELECT FOR UPDATE 에서 대기하도록 제어
    t1_inside = asyncio.Event()
    release_t1 = asyncio.Event()

    import app.services.error_group_service as eg_svc
    original_transition = eg_svc.transition_status

    call_count = {"n": 0}

    async def slow_transition(db, group_obj, *, action, user_id, resolved_in_version_sha):
        call_count["n"] += 1
        if call_count["n"] == 1:
            # T1 첫 호출: lock 이 이미 SELECT FOR UPDATE 로 획득된 상태.
            # T2 가 같은 row 의 FOR UPDATE 에서 대기하도록 신호 + 잠시 점유.
            t1_inside.set()
            await release_t1.wait()
        return await original_transition(
            db, group_obj,
            action=action, user_id=user_id,
            resolved_in_version_sha=resolved_in_version_sha,
        )

    monkeypatch.setattr(eg_svc, "transition_status", slow_transition)

    dsn = upgraded_db["async_url"]
    engine_a = create_async_engine(dsn, echo=False)
    engine_b = create_async_engine(dsn, echo=False)
    maker_a = async_sessionmaker(engine_a, expire_on_commit=False)
    maker_b = async_sessionmaker(engine_b, expire_on_commit=False)

    from app.services.auth_service import create_access_token
    tok = create_access_token({"sub": str(user_id)})
    auth_header = {"Authorization": f"Bearer {tok}"}

    results: dict[str, int] = {}

    async def call_patch(maker, action: str, label: str) -> None:
        from app.main import app as _app
        from app.database import get_db

        async def _override_db():
            async with maker() as db:
                yield db

        _app.dependency_overrides[get_db] = _override_db
        transport = ASGITransport(app=_app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.patch(
                f"/api/v1/projects/{project_id}/errors/{group_id}",
                json={"action": action},
                headers=auth_header,
            )
        _app.dependency_overrides.pop(get_db, None)
        results[label] = resp.status_code

    async def releaser():
        # T1 이 lock 보유 상태 확인 후 T2 도 진입할 시간 줌, 그 다음 T1 해제.
        await t1_inside.wait()
        await asyncio.sleep(0.4)
        release_t1.set()

    try:
        t1 = asyncio.create_task(call_patch(maker_a, "resolve", "resolve"))
        await asyncio.sleep(0.05)
        t2 = asyncio.create_task(call_patch(maker_b, "ignore", "ignore"))
        rel = asyncio.create_task(releaser())
        await asyncio.gather(t1, t2, rel)
    finally:
        await engine_a.dispose()
        await engine_b.dispose()

    # 정확히 하나만 성공, 나머지는 400 (illegal transition) 또는 200
    statuses = list(results.values())
    assert 200 in statuses, f"둘 다 실패: {results}"
    assert 400 in statuses, (
        f"row lock 없으면 last-writer-wins → 둘 다 200 가능 (results={results})"
    )

    # DB 최종 상태 일관성 검증 — upgraded_db DSN 으로 직접 조회
    check_engine = create_async_engine(dsn, echo=False)
    try:
        from sqlalchemy import select as sa_select
        async with async_sessionmaker(check_engine, expire_on_commit=False)() as chk:
            final_group = (
                await chk.execute(
                    sa_select(ErrorGroup).where(ErrorGroup.id == group_id)
                )
            ).scalar_one()
            # resolve 가 이겼으면 resolved_at 필드 set, ignore 가 이겼으면 None
            if final_group.status == ErrorGroupStatus.RESOLVED:
                assert final_group.resolved_at is not None, "RESOLVED 인데 resolved_at NULL"
            elif final_group.status == ErrorGroupStatus.IGNORED:
                assert final_group.resolved_at is None, "IGNORED 인데 resolved_at SET"
            else:
                pytest.fail(f"예상 외 최종 status: {final_group.status}")
    finally:
        await check_engine.dispose()
