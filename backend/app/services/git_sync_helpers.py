"""git sync 공용 헬퍼 — sync_service 와 drift_service 가 공유.

설계서: 2026-04-26-ai-task-automation-design.md §6.2 (handoff 위치 규약)
spec: docs/tasks/sync-service-split/spec.md
"""

import logging

from app.models.project import Project

logger = logging.getLogger(__name__)


def handoff_file_path(project: Project, branch: str) -> str:
    """`handoff_dir + branch.replace('/', '-') + '.md'`. 설계서 §6.2 위치 규약."""
    base = project.handoff_dir if project.handoff_dir.endswith("/") else project.handoff_dir + "/"
    return base + branch.replace("/", "-") + ".md"


def decrypt_pat(project: Project) -> str | None:
    if project.github_pat_encrypted is None:
        return None
    from app.core.crypto import decrypt_secret

    try:
        return decrypt_secret(project.github_pat_encrypted)
    except Exception:
        logger.exception("failed to decrypt PAT for project %s", project.id)
        return None
