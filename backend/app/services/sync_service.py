"""webhook GitPushEvent → DB 반영 조립 서비스.

설계서: 2026-04-26-ai-task-automation-design.md §5.1 (⑤), §7.1

흐름:
  1) processed_at 가드 — 이미 처리된 이벤트는 즉시 종료 (멱등성)
  2) 변경 파일 목록 결정 — commits_truncated 시 Compare API, 아니면 commits[*].modified
  3) PLAN/handoff 매칭 — 둘 다 없으면 sync 종료 (processed_at = now)
  4) git_repo_service 로 head_sha 기준 raw fetch
  5) plan_parser_service / handoff_parser_service 호출
  6) DB 반영: Task status / archived_at / Handoff INSERT / TaskEvent
  7) processed_at = now (성공/실패 모두). 실패면 error 도 기록.
"""

import logging
from collections.abc import Awaitable, Callable
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.git_push_event import GitPushEvent
from app.models.project import Project
from app.services import notification_dispatcher
from app.services.git_sync_helpers import decrypt_pat, handoff_file_path
# apply_plan 을 _apply_plan 별칭으로 유지 — 테스트가 sync_service._apply_plan 을 monkeypatch 하는 시임
from app.services.plan_sync_service import PlanChanges
from app.services.plan_sync_service import apply_plan as _apply_plan
from app.services.handoff_sync_service import apply_handoff
from app.services.push_summary_service import format_push_summary, resolve_skip_branches

logger = logging.getLogger(__name__)


FetchFile = Callable[[str, str | None, str, str], Awaitable[str | None]]
FetchCompare = Callable[[str, str | None, str, str], Awaitable[list[str]]]


