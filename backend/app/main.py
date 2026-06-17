import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.api.v1.router import api_v1_router
from app.database import AsyncSessionLocal
from app.services.discord_service import start_weekly_scheduler
from app.services.git_repo_service import fetch_compare_files, fetch_file
from app.services import log_fingerprint_reaper
from app.services.push_event_reaper import reap_pending_events
from app.services.sync_service import process_event

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: 미처리 push event 회수 (Phase 4 — sync_service 콜백 주입)
    try:
        from sqlalchemy import select

        from app.models.git_push_event import GitPushEvent

        async def _cb(ev: GitPushEvent) -> None:
            # I-3 fix: 이벤트마다 fresh session — 한 이벤트의 세션 poison 이 다음으로 전파 안 되게
            event_id = ev.id
            async with AsyncSessionLocal() as inner_db:
                refetched = (await inner_db.execute(
                    select(GitPushEvent).where(GitPushEvent.id == event_id)
                )).scalar_one_or_none()
                if refetched is None:
                    return
                await process_event(
                    inner_db, refetched,
                    fetch_file=fetch_file,
                    fetch_compare=fetch_compare_files,
                )

        async with AsyncSessionLocal() as outer_db:
            reaped = await reap_pending_events(outer_db, _cb)

        if reaped:
            logger.info("startup reaper picked up %d pending push events", reaped)
    except Exception:
        logger.exception("startup reaper failed")

    # Phase 3 — log_fingerprint_reaper: 미처리 ERROR↑ LogEvent 회수
    try:
        await log_fingerprint_reaper.run_reaper_once()
    except Exception:
        logger.exception("log_fingerprint_reaper failed at startup")

    # Startup: 주간 리포트 스케줄러 시작
    scheduler_task = asyncio.create_task(start_weekly_scheduler())
    yield
    # Shutdown: 스케줄러 정리
    scheduler_task.cancel()


app = FastAPI(
    title="pslog API",
    description="B2B Task Management & Collaboration Tool",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins.split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
async def root():
    return {"message": "pslog API is running"}


@app.head("/health")
@app.get("/health")
async def health():
    return {"status": "ok"}


app.include_router(api_v1_router, prefix="/api/v1")
