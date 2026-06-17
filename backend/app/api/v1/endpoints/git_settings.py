"""git-settings endpoints — 프로젝트별 git 연동 설정.

설계서: 2026-04-26-ai-task-automation-design.md §5.2, §9
"""

import logging
from uuid import UUID

from cryptography.fernet import InvalidToken
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_project_member
from app.config import settings
from app.core.crypto import decrypt_secret, encrypt_secret, generate_webhook_secret
from app.database import get_db
from app.models.git_push_event import GitPushEvent
from app.models.handoff import Handoff
from app.models.workspace import WorkspaceRole
from app.schemas.git_settings import (
    GitEventSummary,
    GitSettingsResponse,
    GitSettingsUpdate,
    HandoffSummary,
    ReprocessResponse,
    WebhookRegisterResponse,
)
from app.services import github_hook_service, project_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/projects", tags=["git-settings"])


def _public_webhook_url() -> str:
    base = settings.pslog_public_url.rstrip("/")
    return f"{base}/api/v1/webhooks/github"


def _normalize_url(url: str) -> str:
    return url.rstrip("/").lower()


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
        public_webhook_url=_public_webhook_url(),
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

    data = update.model_dump(exclude_unset=True)
    for key in (
        "git_repo_url",
        "git_default_branch",
        "plan_path",
        "handoff_dir",
        "handoff_skip_branches",
    ):
        if key in data:
            setattr(project, key, data[key])
    if "github_pat" in data and data["github_pat"]:
        project.github_pat_encrypted = encrypt_secret(data["github_pat"])

    await db.commit()
    await db.refresh(project)

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

    project.discord_consecutive_failures = 0
    project.discord_disabled_at = None
    await db.commit()
    await db.refresh(project)

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

    # B1 / I-2: 권한 검증 후 row lock 획득.
    # 동시 OWNER 호출 시 후행은 여기서 대기 → 선행 commit 후 webhook_secret_encrypted 갱신본 보고 진행.
    # final commit 시 lock release. process_event 와 같은 단일 outer commit 구조라 안전.
    await db.refresh(project, with_for_update={"nowait": False})

    if not project.git_repo_url:
        raise HTTPException(status_code=400, detail="git_repo_url 미설정")
    if project.github_pat_encrypted is None:
        raise HTTPException(status_code=400, detail="GitHub PAT 미설정")

    try:
        pat = decrypt_secret(project.github_pat_encrypted)
    except InvalidToken:
        logger.error("PAT 복호화 실패 — Fernet 마스터 키 mismatch project=%s", project_id)
        raise HTTPException(status_code=500, detail="PAT 복호화 실패")

    callback_url = _public_webhook_url()
    new_secret = generate_webhook_secret()

    existing_hooks = await github_hook_service.list_hooks(project.git_repo_url, pat)
    target = _normalize_url(callback_url)
    matching = next(
        (h for h in existing_hooks if _normalize_url(h.get("config", {}).get("url") or "") == target),
        None,
    )

    if matching is not None:
        hook = await github_hook_service.update_hook(
            project.git_repo_url, pat,
            hook_id=matching["id"],
            callback_url=callback_url,
            secret=new_secret,
        )
        was_existing = True
    else:
        hook = await github_hook_service.create_hook(
            project.git_repo_url, pat,
            callback_url=callback_url,
            secret=new_secret,
        )
        was_existing = False

    project.webhook_secret_encrypted = encrypt_secret(new_secret)
    await db.commit()

    return WebhookRegisterResponse(
        webhook_id=hook["id"],
        was_existing=was_existing,
        public_webhook_url=callback_url,
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
    limit = max(1, min(limit, 200))

    stmt = (
        select(Handoff)
        .where(Handoff.project_id == project_id)
        .order_by(Handoff.pushed_at.desc())
        .limit(limit)
    )
    if branch is not None:
        stmt = stmt.where(Handoff.branch == branch)

    rows = (await db.execute(stmt)).scalars().all()
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
    limit = max(1, min(limit, 200))

    stmt = (
        select(GitPushEvent)
        .where(GitPushEvent.project_id == project_id)
        .order_by(GitPushEvent.received_at.desc(), GitPushEvent.id.desc())
        .limit(limit)
    )
    if failed_only:
        stmt = stmt.where(
            GitPushEvent.processed_at.is_not(None),
            GitPushEvent.error.is_not(None),
        )

    rows = (await db.execute(stmt)).scalars().all()
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
    event = await db.get(GitPushEvent, event_id)
    if event is None or event.project_id != project_id:
        raise HTTPException(status_code=404, detail="Event not found")

    # B1 / I-4 layer 1: in-flight 거부.
    # processed_at IS NULL = 초기 BackgroundTask 가 아직 처리 중이거나 reaper 회수 대기.
    # 이 시점에 reprocess 트리거하면 두 process_event 가 같은 event 로 동시 실행 → TaskEvent 중복.
    # reaper 가 5분 grace 후 회수 가능하므로 사용자는 기다리거나 재처리 대신 reaper 에 위임.
    if event.processed_at is None:
        raise HTTPException(
            status_code=409,
            detail="Event is still being processed — please try again shortly",
        )

    if event.processed_at is not None and event.error is None:
        raise HTTPException(
            status_code=400,
            detail="Event already processed successfully — nothing to reprocess",
        )

    event.processed_at = None
    event.error = None
    await db.commit()

    # BackgroundTask — Phase 4 webhook endpoint 의 _run_sync_in_new_session 재사용.
    # module-level 참조를 통해 호출해야 테스트 monkeypatch 가 동작함.
    from app.api.v1.endpoints import webhooks as webhooks_module
    background_tasks.add_task(webhooks_module._run_sync_in_new_session, event_id)

    return ReprocessResponse(event_id=event_id, status="queued")
