"""auth endpoint 통합 테스트 — register / login / me / patch me."""

import pytest
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.fixture()
async def client(async_session: AsyncSession, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("pslog_FERNET_KEY", Fernet.generate_key().decode())
    import importlib
    import app.config
    importlib.reload(app.config)

    from app.database import get_db
    from app.main import app

    async def override_get_db():
        yield async_session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


async def _register_and_token(
    client: AsyncClient,
    *,
    email: str = "user@test.com",
    name: str = "user",
    password: str = "secret123",
) -> str:
    res = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "name": name, "password": password},
    )
    assert res.status_code == 201, res.text
    return res.json()["token"]["access_token"]


async def test_patch_me_sets_username(client: AsyncClient):
    token = await _register_and_token(client)
    res = await client.patch(
        "/api/v1/auth/me",
        json={"username": "arden"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["username"] == "arden"

    me = await client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
    )
    assert me.json()["username"] == "arden"


async def test_patch_me_rejects_invalid_format(client: AsyncClient):
    """Uppercase / 짧음 / 특수문자 → 422 (Pydantic pattern)."""
    token = await _register_and_token(client)
    for bad in ["Arden", "a", "한글", "user!"]:
        res = await client.patch(
            "/api/v1/auth/me",
            json={"username": bad},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res.status_code == 422, f"{bad!r} should be rejected"


async def test_patch_me_username_conflict(client: AsyncClient):
    """다른 사람이 이미 차지한 username 으로 변경 시 409."""
    token_a = await _register_and_token(
        client, email="a@test.com", name="a", password="secret123"
    )
    token_b = await _register_and_token(
        client, email="b@test.com", name="b", password="secret123"
    )

    res_a = await client.patch(
        "/api/v1/auth/me",
        json={"username": "shared"},
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert res_a.status_code == 200

    res_b = await client.patch(
        "/api/v1/auth/me",
        json={"username": "shared"},
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert res_b.status_code == 409


async def test_patch_me_idempotent_same_username(client: AsyncClient):
    """본인이 이미 가진 username 그대로 PATCH → 200, 충돌 아님."""
    token = await _register_and_token(client)
    await client.patch(
        "/api/v1/auth/me",
        json={"username": "arden"},
        headers={"Authorization": f"Bearer {token}"},
    )
    res = await client.patch(
        "/api/v1/auth/me",
        json={"username": "arden"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200
    assert res.json()["username"] == "arden"
