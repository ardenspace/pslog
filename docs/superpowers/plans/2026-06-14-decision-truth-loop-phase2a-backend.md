# 결정-진실 루프 Phase 2a (pslog 드리프트 감지 — 백엔드) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** pslog(pslog)가 handoff `### 결정`의 미승격(A)과 handoff↔PLAN 상태 모순(B)을 자동 감지해 `Drift` 레코드(OPEN→자동 RESOLVED/수동 IGNORED)로 가시화하고 Discord로 알린다. 백엔드만 — API까지 동작, 프론트는 Phase 2b.

**Architecture:**
- **B (상태 모순)**: 기존 git push sync 경로(`sync_service`)에서, **이미 DB에 저장 중인** `Handoff.parsed_tasks`(지금까지 안 쓰던 데이터)를 `Task.status`와 `external_id`로 조인해 모순 감지.
- **A (결정 미승격)**: 새 GitHub `pull_request` 웹훅 이벤트에서, 브랜치 handoff의 `### 결정` 항목 중 `→ DECISIONS`/`→ ADR` 마커가 없거나 PR diff에 DECISIONS.md 변경이 없으면 감지.
- 공용 `Drift` 모델 + `drift_service`(멱등 open/resolve) + Discord dispatch. `error_group` 패턴을 그대로 차용.

**Tech Stack:** FastAPI, SQLAlchemy 2.0 async, Alembic, Pydantic v2, pytest(async_session 픽스처). 모든 코드는 `pslog/backend`.

**상위 설계서:** `docs/superpowers/specs/2026-06-14-decision-truth-loop-design.md` §5, §7. (§7 미결 4건 확정: Q1=PR 웹훅 추가 / Q2=마커 기반+파일변경 교차확인 / Q3=external_id inner-join, 서브체크박스 제외 / Q4=한국어, i18n 없음.)

**작업 브랜치:** pslog repo 에서 `feat/pslog-drift-detection` (main 기준 생성).

---

## File Structure (pslog/backend)

| 파일 | 변경 | 책임 |
|---|---|---|
| `app/schemas/parsed_handoff.py` | 수정 | `Decision` 스키마 + `HandoffSection.decisions` |
| `app/services/handoff_parser_service.py` | 수정 | `### 결정` 파싱 |
| `app/models/drift.py` | 신규 | `Drift` 모델 + `DriftType`/`DriftStatus` |
| `app/models/project.py` | 수정 | `decisions_path` 컬럼 |
| `app/models/__init__.py` | 수정 | `Drift` export |
| `app/services/drift_service.py` | 신규 | 멱등 open/resolve + 감지 A·B 로직 |
| `app/services/sync_service.py` | 수정 | `_process_inner`에 B 평가 wiring |
| `app/schemas/webhook.py` | 수정 | `GitHubPullRequestPayload` |
| `app/api/v1/endpoints/webhooks.py` | 수정 | `pull_request` 이벤트 분기 → A 평가 |
| `app/schemas/drift.py` | 신규 | API 응답/요청 스키마 |
| `app/api/v1/endpoints/drifts.py` | 신규 | `GET/PATCH /projects/{id}/drifts` |
| `app/api/v1/router.py` (또는 등록 지점) | 수정 | drifts 라우터 등록 |
| `app/services/notification_dispatcher.py` 사용처 | 수정 | 드리프트 Discord 알림 |
| `alembic/versions/*` | 신규(autogenerate) | drift 테이블 + projects.decisions_path |

---

## Task 0: 작업 브랜치 생성

- [ ] **Step 1: main 기준 브랜치 생성**

Run:
```bash
cd ~/Documents/ardensdevspace/pslog && git checkout main && git pull --ff-only 2>/dev/null; git checkout -b feat/pslog-drift-detection && git branch --show-current
```
Expected: `feat/pslog-drift-detection`

- [ ] **Step 2: 백엔드 venv 활성 + 테스트 통과 baseline 확인**

Run:
```bash
cd ~/Documents/ardensdevspace/pslog/backend && source venv/bin/activate && python -m pytest -q 2>&1 | tail -5
```
Expected: 전부 PASS (baseline). 실패가 이미 있으면 사용자에게 보고 후 진행.

---

## Task 1: `Decision` 스키마 + `HandoffSection.decisions`

**Files:**
- Modify: `app/schemas/parsed_handoff.py`

- [ ] **Step 1: `Decision` 모델 추가 + `HandoffSection`에 필드 추가**

`app/schemas/parsed_handoff.py` 의 `FreeNotes` 클래스 바로 뒤(`class HandoffSection` 앞)에 추가:
```python
class Decision(BaseModel):
    """`### 결정` 서브섹션의 한 항목 — 구현 중 기획과 달라진 결정.

    형식: `- [task-NNN] <무엇 바꿈> — <왜> → DECISIONS|ADR-NNN`
    """

    model_config = ConfigDict(extra="forbid")

    external_id: str | None    # "task-001" — 없을 수도 있음(브랜치 전체 결정)
    text: str                  # 마커 제외 본문
    promoted: bool             # `→ DECISIONS` / `→ ADR` 마커 존재 여부
```

`HandoffSection` 에 필드 추가 (`subtasks` 줄 아래):
```python
    decisions: list[Decision] = Field(default_factory=list)
```

- [ ] **Step 2: import 가능 확인**

Run:
```bash
cd ~/Documents/ardensdevspace/pslog/backend && source venv/bin/activate && python -c "from app.schemas.parsed_handoff import Decision, HandoffSection; print(HandoffSection().decisions)"
```
Expected: `[]`

- [ ] **Step 3: 커밋**

```bash
cd ~/Documents/ardensdevspace/pslog/backend && git add app/schemas/parsed_handoff.py && git commit -m "feat(schema): ParsedHandoff에 Decision + HandoffSection.decisions"
```

---

## Task 2: handoff 파서가 `### 결정` 추출

`_FREE_NOTE_HEADERS`에 "결정"을 새 자유키로 추가하고, 그 영역의 `- [task-NNN] ... → DECISIONS` 라인을 `Decision`으로 파싱한다. 기존 free_notes(last_commit/next/blockers)와 동일하게 "체크박스 영역 이후 H3 zone"에서 수집.

**Files:**
- Modify: `app/services/handoff_parser_service.py`
- Test: `tests/test_handoff_parser_service.py`

- [ ] **Step 1: 실패 테스트 작성**

`tests/test_handoff_parser_service.py` 끝에 추가:
```python
def test_parse_handoff_decisions_section():
    text = """# Handoff: feat/x — @alice

