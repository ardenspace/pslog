# Phase 1 — 모델/마이그레이션 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** task-automation + error-log 두 설계서가 요구하는 6개 신규 테이블 + 2개 기존 모델 확장 + enum 확장 + CHECK/UNIQUE 제약 + pg_trgm + log_events 일별 파티셔닝을 단일 alembic revision으로 도입한다. 기존 데이터 무손실.

**Architecture:** SQLAlchemy 2.0 declarative 모델 → 단일 alembic revision (raw SQL 섞음: ALTER TYPE / CHECK / partition / pg_trgm / partial index). Phase 1은 모델 + 마이그레이션 + 회귀 테스트만 — 라우터/서비스 X. 회귀 테스트는 신규 도입하는 pytest + 일회성 PostgreSQL 인스턴스 위에서 alembic up/down 시나리오로 검증.

**Tech Stack:** SQLAlchemy 2.0.25, alembic 1.13.1, asyncpg 0.30.0, pytest, pytest-asyncio, PostgreSQL 14+ (pg_trgm + declarative partitioning).

**선행 조건:**
- pslog main 브랜치, alembic head = `be8724268ae4`
- app-chak Phase 0 PR #1 머지 완료 (handoff `2026-04-27` 기록)
- 두 설계서: `docs/superpowers/specs/2026-04-26-ai-task-automation-design.md`, `docs/superpowers/specs/2026-04-26-error-log-design.md`

**중요한 계약:**
- `commit_sha` / `last_commit_sha` / `head_commit_sha` / `version_sha` = 40자 hex full (또는 nullable이면 NULL, version_sha는 `"unknown"` 도 허용). DB CHECK 제약으로 강제. error-log 설계서 Decision Log 2026-04-27(Rev3).
- `external_id`: 프로젝트 내 UNIQUE (NULL은 다중 허용 — 부분 인덱스).
- 기존 Task row는 `source = "manual"`, `archived_at = NULL`로 보존.

---

## File Structure

**신규 모델 파일 (6개):**
- `backend/app/models/handoff.py` — Handoff 모델
- `backend/app/models/git_push_event.py` — GitPushEvent 모델
- `backend/app/models/log_event.py` — LogEvent + LogLevel enum
- `backend/app/models/error_group.py` — ErrorGroup + ErrorGroupStatus enum
- `backend/app/models/log_ingest_token.py` — LogIngestToken 모델
- `backend/app/models/rate_limit_window.py` — RateLimitWindow 모델

**수정 파일:**
- `backend/app/models/project.py` — git 6 필드 추가
- `backend/app/models/task.py` — 4 필드 + TaskSource enum 추가
- `backend/app/models/task_event.py` — TaskEventAction enum 4값 추가
- `backend/app/models/__init__.py` — 신규 모델 export

**마이그레이션 (단일 revision):**
- `backend/alembic/versions/<rev>_phase1_logs_handoffs_git.py`

**테스트 인프라 (신규):**
- `backend/tests/__init__.py`
- `backend/tests/conftest.py` — async DB fixture, alembic fixture, 테스트 DB URL
- `backend/tests/test_models_smoke.py` — 모델 import / 기본 INSERT
- `backend/tests/test_migrations.py` — 회귀 (기존 데이터 보존, up/down)
- `backend/tests/test_constraints.py` — CHECK/UNIQUE 동작
- `backend/tests/test_partitioning.py` — log_events 파티션 라우팅 + 30일 pre-create
- `backend/requirements-dev.txt` — pytest 등 테스트 의존성
- `backend/pytest.ini` — pytest 설정

---

## Self-Review Notes

본 plan 작성 후 작성자(Claude)가 한 번 self-review:
- 설계서 §4.1/§4.2 데이터 모델 — 모든 필드 task에 매핑됨 (Task 1~9에 분배)
- CHECK 제약 (Decision Log 2026-04-27 Rev3) — Task 4, 5, 6, 9, 10에서 각 sha 컬럼에 명시
- pg_trgm + 부분 인덱스 — Task 10
- 파티셔닝 + pre-create — Task 11
- 회귀 테스트 (CRITICAL) — Task 12
- TaskEventAction enum 확장 (Decision Log Rev2) — Task 1 + Task 11 alembic ALTER TYPE
- 멱등성 (UNIQUE 제약) — 각 모델 task에 명시

---

## Task 0: 테스트 인프라 (pytest + async DB fixture)

**Files:**
- Create: `backend/requirements-dev.txt`
- Create: `backend/pytest.ini`
- Create: `backend/tests/__init__.py`
- Create: `backend/tests/conftest.py`
- Create: `backend/tests/test_smoke.py`

- [ ] **Step 1: 테스트 의존성 추가**

Create `backend/requirements-dev.txt`:

```
-r requirements.txt

pytest==8.3.4
pytest-asyncio==0.24.0
pytest-postgresql==6.1.1
psycopg[binary]==3.2.3
```

> psycopg는 pytest-postgresql이 동기 admin 연결용으로 요구. asyncpg는 본 코드용 그대로.

- [ ] **Step 2: pytest 설정 파일**

Create `backend/pytest.ini`:

```ini
[pytest]
testpaths = tests
python_files = test_*.py
python_classes = Test*
python_functions = test_*
asyncio_mode = auto
filterwarnings =
    ignore::DeprecationWarning
```

- [ ] **Step 3: 의존성 설치**

Run: `cd backend && pip install -r requirements-dev.txt`
Expected: 설치 성공, `pytest --version` 8.3.4 출력.

- [ ] **Step 4: tests 패키지 생성**

Create `backend/tests/__init__.py`:

```python
```

(빈 파일 — Python이 패키지로 인식할 수 있게)

- [ ] **Step 5: conftest 작성 — async session + alembic 헬퍼**

Create `backend/tests/conftest.py`:

```python
"""Phase 1 테스트 인프라.

각 테스트는 격리된 PostgreSQL DB 위에서 alembic upgrade head 후 시작.
session-scoped fixture로 부팅 1회, function-scoped 트랜잭션으로 격리.
"""
import asyncio
import os
from pathlib import Path

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from pytest_postgresql import factories
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# 1) 테스트용 PostgreSQL 인스턴스 (pytest-postgresql 가 부팅/종료)
postgresql_proc = factories.postgresql_proc(port=None)
postgresql_db = factories.postgresql("postgresql_proc")


def _async_url(pg_dsn: dict) -> str:
    """pytest-postgresql 의 동기 DSN 을 asyncpg URL로 변환."""
    return (
        f"postgresql+asyncpg://{pg_dsn['user']}:{pg_dsn['password']}"
        f"@{pg_dsn['host']}:{pg_dsn['port']}/{pg_dsn['dbname']}"
    )


@pytest.fixture(scope="function")
def alembic_config(postgresql_db) -> Config:
    """asyncpg URL 을 alembic.ini 와 동일 형식으로 환경에 주입한 Config."""
    backend_dir = Path(__file__).resolve().parent.parent
    cfg = Config(str(backend_dir / "alembic.ini"))
    info = postgresql_db.info
    sync_url = (
        f"postgresql+psycopg://{info.user}:{info.password}"
        f"@{info.host}:{info.port}/{info.dbname}"
    )
    cfg.set_main_option("sqlalchemy.url", sync_url)
    cfg.set_main_option("script_location", str(backend_dir / "alembic"))
    return cfg


@pytest.fixture(scope="function")
def upgraded_db(alembic_config, postgresql_db):
    """alembic upgrade head 적용된 DB. 각 테스트 후 drop 자동."""
    command.upgrade(alembic_config, "head")
    yield postgresql_db


@pytest_asyncio.fixture(scope="function")
async def async_session(upgraded_db) -> AsyncSession:
    """async SQLAlchemy session — upgraded_db 위에서."""
    info = upgraded_db.info
    url = (
        f"postgresql+asyncpg://{info.user}:{info.password}"
        f"@{info.host}:{info.port}/{info.dbname}"
    )
    engine = create_async_engine(url, echo=False)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        yield session
    await engine.dispose()
```

- [ ] **Step 6: smoke test 작성**

Create `backend/tests/test_smoke.py`:

```python
"""테스트 인프라 자체가 동작하는지 확인."""
import pytest
from sqlalchemy import text


def test_pytest_works():
    assert 1 + 1 == 2


def test_alembic_upgrade_head(upgraded_db):
    """현 head(`be8724268ae4`)까지 upgrade 가 깨끗이 돈다."""
    cur = upgraded_db.cursor()
    cur.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema='public' ORDER BY table_name"
    )
    tables = {row[0] for row in cur.fetchall()}
    cur.close()
    assert "users" in tables
    assert "projects" in tables
    assert "tasks" in tables
    assert "alembic_version" in tables


@pytest.mark.asyncio
async def test_async_session(async_session):
    """async fixture 가 살아있는 connection 을 준다."""
    result = await async_session.execute(text("SELECT 1"))
    assert result.scalar_one() == 1
```

- [ ] **Step 7: smoke test 실행**

Run: `cd backend && pytest tests/test_smoke.py -v`
Expected: 3 passed. 실패 시 PostgreSQL이 시스템에 깔려있는지 (`which pg_ctl`) 확인.

- [ ] **Step 8: 커밋**

```bash
git add backend/requirements-dev.txt backend/pytest.ini backend/tests/__init__.py backend/tests/conftest.py backend/tests/test_smoke.py
git commit -m "test: pytest + async DB fixture 도입 (Phase 1 회귀 테스트 인프라)"
```