async def process_event(
    db: AsyncSession,
    event: GitPushEvent,
    *,
    fetch_file: FetchFile,
    fetch_compare: FetchCompare,
) -> None:
    """진입점. 멱등 + 결정적 — 같은 event 두 번 호출해도 DB 변경 1회만.

    B1 / I-4 layer 2: 진입 시 row-level lock 획득 (FOR UPDATE) 후 processed_at 재확인.
    동시 호출 시 후행 caller 는 lock 대기 → 선행 caller commit 후 processed_at 갱신본 보고 return.
    final commit 시 lock release. process_event 가 단일 outer commit 구조라 그대로 적용 가능.
    """
    # FOR UPDATE 로 row 점유 — 동시 caller 차단.
    # SQLAlchemy 2.0: db.refresh(obj, with_for_update=...). nowait=False 로 lock 대기.
    await db.refresh(event, with_for_update={"nowait": False})

    if event.processed_at is not None:
        logger.info("event %s already processed at %s — skip", event.id, event.processed_at)
        return

    project = await db.get(Project, event.project_id)
    if project is None:
        event.processed_at = datetime.utcnow()
        event.error = "project not found"
        await db.commit()
        return

    event_id = event.id  # 세션 poison 후 expire 대비
    # B2: rollback 후 project/event 가 expire — Discord 알림에 필요한 값 미리 캡처.
    discord_webhook_url = project.discord_webhook_url
    project_name = project.name
    event_branch = event.branch
    event_head_sha = event.head_commit_sha

    try:
        plan_changes, handoff_present, plan_changed, drift_alert = await _process_inner(
            db, event, project, fetch_file=fetch_file, fetch_compare=fetch_compare,
        )
        # M-6: 성공 시 project.last_synced_commit_sha 를 head 로 갱신.
        # commits_truncated 의 Compare API base 로 사용됨 (sync_service._collect_changed_files).
        # 실패 path (except 분기) 에서는 갱신 안 함 — 재처리 시 직전 성공 커밋 base 가 유지됨.
        project.last_synced_commit_sha = event.head_commit_sha
        event.processed_at = datetime.utcnow()
        await db.commit()

        # Phase 6: success path push summary 알림.
        # 변경 있을 때만 dispatcher 호출 — no-op push noise 방지.
        # dispatcher 자체가 URL NULL / disabled / 네트워크 오류 swallow 하지만,
        # commit 후 ORM 접근이라 db.refresh 필요 없음 (rollback 안 했으니 expire 없음).
        # handoff_missing — 통합 브랜치(main + 사용자 지정) 자동 스킵 + plan_changes 의미적
        # 변화 0건이면 누락 단독 라인 무의미하니 같이 스킵.
        skip_branches = resolve_skip_branches(project.handoff_skip_branches)
        plan_meaningfully_changed = plan_changes is not None and plan_changes.has_changes()
        handoff_missing = (
            plan_meaningfully_changed
            and event.branch not in skip_branches
            and not handoff_present
        )
        content = format_push_summary(
            pusher=event.pusher,
            branch=event.branch,
            short_sha=event.head_commit_sha[:7],
            plan_changes=plan_changes,
            handoff_missing=handoff_missing,
            handoff_path=handoff_file_path(project, event.branch),
        )
        if content:
            try:
                await notification_dispatcher.dispatch_discord_alert(db, project, content)
            except Exception:
                logger.exception(
                    "Failed to dispatch push summary alert for event %s", event_id,
                )

        # 결정-진실 루프 B: 신규 OPEN 상태모순 드리프트 알림 (commit 후 dispatch).
        if drift_alert:
            try:
                await notification_dispatcher.dispatch_discord_alert(db, project, drift_alert)
            except Exception:
                logger.exception(
                    "Failed to dispatch drift alert for event %s", event_id,
                )
    except Exception as exc:
        # I-2 fix: _process_inner 내부에서 예외 발생 시 세션이 poisoned 상태일 수 있음.
        # rollback → SQLAlchemy 가 pending/new 객체를 identity map 에서 자동 제거.
        # event 는 persistent 상태로 남음. autoflush=False 로 commit 전 autoflush 유발 방지.
        logger.exception("sync failed for event %s", event_id)
        try:
            await db.rollback()
        except Exception:
            pass
        db.sync_session.autoflush = False
        now = datetime.utcnow()
        error_msg = f"{type(exc).__name__}: {exc}"
        event.processed_at = now
        event.error = error_msg
        await db.commit()
        db.sync_session.autoflush = True
        # B2: Discord sync-failure 알림 — dispatcher 경유 (auto-disable 정책 통합).
        # 1차 게이트: rollback 전 캡처한 webhook URL 로 refresh 비용 회피.
        # B1 lesson: rollback 으로 ORM 객체 expire 됨 → dispatcher 진입 전 refresh 필요.
        if discord_webhook_url:
            try:
                await db.refresh(project)
                content = (
                    f"⚠️ **pslog sync 실패** — {project_name}\n"
                    f"branch: `{event_branch}`\n"
                    f"commit: `{event_head_sha[:7]}`\n"
                    f"error: ```{error_msg[:500]}```"
                )
                await notification_dispatcher.dispatch_discord_alert(db, project, content)
            except Exception:
                logger.exception(
                    "Failed to dispatch sync-failure alert for event %s", event_id,
                )