## 2026-06-14
- [x] task-001

### 결정
- [task-001] 약관을 인라인 체크박스로 — 전환비용↓ → DECISIONS
- [task-002] 캐시 TTL 5→15분 — 부하 감소
"""
    h = parse_handoff(text)
    decisions = h.sections[0].decisions
    assert len(decisions) == 2
    assert decisions[0].external_id == "task-001"
    assert decisions[0].promoted is True
    assert "약관을 인라인" in decisions[0].text
    assert decisions[1].external_id == "task-002"
    assert decisions[1].promoted is False
```

- [ ] **Step 2: 실패 확인**

Run:
```bash
cd ~/Documents/ardensdevspace/pslog/backend && source venv/bin/activate && python -m pytest tests/test_handoff_parser_service.py::test_parse_handoff_decisions_section -q
```
Expected: FAIL (`decisions` 비어있음 → assert 0 == 2).

- [ ] **Step 3: 파서 구현**

`app/services/handoff_parser_service.py` 수정:

(a) import 에 `Decision` 추가:
```python
from app.schemas.parsed_handoff import (
    CheckItem,
    Decision,
    FreeNotes,
    HandoffSection,
    ParsedHandoff,
    Subtask,
)
```

(b) `_FREE_NOTE_HEADERS` 에 결정 추가 + 결정 라인 정규식 추가:
```python
_FREE_NOTE_HEADERS = {
    "마지막 커밋": "last_commit",
    "다음": "next",
    "블로커": "blockers",
    "결정": "decisions",
}
_DECISION_LINE_RE = re.compile(
    r"^-\s+(?:\[(?P<id>task-[A-Za-z0-9_-]+)\]\s+)?(?P<body>.+?)\s*$"
)
_PROMOTED_RE = re.compile(r"→\s*(DECISIONS|ADR(?:-[A-Za-z0-9_]+)?)\s*$")
```

(c) `_parse_section_body` 의 `free_notes_raw` 초기화에 decisions 키 추가:
```python
    free_notes_raw: dict[str, list[str]] = {
        "last_commit": [], "next": [], "blockers": [], "decisions": [],
    }
```

(d) 반환부에서 decisions 라인들을 `Decision`으로 변환. `_join`/`free_notes` 생성 직전에 추가:
```python
    decisions: list[Decision] = []
    for raw in free_notes_raw["decisions"]:
        line = raw.strip()
        if not line.startswith("- "):
            continue  # 주석(# ...) / 빈 줄 무시
        m = _DECISION_LINE_RE.match(line)
        if not m:
            continue
        body = m.group("body").strip()
        promoted = bool(_PROMOTED_RE.search(body))
        text_clean = _PROMOTED_RE.sub("", body).strip().rstrip("→").strip()
        decisions.append(Decision(
            external_id=m.group("id"),
            text=text_clean,
            promoted=promoted,
        ))
```

(e) 함수 반환 시그니처를 4-tuple로 확장 — `return checks, subtasks, free_notes` 를:
```python
    return checks, subtasks, free_notes, decisions
```
로 바꾸고, 시그니처 타입힌트도:
```python
def _parse_section_body(body_lines: list[str]) -> tuple[list[CheckItem], list[Subtask], FreeNotes, list[Decision]]:
```

(f) 호출부(`parse_handoff` 내부) 업데이트:
```python
        checks, subtasks, free_notes, decisions = _parse_section_body(body)
        sections.append(
            HandoffSection(
                date=date,
                checks=checks,
                subtasks=subtasks,
                free_notes=free_notes,
                decisions=decisions,
            )
        )
```

- [ ] **Step 4: 통과 확인 + 회귀 확인**

Run:
```bash
cd ~/Documents/ardensdevspace/pslog/backend && source venv/bin/activate && python -m pytest tests/test_handoff_parser_service.py -q
```
Expected: 신규 포함 전부 PASS. (`### 결정`이 free_notes 수집을 가로채지 않는지 — 기존 테스트로 회귀 확인.)

- [ ] **Step 5: 커밋**

```bash
git add app/services/handoff_parser_service.py tests/test_handoff_parser_service.py && git commit -m "feat(handoff-parser): ### 결정 섹션 파싱 → decisions[]"
```

---

## Task 3: `Drift` 모델 + `DriftType`/`DriftStatus` + `Project.decisions_path`

**Files:**
- Create: `app/models/drift.py`
- Modify: `app/models/project.py`, `app/models/__init__.py`

- [ ] **Step 1: `app/models/drift.py` 생성**

```python
import enum
import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class DriftType(str, enum.Enum):
    DECISION_NOT_PROMOTED = "decision_not_promoted"   # A
    STATUS_CONTRADICTION = "status_contradiction"     # B


class DriftStatus(str, enum.Enum):
    OPEN = "open"
    RESOLVED = "resolved"
    IGNORED = "ignored"


class Drift(Base):
    """PLAN/handoff/DECISIONS 정합성 위반 1건.

    설계서: 2026-06-14-decision-truth-loop-design.md §5.2
    멱등 키: UNIQUE(project_id, type, dedup_key). dedup_key = branch 또는 branch:external_id.
    """

    __tablename__ = "drifts"
    __table_args__ = (
        UniqueConstraint("project_id", "type", "dedup_key", name="uq_drift_dedup"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))

    type: Mapped[DriftType]
    status: Mapped[DriftStatus] = mapped_column(default=DriftStatus.OPEN)

    branch: Mapped[str]
    external_id: Mapped[str | None] = mapped_column(default=None)  # task-NNN (B), nullable
    dedup_key: Mapped[str]   # "branch" (A) 또는 "branch:task-NNN" (B)

    detail: Mapped[str] = mapped_column(Text)   # 사람용 설명 + 고칠 힌트

    opened_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(default=None)
    last_seen_commit_sha: Mapped[str | None] = mapped_column(default=None)

    def __init__(self, **kwargs: object) -> None:
        kwargs.setdefault("status", DriftStatus.OPEN)
        super().__init__(**kwargs)
```

- [ ] **Step 2: `Project.decisions_path` 컬럼 추가**

`app/models/project.py` 의 `plan_path` 줄 아래에 추가:
```python
    decisions_path: Mapped[str] = mapped_column(default="DECISIONS.md")
```
그리고 `__init__` 의 setdefault 블록에 추가:
```python
        kwargs.setdefault("decisions_path", "DECISIONS.md")
```

- [ ] **Step 3: `app/models/__init__.py` 에 export 추가**

`app/models/__init__.py` 를 열어 다른 모델 import 패턴과 동일하게 추가:
```python
from app.models.drift import Drift, DriftStatus, DriftType  # noqa: F401
```
(기존 `__all__` 이 있으면 `"Drift", "DriftStatus", "DriftType"` 추가.)

- [ ] **Step 4: import 확인**

Run:
```bash
cd ~/Documents/ardensdevspace/pslog/backend && source venv/bin/activate && python -c "from app.models import Drift, DriftType, DriftStatus; from app.models.project import Project; print('decisions_path' in Project.__mapper__.columns)"
```
Expected: `True`

- [ ] **Step 5: 커밋**

```bash
git add app/models/drift.py app/models/project.py app/models/__init__.py && git commit -m "feat(model): Drift 모델 + Project.decisions_path"
```

---

## Task 4: Alembic 마이그레이션 (drifts 테이블 + projects.decisions_path)

**Files:**
- Create: `alembic/versions/<autogen>.py`

- [ ] **Step 1: autogenerate**

Run:
```bash
cd ~/Documents/ardensdevspace/pslog/backend && source venv/bin/activate && alembic revision --autogenerate -m "phase7 drift detection: drifts table + projects.decisions_path"
```
Expected: 새 버전 파일 생성.

- [ ] **Step 2: 생성 파일 직접 검토**

생성된 `alembic/versions/*phase7_drift*.py` 를 열어 확인:
- `op.create_table("drifts", ...)` 에 모든 컬럼 + `uq_drift_dedup` UNIQUE 존재.
- `op.add_column("projects", sa.Column("decisions_path", ...))` 존재. (server_default 가 없으면 기존 row NULL 위험 → `server_default="DECISIONS.md"` 추가하거나, 기존 프로젝트 수가 적으니 nullable=False + server_default 명시.)
- `downgrade()` 가 `drop_table("drifts")` + `drop_column("projects","decisions_path")` 로 대칭인지.
- Enum 타입(`drifttype`, `driftstatus`)이 SQLAlchemy 기본 VARCHAR로 가는지(이 코드베이스는 `str enum` → VARCHAR) — 별도 PG ENUM 생성 안 하면 OK.

필요 시 `decisions_path` 컬럼에 `server_default="DECISIONS.md"` 를 직접 추가한다.

- [ ] **Step 3: upgrade + downgrade 왕복 검증**

Run:
```bash
cd ~/Documents/ardensdevspace/pslog/backend && source venv/bin/activate && alembic upgrade head && alembic downgrade -1 && alembic upgrade head && echo "round-trip OK"
```
Expected: 에러 없이 `round-trip OK`.

- [ ] **Step 4: 커밋**

```bash
git add alembic/versions/ && git commit -m "feat(db): drifts 테이블 + projects.decisions_path 마이그레이션"
```

---

## Task 5: `drift_service` 코어 — 멱등 open/resolve

감지 로직(A·B)이 공통으로 쓸 "지금 드리프트인 항목 집합"을 받아 OPEN 유지/신규 생성하고, 빠진 항목은 자동 RESOLVED 처리. IGNORED는 건드리지 않음.

**Files:**
- Create: `app/services/drift_service.py`
- Test: `tests/test_drift_service.py`

- [ ] **Step 1: 실패 테스트 작성**

`tests/test_drift_service.py` 생성:
```python
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.drift import Drift, DriftStatus, DriftType
from app.models.project import Project
from app.models.workspace import Workspace
from app.services import drift_service


async def _seed_project(db: AsyncSession) -> Project:
    ws = Workspace(name="ws", slug=f"ws-{uuid.uuid4().hex[:8]}")
    db.add(ws)
    await db.flush()
    proj = Project(workspace_id=ws.id, name="p")
    db.add(proj)
    await db.commit()
    await db.refresh(proj)
    return proj


async def _open_drifts(db, proj, type_):
    rows = (await db.execute(
        select(Drift).where(
            Drift.project_id == proj.id, Drift.type == type_,
            Drift.status == DriftStatus.OPEN,
        )
    )).scalars().all()
    return rows


async def test_reconcile_opens_then_autoresolves(async_session: AsyncSession):
    proj = await _seed_project(async_session)
    # 1라운드: task-007 모순 발생
    await drift_service.reconcile(
        async_session, project_id=proj.id, type_=DriftType.STATUS_CONTRADICTION,
        current=[drift_service.DriftItem(
            dedup_key="feat/x:task-007", branch="feat/x", external_id="task-007",
            detail="PLAN DONE인데 handoff 미완", commit_sha="a"*40,
        )],
    )
    await async_session.commit()
    assert len(await _open_drifts(async_session, proj, DriftType.STATUS_CONTRADICTION)) == 1

    # 2라운드: 모순 사라짐 → 자동 RESOLVED
    await drift_service.reconcile(
        async_session, project_id=proj.id, type_=DriftType.STATUS_CONTRADICTION,
        current=[],
    )
    await async_session.commit()
    assert len(await _open_drifts(async_session, proj, DriftType.STATUS_CONTRADICTION)) == 0


async def test_reconcile_idempotent_no_duplicate(async_session: AsyncSession):
    proj = await _seed_project(async_session)
    item = drift_service.DriftItem(
        dedup_key="feat/x", branch="feat/x", external_id=None,
        detail="결정 미승격: task-002", commit_sha="b"*40,
    )
    for _ in range(3):
        await drift_service.reconcile(
            async_session, project_id=proj.id,
            type_=DriftType.DECISION_NOT_PROMOTED, current=[item],
        )
        await async_session.commit()
    rows = (await async_session.execute(
        select(Drift).where(Drift.project_id == proj.id,
                            Drift.type == DriftType.DECISION_NOT_PROMOTED)
    )).scalars().all()
    assert len(rows) == 1  # 멱등
    assert rows[0].status == DriftStatus.OPEN
```

- [ ] **Step 2: 실패 확인**

Run:
```bash
cd ~/Documents/ardensdevspace/pslog/backend && source venv/bin/activate && python -m pytest tests/test_drift_service.py -q
```
Expected: FAIL (`drift_service` / `DriftItem` / `reconcile` 미존재).

- [ ] **Step 3: `app/services/drift_service.py` 구현**

```python
"""드리프트 멱등 open/resolve + 감지 A·B.

