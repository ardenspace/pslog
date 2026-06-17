# Phase 6 — Discord 알림 통합 본편 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** spec §11 의 Phase 6 본편 구현 — push 1건 = 알림 1건 요약 (체크/롤백/archived/handoff 누락) + Discord webhook 3회 연속 실패 시 자동 disable + GitSettings 모달에서 재활성화 + B2 sync-failure alert 도 같은 dispatcher 통과.

**Architecture:** `notification_dispatcher` 신규 서비스가 모든 Discord 알림의 1점 진입 (URL/disable 체크 + counter 갱신). `Project` 에 2 컬럼 (`discord_consecutive_failures` / `discord_disabled_at`) 추가, alembic 1건. `_apply_plan` 가 `PlanChanges` dataclass 를 return 해 `process_event` 가 모아서 dispatcher 호출 (mutable global state 안 씀, 함수 순수성 유지). B1 의 rollback 후 ORM expire 함정 학습 — except path 에서 dispatcher 호출 직전 `db.refresh(project)` 1줄. URL 자체 편집은 기존 DashboardPage + PATCH `/projects` 그대로 (UI 변경 없음); GitSettings 모달엔 상태 표시 + 재활성화 버튼만 추가. `project_service.update_project` 가 URL 변경 시 자동 counter/disabled_at reset.

**Tech Stack:** FastAPI 0.115, SQLAlchemy 2.0 async, Pydantic v2, Alembic, React 19 + TypeScript 5, TanStack Query, Tailwind, bun. backend tests: pytest + testcontainers PostgreSQL.

