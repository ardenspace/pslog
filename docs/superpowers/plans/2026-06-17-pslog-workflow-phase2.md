# pslog-workflow Phase 2 (준비도 추적) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. 각 코드 task는 superpowers:test-driven-development 로 (실패 테스트 → 구현 → 통과). Steps use checkbox (`- [ ]`).

**Goal:** pslog 가 `(deep)` 무게에 맞는 준비 산출물(`docs/tasks/task-NNN/`)이 없는 채 브랜치에 코드가 들어오면 `Drift(TASK_NOT_PREPARED)` 로 감지·시각화·알림한다 — 기존 드리프트 인프라(모델/서비스/API/대시보드/Discord) 재사용.

**Architecture:** `sync_service._process_inner` 의 드리프트 평가 단계(이미 A/B 평가)에 C 평가를 추가한다. C 는 **현재 push 된 브랜치 1개**에 한정: 브랜치명 `feat/task-NNN-*` → `task-NNN` 매핑 → PLAN.md 에서 그 task 의 `(deep)` 무게 판정 → 무게별 필수 파일(deep: `spec.md`+`plan.md` / light: `brief.md`)을 `{project.tasks_dir}/task-NNN/` 에서 `fetch_file` 로 확인 → 코드가 실제로 들어왔고(=tasks_dir 밖 파일 변경 존재) 필수 파일이 없으면 `DriftItem` → 기존 `reconcile()` 로 OPEN/자동 RESOLVED/IGNORED. 새 모델 없음, 새 타입 1개.

**Tech Stack:** FastAPI, SQLAlchemy 2.0 async, Alembic, Pydantic v2, pytest(+postgres testcontainer), React/TS(드리프트 패널).

설계 근거: `docs/superpowers/specs/2026-06-17-pslog-workflow-design.md` §7(추적), §9.1–9.2(판정 강도 v1 권장).

### v1 판정 규칙 (스펙 §9 확정)
- **코드 들어옴**(§9.1): 브랜치명이 `feat/task-NNN-*` 이고, `before_sha..head_sha` 변경 파일 중 **`tasks_dir` 밖 파일이 1개 이상**. `before_sha` 없으면(최초 push 등) 오탐 방지로 C 평가 skip.
- **산출물 "있음"**(§9.2): `fetch_file` 결과가 `None` 이 아니고 공백만은 아님(`.strip() != ""`).
- **무게**: PLAN.md 의 해당 task 줄에 `(deep)` → spec.md+plan.md 필수 / 없으면 brief.md 필수.

---

## File Structure

```
backend/app/
├── models/drift.py                 (M) DriftType 에 TASK_NOT_PREPARED 추가
├── models/project.py               (M) tasks_dir 컬럼 + __init__ 기본값
├── schemas/parsed_plan.py          (M) ParsedTask.deep: bool 필드
├── services/plan_parser_service.py (M) (deep) 마커 파싱 → ParsedTask.deep
├── services/drift_service.py       (M) detect_task_not_prepared() 추가 + TYPE_HINT(선택)
├── services/sync_service.py        (M) _process_inner 에 C 평가 호출
└── alembic/versions/XXXX_*.py      (C) drifttype enum 값 + projects.tasks_dir
backend/tests/
└── test_drift_detection_c.py       (C) C 감지 테스트
frontend/src/
├── types/drift.ts                  (M) DriftType union 에 'task_not_prepared'
└── components/drifts/DriftsList.tsx(M) TYPE_LABEL 에 라벨 추가
```

기존 패턴을 반드시 먼저 읽고 맞춘다: `drift_service.py` 의 `detect_unpromoted_decisions`(A)/`detect_status_contradictions`(B) — PAT 복호화·`fetch_file` 사용·반환 형태를 그대로 따른다.

---

## Task 1: 모델/스키마/파서 — 새 타입 + tasks_dir + (deep) 파싱

