"""PLAN.md → Task 반영.

설계서: 2026-04-26-ai-task-automation-design.md §5.1, 2026-05-01-phase-6-discord-notifications-design.md §3.4
spec: docs/tasks/sync-service-split/spec.md
"""

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.git_push_event import GitPushEvent
from app.models.project import Project
from app.models.task import Task, TaskSource, TaskStatus
from app.models.task_event import TaskEvent, TaskEventAction
from app.models.user import User
from app.services.plan_parser_service import parse_plan

logger = logging.getLogger(__name__)


@dataclass
class PlanChanges:
    """`apply_plan` 의 알림용 변경 요약 — `process_event` 가 dispatcher 에 전달.

    설계서: 2026-05-01-phase-6-discord-notifications-design.md §3.4
    """
    checked: list[tuple[str, str]] = field(default_factory=list)    # [(external_id, title)]
    unchecked: list[tuple[str, str]] = field(default_factory=list)
    archived: list[tuple[str, str]] = field(default_factory=list)

    def has_changes(self) -> bool:
        return bool(self.checked or self.unchecked or self.archived)


async def apply_plan(
    db: AsyncSession,
    project: Project,
    event: GitPushEvent,
    plan_text: str,
) -> PlanChanges:
    """PLAN.md 파싱 → Task INSERT/UPDATE + archived_at + 알림용 변경 요약 return.

    설계서: 2026-05-01-phase-6-discord-notifications-design.md §3.4
    신규 INSERT 는 changes 에 안 담음 — sprint 초 noise 회피 (YAGNI).
    """
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
