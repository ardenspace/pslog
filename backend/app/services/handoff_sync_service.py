"""handoff 파일 → Handoff row 반영.

설계서: 2026-04-26-ai-task-automation-design.md §5.1, §6.2
spec: docs/tasks/sync-service-split/spec.md
"""

import logging

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.git_push_event import GitPushEvent
from app.models.handoff import Handoff
from app.models.project import Project
from app.services.handoff_parser_service import parse_handoff

logger = logging.getLogger(__name__)


async def apply_handoff(
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
