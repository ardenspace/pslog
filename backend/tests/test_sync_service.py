"""sync_service — webhook 이벤트 → DB 반영 통합 테스트.

설계서: 2026-04-26-ai-task-automation-design.md §5.1 (⑤), §7.1, §10.2
"""

import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.git_push_event import GitPushEvent
from app.models.handoff import Handoff
from app.models.project import Project
from app.models.task import Task, TaskSource, TaskStatus
from app.models.task_event import TaskEvent, TaskEventAction
from app.models.user import User
from app.models.workspace import Workspace
from app.services.sync_service import process_event


async def _seed_user(db: AsyncSession, *, username: str, email: str | None = None) -> User:
    user = User(
        email=email or f"{username}@example.com",
        username=username,
        name=username,
        password_hash="x",
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def _seed_project(
    db: AsyncSession, *, repo_url: str | None = "https://github.com/ardenspace/app-chak"
) -> Project:
    ws = Workspace(name="ws", slug=f"ws-{uuid.uuid4().hex[:8]}")
    db.add(ws)
    await db.flush()
    proj = Project(workspace_id=ws.id, name="p", git_repo_url=repo_url)
    db.add(proj)
    await db.commit()
    await db.refresh(proj)
    return proj


async def _seed_event(
    db: AsyncSession,
    project: Project,
    *,
    head_sha: str = "a" * 40,
    branch: str = "main",
    commits: list[dict] | None = None,
    commits_truncated: bool = False,
    processed_at: datetime | None = None,
) -> GitPushEvent:
    event = GitPushEvent(
        project_id=project.id,
        branch=branch,
        head_commit_sha=head_sha,
        commits=commits or [],
        commits_truncated=commits_truncated,
        pusher="alice",
        processed_at=processed_at,
    )
    db.add(event)
    await db.commit()
    await db.refresh(event)
    return event


async def _noop_fetch_file(repo_url: str, pat: str | None, sha: str, path: str) -> str | None:
    return None


async def _noop_fetch_compare(repo_url: str, pat: str | None, base: str, head: str) -> list[str]:
    return []


async def test_process_event_skips_already_processed(async_session: AsyncSession):
    """processed_at IS NOT NULL 이면 즉시 종료 — DB 변경 없음."""
    proj = await _seed_project(async_session)
    event = await _seed_event(
        async_session, proj, processed_at=datetime.utcnow() - timedelta(minutes=10)
    )
    initial_processed_at = event.processed_at

    await process_event(
        async_session, event,
        fetch_file=_noop_fetch_file, fetch_compare=_noop_fetch_compare,
    )

    await async_session.refresh(event)
    assert event.processed_at == initial_processed_at
    assert event.error is None


async def test_process_event_marks_processed_when_no_relevant_files(
    async_session: AsyncSession,
):
    """변경 파일 중 PLAN/handoff 없음 → fetch 안 함, processed_at = now()."""
    proj = await _seed_project(async_session)
    event = await _seed_event(
        async_session, proj,
        commits=[{"modified": ["frontend/Button.tsx"], "added": [], "removed": []}],
    )

    await process_event(
        async_session, event,
        fetch_file=_noop_fetch_file, fetch_compare=_noop_fetch_compare,
    )

    await async_session.refresh(event)
    assert event.processed_at is not None
    assert event.error is None


async def test_process_event_creates_new_tasks_from_plan(async_session: AsyncSession):
    """PLAN 에 새 task-XXX 가 있으면 Task INSERT (source=SYNCED_FROM_PLAN, status 매핑)."""
    proj = await _seed_project(async_session)
    plan_text = """# 스프린트: 2026-04

## 태스크

- [ ] [task-001] 새 작업 — @alice
- [x] [task-002] 이미 완료 — @bob
"""

    async def fake_fetch_file(repo_url, pat, sha, path):
        if path == "PLAN.md":
            return plan_text
        return None

    event = await _seed_event(
        async_session, proj,
        commits=[{"modified": ["PLAN.md"], "added": [], "removed": []}],
    )

    await process_event(
        async_session, event,
        fetch_file=fake_fetch_file, fetch_compare=_noop_fetch_compare,
    )

    await async_session.refresh(event)
    assert event.processed_at is not None
    assert event.error is None

    rows = (
        await async_session.execute(
            select(Task).where(Task.project_id == proj.id).order_by(Task.external_id)
        )
    ).scalars().all()
    assert len(rows) == 2
    t1 = next(t for t in rows if t.external_id == "task-001")
    t2 = next(t for t in rows if t.external_id == "task-002")
    assert t1.source == TaskSource.SYNCED_FROM_PLAN
    assert t1.status == TaskStatus.TODO
    assert t1.title == "새 작업"
    assert t1.last_commit_sha == event.head_commit_sha
    assert t2.status == TaskStatus.DONE
    assert t2.last_commit_sha == event.head_commit_sha


async def test_process_event_records_synced_from_plan_event(async_session: AsyncSession):
    """신규 Task INSERT 시 TaskEvent(action=SYNCED_FROM_PLAN) 도 만들어짐."""
    proj = await _seed_project(async_session)
    plan_text = "## 태스크\n\n- [ ] [task-100] 신규 — @alice\n"

    async def fake_fetch_file(repo_url, pat, sha, path):
        return plan_text if path == "PLAN.md" else None

    event = await _seed_event(
        async_session, proj, commits=[{"modified": ["PLAN.md"]}]
    )
    await process_event(
        async_session, event,
        fetch_file=fake_fetch_file, fetch_compare=_noop_fetch_compare,
    )

    task = (await async_session.execute(
        select(Task).where(Task.external_id == "task-100")
    )).scalar_one()
    events = (await async_session.execute(
        select(TaskEvent).where(TaskEvent.task_id == task.id)
    )).scalars().all()
    assert any(e.action == TaskEventAction.SYNCED_FROM_PLAN for e in events)


async def test_process_event_skips_when_plan_404(async_session: AsyncSession):
    """fetch_file 이 None (404) 반환 → sync 종료, error 기록 없음."""
    proj = await _seed_project(async_session)

    async def fake_fetch_file(repo_url, pat, sha, path):
        return None

    event = await _seed_event(
        async_session, proj, commits=[{"modified": ["PLAN.md"]}]
    )
    await process_event(
        async_session, event,
        fetch_file=fake_fetch_file, fetch_compare=_noop_fetch_compare,
    )

    await async_session.refresh(event)
    assert event.processed_at is not None
    assert event.error is None
    rows = (await async_session.execute(
        select(Task).where(Task.project_id == proj.id)
    )).scalars().all()
    assert rows == []


async def test_process_event_checks_existing_task_to_done(async_session: AsyncSession):
    """기존 TODO task 가 PLAN 에서 [x] 로 → DONE + CHECKED_BY_COMMIT TaskEvent."""
    proj = await _seed_project(async_session)
    existing = Task(
        project_id=proj.id,
        title="기존",
        source=TaskSource.SYNCED_FROM_PLAN,
        external_id="task-001",
        status=TaskStatus.TODO,
    )
    async_session.add(existing)
    await async_session.commit()
    await async_session.refresh(existing)

    plan_text = "## 태스크\n\n- [x] [task-001] 기존 — @alice\n"

    async def fake_fetch_file(repo_url, pat, sha, path):
        return plan_text if path == "PLAN.md" else None

    event = await _seed_event(
        async_session, proj, commits=[{"modified": ["PLAN.md"]}]
    )
    await process_event(
        async_session, event,
        fetch_file=fake_fetch_file, fetch_compare=_noop_fetch_compare,
    )
    await async_session.refresh(existing)

    assert existing.status == TaskStatus.DONE
    assert existing.last_commit_sha == event.head_commit_sha
    events = (await async_session.execute(
        select(TaskEvent).where(TaskEvent.task_id == existing.id)
    )).scalars().all()
    assert any(e.action == TaskEventAction.CHECKED_BY_COMMIT for e in events)


async def test_process_event_rolls_back_done_to_todo(async_session: AsyncSession):
    """직전 DONE 인 task 가 PLAN 에서 [ ] 로 → TODO + UNCHECKED_BY_COMMIT."""
    proj = await _seed_project(async_session)
    existing = Task(
        project_id=proj.id,
        title="롤백 케이스",
        source=TaskSource.SYNCED_FROM_PLAN,
        external_id="task-002",
        status=TaskStatus.DONE,
    )
    async_session.add(existing)
    await async_session.commit()
    await async_session.refresh(existing)

    plan_text = "## 태스크\n\n- [ ] [task-002] 롤백 케이스\n"

    async def fake_fetch_file(repo_url, pat, sha, path):
        return plan_text if path == "PLAN.md" else None

    event = await _seed_event(
        async_session, proj, commits=[{"modified": ["PLAN.md"]}]
    )
    await process_event(
        async_session, event,
        fetch_file=fake_fetch_file, fetch_compare=_noop_fetch_compare,
    )
    await async_session.refresh(existing)

    assert existing.status == TaskStatus.TODO
    events = (await async_session.execute(
        select(TaskEvent).where(TaskEvent.task_id == existing.id)
    )).scalars().all()
    assert any(e.action == TaskEventAction.UNCHECKED_BY_COMMIT for e in events)


async def test_process_event_no_change_when_unchecked_and_already_not_done(
    async_session: AsyncSession,
):
    """직전 TODO 인 task 가 PLAN 에서 [ ] → 변경 없음, TaskEvent 도 안 만듦."""
    proj = await _seed_project(async_session)
    existing = Task(
        project_id=proj.id,
        title="변경 없음",
        source=TaskSource.SYNCED_FROM_PLAN,
        external_id="task-003",
        status=TaskStatus.DOING,
    )
    async_session.add(existing)
    await async_session.commit()
    await async_session.refresh(existing)

    plan_text = "## 태스크\n\n- [ ] [task-003] 변경 없음\n"

    async def fake_fetch_file(repo_url, pat, sha, path):
        return plan_text if path == "PLAN.md" else None

    event = await _seed_event(
        async_session, proj, commits=[{"modified": ["PLAN.md"]}]
    )
    await process_event(
        async_session, event,
        fetch_file=fake_fetch_file, fetch_compare=_noop_fetch_compare,
    )
    await async_session.refresh(existing)

    assert existing.status == TaskStatus.DOING
    events = (await async_session.execute(
        select(TaskEvent).where(TaskEvent.task_id == existing.id)
    )).scalars().all()
    assert len(events) == 0


async def test_process_event_archives_tasks_removed_from_plan(async_session: AsyncSession):
    """기존 synced task 가 새 PLAN 에 없으면 archived_at = now() + ARCHIVED_FROM_PLAN."""
    proj = await _seed_project(async_session)
    keep = Task(
        project_id=proj.id, title="유지", source=TaskSource.SYNCED_FROM_PLAN,
        external_id="task-001", status=TaskStatus.TODO,
    )
    removed = Task(
        project_id=proj.id, title="삭제됨", source=TaskSource.SYNCED_FROM_PLAN,
        external_id="task-OLD", status=TaskStatus.DOING,
    )
    async_session.add_all([keep, removed])
    await async_session.commit()
    await async_session.refresh(removed)

    plan_text = "## 태스크\n\n- [ ] [task-001] 유지\n"

    async def fake_fetch_file(repo_url, pat, sha, path):
        return plan_text if path == "PLAN.md" else None

    event = await _seed_event(
        async_session, proj, commits=[{"modified": ["PLAN.md"]}]
    )
    await process_event(
        async_session, event,
        fetch_file=fake_fetch_file, fetch_compare=_noop_fetch_compare,
    )
    await async_session.refresh(removed)
    await async_session.refresh(keep)

    assert removed.archived_at is not None
    assert keep.archived_at is None
    events = (await async_session.execute(
        select(TaskEvent).where(TaskEvent.task_id == removed.id)
    )).scalars().all()
    assert any(e.action == TaskEventAction.ARCHIVED_FROM_PLAN for e in events)


async def test_process_event_does_not_archive_manual_tasks(async_session: AsyncSession):
    """source=MANUAL 인 task 는 PLAN 에 없어도 archived_at 안 변경."""
    proj = await _seed_project(async_session)
    manual = Task(
        project_id=proj.id, title="수동", source=TaskSource.MANUAL,
        external_id=None, status=TaskStatus.TODO,
    )
    async_session.add(manual)
    await async_session.commit()
    await async_session.refresh(manual)

    plan_text = "## 태스크\n\n- [ ] [task-001] PLAN 만\n"

    async def fake_fetch_file(repo_url, pat, sha, path):
        return plan_text if path == "PLAN.md" else None

    event = await _seed_event(
        async_session, proj, commits=[{"modified": ["PLAN.md"]}]
    )
    await process_event(
        async_session, event,
        fetch_file=fake_fetch_file, fetch_compare=_noop_fetch_compare,
    )
    await async_session.refresh(manual)
    assert manual.archived_at is None


async def test_process_event_inserts_handoff_row(async_session: AsyncSession):
    """handoff 변경 → fetch + parse → Handoff INSERT (parsed_tasks/free_notes 채워짐)."""
    proj = await _seed_project(async_session)
    handoff_text = """# Handoff: feature/login — @alice

## 2026-04-30

- [x] task-001
- [ ] task-002

### 마지막 커밋

abc1234 — 작업 진행
"""

    async def fake_fetch_file(repo_url, pat, sha, path):
        if path == "handoffs/feature-login.md":
            return handoff_text
        return None

    event = await _seed_event(
        async_session, proj,
        branch="feature/login",
        commits=[{"modified": ["handoffs/feature-login.md"]}],
    )
    await process_event(
        async_session, event,
        fetch_file=fake_fetch_file, fetch_compare=_noop_fetch_compare,
    )

    rows = (await async_session.execute(
        select(Handoff).where(Handoff.project_id == proj.id)
    )).scalars().all()
    assert len(rows) == 1
    h = rows[0]
    assert h.commit_sha == event.head_commit_sha
    assert h.branch == "feature/login"
    assert h.author_git_login == "alice"
    assert h.parsed_tasks is not None
    ids = [pt["external_id"] for pt in h.parsed_tasks]
    assert ids == ["task-001", "task-002"]
    assert h.free_notes is not None
    assert "abc1234" in h.free_notes.get("last_commit", "")


async def test_process_event_handoff_idempotent_on_replay(async_session: AsyncSession):
    """같은 commit_sha 로 두 번 process → Handoff 1 행 (processed_at 가드)."""
    proj = await _seed_project(async_session)
    handoff_text = "# Handoff: main — @alice\n\n## 2026-04-30\n\n- [x] task-001\n"

    async def fake_fetch_file(repo_url, pat, sha, path):
        return handoff_text if path == "handoffs/main.md" else None

    event1 = await _seed_event(
        async_session, proj, head_sha="c" * 40,
        commits=[{"modified": ["handoffs/main.md"]}],
    )
    await process_event(
        async_session, event1,
        fetch_file=fake_fetch_file, fetch_compare=_noop_fetch_compare,
    )
    # 두 번째 호출 — processed_at 가드로 즉시 종료
    await process_event(
        async_session, event1,
        fetch_file=fake_fetch_file, fetch_compare=_noop_fetch_compare,
    )

    rows = (await async_session.execute(
        select(Handoff).where(Handoff.project_id == proj.id)
    )).scalars().all()
    assert len(rows) == 1


async def test_process_event_handoff_unique_conflict_silent_skip(
    async_session: AsyncSession,
):
    """Handoff UNIQUE (project_id, commit_sha) 충돌 → SAVEPOINT rollback, silent skip, error 없음.

    시나리오: 동일 commit_sha 로 Handoff 행이 이미 존재할 때 process_event 가
    IntegrityError 를 SAVEPOINT 로 흡수하고, processed_at 을 기록하며 error 는 None.
    """
    proj = await _seed_project(async_session)
    target_sha = "d" * 40
    handoff_text = "# Handoff: main — @alice\n\n## 2026-04-30\n\n- [x] task-001\n"

    # Handoff 행을 미리 직접 삽입 — 이후 event 가 동일 commit_sha 로 충돌 유발
    pre_existing = Handoff(
        project_id=proj.id,
        branch="main",
        author_git_login="alice",
        commit_sha=target_sha,
        pushed_at=datetime.utcnow(),
        raw_content=handoff_text,
        parsed_tasks=[{"external_id": "task-001", "checked": True, "extra": None}],
        free_notes={},
    )
    async_session.add(pre_existing)
    await async_session.commit()

    async def fake_fetch_file(repo_url, pat, sha, path):
        return handoff_text if path == "handoffs/main.md" else None

    # 새 이벤트: head_sha = target_sha → _apply_handoff 에서 UNIQUE 충돌 발생
    event = await _seed_event(
        async_session, proj, head_sha=target_sha,
        commits=[{"modified": ["handoffs/main.md"]}],
    )
    await process_event(
        async_session, event,
        fetch_file=fake_fetch_file, fetch_compare=_noop_fetch_compare,
    )

    await async_session.refresh(event)
    # SAVEPOINT 가 conflict 를 흡수해야 함 — error 없이 processed_at 설정
    assert event.processed_at is not None
    assert event.error is None

    rows = (await async_session.execute(
        select(Handoff).where(Handoff.project_id == proj.id)
    )).scalars().all()
    assert len(rows) == 1


async def test_process_event_malformed_handoff_records_error(async_session: AsyncSession):
    """handoff 헤더 없음 → MalformedHandoffError → event.error 기록, processed_at = now."""
    proj = await _seed_project(async_session)

    async def fake_fetch_file(repo_url, pat, sha, path):
        if path == "handoffs/main.md":
            return "## 2026-04-30\n\n- [ ] task-001\n"
        return None

    event = await _seed_event(
        async_session, proj,
        commits=[{"modified": ["handoffs/main.md"]}],
    )
    await process_event(
        async_session, event,
        fetch_file=fake_fetch_file, fetch_compare=_noop_fetch_compare,
    )
    await async_session.refresh(event)
    assert event.processed_at is not None
    assert event.error is not None
    assert "MalformedHandoffError" in event.error


async def test_process_event_idempotent_full_cycle(async_session: AsyncSession):
    """CRITICAL: PLAN+handoff 동시 변경 푸시를 두 번 process → DB 변경 1회, Handoff 1행, TaskEvent 중복 없음."""
    proj = await _seed_project(async_session)
    plan_text = """# 스프린트: 2026-04

## 태스크

- [x] [task-001] 완료된 작업 — @alice
"""
    handoff_text = """# Handoff: main — @alice

## 2026-04-30

- [x] task-001
"""

    async def fake_fetch_file(repo_url, pat, sha, path):
        if path == "PLAN.md":
            return plan_text
        if path == "handoffs/main.md":
            return handoff_text
        return None

    event = await _seed_event(
        async_session, proj,
        head_sha="e" * 40,
        commits=[{"modified": ["PLAN.md", "handoffs/main.md"]}],
    )

    await process_event(
        async_session, event,
        fetch_file=fake_fetch_file, fetch_compare=_noop_fetch_compare,
    )

    tasks_after_first = (await async_session.execute(
        select(Task).where(Task.project_id == proj.id)
    )).scalars().all()
    handoffs_after_first = (await async_session.execute(
        select(Handoff).where(Handoff.project_id == proj.id)
    )).scalars().all()
    events_after_first = (await async_session.execute(
        select(TaskEvent)
        .where(TaskEvent.task_id.in_([t.id for t in tasks_after_first]))
    )).scalars().all()

    await process_event(
        async_session, event,
        fetch_file=fake_fetch_file, fetch_compare=_noop_fetch_compare,
    )

    tasks_after_second = (await async_session.execute(
        select(Task).where(Task.project_id == proj.id)
    )).scalars().all()
    handoffs_after_second = (await async_session.execute(
        select(Handoff).where(Handoff.project_id == proj.id)
    )).scalars().all()
    events_after_second = (await async_session.execute(
        select(TaskEvent)
        .where(TaskEvent.task_id.in_([t.id for t in tasks_after_second]))
    )).scalars().all()

    assert len(tasks_after_first) == len(tasks_after_second) == 1
    assert len(handoffs_after_first) == len(handoffs_after_second) == 1
    assert len(events_after_first) == len(events_after_second)


async def test_process_event_records_error_on_duplicate_external_id(
    async_session: AsyncSession,
):
    """PLAN 에 같은 external_id 가 두 번 → DuplicateExternalIdError → event.error 기록."""
    proj = await _seed_project(async_session)
    plan_text = """## 태스크

- [ ] [task-001] 첫 번째
- [ ] [task-001] 중복 — @bob
"""

    async def fake_fetch_file(repo_url, pat, sha, path):
        return plan_text if path == "PLAN.md" else None

    event = await _seed_event(
        async_session, proj,
        commits=[{"modified": ["PLAN.md"]}],
    )
    await process_event(
        async_session, event,
        fetch_file=fake_fetch_file, fetch_compare=_noop_fetch_compare,
    )
    await async_session.refresh(event)
    assert event.error is not None
    assert "DuplicateExternalIdError" in event.error
    assert event.processed_at is not None


async def test_process_event_unarchives_task_when_re_added_to_plan(
    async_session: AsyncSession,
):
    """code review I-1: archived task 가 PLAN 에 다시 등장하면 un-archive (재INSERT 안 함, IntegrityError 안 남)."""
    proj = await _seed_project(async_session)
    archived = Task(
        project_id=proj.id,
        title="이전 archived",
        source=TaskSource.SYNCED_FROM_PLAN,
        external_id="task-001",
        status=TaskStatus.TODO,
        archived_at=datetime.utcnow() - timedelta(days=1),
    )
    async_session.add(archived)
    await async_session.commit()
    await async_session.refresh(archived)
    archived_id = archived.id

    plan_text = "## 태스크\n\n- [ ] [task-001] 다시 등장 — @alice\n"

    async def fake_fetch_file(repo_url, pat, sha, path):
        return plan_text if path == "PLAN.md" else None

    event = await _seed_event(
        async_session, proj, commits=[{"modified": ["PLAN.md"]}]
    )
    await process_event(
        async_session, event,
        fetch_file=fake_fetch_file, fetch_compare=_noop_fetch_compare,
    )
    await async_session.refresh(event)
    assert event.error is None  # IntegrityError 안 발생

    # 같은 row 가 un-archive 됨 (재INSERT 아님)
    rows = (await async_session.execute(
        select(Task).where(Task.project_id == proj.id, Task.external_id == "task-001")
    )).scalars().all()
    assert len(rows) == 1
    assert rows[0].id == archived_id
    assert rows[0].archived_at is None


async def test_process_event_records_error_even_when_session_poisoned(
    async_session: AsyncSession,
):
    """code review I-2: _apply_plan 안에서 IntegrityError 가 나도 event.error / processed_at 가 영구 저장됨."""
    proj = await _seed_project(async_session)

    # 시나리오: source=SYNCED_FROM_PLAN, archived_at=None 이지만 PLAN 의 task 와는 별개 external_id —
    # 이 task 는 partial UNIQUE 에 걸리지 않음. 대신 manual force IntegrityError 시뮬레이션:
    # 같은 commit_sha 로 Handoff 가 미리 들어가있으면 _apply_handoff 가 SAVEPOINT 로 흡수해서 통과 — 부적합.
    # 대신 archived 가 있는 상태에서 (I-1 미수정 상태라면) IntegrityError 강제. I-1 fix 후엔 이 시나리오가
    # un-archive 로 통과. 따라서 I-2 단독 회귀는 monkeypatch 로 _apply_plan 을 force-raise.

    import app.services.sync_service as sync_mod

    original_apply_plan = sync_mod._apply_plan

    async def boom(db, project, event, plan_text):
        # 진짜 IntegrityError 처럼 — 세션이 poisoned 상태로 진입하도록 유사 INSERT 후 중복 INSERT
        from app.models.task import Task as TaskModel, TaskSource as TS
        t1 = TaskModel(
            project_id=project.id, title="x", source=TS.SYNCED_FROM_PLAN,
            external_id="task-DUP",
        )
        db.add(t1)
        await db.flush()
        t2 = TaskModel(
            project_id=project.id, title="x2", source=TS.SYNCED_FROM_PLAN,
            external_id="task-DUP",
        )
        db.add(t2)
        await db.flush()  # IntegrityError raise

    plan_text = "## 태스크\n\n- [ ] [task-001] anything — @alice\n"

    async def fake_fetch_file(repo_url, pat, sha, path):
        return plan_text if path == "PLAN.md" else None

    event = await _seed_event(
        async_session, proj, commits=[{"modified": ["PLAN.md"]}]
    )
    event_id = event.id  # process_event 호출 전에 id 저장 — 세션 상태 변화와 무관하게 접근 가능

    sync_mod._apply_plan = boom
    try:
        await process_event(
            async_session, event,
            fetch_file=fake_fetch_file, fetch_compare=_noop_fetch_compare,
        )
    finally:
        sync_mod._apply_plan = original_apply_plan

    # DB 에서 직접 재조회 — event 객체 상태와 무관하게 영구 저장 확인
    refetched = (await async_session.execute(
        select(GitPushEvent).where(GitPushEvent.id == event_id)
    )).scalar_one()
    assert refetched.processed_at is not None
    assert refetched.error is not None
    assert "IntegrityError" in refetched.error or "Integrity" in refetched.error


async def test_process_event_no_change_when_checked_and_already_done(
    async_session: AsyncSession,
):
    """code review M-5: 이미 DONE 인 task 가 PLAN 에서 [x] → 변경 없음, TaskEvent 도 안 만듦."""
    proj = await _seed_project(async_session)
    existing = Task(
        project_id=proj.id, title="이미 완료", source=TaskSource.SYNCED_FROM_PLAN,
        external_id="task-DONE", status=TaskStatus.DONE,
    )
    async_session.add(existing)
    await async_session.commit()
    await async_session.refresh(existing)
    initial_last_sha = existing.last_commit_sha

    plan_text = "## 태스크\n\n- [x] [task-DONE] 이미 완료 — @alice\n"

    async def fake_fetch_file(repo_url, pat, sha, path):
        return plan_text if path == "PLAN.md" else None

    event = await _seed_event(
        async_session, proj, commits=[{"modified": ["PLAN.md"]}]
    )
    await process_event(
        async_session, event,
        fetch_file=fake_fetch_file, fetch_compare=_noop_fetch_compare,
    )
    await async_session.refresh(existing)

    assert existing.status == TaskStatus.DONE  # 보존
    assert existing.last_commit_sha == initial_last_sha  # last_commit_sha 도 안 바뀜
    events = (await async_session.execute(
        select(TaskEvent).where(TaskEvent.task_id == existing.id)
    )).scalars().all()
    assert len(events) == 0


async def test_collect_changed_files_uses_before_sha_when_truncated(async_session: AsyncSession):
    """commits_truncated 시 before_commit_sha 가 base 로 사용됨 (Phase 5a 보강)."""
    proj = await _seed_project(async_session)
    captured: dict[str, str] = {}

    async def fake_compare(repo_url, pat, base, head):
        captured["base"] = base
        captured["head"] = head
        return ["PLAN.md"]

    async def fake_fetch_file(repo_url, pat, sha, path):
        return None  # PLAN 변경됐다고만 알려주고 fetch 는 404 → silent skip

    event = await _seed_event(
        async_session, proj,
        head_sha="b" * 40,
        commits_truncated=True,
        commits=[{"id": "c" * 40, "modified": []}],
    )
    event.before_commit_sha = "a" * 40
    await async_session.commit()
    await async_session.refresh(event)

    await process_event(
        async_session, event,
        fetch_file=fake_fetch_file, fetch_compare=fake_compare,
    )

    assert captured["base"] == "a" * 40  # before_commit_sha 우선
    assert captured["head"] == "b" * 40


async def test_collect_changed_files_skips_null_sha_before(async_session: AsyncSession):
    """code review I-5: before_commit_sha 가 '0' * 40 (GitHub null sha) 이면 next priority 사용."""
    proj = await _seed_project(async_session)
    proj.last_synced_commit_sha = "f" * 40
    await async_session.commit()
    await async_session.refresh(proj)

    captured: dict[str, str] = {}

    async def fake_compare(repo_url, pat, base, head):
        captured["base"] = base
        return ["PLAN.md"]

    async def fake_fetch_file(repo_url, pat, sha, path):
        return None

    event = await _seed_event(
        async_session, proj,
        head_sha="b" * 40,
        commits_truncated=True,
        commits=[{"id": "c" * 40, "modified": []}],
    )
    event.before_commit_sha = "0" * 40  # GitHub null sha
    await async_session.commit()
    await async_session.refresh(event)

    await process_event(
        async_session, event,
        fetch_file=fake_fetch_file, fetch_compare=fake_compare,
    )

    # null sha skip → project.last_synced_commit_sha (= "f" * 40) 사용
    assert captured["base"] == "f" * 40


# ---------------------------------------------------------------------------
# B1 / M-6: last_synced_commit_sha update on success
# ---------------------------------------------------------------------------


async def test_process_event_updates_last_synced_on_plan_success(
    async_session: AsyncSession,
):
    """정상 처리 (PLAN 변경 reflect) 후 project.last_synced_commit_sha == event.head_commit_sha."""
    proj = await _seed_project(async_session)
    head = "f" * 40
    event = await _seed_event(
        async_session,
        proj,
        head_sha=head,
        commits=[{"id": head, "modified": ["PLAN.md"], "added": []}],
    )

    async def fake_fetch_file(repo, pat, sha, path):
        return "## 태스크\n\n- [ ] [task-001] T — @alice"

    async def fake_compare(repo, pat, base, head_sha):  # noqa: ARG001
        return ["PLAN.md"]

    await process_event(
        async_session, event, fetch_file=fake_fetch_file, fetch_compare=fake_compare,
    )

    await async_session.refresh(proj)
    assert proj.last_synced_commit_sha == head
    await async_session.refresh(event)
    assert event.processed_at is not None
    assert event.error is None


async def test_process_event_does_not_update_last_synced_on_failure(
    async_session: AsyncSession,
):
    """fetch_file 가 raise → process_event 가 catch + event.error 기록.
    이 case 에서는 last_synced_commit_sha 갱신 X (재처리 시 정확한 base 보존)."""
    proj = await _seed_project(async_session)
    proj.last_synced_commit_sha = "a" * 40  # 이전 처리분
    await async_session.commit()
    await async_session.refresh(proj)

    head = "b" * 40
    event = await _seed_event(
        async_session,
        proj,
        head_sha=head,
        commits=[{"id": head, "modified": ["PLAN.md"], "added": []}],
    )

    async def fake_fetch_file(repo, pat, sha, path):
        raise RuntimeError("github 502")

    async def fake_compare(repo, pat, base, head_sha):  # noqa: ARG001
        return ["PLAN.md"]

    await process_event(
        async_session, event, fetch_file=fake_fetch_file, fetch_compare=fake_compare,
    )

    await async_session.refresh(proj)
    assert proj.last_synced_commit_sha == "a" * 40  # 이전 값 유지
    await async_session.refresh(event)
    assert event.error is not None
    assert "RuntimeError" in event.error


# ---------------------------------------------------------------------------
# B1 / I-4 layer 2: process_event SELECT FOR UPDATE 재진입 가드
# ---------------------------------------------------------------------------

import asyncio


async def test_concurrent_process_event_only_runs_once(
    async_session: AsyncSession, upgraded_db,
):
    """같은 event 를 두 session 이 동시에 process_event 호출 → fetch 는 1번만 실행.
    FOR UPDATE row lock 으로 T2 가 T1 final commit 까지 대기 → processed_at 보고 즉시 return.
    fix 없으면 두 호출 다 fetch → counter == 2.
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    proj = await _seed_project(async_session)
    head = "c" * 40
    event = await _seed_event(
        async_session,
        proj,
        head_sha=head,
        commits=[{"id": head, "modified": ["PLAN.md"], "added": []}],
    )
    event_id = event.id

    # 같은 per-test DB 에 별도 engine 두 개 — 두 독립 세션이 row lock 경쟁하도록.
    dsn = upgraded_db["async_url"]
    engine_a = create_async_engine(dsn, echo=False)
    engine_b = create_async_engine(dsn, echo=False)
    maker_a = async_sessionmaker(engine_a, expire_on_commit=False)
    maker_b = async_sessionmaker(engine_b, expire_on_commit=False)

    counter = {"n": 0}
    release = asyncio.Event()
    t1_inside_fetch = asyncio.Event()

    async def slow_fetch_file(repo, pat, sha, path):
        counter["n"] += 1
        if counter["n"] == 1:
            t1_inside_fetch.set()
        await release.wait()
        return "## 태스크\n\n- [ ] [task-001] T — @alice"

    async def fake_compare(repo, pat, base, head_sha):  # noqa: ARG001
        return ["PLAN.md"]

    async def runner(maker):
        async with maker() as db:
            ev = await db.get(GitPushEvent, event_id)
            await process_event(
                db, ev, fetch_file=slow_fetch_file, fetch_compare=fake_compare,
            )

    async def releaser():
        # T1 이 fetch 까지 들어간 시점에 T2 도 entry FOR UPDATE 에서 대기 중이도록 시간 둠.
        await t1_inside_fetch.wait()
        await asyncio.sleep(0.4)
        release.set()

    try:
        # T1 먼저 시작해 lock 획득. T2 는 약간 후에 시작해 entry 에서 대기.
        t1 = asyncio.create_task(runner(maker_a))
        await asyncio.sleep(0.05)
        t2 = asyncio.create_task(runner(maker_b))
        rel = asyncio.create_task(releaser())
        await asyncio.gather(t1, t2, rel)
    finally:
        await engine_a.dispose()
        await engine_b.dispose()

    # 핵심: fetch 가 정확히 1번만 호출됨 — T2 는 lock 대기 → processed_at 보고 return.
    # fix 없으면 counter["n"] == 2.
    assert counter["n"] == 1, (
        f"expected fetch to be called once but was called {counter['n']} times — "
        "FOR UPDATE re-read 가 process_event entry 에 적용되지 않음"
    )


# ---------------------------------------------------------------------------
# B2: Discord sync-failure 알림 — process_event except 분기
# ---------------------------------------------------------------------------


async def test_discord_alert_called_on_failure_with_webhook_url(
    async_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
):
    """failure path + Project.discord_webhook_url set → discord_service.send_webhook 1회 호출."""
    proj = await _seed_project(async_session)
    proj.discord_webhook_url = "https://discord.com/api/webhooks/1/abc"
    await async_session.commit()
    await async_session.refresh(proj)

    head = "f" * 40
    event = await _seed_event(
        async_session, proj, head_sha=head,
        commits=[{"id": head, "modified": ["PLAN.md"], "added": []}],
    )

    async def boom_fetch(repo, pat, sha, path):
        raise RuntimeError("github 502")

    async def fake_compare(repo, pat, base, head_sha):  # noqa: ARG001
        return ["PLAN.md"]

    sent: list[tuple[str, str]] = []

    async def fake_send(content, webhook_url):
        sent.append((content, webhook_url))

    import app.services.discord_service as discord_mod
    monkeypatch.setattr(discord_mod, "send_webhook", fake_send)

    await process_event(
        async_session, event, fetch_file=boom_fetch, fetch_compare=fake_compare,
    )

    # process_event 가 commit 하면 proj/event expire — 속성 접근 전 refresh 필요.
    await async_session.refresh(proj)
    await async_session.refresh(event)

    assert len(sent) == 1
    content, url = sent[0]
    assert url == "https://discord.com/api/webhooks/1/abc"
    assert "forps sync 실패" in content
    assert proj.name in content
    assert event.branch in content
    assert head[:7] in content
    assert "RuntimeError" in content


async def test_discord_alert_skipped_when_webhook_url_missing(
    async_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
):
    """failure path + Project.discord_webhook_url IS NULL → send_webhook 호출 안 함."""
    proj = await _seed_project(async_session)
    # discord_webhook_url 미설정 (default None)
    head = "e" * 40
    event = await _seed_event(
        async_session, proj, head_sha=head,
        commits=[{"id": head, "modified": ["PLAN.md"], "added": []}],
    )

    async def boom_fetch(repo, pat, sha, path):
        raise RuntimeError("github 502")

    async def fake_compare(repo, pat, base, head_sha):  # noqa: ARG001
        return ["PLAN.md"]

    sent: list = []

    async def fake_send(content, webhook_url):
        sent.append((content, webhook_url))

    import app.services.discord_service as discord_mod
    monkeypatch.setattr(discord_mod, "send_webhook", fake_send)

    await process_event(
        async_session, event, fetch_file=boom_fetch, fetch_compare=fake_compare,
    )

    assert len(sent) == 0
    await async_session.refresh(event)
    assert event.error is not None  # failure recorded


async def test_discord_alert_not_called_on_success_path_without_relevant_files(
    async_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
):
    """success path + PLAN/handoff 와 무관한 파일만 변경 → send_webhook 호출 안 함.

    Phase 6: success path 도 push summary 알림을 보낼 수 있게 됨.
    그러나 PLAN/handoff 둘 다 변경 안 됐고 actionable change 도 없으면 무알림 (no-op push noise 방지).
    """
    proj = await _seed_project(async_session)
    proj.discord_webhook_url = "https://discord.com/api/webhooks/1/abc"
    await async_session.commit()

    head = "d" * 40
    event = await _seed_event(
        async_session, proj, head_sha=head,
        commits=[{"id": head, "modified": ["README.md"], "added": []}],
    )

    async def fake_fetch_file(repo, pat, sha, path):  # noqa: ARG001
        return None

    async def fake_compare(repo, pat, base, head_sha):  # noqa: ARG001
        return ["README.md"]

    sent: list = []

    async def fake_send(content, webhook_url):
        sent.append((content, webhook_url))

    import app.services.discord_service as discord_mod
    monkeypatch.setattr(discord_mod, "send_webhook", fake_send)

    await process_event(
        async_session, event,
        fetch_file=fake_fetch_file, fetch_compare=fake_compare,
    )

    assert len(sent) == 0
    await async_session.refresh(event)
    assert event.error is None  # success


# ---------------------------------------------------------------------------
# Phase 6: push summary 알림
# ---------------------------------------------------------------------------


async def test_push_summary_includes_all_categories(
    async_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
):
    """체크 + 롤백 + archived 섞인 push → dispatcher 호출, content 에 모든 카테고리 줄 포함."""
    proj = await _seed_project(async_session)
    proj.discord_webhook_url = "https://discord.com/api/webhooks/1/abc"
    await async_session.commit()
    await async_session.refresh(proj)

    # 기존 SYNCED tasks 시드 — 체크/롤백/archived 의 before-state
    from app.models.task import Task, TaskSource, TaskStatus
    t_check = Task(
        project_id=proj.id, title="구글 로그인",
        source=TaskSource.SYNCED_FROM_PLAN, external_id="task-001",
        status=TaskStatus.TODO,
    )
    t_unchk = Task(
        project_id=proj.id, title="결제 모듈",
        source=TaskSource.SYNCED_FROM_PLAN, external_id="task-007",
        status=TaskStatus.DONE,
    )
    t_arch = Task(
        project_id=proj.id, title="구버전 마이그레이션",
        source=TaskSource.SYNCED_FROM_PLAN, external_id="task-009",
        status=TaskStatus.TODO,
    )
    async_session.add_all([t_check, t_unchk, t_arch])
    await async_session.commit()

    head = "1" * 40
    event = await _seed_event(
        async_session, proj, head_sha=head,
        commits=[{"id": head, "modified": ["PLAN.md", "handoffs/main.md"], "added": []}],
    )

    plan_text = (
        "## 태스크\n\n"
        "- [x] [task-001] 구글 로그인 — @alice\n"
        "- [ ] [task-007] 결제 모듈 — @bob\n"
        # task-009 PLAN 에서 사라짐 → archived
    )
    handoff_text = (
        "# Handoff: main — @alice\n\n"
        "## 2026-05-01\n\n"
        "- [x] [task-001]\n"
    )

    async def fake_fetch(repo, pat, sha, path):
        if path == "PLAN.md":
            return plan_text
        if path == "handoffs/main.md":
            return handoff_text
        return None

    async def fake_compare(repo, pat, base, head_sha):  # noqa: ARG001
        return ["PLAN.md", "handoffs/main.md"]

    sent: list[tuple[str, str]] = []
    async def fake_send(content, url):
        sent.append((content, url))

    import app.services.discord_service as discord_mod
    monkeypatch.setattr(discord_mod, "send_webhook", fake_send)

    await process_event(
        async_session, event, fetch_file=fake_fetch, fetch_compare=fake_compare,
    )

    assert len(sent) == 1
    content, _ = sent[0]
    assert "📦" in content
    assert event.pusher in content
    assert event.branch in content
    assert head[:7] in content
    assert "✅ 완료" in content
    assert "[task-001] 구글 로그인" in content
    assert "↩️ 되돌림" in content
    assert "[task-007] 결제 모듈" in content
    assert "🗑️ PLAN 에서 제거" in content
    assert "[task-009] 구버전 마이그레이션" in content
    # handoff 정상 → 누락 줄 없음
    assert "handoff 누락" not in content


async def _seed_existing_task(
    db: AsyncSession, project: Project, *, external_id: str = "task-001"
) -> Task:
    """has_changes()=True 시나리오 트리거용 사전 TODO task — PLAN 에서 [x] 처리되면 checked 로 잡힘."""
    t = Task(
        project_id=project.id,
        title="기존",
        source=TaskSource.SYNCED_FROM_PLAN,
        external_id=external_id,
        status=TaskStatus.TODO,
    )
    db.add(t)
    await db.commit()
    await db.refresh(t)
    return t


async def test_push_summary_includes_handoff_missing_line(
    async_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
):
    """비-스킵 브랜치에서 PLAN 변경(checked) + handoff 부재 → ⚠️ handoff 누락 줄 발화."""
    proj = await _seed_project(async_session)
    proj.discord_webhook_url = "https://discord.com/api/webhooks/1/abc"
    await async_session.commit()
    await async_session.refresh(proj)
    await _seed_existing_task(async_session, proj)

    head = "2" * 40
    # main 은 자동 스킵 — feat/* 브랜치로 알림 발화 검증
    event = await _seed_event(
        async_session, proj, head_sha=head, branch="feat/task-001",
        commits=[{"id": head, "modified": ["PLAN.md"], "added": []}],
    )

    plan_text = "## 태스크\n\n- [x] [task-001] 기존 — @alice\n"

    async def fake_fetch(repo, pat, sha, path):
        if path == "PLAN.md":
            return plan_text
        return None  # handoff 부재

    async def fake_compare(repo, pat, base, head_sha):  # noqa: ARG001
        return ["PLAN.md", "handoffs/feat-task-001.md"]

    sent: list = []
    async def fake_send(content, url):
        sent.append((content, url))

    import app.services.discord_service as discord_mod
    monkeypatch.setattr(discord_mod, "send_webhook", fake_send)

    await process_event(
        async_session, event, fetch_file=fake_fetch, fetch_compare=fake_compare,
    )

    assert len(sent) == 1
    content, _ = sent[0]
    assert "⚠️ handoff 누락" in content
    assert "handoffs/feat-task-001.md" in content


async def test_main_branch_handoff_missing_auto_skipped(
    async_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
):
    """main 브랜치는 통합 브랜치 컨벤션으로 handoff 누락 알림 자동 스킵.

    PLAN 변경(✅ 완료) 줄은 그대로 떠야 하지만 ⚠️ handoff 누락 줄은 안 떠야 함.
    """
    proj = await _seed_project(async_session)
    proj.discord_webhook_url = "https://discord.com/api/webhooks/1/abc"
    await async_session.commit()
    await async_session.refresh(proj)
    await _seed_existing_task(async_session, proj)

    head = "3" * 40
    event = await _seed_event(
        async_session, proj, head_sha=head, branch="main",
        commits=[{"id": head, "modified": ["PLAN.md"], "added": []}],
    )

    plan_text = "## 태스크\n\n- [x] [task-001] 기존 — @alice\n"

    async def fake_fetch(repo, pat, sha, path):
        if path == "PLAN.md":
            return plan_text
        return None

    async def fake_compare(repo, pat, base, head_sha):  # noqa: ARG001
        return ["PLAN.md", "handoffs/main.md"]

    sent: list = []
    async def fake_send(content, url):
        sent.append((content, url))

    import app.services.discord_service as discord_mod
    monkeypatch.setattr(discord_mod, "send_webhook", fake_send)

    await process_event(
        async_session, event, fetch_file=fake_fetch, fetch_compare=fake_compare,
    )

    # PLAN 변경 자체는 알림 ✅ 완료 줄로 떠야 함
    assert len(sent) == 1
    content, _ = sent[0]
    assert "✅ 완료" in content
    # 다만 handoff 누락 줄은 자동 스킵
    assert "handoff 누락" not in content


async def test_user_listed_skip_branch_no_handoff_missing(
    async_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
):
    """사용자가 git_settings 에 적은 브랜치도 handoff 누락 알림 스킵."""
    proj = await _seed_project(async_session)
    proj.discord_webhook_url = "https://discord.com/api/webhooks/1/abc"
    proj.handoff_skip_branches = "develop, staging"
    await async_session.commit()
    await async_session.refresh(proj)
    await _seed_existing_task(async_session, proj)

    head = "4" * 40
    event = await _seed_event(
        async_session, proj, head_sha=head, branch="develop",
        commits=[{"id": head, "modified": ["PLAN.md"], "added": []}],
    )

    plan_text = "## 태스크\n\n- [x] [task-001] 기존 — @alice\n"

    async def fake_fetch(repo, pat, sha, path):
        if path == "PLAN.md":
            return plan_text
        return None

    async def fake_compare(repo, pat, base, head_sha):  # noqa: ARG001
        return ["PLAN.md", "handoffs/develop.md"]

    sent: list = []
    async def fake_send(content, url):
        sent.append((content, url))

    import app.services.discord_service as discord_mod
    monkeypatch.setattr(discord_mod, "send_webhook", fake_send)

    await process_event(
        async_session, event, fetch_file=fake_fetch, fetch_compare=fake_compare,
    )

    assert len(sent) == 1
    content, _ = sent[0]
    assert "handoff 누락" not in content


async def test_unlisted_branch_still_alerts(
    async_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
):
    """skip 리스트에 없는 브랜치는 누락 알림 그대로 발화 (회귀 가드)."""
    proj = await _seed_project(async_session)
    proj.discord_webhook_url = "https://discord.com/api/webhooks/1/abc"
    proj.handoff_skip_branches = "staging"  # hotfix 는 미포함
    await async_session.commit()
    await async_session.refresh(proj)
    await _seed_existing_task(async_session, proj)

    head = "5" * 40
    event = await _seed_event(
        async_session, proj, head_sha=head, branch="hotfix/x",
        commits=[{"id": head, "modified": ["PLAN.md"], "added": []}],
    )

    plan_text = "## 태스크\n\n- [x] [task-001] 기존 — @alice\n"

    async def fake_fetch(repo, pat, sha, path):
        if path == "PLAN.md":
            return plan_text
        return None

    async def fake_compare(repo, pat, base, head_sha):  # noqa: ARG001
        return ["PLAN.md", "handoffs/hotfix-x.md"]

    sent: list = []
    async def fake_send(content, url):
        sent.append((content, url))

    import app.services.discord_service as discord_mod
    monkeypatch.setattr(discord_mod, "send_webhook", fake_send)

    await process_event(
        async_session, event, fetch_file=fake_fetch, fetch_compare=fake_compare,
    )

    assert len(sent) == 1
    content, _ = sent[0]
    assert "⚠️ handoff 누락" in content


async def test_no_meaningful_plan_changes_no_handoff_missing_alert(
    async_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
):
    """PLAN.md 가 changed_files 에 있지만 의미적 변화 0건이면 누락 알림 안 뜸.

    실제 시나리오: feat 브랜치에서 이미 처리된 상태가 dev 등 통합 브랜치로 PR 머지될 때.
    forps DB 의 task 가 PLAN 상태와 동일 → _apply_plan 변화 0건 → has_changes()=False.
    """
    proj = await _seed_project(async_session)
    proj.discord_webhook_url = "https://discord.com/api/webhooks/1/abc"
    await async_session.commit()
    await async_session.refresh(proj)

    head = "6" * 40
    # main / 사용자 스킵 영향 배제 위해 비-스킵 브랜치
    event = await _seed_event(
        async_session, proj, head_sha=head, branch="feat/x",
        commits=[{"id": head, "modified": ["PLAN.md"], "added": []}],
    )

    # PLAN 에 task 1건 — 신규도 아니고 (forps DB 비어있음) checked 도 unchecked 도 아님.
    # _apply_plan 신규 INSERT 동작 시 PlanChanges 가 변화로 잡지만, plan_text 가 비어있으면
    # archived 도 없고 changes 0. 가장 단순한 시나리오는 빈 PLAN.
    plan_text = "## 태스크\n\n"

    async def fake_fetch(repo, pat, sha, path):
        if path == "PLAN.md":
            return plan_text
        return None

    async def fake_compare(repo, pat, base, head_sha):  # noqa: ARG001
        return ["PLAN.md", "handoffs/feat-x.md"]

    sent: list = []
    async def fake_send(content, url):
        sent.append((content, url))

    import app.services.discord_service as discord_mod
    monkeypatch.setattr(discord_mod, "send_webhook", fake_send)

    await process_event(
        async_session, event, fetch_file=fake_fetch, fetch_compare=fake_compare,
    )

    # 의미적 변화 0건 + handoff 누락 단독 → 알림 자체 None (외로운 누락 줄 방지)
    assert len(sent) == 0


async def test_apply_plan_resolves_assignee_on_insert(async_session: AsyncSession):
    """`@username` 이 매칭되는 User 면 신규 Task.assignee_id 가 채워짐."""
    proj = await _seed_project(async_session)
    sejong = await _seed_user(async_session, username="sejong")
    plan_text = (
        "## 태스크\n\n"
        "- [ ] [task-001] 백엔드 작업 — @sejong — `backend/app.py`\n"
    )

    async def fake_fetch_file(repo_url, pat, sha, path):  # noqa: ARG001
        return plan_text if path == "PLAN.md" else None

    event = await _seed_event(
        async_session, proj, commits=[{"modified": ["PLAN.md"]}],
    )
    await process_event(
        async_session, event,
        fetch_file=fake_fetch_file, fetch_compare=_noop_fetch_compare,
    )

    task = (await async_session.execute(
        select(Task).where(Task.external_id == "task-001")
    )).scalar_one()
    assert task.assignee_id == sejong.id


async def test_apply_plan_unknown_assignee_leaves_unset(
    async_session: AsyncSession, caplog: pytest.LogCaptureFixture,
):
    """매칭되는 User 가 없으면 assignee_id 는 None — 에러 아님, INFO 로그만."""
    proj = await _seed_project(async_session)
    plan_text = "## 태스크\n\n- [ ] [task-002] 미배정 — @ghost\n"

    async def fake_fetch_file(repo_url, pat, sha, path):  # noqa: ARG001
        return plan_text if path == "PLAN.md" else None

    event = await _seed_event(
        async_session, proj, commits=[{"modified": ["PLAN.md"]}],
    )
    await process_event(
        async_session, event,
        fetch_file=fake_fetch_file, fetch_compare=_noop_fetch_compare,
    )

    task = (await async_session.execute(
        select(Task).where(Task.external_id == "task-002")
    )).scalar_one()
    assert task.assignee_id is None
    assert any("unknown @ghost" in r.message for r in caplog.records)


async def test_apply_plan_assignee_change_emits_event(async_session: AsyncSession):
    """기존 task 의 assignee 가 PLAN 에서 바뀌면 update + ASSIGNED TaskEvent."""
    proj = await _seed_project(async_session)
    arden = await _seed_user(async_session, username="arden")
    sejong = await _seed_user(async_session, username="sejong")

    # 첫 sync — @arden
    async def fetch_v1(repo_url, pat, sha, path):  # noqa: ARG001
        return "## 태스크\n\n- [ ] [task-010] 작업 — @arden\n" if path == "PLAN.md" else None

    event1 = await _seed_event(
        async_session, proj, head_sha="b" * 40,
        commits=[{"id": "b" * 40, "modified": ["PLAN.md"]}],
    )
    await process_event(
        async_session, event1, fetch_file=fetch_v1, fetch_compare=_noop_fetch_compare,
    )
    task = (await async_session.execute(
        select(Task).where(Task.external_id == "task-010")
    )).scalar_one()
    assert task.assignee_id == arden.id

    # 두 번째 sync — @sejong 으로 변경
    async def fetch_v2(repo_url, pat, sha, path):  # noqa: ARG001
        return "## 태스크\n\n- [ ] [task-010] 작업 — @sejong\n" if path == "PLAN.md" else None

    event2 = await _seed_event(
        async_session, proj, head_sha="c" * 40,
        commits=[{"id": "c" * 40, "modified": ["PLAN.md"]}],
    )
    await process_event(
        async_session, event2, fetch_file=fetch_v2, fetch_compare=_noop_fetch_compare,
    )

    await async_session.refresh(task)
    assert task.assignee_id == sejong.id

    events = (await async_session.execute(
        select(TaskEvent).where(
            TaskEvent.task_id == task.id, TaskEvent.action == TaskEventAction.ASSIGNED,
        )
    )).scalars().all()
    assert len(events) == 1
    assert events[0].changes == {
        "previous_assignee_id": str(arden.id),
        "assignee": "sejong",
    }


async def test_apply_plan_assignee_dropped_clears(async_session: AsyncSession):
    """PLAN 에서 `@username` 이 빠지면 assignee_id 는 NULL 로 clear."""
    proj = await _seed_project(async_session)
    arden = await _seed_user(async_session, username="arden")

    async def fetch_v1(repo_url, pat, sha, path):  # noqa: ARG001
        return "## 태스크\n\n- [ ] [task-020] 작업 — @arden\n" if path == "PLAN.md" else None

    event1 = await _seed_event(
        async_session, proj, head_sha="d" * 40,
        commits=[{"id": "d" * 40, "modified": ["PLAN.md"]}],
    )
    await process_event(
        async_session, event1, fetch_file=fetch_v1, fetch_compare=_noop_fetch_compare,
    )
    task = (await async_session.execute(
        select(Task).where(Task.external_id == "task-020")
    )).scalar_one()
    assert task.assignee_id == arden.id

    async def fetch_v2(repo_url, pat, sha, path):  # noqa: ARG001
        return "## 태스크\n\n- [ ] [task-020] 작업\n" if path == "PLAN.md" else None

    event2 = await _seed_event(
        async_session, proj, head_sha="e" * 40,
        commits=[{"id": "e" * 40, "modified": ["PLAN.md"]}],
    )
    await process_event(
        async_session, event2, fetch_file=fetch_v2, fetch_compare=_noop_fetch_compare,
    )

    await async_session.refresh(task)
    assert task.assignee_id is None


async def test_push_summary_skipped_when_no_changes(
    async_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
):
    """PLAN/handoff 변경 없는 push → dispatcher 호출 안 함 (no-op push 노이즈 방지)."""
    proj = await _seed_project(async_session)
    proj.discord_webhook_url = "https://discord.com/api/webhooks/1/abc"
    await async_session.commit()
    await async_session.refresh(proj)

    head = "3" * 40
    event = await _seed_event(
        async_session, proj, head_sha=head,
        commits=[{"id": head, "modified": ["README.md"], "added": []}],  # PLAN/handoff 무관 파일
    )

    async def fake_fetch(repo, pat, sha, path):  # noqa: ARG001
        return None

    async def fake_compare(repo, pat, base, head_sha):  # noqa: ARG001
        return ["README.md"]

    sent: list = []
    async def fake_send(content, url):
        sent.append((content, url))

    import app.services.discord_service as discord_mod
    monkeypatch.setattr(discord_mod, "send_webhook", fake_send)

    await process_event(
        async_session, event, fetch_file=fake_fetch, fetch_compare=fake_compare,
    )

    assert sent == []