**Files:**
- Modify: `backend/app/models/drift.py:11-13`
- Modify: `backend/app/models/project.py` (paths 컬럼 옆 + `__init__`)
- Modify: `backend/app/schemas/parsed_plan.py` (`ParsedTask`)
- Modify: `backend/app/services/plan_parser_service.py` (task rest 파싱)

- [ ] **Step 1: DriftType 에 타입 추가** — `backend/app/models/drift.py`

```python
class DriftType(str, enum.Enum):
    DECISION_NOT_PROMOTED = "decision_not_promoted"   # A
    STATUS_CONTRADICTION = "status_contradiction"     # B
    TASK_NOT_PREPARED = "task_not_prepared"           # C
```

- [ ] **Step 2: Project.tasks_dir 추가** — `backend/app/models/project.py` (기존 `handoff_dir` 줄 바로 아래, 동일 스타일)

```python
tasks_dir: Mapped[str] = mapped_column(default="docs/tasks/", server_default="docs/tasks/")
```
그리고 `__init__` 에 기존 `setdefault` 들과 같은 자리에:
```python
kwargs.setdefault("tasks_dir", "docs/tasks/")
```

- [ ] **Step 3: ParsedTask 에 deep 필드** — `backend/app/schemas/parsed_plan.py` (`ParsedTask` 에 추가, 기본 False)

```python
deep: bool = False
```

- [ ] **Step 4: 파서가 (deep) 마커 인식** — `backend/app/services/plan_parser_service.py` 의 task 줄 파싱부에서 `rest`(task 제목/메타) 에 `(deep)` 가 있으면 deep=True. `ParsedTask(...)` 생성 시 `deep=` 전달.

```python
deep = bool(re.search(r"\(deep\)", m.group("rest")))
# ... ParsedTask(external_id=..., title=..., checked=..., assignee=..., paths=..., deep=deep)
```
(title 에서 `(deep)` 를 굳이 제거할 필요 없음 — 표시용이라 무해. 제거하려면 `title.replace("(deep)", "").strip()`.)

- [ ] **Step 5: 파서 단위 테스트** — 기존 plan 파서 테스트 파일에 케이스 추가(없으면 `backend/tests/test_plan_parser_service.py` 의 패턴 사용).

```python
def test_parse_plan_detects_deep_marker():
    text = "## 태스크\n- [ ] [task-007] (deep) 결제 재시도 — @me — `x.py`\n- [ ] [task-008] 오타 — @me\n"
    parsed = parse_plan(text)
    by_id = {t.external_id: t for t in parsed.tasks}
    assert by_id["task-007"].deep is True
    assert by_id["task-008"].deep is False
```

- [ ] **Step 6: 파서 테스트 실행**

Run: `cd backend && python -m pytest tests/test_plan_parser_service.py -q`
Expected: PASS (deep 케이스 포함). (DB 불필요한 순수 파서 테스트.)

- [ ] **Step 7: Commit**

```bash
git add backend/app/models/drift.py backend/app/models/project.py backend/app/schemas/parsed_plan.py backend/app/services/plan_parser_service.py backend/tests/test_plan_parser_service.py
git commit -m "feat(drift): TASK_NOT_PREPARED 타입 + Project.tasks_dir + PLAN (deep) 파싱"
```

---

## Task 2: Alembic 마이그레이션 — enum 값 + tasks_dir 컬럼

**Files:**
- Create: `backend/alembic/versions/<rev>_add_task_not_prepared_and_tasks_dir.py`

- [ ] **Step 1: 기존 enum 생성 방식 확인** — `backend/alembic/versions/b9c0409cfb53_*.py` 를 열어 `drifttype` enum 이 DB 에 **어떤 리터럴로** 만들어졌는지 본다(SQLAlchemy `Enum(DriftType)` 는 기본적으로 **이름**=대문자 `DECISION_NOT_PROMOTED` 를 저장). 새 값은 **그 케이싱과 동일하게** 추가한다.

- [ ] **Step 2: 마이그레이션 생성** — 자동생성 대신 수기로 작성(enum ADD VALUE 는 autogen 이 못 잡음).