설계서: 2026-06-14-decision-truth-loop-design.md §5.
reconcile(): 특정 (project, type)의 "현재 드리프트 집합"을 받아
OPEN 유지/생성, 빠진 OPEN은 RESOLVED. IGNORED는 불변.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.drift import Drift, DriftStatus, DriftType

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
) -> list[Drift]:
    """current = 지금 위반 중인 항목들. 신규는 OPEN 생성, 사라진 OPEN은 RESOLVED.

    Returns: 이번 호출로 새로 OPEN된 Drift 목록 (알림용).
    """
    existing = (await db.execute(
        select(Drift).where(Drift.project_id == project_id, Drift.type == type_)
    )).scalars().all()
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
```

- [ ] **Step 4: 통과 확인**

Run:
```bash
cd ~/Documents/ardensdevspace/pslog/backend && source venv/bin/activate && python -m pytest tests/test_drift_service.py -q
```
Expected: 2 PASS.

- [ ] **Step 5: 커밋**

```bash
git add app/services/drift_service.py tests/test_drift_service.py && git commit -m "feat(drift): drift_service.reconcile 멱등 open/resolve 코어"
```

---

## Task 6: 감지 B (상태 모순) — 저장된 Handoff.parsed_tasks vs Task.status

`Handoff.parsed_tasks`(이미 DB에 저장 중, 지금까지 미사용)를 `Task.status`와 `external_id`로 inner-join. handoff 체크 ≠ (status==DONE) 이면 모순. `drift_service`에 함수 추가 후 `sync_service._process_inner`에서 호출.

**Files:**
- Modify: `app/services/drift_service.py`
- Modify: `app/services/sync_service.py`
- Test: `tests/test_drift_detection_b.py`

- [ ] **Step 1: 실패 테스트 작성**

`tests/test_drift_detection_b.py` 생성:
```python
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.drift import Drift, DriftStatus, DriftType
from app.models.handoff import Handoff
from app.models.project import Project
from app.models.task import Task, TaskSource, TaskStatus
from app.models.workspace import Workspace
from app.services import drift_service


