"""log_query_service 단위 테스트.

설계서: 2026-05-01-error-log-phase4-query-design.md §3.1
"""

import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.error_group import ErrorGroup, ErrorGroupStatus
from app.models.git_push_event import GitPushEvent
from app.models.handoff import Handoff
from app.models.log_event import LogEvent, LogLevel
from app.models.project import Project
from app.models.task import Task, TaskSource, TaskStatus
from app.models.workspace import Workspace
from app.services import log_query_service


async def _seed_project(db: AsyncSession) -> Project:
    ws = Workspace(name="ws", slug=f"ws-{uuid.uuid4().hex[:8]}")
    db.add(ws)
    await db.flush()
    proj = Project(workspace_id=ws.id, name="p")
    db.add(proj)
    await db.commit()
    await db.refresh(proj)
    return proj


def _make_group(
    proj: Project, *, fingerprint: str = "fp-1",
    status: ErrorGroupStatus = ErrorGroupStatus.OPEN,
    last_seen_at: datetime | None = None,
) -> ErrorGroup:
    now = datetime.utcnow()
    return ErrorGroup(
        project_id=proj.id, fingerprint=fingerprint,
        exception_class="KeyError", exception_message_sample="x",
        first_seen_at=now, first_seen_version_sha="a" * 40,
        last_seen_at=last_seen_at or now, last_seen_version_sha="a" * 40,
        event_count=1, status=status,
    )


# ---- list_groups ----

async def test_list_groups_returns_all_when_no_filter(async_session: AsyncSession):
    """필터 없음 — 모든 group + total 반환."""
    proj = await _seed_project(async_session)
    g1 = _make_group(proj, fingerprint="fp-a")
    g2 = _make_group(proj, fingerprint="fp-b", status=ErrorGroupStatus.RESOLVED)
    async_session.add_all([g1, g2])
    await async_session.commit()

    rows, total = await log_query_service.list_groups(
        async_session, project_id=proj.id,
    )
    assert total == 2
    assert len(rows) == 2


async def test_list_groups_filter_by_status(async_session: AsyncSession):
    """status=OPEN 필터."""
    proj = await _seed_project(async_session)
    g_open = _make_group(proj, fingerprint="fp-a", status=ErrorGroupStatus.OPEN)
    g_resolved = _make_group(proj, fingerprint="fp-b", status=ErrorGroupStatus.RESOLVED)
    async_session.add_all([g_open, g_resolved])
    await async_session.commit()

    rows, total = await log_query_service.list_groups(
        async_session, project_id=proj.id, status=ErrorGroupStatus.OPEN,
    )
    assert total == 1
    assert rows[0].fingerprint == "fp-a"


async def test_list_groups_filter_by_since(async_session: AsyncSession):
    """since=... 필터 (last_seen_at >= since)."""
    proj = await _seed_project(async_session)
    cutoff = datetime(2026, 5, 1, 10, 0)
    old = _make_group(proj, fingerprint="fp-old", last_seen_at=datetime(2026, 4, 30, 23, 0))
    new = _make_group(proj, fingerprint="fp-new", last_seen_at=datetime(2026, 5, 1, 11, 0))
    async_session.add_all([old, new])
    await async_session.commit()

    rows, total = await log_query_service.list_groups(
        async_session, project_id=proj.id, since=cutoff,
    )
    assert total == 1
    assert rows[0].fingerprint == "fp-new"


async def test_list_groups_pagination_total_correct(async_session: AsyncSession):
    """offset/limit — total 은 전체, items 는 limit 만큼."""
    proj = await _seed_project(async_session)
    for i in range(5):
        async_session.add(_make_group(proj, fingerprint=f"fp-{i}"))
    await async_session.commit()

    rows, total = await log_query_service.list_groups(
        async_session, project_id=proj.id, offset=0, limit=2,
    )
    assert total == 5
    assert len(rows) == 2

    rows2, total2 = await log_query_service.list_groups(
        async_session, project_id=proj.id, offset=4, limit=2,
    )
    assert total2 == 5
    assert len(rows2) == 1  # 5 - 4 = 1


# ---- get_group_detail ----


