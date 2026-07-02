"""git-settings 비즈니스 로직 — 라우터(endpoints/git_settings.py)와 분리.

설계서: 2026-04-26-ai-task-automation-design.md §5.2, §9
spec: docs/tasks/git-settings-service/spec.md

에러 전달: HTTP 를 모르는 도메인 예외를 raise 하고 라우터가 상태코드로 매핑한다.
commit/refresh 는 서비스가 담당 (project_service 관습).
"""

import logging
from typing import NamedTuple
from uuid import UUID

from cryptography.fernet import InvalidToken
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.crypto import decrypt_secret, encrypt_secret, generate_webhook_secret
from app.models.git_push_event import GitPushEvent
from app.models.handoff import Handoff
from app.models.project import Project
from app.services import github_hook_service

logger = logging.getLogger(__name__)

LIST_LIMIT_MAX = 200

UPDATABLE_FIELDS = (
    "git_repo_url",
    "git_default_branch",
    "plan_path",
    "handoff_dir",
    "handoff_skip_branches",
)


class GitSettingsError(Exception):
    """git-settings 도메인 예외 베이스."""


class WebhookConfigError(GitSettingsError):
    """webhook 등록 전제조건 미충족 (repo URL / PAT 미설정) → 400."""


class PatDecryptError(GitSettingsError):
    """PAT 복호화 실패 — Fernet 마스터 키 mismatch → 500."""


class EventNotFoundError(GitSettingsError):
    """event 없음 또는 다른 프로젝트 소속 → 404."""


class EventInFlightError(GitSettingsError):
    """event 가 아직 처리 중 (processed_at IS NULL) → 409."""


class EventAlreadyProcessedError(GitSettingsError):
    """event 가 이미 성공 처리됨 (error IS NULL) → 400."""


class WebhookResult(NamedTuple):
    hook_id: int
    was_existing: bool
    callback_url: str


def public_webhook_url() -> str:
    base = settings.pslog_public_url.rstrip("/")
    return f"{base}/api/v1/webhooks/github"


def _normalize_url(url: str) -> str:
    return url.rstrip("/").lower()


async def register_webhook(db: AsyncSession, project: Project) -> WebhookResult:
    """GitHub repo 에 push webhook 자동 등록 (또는 갱신).

    - 같은 callback url 의 hook 이 있으면 PATCH (config.secret 갱신)
    - 없으면 POST (신규 등록)
    - 새 webhook_secret 항상 생성 — 기존 secret 무효화 (regenerate 의 부수 효과)

    Raises: WebhookConfigError (전제조건 미충족), PatDecryptError (키 mismatch).
    """
    # B1 / I-2: 권한 검증 후 row lock 획득.
    # 동시 OWNER 호출 시 후행은 여기서 대기 → 선행 commit 후 webhook_secret_encrypted 갱신본 보고 진행.
    # final commit 시 lock release. process_event 와 같은 단일 outer commit 구조라 안전.
    await db.refresh(project, with_for_update={"nowait": False})

    if not project.git_repo_url:
        raise WebhookConfigError("git_repo_url 미설정")
    if project.github_pat_encrypted is None:
        raise WebhookConfigError("GitHub PAT 미설정")

    try:
        pat = decrypt_secret(project.github_pat_encrypted)
    except InvalidToken:
        logger.error("PAT 복호화 실패 — Fernet 마스터 키 mismatch project=%s", project.id)
        raise PatDecryptError("PAT 복호화 실패")

    callback_url = public_webhook_url()
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

    return WebhookResult(
        hook_id=hook["id"],
        was_existing=was_existing,
        callback_url=callback_url,
    )


async def get_reprocessable_event(
    db: AsyncSession, project_id: UUID, event_id: UUID
) -> GitPushEvent:
    """실패한 git push event 를 재처리 가능 상태로 리셋.

    Raises:
        EventNotFoundError: event 없음 / 다른 프로젝트 소속.
        EventInFlightError: 아직 처리 중 (processed_at IS NULL) — 동시 실행 방지.
        EventAlreadyProcessedError: 이미 성공 처리됨 — 재처리 대상 아님.
    """
    event = await db.get(GitPushEvent, event_id)
    if event is None or event.project_id != project_id:
        raise EventNotFoundError("Event not found")

    # B1 / I-4 layer 1: in-flight 거부.
    # processed_at IS NULL = 초기 BackgroundTask 가 아직 처리 중이거나 reaper 회수 대기.
    # 이 시점에 reprocess 트리거하면 두 process_event 가 같은 event 로 동시 실행 → TaskEvent 중복.
    # reaper 가 5분 grace 후 회수 가능하므로 사용자는 기다리거나 재처리 대신 reaper 에 위임.
    if event.processed_at is None:
        raise EventInFlightError(
            "Event is still being processed — please try again shortly"
        )

    if event.processed_at is not None and event.error is None:
        raise EventAlreadyProcessedError(
            "Event already processed successfully — nothing to reprocess"
        )

    event.processed_at = None
    event.error = None
    await db.commit()

    return event


async def update_settings(db: AsyncSession, project: Project, data: dict) -> None:
    """PATCH 필드 반영 — 평문 필드 + PAT 암호화 저장."""
    for key in UPDATABLE_FIELDS:
        if key in data:
            setattr(project, key, data[key])
    if "github_pat" in data and data["github_pat"]:
        project.github_pat_encrypted = encrypt_secret(data["github_pat"])

    await db.commit()
    await db.refresh(project)


async def reset_discord(db: AsyncSession, project: Project) -> None:
    """Discord 알림 비활성화 해제 — counter 0 + disabled_at NULL.

    설계서: 2026-05-01-phase-6-discord-notifications-design.md §3.5
    """
    project.discord_consecutive_failures = 0
    project.discord_disabled_at = None
    await db.commit()
    await db.refresh(project)


async def list_handoffs(
    db: AsyncSession,
    project_id: UUID,
    *,
    branch: str | None = None,
    limit: int = 50,
) -> list[Handoff]:
    """handoff 이력 조회 — branch 필터 + limit clamp."""
    limit = max(1, min(limit, LIST_LIMIT_MAX))

    stmt = (
        select(Handoff)
        .where(Handoff.project_id == project_id)
        .order_by(Handoff.pushed_at.desc())
        .limit(limit)
    )
    if branch is not None:
        stmt = stmt.where(Handoff.branch == branch)

    return list((await db.execute(stmt)).scalars().all())


async def list_git_events(
    db: AsyncSession,
    project_id: UUID,
    *,
    failed_only: bool = True,
    limit: int = 50,
) -> list[GitPushEvent]:
    """git push event 목록 — v1 은 failed only 가 의미 있는 case.

    설계서: 2026-05-01-phase-5-followup-b2-design.md §2.3
    """
    limit = max(1, min(limit, LIST_LIMIT_MAX))

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

    return list((await db.execute(stmt)).scalars().all())