async def _seed(db: AsyncSession):
    ws = Workspace(name="ws", slug=f"ws-{uuid.uuid4().hex[:8]}")
    db.add(ws); await db.flush()
    proj = Project(workspace_id=ws.id, name="p")
    db.add(proj); await db.flush()
    return proj


async def test_detect_b_contradiction(async_session: AsyncSession):
    proj = await _seed(async_session)
    # PLAN: task-007 DONE
    async_session.add(Task(
        project_id=proj.id, title="t7", source=TaskSource.SYNCED_FROM_PLAN,
        external_id="task-007", status=TaskStatus.DONE,
    ))
    # handoff: task-007 미체크
    async_session.add(Handoff(
        project_id=proj.id, branch="feat/x", author_git_login="alice",
        commit_sha="a"*40, pushed_at=None, raw_content="...",
        parsed_tasks=[{"external_id": "task-007", "checked": False, "extra": ""}],
        free_notes={},
    ))
    await async_session.commit()

    await drift_service.detect_status_contradictions(
        async_session, project_id=proj.id, branch="feat/x", commit_sha="a"*40,
    )
    await async_session.commit()

    rows = (await async_session.execute(
        select(Drift).where(Drift.project_id == proj.id,
                            Drift.type == DriftType.STATUS_CONTRADICTION,
                            Drift.status == DriftStatus.OPEN)
    )).scalars().all()
    assert len(rows) == 1
    assert rows[0].external_id == "task-007"


async def test_detect_b_no_contradiction(async_session: AsyncSession):
    proj = await _seed(async_session)
    async_session.add(Task(
        project_id=proj.id, title="t7", source=TaskSource.SYNCED_FROM_PLAN,
        external_id="task-007", status=TaskStatus.DONE,
    ))
    async_session.add(Handoff(
        project_id=proj.id, branch="feat/x", author_git_login="alice",
        commit_sha="a"*40, pushed_at=None, raw_content="...",
        parsed_tasks=[{"external_id": "task-007", "checked": True, "extra": ""}],
        free_notes={},
    ))
    await async_session.commit()
    await drift_service.detect_status_contradictions(
        async_session, project_id=proj.id, branch="feat/x", commit_sha="a"*40,
    )
    await async_session.commit()
    rows = (await async_session.execute(
        select(Drift).where(Drift.project_id == proj.id,
                            Drift.type == DriftType.STATUS_CONTRADICTION,
                            Drift.status == DriftStatus.OPEN)
    )).scalars().all()
    assert len(rows) == 0
```

- [ ] **Step 2: 실패 확인**

Run:
```bash
cd ~/Documents/ardensdevspace/pslog/backend && source venv/bin/activate && python -m pytest tests/test_drift_detection_b.py -q
```
Expected: FAIL (`detect_status_contradictions` 미존재).

- [ ] **Step 3: `drift_service.detect_status_contradictions` 구현**

`app/services/drift_service.py` 끝에 추가 (필요 import는 함수 내부 지연 import로):
```python
async def detect_status_contradictions(
    db: AsyncSession,
    *,
    project_id: uuid.UUID,
    branch: str,
    commit_sha: str | None,
) -> list[Drift]:
    """해당 branch 최신 Handoff.parsed_tasks vs Task.status 모순 → Drift(B) reconcile.

    inner-join on external_id. handoff.checked != (task.status == DONE) → 모순.
    서브 체크박스는 parsed_tasks에 애초에 안 들어있음(파서가 들여쓰기 0만 담음).
    """
    from app.models.handoff import Handoff
    from app.models.task import Task, TaskSource, TaskStatus

    handoff = (await db.execute(
        select(Handoff).where(
            Handoff.project_id == project_id, Handoff.branch == branch,
        ).order_by(Handoff.pushed_at.desc().nullslast(), Handoff.id.desc())
    )).scalars().first()
    if handoff is None:
        return []

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
        type_=DriftType.STATUS_CONTRADICTION, current=items,
    )