---

## Task 1: 신규/확장 enum 정의 (모델 파일만)

**Files:**
- Modify: `backend/app/models/task.py` — TaskSource enum 추가
- Modify: `backend/app/models/task_event.py` — TaskEventAction 4값 추가
- Test: `backend/tests/test_enums.py`

> 신규 enum 중 LogLevel / ErrorGroupStatus 는 자기 모델 파일과 함께 정의 (Task 7, 9). 본 task는 기존 모델에 영향 주는 enum만.

- [ ] **Step 1: 실패 테스트 작성**

Create `backend/tests/test_enums.py`:

```python
"""신규/확장 enum 정의 검증 (모델 파일만, alembic 은 Task 11에서)."""
from app.models.task import TaskSource
from app.models.task_event import TaskEventAction


def test_task_source_values():
    assert TaskSource.MANUAL.value == "manual"
    assert TaskSource.SYNCED_FROM_PLAN.value == "synced_from_plan"
    assert {s.value for s in TaskSource} == {"manual", "synced_from_plan"}


def test_task_event_action_existing_preserved():
    """기존 6값이 그대로 살아있어야 한다."""
    assert TaskEventAction.CREATED.value == "created"
    assert TaskEventAction.UPDATED.value == "updated"
    assert TaskEventAction.STATUS_CHANGED.value == "status_changed"
    assert TaskEventAction.ASSIGNED.value == "assigned"
    assert TaskEventAction.COMMENTED.value == "commented"
    assert TaskEventAction.DELETED.value == "deleted"


def test_task_event_action_new_values():
    """Decision Log 2026-04-26 Rev2: 4값 추가."""
    assert TaskEventAction.SYNCED_FROM_PLAN.value == "synced_from_plan"
    assert TaskEventAction.CHECKED_BY_COMMIT.value == "checked_by_commit"
    assert TaskEventAction.UNCHECKED_BY_COMMIT.value == "unchecked_by_commit"
    assert TaskEventAction.ARCHIVED_FROM_PLAN.value == "archived_from_plan"
```

- [ ] **Step 2: 테스트 실행 — 실패 확인**

Run: `cd backend && pytest tests/test_enums.py -v`
Expected: ImportError 또는 AttributeError (TaskSource 미존재).

- [ ] **Step 3: TaskSource enum 추가**

Edit `backend/app/models/task.py` — `TaskStatus` 클래스 정의 직후에 추가:

```python
class TaskSource(str, enum.Enum):
    MANUAL = "manual"
    SYNCED_FROM_PLAN = "synced_from_plan"
```

- [ ] **Step 4: TaskEventAction 4값 추가**

Edit `backend/app/models/task_event.py` — `TaskEventAction` 클래스에 4값 추가 (마지막 `DELETED` 아래에):

```python
class TaskEventAction(str, enum.Enum):
    CREATED = "created"
    UPDATED = "updated"
    STATUS_CHANGED = "status_changed"
    ASSIGNED = "assigned"
    COMMENTED = "commented"
    DELETED = "deleted"
    # Phase 1 — task-automation 설계서 Decision Log 2026-04-26 Rev2
    SYNCED_FROM_PLAN = "synced_from_plan"
    CHECKED_BY_COMMIT = "checked_by_commit"
    UNCHECKED_BY_COMMIT = "unchecked_by_commit"
    ARCHIVED_FROM_PLAN = "archived_from_plan"
```

- [ ] **Step 5: 테스트 실행 — 통과 확인**

Run: `cd backend && pytest tests/test_enums.py -v`
Expected: 3 passed.

- [ ] **Step 6: 커밋**

```bash
git add backend/app/models/task.py backend/app/models/task_event.py backend/tests/test_enums.py
git commit -m "feat(models): TaskSource enum + TaskEventAction 4값 확장"
```

---

## Task 2: Project 6 필드 확장 (모델만)

**Files:**
- Modify: `backend/app/models/project.py`
- Test: `backend/tests/test_project_model.py`

- [ ] **Step 1: 실패 테스트 작성**

Create `backend/tests/test_project_model.py`:

```python
"""Project 모델 git 6 필드 확장 검증 (모델만, alembic 은 Task 11)."""
from sqlalchemy.orm import Mapped

from app.models.project import Project


def test_project_has_git_fields():
    """6 필드가 모델에 정의되어 있어야 한다."""
    annotations = Project.__annotations__
    assert "git_repo_url" in annotations
    assert "git_default_branch" in annotations
    assert "plan_path" in annotations
    assert "handoff_dir" in annotations
    assert "last_synced_commit_sha" in annotations
    assert "webhook_secret_encrypted" in annotations


def test_project_git_default_branch_default():
    """git_default_branch 의 default 값이 'main'."""
    p = Project(workspace_id=None, name="t")  # type: ignore[arg-type]
    assert p.git_default_branch == "main"
    assert p.plan_path == "PLAN.md"
    assert p.handoff_dir == "handoffs/"
```

- [ ] **Step 2: 테스트 실행 — 실패 확인**

Run: `cd backend && pytest tests/test_project_model.py -v`
Expected: KeyError (필드 미존재).

- [ ] **Step 3: Project 모델 확장**

Edit `backend/app/models/project.py` — `discord_webhook_url` 라인 다음에 추가:

```python
    discord_webhook_url: Mapped[str | None] = mapped_column(default=None)

    # Phase 1 — task-automation 설계서 §4.1
    git_repo_url: Mapped[str | None] = mapped_column(default=None)
    git_default_branch: Mapped[str] = mapped_column(default="main")
    plan_path: Mapped[str] = mapped_column(default="PLAN.md")
    handoff_dir: Mapped[str] = mapped_column(default="handoffs/")
    last_synced_commit_sha: Mapped[str | None] = mapped_column(default=None)
    webhook_secret_encrypted: Mapped[bytes | None] = mapped_column(default=None)
```

> `webhook_secret_encrypted`는 Fernet 암호문(bytes). LargeBinary 컬럼으로 alembic 마이그레이션에서 매핑 (Task 11).

- [ ] **Step 4: 테스트 실행 — 통과 확인**

Run: `cd backend && pytest tests/test_project_model.py -v`
Expected: 2 passed.

- [ ] **Step 5: 커밋**

```bash
git add backend/app/models/project.py backend/tests/test_project_model.py
git commit -m "feat(models): Project git 6 필드 확장 (repo_url, branch, paths, secret)"
```

---

## Task 3: Task 4 필드 확장 (모델만)

**Files:**
- Modify: `backend/app/models/task.py`
- Test: `backend/tests/test_task_model.py`

- [ ] **Step 1: 실패 테스트 작성**

Create `backend/tests/test_task_model.py`:

```python
"""Task 모델 4 필드 확장 검증 (모델만, alembic 은 Task 11)."""
from datetime import datetime

from app.models.task import Task, TaskSource


def test_task_has_new_fields():
    annotations = Task.__annotations__
    assert "source" in annotations
    assert "external_id" in annotations
    assert "last_commit_sha" in annotations
    assert "archived_at" in annotations


def test_task_source_default_manual():
    """기존 데이터 호환: 새 컬럼 default = MANUAL."""
    t = Task(project_id=None, title="x")  # type: ignore[arg-type]
    assert t.source == TaskSource.MANUAL
    assert t.external_id is None
    assert t.last_commit_sha is None
    assert t.archived_at is None
```

- [ ] **Step 2: 테스트 실행 — 실패 확인**

Run: `cd backend && pytest tests/test_task_model.py -v`
Expected: KeyError 또는 AttributeError.

- [ ] **Step 3: Task 모델 확장**

Edit `backend/app/models/task.py` — `due_date` 라인 다음에 추가:

```python
    due_date: Mapped[date | None]

    # Phase 1 — task-automation 설계서 §4.1
    source: Mapped[TaskSource] = mapped_column(default=TaskSource.MANUAL)
    external_id: Mapped[str | None] = mapped_column(default=None)
    last_commit_sha: Mapped[str | None] = mapped_column(default=None)
    archived_at: Mapped[datetime | None] = mapped_column(default=None)
```

- [ ] **Step 4: 테스트 실행 — 통과 확인**

Run: `cd backend && pytest tests/test_task_model.py -v`
Expected: 2 passed.

- [ ] **Step 5: 커밋**

```bash
git add backend/app/models/task.py backend/tests/test_task_model.py
git commit -m "feat(models): Task 4 필드 확장 (source, external_id, last_commit_sha, archived_at)"
```

---

## Task 4: Handoff 모델

**Files:**
- Create: `backend/app/models/handoff.py`
- Modify: `backend/app/models/__init__.py`
- Test: `backend/tests/test_handoff_model.py`

- [ ] **Step 1: 실패 테스트 작성**

Create `backend/tests/test_handoff_model.py`:

```python
"""Handoff 모델 정의 검증 (모델만)."""
from app.models.handoff import Handoff


def test_handoff_fields():
    a = Handoff.__annotations__
    for f in [
        "id", "project_id", "branch", "author_user_id", "author_git_login",
        "commit_sha", "pushed_at", "raw_content", "parsed_tasks", "free_notes",
    ]:
        assert f in a, f"Handoff 모델에 {f} 필드 누락"


def test_handoff_in_models_init():
    from app.models import Handoff as Exported
    assert Exported is Handoff
```