```bash
cd backend && alembic revision -m "add task_not_prepared and tasks_dir"
```
생성된 파일의 `down_revision` 이 현재 head(`b9c0409cfb53`)인지 확인. `upgrade()`:
```python
def upgrade() -> None:
    # 1) drifttype enum 에 값 추가 (PostgreSQL). Step 1 에서 확인한 케이싱과 일치시킬 것.
    op.execute("ALTER TYPE drifttype ADD VALUE IF NOT EXISTS 'TASK_NOT_PREPARED'")
    # 2) projects.tasks_dir
    op.add_column(
        "projects",
        sa.Column("tasks_dir", sa.String(), nullable=False, server_default="docs/tasks/"),
    )

def downgrade() -> None:
    op.drop_column("projects", "tasks_dir")
    # PostgreSQL enum 값 제거는 비파괴적으로 어려움 — 값은 그대로 둔다(관용).
```
주의: `ALTER TYPE ... ADD VALUE` 는 일부 PG 버전에서 트랜잭션 안에서 실패할 수 있다. 그럴 경우 마이그레이션 상단에서 `op.execute("COMMIT")` 후 실행하거나, Alembic 의 `with op.get_context().autocommit_block():` 로 감싼다. (실행 중 에러나면 이 방식으로 전환.)

- [ ] **Step 3: 업/다운 검증**

Run: `cd backend && alembic upgrade head && alembic downgrade -1 && alembic upgrade head`
Expected: 3개 명령 모두 에러 없이 완료. (로컬 postgres 필요 — `make db-up` 으로 5433 띄워둘 것.)

- [ ] **Step 4: Commit**

```bash
git add backend/alembic/versions/
git commit -m "feat(drift): 마이그레이션 — drifttype TASK_NOT_PREPARED + projects.tasks_dir"
```

---

## Task 3: detect_task_not_prepared (TDD) — 핵심 감지 로직

**Files:**
- Modify: `backend/app/services/drift_service.py` (새 함수 + 헬퍼)
- Create: `backend/tests/test_drift_detection_c.py`

먼저 `drift_service.py` 의 `detect_status_contradictions`/`detect_unpromoted_decisions` 를 읽어 **PAT 복호화 헬퍼명·`fetch_file` 호출 형태·반환(`reconcile` 호출)** 을 확인하고 동일하게 쓴다. 아래 코드의 PAT 복호화/`reconcile` 호출은 그 패턴에 맞춰 조정한다.

- [ ] **Step 1: 실패 테스트 작성** — `backend/tests/test_drift_detection_c.py` (fixture 는 `test_drift_detection_b.py` 의 `async_session`/시드 헬퍼 패턴 그대로)