def _make_log_event(
    proj: Project, *, fingerprint: str = "fp-1", version_sha: str = "a" * 40,
    environment: str = "production", received_at: datetime | None = None,
) -> LogEvent:
    return LogEvent(
        project_id=proj.id, level=LogLevel.ERROR,
        message="boom", logger_name="app.x", version_sha=version_sha,
        environment=environment, hostname="h",
        emitted_at=datetime.utcnow(), received_at=received_at or datetime.utcnow(),
        exception_class="KeyError", exception_message="x",
        fingerprint=fingerprint, fingerprinted_at=datetime.utcnow(),
    )


async def test_get_group_detail_normal_path_with_git_context(async_session: AsyncSession):
    """정상 path — group + recent events + git context (handoff/task/push_event) 채워짐."""
    proj = await _seed_project(async_session)
    sha = "a" * 40
    group = _make_group(proj, fingerprint="fp-1")
    event = _make_log_event(proj, fingerprint="fp-1", version_sha=sha)
    handoff = Handoff(
        project_id=proj.id, branch="main",
        author_git_login="alice", commit_sha=sha,
        pushed_at=datetime.utcnow(), parsed_tasks=[], free_notes={},
        raw_content="x",
    )
    task = Task(
        project_id=proj.id, title="T",
        source=TaskSource.SYNCED_FROM_PLAN, external_id="task-001",
        status=TaskStatus.DONE, last_commit_sha=sha,
    )
    push = GitPushEvent(
        project_id=proj.id, branch="main", head_commit_sha=sha,
        commits=[], commits_truncated=False, pusher="alice",
        received_at=datetime.utcnow(), processed_at=datetime.utcnow(),
    )
    async_session.add_all([group, event, handoff, task, push])
    await async_session.commit()
    await async_session.refresh(group)

    detail = await log_query_service.get_group_detail(
        async_session, project_id=proj.id, group_id=group.id,
    )
    assert detail is not None
    assert detail["group"].id == group.id
    assert len(detail["recent_events"]) == 1
    git_ctx = detail["git_context"]
    assert len(git_ctx["first_seen"]["handoffs"]) == 1
    assert len(git_ctx["first_seen"]["tasks"]) == 1
    assert git_ctx["first_seen"]["git_push_event"] is not None
    assert git_ctx["previous_good_sha"] is None  # 다른 fingerprint 의 SHA 없음


async def test_get_group_detail_unknown_sha_only(async_session: AsyncSession):
    """모든 events 의 version_sha == 'unknown' → git context 빈."""
    proj = await _seed_project(async_session)
    group = _make_group(proj, fingerprint="fp-2")
    event = _make_log_event(proj, fingerprint="fp-2", version_sha="unknown")
    async_session.add_all([group, event])
    await async_session.commit()
    await async_session.refresh(group)

    detail = await log_query_service.get_group_detail(
        async_session, project_id=proj.id, group_id=group.id,
    )
    assert detail is not None
    git_ctx = detail["git_context"]
    assert git_ctx["first_seen"]["handoffs"] == []
    assert git_ctx["first_seen"]["tasks"] == []
    assert git_ctx["first_seen"]["git_push_event"] is None


async def test_get_group_detail_previous_good_sha_algorithm(async_session: AsyncSession):
    """직전 정상 SHA — 같은 environment 의 다른 fingerprint SHA 가 가장 최근 정상."""
    proj = await _seed_project(async_session)
    target_fp = "fp-target"
    other_fp = "fp-other"

    # Old: 다른 fingerprint 의 event (정상 SHA — target_fp 발생 안 함)
    # NB: version_sha CHECK = '^[0-9a-f]{40}$' OR 'unknown' — hex 문자만.
    good_sha = "b" * 40
    target_sha = "c" * 40
    # 고정 날짜 금지 — log_events 파티션은 migration 실행일 기준 +30일만 존재 (시한폭탄 회피)
    target_time = datetime.utcnow()
    good_event = _make_log_event(
        proj, fingerprint=other_fp, version_sha=good_sha,
        environment="production",
        received_at=target_time - timedelta(hours=1),  # before target
    )

    # New: target_fp 의 첫 발생
    target_event = _make_log_event(
        proj, fingerprint=target_fp, version_sha=target_sha,
        environment="production",
        received_at=target_time,
    )

    group = _make_group(proj, fingerprint=target_fp)
    group.first_seen_at = target_time

    async_session.add_all([good_event, target_event, group])
    await async_session.commit()
    await async_session.refresh(group)

    detail = await log_query_service.get_group_detail(
        async_session, project_id=proj.id, group_id=group.id,
    )
    assert detail is not None
    assert detail["git_context"]["previous_good_sha"] == good_sha