- [ ] **Step 2: 테스트 실행 — 실패 확인**

Run: `cd backend && pytest tests/test_handoff_model.py -v`
Expected: ImportError.

- [ ] **Step 3: Handoff 모델 작성**

Create `backend/app/models/handoff.py`:

```python
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, Text
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Handoff(Base):
    """git push 마다 1행 INSERT — handoff 파일 파싱 결과 보존.

    설계서: 2026-04-26-ai-task-automation-design.md §4.2
    UNIQUE (project_id, commit_sha) — webhook 재전송 멱등성.
    commit_sha 는 40자 hex full (CHECK 제약 alembic 에서).
    """

    __tablename__ = "handoffs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))

    branch: Mapped[str]
    author_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    author_git_login: Mapped[str]
    commit_sha: Mapped[str]
    pushed_at: Mapped[datetime]

    raw_content: Mapped[str | None] = mapped_column(Text)  # 30일 후 NULL (별도 GC, Phase 후반)
    parsed_tasks: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)
    free_notes: Mapped[dict[str, Any] | None] = mapped_column(JSON)

    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
```

- [ ] **Step 4: __init__.py 에 export 추가**

Edit `backend/app/models/__init__.py`:

```python
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceMember
from app.models.project import Project, ProjectMember
from app.models.task import Task, Comment
from app.models.share_link import ShareLink
from app.models.task_event import TaskEvent
from app.models.handoff import Handoff

__all__ = [
    "User",
    "Workspace",
    "WorkspaceMember",
    "Project",
    "ProjectMember",
    "Task",
    "Comment",
    "ShareLink",
    "TaskEvent",
    "Handoff",
]
```

- [ ] **Step 5: 테스트 실행 — 통과 확인**

Run: `cd backend && pytest tests/test_handoff_model.py -v`
Expected: 2 passed.

- [ ] **Step 6: 커밋**

```bash
git add backend/app/models/handoff.py backend/app/models/__init__.py backend/tests/test_handoff_model.py
git commit -m "feat(models): Handoff 모델 (push 단위 보존, UNIQUE project_id+commit_sha)"
```

---

## Task 5: GitPushEvent 모델

**Files:**
- Create: `backend/app/models/git_push_event.py`
- Modify: `backend/app/models/__init__.py`
- Test: `backend/tests/test_git_push_event_model.py`

- [ ] **Step 1: 실패 테스트 작성**

Create `backend/tests/test_git_push_event_model.py`:

```python
from app.models.git_push_event import GitPushEvent


def test_git_push_event_fields():
    a = GitPushEvent.__annotations__
    for f in [
        "id", "project_id", "branch", "head_commit_sha", "commits",
        "commits_truncated", "pusher", "received_at", "processed_at", "error",
    ]:
        assert f in a, f"GitPushEvent 모델에 {f} 필드 누락"


def test_git_push_event_export():
    from app.models import GitPushEvent as Exported
    assert Exported is GitPushEvent
```

- [ ] **Step 2: 테스트 실행 — 실패 확인**

Run: `cd backend && pytest tests/test_git_push_event_model.py -v`
Expected: ImportError.

- [ ] **Step 3: GitPushEvent 모델 작성**

Create `backend/app/models/git_push_event.py`:

```python
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, Text
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class GitPushEvent(Base):
    """GitHub webhook payload 의 raw 보존.

    설계서: 2026-04-26-ai-task-automation-design.md §4.2
    UNIQUE (project_id, head_commit_sha) — 멱등성.
    head_commit_sha 40자 hex full (CHECK 제약 alembic).
    commits_truncated == True 면 sync 단계에서 Compare API 호출.
    """

    __tablename__ = "git_push_events"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))

    branch: Mapped[str]
    head_commit_sha: Mapped[str]
    commits: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)
    commits_truncated: Mapped[bool] = mapped_column(default=False)
    pusher: Mapped[str]

    received_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    processed_at: Mapped[datetime | None] = mapped_column(default=None)
    error: Mapped[str | None] = mapped_column(Text, default=None)
```

- [ ] **Step 4: __init__.py export 추가**

Edit `backend/app/models/__init__.py`: `from app.models.handoff import Handoff` 다음 줄에 `from app.models.git_push_event import GitPushEvent` 추가, `__all__` 에 `"GitPushEvent"` 추가.

- [ ] **Step 5: 테스트 통과 확인**

Run: `cd backend && pytest tests/test_git_push_event_model.py -v`
Expected: 2 passed.

- [ ] **Step 6: 커밋**

```bash
git add backend/app/models/git_push_event.py backend/app/models/__init__.py backend/tests/test_git_push_event_model.py
git commit -m "feat(models): GitPushEvent 모델 (webhook raw 보존, commits_truncated 플래그)"
```

---

## Task 6: LogIngestToken 모델

**Files:**
- Create: `backend/app/models/log_ingest_token.py`
- Modify: `backend/app/models/__init__.py`
- Test: `backend/tests/test_log_ingest_token_model.py`

- [ ] **Step 1: 실패 테스트 작성**

Create `backend/tests/test_log_ingest_token_model.py`:

```python
from app.models.log_ingest_token import LogIngestToken


def test_log_ingest_token_fields():
    a = LogIngestToken.__annotations__
    for f in [
        "id", "project_id", "name", "secret_hash",
        "created_at", "last_used_at", "revoked_at", "rate_limit_per_minute",
    ]:
        assert f in a, f"LogIngestToken 모델에 {f} 필드 누락"


def test_log_ingest_token_default_rate_limit():
    t = LogIngestToken(project_id=None, name="t", secret_hash="h")  # type: ignore[arg-type]
    assert t.rate_limit_per_minute == 600


def test_log_ingest_token_export():
    from app.models import LogIngestToken as Exported
    assert Exported is LogIngestToken
```

- [ ] **Step 2: 테스트 실행 — 실패 확인**

Run: `cd backend && pytest tests/test_log_ingest_token_model.py -v`
Expected: ImportError.

- [ ] **Step 3: 모델 작성**

Create `backend/app/models/log_ingest_token.py`:

```python
import uuid
from datetime import datetime

from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class LogIngestToken(Base):
    """프로젝트별 로그 수신 토큰.

    설계서: 2026-04-26-error-log-design.md §4.1
    토큰 평문 = "<key_id>.<secret>". key_id == row.id (UUID 문자열).
    secret_hash 는 bcrypt(secret) — 평문은 발급 시 1회만 응답.
    """

    __tablename__ = "log_ingest_tokens"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))

    name: Mapped[str]
    secret_hash: Mapped[str]

    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(default=None)
    revoked_at: Mapped[datetime | None] = mapped_column(default=None)
    rate_limit_per_minute: Mapped[int] = mapped_column(default=600)
```

- [ ] **Step 4: __init__.py export 추가**

Edit `backend/app/models/__init__.py`: import + `__all__` 에 `"LogIngestToken"` 추가.

- [ ] **Step 5: 테스트 통과 확인**

Run: `cd backend && pytest tests/test_log_ingest_token_model.py -v`
Expected: 3 passed.

- [ ] **Step 6: 커밋**

```bash
git add backend/app/models/log_ingest_token.py backend/app/models/__init__.py backend/tests/test_log_ingest_token_model.py
git commit -m "feat(models): LogIngestToken 모델 (key_id + bcrypt secret_hash)"
```

---

## Task 7: RateLimitWindow 모델 (composite PK)

**Files:**
- Create: `backend/app/models/rate_limit_window.py`
- Modify: `backend/app/models/__init__.py`
- Test: `backend/tests/test_rate_limit_window_model.py`

- [ ] **Step 1: 실패 테스트 작성**

Create `backend/tests/test_rate_limit_window_model.py`:

```python
from app.models.rate_limit_window import RateLimitWindow


def test_rate_limit_window_fields():
    a = RateLimitWindow.__annotations__
    for f in ["project_id", "token_id", "window_start", "event_count"]:
        assert f in a, f"RateLimitWindow 모델에 {f} 필드 누락"


def test_rate_limit_window_composite_pk():
    """PRIMARY KEY (project_id, token_id, window_start)."""
    pk_cols = {c.name for c in RateLimitWindow.__table__.primary_key.columns}
    assert pk_cols == {"project_id", "token_id", "window_start"}


def test_rate_limit_window_export():
    from app.models import RateLimitWindow as Exported
    assert Exported is RateLimitWindow
```

- [ ] **Step 2: 테스트 실행 — 실패 확인**

Run: `cd backend && pytest tests/test_rate_limit_window_model.py -v`
Expected: ImportError.

- [ ] **Step 3: 모델 작성**

Create `backend/app/models/rate_limit_window.py`:

```python
import uuid
from datetime import datetime

from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class RateLimitWindow(Base):
    """분당 카운터 — log-ingest 의 PostgreSQL UPSERT 기반 rate limit.

    설계서: 2026-04-26-error-log-design.md §4.1
    PRIMARY KEY (project_id, token_id, window_start) — 분 단위 truncate.
    24시간 지난 row 는 별도 cron 으로 GC (Phase 7).
    """

    __tablename__ = "rate_limit_windows"

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True
    )
    token_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("log_ingest_tokens.id", ondelete="CASCADE"), primary_key=True
    )
    window_start: Mapped[datetime] = mapped_column(primary_key=True)
    event_count: Mapped[int] = mapped_column(default=0)
```

- [ ] **Step 4: __init__.py export 추가**