```python
import pytest
from sqlalchemy import select
from app.models.drift import Drift, DriftType, DriftStatus
from app.services import drift_service

PLAN_DEEP = "## 태스크\n- [ ] [task-007] (deep) 결제 재시도 — @me — `x.py`\n"
PLAN_LIGHT = "## 태스크\n- [ ] [task-008] 오타 — @me\n"

def _ff(files: dict[str, str]):
    async def fetch_file(url, pat, sha, path, *, timeout=30.0):
        return files.get(path)
    return fetch_file

def _fc(changed: list[str]):
    async def fetch_compare(url, pat, base, head, *, timeout=30.0):
        return changed
    return fetch_compare

async def _open_c(db, project):
    rows = (await db.execute(
        select(Drift).where(Drift.project_id == project.id, Drift.type == DriftType.TASK_NOT_PREPARED)
    )).scalars().all()
    return [r for r in rows if r.status == DriftStatus.OPEN]

@pytest.mark.asyncio
async def test_deep_missing_plan_opens_drift(async_session):
    proj = await _seed_project(async_session)  # git_repo_url 세팅된 프로젝트 (B 테스트 헬퍼 재사용)
    files = {proj.plan_path: PLAN_DEEP, "docs/tasks/task-007/spec.md": "# spec"}  # plan.md 없음
    newly = await drift_service.detect_task_not_prepared(
        async_session, project=proj, branch="feat/task-007-pay",
        head_sha="a"*40, base_sha="b"*40,
        fetch_file=_ff(files), fetch_compare=_fc(["backend/x.py"]),
    )
    await async_session.commit()
    assert len(newly) == 1
    assert "task-007" in newly[0].detail
    assert len(await _open_c(async_session, proj)) == 1

@pytest.mark.asyncio
async def test_deep_both_present_no_drift(async_session):
    proj = await _seed_project(async_session)
    files = {proj.plan_path: PLAN_DEEP, "docs/tasks/task-007/spec.md": "# s", "docs/tasks/task-007/plan.md": "# p"}
    newly = await drift_service.detect_task_not_prepared(
        async_session, project=proj, branch="feat/task-007-pay",
        head_sha="a"*40, base_sha="b"*40, fetch_file=_ff(files), fetch_compare=_fc(["backend/x.py"]),
    )
    await async_session.commit()
    assert newly == []
    assert await _open_c(async_session, proj) == []

@pytest.mark.asyncio
async def test_light_missing_brief_opens_drift(async_session):
    proj = await _seed_project(async_session)
    files = {proj.plan_path: PLAN_LIGHT}  # brief 없음
    newly = await drift_service.detect_task_not_prepared(
        async_session, project=proj, branch="feat/task-008-typo",
        head_sha="a"*40, base_sha="b"*40, fetch_file=_ff(files), fetch_compare=_fc(["README.md", "src/a.py"]),
    )
    await async_session.commit()
    assert len(newly) == 1

@pytest.mark.asyncio
async def test_no_code_change_no_drift(async_session):
    proj = await _seed_project(async_session)
    files = {proj.plan_path: PLAN_LIGHT}
    # 변경 파일이 tasks_dir 안에만 있음 → 코드 안 들어옴
    newly = await drift_service.detect_task_not_prepared(
        async_session, project=proj, branch="feat/task-008-typo",
        head_sha="a"*40, base_sha="b"*40, fetch_file=_ff(files), fetch_compare=_fc(["docs/tasks/task-008/brief.md"]),
    )
    await async_session.commit()
    assert newly == []

@pytest.mark.asyncio
async def test_non_task_branch_no_drift(async_session):
    proj = await _seed_project(async_session)
    files = {proj.plan_path: PLAN_LIGHT}
    newly = await drift_service.detect_task_not_prepared(
        async_session, project=proj, branch="chore/cleanup",
        head_sha="a"*40, base_sha="b"*40, fetch_file=_ff(files), fetch_compare=_fc(["src/a.py"]),
    )
    await async_session.commit()
    assert newly == []

@pytest.mark.asyncio
async def test_autoresolve_when_docs_added(async_session):
    proj = await _seed_project(async_session)
    # 1차: brief 없음 → OPEN
    await drift_service.detect_task_not_prepared(
        async_session, project=proj, branch="feat/task-008-typo", head_sha="a"*40, base_sha="b"*40,
        fetch_file=_ff({proj.plan_path: PLAN_LIGHT}), fetch_compare=_fc(["src/a.py"]),
    )
    await async_session.commit()
    assert len(await _open_c(async_session, proj)) == 1
    # 2차: brief 생김 → 자동 RESOLVED
    await drift_service.detect_task_not_prepared(
        async_session, project=proj, branch="feat/task-008-typo", head_sha="c"*40, base_sha="a"*40,
        fetch_file=_ff({proj.plan_path: PLAN_LIGHT, "docs/tasks/task-008/brief.md": "# brief"}),
        fetch_compare=_fc(["src/b.py"]),
    )
    await async_session.commit()
    assert await _open_c(async_session, proj) == []
```
(`_seed_project` 는 B 테스트의 시드 헬퍼를 재사용/복사 — `git_repo_url`, `plan_path="PLAN.md"`, `tasks_dir="docs/tasks/"` 포함.)

- [ ] **Step 2: 실패 확인**

Run: `cd backend && python -m pytest tests/test_drift_detection_c.py -q`
Expected: FAIL (`detect_task_not_prepared` 없음 → AttributeError/ImportError).

