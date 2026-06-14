from fastapi import APIRouter

from app.api.v1.endpoints.auth import router as auth_router
from app.api.v1.endpoints.tasks import router as tasks_router
from app.api.v1.endpoints.workspaces import router as workspaces_router
from app.api.v1.endpoints.projects import router as projects_router
from app.api.v1.endpoints.share_links import router as share_links_router
from app.api.v1.endpoints.discord import router as discord_router
from app.api.v1.endpoints.webhooks import router as webhooks_router
from app.api.v1.endpoints.git_settings import router as git_settings_router
from app.api.v1.endpoints.log_tokens import router as log_tokens_router
from app.api.v1.endpoints.log_ingest import router as log_ingest_router
from app.api.v1.endpoints.log_errors import router as log_errors_router
from app.api.v1.endpoints.log_logs import router as log_logs_router
from app.api.v1.endpoints.log_health import router as log_health_router
from app.api.v1.endpoints.drifts import router as drifts_router

api_v1_router = APIRouter()
api_v1_router.include_router(auth_router)
api_v1_router.include_router(tasks_router)
api_v1_router.include_router(workspaces_router)
api_v1_router.include_router(projects_router)
api_v1_router.include_router(share_links_router)
api_v1_router.include_router(discord_router)
api_v1_router.include_router(webhooks_router)
api_v1_router.include_router(git_settings_router)
api_v1_router.include_router(log_tokens_router)
api_v1_router.include_router(log_ingest_router)
api_v1_router.include_router(log_errors_router)
api_v1_router.include_router(log_logs_router)
api_v1_router.include_router(log_health_router)
api_v1_router.include_router(drifts_router)