Edit `backend/app/models/__init__.py`: import + `__all__` 에 `"RateLimitWindow"` 추가.

- [ ] **Step 5: 테스트 통과 확인**

Run: `cd backend && pytest tests/test_rate_limit_window_model.py -v`
Expected: 3 passed.

- [ ] **Step 6: 커밋**

```bash
git add backend/app/models/rate_limit_window.py backend/app/models/__init__.py backend/tests/test_rate_limit_window_model.py
git commit -m "feat(models): RateLimitWindow 모델 (composite PK, UPSERT 카운터)"
```

---

## Task 8: ErrorGroup 모델 + ErrorGroupStatus enum

**Files:**
- Create: `backend/app/models/error_group.py`
- Modify: `backend/app/models/__init__.py`
- Test: `backend/tests/test_error_group_model.py`

- [ ] **Step 1: 실패 테스트 작성**

Create `backend/tests/test_error_group_model.py`:

```python
from app.models.error_group import ErrorGroup, ErrorGroupStatus


def test_error_group_status_enum():
    """설계서 §4.1 — 4 상태."""
    assert {s.value for s in ErrorGroupStatus} == {"open", "resolved", "ignored", "regressed"}


def test_error_group_fields():
    a = ErrorGroup.__annotations__
    for f in [
        "id", "project_id", "fingerprint", "exception_class",
        "exception_message_sample", "first_seen_at", "first_seen_version_sha",
        "last_seen_at", "last_seen_version_sha", "event_count", "status",
        "resolved_at", "resolved_by_user_id", "resolved_in_version_sha",
        "last_alerted_new_at", "last_alerted_spike_at", "last_alerted_regression_at",
    ]:
        assert f in a, f"ErrorGroup 모델에 {f} 필드 누락"


def test_error_group_default_status_open():
    g = ErrorGroup(
        project_id=None, fingerprint="x", exception_class="X",  # type: ignore[arg-type]
        first_seen_version_sha="a"*40, last_seen_version_sha="a"*40,
    )
    assert g.status == ErrorGroupStatus.OPEN
    assert g.event_count == 0


def test_error_group_export():
    from app.models import ErrorGroup as Exported
    assert Exported is ErrorGroup
```

- [ ] **Step 2: 테스트 실행 — 실패 확인**

Run: `cd backend && pytest tests/test_error_group_model.py -v`
Expected: ImportError.

- [ ] **Step 3: 모델 작성**

Create `backend/app/models/error_group.py`:

```python
import enum
import uuid
from datetime import datetime

from sqlalchemy import BigInteger, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ErrorGroupStatus(str, enum.Enum):
    OPEN = "open"
    RESOLVED = "resolved"
    IGNORED = "ignored"
    REGRESSED = "regressed"


class ErrorGroup(Base):
    """fingerprint 별 에러 집계 (롤업 캐시).

    설계서: 2026-04-26-error-log-design.md §4.1
    UNIQUE (project_id, fingerprint).
    *_version_sha 는 40자 hex full 또는 'unknown' (CHECK 제약 alembic).
    """

    __tablename__ = "error_groups"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))

    fingerprint: Mapped[str]
    exception_class: Mapped[str]
    exception_message_sample: Mapped[str | None] = mapped_column(Text)

    first_seen_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    first_seen_version_sha: Mapped[str]
    last_seen_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    last_seen_version_sha: Mapped[str]

    event_count: Mapped[int] = mapped_column(BigInteger, default=0)
    status: Mapped[ErrorGroupStatus] = mapped_column(default=ErrorGroupStatus.OPEN)

    resolved_at: Mapped[datetime | None] = mapped_column(default=None)
    resolved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    resolved_in_version_sha: Mapped[str | None] = mapped_column(default=None)

    last_alerted_new_at: Mapped[datetime | None] = mapped_column(default=None)
    last_alerted_spike_at: Mapped[datetime | None] = mapped_column(default=None)
    last_alerted_regression_at: Mapped[datetime | None] = mapped_column(default=None)
```

- [ ] **Step 4: __init__.py export 추가**

Edit `backend/app/models/__init__.py`: import (`ErrorGroup`, `ErrorGroupStatus`도 함께) + `__all__` 에 `"ErrorGroup"`, `"ErrorGroupStatus"` 추가.

- [ ] **Step 5: 테스트 통과 확인**

Run: `cd backend && pytest tests/test_error_group_model.py -v`
Expected: 4 passed.

- [ ] **Step 6: 커밋**

```bash
git add backend/app/models/error_group.py backend/app/models/__init__.py backend/tests/test_error_group_model.py
git commit -m "feat(models): ErrorGroup + ErrorGroupStatus enum (4 상태)"
```

---

## Task 9: LogEvent 모델 + LogLevel enum

**Files:**
- Create: `backend/app/models/log_event.py`
- Modify: `backend/app/models/__init__.py`
- Test: `backend/tests/test_log_event_model.py`

> 파티셔닝은 alembic raw SQL (Task 11). SQLAlchemy 모델 자체는 일반 테이블처럼 매핑.

- [ ] **Step 1: 실패 테스트 작성**

Create `backend/tests/test_log_event_model.py`:

```python
from app.models.log_event import LogEvent, LogLevel


def test_log_level_enum():
    """설계서 §4.1 — 5 레벨."""
    assert {l.value for l in LogLevel} == {"debug", "info", "warning", "error", "critical"}


def test_log_event_fields():
    a = LogEvent.__annotations__
    for f in [
        "id", "project_id", "level", "message", "logger_name",
        "version_sha", "environment", "hostname",
        "emitted_at", "received_at",
        "exception_class", "exception_message", "stack_trace", "stack_frames",
        "fingerprint", "fingerprinted_at",
        "user_id_external", "request_id", "extra",
    ]:
        assert f in a, f"LogEvent 모델에 {f} 필드 누락"


def test_log_event_export():
    from app.models import LogEvent as Exported, LogLevel as ExportedLevel
    assert Exported is LogEvent
    assert ExportedLevel is LogLevel
```

- [ ] **Step 2: 테스트 실행 — 실패 확인**

Run: `cd backend && pytest tests/test_log_event_model.py -v`
Expected: ImportError.

- [ ] **Step 3: 모델 작성**

Create `backend/app/models/log_event.py`:

```python
import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, Text
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class LogLevel(str, enum.Enum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class LogEvent(Base):
    """수신한 로그 한 줄.

    설계서: 2026-04-26-error-log-design.md §4.1
    PostgreSQL declarative range partition by received_at — Task 11 alembic raw SQL.
    SQLAlchemy 측은 일반 테이블처럼 매핑 (parent table).
    version_sha 는 40자 hex full 또는 'unknown' (CHECK 제약 alembic).
    fingerprint / fingerprinted_at 은 ERROR↑ 이벤트만 BackgroundTask 가 채움.
    """

    __tablename__ = "log_events"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))

    level: Mapped[LogLevel]
    message: Mapped[str] = mapped_column(Text)
    logger_name: Mapped[str]
    version_sha: Mapped[str]
    environment: Mapped[str]
    hostname: Mapped[str]

    emitted_at: Mapped[datetime]
    received_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)

    # 에러 전용
    exception_class: Mapped[str | None] = mapped_column(default=None)
    exception_message: Mapped[str | None] = mapped_column(Text, default=None)
    stack_trace: Mapped[str | None] = mapped_column(Text, default=None)
    stack_frames: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, default=None)
    fingerprint: Mapped[str | None] = mapped_column(default=None)
    fingerprinted_at: Mapped[datetime | None] = mapped_column(default=None)

    # 선택
    user_id_external: Mapped[str | None] = mapped_column(default=None)
    request_id: Mapped[str | None] = mapped_column(default=None)
    extra: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=None)
```

- [ ] **Step 4: __init__.py export 추가**

Edit `backend/app/models/__init__.py`: import + `__all__` 에 `"LogEvent"`, `"LogLevel"` 추가.

- [ ] **Step 5: 테스트 통과 확인**

Run: `cd backend && pytest tests/test_log_event_model.py -v`
Expected: 3 passed.

- [ ] **Step 6: 커밋**

```bash
git add backend/app/models/log_event.py backend/app/models/__init__.py backend/tests/test_log_event_model.py
git commit -m "feat(models): LogEvent + LogLevel enum (파티셔닝은 alembic 단계)"
```

---

## Task 10: alembic revision 작성 — DDL (1) 기존 enum/table 확장

**Files:**
- Create: `backend/alembic/versions/<rev>_phase1_logs_handoffs_git.py`

> Task 10/11 둘 다 같은 revision 파일에 작업한다. Task 10 = `upgrade()` 의 1~3번째 블록 (enum 확장 + Project/Task 컬럼 + 부분 인덱스 + CHECK), Task 11 = 신규 테이블 + 파티셔닝.

- [ ] **Step 1: revision 파일 생성**

Run: `cd backend && alembic revision -m "phase1_logs_handoffs_git"`
Expected: `backend/alembic/versions/<해시>_phase1_logs_handoffs_git.py` 생성. 해시는 alembic 자동.

생성된 파일의 `down_revision` 이 `'be8724268ae4'` 인지 확인 — 아니면 수동 수정.

> 본 plan 의 후속 step 들은 이 신규 파일을 `migration_file` 로 칭함.

- [ ] **Step 2: revision 헤더 + import 작성**

`migration_file` 의 상단을 다음으로 교체:

```python
"""phase1: logs + handoffs + git push events + project/task git fields

Revision ID: <자동>
Revises: be8724268ae4
Create Date: 2026-04-28 ...

설계서:
- docs/superpowers/specs/2026-04-26-ai-task-automation-design.md §4
- docs/superpowers/specs/2026-04-26-error-log-design.md §4

본 revision 은 단일 PR 머지를 의도. raw SQL 다수 사용 — alembic 자동 생성으로
표현 불가능한 부분(ALTER TYPE ADD VALUE / CHECK 정규식 / pg_trgm /
declarative partition / partial index).
"""
from typing import Sequence, Union
from datetime import datetime, timedelta

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = '<자동 생성된 해시 그대로>'
down_revision: Union[str, None] = 'be8724268ae4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
```

- [ ] **Step 3: upgrade() 1번 블록 — TaskEventAction enum 4값 추가**

`migration_file` 에 `def upgrade() -> None:` 정의:

```python
def upgrade() -> None:
    # ── 1) 기존 enum 확장: TaskEventAction ────────────────────────────
    # PostgreSQL ALTER TYPE ... ADD VALUE 는 트랜잭션 안에서 못 돌리므로
    # autocommit 블록으로 분리.
    with op.get_context().autocommit_block():
        for value in [
            "synced_from_plan",
            "checked_by_commit",
            "unchecked_by_commit",
            "archived_from_plan",
        ]:
            op.execute(f"ALTER TYPE taskeventaction ADD VALUE IF NOT EXISTS '{value}'")
```

- [ ] **Step 4: upgrade() 2번 블록 — 신규 enum 타입 (TaskSource, LogLevel, ErrorGroupStatus)**

같은 `upgrade()` 함수 안에 이어서:

```python
    # ── 2) 신규 enum 타입 ───────────────────────────────────────────
    task_source = postgresql.ENUM(
        "manual", "synced_from_plan", name="tasksource", create_type=False
    )
    task_source.create(op.get_bind(), checkfirst=True)

    log_level = postgresql.ENUM(
        "debug", "info", "warning", "error", "critical",
        name="loglevel", create_type=False,
    )
    log_level.create(op.get_bind(), checkfirst=True)

    error_group_status = postgresql.ENUM(
        "open", "resolved", "ignored", "regressed",
        name="errorgroupstatus", create_type=False,
    )
    error_group_status.create(op.get_bind(), checkfirst=True)
```

- [ ] **Step 5: upgrade() 3번 블록 — Project 6 컬럼 + Task 4 컬럼 + 부분 인덱스 + CHECK 제약**

이어서:

```python
    # ── 3) Project 6 컬럼 추가 ─────────────────────────────────────
    op.add_column("projects", sa.Column("git_repo_url", sa.String(), nullable=True))
    op.add_column(
        "projects",
        sa.Column("git_default_branch", sa.String(), nullable=False, server_default="main"),
    )
    op.add_column(
        "projects",
        sa.Column("plan_path", sa.String(), nullable=False, server_default="PLAN.md"),
    )
    op.add_column(
        "projects",
        sa.Column("handoff_dir", sa.String(), nullable=False, server_default="handoffs/"),
    )
    op.add_column("projects", sa.Column("last_synced_commit_sha", sa.String(), nullable=True))
    op.add_column(
        "projects", sa.Column("webhook_secret_encrypted", sa.LargeBinary(), nullable=True)
    )

    # ── 4) Task 4 컬럼 + UNIQUE 부분 인덱스 + CHECK 제약 ──────────
    op.add_column(
        "tasks",
        sa.Column(
            "source",
            postgresql.ENUM("manual", "synced_from_plan", name="tasksource", create_type=False),
            nullable=False,
            server_default="manual",
        ),
    )
    op.add_column("tasks", sa.Column("external_id", sa.String(), nullable=True))
    op.add_column("tasks", sa.Column("last_commit_sha", sa.String(), nullable=True))
    op.add_column("tasks", sa.Column("archived_at", sa.DateTime(), nullable=True))

    # external_id 프로젝트 내 UNIQUE (NULL 다중 허용 — 부분 인덱스)
    op.create_index(
        "idx_task_project_external_id",
        "tasks",
        ["project_id", "external_id"],
        unique=True,
        postgresql_where=sa.text("external_id IS NOT NULL"),
    )
    # last_commit_sha 형식 (40자 hex 또는 NULL)
    op.create_check_constraint(
        "ck_task_last_commit_sha_format",
        "tasks",
        "last_commit_sha IS NULL OR last_commit_sha ~ '^[0-9a-f]{40}$'",
    )
```

> Task 11 의 step 들이 같은 `upgrade()` 함수에 계속 추가된다.

- [ ] **Step 6: 본 task 분량까지 alembic 검증 (downgrade 는 Task 12에서 한꺼번에 작성)**

임시로 빈 `downgrade()` 추가:

```python
def downgrade() -> None:
    raise NotImplementedError("Task 12 에서 작성")
```

Run: `cd backend && pytest tests/test_smoke.py -v`
Expected: 3 passed (alembic upgrade head 가 깨끗이 돈다 — 본 task 의 변경 포함).

> alembic 자체 syntax 오류만 잡는 단계. 실제 회귀 검증은 Task 12.

- [ ] **Step 7: 본 단계 임시 커밋 (다음 task 들에서 같은 파일 계속 추가)**

```bash
git add backend/alembic/versions/*_phase1_logs_handoffs_git.py
git commit -m "feat(alembic): phase1 — enum 확장 + Project/Task 컬럼/제약 (WIP)"
```

> WIP 커밋이지만 단계별로 나눠서 머지 시 검토 용이.

---

## Task 11: alembic revision 작성 — DDL (2) 신규 테이블 6개 + 파티셔닝 + pg_trgm

**Files:**
- Modify: Task 10 의 `migration_file` (같은 파일에 이어서)
- Test: `backend/tests/test_partitioning.py`

- [ ] **Step 1: 실패 테스트 작성 — 파티션 라우팅 검증**

Create `backend/tests/test_partitioning.py`:

```python
"""log_events 파티셔닝 검증.

- parent table 은 PARTITION BY RANGE (received_at)
- 다음 30일치 daily partition pre-create
- INSERT 가 올바른 파티션으로 라우팅
"""
from datetime import datetime, timedelta, timezone

import pytest


def test_log_events_is_partitioned(upgraded_db):
    cur = upgraded_db.cursor()
    cur.execute(
        "SELECT relkind FROM pg_class WHERE relname = 'log_events'"
    )
    relkind = cur.fetchone()[0]
    cur.close()
    assert relkind == "p", f"log_events 가 partitioned table 이 아님 (relkind={relkind})"


def test_log_events_has_30_day_partitions(upgraded_db):
    cur = upgraded_db.cursor()
    cur.execute(
        "SELECT count(*) FROM pg_inherits "
        "WHERE inhparent = 'log_events'::regclass"
    )
    n = cur.fetchone()[0]
    cur.close()
    # 30일치 + 오늘 = 31. 보수적으로 30 이상.
    assert n >= 30, f"파티션 수 {n} < 30"


def test_pg_trgm_extension_enabled(upgraded_db):
    cur = upgraded_db.cursor()
    cur.execute("SELECT extname FROM pg_extension WHERE extname = 'pg_trgm'")
    row = cur.fetchone()
    cur.close()
    assert row is not None, "pg_trgm extension 미활성"
```

- [ ] **Step 2: 테스트 실행 — 실패 확인**

Run: `cd backend && pytest tests/test_partitioning.py -v`
Expected: 모두 fail (테이블이 아직 없음).

- [ ] **Step 3: upgrade() 5번 블록 — Handoff + GitPushEvent**

`migration_file` 의 `upgrade()` 함수 끝에 추가:

```python
    # ── 5) Handoff ─────────────────────────────────────────────────
    op.create_table(
        "handoffs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("branch", sa.String(), nullable=False),
        sa.Column("author_user_id", sa.UUID(), nullable=True),
        sa.Column("author_git_login", sa.String(), nullable=False),
        sa.Column("commit_sha", sa.String(), nullable=False),
        sa.Column("pushed_at", sa.DateTime(), nullable=False),
        sa.Column("raw_content", sa.Text(), nullable=True),
        sa.Column("parsed_tasks", postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column("free_notes", postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["author_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "commit_sha", name="uq_handoff_project_commit"),
        sa.CheckConstraint(
            "commit_sha ~ '^[0-9a-f]{40}$'",
            name="ck_handoff_commit_sha_format",
        ),
    )

    # ── 6) GitPushEvent ────────────────────────────────────────────
    op.create_table(
        "git_push_events",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("branch", sa.String(), nullable=False),
        sa.Column("head_commit_sha", sa.String(), nullable=False),
        sa.Column("commits", postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "commits_truncated", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("pusher", sa.String(), nullable=False),
        sa.Column("received_at", sa.DateTime(), nullable=False),
        sa.Column("processed_at", sa.DateTime(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "project_id", "head_commit_sha", name="uq_git_push_project_head"
        ),
        sa.CheckConstraint(
            "head_commit_sha ~ '^[0-9a-f]{40}$'",
            name="ck_git_push_head_commit_sha_format",
        ),
    )
```

- [ ] **Step 4: upgrade() 6번 블록 — LogIngestToken + RateLimitWindow**

이어서:

```python
    # ── 7) LogIngestToken ──────────────────────────────────────────
    op.create_table(
        "log_ingest_tokens",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("secret_hash", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column(
            "rate_limit_per_minute", sa.Integer(), nullable=False, server_default="600"
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    # ── 8) RateLimitWindow (composite PK) ─────────────────────────
    op.create_table(
        "rate_limit_windows",
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("token_id", sa.UUID(), nullable=False),
        sa.Column("window_start", sa.DateTime(), nullable=False),
        sa.Column("event_count", sa.Integer(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["token_id"], ["log_ingest_tokens.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint(
            "project_id", "token_id", "window_start", name="pk_rate_limit_window"
        ),
    )
```

- [ ] **Step 5: upgrade() 7번 블록 — ErrorGroup**

이어서:

```python
    # ── 9) ErrorGroup ──────────────────────────────────────────────
    op.create_table(
        "error_groups",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("fingerprint", sa.String(), nullable=False),
        sa.Column("exception_class", sa.String(), nullable=False),
        sa.Column("exception_message_sample", sa.Text(), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(), nullable=False),
        sa.Column("first_seen_version_sha", sa.String(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_version_sha", sa.String(), nullable=False),
        sa.Column(
            "event_count", sa.BigInteger(), nullable=False, server_default="0"
        ),
        sa.Column(
            "status",
            postgresql.ENUM(
                "open", "resolved", "ignored", "regressed",
                name="errorgroupstatus", create_type=False,
            ),
            nullable=False,
            server_default="open",
        ),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.Column("resolved_by_user_id", sa.UUID(), nullable=True),
        sa.Column("resolved_in_version_sha", sa.String(), nullable=True),
        sa.Column("last_alerted_new_at", sa.DateTime(), nullable=True),
        sa.Column("last_alerted_spike_at", sa.DateTime(), nullable=True),
        sa.Column("last_alerted_regression_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["resolved_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "project_id", "fingerprint", name="uq_error_group_project_fingerprint"
        ),
        sa.CheckConstraint(
            "first_seen_version_sha ~ '^[0-9a-f]{40}$' OR first_seen_version_sha = 'unknown'",
            name="ck_error_group_first_sha_format",
        ),
        sa.CheckConstraint(
            "last_seen_version_sha ~ '^[0-9a-f]{40}$' OR last_seen_version_sha = 'unknown'",
            name="ck_error_group_last_sha_format",
        ),
        sa.CheckConstraint(
            "resolved_in_version_sha IS NULL OR resolved_in_version_sha ~ '^[0-9a-f]{40}$'",
            name="ck_error_group_resolved_sha_format",
        ),
    )
```

- [ ] **Step 6: upgrade() 8번 블록 — pg_trgm + LogEvent partitioned table + 인덱스 5종**

이어서:

```python
    # ── 10) pg_trgm extension (WARNING+ 메시지 풀텍스트 검색용) ───
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    # ── 11) LogEvent (declarative partition by received_at) ──────
    # SQLAlchemy 가 PARTITION BY 절 자동 생성 못 하므로 raw SQL.
    op.execute("""
        CREATE TABLE log_events (
            id            UUID NOT NULL,
            project_id    UUID NOT NULL,
            level         loglevel NOT NULL,
            message       TEXT NOT NULL,
            logger_name   TEXT NOT NULL,
            version_sha   TEXT NOT NULL,
            environment   TEXT NOT NULL,
            hostname      TEXT NOT NULL,
            emitted_at    TIMESTAMP NOT NULL,
            received_at   TIMESTAMP NOT NULL,
            exception_class    TEXT,
            exception_message  TEXT,
            stack_trace        TEXT,
            stack_frames       JSON,
            fingerprint        TEXT,
            fingerprinted_at   TIMESTAMP,
            user_id_external   TEXT,
            request_id         TEXT,
            extra              JSON,
            PRIMARY KEY (id, received_at),
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
            CHECK (version_sha ~ '^[0-9a-f]{40}$' OR version_sha = 'unknown')
        ) PARTITION BY RANGE (received_at)
    """)

    # ── 12) 인덱스 5종 (parent 에 만들면 모든 파티션에 자동 적용) ──
    op.create_index(
        "idx_log_project_level_received",
        "log_events",
        ["project_id", "level", sa.text("received_at DESC")],
    )
    op.create_index(
        "idx_log_fingerprint",
        "log_events",
        ["project_id", "fingerprint"],
        postgresql_where=sa.text("fingerprint IS NOT NULL"),
    )
    op.create_index(
        "idx_log_version_sha",
        "log_events",
        ["project_id", "version_sha"],
    )
    op.create_index(
        "idx_log_unfingerprinted",
        "log_events",
        ["project_id", "id"],
        postgresql_where=sa.text(
            "level IN ('error','critical') AND fingerprinted_at IS NULL"
        ),
    )
    # 풀텍스트 — pg_trgm GIN, WARNING+ 만 인덱싱
    op.execute("""
        CREATE INDEX idx_log_message_trgm
          ON log_events USING gin (message gin_trgm_ops)
          WHERE level IN ('warning','error','critical')
    """)
```

- [ ] **Step 7: upgrade() 9번 블록 — 30일치 daily partition pre-create**

이어서:

```python
    # ── 13) 다음 30일치 daily partition pre-create ─────────────────
    # GC 자동화는 Phase 7. 본 단계는 부팅 시 1회만.
    today = datetime.utcnow().date()
    for day_offset in range(31):  # 오늘 + 30일
        d = today + timedelta(days=day_offset)
        next_d = d + timedelta(days=1)
        partition_name = f"log_events_{d.strftime('%Y%m%d')}"
        op.execute(f"""
            CREATE TABLE {partition_name} PARTITION OF log_events
              FOR VALUES FROM ('{d.isoformat()}') TO ('{next_d.isoformat()}')
        """)
```

- [ ] **Step 8: 파티셔닝 테스트 통과 확인**

Run: `cd backend && pytest tests/test_partitioning.py -v`
Expected: 3 passed.

- [ ] **Step 9: 임시 커밋**

```bash
git add backend/alembic/versions/*_phase1_logs_handoffs_git.py backend/tests/test_partitioning.py
git commit -m "feat(alembic): phase1 — 신규 테이블 6개 + pg_trgm + 파티셔닝 (WIP)"
```

---

## Task 12: alembic downgrade() + 회귀 테스트 (CRITICAL)

**Files:**
- Modify: Task 10 의 `migration_file` (downgrade 본문)
- Test: `backend/tests/test_migrations.py`
- Test: `backend/tests/test_constraints.py`

- [ ] **Step 1: 실패 테스트 — 회귀 (기존 데이터 보존)**

Create `backend/tests/test_migrations.py`:

```python
"""마이그레이션 회귀 — CRITICAL.

설계서:
- task-automation §10.3: 기존 production-like 데이터셋 위에서 alembic up,
  기존 API 응답이 byte-equal 동일.
- error-log §10.4: 기존 모델 무변경 + alembic up/down 정상.
"""
import uuid
from datetime import datetime

from alembic import command
from sqlalchemy import text


def _seed_pre_phase1(cursor):
    """Phase 1 직전 상태의 production-like 데이터 시딩."""
    workspace_id = str(uuid.uuid4())
    project_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    task_id = str(uuid.uuid4())
    cursor.execute(
        "INSERT INTO workspaces(id, name, slug, created_at) VALUES (%s, %s, %s, now())",
        (workspace_id, "ws", "ws-slug"),
    )
    cursor.execute(
        "INSERT INTO users(id, email, name, password_hash, created_at) "
        "VALUES (%s, %s, %s, %s, now())",
        (user_id, "u@x.com", "u", "h"),
    )
    cursor.execute(
        "INSERT INTO projects(id, workspace_id, name, created_at) VALUES (%s, %s, %s, now())",
        (project_id, workspace_id, "p"),
    )
    cursor.execute(
        "INSERT INTO tasks(id, project_id, title, status, created_at) "
        "VALUES (%s, %s, %s, %s, now())",
        (task_id, project_id, "기존 태스크", "todo"),
    )
    return {"task_id": task_id, "project_id": project_id, "user_id": user_id}


def test_existing_data_preserved_after_phase1(alembic_config, postgresql_db):
    """Phase 1 직전 head 까지 올린 후 데이터 시딩 → Phase 1 적용 → 무손실."""
    command.upgrade(alembic_config, "be8724268ae4")
    cur = postgresql_db.cursor()
    seeded = _seed_pre_phase1(cur)
    postgresql_db.commit()

    command.upgrade(alembic_config, "head")

    cur.execute("SELECT title, status FROM tasks WHERE id = %s", (seeded["task_id"],))
    row = cur.fetchone()
    assert row is not None
    assert row[0] == "기존 태스크"
    assert row[1] == "todo"

    cur.execute(
        "SELECT source, external_id, last_commit_sha, archived_at "
        "FROM tasks WHERE id = %s",
        (seeded["task_id"],),
    )
    row = cur.fetchone()
    assert row[0] == "manual", "기존 task 의 source 가 manual default 가 아님"
    assert row[1] is None
    assert row[2] is None
    assert row[3] is None

    cur.execute(
        "SELECT git_default_branch, plan_path, handoff_dir "
        "FROM projects WHERE id = %s",
        (seeded["project_id"],),
    )
    row = cur.fetchone()
    assert row == ("main", "PLAN.md", "handoffs/")
    cur.close()


def test_alembic_downgrade_then_upgrade_roundtrip(alembic_config):
    """upgrade head → downgrade -1 → upgrade head 가 깨끗이 돈다."""
    command.upgrade(alembic_config, "head")
    command.downgrade(alembic_config, "-1")
    command.upgrade(alembic_config, "head")


def test_downgrade_drops_phase1_objects(alembic_config, postgresql_db):
    """downgrade -1 후 phase1 객체가 모두 사라진다."""
    command.upgrade(alembic_config, "head")
    command.downgrade(alembic_config, "-1")

    cur = postgresql_db.cursor()
    cur.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema='public'"
    )
    tables = {row[0] for row in cur.fetchall()}
    cur.close()
    for t in [
        "handoffs", "git_push_events", "log_ingest_tokens",
        "rate_limit_windows", "error_groups", "log_events",
    ]:
        assert t not in tables, f"downgrade 후 {t} 가 남아있음"


def test_task_event_action_existing_values_preserved_after_phase1(
    alembic_config, postgresql_db
):
    """ALTER TYPE ADD VALUE 로 기존 enum 값이 사라지면 안 됨."""
    command.upgrade(alembic_config, "head")
    cur = postgresql_db.cursor()
    cur.execute(
        "SELECT enumlabel FROM pg_enum "
        "JOIN pg_type ON pg_enum.enumtypid = pg_type.oid "
        "WHERE pg_type.typname = 'taskeventaction' "
        "ORDER BY enumlabel"
    )
    labels = {row[0] for row in cur.fetchall()}
    cur.close()
    assert {"created", "updated", "status_changed", "assigned", "commented", "deleted"} <= labels
    assert {"synced_from_plan", "checked_by_commit", "unchecked_by_commit", "archived_from_plan"} <= labels
```