- [ ] **Step 3: 구현** — `backend/app/services/drift_service.py` 에 추가 (브랜치 매핑 헬퍼 + 감지 함수). PAT 복호화/`reconcile` 호출은 기존 detect 함수와 동일 패턴.

```python
import re
from app.services.plan_parser_service import parse_plan

_BRANCH_TASK_RE = re.compile(r"^feat/(task-[0-9]+)-")

def _branch_to_task(branch: str) -> str | None:
    m = _BRANCH_TASK_RE.match(branch or "")
    return m.group(1) if m else None

async def detect_task_not_prepared(
    db,
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
    external_id = _branch_to_task(branch)
    if external_id is None or project.git_repo_url is None or base_sha is None:
        # 평가 대상 아님(브랜치 형식/최초 push/저장소 미설정) → reconcile 안 함(기존 OPEN 보존)
        return []

    pat = _decrypt_pat(project)  # 기존 detect 함수와 동일 헬퍼 사용

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
```
주의: `reconcile` 의 `branch` 인자로 필터되어, 같은 브랜치의 C 드리프트만 대상으로 자동 RESOLVED 처리되어야 한다(코드 들어옴=false 또는 산출물 충족 시 `current=[]` → 기존 OPEN 자동 RESOLVED). `reconcile` 시그니처에 `branch` 필터가 실제로 그렇게 동작하는지 기존 구현으로 확인하고 맞출 것.

- [ ] **Step 4: 통과 확인**

Run: `cd backend && python -m pytest tests/test_drift_detection_c.py -q`
Expected: 6 passed. (postgres testcontainer 필요 — Docker 실행 상태.)

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/drift_service.py backend/tests/test_drift_detection_c.py
git commit -m "feat(drift): detect_task_not_prepared (브랜치 task 준비도 감지) + 테스트"
```

---

## Task 4: sync_service 통합 — C 평가 호출

**Files:**
- Modify: `backend/app/services/sync_service.py` (`_process_inner` 드리프트 평가부, B 평가 직후)

- [ ] **Step 1: B 평가 직후 C 평가 추가** — 기존 B 블록 아래에 (변수명/들여쓰기 실제 코드에 맞춤)

```python
# 결정-진실 루프 C: 브랜치 task 준비도 미달 감지
try:
    newly_c = await drift_service.detect_task_not_prepared(
        db, project=project, branch=event.branch,
        head_sha=event.head_commit_sha, base_sha=event.before_commit_sha or None,
        fetch_file=fetch_file, fetch_compare=fetch_compare,
    )
    c_alert = drift_service.format_drift_alert(newly_c)
    if c_alert:
        drift_alert = f"{drift_alert}\n\n{c_alert}" if drift_alert else c_alert
except Exception:
    logger.exception("drift(C) detection failed for event %s", event.id)
