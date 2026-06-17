"""POST /log-ingest — 외부 (app-chak) 가 호출하는 log batch ingest.

설계서: 2026-05-01-error-log-phase2-ingest-design.md §3.2
"""

import gzip
import json
import logging

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal, get_db
from app.models.log_event import LogEvent, LogLevel
from app.services import log_ingest_service

logger = logging.getLogger(__name__)

router = APIRouter(tags=["log-ingest"])


async def _process_log_event_in_new_session(event_id) -> None:
    """BackgroundTask — 자체 session, fingerprint_processor 호출, 멱등."""
    from app.services import fingerprint_processor

    try:
        async with AsyncSessionLocal() as db:
            event = await db.get(LogEvent, event_id)
            if event is None or event.fingerprinted_at is not None:
                return
            await fingerprint_processor.process(db, event)
    except Exception:
        logger.exception("background fingerprint processing failed for event %s", event_id)


@router.post("/log-ingest")
async def ingest_logs(
    request: Request,
    background_tasks: BackgroundTasks,
    authorization: str | None = Header(default=None),
    content_encoding: str | None = Header(default=None),
    x_pslog_dropped_since_last: int | None = Header(default=None, alias="X-pslog-Dropped-Since-Last"),
    db: AsyncSession = Depends(get_db),
):
    """외부 앱이 로그 batch 를 push.

    응답:
    - 200: 정상 또는 부분 성공 (accepted, rejected)
    - 400: gzip / JSON parse fail / events 키 없음 / 모든 event invalid
    - 401: 인증 실패 (사유 구분 안 함, timing attack 회피)
    - 429: rate limit 초과 (Retry-After 헤더)
    - 500: DB 쓰기 실패
    """
    body = await request.body()

    if content_encoding == "gzip":
        try:
            body = gzip.decompress(body)
        except Exception:
            raise HTTPException(status_code=400, detail="gzip decode failed")

    try:
        payload = json.loads(body)
    except Exception:
        raise HTTPException(status_code=400, detail="invalid JSON body")

    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="payload must be a JSON object")

    # 토큰 검증 (HTTPException 401 raise 시 그대로 propagate)
    key_id, secret = await log_ingest_service.parse_token(authorization)
    token = await log_ingest_service.verify_token(db, key_id, secret)

    # ingest_batch 가 rate limit + validate + insert + commit 처리
    try:
        accepted, rejected, accepted_ids = await log_ingest_service.ingest_batch(
            db, token=token,
            payload_dict=payload,
            dropped_since_last=x_pslog_dropped_since_last,
        )
    except HTTPException:
        # rate limit 429 / events 빈 list 400 등 그대로 propagate
        raise
    except Exception:
        logger.exception("log-ingest unexpected error")
        raise HTTPException(status_code=500, detail="Internal error")

    # Phase 3 — ERROR↑ event 만 BackgroundTask 큐 (fingerprint 처리 trigger)
    if accepted_ids:
        error_stmt = (
            select(LogEvent.id)
            .where(LogEvent.id.in_(accepted_ids))
            .where(LogEvent.level.in_([LogLevel.ERROR, LogLevel.CRITICAL]))
        )
        error_ids = (await db.execute(error_stmt)).scalars().all()
        for eid in error_ids:
            background_tasks.add_task(_process_log_event_in_new_session, eid)

    # 모두 invalid → 400
    if accepted == 0 and rejected:
        return JSONResponse(
            status_code=400,
            content={"accepted": 0, "rejected": rejected},
        )

    return {"accepted": accepted, "rejected": rejected}