```

⚠️ 주의: `reconcile`은 (project, type) 전체를 reconcile하므로, 위 호출은 **해당 branch의 모순만** current에 담는다. 다른 branch의 OPEN(B)를 잘못 RESOLVED하지 않도록, `reconcile`에 `branch` 스코프가 필요하면 dedup_key prefix로 거른다 — 여기서는 단순화를 위해 **B reconcile을 branch 단위로 제한**한다. Step 3a 참조.

- [ ] **Step 3a: `reconcile`에 branch 스코프 옵션 추가**

`reconcile` 시그니처에 `branch: str | None = None` 추가하고, `existing` 쿼리에 조건 추가:
```python
async def reconcile(
    db: AsyncSession,
    *,
    project_id: uuid.UUID,
    type_: DriftType,
    current: list[DriftItem],
    branch: str | None = None,
) -> list[Drift]:
    ...
    stmt = select(Drift).where(Drift.project_id == project_id, Drift.type == type_)
    if branch is not None:
        stmt = stmt.where(Drift.branch == branch)
    existing = (await db.execute(stmt)).scalars().all()
    ...
```
그리고 `detect_status_contradictions`의 `reconcile(...)` 호출에 `branch=branch` 추가. Task 5의 테스트는 `branch` 미지정(전체)이라 그대로 통과.

- [ ] **Step 4: `sync_service._process_inner`에서 B 호출**

`app/services/sync_service.py` 의 `_process_inner` 끝(`return plan_changes, handoff_present, plan_changed` 직전)에 추가:
```python
    # 결정-진실 루프 B: handoff↔PLAN 상태 모순 감지 (저장된 Handoff.parsed_tasks 사용).
    # PLAN 또는 handoff 변경 시 항상 재평가.
    from app.services import drift_service
    try:
        await drift_service.detect_status_contradictions(
            db, project_id=project.id, branch=event.branch,
            commit_sha=event.head_commit_sha,
        )
    except Exception:
        logger.exception("drift(B) detection failed for event %s", event.id)
```
(sync는 outer commit 구조이므로 flush만; commit은 process_event가 함.)

- [ ] **Step 5: 통과 + 회귀 확인**

Run:
```bash
cd ~/Documents/ardensdevspace/pslog/backend && source venv/bin/activate && python -m pytest tests/test_drift_detection_b.py tests/test_drift_service.py -q && python -m pytest tests/ -q -k "sync or handoff or plan" 2>&1 | tail -5
```
Expected: drift 테스트 PASS + sync/handoff 회귀 PASS.

- [ ] **Step 6: 커밋**

```bash
git add app/services/drift_service.py app/services/sync_service.py tests/test_drift_detection_b.py && git commit -m "feat(drift): B 상태모순 감지 (저장된 Handoff.parsed_tasks 활용) + sync wiring"
```

---

## Task 7: `GitHubPullRequestPayload` 스키마

**Files:**
- Modify: `app/schemas/webhook.py`
- Test: `tests/test_pr_payload_schema.py`

- [ ] **Step 1: 실패 테스트**

`tests/test_pr_payload_schema.py`:
```python
from app.schemas.webhook import GitHubPullRequestPayload

RAW = """
{"action":"opened",
 "repository":{"html_url":"https://github.com/o/r"},
 "pull_request":{"number":12,
   "head":{"ref":"feat/x","sha":"%s"},
   "base":{"ref":"main","sha":"%s"}}}
""" % ("a"*40, "b"*40)


def test_pr_payload_parses():
    p = GitHubPullRequestPayload.model_validate_json(RAW)
    assert p.action == "opened"
    assert p.repository.html_url == "https://github.com/o/r"
    assert p.pull_request.head.ref == "feat/x"
    assert p.pull_request.head.sha == "a"*40
    assert p.pull_request.base.sha == "b"*40
```

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest tests/test_pr_payload_schema.py -q`
Expected: FAIL (ImportError).

- [ ] **Step 3: 스키마 구현**

`app/schemas/webhook.py` 에 추가 (기존 `Repository`/패턴 재사용 — 파일 안 `repository` 모델명 확인 후 일치시킬 것. 없으면 아래대로):
```python
from pydantic import BaseModel


class _PrRef(BaseModel):
    ref: str
    sha: str


class _PrBody(BaseModel):
    number: int
    head: _PrRef
    base: _PrRef


class _PrRepo(BaseModel):
    html_url: str


class GitHubPullRequestPayload(BaseModel):
    action: str
    repository: _PrRepo
    pull_request: _PrBody
```
(이미 `Repository` 모델이 있으면 `_PrRepo` 대신 그걸 재사용.)

- [ ] **Step 4: 통과 확인 + 커밋**

Run: `python -m pytest tests/test_pr_payload_schema.py -q`
Expected: PASS.
```bash
git add app/schemas/webhook.py tests/test_pr_payload_schema.py && git commit -m "feat(schema): GitHubPullRequestPayload"
```

---

## Task 8: 감지 A (결정 미승격) — drift_service

handoff `### 결정` 항목 중 `promoted=False`가 있거나(미마킹), 전부 마킹됐는데 PR diff에 DECISIONS.md가 없으면 Drift(A). fetcher는 주입(테스트 용이).

**Files:**
- Modify: `app/services/drift_service.py`
- Test: `tests/test_drift_detection_a.py`

- [ ] **Step 1: 실패 테스트**