- [ ] **Step 2: 실패 테스트 — CHECK / UNIQUE 제약**

Create `backend/tests/test_constraints.py`:

```python
"""CHECK / UNIQUE 제약 동작 — 설계서 Decision Log Rev3 의 sha 형식 강제."""
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError


@pytest.mark.asyncio
async def test_handoff_short_sha_rejected(async_session):
    """handoffs.commit_sha 가 40자 hex 가 아니면 reject."""
    workspace_id = uuid.uuid4()
    project_id = uuid.uuid4()
    user_id = uuid.uuid4()
    await async_session.execute(text(
        "INSERT INTO workspaces(id, name, slug, created_at) VALUES (:id, 'w', 's', now())"
    ), {"id": workspace_id})
    await async_session.execute(text(
        "INSERT INTO users(id, email, name, password_hash, created_at) "
        "VALUES (:id, 'u@x.com', 'u', 'h', now())"
    ), {"id": user_id})
    await async_session.execute(text(
        "INSERT INTO projects(id, workspace_id, name, created_at) "
        "VALUES (:id, :ws, 'p', now())"
    ), {"id": project_id, "ws": workspace_id})
    await async_session.commit()

    with pytest.raises(IntegrityError):
        await async_session.execute(text(
            "INSERT INTO handoffs(id, project_id, branch, author_git_login, "
            "commit_sha, pushed_at, created_at) "
            "VALUES (:id, :pid, 'main', 'a', 'abc', now(), now())"
        ), {"id": uuid.uuid4(), "pid": project_id})
        await async_session.commit()


@pytest.mark.asyncio
async def test_handoff_full_sha_accepted(async_session):
    """40자 hex 는 통과."""
    workspace_id = uuid.uuid4()
    project_id = uuid.uuid4()
    await async_session.execute(text(
        "INSERT INTO workspaces(id, name, slug, created_at) VALUES (:id, 'w', 's2', now())"
    ), {"id": workspace_id})
    await async_session.execute(text(
        "INSERT INTO projects(id, workspace_id, name, created_at) "
        "VALUES (:id, :ws, 'p', now())"
    ), {"id": project_id, "ws": workspace_id})
    await async_session.commit()

    full = "a" * 40
    await async_session.execute(text(
        "INSERT INTO handoffs(id, project_id, branch, author_git_login, "
        "commit_sha, pushed_at, created_at) "
        "VALUES (:id, :pid, 'main', 'a', :sha, now(), now())"
    ), {"id": uuid.uuid4(), "pid": project_id, "sha": full})
    await async_session.commit()


@pytest.mark.asyncio
async def test_log_event_unknown_version_sha_accepted(async_session):
    """LogEvent.version_sha = 'unknown' 은 허용 (운영 가시화 정책)."""
    workspace_id = uuid.uuid4()
    project_id = uuid.uuid4()
    await async_session.execute(text(
        "INSERT INTO workspaces(id, name, slug, created_at) VALUES (:id, 'w', 's3', now())"
    ), {"id": workspace_id})
    await async_session.execute(text(
        "INSERT INTO projects(id, workspace_id, name, created_at) "
        "VALUES (:id, :ws, 'p', now())"
    ), {"id": project_id, "ws": workspace_id})
    await async_session.commit()

    await async_session.execute(text(
        "INSERT INTO log_events(id, project_id, level, message, logger_name, "
        "version_sha, environment, hostname, emitted_at, received_at) "
        "VALUES (:id, :pid, 'info', 'm', 'l', 'unknown', 'dev', 'h', now(), now())"
    ), {"id": uuid.uuid4(), "pid": project_id})
    await async_session.commit()


@pytest.mark.asyncio
async def test_log_event_short_version_sha_rejected(async_session):
    """7자 short SHA 는 reject (Rev3 Decision)."""
    workspace_id = uuid.uuid4()
    project_id = uuid.uuid4()
    await async_session.execute(text(
        "INSERT INTO workspaces(id, name, slug, created_at) VALUES (:id, 'w', 's4', now())"
    ), {"id": workspace_id})
    await async_session.execute(text(
        "INSERT INTO projects(id, workspace_id, name, created_at) "
        "VALUES (:id, :ws, 'p', now())"
    ), {"id": project_id, "ws": workspace_id})
    await async_session.commit()

    with pytest.raises(IntegrityError):
        await async_session.execute(text(
            "INSERT INTO log_events(id, project_id, level, message, logger_name, "
            "version_sha, environment, hostname, emitted_at, received_at) "
            "VALUES (:id, :pid, 'info', 'm', 'l', 'abc1234', 'dev', 'h', now(), now())"
        ), {"id": uuid.uuid4(), "pid": project_id})
        await async_session.commit()


@pytest.mark.asyncio
async def test_task_external_id_unique_per_project(async_session):
    """external_id 같은 값 같은 project → reject. NULL 은 다중 허용."""
    workspace_id = uuid.uuid4()
    project_id = uuid.uuid4()
    await async_session.execute(text(
        "INSERT INTO workspaces(id, name, slug, created_at) VALUES (:id, 'w', 's5', now())"
    ), {"id": workspace_id})
    await async_session.execute(text(
        "INSERT INTO projects(id, workspace_id, name, created_at) "
        "VALUES (:id, :ws, 'p', now())"
    ), {"id": project_id, "ws": workspace_id})

    # NULL 두 개 — 통과
    await async_session.execute(text(
        "INSERT INTO tasks(id, project_id, title, status, source, created_at) "
        "VALUES (:id, :pid, 't1', 'todo', 'manual', now())"
    ), {"id": uuid.uuid4(), "pid": project_id})
    await async_session.execute(text(
        "INSERT INTO tasks(id, project_id, title, status, source, created_at) "
        "VALUES (:id, :pid, 't2', 'todo', 'manual', now())"
    ), {"id": uuid.uuid4(), "pid": project_id})
    await async_session.commit()

    # 같은 external_id "task-001" 두 개 → reject
    await async_session.execute(text(
        "INSERT INTO tasks(id, project_id, title, status, source, external_id, created_at) "
        "VALUES (:id, :pid, 't3', 'todo', 'manual', 'task-001', now())"
    ), {"id": uuid.uuid4(), "pid": project_id})
    await async_session.commit()

    with pytest.raises(IntegrityError):
        await async_session.execute(text(
            "INSERT INTO tasks(id, project_id, title, status, source, external_id, created_at) "
            "VALUES (:id, :pid, 't4', 'todo', 'manual', 'task-001', now())"
        ), {"id": uuid.uuid4(), "pid": project_id})
        await async_session.commit()


@pytest.mark.asyncio
async def test_handoff_unique_project_commit(async_session):
    """webhook 재전송 멱등성: 같은 (project_id, commit_sha) → reject."""
    workspace_id = uuid.uuid4()
    project_id = uuid.uuid4()
    await async_session.execute(text(
        "INSERT INTO workspaces(id, name, slug, created_at) VALUES (:id, 'w', 's6', now())"
    ), {"id": workspace_id})
    await async_session.execute(text(
        "INSERT INTO projects(id, workspace_id, name, created_at) "
        "VALUES (:id, :ws, 'p', now())"
    ), {"id": project_id, "ws": workspace_id})
    await async_session.commit()

    sha = "b" * 40
    await async_session.execute(text(
        "INSERT INTO handoffs(id, project_id, branch, author_git_login, "
        "commit_sha, pushed_at, created_at) "
        "VALUES (:id, :pid, 'main', 'a', :sha, now(), now())"
    ), {"id": uuid.uuid4(), "pid": project_id, "sha": sha})
    await async_session.commit()

    with pytest.raises(IntegrityError):
        await async_session.execute(text(
            "INSERT INTO handoffs(id, project_id, branch, author_git_login, "
            "commit_sha, pushed_at, created_at) "
            "VALUES (:id, :pid, 'feat', 'b', :sha, now(), now())"
        ), {"id": uuid.uuid4(), "pid": project_id, "sha": sha})
        await async_session.commit()
```

