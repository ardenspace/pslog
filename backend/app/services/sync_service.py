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
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.git_push_event import GitPushEvent
from app.models.project import Project
from app.services import notification_dispatcher

logger = logging.getLogger(__name__)


FetchFile = Callable[[str, str | None, str, str], Awaitable[str | None]]
FetchCompare = Callable[[str, str | None, str, str], Awaitable[list[str]]]


@dataclass
class PlanChanges:
    """`_apply_plan` 의 알림용 변경 요약 — `process_event` 가 dispatcher 에 전달.

    설계서: 2026-05-01-phase-6-discord-notifications-design.md §3.4
    """
    checked: list[tuple[str, str]] = field(default_factory=list)    # [(external_id, title)]
    unchecked: list[tuple[str, str]] = field(default_factory=list)
    archived: list[tuple[str, str]] = field(default_factory=list)

    def has_changes(self) -> bool:
        return bool(self.checked or self.unchecked or self.archived)


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
        plan_changes, handoff_present, plan_changed = await _process_inner(
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
        skip_branches = _resolve_skip_branches(project.handoff_skip_branches)
        plan_meaningfully_changed = plan_changes is not None and plan_changes.has_changes()
        handoff_missing = (
            plan_meaningfully_changed
            and event.branch not in skip_branches
            and not handoff_present
        )
        content = _format_push_summary(
            pusher=event.pusher,
            branch=event.branch,
            short_sha=event.head_commit_sha[:7],
            plan_changes=plan_changes,
            handoff_missing=handoff_missing,
            handoff_path=_handoff_file_path(project, event.branch),
        )
        if content:
            try:
                await notification_dispatcher.dispatch_discord_alert(db, project, content)
            except Exception:
                logger.exception(
                    "Failed to dispatch push summary alert for event %s", event_id,
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
                    f"⚠️ **forps sync 실패** — {project_name}\n"
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
) -> tuple[PlanChanges | None, bool, bool]:
    """Returns: (plan_changes, handoff_present, plan_changed) — process_event 의 알림 결정용."""
    changed_files = await _collect_changed_files(
        event, project, fetch_compare=fetch_compare
    )
    plan_changed = project.plan_path in changed_files
    handoff_path = _handoff_file_path(project, event.branch)
    handoff_changed = handoff_path in changed_files

    plan_changes: PlanChanges | None = None
    handoff_present = False

    if not plan_changed and not handoff_changed:
        logger.info("event %s: no PLAN/handoff in changed files — skip", event.id)
        return plan_changes, handoff_present, plan_changed

    pat = _decrypt_pat(project)

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
            handoff_present = await _apply_handoff(db, project, event, handoff_text)
        else:
            logger.warning(
                "event %s: handoff %s returned 404 — skip", event.id, handoff_path
            )

    return plan_changes, handoff_present, plan_changed


def _format_push_summary(
    *,
    pusher: str,
    branch: str,
    short_sha: str,
    plan_changes: PlanChanges | None,
    handoff_missing: bool,
    handoff_path: str,
) -> str | None:
    """push summary 메시지 생성. 모든 카테고리 비고 + handoff 정상 → None.

    handoff_path: `_handoff_file_path(project, branch)` 결과 (custom handoff_dir / 슬래시 branch 정확).
    """
    has_plan = plan_changes is not None and plan_changes.has_changes()
    if not has_plan and not handoff_missing:
        return None

    lines = [f"📦 {pusher} 가 {branch} 에 push (commit `{short_sha}`)"]

    if has_plan:
        if plan_changes.checked:
            lines.append(f"✅ 완료 ({len(plan_changes.checked)}):")
            for ext_id, title in plan_changes.checked:
                lines.append(f"  • [{ext_id}] {title}")
        if plan_changes.unchecked:
            lines.append(f"↩️ 되돌림 ({len(plan_changes.unchecked)}):")
            for ext_id, title in plan_changes.unchecked:
                lines.append(f"  • [{ext_id}] {title} — DONE → TODO")
        if plan_changes.archived:
            lines.append(f"🗑️ PLAN 에서 제거 ({len(plan_changes.archived)}):")
            for ext_id, title in plan_changes.archived:
                lines.append(f"  • [{ext_id}] {title} (archived)")

    if handoff_missing:
        lines.append(f"⚠️ handoff 누락 — {handoff_path} 갱신 필요")

    return "\n".join(lines)


def _handoff_file_path(project: Project, branch: str) -> str:
    """`handoff_dir + branch.replace('/', '-') + '.md'`. 설계서 §6.2 위치 규약."""
    base = project.handoff_dir if project.handoff_dir.endswith("/") else project.handoff_dir + "/"
    return base + branch.replace("/", "-") + ".md"


def _resolve_skip_branches(raw: str) -> set[str]:
    """handoff 누락 알림 스킵 브랜치 집합 — main 자동 + 사용자 지정 합집합.

    raw: `Project.handoff_skip_branches` (쉼표/줄바꿈 split, 공백 strip, 빈 항목 제거).
    main 은 통합 브랜치 컨벤션의 절대 다수라 코드 레벨 하드코드 스킵.
    """
    user_listed = {
        token.strip()
        for token in raw.replace("\n", ",").split(",")
        if token.strip()
    }
    return {"main", *user_listed}


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

    pat = _decrypt_pat(project)
    files_list = await fetch_compare(project.git_repo_url, pat, base, event.head_commit_sha)
    return set(files_list)


def _decrypt_pat(project: Project) -> str | None:
    if project.github_pat_encrypted is None:
        return None
    from app.core.crypto import decrypt_secret

    try:
        return decrypt_secret(project.github_pat_encrypted)
    except Exception:
        logger.exception("failed to decrypt PAT for project %s", project.id)
        return None


async def _apply_plan(
    db: AsyncSession,
    project: Project,
    event: GitPushEvent,
    plan_text: str,
) -> PlanChanges:
    """PLAN.md 파싱 → Task INSERT/UPDATE + archived_at + 알림용 변경 요약 return.

    설계서: 2026-05-01-phase-6-discord-notifications-design.md §3.4
    신규 INSERT 는 changes 에 안 담음 — sprint 초 noise 회피 (YAGNI).
    """
    from sqlalchemy import select

    from app.models.task import Task, TaskSource, TaskStatus
    from app.models.task_event import TaskEvent, TaskEventAction
    from app.models.user import User
    from app.services.plan_parser_service import parse_plan

    parsed = parse_plan(plan_text)  # DuplicateExternalIdError 는 process_event 가 catch

    changes = PlanChanges()

    # `@username` → user_id 매핑. parser 가 lowercase 가 아닌 핸들도 통과시킬 수 있어
    # User.username (lowercase 만 허용) 과 매칭하려면 비교 시 lower 정규화.
    handles = {
        pt.assignee.lower() for pt in parsed.tasks if pt.assignee is not None
    }
    user_id_by_handle: dict[str, uuid.UUID] = {}
    if handles:
        user_rows = (await db.execute(
            select(User.id, User.username).where(User.username.in_(handles))
        )).all()
        user_id_by_handle = {row.username: row.id for row in user_rows}

    def _resolve_assignee(parsed_handle: str | None) -> uuid.UUID | None:
        if parsed_handle is None:
            return None
        resolved = user_id_by_handle.get(parsed_handle.lower())
        if resolved is None:
            logger.info(
                "plan sync: unknown @%s — leaving assignee unset", parsed_handle
            )
        return resolved

    rows = (await db.execute(
        select(Task).where(
            Task.project_id == project.id,
            Task.source == TaskSource.SYNCED_FROM_PLAN,
        )
    )).scalars().all()
    existing: dict[str, Task] = {t.external_id: t for t in rows if t.external_id}

    for parsed_task in parsed.tasks:
        existing_task = existing.get(parsed_task.external_id)
        new_status = TaskStatus.DONE if parsed_task.checked else TaskStatus.TODO
        new_assignee_id = _resolve_assignee(parsed_task.assignee)

        if existing_task is None:
            t = Task(
                project_id=project.id,
                title=parsed_task.title,
                source=TaskSource.SYNCED_FROM_PLAN,
                external_id=parsed_task.external_id,
                status=new_status,
                assignee_id=new_assignee_id,
                last_commit_sha=event.head_commit_sha,
            )
            db.add(t)
            await db.flush()
            db.add(TaskEvent(
                task_id=t.id,
                action=TaskEventAction.SYNCED_FROM_PLAN,
                changes={
                    "external_id": parsed_task.external_id,
                    "title": parsed_task.title,
                    "checked": parsed_task.checked,
                    "assignee": parsed_task.assignee,
                },
            ))
            # NOTE: 신규 INSERT 는 changes 에 담지 않음 (YAGNI — sprint init noise 회피)
        else:
            previous_status = existing_task.status
            # I-1 fix: archived 였으면 un-archive (재 INSERT 아님 — 히스토리 보존)
            if existing_task.archived_at is not None:
                existing_task.archived_at = None
                # un-archive 자체는 status 변경 없음 — 아래 status 전이 규칙이 그대로 적용됨
            if parsed_task.checked and previous_status != TaskStatus.DONE:
                existing_task.status = TaskStatus.DONE
                existing_task.last_commit_sha = event.head_commit_sha
                db.add(TaskEvent(
                    task_id=existing_task.id,
                    action=TaskEventAction.CHECKED_BY_COMMIT,
                    changes={
                        "previous_status": previous_status.value,
                        "commit_sha": event.head_commit_sha,
                    },
                ))
                changes.checked.append((parsed_task.external_id, parsed_task.title))
            elif not parsed_task.checked and previous_status == TaskStatus.DONE:
                existing_task.status = TaskStatus.TODO
                existing_task.last_commit_sha = event.head_commit_sha
                db.add(TaskEvent(
                    task_id=existing_task.id,
                    action=TaskEventAction.UNCHECKED_BY_COMMIT,
                    changes={
                        "previous_status": previous_status.value,
                        "commit_sha": event.head_commit_sha,
                    },
                ))
                changes.unchecked.append((parsed_task.external_id, parsed_task.title))
            # else: status 변경 없음 — last_commit_sha 도 안 바꿈

            # assignee 는 status 와 독립적으로 sync — PLAN.md 가 source of truth
            if existing_task.assignee_id != new_assignee_id:
                previous_assignee_id = existing_task.assignee_id
                existing_task.assignee_id = new_assignee_id
                db.add(TaskEvent(
                    task_id=existing_task.id,
                    action=TaskEventAction.ASSIGNED,
                    changes={
                        "previous_assignee_id": (
                            str(previous_assignee_id) if previous_assignee_id else None
                        ),
                        "assignee": parsed_task.assignee,
                    },
                ))

    parsed_ids = {t.external_id for t in parsed.tasks}
    for ext_id, task in existing.items():
        if ext_id not in parsed_ids and task.archived_at is None:
            task.archived_at = datetime.utcnow()
            db.add(TaskEvent(
                task_id=task.id,
                action=TaskEventAction.ARCHIVED_FROM_PLAN,
                changes={
                    "external_id": ext_id,
                    "commit_sha": event.head_commit_sha,
                },
            ))
            changes.archived.append((ext_id, task.title))

    return changes


async def _apply_handoff(
    db: AsyncSession,
    project: Project,
    event: GitPushEvent,
    handoff_text: str,
) -> bool:
    """handoff 파싱 → Handoff INSERT (UNIQUE 멱등) + raw_content 저장.

    parsed_tasks 는 sections[0].checks (active 섹션). free_notes 는 sections[0] 의
    free_notes + subtasks 합본. 다중 날짜 history 는 raw_content 에 보존.

    Returns: True (INSERT 또는 UNIQUE conflict savepoint skip — 둘 다 DB 에 handoff 존재).
    """
    from sqlalchemy.exc import IntegrityError

    from app.models.handoff import Handoff
    from app.services.handoff_parser_service import parse_handoff

    parsed = parse_handoff(handoff_text)
    if not parsed.sections:
        return False

    active = parsed.sections[0]
    parsed_tasks = [
        {"external_id": c.external_id, "checked": c.checked, "extra": c.extra}
        for c in active.checks
    ]
    free_notes = {
        "last_commit": active.free_notes.last_commit,
        "next": active.free_notes.next,
        "blockers": active.free_notes.blockers,
        "subtasks": [
            {
                "parent_external_id": s.parent_external_id,
                "checked": s.checked,
                "text": s.text,
            }
            for s in active.subtasks
        ],
    }

    handoff = Handoff(
        project_id=project.id,
        branch=parsed.branch,
        author_git_login=parsed.author_git_login,
        commit_sha=event.head_commit_sha,
        pushed_at=event.received_at,
        raw_content=handoff_text,
        parsed_tasks=parsed_tasks,
        free_notes=free_notes,
    )
    try:
        async with db.begin_nested():
            db.add(handoff)
            await db.flush()
    except IntegrityError:
        logger.info(
            "handoff already exists for project=%s commit=%s — skip",
            project.id, event.head_commit_sha,
        )
    # INSERT 또는 UNIQUE conflict skip 둘 다 "handoff 존재" — caller 의 알림 결정용 True
    return True