`tests/test_drift_detection_a.py`:
```python
import uuid
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.drift import Drift, DriftStatus, DriftType
from app.models.project import Project
from app.models.workspace import Workspace
from app.services import drift_service

HANDOFF_UNPROMOTED = """# Handoff: feat/x — @alice

## 2026-06-14
- [x] task-001

### 결정
- [task-002] 캐시 TTL 5→15분 — 부하 감소
"""

HANDOFF_PROMOTED = """# Handoff: feat/x — @alice

## 2026-06-14
- [x] task-001

### 결정
- [task-002] 캐시 TTL 5→15분 — 부하 감소 → DECISIONS
"""


async def _seed(db):
    ws = Workspace(name="ws", slug=f"ws-{uuid.uuid4().hex[:8]}")
    db.add(ws); await db.flush()
    proj = Project(workspace_id=ws.id, name="p",
                   git_repo_url="https://github.com/o/r")
    db.add(proj); await db.commit(); await db.refresh(proj)
    return proj


async def test_detect_a_unpromoted_opens_drift(async_session: AsyncSession):
    proj = await _seed(async_session)

    async def fake_fetch_file(url, pat, sha, path): return HANDOFF_UNPROMOTED
    async def fake_fetch_compare(url, pat, base, head): return ["backend/x.py"]

    await drift_service.detect_unpromoted_decisions(
        async_session, project=proj, branch="feat/x",
        head_sha="a"*40, base_sha="b"*40,
        fetch_file=fake_fetch_file, fetch_compare=fake_fetch_compare,
    )
    await async_session.commit()
    rows = (await async_session.execute(
        select(Drift).where(Drift.project_id == proj.id,
                            Drift.type == DriftType.DECISION_NOT_PROMOTED,
                            Drift.status == DriftStatus.OPEN)
    )).scalars().all()
    assert len(rows) == 1
    assert "task-002" in rows[0].detail


async def test_detect_a_promoted_and_decisions_changed_no_drift(async_session: AsyncSession):
    proj = await _seed(async_session)

    async def fake_fetch_file(url, pat, sha, path): return HANDOFF_PROMOTED
    async def fake_fetch_compare(url, pat, base, head): return ["DECISIONS.md"]

    await drift_service.detect_unpromoted_decisions(
        async_session, project=proj, branch="feat/x",
        head_sha="a"*40, base_sha="b"*40,
        fetch_file=fake_fetch_file, fetch_compare=fake_fetch_compare,
    )
    await async_session.commit()
    rows = (await async_session.execute(
        select(Drift).where(Drift.project_id == proj.id,
                            Drift.type == DriftType.DECISION_NOT_PROMOTED,
                            Drift.status == DriftStatus.OPEN)
    )).scalars().all()
    assert len(rows) == 0
```

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest tests/test_drift_detection_a.py -q`
Expected: FAIL (`detect_unpromoted_decisions` 미존재).

- [ ] **Step 3: 구현**

`app/services/drift_service.py` 에 추가:
```python
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
    """브랜치 handoff의 `### 결정` 미승격 감지 → Drift(A) reconcile.

    조건: (1) promoted=False 항목 존재  OR
          (2) 항목은 있고 전부 promoted=True인데 PR diff에 DECISIONS.md 변경 없음.
    """
    from app.services.handoff_parser_service import MalformedHandoffError, parse_handoff
    from app.services.sync_service import _decrypt_pat, _handoff_file_path

    if project.git_repo_url is None:
        return []
    pat = _decrypt_pat(project)
    handoff_path = _handoff_file_path(project, branch)
    text = await fetch_file(project.git_repo_url, pat, head_sha, handoff_path)
    if text is None:
        # handoff 없음 → A 평가 불가, 기존 OPEN(A) 정리
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
```

- [ ] **Step 4: 통과 확인 + 커밋**

Run: `python -m pytest tests/test_drift_detection_a.py -q`
Expected: 2 PASS.
```bash
git add app/services/drift_service.py tests/test_drift_detection_a.py && git commit -m "feat(drift): A 결정 미승격 감지 (handoff ### 결정 + DECISIONS.md diff)"
```

---

## Task 9: `pull_request` 웹훅 분기 → A 평가

**Files:**
- Modify: `app/api/v1/endpoints/webhooks.py`
- Test: `tests/test_pr_webhook_endpoint.py`

- [ ] **Step 1: 실패 테스트 (서명 검증 포함 통합)**

`tests/test_pr_webhook_endpoint.py` — 기존 `tests/test_log_ingest_endpoint.py` / webhook 테스트의 app client + 서명 생성 패턴을 참고해 작성. 핵심 검증: `X-GitHub-Event: pull_request` + `action=opened` 페이로드 POST → 200, 그리고 A 평가가 호출되어(미승격 handoff fixture) Drift(A) 1건 OPEN.

(작성 시 기존 webhook 테스트에서 `verify_signature`용 secret 세팅 + `httpx.AsyncClient`/`TestClient` 픽스처 이름을 그대로 차용. 서명: `hmac.new(secret, body, sha256)`.)

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest tests/test_pr_webhook_endpoint.py -q`
Expected: FAIL.

- [ ] **Step 3: 엔드포인트 분기 구현**

`app/api/v1/endpoints/webhooks.py` 수정:

(a) `receive_github_push` 의 이벤트 가드를 확장 — `push`는 기존대로, `pull_request`는 새 처리:
```python
    if x_github_event == "pull_request":
        return await _handle_pull_request(request, background_tasks, db, body,
                                          x_hub_signature_256)
    if x_github_event != "push":
        return {"status": "ignored", "event": x_github_event}
```
(서명 검증은 push와 동일 로직을 재사용해야 하므로, `_handle_pull_request` 내부에서 repo 매칭→secret decrypt→verify_signature를 push와 동일하게 수행.)