async def test_get_group_detail_returns_none_for_other_project(async_session: AsyncSession):
    """다른 project 의 group_id → None."""
    proj_a = await _seed_project(async_session)
    proj_b = await _seed_project(async_session)
    group = _make_group(proj_b, fingerprint="fp-x")
    async_session.add(group)
    await async_session.commit()
    await async_session.refresh(group)

    detail = await log_query_service.get_group_detail(
        async_session, project_id=proj_a.id, group_id=group.id,
    )
    assert detail is None


# ---- list_logs ----

async def test_list_logs_filter_by_level(async_session: AsyncSession):
    """level=ERROR 필터 (단일 값 매칭)."""
    proj = await _seed_project(async_session)
    e_error = _make_log_event(proj, fingerprint="fp-1")
    e_error.level = LogLevel.ERROR
    e_warning = _make_log_event(proj, fingerprint="fp-2")
    e_warning.level = LogLevel.WARNING
    async_session.add_all([e_error, e_warning])
    await async_session.commit()

    rows, total = await log_query_service.list_logs(
        async_session, project_id=proj.id, level=LogLevel.ERROR,
    )
    assert total == 1
    assert rows[0].level == LogLevel.ERROR


async def test_list_logs_filter_by_since(async_session: AsyncSession):
    """since 필터 (received_at >= since).

    날짜 hardcode 회피 — log_events 는 daily range partition (today+30일).
    utcnow 기준 상대값 사용으로 미래 날짜에서도 안정적.
    """
    proj = await _seed_project(async_session)
    now = datetime.utcnow().replace(minute=0, second=0, microsecond=0)
    cutoff = now + timedelta(hours=1)
    old_e = _make_log_event(
        proj, fingerprint="fp-old",
        received_at=now,  # cutoff 보다 1시간 전
    )
    new_e = _make_log_event(
        proj, fingerprint="fp-new",
        received_at=cutoff + timedelta(hours=1),  # cutoff 보다 1시간 후
    )
    async_session.add_all([old_e, new_e])
    await async_session.commit()

    rows, total = await log_query_service.list_logs(
        async_session, project_id=proj.id, since=cutoff,
    )
    assert total == 1
    assert rows[0].fingerprint == "fp-new"


async def test_list_logs_q_full_text_filters_to_warning_and_above(
    async_session: AsyncSession,
):
    """q 풀텍스트 — level >= WARNING 자동 강제 + ILIKE."""
    proj = await _seed_project(async_session)

    # WARNING + matching message
    e1 = _make_log_event(proj, fingerprint="fp-1")
    e1.level = LogLevel.WARNING
    e1.message = "this is a special_marker thing"

    # ERROR + matching message
    e2 = _make_log_event(proj, fingerprint="fp-2")
    e2.level = LogLevel.ERROR
    e2.message = "another special_marker here"

    # INFO + matching message — should be excluded (level < WARNING)
    e3 = _make_log_event(proj, fingerprint="fp-3")
    e3.level = LogLevel.INFO
    e3.message = "info with special_marker"

    # WARNING + non-matching — should be excluded (no q match)
    e4 = _make_log_event(proj, fingerprint="fp-4")
    e4.level = LogLevel.WARNING
    e4.message = "completely different"

    async_session.add_all([e1, e2, e3, e4])
    await async_session.commit()

    rows, total = await log_query_service.list_logs(
        async_session, project_id=proj.id, q="special_marker",
    )
    assert total == 2
    msgs = {r.message for r in rows}
    assert msgs == {
        "this is a special_marker thing",
        "another special_marker here",
    }
