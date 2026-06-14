"""GitHub push webhook 수신 endpoint.

설계서: 2026-04-26-ai-task-automation-design.md §5.2, §7.1, §8
응답 정책:
  - 401: 서명 검증 실패 (또는 secret 없음 / signature 헤더 없음)
  - 200 + 경고 로그: 알 수 없는 repo (GitHub 재전송 방지)
  - 200: 정상 + GitPushEvent INSERT (중복 commit_sha 도 200, 멱등성)
  - 500: DB 쓰기 실패 (GitHub 자동 재시도)
"""

import logging

from cryptography.fernet import InvalidToken
from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt_secret
from app.database import AsyncSessionLocal, get_db
from app.schemas.webhook import GitHubPullRequestPayload, GitHubPushPayload
from app.services.git_repo_service import fetch_compare_files, fetch_file
from app.services.github_webhook_service import (
    find_project_by_repo_url,
    record_push_event,
    verify_signature,
)
from app.services.sync_service import process_event
from app.models.git_push_event import GitPushEvent

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/github")
async def receive_github_push(
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    x_hub_signature_256: str | None = Header(default=None, alias="X-Hub-Signature-256"),
    x_github_event: str | None = Header(default=None, alias="X-GitHub-Event"),
):
    """GitHub push webhook 수신.

    흐름: body 읽기 → payload 파싱 → repo 매칭 → secret decrypt → 서명 검증 → INSERT.
    Phase 2 범위: raw 보존만. 파싱/sync는 Phase 4.
    """
    body = await request.body()

    # pull_request 이벤트 — 결정 미승격(A) 평가
    if x_github_event == "pull_request":
        return await _handle_pull_request(
            background_tasks, db, body, x_hub_signature_256
        )

    # push 이벤트만 처리 — 다른 이벤트는 200 ACK + skip
    if x_github_event != "push":
        return {"status": "ignored", "event": x_github_event}

    try:
        payload = GitHubPushPayload.model_validate_json(body)
    except ValueError:
        # 깨진 payload — 400 (재전송 의미 없음)
        raise HTTPException(status_code=400, detail="Invalid push payload")

    project = await find_project_by_repo_url(db, payload.repository.html_url)
    if project is None:
        # 알 수 없는 repo: 200 + 경고 로그 (재전송 방지)
        logger.warning(
            "github webhook for unknown repo: %s", payload.repository.html_url
        )
        return {"status": "unknown_repo"}

    if project.webhook_secret_encrypted is None:
        # repo는 등록됐지만 secret 미설정 — 검증 불가, 401
        logger.warning("project %s has git_repo_url but no webhook secret", project.id)
        raise HTTPException(status_code=401, detail="Webhook secret not configured")

    try:
        secret = decrypt_secret(project.webhook_secret_encrypted)
    except InvalidToken:
        logger.error(
            "failed to decrypt webhook secret for project %s — Fernet master key mismatch",
            project.id,
        )
        raise HTTPException(status_code=500, detail="Secret decryption failed")

    if not verify_signature(body, x_hub_signature_256, secret):
        logger.warning(
            "github webhook signature verification failed for project %s", project.id
        )
        raise HTTPException(status_code=401, detail="Invalid signature")

    event = await record_push_event(db, project, payload)
    if event is not None:
        background_tasks.add_task(_run_sync_in_new_session, event.id)
    return {"status": "received", "event_id": str(event.id) if event else None}


async def _run_sync_in_new_session(event_id):
    """BackgroundTask 진입점 — 자체 세션 + 실제 fetcher 주입."""
    try:
        async with AsyncSessionLocal() as db:
            event = (await db.execute(
                select(GitPushEvent).where(GitPushEvent.id == event_id)
            )).scalar_one_or_none()
            if event is None:
                return
            await process_event(
                db, event,
                fetch_file=fetch_file,
                fetch_compare=fetch_compare_files,
            )
    except Exception:
        logger.exception("background sync failed for event %s", event_id)


# PR 열림 시 action — 이 외(예: closed)는 skip
_PR_EVAL_ACTIONS = {"opened", "reopened", "synchronize", "ready_for_review"}


async def _handle_pull_request(background_tasks, db, body, signature):
    """pull_request 이벤트 → 서명 검증 후 결정 미승격(A) 평가를 백그라운드로."""
    from app.services import drift_service  # noqa: F401  (참조 보장)

    try:
        payload = GitHubPullRequestPayload.model_validate_json(body)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid PR payload")

    if payload.action not in _PR_EVAL_ACTIONS:
        return {"status": "ignored_action", "action": payload.action}

    project = await find_project_by_repo_url(db, payload.repository.html_url)
    if project is None:
        logger.warning("PR webhook for unknown repo: %s", payload.repository.html_url)
        return {"status": "unknown_repo"}
    if project.webhook_secret_encrypted is None:
        logger.warning("project %s has git_repo_url but no webhook secret", project.id)
        raise HTTPException(status_code=401, detail="Webhook secret not configured")
    try:
        secret = decrypt_secret(project.webhook_secret_encrypted)
    except InvalidToken:
        logger.error("failed to decrypt webhook secret for project %s", project.id)
        raise HTTPException(status_code=500, detail="Secret decryption failed")
    if not verify_signature(body, signature, secret):
        logger.warning("PR webhook signature verification failed for project %s", project.id)
        raise HTTPException(status_code=401, detail="Invalid signature")

    pr = payload.pull_request
    background_tasks.add_task(
        _run_drift_a, project.id, pr.head.ref, pr.head.sha, pr.base.sha
    )
    return {"status": "pr_received", "number": pr.number}


async def _run_drift_a(project_id, branch, head_sha, base_sha):
    """BackgroundTask — 결정 미승격(A) 평가 + 신규 OPEN Discord 알림."""
    from app.models.project import Project
    from app.services import drift_service, notification_dispatcher

    try:
        async with AsyncSessionLocal() as db:
            project = await db.get(Project, project_id)
            if project is None:
                return
            newly = await drift_service.detect_unpromoted_decisions(
                db, project=project, branch=branch,
                head_sha=head_sha, base_sha=base_sha,
                fetch_file=fetch_file, fetch_compare=fetch_compare_files,
            )
            await db.commit()
            content = drift_service.format_drift_alert(newly)
            if content:
                try:
                    await notification_dispatcher.dispatch_discord_alert(db, project, content)
                except Exception:
                    logger.exception("drift(A) alert dispatch failed: %s", project_id)
    except Exception:
        logger.exception(
            "drift(A) detection failed: project=%s branch=%s", project_id, branch
        )
