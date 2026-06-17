"""log_ingest_service 단위 테스트.

설계서: 2026-05-01-error-log-phase2-ingest-design.md §3.1
"""

import asyncio
import uuid
from datetime import datetime, timedelta

import bcrypt
import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.log_ingest_token import LogIngestToken
from app.models.workspace import Workspace
from app.models.project import Project
from app.services import log_ingest_service


async def _seed_project_and_token(
    db: AsyncSession,
    *,
    secret: str = "test-secret-256bit",
    revoked: bool = False,
    rate_limit_per_minute: int = 600,
) -> tuple[Project, LogIngestToken, str]:
    """Workspace + Project + LogIngestToken 시드. 반환: (project, token, plain_secret)."""
    ws = Workspace(name="ws", slug=f"ws-{uuid.uuid4().hex[:8]}")
    db.add(ws)
    await db.flush()
    proj = Project(workspace_id=ws.id, name="p")
    db.add(proj)
    await db.flush()

    secret_hash = bcrypt.hashpw(secret.encode(), bcrypt.gensalt(rounds=4)).decode()
    token = LogIngestToken(
        project_id=proj.id,
        name="test-token",
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


# ---- parse_token ----

async def test_parse_token_no_header():
    """Authorization 헤더 None → 401."""
    with pytest.raises(HTTPException) as exc:
        await log_ingest_service.parse_token(None)
    assert exc.value.status_code == 401
    assert exc.value.detail == "Invalid token"


async def test_parse_token_no_dot_separator():
    """Bearer 다음에 . 분리자 없음 → 401."""
    with pytest.raises(HTTPException) as exc:
        await log_ingest_service.parse_token("Bearer just-secret-no-dot")
    assert exc.value.status_code == 401


async def test_parse_token_invalid_uuid():
    """key_id 가 UUID 아님 → 401."""
    with pytest.raises(HTTPException) as exc:
        await log_ingest_service.parse_token("Bearer notauuid.somesecret")
    assert exc.value.status_code == 401


async def test_parse_token_valid():
    """Bearer <uuid>.<secret> → (uuid_obj, secret_str)."""
    key_id = uuid.uuid4()
    secret = "the-secret"
    parsed_id, parsed_secret = await log_ingest_service.parse_token(
        f"Bearer {key_id}.{secret}"
    )
    assert parsed_id == key_id
    assert parsed_secret == secret


# ---- verify_token ----

async def test_verify_token_lookup_fail(async_session: AsyncSession):
    """key_id 가 DB 에 없음 → 401."""
    with pytest.raises(HTTPException) as exc:
        await log_ingest_service.verify_token(async_session, uuid.uuid4(), "any-secret")
    assert exc.value.status_code == 401


async def test_verify_token_revoked(async_session: AsyncSession):
    """revoked_at set → 401."""
    proj, token, secret = await _seed_project_and_token(async_session, revoked=True)
    with pytest.raises(HTTPException) as exc:
        await log_ingest_service.verify_token(async_session, token.id, secret)
    assert exc.value.status_code == 401


async def test_verify_token_bcrypt_fail(async_session: AsyncSession):
    """잘못된 secret → 401."""
    proj, token, secret = await _seed_project_and_token(async_session)
    with pytest.raises(HTTPException) as exc:
        await log_ingest_service.verify_token(async_session, token.id, "wrong-secret")
    assert exc.value.status_code == 401


async def test_verify_token_success_and_last_used(async_session: AsyncSession):
    """정상 verify → token 반환 + last_used_at 갱신 (in-memory)."""
    proj, token, secret = await _seed_project_and_token(async_session)
    assert token.last_used_at is None

    verified = await log_ingest_service.verify_token(async_session, token.id, secret)
    assert verified.id == token.id
    assert verified.last_used_at is not None


# ---- check_rate_limit ----

async def test_check_rate_limit_first_call_inserts_window(async_session: AsyncSession):
    """첫 호출 → RateLimitWindow row INSERT, event_count == batch_size."""
    proj, token, _ = await _seed_project_and_token(async_session, rate_limit_per_minute=600)
    now = datetime(2026, 5, 1, 10, 30, 45)

    await log_ingest_service.check_rate_limit(
        async_session, project_id=proj.id, token=token, batch_size=5, now=now,
    )

    from app.models.rate_limit_window import RateLimitWindow
    expected_window = datetime(2026, 5, 1, 10, 30, 0)  # 분 truncate
    row = await async_session.get(
        RateLimitWindow, (proj.id, token.id, expected_window)
    )
    assert row is not None
    assert row.event_count == 5


async def test_check_rate_limit_same_minute_accumulates(async_session: AsyncSession):
    """같은 분 재호출 → event_count 누적."""
    proj, token, _ = await _seed_project_and_token(async_session, rate_limit_per_minute=600)
    now = datetime(2026, 5, 1, 10, 30, 12)

    await log_ingest_service.check_rate_limit(
        async_session, project_id=proj.id, token=token, batch_size=5, now=now,
    )
    await log_ingest_service.check_rate_limit(
        async_session, project_id=proj.id, token=token, batch_size=3,
        now=datetime(2026, 5, 1, 10, 30, 47),  # 같은 분
    )

    from app.models.rate_limit_window import RateLimitWindow
    row = await async_session.get(
        RateLimitWindow, (proj.id, token.id, datetime(2026, 5, 1, 10, 30, 0))
    )
    assert row.event_count == 8


async def test_check_rate_limit_exceeds_raises_429(async_session: AsyncSession):
    """event_count > limit → 429 + Retry-After 헤더 (최대 60)."""
    proj, token, _ = await _seed_project_and_token(async_session, rate_limit_per_minute=10)
    now = datetime(2026, 5, 1, 10, 30, 30)

    with pytest.raises(HTTPException) as exc:
        await log_ingest_service.check_rate_limit(
            async_session, project_id=proj.id, token=token, batch_size=11, now=now,
        )
    assert exc.value.status_code == 429
    assert exc.value.detail == "Rate limit exceeded"
    # Retry-After: 30초 남음 (60초 - 30초 경과)
    retry_after = int(exc.value.headers["Retry-After"])
    assert 1 <= retry_after <= 60


async def test_check_rate_limit_next_minute_new_row(async_session: AsyncSession):
    """다음 분 호출 → 새 RateLimitWindow row (event_count = batch_size)."""
    proj, token, _ = await _seed_project_and_token(async_session, rate_limit_per_minute=600)

    await log_ingest_service.check_rate_limit(
        async_session, project_id=proj.id, token=token, batch_size=5,
        now=datetime(2026, 5, 1, 10, 30, 30),
    )
    await log_ingest_service.check_rate_limit(
        async_session, project_id=proj.id, token=token, batch_size=3,
        now=datetime(2026, 5, 1, 10, 31, 5),  # 다음 분
    )

    from app.models.rate_limit_window import RateLimitWindow
    row1 = await async_session.get(
        RateLimitWindow, (proj.id, token.id, datetime(2026, 5, 1, 10, 30, 0))
    )
    row2 = await async_session.get(
        RateLimitWindow, (proj.id, token.id, datetime(2026, 5, 1, 10, 31, 0))
    )
    assert row1.event_count == 5
    assert row2.event_count == 3


# ---- validate_event ----

def _valid_event_dict() -> dict:
    """테스트용 정상 event dict — 모든 필수 필드 포함."""
    return {
        "level": "ERROR",
        "message": "test error",
        "logger_name": "app.test",
        "version_sha": "a" * 40,
        "environment": "production",
        "hostname": "host-1",
        "emitted_at": "2026-05-01T10:30:00Z",
    }


def test_validate_event_valid_returns_log_event():
    """정상 event dict → (LogEvent, None)."""
    proj_id = uuid.uuid4()
    log_event, rejection = log_ingest_service.validate_event(
        _valid_event_dict(), index=0, project_id=proj_id,
    )
    assert log_event is not None
    assert rejection is None
    assert log_event.project_id == proj_id
    assert log_event.message == "test error"
    assert log_event.version_sha == "a" * 40


def test_validate_event_unknown_version_sha_ok():
    """version_sha == 'unknown' 정상."""
    d = _valid_event_dict()
    d["version_sha"] = "unknown"
    log_event, rejection = log_ingest_service.validate_event(d, 0, uuid.uuid4())
    assert log_event is not None
    assert rejection is None


def test_validate_event_short_sha_rejected():
    """version_sha short SHA → reject."""
    d = _valid_event_dict()
    d["version_sha"] = "abc1234"  # 7자, 40자 아님
    log_event, rejection = log_ingest_service.validate_event(d, 5, uuid.uuid4())
    assert log_event is None
    assert rejection == {"index": 5, "reason": "version_sha format invalid"}


def test_validate_event_extra_field_rejected():
    """Pydantic schema 위배 (extra='forbid') → reject."""
    d = _valid_event_dict()
    d["unknown_field"] = "boom"
    log_event, rejection = log_ingest_service.validate_event(d, 2, uuid.uuid4())
    assert log_event is None
    assert rejection["index"] == 2
    assert "unknown_field" in rejection["reason"] or "extra" in rejection["reason"].lower()


def test_validate_event_oversized_extra_rejected():
    """extra > 4KB → reject."""
    d = _valid_event_dict()
    d["extra"] = {"k": "x" * 5000}  # > 4KB
    log_event, rejection = log_ingest_service.validate_event(d, 1, uuid.uuid4())
    assert log_event is None
    assert rejection["index"] == 1
    assert "extra" in rejection["reason"].lower()


# ---- insert_events ----

async def test_insert_events_batch_inserts_with_null_fingerprint(async_session: AsyncSession):
    """batch INSERT → 모든 row 의 fingerprint=NULL."""
    proj, token, _ = await _seed_project_and_token(async_session)
    from app.models.log_event import LogEvent, LogLevel

    events = [
        LogEvent(
            project_id=proj.id,
            level=LogLevel.ERROR,
            message=f"msg-{i}",
            logger_name="app.test",
            version_sha="a" * 40,
            environment="production",
            hostname="host-1",
            emitted_at=datetime.utcnow(),
        )
        for i in range(5)
    ]

    inserted = await log_ingest_service.insert_events(async_session, events)
    assert inserted == 5

    from sqlalchemy import select
    rows = (await async_session.execute(
        select(LogEvent).where(LogEvent.project_id == proj.id)
    )).scalars().all()
    assert len(rows) == 5
    for row in rows:
        assert row.fingerprint is None
        assert row.fingerprinted_at is None


# ---- ingest_batch ----

async def test_ingest_batch_partial_success(async_session: AsyncSession, caplog):
    """10 events 중 8 valid 2 invalid → accepted=8, rejected=2건. DB 8 행."""
    proj, token, _ = await _seed_project_and_token(async_session)

    events = [_valid_event_dict() for _ in range(10)]
    events[2]["version_sha"] = "abc"  # short SHA reject
    events[7]["unknown_field"] = "x"  # extra field reject

    accepted, rejected, _accepted_ids = await log_ingest_service.ingest_batch(
        async_session, token=token,
        payload_dict={"events": events},
        dropped_since_last=None,
    )

    assert accepted == 8
    assert len(rejected) == 2
    rejected_indices = {r["index"] for r in rejected}
    assert rejected_indices == {2, 7}

    # DB 8 행
    from sqlalchemy import select
    from app.models.log_event import LogEvent
    rows = (await async_session.execute(
        select(LogEvent).where(LogEvent.project_id == proj.id)
    )).scalars().all()
    assert len(rows) == 8

    # last_used_at 갱신 (commit 됨)
    await async_session.refresh(token)
    assert token.last_used_at is not None


async def test_ingest_batch_dropped_header_logs_warning(
    async_session: AsyncSession, caplog,
):
    """X-pslog-Dropped-Since-Last 받으면 logger.warning."""
    import logging
    proj, token, _ = await _seed_project_and_token(async_session)

    with caplog.at_level(logging.WARNING, logger="app.services.log_ingest_service"):
        await log_ingest_service.ingest_batch(
            async_session, token=token,
            payload_dict={"events": [_valid_event_dict()]},
            dropped_since_last=42,
        )

    assert any(
        "dropped" in record.message.lower() and "42" in record.message
        for record in caplog.records
    )