async def _process_inner(
    db: AsyncSession,
    event: GitPushEvent,
    project: Project,
    *,
    fetch_file: FetchFile,
    fetch_compare: FetchCompare,
) -> tuple[PlanChanges | None, bool, bool, str | None]:
    """Returns: (plan_changes, handoff_present, plan_changed, drift_alert).

    drift_alert: 이번 sync 로 신규 OPEN 된 B+C 드리프트 알림 content (없으면 None).
    """
    changed_files = await _collect_changed_files(
        event, project, fetch_compare=fetch_compare
    )
    plan_changed = project.plan_path in changed_files
    handoff_path = handoff_file_path(project, event.branch)
    handoff_changed = handoff_path in changed_files

    plan_changes: PlanChanges | None = None
    handoff_present = False

    from app.services import drift_service

    # 결정-진실 루프 C: 브랜치 task 준비도 미달 감지.
    # PLAN/handoff 변경과 무관하게(코드만 push 해도) 평가해야 하므로 early-return 가드 앞에서 독립 실행.
    c_alert: str | None = None
    try:
        newly_c = await drift_service.detect_task_not_prepared(
            db, project=project, branch=event.branch,
            head_sha=event.head_commit_sha, base_sha=event.before_commit_sha,
            fetch_file=fetch_file, fetch_compare=fetch_compare,
        )
        c_alert = drift_service.format_drift_alert(newly_c)
    except Exception:
        logger.exception("drift(C) detection failed for event %s", event.id)

    if not plan_changed and not handoff_changed:
        logger.info("event %s: no PLAN/handoff in changed files — skip", event.id)
        # PLAN/handoff 무변경이어도 C(준비도) 알림은 전달.
        return plan_changes, handoff_present, plan_changed, c_alert

    pat = decrypt_pat(project)

    if plan_changed and project.git_repo_url is not None:
        plan_text = await fetch_file(
            project.git_repo_url, pat, event.head_commit_sha, project.plan_path
        )
        if plan_text is not None:
            plan_changes = await _apply_plan(db, project, event, plan_text)
        else:
            logger.info("event %s: PLAN.md returned 404 — skip plan", event.id)

    if handoff_changed and project.git_repo_url is not None:
        handoff_text = await fetch_file(
            project.git_repo_url, pat, event.head_commit_sha, handoff_path
        )
        if handoff_text is not None:
            handoff_present = await apply_handoff(db, project, event, handoff_text)
        else:
            logger.warning(
                "event %s: handoff %s returned 404 — skip", event.id, handoff_path
            )

    # 결정-진실 루프 B: handoff↔PLAN 상태 모순 감지 (저장된 Handoff.parsed_tasks 사용).
    # PLAN 또는 handoff 변경 시 항상 재평가. flush 만 — commit 은 process_event 가 담당.
    # 신규 OPEN 드리프트는 알림 content 로 만들어 caller(process_event)가 commit 후 dispatch.
    drift_alert: str | None = c_alert
    try:
        newly = await drift_service.detect_status_contradictions(
            db, project_id=project.id, branch=event.branch,
            commit_sha=event.head_commit_sha,
        )
        b_alert = drift_service.format_drift_alert(newly)
        if b_alert:
            drift_alert = f"{drift_alert}\n\n{b_alert}" if drift_alert else b_alert
    except Exception:
        logger.exception("drift(B) detection failed for event %s", event.id)

    return plan_changes, handoff_present, plan_changed, drift_alert


async def _collect_changed_files(
    event: GitPushEvent,
    project: Project,
    *,
    fetch_compare: FetchCompare,
) -> set[str]:
    """변경 파일 결정. commits_truncated 시 Compare API 호출.

    - truncated == False: commits[*].modified ∪ commits[*].added 합집합
    - truncated == True: Compare API. base = project.last_synced_commit_sha or commits[-1].id (fallback)
    """
    if not event.commits_truncated:
        files: set[str] = set()
        for c in event.commits or []:
            files.update(c.get("modified") or [])
            files.update(c.get("added") or [])
        return files

    if project.git_repo_url is None:
        return set()

    base = event.before_commit_sha
    # GitHub null-sha (`0` * 40) → "no prior commit", fall through to next priority
    if base == "0" * 40:
        base = None
    if base is None:
        base = project.last_synced_commit_sha
    if base is None and event.commits:
        base = event.commits[-1].get("id") or event.head_commit_sha
    if base is None:
        base = event.head_commit_sha

    pat = decrypt_pat(project)
    files_list = await fetch_compare(project.git_repo_url, pat, base, event.head_commit_sha)
    return set(files_list)