(b) 파일 하단에 추가:
```python
from app.schemas.webhook import GitHubPullRequestPayload
from app.services import drift_service


async def _handle_pull_request(request, background_tasks, db, body, signature):
    try:
        payload = GitHubPullRequestPayload.model_validate_json(body)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid PR payload")
    if payload.action not in {"opened", "reopened", "synchronize", "ready_for_review"}:
        return {"status": "ignored_action", "action": payload.action}

    project = await find_project_by_repo_url(db, payload.repository.html_url)
    if project is None:
        return {"status": "unknown_repo"}
    if project.webhook_secret_encrypted is None:
        raise HTTPException(status_code=401, detail="Webhook secret not configured")
    try:
        secret = decrypt_secret(project.webhook_secret_encrypted)
    except InvalidToken:
        raise HTTPException(status_code=500, detail="Secret decryption failed")
    if not verify_signature(body, signature, secret):
        raise HTTPException(status_code=401, detail="Invalid signature")

    pr = payload.pull_request
    project_id = project.id
    head_sha, base_sha, branch = pr.head.sha, pr.base.sha, pr.head.ref
    background_tasks.add_task(_run_drift_a, project_id, branch, head_sha, base_sha)
    return {"status": "pr_received", "number": pr.number}


async def _run_drift_a(project_id, branch, head_sha, base_sha):
    from app.models.project import Project
    try:
        async with AsyncSessionLocal() as db:
            project = await db.get(Project, project_id)
            if project is None:
                return
            newly = await drift_service.detect_unpromoted_decisions(
                db, project=project, branch=branch,
                head_sha=head_sha, base_sha=base_sha,
                fetch_file=fetch_file, fetch_compare=fetch_compare_files,
            )
            await db.commit()
            # 신규 OPEN 알림은 Task 11에서 dispatcher 연결
            for d in newly:
                logger.info("drift(A) opened: project=%s branch=%s", project_id, branch)
    except Exception:
        logger.exception("drift(A) detection failed: project=%s branch=%s", project_id, branch)
```

- [ ] **Step 4: GitHub 웹훅 이벤트 구독에 pull_request 추가**

웹훅 등록 코드(`git_settings` 엔드포인트 또는 GitHub API로 hook 생성하는 곳)에서 `"events": ["push"]` 를 `["push", "pull_request"]` 로 확장. 등록 지점:
```bash
cd ~/Documents/ardensdevspace/pslog/backend && grep -rn '"push"' app/ | grep -i "event"
```
찾은 곳에서 `pull_request` 추가. (기존 등록된 hook은 GitHub UI/재등록으로 갱신 필요 — README/배포 노트에 1줄.)

- [ ] **Step 5: 통과 + 회귀 확인**

Run:
```bash
python -m pytest tests/test_pr_webhook_endpoint.py tests/test_github_webhook_service.py -q
```
Expected: PASS.

- [ ] **Step 6: 커밋**

```bash
git add app/api/v1/endpoints/webhooks.py tests/test_pr_webhook_endpoint.py && git commit -m "feat(webhook): pull_request 이벤트 → 결정 미승격(A) 평가"
```

---

## Task 10: API — `GET/PATCH /projects/{id}/drifts`

`log_errors` 엔드포인트 패턴 차용: 목록(status 필터) + PATCH(ignore/reopen).

**Files:**
- Create: `app/schemas/drift.py`, `app/api/v1/endpoints/drifts.py`
- Modify: 라우터 등록 지점 (`app/api/v1/endpoints/log_errors.py`가 등록된 곳과 동일 파일)
- Test: `tests/test_drifts_endpoint.py`

- [ ] **Step 1: 라우터 등록 지점 확인**

Run:
```bash
cd ~/Documents/ardensdevspace/pslog/backend && grep -rn "log_errors\|errors" app/api/v1/ | grep -i "router\|include" | head
```
→ `log_errors` 라우터가 include되는 파일/패턴 확인 후 drifts도 동일하게 등록.

- [ ] **Step 2: 스키마 `app/schemas/drift.py`**

```python
import uuid
from datetime import datetime

from pydantic import BaseModel

from app.models.drift import DriftStatus, DriftType


class DriftOut(BaseModel):
    id: uuid.UUID
    type: DriftType
    status: DriftStatus
    branch: str
    external_id: str | None
    detail: str
    opened_at: datetime
    resolved_at: datetime | None

    model_config = {"from_attributes": True}


class DriftListOut(BaseModel):
    items: list[DriftOut]
    total: int


class DriftPatchIn(BaseModel):
    action: str   # "ignore" | "reopen"
```

- [ ] **Step 3: 실패 테스트 `tests/test_drifts_endpoint.py`**

`test_log_errors_endpoint.py`의 client/auth 픽스처 패턴을 그대로 차용해: 드리프트 1건 seed → `GET /api/v1/projects/{id}/drifts?status=open` 200 + 1건; `PATCH .../drifts/{drift_id}` body `{"action":"ignore"}` → status IGNORED. 권한은 프로젝트 멤버 기준(log_errors와 동일 deps).

- [ ] **Step 4: 실패 확인**

Run: `python -m pytest tests/test_drifts_endpoint.py -q` → FAIL.

- [ ] **Step 5: 엔드포인트 `app/api/v1/endpoints/drifts.py`**

```python
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.deps import get_db  # log_errors와 동일 deps 경로 확인 후 맞출 것
from app.models.drift import Drift, DriftStatus
from app.schemas.drift import DriftListOut, DriftOut, DriftPatchIn
# 권한 의존성은 log_errors.py에서 쓰는 것과 동일하게 import (예: require_project_member)

router = APIRouter(prefix="/projects/{project_id}/drifts", tags=["drifts"])


@router.get("", response_model=DriftListOut)
async def list_drifts(
    project_id: uuid.UUID,
    status: DriftStatus | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    # _member = Depends(require_project_member),  # log_errors 패턴대로
):
    stmt = select(Drift).where(Drift.project_id == project_id)
    if status is not None:
        stmt = stmt.where(Drift.status == status)
    stmt = stmt.order_by(Drift.opened_at.desc())
    rows = (await db.execute(stmt)).scalars().all()
    total = (await db.execute(
        select(func.count()).select_from(stmt.subquery())
    )).scalar_one()
    return DriftListOut(items=[DriftOut.model_validate(r) for r in rows], total=total)


@router.patch("/{drift_id}", response_model=DriftOut)
async def patch_drift(
    project_id: uuid.UUID,
    drift_id: uuid.UUID,
    body: DriftPatchIn,
    db: AsyncSession = Depends(get_db),
    # _member = Depends(require_project_member),
):
    drift = await db.get(Drift, drift_id)
    if drift is None or drift.project_id != project_id:
        raise HTTPException(status_code=404, detail="Drift not found")
    if body.action == "ignore":
        drift.status = DriftStatus.IGNORED
    elif body.action == "reopen":
        drift.status = DriftStatus.OPEN
        drift.resolved_at = None
    else:
        raise HTTPException(status_code=400, detail="Unknown action")
    await db.commit()
    await db.refresh(drift)
    return DriftOut.model_validate(drift)
```
⚠️ `get_db`/권한 의존성의 정확한 import 경로는 `app/api/v1/endpoints/log_errors.py` 상단을 그대로 따른다(Step 1에서 확인).

