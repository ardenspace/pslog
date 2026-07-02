"""드리프트 멱등 open/resolve + 감지 A·B.

설계서: 2026-06-14-decision-truth-loop-design.md §5.
reconcile(): 특정 (project, type[, branch])의 "현재 드리프트 집합"을 받아
OPEN 유지/생성, 빠진 OPEN은 RESOLVED. IGNORED는 불변.
"""

import logging
import re
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.drift import Drift, DriftStatus, DriftType
from app.services.git_sync_helpers import decrypt_pat, handoff_file_path

logger = logging.getLogger(__name__)


@dataclass
class DriftItem:
    dedup_key: str
    branch: str
    external_id: str | None
    detail: str
    commit_sha: str | None = None


async def reconcile(
    db: AsyncSession,
    *,
    project_id: uuid.UUID,
    type_: DriftType,
    current: list[DriftItem],
    branch: str | None = None,
) -> list[Drift]:
    """current = 지금 위반 중인 항목들. 신규는 OPEN 생성, 사라진 OPEN은 RESOLVED.

    branch 지정 시 해당 branch 의 (project, type) 드리프트만 reconcile 범위로 삼는다
    (다른 branch 의 OPEN 을 잘못 RESOLVED 하지 않도록).

    Returns: 이번 호출로 새로 OPEN 된 Drift 목록 (알림용).
    """
    stmt = select(Drift).where(Drift.project_id == project_id, Drift.type == type_)
    if branch is not None:
        stmt = stmt.where(Drift.branch == branch)
    existing = (await db.execute(stmt)).scalars().all()
    by_key = {d.dedup_key: d for d in existing}
    current_keys = {it.dedup_key for it in current}

    newly_opened: list[Drift] = []
    for it in current:
        row = by_key.get(it.dedup_key)
        if row is None:
            row = Drift(
                project_id=project_id, type=type_, status=DriftStatus.OPEN,
                branch=it.branch, external_id=it.external_id, dedup_key=it.dedup_key,
                detail=it.detail, last_seen_commit_sha=it.commit_sha,
            )
            db.add(row)
            newly_opened.append(row)
        elif row.status == DriftStatus.RESOLVED:
            # 재발 — 다시 OPEN
            row.status = DriftStatus.OPEN
            row.resolved_at = None
            row.detail = it.detail
            row.last_seen_commit_sha = it.commit_sha
            newly_opened.append(row)
        elif row.status == DriftStatus.OPEN:
            row.detail = it.detail
            row.last_seen_commit_sha = it.commit_sha
        # IGNORED: 불변

    # 사라진 OPEN → RESOLVED
    for d in existing:
        if d.status == DriftStatus.OPEN and d.dedup_key not in current_keys:
            d.status = DriftStatus.RESOLVED
            d.resolved_at = datetime.utcnow()

    await db.flush()
    return newly_opened


# 드리프트 타입 → Discord 알림 표시용 한글 라벨 (없으면 enum value 로 폴백)
_TYPE_LABEL = {
    DriftType.DECISION_NOT_PROMOTED: "결정 미승격",
    DriftType.STATUS_CONTRADICTION: "상태 모순",
    DriftType.TASK_NOT_PREPARED: "태스크 미준비",
}


def format_drift_alert(newly_opened: list[Drift]) -> str | None:
    """신규 OPEN 드리프트 목록 → Discord 알림 문자열. 빈 목록이면 None."""
    if not newly_opened:
        return None
    lines = ["⚠️ **pslog 드리프트 감지**"]
    for d in newly_opened:
        lines.append(f"• [{_TYPE_LABEL.get(d.type, d.type.value)}] {d.branch} — {d.detail}")
    return "\n".join(lines)


async def detect_status_contradictions(
    db: AsyncSession,
    *,
    project_id: uuid.UUID,
    branch: str,
    commit_sha: str | None,
) -> list[Drift]:
    """해당 branch 최신 Handoff.parsed_tasks vs Task.status 모순 → Drift(B) reconcile.

    inner-join on external_id. handoff.checked != (task.status == DONE) → 모순.
    서브 체크박스는 parsed_tasks 에 애초에 없음(파서가 들여쓰기 0 만 담음).
    """
    from app.models.handoff import Handoff
    from app.models.task import Task, TaskSource, TaskStatus

    handoff = (await db.execute(
        select(Handoff).where(
            Handoff.project_id == project_id, Handoff.branch == branch,
        ).order_by(Handoff.pushed_at.desc().nullslast(), Handoff.id.desc())
    )).scalars().first()
    if handoff is None:
        return await reconcile(
            db, project_id=project_id,
            type_=DriftType.STATUS_CONTRADICTION, current=[], branch=branch,
        )

    tasks = (await db.execute(
        select(Task).where(
            Task.project_id == project_id,
            Task.source == TaskSource.SYNCED_FROM_PLAN,
            Task.archived_at.is_(None),
        )
    )).scalars().all()
    status_by_id = {t.external_id: t.status for t in tasks if t.external_id}

    items: list[DriftItem] = []
    for pt in handoff.parsed_tasks or []:
        ext = pt.get("external_id")
        if ext is None or ext not in status_by_id:
            continue
        handoff_done = bool(pt.get("checked"))
        plan_done = status_by_id[ext] == TaskStatus.DONE
        if handoff_done != plan_done:
            if plan_done and not handoff_done:
                detail = f"PLAN({ext}) DONE인데 handoff 미완 — 둘 중 하나 맞추세요"
            else:
                detail = f"handoff({ext}) 완료 표시인데 PLAN 미완 — PLAN 체크/커밋 확인"
            items.append(DriftItem(
                dedup_key=f"{branch}:{ext}", branch=branch, external_id=ext,
                detail=detail, commit_sha=commit_sha,
            ))

    return await reconcile(
        db, project_id=project_id,
        type_=DriftType.STATUS_CONTRADICTION, current=items, branch=branch,
    )


