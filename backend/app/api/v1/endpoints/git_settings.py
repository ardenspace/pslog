"""git-settings endpoints — 프로젝트별 git 연동 설정.

설계서: 2026-04-26-ai-task-automation-design.md §5.2, §9
"""

from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_project_member
from app.config import settings  # noqa: F401 — 테스트가 이 모듈 경로로 pslog_public_url 을 monkeypatch 함
from app.database import get_db
from app.models.workspace import WorkspaceRole
from app.schemas.git_settings import (
    GitEventSummary,
    GitSettingsResponse,
    GitSettingsUpdate,
    HandoffSummary,
    ReprocessResponse,
    WebhookRegisterResponse,
)
from app.services import git_settings_service, project_service

router = APIRouter(prefix="/projects", tags=["git-settings"])


def _build_git_settings_response(project) -> GitSettingsResponse:
    """GitSettings 응답 builder — GET/PATCH/discord-reset 공통 (DRY)."""
    return GitSettingsResponse(
        git_repo_url=project.git_repo_url,
        git_default_branch=project.git_default_branch,
        plan_path=project.plan_path,
        handoff_dir=project.handoff_dir,
        last_synced_commit_sha=project.last_synced_commit_sha,
        has_webhook_secret=project.webhook_secret_encrypted is not None,
        has_github_pat=project.github_pat_encrypted is not None,
        public_webhook_url=git_settings_service.public_webhook_url(),
        # Phase 6
        discord_enabled=(
            project.discord_webhook_url is not None
            and project.discord_disabled_at is None
        ),
        discord_disabled_at=project.discord_disabled_at,
        discord_consecutive_failures=project.discord_consecutive_failures,
        handoff_skip_branches=project.handoff_skip_branches,
    )


@router.get("/{project_id}/git-settings", response_model=GitSettingsResponse)
async def get_git_settings(
    project_id: UUID,
    db: AsyncSession = Depends(get_db),
    _role: WorkspaceRole = Depends(require_project_member(hide_existence=True)),
):
    project = await project_service.get_project(db, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    return _build_git_settings_response(project)


@router.patch("/{project_id}/git-settings", response_model=GitSettingsResponse)
async def patch_git_settings(
    project_id: UUID,
    update: GitSettingsUpdate,
    db: AsyncSession = Depends(get_db),
    _role: WorkspaceRole = Depends(require_project_member(
        min_role=WorkspaceRole.OWNER,
        hide_existence=True,
        denied_detail="Owner only",
    )),
):
    project = await project_service.get_project(db, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    await git_settings_service.update_settings(
        db, project, update.model_dump(exclude_unset=True)
    )

    return _build_git_settings_response(project)


@router.post(
    "/{project_id}/git-settings/discord-reset",
    response_model=GitSettingsResponse,
)
async def reset_discord(
    project_id: UUID,
    db: AsyncSession = Depends(get_db),
    _role: WorkspaceRole = Depends(require_project_member(
        min_role=WorkspaceRole.OWNER,
        hide_existence=True,
        denied_detail="Owner only",
    )),
):
    """Discord 알림 비활성화 해제 — counter 0 + disabled_at NULL.
    설계서: 2026-05-01-phase-6-discord-notifications-design.md §3.5
    """
    project = await project_service.get_project(db, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    await git_settings_service.reset_discord(db, project)

    return _build_git_settings_response(project)


@router.post(
    "/{project_id}/git-settings/webhook",
    response_model=WebhookRegisterResponse,
)
async def register_webhook(
    project_id: UUID,
    db: AsyncSession = Depends(get_db),
    _role: WorkspaceRole = Depends(require_project_member(
        min_role=WorkspaceRole.OWNER,
        hide_existence=True,
        denied_detail="Owner only",
    )),
):
    """GitHub repo 에 push webhook 자동 등록 (또는 갱신).

    - 같은 callback url 의 hook 이 있으면 PATCH (config.secret 갱신)
    - 없으면 POST (신규 등록)
    - 새 webhook_secret 항상 생성 — 기존 secret 무효화 (regenerate 의 부수 효과)
    """
    project = await project_service.get_project(db, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    try:
        result = await git_settings_service.register_webhook(db, project)
    except git_settings_service.WebhookConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except git_settings_service.PatDecryptError as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    return WebhookRegisterResponse(
        webhook_id=result.hook_id,
        was_existing=result.was_existing,
        public_webhook_url=result.callback_url,
    )


@router.get("/{project_id}/handoffs", response_model=list[HandoffSummary])
async def list_handoffs(
    project_id: UUID,
    db: AsyncSession = Depends(get_db),
    branch: str | None = None,
    limit: int = 50,
    _role: WorkspaceRole = Depends(require_project_member(hide_existence=True)),
):
    """프로젝트의 handoff 이력 조회 (branch 필터 + limit clamp)."""
    rows = await git_settings_service.list_handoffs(
        db, project_id, branch=branch, limit=limit
    )
    return [
        HandoffSummary(
            id=h.id,
            branch=h.branch,
            author_git_login=h.author_git_login,
            commit_sha=h.commit_sha,
            pushed_at=h.pushed_at,
            parsed_tasks_count=len(h.parsed_tasks or []),
        )
        for h in rows
    ]


@router.get(
    "/{project_id}/git-events",
    response_model=list[GitEventSummary],
)
async def list_git_events(
    project_id: UUID,
    db: AsyncSession = Depends(get_db),
    failed_only: bool = True,
    limit: int = 50,
    _role: WorkspaceRole = Depends(require_project_member(hide_existence=True)),
):
    """프로젝트의 git push event list — v1 은 failed only 가 의미 있는 case.

    설계서: 2026-05-01-phase-5-followup-b2-design.md §2.3
    """
    rows = await git_settings_service.list_git_events(
        db, project_id, failed_only=failed_only, limit=limit
    )
    return [
        GitEventSummary(
            id=e.id,
            branch=e.branch,
            head_commit_sha=e.head_commit_sha,
            pusher=e.pusher,
            received_at=e.received_at,
            processed_at=e.processed_at,
            error=e.error,
        )
        for e in rows
    ]


@router.post(
    "/{project_id}/git-events/{event_id}/reprocess",
    response_model=ReprocessResponse,
)
async def reprocess_git_event(
    project_id: UUID,
    event_id: UUID,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    _role: WorkspaceRole = Depends(require_project_member(
        min_role=WorkspaceRole.OWNER,
        hide_existence=True,
        denied_detail="Owner only",
    )),
):
    """실패한 git push event 를 수동으로 재처리 큐에 추가 (OWNER 전용)."""
    try:
        await git_settings_service.get_reprocessable_event(db, project_id, event_id)
    except git_settings_service.EventNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except git_settings_service.EventInFlightError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except git_settings_service.EventAlreadyProcessedError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # BackgroundTask — Phase 4 webhook endpoint 의 _run_sync_in_new_session 재사용.
    # module-level 참조를 통해 호출해야 테스트 monkeypatch 가 동작함.
    from app.api.v1.endpoints import webhooks as webhooks_module
    background_tasks.add_task(webhooks_module._run_sync_in_new_session, event_id)

    return ReprocessResponse(event_id=event_id, status="queued")