**선행 조건:**
- pslog `main` = `29c7db7` (B2 PR #14 머지 직후), alembic head = `a1b2c3d4e5f6`
- backend tests baseline = **184 passing**
- frontend `bun run build` clean, `bun run lint` 8 pre-existing only
- Python 3.12 venv (`backend/venv` symlink), `.env` 의 `pslog_FERNET_KEY` 존재
- spec: `docs/superpowers/specs/2026-05-01-phase-6-discord-notifications-design.md`

**중요한 계약:**

- **`Project.discord_consecutive_failures: int = 0` (NOT NULL)** + **`discord_disabled_at: datetime | None`**: 새 alembic revision 에서 추가, 기존 row 자동 default 활성 상태.
- **`notification_dispatcher.dispatch_discord_alert(db, project, content)`**: 1점 진입. `discord_webhook_url IS NULL` → no-op. `discord_disabled_at IS NOT NULL` → no-op. send 성공 → counter > 0 이면 reset. send 실패 → counter +1, 3 도달 시 `disabled_at = now()`. 자체 commit (counter 갱신 영속). 알림 실패 silent (caller 에 영향 없음).
- **Push 알림 (success path)**: 한 push 가 일으킨 모든 변경을 한 메시지로. 4 카테고리 (체크 / 롤백 / archived / handoff 누락). 변경 없는 카테고리 줄 자체 생략. 모든 카테고리 비고 + handoff 정상 → `_format_push_summary` None return → dispatcher 호출 안 함.
- **Handoff 누락 정의**: `plan_changed AND NOT handoff_present` (같은 push 안에서). spec §11.1 의 "**git push 직전 반드시** handoff commit" 정책 직접 매칭. 시간 grace 또는 background scheduler 없음.
- **B2 sync-failure 리팩토링**: `process_event` except 분기 끝의 직접 `discord_service.send_webhook` 호출 → `notification_dispatcher.dispatch_discord_alert` 경유. cooldown / disable 정책 자동 적용. **`db.refresh(project)` 추가** — rollback 후 ORM expire 회피.
- **`POST /git-settings/discord-reset`** (OWNER 전용): counter 0 + disabled_at NULL reset.
- **URL 자동 reset**: `project_service.update_project` 가 `discord_webhook_url` 변경 감지 시 같은 트랜잭션에서 counter 0 + disabled_at NULL.
- **Frontend GitSettings 모달**: URL 입력 추가 X (DashboardPage 그대로), **상태 표시 + 재활성화 버튼만** 추가.
- **에러 정책**:
  - dispatcher 의 send 실패 → silent (logger.exception)
  - POST `/discord-reset` — 비-OWNER 403, 비-멤버 404
  - 알림 종류별 on/off 미도입 (YAGNI)

---

## File Structure

**신규 파일 (backend 소스):**
- `backend/alembic/versions/<auto>_phase6_discord_counter.py` — Project 2 컬럼 추가
- `backend/app/services/notification_dispatcher.py` — 알림 dispatcher

**신규 파일 (backend 테스트):**
- `backend/tests/test_notification_dispatcher.py` — dispatcher 단위 4건
- `backend/tests/test_phase6_migration.py` — 마이그레이션 회귀 2건

**수정 파일 (backend):**
- `backend/app/models/project.py` — 2 컬럼 + `__init__` default
- `backend/app/services/sync_service.py` — `PlanChanges` dataclass + `_apply_plan` return + `_apply_handoff` return bool + `process_event` 통합 + B2 sync-failure 리팩토링 + `_format_push_summary` 신규
- `backend/app/services/project_service.py` — `update_project` 가 URL 변경 시 reset
- `backend/app/schemas/git_settings.py` — `GitSettingsResponse` 에 3 필드
- `backend/app/api/v1/endpoints/git_settings.py` — `GET /git-settings` 의 response 에 3 필드 채움 + `POST /discord-reset` 핸들러
- `backend/tests/test_sync_service.py` — push summary 3건 + B2 sync-failure 3건의 mock target 갱신
- `backend/tests/test_git_settings_endpoint.py` — discord-reset 3건 + GET /git-settings 응답 검증 1건
- `backend/tests/test_project_service.py` (또는 새 파일) — URL 변경 시 reset 검증 1건

**수정 파일 (frontend):**
- `frontend/src/types/git.ts` — `GitSettings` 인터페이스에 3 필드
- `frontend/src/services/api.ts` — `git.resetDiscord` method
- `frontend/src/hooks/useGithubSettings.ts` — `useResetDiscord` 훅
- `frontend/src/components/sidebar/ProjectGitSettingsModal.tsx` — Discord 섹션 (상태 + 재활성화 버튼)

**미변경:**
- `frontend/src/pages/DashboardPage.tsx` (URL 입력 그대로)
- `frontend/src/hooks/useProjects.ts` (URL 변경 mutation 그대로 — backend 가 자동 reset)
- `backend/app/services/discord_service.py` (primitive `send_webhook` 그대로 재사용)
- frontend 단위 테스트 인프라 (Vitest 미도입 그대로)

---

### Task 1: Backend — Project 2 컬럼 + alembic 마이그레이션

**Files:**
- Modify: `backend/app/models/project.py` (2 컬럼 + `__init__` default)
- Create: `backend/alembic/versions/<auto>_phase6_discord_counter.py`
- Create: `backend/tests/test_phase6_migration.py`

- [ ] **Step 1: Baseline 확인**

```bash
cd /Users/arden/Documents/ardensdevspace/pslog/.worktrees/phase-6-discord-notifications/backend
source venv/bin/activate
pytest -q 2>&1 | tail -3
```

Expected: `184 passed`. 다르면 STOP.

- [ ] **Step 2: 모델 변경**

`backend/app/models/project.py` 의 `Project` 클래스에 2 컬럼 추가 (기존 `github_pat_encrypted` 다음 줄 권장):

```python
    # Phase 6 — Discord 알림 cooldown / auto-disable
    discord_consecutive_failures: Mapped[int] = mapped_column(default=0, nullable=False)
    discord_disabled_at: Mapped[datetime | None] = mapped_column(default=None)
```

`__init__` 의 `setdefault` 블록에 추가:

```python
        kwargs.setdefault("discord_consecutive_failures", 0)
```

(`discord_disabled_at` 은 NULL default 라 setdefault 불필요.)

- [ ] **Step 3: alembic autogenerate**

```bash
cd backend && source venv/bin/activate
alembic revision --autogenerate -m "phase6_discord_counter"
```

생성된 파일을 열어서 (`backend/alembic/versions/<random_hex>_phase6_discord_counter.py`) 확인:
- `op.add_column('projects', sa.Column('discord_consecutive_failures', sa.Integer(), nullable=False, server_default='0'))`
- `op.add_column('projects', sa.Column('discord_disabled_at', sa.DateTime(), nullable=True))`
- downgrade: `op.drop_column('projects', 'discord_disabled_at')` + `op.drop_column('projects', 'discord_consecutive_failures')`

`server_default='0'` 가 자동으로 들어가는지 확인 — autogenerate 가 `nullable=False` + Python `default=0` 만 보면 server_default 안 넣을 수 있음. 그러면 기존 row 가 NOT NULL 위반. 수동으로 `server_default='0'` 추가:

```python
op.add_column('projects', sa.Column(
    'discord_consecutive_failures', sa.Integer(),
    nullable=False, server_default='0',
))
```

(autogenerate 가 알아서 넣었으면 그대로 두기.)

- [ ] **Step 4: 마이그레이션 회귀 테스트 작성**

`backend/tests/test_phase6_migration.py` 신규:

```python
"""Phase 6 마이그레이션 회귀 — discord_consecutive_failures / discord_disabled_at 컬럼 추가.

설계서: 2026-05-01-phase-6-discord-notifications-design.md §3.3
"""

import uuid
import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.project import Project
from app.models.workspace import Workspace


async def test_phase6_columns_exist(async_session: AsyncSession):
    """alembic upgrade 후 Project 에 두 컬럼 존재 + 기본값 적용."""
    ws = Workspace(name="ws", slug=f"ws-{uuid.uuid4().hex[:8]}")
    async_session.add(ws)
    await async_session.flush()
    proj = Project(workspace_id=ws.id, name="p")
    async_session.add(proj)
    await async_session.commit()
    await async_session.refresh(proj)

    assert proj.discord_consecutive_failures == 0
    assert proj.discord_disabled_at is None


async def test_phase6_columns_set_and_persist(async_session: AsyncSession):
    """수동으로 set 한 값이 round-trip 으로 보존."""
    from datetime import datetime
    ws = Workspace(name="ws", slug=f"ws-{uuid.uuid4().hex[:8]}")
    async_session.add(ws)
    await async_session.flush()
    proj = Project(workspace_id=ws.id, name="p")
    async_session.add(proj)
    await async_session.commit()
    await async_session.refresh(proj)

    now = datetime.utcnow()
    proj.discord_consecutive_failures = 3
    proj.discord_disabled_at = now
    await async_session.commit()
    await async_session.refresh(proj)

    assert proj.discord_consecutive_failures == 3
    assert proj.discord_disabled_at is not None
    # microseconds 손실 가능성 회피 — 같은 second 인지만 확인
    assert proj.discord_disabled_at.replace(microsecond=0) == now.replace(microsecond=0)
```

- [ ] **Step 5: 테스트 실행 + 회귀**

```bash
pytest tests/test_phase6_migration.py -v 2>&1 | tail -10
pytest -q 2>&1 | tail -3
```

Expected: 신규 2건 PASS, 전체 `186 passed` (184 + 2).

- [ ] **Step 6: Commit**

```bash
cd /Users/arden/Documents/ardensdevspace/pslog/.worktrees/phase-6-discord-notifications
git add backend/app/models/project.py backend/alembic/versions/*phase6_discord_counter.py backend/tests/test_phase6_migration.py
git commit -m "$(cat <<'EOF'
feat(phase6): Project 모델에 discord_consecutive_failures / discord_disabled_at

- 2 컬럼 추가 (alembic autogenerate, server_default='0' 확인)
- 기존 row 자동 활성 상태 (counter 0 / disabled_at NULL)
- 회귀 테스트 2건: 컬럼 default 값 / 수동 set round-trip

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Backend — `notification_dispatcher` 신규 서비스

**Files:**
- Create: `backend/app/services/notification_dispatcher.py`
- Create: `backend/tests/test_notification_dispatcher.py`

- [ ] **Step 1: Failing tests 작성**

`backend/tests/test_notification_dispatcher.py` 신규:

```python
"""notification_dispatcher 단위 테스트.

설계서: 2026-05-01-phase-6-discord-notifications-design.md §3.1
"""

import uuid
from datetime import datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.project import Project
from app.models.workspace import Workspace
from app.services import notification_dispatcher


async def _seed_project(
    db: AsyncSession,
    *,
    discord_webhook_url: str | None = None,
    discord_disabled_at: datetime | None = None,
    discord_consecutive_failures: int = 0,
) -> Project:
    ws = Workspace(name="ws", slug=f"ws-{uuid.uuid4().hex[:8]}")
    db.add(ws)
    await db.flush()
    proj = Project(
        workspace_id=ws.id, name="p",
        discord_webhook_url=discord_webhook_url,
        discord_disabled_at=discord_disabled_at,
        discord_consecutive_failures=discord_consecutive_failures,
    )
    db.add(proj)
    await db.commit()
    await db.refresh(proj)
    return proj


async def test_dispatch_skipped_when_url_missing(
    async_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
):
    """discord_webhook_url IS NULL → send_webhook 호출 안 함."""
    proj = await _seed_project(async_session, discord_webhook_url=None)

    sent: list = []
    async def fake_send(content, url):
        sent.append((content, url))

    import app.services.discord_service as discord_mod
    monkeypatch.setattr(discord_mod, "send_webhook", fake_send)

    await notification_dispatcher.dispatch_discord_alert(async_session, proj, "hello")

    assert sent == []
    await async_session.refresh(proj)
    assert proj.discord_consecutive_failures == 0
    assert proj.discord_disabled_at is None


async def test_dispatch_skipped_when_disabled(
    async_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
):
    """discord_disabled_at IS NOT NULL → send_webhook 호출 안 함, counter 변동 없음."""
    proj = await _seed_project(
        async_session,
        discord_webhook_url="https://discord.com/api/webhooks/1/abc",
        discord_disabled_at=datetime.utcnow(),
        discord_consecutive_failures=3,
    )

    sent: list = []
    async def fake_send(content, url):
        sent.append((content, url))

    import app.services.discord_service as discord_mod
    monkeypatch.setattr(discord_mod, "send_webhook", fake_send)

    await notification_dispatcher.dispatch_discord_alert(async_session, proj, "hello")

    assert sent == []
    await async_session.refresh(proj)
    # 변동 없음
    assert proj.discord_consecutive_failures == 3
    assert proj.discord_disabled_at is not None


async def test_dispatch_resets_counter_on_success(
    async_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
):
    """send 성공 시 counter > 0 이면 0 reset."""
    proj = await _seed_project(
        async_session,
        discord_webhook_url="https://discord.com/api/webhooks/1/abc",
        discord_consecutive_failures=2,
    )

    sent: list = []
    async def fake_send(content, url):
        sent.append((content, url))

    import app.services.discord_service as discord_mod
    monkeypatch.setattr(discord_mod, "send_webhook", fake_send)

    await notification_dispatcher.dispatch_discord_alert(async_session, proj, "hello")

    assert len(sent) == 1
    await async_session.refresh(proj)
    assert proj.discord_consecutive_failures == 0
    assert proj.discord_disabled_at is None


async def test_dispatch_increments_and_disables_after_threshold(
    async_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
):
    """send 실패 3회 누적 시 disabled_at 자동 설정."""
    proj = await _seed_project(
        async_session,
        discord_webhook_url="https://discord.com/api/webhooks/1/abc",
        discord_consecutive_failures=2,
    )

    async def boom_send(content, url):
        raise RuntimeError("discord 503")

    import app.services.discord_service as discord_mod
    monkeypatch.setattr(discord_mod, "send_webhook", boom_send)

    await notification_dispatcher.dispatch_discord_alert(async_session, proj, "hello")

    await async_session.refresh(proj)
    assert proj.discord_consecutive_failures == 3
    assert proj.discord_disabled_at is not None
```

- [ ] **Step 2: Verify failure**

```bash
pytest tests/test_notification_dispatcher.py -v 2>&1 | tail -10
```

Expected: 4 FAIL with `ImportError: cannot import name 'notification_dispatcher'` 또는 유사. fix 없이 fail 확인.

- [ ] **Step 3: Implement — `notification_dispatcher.py`**

`backend/app/services/notification_dispatcher.py` 신규:

```python
"""Discord 알림 dispatcher — disable 정책 통합 진입점.

설계서: 2026-05-01-phase-6-discord-notifications-design.md §3.1
"""
import logging
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.project import Project
from app.services import discord_service

logger = logging.getLogger(__name__)

DISABLE_THRESHOLD = 3


async def dispatch_discord_alert(
    db: AsyncSession,
    project: Project,
    content: str,
) -> None:
    """Discord 알림 1점 진입. URL NULL / disabled 체크 + 실패 시 counter 갱신.

    실패가 메인 처리에 영향 안 가도록 catch — caller 는 await 만 하면 됨.
    counter 갱신은 같은 session 의 commit 으로 영속.
    """
    if project.discord_webhook_url is None:
        return
    if project.discord_disabled_at is not None:
        logger.info(
            "Discord disabled for project %s since %s — skip",
            project.id, project.discord_disabled_at,
        )
        return

    try:
        await discord_service.send_webhook(content, project.discord_webhook_url)
        # 성공 — counter > 0 이면 reset
        if project.discord_consecutive_failures > 0:
            project.discord_consecutive_failures = 0
            try:
                await db.commit()
            except Exception:
                logger.exception(
                    "Failed to reset Discord failure counter for project %s",
                    project.id,
                )
    except Exception:
        logger.exception("Discord alert failed for project %s", project.id)
        project.discord_consecutive_failures += 1
        if project.discord_consecutive_failures >= DISABLE_THRESHOLD:
            project.discord_disabled_at = datetime.utcnow()
            logger.warning(
                "Discord auto-disabled for project %s after %d consecutive failures",
                project.id, project.discord_consecutive_failures,
            )
        try:
            await db.commit()
        except Exception:
            logger.exception(
                "Failed to record Discord failure counter for project %s",
                project.id,
            )
```

- [ ] **Step 4: Verify pass + 회귀**

```bash
pytest tests/test_notification_dispatcher.py -v 2>&1 | tail -10
pytest -q 2>&1 | tail -3
```

Expected: 4 신규 PASS, 전체 `190 passed` (186 + 4).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/notification_dispatcher.py backend/tests/test_notification_dispatcher.py
git commit -m "$(cat <<'EOF'
feat(phase6): notification_dispatcher — Discord 알림 1점 진입 + auto-disable

- URL NULL / disabled_at set → no-op
- send 성공 시 counter > 0 이면 reset
- send 실패 시 counter +1, 3 도달 시 disabled_at = now
- 알림 실패 silent (caller 영향 없음)
- 회귀 4건: skipped (URL NULL / disabled) / counter reset / threshold disable

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Backend — sync_service 통합 (PlanChanges + push summary + B2 리팩토링)

**Files:**
- Modify: `backend/app/services/sync_service.py` (가장 큰 변경 — 본 phase 의 핵심)
- Modify: `backend/tests/test_sync_service.py` (push summary 3건 + B2 sync-failure 갱신)

- [ ] **Step 1: Failing tests 작성 (push summary)**

`backend/tests/test_sync_service.py` 끝에 추가:

```python
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


async def test_push_summary_includes_handoff_missing_line(
    async_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
):
    """PLAN 변경 + handoff 부재 → content 에 ⚠️ handoff 누락 줄."""
    proj = await _seed_project(async_session)
    proj.discord_webhook_url = "https://discord.com/api/webhooks/1/abc"
    await async_session.commit()
    await async_session.refresh(proj)

    head = "2" * 40
    event = await _seed_event(
        async_session, proj, head_sha=head,
        commits=[{"id": head, "modified": ["PLAN.md"], "added": []}],
    )

    plan_text = "## 태스크\n\n- [ ] [task-001] 새 task — @alice\n"
    # handoff 파일 fetch 가 None — 부재로 시뮬레이션

    async def fake_fetch(repo, pat, sha, path):
        if path == "PLAN.md":
            return plan_text
        return None  # handoff 부재

    async def fake_compare(repo, pat, base, head_sha):  # noqa: ARG001
        return ["PLAN.md", "handoffs/main.md"]  # changed_files 에는 있지만 fetch 가 None

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
    assert "handoffs/main.md" in content


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
```

- [ ] **Step 2: Verify failure**

```bash
pytest tests/test_sync_service.py -k "push_summary" -v 2>&1 | tail -15
```

Expected: 3 FAIL — 현재 코드는 dispatcher 호출 안 함.

- [ ] **Step 3: Implement — `_apply_plan` 가 PlanChanges return + `_apply_handoff` bool return + `_format_push_summary` + `process_event` 통합**

`backend/app/services/sync_service.py`. 파일 상단 imports + `PlanChanges` dataclass:

```python
from dataclasses import dataclass, field
# ... 기존 imports
from app.services import notification_dispatcher  # 새 import (top-level — 순환 위험 없음, dispatcher 가 sync_service import 안 함)


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
```

`_apply_plan` 시그니처 + 본문 변경 — return value `PlanChanges`:

(기존 함수의 status 전이 분기에서 PlanChanges 인스턴스 mutate 하면서 return.)

핵심 변경 포인트:
- 함수 시작 시 `changes = PlanChanges()` 생성
- 새 task INSERT 분기 — 알림 안 함 (changes 에 안 더함, YAGNI)
- 기존 task 의 `parsed_task.checked AND previous_status != DONE` 분기 → `changes.checked.append((parsed_task.external_id, parsed_task.title))`
  - 단 `parsed_task.title` 이 PLAN 에서만 옴 — 기존 Task row 의 title 과 다를 수 있음. **PLAN 의 title 우선** (사용자가 push 한 최신).
- 기존 task 의 `not parsed_task.checked AND previous_status == DONE` 분기 → `changes.unchecked.append((parsed_task.external_id, parsed_task.title))`
- archived 루프 — `changes.archived.append((ext_id, task.title))` (이건 DB Task row 의 title 사용 — PLAN 에서 사라졌으므로)
- 함수 끝에 `return changes`

`_apply_handoff` 시그니처 + 본문 변경 — return `bool`:

```python
async def _apply_handoff(
    db: AsyncSession,
    project: Project,
    event: GitPushEvent,
    handoff_text: str,
) -> bool:
    """... 기존 docstring ...

    Returns: True (handoff INSERT 또는 UNIQUE 멱등 skip 으로 DB 에 존재) — 알림용 handoff_present 플래그.
    """
    # ... 기존 본문 그대로
    # try ... begin_nested ... db.add ... db.flush ... except IntegrityError: ... logger.info ...

    return True  # 함수 끝 (정상 path 또는 SAVEPOINT skip 양쪽 다 True)
```

`_format_push_summary` 신규 함수 (sync_service.py 안에):

```python
def _format_push_summary(
    *,
    pusher: str,
    branch: str,
    short_sha: str,
    plan_changes: PlanChanges | None,
    handoff_missing: bool,
) -> str | None:
    """push summary 메시지 생성. 모든 카테고리 비고 + handoff 정상 → None."""
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
        lines.append(f"⚠️ handoff 누락 — handoffs/{branch}.md 갱신 필요")

    return "\n".join(lines)
```

`process_event` 변경 — success path 에 dispatcher 호출 추가, except path 의 직접 send_webhook 호출을 dispatcher 경유로:

(파일에서 `process_event` 함수의 try 분기 + except 분기 둘 다 변경 필요. 아래는 기존 함수 통째 교체 형태로 보여줌 — 실제 편집 시는 try / except 두 블록만 수정.)

```python
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

    event_id = event.id
    # B2 capture: rollback 후 ORM expire 회피
    project_name = project.name
    event_branch = event.branch
    event_head_sha = event.head_commit_sha
    discord_webhook_url = project.discord_webhook_url

    try:
        plan_changes: PlanChanges | None = None
        handoff_present = False
        plan_changed_in_event = project.plan_path in await _collect_changed_files(
            event, project, fetch_compare=fetch_compare,
        )
        # NOTE: 위 줄은 _process_inner 안에서도 _collect_changed_files 호출. 중복 호출 회피 위해
        # _process_inner 가 plan_changed/handoff_changed/plan_changes/handoff_present 를 dict 또는
        # tuple 로 return 하는 패턴이 더 깔끔. 본 plan 의 권장 변경:
        # _process_inner → return (plan_changes, handoff_present, plan_changed)
        # process_event 가 받아서 알림 결정.

        plan_changes, handoff_present, plan_changed = await _process_inner(
            db, event, project, fetch_file=fetch_file, fetch_compare=fetch_compare,
        )

        project.last_synced_commit_sha = event.head_commit_sha
        event.processed_at = datetime.utcnow()
        await db.commit()

        # 알림 — commit 후 (DB 일관 상태에서 발송)
        handoff_missing = plan_changed and not handoff_present
        content = _format_push_summary(
            pusher=event.pusher,
            branch=event.branch,
            short_sha=event.head_commit_sha[:7],
            plan_changes=plan_changes,
            handoff_missing=handoff_missing,
        )
        if content:
            try:
                await notification_dispatcher.dispatch_discord_alert(db, project, content)
            except Exception:
                logger.exception("Failed to dispatch push summary alert for event %s", event_id)
    except Exception as exc:
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

        # B2 sync-failure alert — Phase 6 에서 dispatcher 경유로 변경
        # rollback 후 ORM expire — dispatcher 호출 직전 db.refresh(project) 필요
        if discord_webhook_url:  # 1차 게이트 (refresh 비용 회피)
            try:
                await db.refresh(project)  # ORM 살리기 — dispatcher 안에서 attribute 접근 안전
                content = (
                    f"⚠️ **pslog sync 실패** — {project_name}\n"
                    f"branch: `{event_branch}`\n"
                    f"commit: `{event_head_sha[:7]}`\n"
                    f"error: ```{error_msg[:500]}```"
                )
                await notification_dispatcher.dispatch_discord_alert(db, project, content)
            except Exception:
                logger.exception("Failed to dispatch sync-failure alert for event %s", event_id)
```

`_process_inner` 시그니처 변경 — return tuple `(plan_changes: PlanChanges | None, handoff_present: bool, plan_changed: bool)`:

```python
async def _process_inner(
    db: AsyncSession,
    event: GitPushEvent,
    project: Project,
    *,
    fetch_file: FetchFile,
    fetch_compare: FetchCompare,
) -> tuple[PlanChanges | None, bool, bool]:
    changed_files = await _collect_changed_files(
        event, project, fetch_compare=fetch_compare,
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
```

- [ ] **Step 4: Verify push summary tests pass + 기존 sync_service 테스트 회귀**

```bash
pytest tests/test_sync_service.py -v 2>&1 | tail -20
```

Expected: 모든 sync_service 테스트 PASS. 신규 push_summary 3건 + 기존 26 + B2 의 sync-failure 3건. 만약 B2 의 sync-failure 테스트 (`test_discord_alert_called_on_failure_with_webhook_url` 등) 가 monkeypatch target 차이로 fail 하면 다음 step 5 에서 갱신.

(사실 B2 가 `app.services.discord_service.send_webhook` 을 mock 하고, dispatcher 도 결국 같은 함수 호출 — 둘 다 잡힘. 통과 가능성 높음.)

- [ ] **Step 5: B2 sync-failure 테스트 검증 — fail 하면 갱신**

만약 step 4 에서 B2 의 다음 테스트들이 fail 했다면:
- `test_discord_alert_called_on_failure_with_webhook_url`
- `test_discord_alert_skipped_when_webhook_url_missing`
- `test_discord_alert_not_called_on_success_path`

원인 가능성:
- (a) `discord_webhook_url IS NULL` 케이스에서 dispatcher 도 안 거치면 send 안 호출 — 동일 결과 (기존 테스트 통과).
- (b) `discord_webhook_url` set 인데 process_event 의 success path 가 끝 commit 후 dispatcher 호출 — `test_discord_alert_not_called_on_success_path` 가 깨질 수 있음 (success path 알림 추가됨!). 이 테스트의 PLAN content `"## 태스크\n\n- [ ] [task-001] T — @alice"` 는 신규 task 1건 — 알림 안 함 (YAGNI 결정). content 가 PlanChanges.checked/unchecked/archived 에 안 들어가므로 _format_push_summary 가 None return → dispatcher 호출 안 함 → 테스트 통과.
  
  단 만약 신규 INSERT 도 알림 카테고리에 들어가도록 의도가 바뀌면 이 테스트 가정 깨짐. 현재 plan 은 신규 INSERT 알림 안 함이라 통과.

요약: 통과 예상. fail 시 mock target 추가 (`monkeypatch.setattr(notification_dispatcher_mod, "dispatch_discord_alert", fake_dispatch)`) 하는 식으로 적응.

- [ ] **Step 6: 전체 회귀**

```bash
pytest -q 2>&1 | tail -5
```

Expected: 193 passed (190 + 3 push_summary). 만약 B2 sync-failure 테스트 갱신 필요했다면 같은 수.

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/sync_service.py backend/tests/test_sync_service.py
git commit -m "$(cat <<'EOF'
feat(phase6): sync_service push summary 알림 + B2 sync-failure dispatcher 경유

- PlanChanges dataclass — _apply_plan 가 변경 요약 return (체크/롤백/archived)
- _apply_handoff 가 bool return — handoff_present 플래그
- _process_inner 가 (plan_changes, handoff_present, plan_changed) tuple return
- _format_push_summary — push 1건 = 메시지 1건 (변경 없는 카테고리 줄 생략)
- process_event success path: commit 후 dispatcher 호출 (변경 있을 때만)
- process_event except path: 직접 send_webhook → dispatcher 경유 + db.refresh(project) 추가 (rollback ORM expire 회피)
- 신규 INSERT 알림 안 함 (sprint 초 noise 회피, YAGNI)
- 회귀 3건: 모든 카테고리 / handoff 누락 줄 / no-op push skip

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Backend — `GET /git-settings` 응답 확장 + `POST /discord-reset` + `update_project` 자동 reset

**Files:**
- Modify: `backend/app/schemas/git_settings.py` (GitSettingsResponse 3 필드 + ConfigDict 확인)
- Modify: `backend/app/api/v1/endpoints/git_settings.py` (GET/PATCH 응답에 3 필드 + POST /discord-reset)
- Modify: `backend/app/services/project_service.py` (update_project 가 URL 변경 시 자동 reset)
- Modify: `backend/tests/test_git_settings_endpoint.py` (discord-reset 3건 + GET 응답 1건)
- Modify or Create: `backend/tests/test_project_service.py` (URL 변경 시 reset 1건)

- [ ] **Step 1: Failing tests 작성**

`backend/tests/test_git_settings_endpoint.py` 끝에 추가:

```python
# ---------------------------------------------------------------------------
# Phase 6: GitSettings 응답 확장 + POST /discord-reset
# ---------------------------------------------------------------------------


async def test_get_git_settings_includes_discord_status(
    client_with_db, async_session: AsyncSession
):
    """GET /git-settings 응답에 discord_enabled / discord_disabled_at / discord_consecutive_failures 포함."""
    user, proj = await _seed_user_project(async_session)
    proj.discord_webhook_url = "https://discord.com/api/webhooks/1/abc"
    await async_session.commit()

    token = _auth_token(user)
    res = await client_with_db.get(
        f"/api/v1/projects/{proj.id}/git-settings",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["discord_enabled"] is True
    assert body["discord_disabled_at"] is None
    assert body["discord_consecutive_failures"] == 0


async def test_discord_reset_clears_counter_and_disabled_at(
    client_with_db, async_session: AsyncSession
):
    """POST /discord-reset (OWNER) → counter 0 + disabled_at NULL."""
    user, proj = await _seed_user_project(async_session)
    proj.discord_webhook_url = "https://discord.com/api/webhooks/1/abc"
    proj.discord_consecutive_failures = 3
    proj.discord_disabled_at = datetime.utcnow()
    await async_session.commit()

    token = _auth_token(user)
    res = await client_with_db.post(
        f"/api/v1/projects/{proj.id}/git-settings/discord-reset",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["discord_enabled"] is True
    assert body["discord_disabled_at"] is None
    assert body["discord_consecutive_failures"] == 0

    await async_session.refresh(proj)
    assert proj.discord_consecutive_failures == 0
    assert proj.discord_disabled_at is None


async def test_discord_reset_403_for_non_owner(
    client_with_db, async_session: AsyncSession
):
    user, proj = await _seed_user_project(async_session, role=WorkspaceRole.EDITOR)
    token = _auth_token(user)
    res = await client_with_db.post(
        f"/api/v1/projects/{proj.id}/git-settings/discord-reset",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 403


async def test_discord_reset_404_for_non_member(
    client_with_db, async_session: AsyncSession
):
    user, proj = await _seed_user_project(async_session)
    other = User(
        email=f"o-{uuid.uuid4().hex[:8]}@example.com", name="bob", password_hash="x",
    )
    async_session.add(other)
    await async_session.commit()
    await async_session.refresh(other)

    token = _auth_token(other)
    res = await client_with_db.post(
        f"/api/v1/projects/{proj.id}/git-settings/discord-reset",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 404
```

`backend/tests/test_project_service.py` 신규:

```python
"""project_service 회귀 — Phase 6 의 URL 변경 시 자동 reset.

설계서: 2026-05-01-phase-6-discord-notifications-design.md §3.7
"""

import uuid
from datetime import datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.project import Project
from app.models.workspace import Workspace
from app.schemas.project import ProjectUpdate
from app.services import project_service


async def test_update_project_resets_discord_counter_when_url_changes(
    async_session: AsyncSession,
):
    """update_project 가 discord_webhook_url 변경 감지 시 counter / disabled_at reset."""
    ws = Workspace(name="ws", slug=f"ws-{uuid.uuid4().hex[:8]}")
    async_session.add(ws)
    await async_session.flush()
    proj = Project(
        workspace_id=ws.id, name="p",
        discord_webhook_url="https://discord.com/api/webhooks/1/old",
        discord_consecutive_failures=3,
        discord_disabled_at=datetime.utcnow(),
    )
    async_session.add(proj)
    await async_session.commit()
    await async_session.refresh(proj)

    update = ProjectUpdate(discord_webhook_url="https://discord.com/api/webhooks/1/new")
    updated = await project_service.update_project(async_session, proj.id, update)

    assert updated is not None
    assert updated.discord_webhook_url == "https://discord.com/api/webhooks/1/new"
    assert updated.discord_consecutive_failures == 0
    assert updated.discord_disabled_at is None
```

- [ ] **Step 2: Verify failure**

```bash
pytest tests/test_git_settings_endpoint.py -k "discord_reset or discord_status" -v 2>&1 | tail -15
pytest tests/test_project_service.py -v 2>&1 | tail -10
```

Expected: 4 + 1 = 5 FAIL — endpoint / 필드 / 자동 reset 모두 미구현.

- [ ] **Step 3: `GitSettingsResponse` 에 3 필드 추가**

`backend/app/schemas/git_settings.py` 의 `GitSettingsResponse` 클래스 (line ~14) 끝에:

```python
class GitSettingsResponse(BaseModel):
    """GET /git-settings — PAT 평문은 절대 응답에 포함 안 함."""

    model_config = ConfigDict(from_attributes=True)

    git_repo_url: str | None
    git_default_branch: str
    plan_path: str
    handoff_dir: str
    last_synced_commit_sha: str | None
    has_webhook_secret: bool
    has_github_pat: bool
    public_webhook_url: str
    # Phase 6 — Discord 알림 상태
    discord_enabled: bool
    discord_disabled_at: datetime | None
    discord_consecutive_failures: int
```

- [ ] **Step 4: `git_settings.py` endpoint 핸들러 — GET/PATCH 응답 확장 + POST /discord-reset**

`backend/app/api/v1/endpoints/git_settings.py`:

GET 핸들러의 response 빌드 시 3 필드 채우기. 기존 GET 핸들러에 `_build_response(project)` 형태로 추출 권장 (DRY — PATCH / discord-reset 도 같은 모양 사용):

```python
def _build_git_settings_response(project) -> GitSettingsResponse:
    """GitSettings 응답 builder — GET/PATCH/discord-reset 공통."""
    return GitSettingsResponse(
        git_repo_url=project.git_repo_url,
        git_default_branch=project.git_default_branch,
        plan_path=project.plan_path,
        handoff_dir=project.handoff_dir,
        last_synced_commit_sha=project.last_synced_commit_sha,
        has_webhook_secret=project.webhook_secret_encrypted is not None,
        has_github_pat=project.github_pat_encrypted is not None,
        public_webhook_url=_public_webhook_url(),
        # Phase 6
        discord_enabled=(
            project.discord_webhook_url is not None
            and project.discord_disabled_at is None
        ),
        discord_disabled_at=project.discord_disabled_at,
        discord_consecutive_failures=project.discord_consecutive_failures,
    )
```

GET / PATCH 핸들러 안의 직접 `GitSettingsResponse(...)` 호출을 `_build_git_settings_response(project)` 로 교체.

POST `/discord-reset` 핸들러 추가 (다른 /git-settings 핸들러들 사이 적당한 위치):

```python
@router.post(
    "/{project_id}/git-settings/discord-reset",
    response_model=GitSettingsResponse,
)
async def reset_discord(
    project_id: UUID,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
):
    """Discord 알림 비활성화 해제 — counter 0 + disabled_at NULL.
    설계서: 2026-05-01-phase-6-discord-notifications-design.md §3.5
    """
    project = await project_service.get_project(db, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    role = await get_effective_role(db, user.id, project_id)
    if role is None:
        raise HTTPException(status_code=404, detail="Project not found")
    if not can_manage(role):
        raise HTTPException(status_code=403, detail="Owner only")

    project.discord_consecutive_failures = 0
    project.discord_disabled_at = None
    await db.commit()
    await db.refresh(project)

    return _build_git_settings_response(project)
```

- [ ] **Step 5: `project_service.update_project` 가 URL 변경 시 reset**

`backend/app/services/project_service.py` 의 `update_project` 함수 (DashboardPage 가 호출하는 PATCH `/projects/{id}` 핸들러의 service). 변경 dict 처리 부분에 분기 추가:

```python
async def update_project(
    db: AsyncSession,
    project_id: UUID,
    data: ProjectUpdate,
) -> Project | None:
    project = await db.get(Project, project_id)
    if project is None:
        return None
    update_data = data.model_dump(exclude_unset=True)
    # Phase 6: discord_webhook_url 변경 감지 시 자동 reset (URL 바뀌면 새 시작)
    if (
        "discord_webhook_url" in update_data
        and update_data["discord_webhook_url"] != project.discord_webhook_url
    ):
        project.discord_consecutive_failures = 0
        project.discord_disabled_at = None
    for field, value in update_data.items():
        setattr(project, field, value)
    await db.commit()
    await db.refresh(project)
    return project
```

(현재 `update_project` 의 정확한 시그니처 / 본문 확인 후 같은 패턴으로 변경 — 위는 일반 패턴. 기존 코드의 dict-iteration 위에 reset 분기 추가.)

- [ ] **Step 6: Verify pass + 회귀**

```bash
pytest tests/test_git_settings_endpoint.py -k "discord_reset or discord_status" -v 2>&1 | tail -10
pytest tests/test_project_service.py -v 2>&1 | tail -10
pytest -q 2>&1 | tail -5
```

Expected: 4 + 1 신규 PASS, 전체 `198 passed` (193 + 5).

만약 기존 `test_get_git_settings_returns_current_state` 등이 깨지면 — 응답에 신규 3 필드 추가됨 → Pydantic strict 가 unknown field 라 reject 안 함 (`from_attributes=True` 만). 이 테스트들은 specific 필드만 assert 하므로 통과 예상.

- [ ] **Step 7: Commit**

```bash
git add backend/app/schemas/git_settings.py backend/app/api/v1/endpoints/git_settings.py backend/app/services/project_service.py backend/tests/test_git_settings_endpoint.py backend/tests/test_project_service.py
git commit -m "$(cat <<'EOF'
feat(phase6): GitSettings 응답 확장 + POST /discord-reset + update_project 자동 reset

- GitSettingsResponse 에 discord_enabled / discord_disabled_at / discord_consecutive_failures
- _build_git_settings_response 헬퍼 (DRY — GET/PATCH/discord-reset 공통)
- POST /git-settings/discord-reset (OWNER 전용) — counter 0 + disabled_at NULL
- project_service.update_project 가 URL 변경 시 자동 reset (DashboardPage 의 PATCH /projects 경로 자동 적용)
- 회귀 5건: GET 응답 / discord-reset 정상 / 비-OWNER 403 / 비-멤버 404 / URL 변경 시 reset

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Frontend — types + api method

**Files:**
- Modify: `frontend/src/types/git.ts` (GitSettings 인터페이스에 3 필드)
- Modify: `frontend/src/services/api.ts` (`git.resetDiscord` method)

- [ ] **Step 1: 변경 적용**

`frontend/src/types/git.ts` 의 `GitSettings` 인터페이스 끝에 3 필드:

```typescript
export interface GitSettings {
  git_repo_url: string | null;
  git_default_branch: string;
  plan_path: string;
  handoff_dir: string;
  last_synced_commit_sha: string | null;
  has_webhook_secret: boolean;
  has_github_pat: boolean;
  public_webhook_url: string;
  // Phase 6 — Discord 알림 상태
  discord_enabled: boolean;
  discord_disabled_at: string | null;       // ISO datetime
  discord_consecutive_failures: number;
}
```

`frontend/src/services/api.ts` 의 `git` 그룹에 method 추가 (적당한 위치 — `reprocessEvent` 다음 권장):

```typescript
git: {
  // ... 기존 method
  resetDiscord: (projectId: string) =>
    apiClient.post<GitSettings>(`/projects/${projectId}/git-settings/discord-reset`),
},
```

- [ ] **Step 2: Build + lint**

```bash
cd frontend
bun run build 2>&1 | tail -5
bun run lint 2>&1 | tail -10
```

Expected: build clean, no new lint violations (8 pre-existing).

- [ ] **Step 3: Commit**

```bash
cd /Users/arden/Documents/ardensdevspace/pslog/.worktrees/phase-6-discord-notifications
git add frontend/src/types/git.ts frontend/src/services/api.ts
git commit -m "$(cat <<'EOF'
feat(phase6/frontend): types + api.ts — GitSettings 3 필드 + resetDiscord method

- discord_enabled / discord_disabled_at / discord_consecutive_failures
- api.git.resetDiscord(projectId) — POST /git-settings/discord-reset

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: Frontend — `useResetDiscord` hook

**Files:**
- Modify: `frontend/src/hooks/useGithubSettings.ts` (신규 훅 추가)

- [ ] **Step 1: 변경 적용**

`frontend/src/hooks/useGithubSettings.ts` 끝에 추가:

```typescript
// Phase 6 — Discord 알림 비활성화 해제 (재활성화 버튼)
export function useResetDiscord(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => api.git.resetDiscord(projectId).then((r) => r.data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['projects', projectId, 'git-settings'] });
    },
  });
}
```

- [ ] **Step 2: Build + lint**

```bash
cd frontend && bun run build 2>&1 | tail -5 && bun run lint 2>&1 | tail -10
```

Expected: clean.

- [ ] **Step 3: Commit**

```bash
cd /Users/arden/Documents/ardensdevspace/pslog/.worktrees/phase-6-discord-notifications
git add frontend/src/hooks/useGithubSettings.ts
git commit -m "$(cat <<'EOF'
feat(phase6/frontend): useResetDiscord hook

- TanStack Query mutation, onSuccess → git-settings invalidate

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: Frontend — `ProjectGitSettingsModal` Discord 섹션

**Files:**
- Modify: `frontend/src/components/sidebar/ProjectGitSettingsModal.tsx` (Discord 섹션 추가)

- [ ] **Step 1: 현재 ProjectGitSettingsModal 읽기**

```bash
cat frontend/src/components/sidebar/ProjectGitSettingsModal.tsx
```

확인 포인트:
- `useGitSettings` 호출 → `settings` 변수
- 폼 영역의 끝 위치 (저장 버튼 직전 또는 직후)
- `useResetDiscord` import 경로

- [ ] **Step 2: 변경 적용 — Discord 섹션 추가**

`frontend/src/components/sidebar/ProjectGitSettingsModal.tsx`:

import 추가:

```typescript
import { useResetDiscord } from '@/hooks/useGithubSettings';
```

(기존 useGitSettings / useUpdateGitSettings / useRegisterWebhook import 옆.)

`GitSettingsForm` 또는 outer 컴포넌트 안에서 `useResetDiscord` 훅 호출:

```typescript
const resetDiscord = useResetDiscord(projectId);
```

폼 끝부분 (저장 버튼 또는 webhook 등록 버튼 다음, 닫기 버튼 전) 에 Discord 섹션 추가:

```tsx
{/* Phase 6: Discord 알림 상태 + 재활성화 */}
<div className="border-t-2 border-black pt-4 mt-4">
  <h3 className="font-black text-sm mb-2">Discord 알림</h3>
  <div className="text-xs">
    상태: {settings.discord_enabled ? (
      <span className="text-green-700 font-medium">✅ 활성</span>
    ) : settings.discord_disabled_at ? (
      <span className="text-red-700 font-medium">
        ⚠️ 비활성화 ({settings.discord_consecutive_failures}회 연속 실패)
      </span>
    ) : (
      <span className="text-muted-foreground">⚪ 미설정 — 프로젝트 설정에서 webhook URL 입력</span>
    )}
  </div>
  {settings.discord_disabled_at && (
    <div className="mt-2 text-[11px] text-muted-foreground">
      비활성화 시각: {new Date(settings.discord_disabled_at).toLocaleString()}
    </div>
  )}
  {settings.discord_disabled_at && (
    <button
      type="button"
      onClick={async () => {
        try {
          await resetDiscord.mutateAsync();
          alert('Discord 알림 재활성화 완료');
        } catch (err: unknown) {
          const error = err as { response?: { data?: { detail?: string } }; message?: string };
          alert(error.response?.data?.detail || error.message || '재활성화 실패');
        }
      }}
      disabled={resetDiscord.isPending}
      className="mt-2 px-3 py-1.5 text-xs font-medium border-2 border-black bg-white hover:bg-yellow-50 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
    >
      {resetDiscord.isPending ? '재활성화 중...' : '재활성화'}
    </button>
  )}
</div>
```

(URL 입력 필드는 추가 안 함 — DashboardPage 의 기존 input 그대로 유지. backend 가 URL 변경 시 자동 reset.)

- [ ] **Step 3: Build + lint**

```bash
cd frontend && bun run build 2>&1 | tail -5 && bun run lint 2>&1 | tail -10
```

Expected: clean. lint 가 `react-hooks/set-state-in-effect` 류 위배 잡으면 — 본 변경은 useEffect 없음, set-state-in-effect 위배 없을 것.

- [ ] **Step 4: Commit**

```bash
cd /Users/arden/Documents/ardensdevspace/pslog/.worktrees/phase-6-discord-notifications
git add frontend/src/components/sidebar/ProjectGitSettingsModal.tsx
git commit -m "$(cat <<'EOF'
feat(phase6/frontend): ProjectGitSettingsModal — Discord 섹션 (상태 + 재활성화)

- 상태 표시 4 분기: 활성 / 비활성화 (실패 횟수) / 미설정 / (URL 자체는 DashboardPage 입력 그대로)
- 비활성화 시각 표시 (toLocaleString)
- 재활성화 버튼 — 비활성화 상태에서만 노출. mutation pending 시 disable + spinner
- onError → alert (toast 미도입, B2 패턴 그대로)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 8: 최종 회귀 + handoff + PR

- [ ] **Step 1: 전체 backend 회귀**

```bash
cd /Users/arden/Documents/ardensdevspace/pslog/.worktrees/phase-6-discord-notifications/backend
source venv/bin/activate
pytest -q 2>&1 | tail -3
```

Expected: 198 passed (184 baseline + 2 migration + 4 dispatcher + 3 push summary + 4 git_settings + 1 project_service).

- [ ] **Step 2: Frontend build + lint**

```bash
cd ../frontend
bun run build 2>&1 | tail -5
bun run lint 2>&1 | tail -10
```

Expected: build clean, lint 신규 위배 0 (Phase 5b/B2 의 8 pre-existing 그대로).

- [ ] **Step 3: handoff 갱신**

`handoffs/main.md` 상단에 새 섹션:

```markdown
## 2026-05-01 (Phase 6 — Discord 알림 통합 본편)

- [x] **Phase 6 — Discord 알림 통합 본편** — 브랜치 `feature/phase-6-discord-notifications`
  - [x] **Project 모델 +2 컬럼** (`discord_consecutive_failures` / `discord_disabled_at`) — alembic 1건. 기존 row 자동 활성 상태.
  - [x] **`notification_dispatcher` 신규 서비스** — 모든 Discord 알림 1점 진입. URL NULL / disabled_at set → no-op. 성공 시 counter reset, 실패 시 +1 (3 도달 시 disabled_at = now). 알림 실패 silent.
  - [x] **Push 알림 (success path)** — `_apply_plan` 가 `PlanChanges` dataclass return, `_apply_handoff` 가 bool return. `_process_inner` 가 tuple return 으로 모음. `_format_push_summary` 가 4 카테고리 (체크/롤백/archived/handoff 누락) — 모든 카테고리 비고 + handoff 정상 → None return. 신규 INSERT 알림은 안 함 (sprint 초 noise YAGNI).
  - [x] **B2 sync-failure 리팩토링** — `process_event` except 분기의 직접 `send_webhook` → dispatcher 경유. **B1 의 rollback ORM expire 함정 학습**: `db.refresh(project)` 1줄 추가 (capture 한 webhook URL 이 truthy 일 때만).
  - [x] **`POST /git-settings/discord-reset`** (OWNER 전용) — counter / disabled_at reset.
  - [x] **`GitSettingsResponse` 3 필드** (`discord_enabled` / `discord_disabled_at` / `discord_consecutive_failures`). `_build_git_settings_response` 헬퍼로 GET/PATCH/discord-reset DRY.
  - [x] **`project_service.update_project` 자동 reset** — URL 변경 감지 시 같은 트랜잭션에서 reset (DashboardPage 의 PATCH /projects 경로 자동 적용).
  - [x] **Frontend `ProjectGitSettingsModal` Discord 섹션** — 상태 (활성/비활성화/미설정) + 비활성화 시각 + 재활성화 버튼. URL 입력은 DashboardPage 그대로 (UI 변경 없음).
  - [x] **검증**: backend **198 tests pass** (184 baseline + 14 신규: 2 migration + 4 dispatcher + 3 push summary + 4 git_settings + 1 project_service). frontend `bun run build` clean, `bun run lint` 8 pre-existing only. **시각 검증 + e2e 사용자 직접** (PR 본문 체크리스트).

### 마지막 커밋

- pslog: `<sha> docs(handoff+plan): Phase 6 완료 + Phase 7 (선택) 다음 할 일`
- 브랜치 base: `29c7db7` (main, B2 PR #14 머지 직후)

### 다음 (Phase 7 — Gemma 브리핑, 선택)

spec §11 의 마지막 phase. 진입 전 맥미니에서 Gemma 4 26B MoE 추론 시간 실측 필요 (30초 빈번 초과 시 비동기 응답 패턴). Phase 1~6 안정화 + 1주 무중단 검증 후 별개 trigger.

또는: **error-log spec 진입** (`2026-04-26-error-log-design.md`). task-automation Phase 4 (sync_service) 안정화 1주 무중단 후 진입 가능 — 현재 충족.

### 블로커

없음

### 메모 (2026-05-01 Phase 6 추가)

- **`PlanChanges` dataclass return 패턴**: `_apply_plan` 가 mutation 외에 변경 요약 dict 도 return — `process_event` 가 모아서 dispatcher 호출. mutable global state 안 씀, 함수 순수성 유지. 향후 알림 종류 추가 시 카테고리만 늘리면 됨.
- **B1 의 rollback ORM expire 함정 재발 회피**: success path 는 commit 직전까지 ORM 살아있어 dispatcher 가 직접 project 접근 안전. except path 는 rollback 이 expire 시키므로 dispatcher 호출 직전 `db.refresh(project)` 필수. capture 된 webhook URL 은 1차 게이트로 사용 (truthy 일 때만 refresh — 비용 회피).
- **신규 INSERT 알림 미도입 결정**: sprint 초 PLAN 작성 시 노이즈 폭발. 사용자 호소 시 후속 옵션 컬럼 추가.
- **알림 종류별 on/off 미도입**: `discord_webhook_url` NULL 또는 `discord_disabled_at` set 만으로 사용자 제어. 종류별 필터는 사용자 호소 시 후속.
- **DashboardPage 의 URL 입력 + GitSettings 의 상태 표시 분리**: 기존 UX 그대로 (URL 변경 코스트 ↓), 백엔드 `update_project` 가 URL 변경 감지 시 자동 reset 으로 일관 보장. URL 입력 모달 안으로 이동은 UX 결정 후속.
- **alembic autogenerate 의 `server_default` 함정**: `nullable=False` + Python `default=0` 만으로는 server_default 안 들어가 기존 row 가 NOT NULL 위반. 수동으로 `server_default='0'` 추가 필요. 본 phase 의 `discord_consecutive_failures` 가 같은 패턴.
- **next 가능 옵션**: Phase 7 (Gemma 브리핑) 또는 error-log spec. Phase 4 안정화 1주 충족 — error-log 진입 trigger 도 가능.
```

- [ ] **Step 4: handoff + plan + spec commit**

```bash
cd /Users/arden/Documents/ardensdevspace/pslog/.worktrees/phase-6-discord-notifications
git add handoffs/main.md docs/superpowers/plans/2026-05-01-phase-6-discord-notifications.md
git commit -m "$(cat <<'EOF'
docs(handoff+plan): Phase 6 완료 + Phase 7 (선택) 다음 할 일

- handoffs/main.md 에 2026-05-01 Phase 6 섹션 추가 (Discord 알림 통합 본편, 198 tests)
- docs/superpowers/plans/2026-05-01-phase-6-discord-notifications.md 신규 (구현 plan 보존)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 5: Push + PR**

```bash
git push -u origin feature/phase-6-discord-notifications
gh pr create \
  --title "feat(phase6): Discord 알림 통합 본편 — push summary + auto-disable + dispatcher" \
  --body "$(cat <<'EOF'
## Summary

spec §11 의 Phase 6 본편 — push 1건 = 알림 1건 요약 (체크/롤백/archived/handoff 누락) + Discord webhook 3회 연속 실패 시 자동 disable + GitSettings 모달에서 재활성화. B2 의 sync-failure alert 도 같은 dispatcher 통과해 cooldown / disable 정책 통합.

- **Project 모델 +2 컬럼** (`discord_consecutive_failures` / `discord_disabled_at`) — alembic 1건
- **`notification_dispatcher` 신규** — 모든 Discord 알림 1점 진입. URL NULL / disabled → no-op. 성공 시 counter reset, 실패 시 +1 (3 도달 시 disabled_at = now). 알림 실패 silent (caller 영향 없음).
- **Push 알림 (success path)** — `_apply_plan` 가 `PlanChanges` dataclass return. `_format_push_summary` 가 4 카테고리 (체크/롤백/archived/handoff 누락). 변경 없는 카테고리 줄 자체 생략. 모든 카테고리 비고 + handoff 정상 → 알림 안 함. 신규 INSERT 알림 안 함 (YAGNI).
- **B2 sync-failure 리팩토링** — dispatcher 경유로. **B1 학습**: `db.refresh(project)` 1줄로 rollback 후 ORM expire 회피.
- **`POST /git-settings/discord-reset`** (OWNER) — counter / disabled_at reset.
- **`GitSettings` 응답 3 필드** + **GitSettings 모달 Discord 섹션** (상태 + 재활성화 버튼).
- **URL 자동 reset** — `project_service.update_project` 가 `discord_webhook_url` 변경 감지 시 같은 트랜잭션에서 reset.

## Test plan

- [x] backend **198 tests pass** (184 B2 baseline + 14 신규: 2 migration + 4 dispatcher + 3 push summary + 4 git_settings + 1 project_service)
- [x] frontend \`bun run build\` clean
- [x] frontend \`bun run lint\` 신규 위배 0 (Phase 5b/B2 의 8 pre-existing 그대로)
- [ ] 시각 검증 — 사용자 dev server 직접:
  - GitSettings 모달의 Discord 섹션 — 활성 / 비활성화 / 미설정 3 상태
  - URL 변경 후 자동 reset (counter 0 + disabled_at NULL)
  - 의도적 잘못된 URL 후 push 3번 → disable, 모달에 표시 → 재활성화 버튼 → counter reset
- [ ] e2e — 의도적 PLAN.md 변경 push (체크 + 롤백 + archived + handoff 누락 섞은 case) → Discord 채널에 push summary 한 메시지 도착, 모든 카테고리 줄 + ⚠️ handoff 누락 줄 포함

## 다음 (Phase 7 또는 error-log spec)

spec §11 의 Phase 7 (Gemma 브리핑, 선택) 또는 error-log spec (`2026-04-26-error-log-design.md`) 진입. task-automation Phase 4 (sync_service) 안정화 1주 무중단 충족 — error-log 진입 trigger 가능.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Self-Review Pass

**1. Spec coverage** — spec §1-7 vs plan tasks 매핑:

| Spec 항목 | Plan task |
|---|---|
| §3.1 dispatcher | Task 2 |
| §3.2 Project 2 컬럼 + `__init__` default | Task 1 |
| §3.3 alembic 마이그레이션 | Task 1 |
| §3.4 sync_service 통합 + PlanChanges + _format_push_summary | Task 3 |
| §3.5 POST /discord-reset | Task 4 |
| §3.6 GitSettingsResponse 3 필드 | Task 4 |
| §3.7 PATCH /git-settings 확장 | **revised → project_service.update_project 자동 reset (Task 4)** — DashboardPage 의 기존 PATCH /projects 경로 그대로 사용. spec 의 GitSettings PATCH 확장은 안 함 (UI 변경 최소화). |
| §4.1 frontend types + api | Task 5 |
| §4.2 useResetDiscord hook | Task 6 |
| §4.3 ProjectGitSettingsModal Discord 섹션 | Task 7 |
| §5 test plan | Task 1-4 의 backend 14건 + Task 8 회귀 |

**§3.7 변경 사유**: 코드 탐색 결과 `discord_webhook_url` 입력 UI 가 이미 DashboardPage 에 있음 (`PATCH /projects` 경로). 본 phase 는 UI 변경 최소화 — auto-reset 로직만 `project_service.update_project` 에 추가. spec 의 "PATCH /git-settings 가 discord_webhook_url 도 받음" 확장은 미구현 (URL 편집 두 곳에서 가능 = 혼란). spec 자체에 명시 — plan 은 이를 반영해 명확화.

**2. Placeholder scan** — `<sha>` (Task 8 handoff 의 commit 후 자리표시) 외 placeholder 없음. "TBD/TODO/implement later" 0.

**3. Type / signature consistency**:
- Backend: `Project.discord_consecutive_failures: int` ↔ schema `discord_consecutive_failures: int` ↔ frontend `discord_consecutive_failures: number` — 일관
- Backend: `Project.discord_disabled_at: datetime | None` ↔ schema `discord_disabled_at: datetime | None` ↔ frontend `discord_disabled_at: string | null` — 일관 (ISO datetime 문자열)
- Backend: `discord_enabled: bool` (computed = url not None AND disabled_at is None) ↔ frontend `discord_enabled: boolean` — 일관
- `notification_dispatcher.dispatch_discord_alert(db, project, content)` ↔ sync_service success/failure path 호출 ↔ test mock — 일관
- `PlanChanges.checked / unchecked / archived` ↔ `_format_push_summary` 안의 사용 ↔ test assertions ("✅ 완료", "↩️ 되돌림", "🗑️ PLAN 에서 제거", "[task-001] 구글 로그인") — 일관
- `POST /git-settings/discord-reset` 경로 ↔ frontend `api.git.resetDiscord` ↔ test endpoint — 일관

**4. 의존 순서**:
- Task 1 (모델 + alembic) → Task 2 (dispatcher 가 project 컬럼 사용) → Task 3 (sync_service 가 dispatcher 사용) → Task 4 (endpoint 가 모델 컬럼 사용) → Task 5 (frontend types 가 backend 응답 매칭) → Task 6 (hook 이 api method 사용) → Task 7 (UI 가 hook + types 사용) → Task 8.
- 현재 순서 의존 만족.

**5. 테스트 결정성**:
- Task 1 마이그레이션: round-trip (microsecond replace 처리)
- Task 2 dispatcher: monkeypatch send_webhook, 결정적
- Task 3 push summary: monkeypatch + 정적 plan_text/handoff_text, 결정적
- Task 4 endpoint: 표준 client_with_db 패턴, 결정적

**6. B2 sync-failure 테스트 호환**:
- B2 의 mock 은 `app.services.discord_service.send_webhook` 을 monkeypatch — dispatcher 도 결국 같은 함수 호출하므로 동일 호출 캡처. 통과 예상.
- 만약 fail 하면 Task 3 step 5 의 분기 처리.

문제 없음. 진행 가능.