async def detect_unpromoted_decisions(
    db: AsyncSession,
    *,
    project,
    branch: str,
    head_sha: str,
    base_sha: str | None,
    fetch_file,
    fetch_compare,
) -> list[Drift]:
    """브랜치 handoff 의 `### 결정` 미승격 감지 → Drift(A) reconcile.

    조건: (1) promoted=False 항목 존재  OR
          (2) 항목은 있고 전부 promoted=True 인데 PR diff 에 DECISIONS.md 변경 없음.
    """
    from app.services.handoff_parser_service import MalformedHandoffError, parse_handoff

    if project.git_repo_url is None:
        return []
    pat = decrypt_pat(project)
    handoff_path = handoff_file_path(project, branch)
    text = await fetch_file(project.git_repo_url, pat, head_sha, handoff_path)
    if text is None:
        return await reconcile(db, project_id=project.id,
                               type_=DriftType.DECISION_NOT_PROMOTED,
                               current=[], branch=branch)
    try:
        parsed = parse_handoff(text)
    except MalformedHandoffError:
        return []
    decisions = parsed.sections[0].decisions if parsed.sections else []

    items: list[DriftItem] = []
    if decisions:
        unpromoted = [d for d in decisions if not d.promoted]
        if unpromoted:
            ids = ", ".join(d.external_id or "?" for d in unpromoted)
            items.append(DriftItem(
                dedup_key=branch, branch=branch, external_id=None,
                detail=f"결정 미승격: {ids} — PR 열기 전 DECISIONS.md로 승격하세요",
                commit_sha=head_sha,
            ))
        else:
            changed = await fetch_compare(
                project.git_repo_url, pat, base_sha or head_sha, head_sha
            )
            if project.decisions_path not in set(changed):
                items.append(DriftItem(
                    dedup_key=branch, branch=branch, external_id=None,
                    detail="결정에 → DECISIONS 마커는 있는데 DECISIONS.md 변경 없음 — 실제 승격 확인",
                    commit_sha=head_sha,
                ))

    return await reconcile(db, project_id=project.id,
                           type_=DriftType.DECISION_NOT_PROMOTED,
                           current=items, branch=branch)


_BRANCH_TASK_RE = re.compile(r"^feat/(task-[0-9]+)-")


def _branch_to_task(branch: str) -> str | None:
    m = _BRANCH_TASK_RE.match(branch or "")
    return m.group(1) if m else None


async def detect_task_not_prepared(
    db: AsyncSession,
    *,
    project,
    branch: str,
    head_sha: str,
    base_sha: str | None,
    fetch_file,
    fetch_compare,
) -> list[Drift]:
    """C: 브랜치 task 에 코드가 들어왔는데 무게별 준비 산출물이 없으면 OPEN.
    deep → spec.md+plan.md / light → brief.md (docs/tasks/task-NNN/)."""
    from app.services.plan_parser_service import parse_plan

    external_id = _branch_to_task(branch)
    if external_id is None or project.git_repo_url is None or base_sha is None:
        # 평가 대상 아님(브랜치 형식/최초 push/저장소 미설정) → reconcile 안 함(기존 OPEN 보존)
        return []

    pat = decrypt_pat(project)

    # 코드 들어옴? (tasks_dir 밖 변경 파일 1개+)
    tasks_root = project.tasks_dir.rstrip("/") + "/"
    try:
        changed = await fetch_compare(project.git_repo_url, pat, base_sha, head_sha)
    except Exception:
        changed = []
    code_landed = any(not f.startswith(tasks_root) for f in changed)
    if not code_landed:
        return await reconcile(db, project_id=project.id, type_=DriftType.TASK_NOT_PREPARED,
                               current=[], branch=branch)

    # PLAN 에서 무게 판정
    plan_text = await fetch_file(project.git_repo_url, pat, head_sha, project.plan_path)
    parsed = parse_plan(plan_text) if plan_text else None
    task = next((t for t in parsed.tasks if t.external_id == external_id), None) if parsed else None
    deep = bool(task.deep) if task else False

    base = f"{tasks_root}{external_id}"
    required = [f"{base}/spec.md", f"{base}/plan.md"] if deep else [f"{base}/brief.md"]

    async def _present(path: str) -> bool:
        txt = await fetch_file(project.git_repo_url, pat, head_sha, path)
        return txt is not None and txt.strip() != ""

    missing = [p for p in required if not await _present(p)]

    items: list[DriftItem] = []
    if missing:
        kind = "spec/plan" if deep else "brief"
        items.append(DriftItem(
            dedup_key=f"{branch}:{external_id}",
            branch=branch,
            external_id=external_id,
            detail=f"{external_id}: 코드 들어왔는데 준비 문서 누락({kind}) → {', '.join(missing)} 작성하세요",
            commit_sha=head_sha,
        ))
    return await reconcile(db, project_id=project.id, type_=DriftType.TASK_NOT_PREPARED,
                           current=items, branch=branch)