```
(`event.before_commit_sha` 의 실제 필드명을 `GitPushEvent` 모델에서 확인 — `before_commit_sha`/`before_sha` 등.)

- [ ] **Step 2: 통합 테스트(있으면)/임포트 sanity** — sync_service 가 임포트되고 기존 sync 테스트가 깨지지 않는지.

Run: `cd backend && python -m pytest tests/ -q -k "sync or drift"`
Expected: 기존 + 신규 통과.

- [ ] **Step 3: Commit**

```bash
git add backend/app/services/sync_service.py
git commit -m "feat(drift): sync_service _process_inner 에 C(준비도) 평가 통합"
```

---

## Task 5: Discord 라벨 (선택, 품질)

**Files:**
- Modify: `backend/app/services/drift_service.py` (`format_drift_alert`)

- [ ] **Step 1: 타입별 한글 라벨** — `format_drift_alert` 가 `d.type.value` 대신 라벨 매핑 사용.

```python
_TYPE_LABEL = {
    DriftType.DECISION_NOT_PROMOTED: "결정 미승격",
    DriftType.STATUS_CONTRADICTION: "상태 모순",
    DriftType.TASK_NOT_PREPARED: "태스크 미준비",
}
# lines.append(f"• [{_TYPE_LABEL.get(d.type, d.type.value)}] {d.branch} — {d.detail}")
```

- [ ] **Step 2: 기존 알림 테스트 통과 확인**

Run: `cd backend && python -m pytest tests/ -q -k "drift or notif"`
Expected: PASS (라벨 문자열 단언이 있으면 함께 갱신).

- [ ] **Step 3: Commit**

```bash
git add backend/app/services/drift_service.py
git commit -m "feat(drift): Discord 알림 타입 한글 라벨(태스크 미준비 포함)"
```

---

## Task 6: 프론트엔드 — 드리프트 패널 라벨

**Files:**
- Modify: `frontend/src/types/drift.ts`
- Modify: `frontend/src/components/drifts/DriftsList.tsx`

- [ ] **Step 1: 타입 union 확장** — `frontend/src/types/drift.ts`

```typescript
export type DriftType = 'decision_not_promoted' | 'status_contradiction' | 'task_not_prepared';
```

- [ ] **Step 2: 라벨 매핑** — `frontend/src/components/drifts/DriftsList.tsx` 의 `TYPE_LABEL`

```typescript
const TYPE_LABEL: Record<DriftType, string> = {
  decision_not_promoted: '결정 미승격',
  status_contradiction: '상태 모순',
  task_not_prepared: '태스크 미준비',
};
```

- [ ] **Step 3: 빌드/타입체크**

Run: `cd frontend && bun run build`
Expected: 타입 에러 없이 빌드 성공(`Record<DriftType,...>` 가 새 멤버 강제 → 빠지면 컴파일 에러로 잡힘).

- [ ] **Step 4: Commit**

```bash
git add frontend/src/types/drift.ts frontend/src/components/drifts/DriftsList.tsx
git commit -m "feat(frontend): 드리프트 패널에 '태스크 미준비'(task_not_prepared) 라벨"
```

---

## Task 7: 최종 검증

- [ ] **Step 1: 백엔드 전체 테스트**

Run: `cd backend && python -m pytest -q`
Expected: 전체 통과(신규 C 테스트 포함). (Docker postgres 필요.)

- [ ] **Step 2: 마이그레이션 왕복 재확인**

Run: `cd backend && alembic upgrade head && alembic downgrade -1 && alembic upgrade head`
Expected: 에러 없음.

- [ ] **Step 3: 프론트 빌드**

Run: `cd frontend && bun run build`
Expected: 성공.

---

## Self-Review (작성자 점검)

- **Spec 커버리지**: §7.1 tasks_dir=Task1/2 / §7.2 준비도 규칙(무게별·코드들어옴·존재)=Task3 detect_task_not_prepared / §7.3 Drift 재사용·OPEN→자동RESOLVED=Task3(reconcile)+enum / §7.4 sync_service 통합=Task4 / 노출(API 자동, 대시보드=Task6, Discord=Task5). §9.1 코드들어옴 v1=Task3 code_landed / §9.2 존재+비공백=Task3 `_present`.
- **Placeholder**: 감지 로직·테스트·마이그레이션 실제 코드 포함. `_decrypt_pat`/`reconcile` 의 정확한 호출은 "기존 detect 함수 읽고 맞춤"으로 명시(추측 금지).
- **타입 일관**: enum value `task_not_prepared`(소문자, 프론트 union 과 일치) vs DB enum 리터럴(Task2 Step1 에서 케이싱 확인). dedup_key=`"{branch}:{external_id}"`(B 패턴과 동일). `DriftItem`/`reconcile` 시그니처는 탐색 확인분 사용.

## 의존성/주의
- 테스트·마이그레이션은 **로컬 postgres(5433)** 필요(`make db-up`). Docker 없으면 Task2 Step3·Task3 Step4·Task7 은 환경 갖춘 뒤 실행.
- `ALTER TYPE ... ADD VALUE` 트랜잭션 이슈 시 autocommit_block 으로 전환(Task2 Step2 주석).
