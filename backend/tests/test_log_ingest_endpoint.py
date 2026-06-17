"""log-ingest endpoint 통합 테스트.

설계서: 2026-05-01-error-log-phase2-ingest-design.md §3.2
"""

import gzip
import json
import uuid
from datetime import datetime
from unittest.mock import patch

import bcrypt
import pytest
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.log_event import LogEvent
from app.models.log_ingest_token import LogIngestToken
from app.models.project import Project
from app.models.workspace import Workspace


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


async def _seed_token(
    db: AsyncSession,
    *,
    secret: str = "test-secret-256bit-base64-urlsafe-fake",
    revoked: bool = False,
    rate_limit_per_minute: int = 600,
) -> tuple[Project, LogIngestToken, str]:
    ws = Workspace(name="ws", slug=f"ws-{uuid.uuid4().hex[:8]}")
    db.add(ws)
    await db.flush()
    proj = Project(workspace_id=ws.id, name="p")
    db.add(proj)
    await db.flush()

    secret_hash = bcrypt.hashpw(secret.encode(), bcrypt.gensalt(rounds=4)).decode()
    token = LogIngestToken(
        project_id=proj.id,
        name="test",
        secret_hash=secret_hash,
        rate_limit_per_minute=rate_limit_per_minute,
    )
    if revoked:
        token.revoked_at = datetime.utcnow()
    db.add(token)
    await db.commit()
    await db.refresh(proj)
    await db.refresh(token)
    return proj, token, secret


def _valid_event() -> dict:
    return {
        "level": "ERROR",
        "message": "boom",
        "logger_name": "app.x",
        "version_sha": "a" * 40,
        "environment": "production",
        "hostname": "h1",
        "emitted_at": "2026-05-01T10:30:00Z",
    }