- [ ] **Step 3: downgrade() 함수 작성**

`migration_file` 의 임시 `downgrade()` 를 다음으로 교체:

```python
def downgrade() -> None:
    # 13) daily partition + log_events
    today = datetime.utcnow().date()
    for day_offset in range(31):
        d = today + timedelta(days=day_offset)
        partition_name = f"log_events_{d.strftime('%Y%m%d')}"
        op.execute(f"DROP TABLE IF EXISTS {partition_name}")
    op.execute("DROP INDEX IF EXISTS idx_log_message_trgm")
    op.drop_index("idx_log_unfingerprinted", table_name="log_events")
    op.drop_index("idx_log_version_sha", table_name="log_events")
    op.drop_index("idx_log_fingerprint", table_name="log_events")
    op.drop_index("idx_log_project_level_received", table_name="log_events")
    op.execute("DROP TABLE IF EXISTS log_events")
    # pg_trgm 은 다른 곳에서 쓸 수도 있으므로 굳이 DROP 하지 않음.

    # 9) ErrorGroup
    op.drop_table("error_groups")

    # 8) RateLimitWindow
    op.drop_table("rate_limit_windows")

    # 7) LogIngestToken
    op.drop_table("log_ingest_tokens")

    # 6) GitPushEvent
    op.drop_table("git_push_events")

    # 5) Handoff
    op.drop_table("handoffs")

    # 4) Task — CHECK + 부분 인덱스 + 4 컬럼
    op.drop_constraint("ck_task_last_commit_sha_format", "tasks", type_="check")
    op.drop_index("idx_task_project_external_id", table_name="tasks")
    op.drop_column("tasks", "archived_at")
    op.drop_column("tasks", "last_commit_sha")
    op.drop_column("tasks", "external_id")
    op.drop_column("tasks", "source")

    # 3) Project — 6 컬럼
    op.drop_column("projects", "webhook_secret_encrypted")
    op.drop_column("projects", "last_synced_commit_sha")
    op.drop_column("projects", "handoff_dir")
    op.drop_column("projects", "plan_path")
    op.drop_column("projects", "git_default_branch")
    op.drop_column("projects", "git_repo_url")

    # 2) 신규 enum 타입
    op.execute("DROP TYPE IF EXISTS errorgroupstatus")
    op.execute("DROP TYPE IF EXISTS loglevel")
    op.execute("DROP TYPE IF EXISTS tasksource")

    # 1) TaskEventAction 4값 — PostgreSQL ALTER TYPE DROP VALUE 미지원.
    # 4 enum 값이 downgrade 후에도 남는다. 새 row 가 enum 값을 사용하지
    # 않았다면 무해. 사용했다면 downgrade 자체가 부적절한 상황.
    # 운영 노트: Phase 1 downgrade 가 필요하면 사전에 row 정리 필요.
```

- [ ] **Step 4: 회귀 + 제약 테스트 실행**

Run: `cd backend && pytest tests/test_migrations.py tests/test_constraints.py -v`
Expected: 모두 passed.

테스트 실패 시:
- IntegrityError 가 안 나면 CHECK 정규식이나 UNIQUE 절 누락 — `migration_file` 검토.
- downgrade roundtrip 실패 시 `downgrade()` 의 DROP 순서 (FK 의존 역순) 검토.
- 기존 데이터 보존 실패 시 `server_default` 누락 검토.

- [ ] **Step 5: 전체 테스트 실행 (회귀 가드)**

Run: `cd backend && pytest -v`
Expected: 모든 task 의 테스트가 한꺼번에 passed.

- [ ] **Step 6: 임시 커밋들 squash (선택)**

Task 10/11/12 의 WIP 커밋 3개를 단일 commit 으로 squash 하면 이력이 깔끔. 솔로 운영이라 선택 사항이지만 권장:

```bash
git log --oneline | head -10  # WIP 커밋 3개 확인
git rebase -i HEAD~3
# pick → squash 두 개, 메시지 정리
```

최종 commit 메시지 권장:

```
feat(phase1): models + alembic — handoffs, git push events, log ingest

- Project: git 6 필드 (repo_url, default_branch, plan_path, handoff_dir,
  last_synced_commit_sha, webhook_secret_encrypted)
- Task: 4 필드 (source, external_id, last_commit_sha, archived_at)
  + UNIQUE(project_id, external_id) 부분 인덱스
  + CHECK last_commit_sha 40자 hex 또는 NULL
- TaskEventAction: 4값 추가 (synced_from_plan, checked_by_commit,
  unchecked_by_commit, archived_from_plan)
- 신규 모델: Handoff, GitPushEvent, LogIngestToken, RateLimitWindow,
  ErrorGroup, LogEvent
- pg_trgm 활성화 + log_events 일별 파티션 + 30일 pre-create
- 인덱스 5종 (level/received_at, fingerprint 부분, version_sha,
  unfingerprinted 부분, message gin_trgm_ops 부분)
- 회귀 테스트: 기존 데이터 보존, alembic up/down, CHECK/UNIQUE 동작

설계서:
  docs/superpowers/specs/2026-04-26-ai-task-automation-design.md §4
  docs/superpowers/specs/2026-04-26-error-log-design.md §4
```

---

## Task 13: handoffs/main.md 갱신 + 머지 준비

**Files:**
- Modify: `handoffs/main.md`

- [ ] **Step 1: 오늘 날짜 섹션 추가**

Edit `handoffs/main.md` — 파일 상단(`# Handoff: main — @ardensdevspace` 다음)에 추가:

```markdown
## 2026-04-28

- [x] **Phase 1 완료** — pslog 본체 alembic 마이그레이션 + pytest 인프라
  - [x] 테스트 인프라: pytest + pytest-asyncio + pytest-postgresql, async DB fixture
  - [x] enum 확장: TaskSource, LogLevel, ErrorGroupStatus, TaskEventAction +4값
  - [x] Project +6 필드 (git_repo_url, git_default_branch, plan_path, handoff_dir, last_synced_commit_sha, webhook_secret_encrypted)
  - [x] Task +4 필드 (source, external_id, last_commit_sha, archived_at) + UNIQUE 부분 인덱스 + CHECK 40자 hex
  - [x] 신규 모델 6개: Handoff, GitPushEvent, LogIngestToken, RateLimitWindow, ErrorGroup, LogEvent
  - [x] pg_trgm + log_events 일별 파티션 + 다음 30일 pre-create
  - [x] 인덱스 5종 (Postgres partial 인덱스 포함)
  - [x] 회귀 테스트: 기존 데이터 보존, up/down roundtrip, CHECK/UNIQUE 동작
  - [x] 단일 alembic revision (단일 PR 머지)

### 마지막 커밋

- pslog: `<phase1 commit hash>` (브랜치 `<branch>`)

### 다음 (Phase 2 — Webhook 수신만)

- [ ] `/api/v1/webhooks/github` endpoint
- [ ] 서명 검증 (프로젝트별 secret, Fernet 복호화)
- [ ] GitPushEvent INSERT 만 (처리 로직 X)
- [ ] push_event_reaper 부팅 hook
- [ ] commits_truncated 플래그 처리

### 블로커

없음

### 메모

- Phase 1 의 단일 alembic revision 은 모든 신규 모델 + 기존 모델 확장 포함.
  downgrade 시 PostgreSQL ALTER TYPE DROP VALUE 미지원으로 TaskEventAction 4값은 잔존 (운영 노트).
- pg_partman 미도입 — 30일 pre-create 만. Phase 7 진입 시 자동 GC 도입.
- 다음 phase 의 Webhook secret 검증을 위해 Fernet 마스터 키 (`pslog_FERNET_KEY`) 환경변수 셋업 필요.
- task-automation 설계서의 Phase 4 안정화 후 error-log 의 Phase 2(ingest endpoint) 진입 가능.

---
```

- [ ] **Step 2: 커밋**

```bash
git add handoffs/main.md
git commit -m "docs(handoff): Phase 1 완료 기록 + Phase 2 다음 할 일"
```

- [ ] **Step 3: PR 준비 (사용자 결정)**

이 시점에서 사용자가 결정:
- 단일 PR 로 main 에 머지 vs feature 브랜치 → review → 머지
- handoff 내용에 PR URL 추가 후 다시 commit

`gh pr create` 는 사용자가 명시 요청 시에만 실행.

---

## 후속 (Phase 2 — 본 plan 범위 밖)

본 plan 은 Phase 1 까지만 다룬다. Phase 2 는 별도 plan 작성:
- `/api/v1/webhooks/github` endpoint + 서명 검증
- `github_webhook_service` (raw INSERT 만)
- `push_event_reaper` 부팅 hook
- log-ingest endpoint 는 task-automation Phase 4 안정화 후 별도 plan

선행 의존:
- task-automation 설계서 §11.1 — app-chak `CLAUDE.md` 의 pslog 연동 규칙 (이미 Phase 0 PR #1 로 머지됨)
- error-log 설계서 §14 — handler 배포 = app-chak 레포 직접 복사 (이미 Phase 0 PR #1)
