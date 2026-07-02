"""push summary Discord 메시지 포매팅.

설계서: 2026-05-01-phase-6-discord-notifications-design.md §3.4
spec: docs/tasks/sync-service-split/spec.md
"""

from app.services.plan_sync_service import PlanChanges


def format_push_summary(
    *,
    pusher: str,
    branch: str,
    short_sha: str,
    plan_changes: PlanChanges | None,
    handoff_missing: bool,
    handoff_path: str,
) -> str | None:
    """push summary 메시지 생성. 모든 카테고리 비고 + handoff 정상 → None.

    handoff_path: `handoff_file_path(project, branch)` 결과 (custom handoff_dir / 슬래시 branch 정확).
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


def resolve_skip_branches(raw: str) -> set[str]:
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