async def test_ingest_normal_200(client_with_db, async_session: AsyncSession):
    """정상 ingest → 200 + accepted/rejected."""
    proj, token, secret = await _seed_token(async_session)
    bearer = f"Bearer {token.id}.{secret}"
    res = await client_with_db.post(
        "/api/v1/log-ingest",
        json={"events": [_valid_event(), _valid_event()]},
        headers={"Authorization": bearer},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["accepted"] == 2
    assert body["rejected"] == []

    from sqlalchemy import select
    rows = (await async_session.execute(
        select(LogEvent).where(LogEvent.project_id == proj.id)
    )).scalars().all()
    assert len(rows) == 2


async def test_ingest_all_invalid_400(client_with_db, async_session: AsyncSession):
    """모든 event invalid → 400 + rejected list."""
    proj, token, secret = await _seed_token(async_session)
    bearer = f"Bearer {token.id}.{secret}"
    bad = _valid_event()
    bad["version_sha"] = "abc"
    res = await client_with_db.post(
        "/api/v1/log-ingest",
        json={"events": [bad, bad]},
        headers={"Authorization": bearer},
    )
    assert res.status_code == 400
    body = res.json()
    assert body["accepted"] == 0
    assert len(body["rejected"]) == 2


async def test_ingest_gzip_body(client_with_db, async_session: AsyncSession):
    """Content-Encoding: gzip 정상 처리."""
    proj, token, secret = await _seed_token(async_session)
    bearer = f"Bearer {token.id}.{secret}"
    raw = json.dumps({"events": [_valid_event()]}).encode("utf-8")
    compressed = gzip.compress(raw)
    res = await client_with_db.post(
        "/api/v1/log-ingest",
        content=compressed,
        headers={
            "Authorization": bearer,
            "Content-Encoding": "gzip",
            "Content-Type": "application/json",
        },
    )
    assert res.status_code == 200
    assert res.json()["accepted"] == 1


async def test_ingest_gzip_decode_fail_400(client_with_db, async_session: AsyncSession):
    """잘못된 gzip byte → 400."""
    proj, token, secret = await _seed_token(async_session)
    bearer = f"Bearer {token.id}.{secret}"
    res = await client_with_db.post(
        "/api/v1/log-ingest",
        content=b"not-gzip-data",
        headers={
            "Authorization": bearer,
            "Content-Encoding": "gzip",
            "Content-Type": "application/json",
        },
    )
    assert res.status_code == 400


async def test_ingest_auth_failures_401(client_with_db, async_session: AsyncSession):
    """인증 실패 4 case 모두 401."""
    proj, token, secret = await _seed_token(async_session)
    payload = {"events": [_valid_event()]}

    # 1. Authorization 헤더 없음
    res = await client_with_db.post("/api/v1/log-ingest", json=payload)
    assert res.status_code == 401

    # 2. 형식 깨짐 (분리자 . 없음)
    res = await client_with_db.post(
        "/api/v1/log-ingest", json=payload,
        headers={"Authorization": "Bearer noseparator"},
    )
    assert res.status_code == 401

    # 3. 잘못된 secret (bcrypt fail)
    res = await client_with_db.post(
        "/api/v1/log-ingest", json=payload,
        headers={"Authorization": f"Bearer {token.id}.wrong-secret"},
    )
    assert res.status_code == 401

    # 4. revoked token
    proj2, token2, secret2 = await _seed_token(async_session, revoked=True)
    res = await client_with_db.post(
        "/api/v1/log-ingest", json=payload,
        headers={"Authorization": f"Bearer {token2.id}.{secret2}"},
    )
    assert res.status_code == 401


async def test_ingest_rate_limit_429(client_with_db, async_session: AsyncSession):
    """rate limit 초과 → 429 + Retry-After 헤더."""
    proj, token, secret = await _seed_token(async_session, rate_limit_per_minute=2)
    bearer = f"Bearer {token.id}.{secret}"
    res = await client_with_db.post(
        "/api/v1/log-ingest",
        json={"events": [_valid_event() for _ in range(5)]},  # batch_size 5 > limit 2
        headers={"Authorization": bearer},
    )
    assert res.status_code == 429
    assert "Retry-After" in res.headers or "retry-after" in res.headers
    retry_after = int(res.headers.get("Retry-After") or res.headers.get("retry-after"))
    assert retry_after >= 1


async def test_ingest_payload_malformed_400(client_with_db, async_session: AsyncSession):
    """JSON parse fail → 400 / events 키 없음 → 400."""
    proj, token, secret = await _seed_token(async_session)
    bearer = f"Bearer {token.id}.{secret}"

    # JSON parse fail
    res = await client_with_db.post(
        "/api/v1/log-ingest",
        content=b"not-json{",
        headers={"Authorization": bearer, "Content-Type": "application/json"},
    )
    assert res.status_code == 400

    # events 키 없음
    res = await client_with_db.post(
        "/api/v1/log-ingest",
        json={"other": []},
        headers={"Authorization": bearer},
    )
    assert res.status_code == 400


async def test_ingest_db_failure_500(client_with_db, async_session: AsyncSession):
    """insert_events 가 raise → 500."""
    proj, token, secret = await _seed_token(async_session)
    bearer = f"Bearer {token.id}.{secret}"

    async def boom(*args, **kwargs):
        raise RuntimeError("db down")

    with patch("app.services.log_ingest_service.insert_events", side_effect=boom):
        res = await client_with_db.post(
            "/api/v1/log-ingest",
            json={"events": [_valid_event()]},
            headers={"Authorization": bearer},
        )
    assert res.status_code == 500


@pytest.mark.asyncio
async def test_ingest_schedules_background_task_for_error_events_only(
    client_with_db, async_session: AsyncSession,
):
    """ERROR/CRITICAL event 만 BackgroundTask 큐. INFO/WARNING 은 큐 안 됨."""
    proj, token, secret = await _seed_token(async_session)
    bearer = f"Bearer {token.id}.{secret}"

    # 3 events: INFO / ERROR / CRITICAL
    events = [_valid_event() for _ in range(3)]
    events[0]["level"] = "INFO"
    events[1]["level"] = "ERROR"
    events[2]["level"] = "CRITICAL"

    scheduled: list = []

    async def fake_helper(event_id) -> None:
        """BackgroundTask 대신 실행 — 실제 DB 없이 event_id 만 기록."""
        scheduled.append(event_id)

    with patch(
        "app.api.v1.endpoints.log_ingest._process_log_event_in_new_session",
        side_effect=fake_helper,
    ):
        res = await client_with_db.post(
            "/api/v1/log-ingest",
            json={"events": events},
            headers={"Authorization": bearer},
        )

    assert res.status_code == 200
    assert res.json()["accepted"] == 3
    # ERROR + CRITICAL 만 큐 — INFO 는 안 됨
    assert len(scheduled) == 2