- [ ] **Step 6: 라우터 등록**

log_errors 라우터를 include하는 곳에 동일 방식으로 `from app.api.v1.endpoints import drifts` + `api_router.include_router(drifts.router)` 추가.

- [ ] **Step 7: 통과 확인 + 커밋**

Run: `python -m pytest tests/test_drifts_endpoint.py -q` → PASS.
```bash
git add app/schemas/drift.py app/api/v1/endpoints/drifts.py app/api/v1/ tests/test_drifts_endpoint.py && git commit -m "feat(api): GET/PATCH /projects/{id}/drifts"
```

---

## Task 11: Discord 알림 — 신규 드리프트

`notification_dispatcher.dispatch_discord_alert`(기존)로 신규 OPEN 드리프트를 알린다. B는 sync 경로(`process_event` 성공 후), A는 `_run_drift_a`에서.

**Files:**
- Modify: `app/services/sync_service.py` (B 신규 OPEN 알림), `app/api/v1/endpoints/webhooks.py` (A 알림)
- Test: `tests/test_drift_alert.py` (dispatcher mock)

- [ ] **Step 1: 알림 포맷 헬퍼 추가 (drift_service)**

`drift_service.py`에:
```python
def format_drift_alert(newly_opened: list[Drift]) -> str | None:
    if not newly_opened:
        return None
    lines = ["⚠️ **pslog 드리프트 감지**"]
    for d in newly_opened:
        lines.append(f"• [{d.type.value}] {d.branch} — {d.detail}")
    return "\n".join(lines)
```

- [ ] **Step 2: B 알림 — process_event 성공 path**

`sync_service.process_event`의 성공 분기에서 `_process_inner`가 B 신규 OPEN을 반환하도록 연결하거나(반환값 확장), 간단히 `_process_inner` 내 B 호출 결과를 모아 `process_event`가 dispatch. 최소 변경: `_process_inner`가 B newly_opened를 4번째 반환값으로 넘기고, `process_event`가 push summary와 함께 또는 별도로 `dispatch_discord_alert(db, project, format_drift_alert(newly))` 호출(content None이면 skip). 기존 dispatcher가 URL None/disabled를 swallow하므로 안전.

- [ ] **Step 3: A 알림 — `_run_drift_a`**

`webhooks._run_drift_a`의 `for d in newly:` 로깅 자리를:
```python
            content = drift_service.format_drift_alert(newly)
            if content:
                from app.services import notification_dispatcher
                try:
                    await notification_dispatcher.dispatch_discord_alert(db, project, content)
                except Exception:
                    logger.exception("drift(A) alert dispatch failed")
```

- [ ] **Step 4: 테스트 (dispatcher monkeypatch)**

`tests/test_drift_alert.py`: `format_drift_alert([])` → None; 1건 이상 → 문자열에 branch/detail 포함. (dispatch 통합은 기존 `test_notification_dispatcher.py` 패턴 참고, 선택.)

- [ ] **Step 5: 통과 + 커밋**

Run: `python -m pytest tests/test_drift_alert.py -q` → PASS.
```bash
git add app/services/drift_service.py app/services/sync_service.py app/api/v1/endpoints/webhooks.py tests/test_drift_alert.py && git commit -m "feat(drift): 신규 드리프트 Discord 알림"
```

---

## Task 12: 전체 회귀 + 최종 점검

- [ ] **Step 1: 전체 테스트**

Run:
```bash
cd ~/Documents/ardensdevspace/pslog/backend && source venv/bin/activate && python -m pytest -q 2>&1 | tail -8
```
Expected: 전부 PASS (Task 0 baseline 대비 신규만 추가).

- [ ] **Step 2: 마이그레이션 최신 + 앱 부팅 스모크**

Run:
```bash
alembic upgrade head && python -c "from app.main import app; print('app import OK')"
```
Expected: `app import OK`.

- [ ] **Step 3: 사용자 보고 + push/PR 확인**

⚠️ push/PR은 사용자 승인 후. 변경 요약(파일/커밋 수, 신규 테스트 수) 보고하고 push 여부 확인.

---

## 자기 검토 (작성자 메모)

- **spec §5 커버리지**: §5.1 파서→T2; §5.1 decisions_path→T3; §5.2 Drift 모델→T3; §5.3 A→T8, B→T6; §5.4 라이프사이클→T5(reconcile)+T10(ignore); §5.5 API→T10, Discord→T11. 프론트(§5.5 패널)=Phase 2b.
- **§7 미결 반영**: Q1 PR 웹훅→T7·T9; Q2 마커+파일변경→T8; Q3 inner-join+서브제외→T6; Q4 한국어/i18n없음→detail 문자열 한국어.
- **개선점(spec 대비)**: B가 재fetch 대신 **저장된 Handoff.parsed_tasks** 사용 — finding #2("데이터 갖고도 안 봄")를 직접 실현. spec §5.3은 일반 서술이라 모순 없음.
- **타입 일관성**: `DriftItem`/`reconcile`/`DriftType`/`DriftStatus`/`detect_status_contradictions`/`detect_unpromoted_decisions`/`format_drift_alert` 명칭 T5~T11 일관.
- **미확정(실행 중 확인 필요, placeholder 아님)**: 라우터 include 지점·권한 deps·webhook 테스트 client 픽스처·hook 이벤트 등록 위치는 "기존 X 파일과 동일하게"로 명시 + 확인 Step 포함. 코드베이스 관례 따르는 의도적 위임.

## Phase 2b (다음 plan) — 프론트 드리프트 패널

`errors` UI를 미러: `ViewMode`에 `'drift'` 추가(`src/pages/DashboardPage.tsx`), `DriftsList.tsx`(status 필터 + 목록), `useDrifts`/`useTransitionDrift` 훅(`src/hooks/`), `api.drifts.{list,transition}`(`src/services/api.ts`), `src/types/drift.ts`. 기존 `components/errors/ErrorsList.tsx`·`hooks/useErrorGroups.ts`·`types/error.ts`가 1:1 템플릿. 백엔드 API(T10)에 그대로 붙음.
