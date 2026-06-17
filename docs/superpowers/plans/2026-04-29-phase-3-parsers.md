# Phase 3 — PLAN/handoff 파서 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** PLAN.md / handoff-{branch}.md 텍스트를 정규식 기반으로 파싱하는 두 서비스 구축. 입력은 텍스트(str), 출력은 구조화된 Pydantic 모델. **DB / 외부 API / 파일 fetch 없음** — 순수 함수 단위. 설계서 §6 형식 규약 준수, §10.1 파서 단위 테스트 항목 전부 매핑.

**Architecture:** 두 파서는 각각 단일 진입점 (`parse_plan(text) → ParsedPlan`, `parse_handoff(text) → ParsedHandoff`). 정규식 + 라인 기반 상태머신으로 헤더/섹션/체크박스/들여쓰기를 추출. PLAN parser 는 `## 태스크` 섹션 안의 최상위 체크박스만 인식, `## 노트` 등 다른 섹션은 무시. handoff parser 는 `## YYYY-MM-DD` 섹션을 단위로 분해, 각 섹션 안에서 최상위 체크박스 / 들여쓰기 ≥ 2 서브태스크 / `### 자유 영역(마지막 커밋·다음·블로커)`을 분리. 파서가 던지는 예외(`DuplicateExternalIdError`, `MalformedPlanError`)는 Phase 4 sync_service 가 잡아 `GitPushEvent.error` 에 기록.

**Tech Stack:** Python 3.12, Pydantic v2 (스키마), 표준 `re` 모듈 (정규식), pytest 8.3 + testcontainers (DB 무관 — Phase 3 테스트는 testcontainers 미사용). 외부 의존 추가 없음.

**선행 조건:**
- pslog main, alembic head = `c4dee7f06004` (Phase 1 머지) + Phase 2 webhook 머지 (PR #8) 완료
- Python 3.12.13 venv (`backend/venv`), `requirements.txt` 핀 유지 — 신규 패키지 없음
- 설계서: `docs/superpowers/specs/2026-04-26-ai-task-automation-design.md` §6, §10.1, §8 (파서 에러 케이스)

**중요한 계약:**
- **PLAN.md 파싱 규칙** (§6.1):
  - `## 태스크` 헤더 아래 영역만 파싱, 다른 섹션(`## 노트` 등) 무시
  - 라인 형식: `- [ ] [task-XXX] <title> — @<user> — \`<path>\`, \`<path>\``
  - `[task-XXX]` ID 필수 — 없으면 해당 라인 skip (raw 로그 경고)
  - `@username` → assignee. 없으면 `None`
  - `` `path` `` (백틱) 0개 이상 → paths
  - 같은 PLAN 내 `external_id` 중복 → `DuplicateExternalIdError` raise
- **handoff 파싱 규칙** (§6.2):
  - `# Handoff: <branch> — @<user>` 헤더 1개 — 없으면 `MalformedHandoffError`
  - 브랜치명의 `/` 그대로 보존 (e.g. `feature/login-redesign`)
  - `## YYYY-MM-DD` 가 일자 섹션. 1개 이상 — 없으면 `MalformedHandoffError`
  - 섹션 정렬: 날짜 descending (최신 = `sections[0]`). 동일 날짜는 입력 순서 보존
  - 각 섹션 안 **들여쓰기 0** 인 `- [x] task-XXX` / `- [ ] task-XXX` 만 `checks[]` 로 들어감
  - 들여쓰기 ≥ 2 인 체크박스는 `subtasks[]` (부모는 직전 최상위 체크박스의 `external_id`)
  - `### 마지막 커밋` / `### 다음` / `### 블로커` 안의 자유 텍스트는 `free_notes` 에 보존
- **에러 정책** (§8 파싱 항):
  - "형식 깨짐 → 파싱 가능한 부분만 처리, raw 보존, error 필드에 사유" — 파서는 가능한 한 partial 파싱 후 정상 반환, 결정적 fail 케이스(헤더/날짜 섹션 부재, external_id 중복)만 예외
  - 빈 파일 → 헤더 부재로 fail (handoff) / 빈 tasks 리스트 (PLAN)
- **출력 모델 호환성**: Phase 4 sync_service 가 `ParsedHandoff.sections[0].checks` → `Handoff.parsed_tasks` (`[{"external_id":..., "checked":...}]`) 로 매핑. 다중 날짜 섹션은 brief_service (Phase 7) 가 history 로 사용.

---

## File Structure

**신규 파일 (소스):**
- `backend/app/schemas/parsed_plan.py` — `ParsedPlan`, `ParsedTask` Pydantic 모델
- `backend/app/schemas/parsed_handoff.py` — `ParsedHandoff`, `HandoffSection`, `CheckItem`, `Subtask`, `FreeNotes` Pydantic 모델
- `backend/app/services/plan_parser_service.py` — `parse_plan(text) → ParsedPlan` + `DuplicateExternalIdError`
- `backend/app/services/handoff_parser_service.py` — `parse_handoff(text) → ParsedHandoff` + `MalformedHandoffError`

**신규 파일 (테스트):**
- `backend/tests/test_plan_parser_service.py`
- `backend/tests/test_handoff_parser_service.py`
- `backend/tests/fixtures/plan_sample.md` — 정상 PLAN 샘플
- `backend/tests/fixtures/handoff_sample.md` — 정상 handoff 샘플

**수정 파일:**
- `handoffs/main.md` — Phase 3 완료 섹션 추가 (Task 9)

**수정 없음:**
- `requirements.txt` (의존성 추가 없음 — Pydantic 이미 설치됨)
- `app/config.py` (설정 추가 없음)
- alembic (마이그레이션 없음 — 모델 변경 없음)

---

## Self-Review Notes

작성 후 self-review 항목:
- 설계서 §6.1 PLAN 파싱 규칙 5개 → Task 2 (정상) + Task 3 (edge case) + Task 4 (중복) 매핑
- 설계서 §6.2 handoff 파싱 규칙 6개 → Task 5 (헤더) + Task 6 (날짜 섹션) + Task 7 (체크박스+서브태스크) + Task 8 (자유 영역) 매핑
- 설계서 §10.1 단위 테스트 항목 → 각 Task 의 failing test 에 매핑
- 설계서 §8 파싱 에러 케이스 (형식 깨짐 / external_id 중복) → Task 3, 4 + handoff Task 5, 6
- handoff 메모 (Phase 1) — `mapped_column(default=)` Python init 미적용 → Phase 3 본 영역 아님, 모델 신규 없음
- handoff 메모 (Phase 2) — alembic logging 함정 → Phase 3 본 영역 아님, alembic 미사용

---

## Task 0: Phase 2 plan 파일 누락 정리 + 브랜치 생성

**Files:**
- Add: `docs/superpowers/plans/2026-04-29-phase-2-webhook-receive.md` (untracked, Phase 2 작업 시 main 누락)
- Add: `docs/superpowers/plans/2026-04-29-phase-3-parsers.md` (본 plan)

- [ ] **Step 1: feature/phase-3-parsers 브랜치 생성**

```bash
cd /Users/arden/Documents/ardensdevspace/pslog
git checkout main && git pull --ff-only origin main
git checkout -b feature/phase-3-parsers
```

Expected: 브랜치 생성됨, main 은 origin 과 sync.

- [ ] **Step 2: Phase 2/3 plan 파일 함께 commit (docs 정리)**

```bash
git add docs/superpowers/plans/2026-04-29-phase-2-webhook-receive.md \
        docs/superpowers/plans/2026-04-29-phase-3-parsers.md
git commit -m "docs(plans): Phase 2 plan 누락 보강 + Phase 3 plan 추가"
```

---

## Task 1: 파서 출력 Pydantic 스키마

**Files:**
- Create: `backend/app/schemas/parsed_plan.py`
- Create: `backend/app/schemas/parsed_handoff.py`

본 task 는 데이터 모델만 — 단위 테스트는 후속 task 의 파서 테스트에서 자동 검증됨.

- [ ] **Step 1: ParsedPlan 스키마 작성**

Create `backend/app/schemas/parsed_plan.py`:

```python
"""PLAN.md 파서 출력 모델.

설계서: 2026-04-26-ai-task-automation-design.md §6.1
"""

from pydantic import BaseModel, ConfigDict, Field


class ParsedTask(BaseModel):
    """PLAN.md 의 한 체크박스 라인 — `- [ ] [task-XXX] <title> — @<user> — \`<path>\`...`"""

    model_config = ConfigDict(extra="forbid")

    external_id: str           # "task-001" — 프로젝트 내 unique
    title: str                 # "로그인 UI 리뉴얼"
    checked: bool              # PLAN 자체의 [x]/[ ]
    assignee: str | None       # "alice" 또는 None
    paths: list[str] = Field(default_factory=list)  # backtick 으로 감싼 파일/디렉토리


class ParsedPlan(BaseModel):
    """PLAN.md 전체 파싱 결과."""

    model_config = ConfigDict(extra="forbid")

    sprint_name: str | None    # "# 스프린트: <이름>" 헤더에서 추출. 없으면 None
    tasks: list[ParsedTask] = Field(default_factory=list)
```

- [ ] **Step 2: ParsedHandoff 스키마 작성**

Create `backend/app/schemas/parsed_handoff.py`:

```python
"""handoff-{branch}.md 파서 출력 모델.

설계서: 2026-04-26-ai-task-automation-design.md §6.2
"""

from pydantic import BaseModel, ConfigDict, Field


class CheckItem(BaseModel):
    """들여쓰기 0 — handoff 섹션의 최상위 체크박스. pslog DB 의 Task 상태에 영향."""

    model_config = ConfigDict(extra="forbid")

    external_id: str           # "task-001"
    checked: bool              # [x] / [ ]
    extra: str = ""            # "(60% 완료)" 같은 부가 텍스트 raw 보존


class Subtask(BaseModel):
    """들여쓰기 ≥ 2 — 직전 최상위 체크박스의 자식. pslog DB 미반영, free_notes 보존만."""

    model_config = ConfigDict(extra="forbid")

    parent_external_id: str | None  # 직전 최상위 체크박스의 external_id (없으면 None)
    checked: bool
    text: str                       # "이메일 입력 필드"


class FreeNotes(BaseModel):
    """`### 마지막 커밋` / `### 다음` / `### 블로커` 자유 영역."""

    model_config = ConfigDict(extra="forbid")

    last_commit: str | None = None  # "abc1234 — 로그인 폼 검증 로직"
    next: str | None = None
    blockers: str | None = None


class HandoffSection(BaseModel):
    """`## YYYY-MM-DD` 한 섹션."""

    model_config = ConfigDict(extra="forbid")

    date: str                                    # "2026-04-26" (검증된 ISO 날짜 형식)
    checks: list[CheckItem] = Field(default_factory=list)
    subtasks: list[Subtask] = Field(default_factory=list)
    free_notes: FreeNotes = Field(default_factory=FreeNotes)


class ParsedHandoff(BaseModel):
    """handoff 파일 전체 파싱 결과 — sections[0] 이 최신(active)."""

    model_config = ConfigDict(extra="forbid")

    branch: str                # "feature/login-redesign" — `/` 보존
    author_git_login: str      # "alice"
    sections: list[HandoffSection] = Field(default_factory=list)  # date desc
```

- [ ] **Step 3: import smoke test**

```bash
cd /Users/arden/Documents/ardensdevspace/pslog/backend
source venv/bin/activate
python -c "from app.schemas.parsed_plan import ParsedPlan, ParsedTask; from app.schemas.parsed_handoff import ParsedHandoff, HandoffSection, CheckItem, Subtask, FreeNotes; print('OK')"
```

Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add backend/app/schemas/parsed_plan.py backend/app/schemas/parsed_handoff.py
git commit -m "feat(phase3): 파서 출력 Pydantic 스키마 (ParsedPlan, ParsedHandoff)"
```

---

## Task 2: plan_parser_service — 정상 파싱 (TDD)

**Files:**
- Create: `backend/tests/fixtures/plan_sample.md`
- Create: `backend/tests/test_plan_parser_service.py`
- Create: `backend/app/services/plan_parser_service.py`

- [ ] **Step 1: 정상 PLAN fixture 저장**

Create `backend/tests/fixtures/plan_sample.md`:

```markdown
# 스프린트: 2026-04 로그인 리뉴얼

## 태스크

- [ ] [task-001] 로그인 UI 리뉴얼 — @alice — `frontend/screens/Login.tsx`, `frontend/components/auth/`
- [ ] [task-002] JWT 토큰 만료 처리 — @bob — `backend/auth/`
- [x] [task-003] 알림 모달 — @charlie — `frontend/components/Notification.tsx`

## 노트

- 이 영역은 pslog 가 무시해야 함
- [ ] [task-999] 노트 안의 체크박스도 파싱하면 안 됨
```

- [ ] **Step 2: Failing test 작성 — 정상 파싱**

Create `backend/tests/test_plan_parser_service.py`:

```python
"""plan_parser_service — PLAN.md 파싱 단위 테스트.

설계서: 2026-04-26-ai-task-automation-design.md §6.1, §10.1
"""

from pathlib import Path

import pytest

from app.services.plan_parser_service import parse_plan


FIXTURE = (Path(__file__).parent / "fixtures" / "plan_sample.md").read_text()


def test_parse_plan_extracts_sprint_name():
    plan = parse_plan(FIXTURE)
    assert plan.sprint_name == "2026-04 로그인 리뉴얼"


def test_parse_plan_extracts_tasks():
    plan = parse_plan(FIXTURE)
    assert len(plan.tasks) == 3
    ids = [t.external_id for t in plan.tasks]
    assert ids == ["task-001", "task-002", "task-003"]


def test_parse_plan_task_fields():
    plan = parse_plan(FIXTURE)
    t1 = plan.tasks[0]
    assert t1.external_id == "task-001"
    assert t1.title == "로그인 UI 리뉴얼"
    assert t1.checked is False
    assert t1.assignee == "alice"
    assert t1.paths == ["frontend/screens/Login.tsx", "frontend/components/auth/"]


def test_parse_plan_checked_status():
    plan = parse_plan(FIXTURE)
    t3 = plan.tasks[2]
    assert t3.external_id == "task-003"
    assert t3.checked is True


def test_parse_plan_ignores_note_section():
    """## 노트 안의 체크박스는 무시 (task-999 가 들어가면 안 됨)."""
    plan = parse_plan(FIXTURE)
    ids = [t.external_id for t in plan.tasks]
    assert "task-999" not in ids
```

- [ ] **Step 3: Run test — 실패 확인**

```bash
cd /Users/arden/Documents/ardensdevspace/pslog/backend
pytest tests/test_plan_parser_service.py -v
```

Expected: ImportError (`app.services.plan_parser_service` 없음)

- [ ] **Step 4: 정상 파싱 구현**

Create `backend/app/services/plan_parser_service.py`:

```python
"""PLAN.md 파서 — `## 태스크` 섹션 안의 체크박스를 ParsedTask 로 추출.

설계서: 2026-04-26-ai-task-automation-design.md §6.1

라인 형식 예:
  - [ ] [task-001] 로그인 UI 리뉴얼 — @alice — `frontend/Login.tsx`, `frontend/auth/`

파싱 규칙:
  - `## 태스크` 헤더 아래만 — 다른 섹션 (## 노트 등) 무시
  - `[task-XXX]` 필수 — 없으면 해당 라인 skip
  - `@username` 0~1 개 → assignee
  - `` `path` `` 0+ 개 → paths
  - 같은 PLAN 내 external_id 중복 → DuplicateExternalIdError
"""

import re

from app.schemas.parsed_plan import ParsedPlan, ParsedTask


class DuplicateExternalIdError(ValueError):
    """같은 PLAN 내에서 같은 external_id 가 두 번 이상 등장."""

    def __init__(self, external_id: str):
        self.external_id = external_id
        super().__init__(f"duplicate external_id in PLAN: {external_id}")


# 라인 단위 정규식
_SPRINT_RE = re.compile(r"^#\s+스프린트\s*:\s*(.+?)\s*$", re.MULTILINE)
_SECTION_RE = re.compile(r"^##\s+(.+?)\s*$")
_TASK_LINE_RE = re.compile(
    r"^-\s+\[(?P<check>[ xX])\]\s+\[(?P<id>task-[A-Za-z0-9_-]+)\]\s+(?P<rest>.+?)\s*$"
)
_ASSIGNEE_RE = re.compile(r"@([A-Za-z0-9_-]+)")
_PATH_RE = re.compile(r"`([^`]+)`")
_TASK_SECTION_HEADER = "태스크"


def _parse_task_rest(rest: str) -> tuple[str, str | None, list[str]]:
    """`<title> — @user — \`path\`, \`path\`` → (title, assignee, paths)."""
    assignee_match = _ASSIGNEE_RE.search(rest)
    assignee = assignee_match.group(1) if assignee_match else None

    paths = _PATH_RE.findall(rest)

    # title 은 첫 ` — ` 또는 ` @` 또는 첫 `` ` `` 이전까지
    title_end = len(rest)
    for marker in (" — ", " @", "`"):
        idx = rest.find(marker)
        if idx != -1 and idx < title_end:
            title_end = idx
    title = rest[:title_end].strip()
    return title, assignee, paths


def parse_plan(text: str) -> ParsedPlan:
    """PLAN.md 텍스트 → ParsedPlan."""
    sprint_match = _SPRINT_RE.search(text)
    sprint_name = sprint_match.group(1).strip() if sprint_match else None

    tasks: list[ParsedTask] = []
    seen_ids: set[str] = set()
    in_task_section = False

    for raw_line in text.splitlines():
        section_match = _SECTION_RE.match(raw_line)
        if section_match:
            in_task_section = section_match.group(1).strip() == _TASK_SECTION_HEADER
            continue
        if not in_task_section:
            continue

        m = _TASK_LINE_RE.match(raw_line)
        if not m:
            continue

        external_id = m.group("id")
        if external_id in seen_ids:
            raise DuplicateExternalIdError(external_id)
        seen_ids.add(external_id)

        title, assignee, paths = _parse_task_rest(m.group("rest"))
        tasks.append(
            ParsedTask(
                external_id=external_id,
                title=title,
                checked=m.group("check").lower() == "x",
                assignee=assignee,
                paths=paths,
            )
        )

    return ParsedPlan(sprint_name=sprint_name, tasks=tasks)
```

- [ ] **Step 5: Run test — pass**

```bash
pytest tests/test_plan_parser_service.py -v
```

Expected: 5 tests pass.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/plan_parser_service.py \
        backend/tests/test_plan_parser_service.py \
        backend/tests/fixtures/plan_sample.md
git commit -m "feat(phase3): plan_parser_service — 정상 PLAN.md 파싱 (정규식)"
```

---

## Task 3: plan_parser_service — edge case (빈/형식 깨짐/노 헤더)

**Files:**
- Modify: `backend/tests/test_plan_parser_service.py`

- [ ] **Step 1: edge case test 추가**

`backend/tests/test_plan_parser_service.py` 끝에 추가:

```python
def test_parse_plan_empty_text():
    plan = parse_plan("")
    assert plan.sprint_name is None
    assert plan.tasks == []


def test_parse_plan_no_task_section():
    text = "# 스프린트: 빈 스프린트\n\n## 노트\n\n- 메모만"
    plan = parse_plan(text)
    assert plan.sprint_name == "빈 스프린트"
    assert plan.tasks == []


def test_parse_plan_skips_lines_without_task_id():
    """[task-XXX] 형식 빠진 체크박스는 skip."""
    text = """# 스프린트: 테스트

## 태스크

- [ ] [task-001] 정상 라인 — @alice
- [ ] 형식 깨짐 (ID 없음)
- [ ] [task-002] 또 정상 — @bob
"""
    plan = parse_plan(text)
    ids = [t.external_id for t in plan.tasks]
    assert ids == ["task-001", "task-002"]


def test_parse_plan_task_without_assignee_or_paths():
    text = """## 태스크

- [ ] [task-100] 최소 형식 라인
"""
    plan = parse_plan(text)
    assert len(plan.tasks) == 1
    t = plan.tasks[0]
    assert t.title == "최소 형식 라인"
    assert t.assignee is None
    assert t.paths == []


def test_parse_plan_task_with_multiple_paths_and_no_assignee():
    text = """## 태스크

- [ ] [task-200] 멀티 path — `frontend/a.tsx`, `frontend/b.tsx`, `backend/c.py`
"""
    plan = parse_plan(text)
    t = plan.tasks[0]
    assert t.assignee is None
    assert t.paths == ["frontend/a.tsx", "frontend/b.tsx", "backend/c.py"]


def test_parse_plan_returns_to_non_task_section():
    """## 태스크 → ## 노트 → ## 태스크 (재진입) 케이스."""
    text = """## 태스크

- [ ] [task-001] 첫 그룹

## 노트

- [ ] [task-NOTE] 무시되어야 함

## 태스크

- [ ] [task-002] 두 번째 그룹
"""
    plan = parse_plan(text)
    ids = [t.external_id for t in plan.tasks]
    assert ids == ["task-001", "task-002"]
    assert "task-NOTE" not in ids
```

- [ ] **Step 2: Run test — pass (재진입 케이스만 빨간 줄 가능, 그러면 구현 보강)**

```bash
pytest tests/test_plan_parser_service.py -v
```

Expected: Task 2 구현이 `## 태스크 → ## 노트 → ## 태스크` 재진입 케이스를 자동으로 처리 (현재 구현이 매 헤더 마다 `in_task_section` 재계산하므로 통과). 모든 테스트 pass.

- [ ] **Step 3: Commit**

```bash
git add backend/tests/test_plan_parser_service.py
git commit -m "test(phase3): plan_parser edge cases (빈/형식 깨짐/섹션 재진입)"
```

---

## Task 4: plan_parser_service — external_id 중복 reject

**Files:**
- Modify: `backend/tests/test_plan_parser_service.py`

- [ ] **Step 1: 중복 reject test 추가**

`backend/tests/test_plan_parser_service.py` 끝에 추가:

```python
from app.services.plan_parser_service import DuplicateExternalIdError


def test_parse_plan_duplicate_external_id_raises():
    text = """## 태스크

- [ ] [task-001] 첫 번째 — @alice
- [ ] [task-002] 다른 거
- [ ] [task-001] 같은 ID 재등장 — @bob
"""
    with pytest.raises(DuplicateExternalIdError) as exc_info:
        parse_plan(text)
    assert exc_info.value.external_id == "task-001"


def test_parse_plan_duplicate_across_task_sections_raises():
    """다른 ## 태스크 섹션이라도 같은 PLAN 내라면 중복 reject."""
    text = """## 태스크

- [ ] [task-001] 첫 번째

## 노트

(중간 노트)

## 태스크

- [ ] [task-001] 다시 나타남
"""
    with pytest.raises(DuplicateExternalIdError):
        parse_plan(text)
```

- [ ] **Step 2: Run test — pass**

```bash
pytest tests/test_plan_parser_service.py -v
```

Expected: 모든 테스트 pass (Task 2 구현이 `seen_ids` set 기반 중복 검출 이미 포함).

- [ ] **Step 3: Commit**

```bash
git add backend/tests/test_plan_parser_service.py
git commit -m "test(phase3): plan_parser external_id 중복 reject 검증"
```

---

## Task 5: handoff_parser_service — 헤더 추출 (TDD)

**Files:**
- Create: `backend/tests/fixtures/handoff_sample.md`
- Create: `backend/tests/test_handoff_parser_service.py`
- Create: `backend/app/services/handoff_parser_service.py`

- [ ] **Step 1: 정상 handoff fixture 저장**

Create `backend/tests/fixtures/handoff_sample.md`:

```markdown
# Handoff: feature/login-redesign — @alice

## 2026-04-26

- [x] task-001
- [ ] task-007 (60% 완료)
  - [x] 이메일 입력 필드
  - [x] validation 로직
  - [ ] 약관 동의 체크박스
  - [ ] 에러 메시지 i18n

### 마지막 커밋

abc1234 — 로그인 폼 검증 로직

### 다음

- task-007 마무리 후 PR

### 블로커

없음

---

## 2026-04-25

- [x] task-001 시작
- [ ] task-002

### 마지막 커밋

def5678 — 초기 스캐폴딩

### 다음

내일 task-007 진입

### 블로커

backend API 응답 포맷 미정 (bob 와 협의 필요)
```

- [ ] **Step 2: Failing 헤더 test 작성**

Create `backend/tests/test_handoff_parser_service.py`:

```python
"""handoff_parser_service — handoff-{branch}.md 파싱 단위 테스트.

설계서: 2026-04-26-ai-task-automation-design.md §6.2, §10.1
"""

from pathlib import Path

import pytest

from app.services.handoff_parser_service import (
    MalformedHandoffError,
    parse_handoff,
)


FIXTURE = (Path(__file__).parent / "fixtures" / "handoff_sample.md").read_text()


def test_parse_handoff_extracts_branch_and_author():
    h = parse_handoff(FIXTURE)
    assert h.branch == "feature/login-redesign"
    assert h.author_git_login == "alice"


def test_parse_handoff_missing_header_raises():
    text = "## 2026-04-26\n- [x] task-001\n"
    with pytest.raises(MalformedHandoffError):
        parse_handoff(text)


def test_parse_handoff_empty_text_raises():
    with pytest.raises(MalformedHandoffError):
        parse_handoff("")


def test_parse_handoff_branch_with_slash_preserved():
    text = "# Handoff: release/v1.2.3 — @bob\n\n## 2026-04-29\n- [ ] task-001\n"
    h = parse_handoff(text)
    assert h.branch == "release/v1.2.3"
    assert h.author_git_login == "bob"
```

- [ ] **Step 3: Run test — 실패 확인**

```bash
pytest tests/test_handoff_parser_service.py -v
```

Expected: ImportError

- [ ] **Step 4: 헤더 파싱 + 예외 정의 + 빈 sections placeholder 구현**

Create `backend/app/services/handoff_parser_service.py`:

```python
"""handoff-{branch}.md 파서 — 헤더 / 날짜 섹션 / 체크박스 / 자유 영역 추출.

설계서: 2026-04-26-ai-task-automation-design.md §6.2

파싱 단계:
  1) `# Handoff: <branch> — @<user>` 헤더 1개 (없으면 MalformedHandoffError)
  2) `## YYYY-MM-DD` 일자 섹션 ≥1 개 (없으면 MalformedHandoffError)
  3) 각 섹션 안:
     - 들여쓰기 0 인 `- [x]/[ ] task-XXX` → CheckItem
     - 들여쓰기 ≥ 2 인 체크박스 → Subtask (parent = 직전 최상위 체크박스)
     - `### 마지막 커밋` / `### 다음` / `### 블로커` 자유 텍스트 → FreeNotes
  4) sections 정렬: date desc (최신 = sections[0])
"""

import re

from app.schemas.parsed_handoff import (
    CheckItem,
    FreeNotes,
    HandoffSection,
    ParsedHandoff,
    Subtask,
)


class MalformedHandoffError(ValueError):
    """필수 헤더(파일 헤더 또는 일자 섹션) 부재."""


_HEADER_RE = re.compile(
    r"^#\s+Handoff\s*:\s*(?P<branch>\S+)\s+—\s+@(?P<user>[A-Za-z0-9_-]+)\s*$"
)
_DATE_SECTION_RE = re.compile(r"^##\s+(?P<date>\d{4}-\d{2}-\d{2})\s*$")
_FREE_NOTE_HEADERS = {
    "마지막 커밋": "last_commit",
    "다음": "next",
    "블로커": "blockers",
}
_FREE_NOTE_HEADER_RE = re.compile(r"^###\s+(?P<name>.+?)\s*$")
_TOP_CHECK_RE = re.compile(
    r"^-\s+\[(?P<check>[ xX])\]\s+(?P<id>task-[A-Za-z0-9_-]+)\s*(?P<extra>.*?)\s*$"
)
_SUB_CHECK_RE = re.compile(
    r"^(?P<indent>(?:    |\t|  )+)-\s+\[(?P<check>[ xX])\]\s+(?P<text>.+?)\s*$"
)


def parse_handoff(text: str) -> ParsedHandoff:
    """handoff 텍스트 → ParsedHandoff. sections 는 date desc."""
    lines = text.splitlines()
    branch: str | None = None
    author: str | None = None

    for line in lines:
        m = _HEADER_RE.match(line)
        if m:
            branch = m.group("branch")
            author = m.group("user")
            break

    if branch is None or author is None:
        raise MalformedHandoffError("missing or malformed `# Handoff: <branch> — @<user>` header")

    sections: list[HandoffSection] = []
    # 날짜 섹션 분리 + 본문 파싱은 후속 task 에서 채움.
    if not any(_DATE_SECTION_RE.match(line) for line in lines):
        raise MalformedHandoffError("no `## YYYY-MM-DD` section found")

    return ParsedHandoff(branch=branch, author_git_login=author, sections=sections)
```

- [ ] **Step 5: Run test — pass (헤더 4개)**

```bash
pytest tests/test_handoff_parser_service.py -v
```

Expected: 4 tests pass (헤더 + 예외 케이스).

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/handoff_parser_service.py \
        backend/tests/test_handoff_parser_service.py \
        backend/tests/fixtures/handoff_sample.md
git commit -m "feat(phase3): handoff_parser — 헤더 (branch+author) 추출 + Malformed 예외"
```

---

## Task 6: handoff_parser_service — 날짜 섹션 분리 + 정렬

**Files:**
- Modify: `backend/app/services/handoff_parser_service.py`
- Modify: `backend/tests/test_handoff_parser_service.py`

- [ ] **Step 1: 날짜 섹션 test 추가**

`backend/tests/test_handoff_parser_service.py` 끝에 추가:

```python
def test_parse_handoff_two_date_sections():
    h = parse_handoff(FIXTURE)
    assert len(h.sections) == 2
    assert h.sections[0].date == "2026-04-26"
    assert h.sections[1].date == "2026-04-25"


def test_parse_handoff_sections_sorted_desc_regardless_of_input_order():
    """입력에서 옛날 날짜가 먼저 나와도 정렬 결과는 desc."""
    text = """# Handoff: feature/x — @alice

## 2026-04-25

- [ ] task-1

## 2026-04-29

- [x] task-2
"""
    h = parse_handoff(text)
    assert [s.date for s in h.sections] == ["2026-04-29", "2026-04-25"]


def test_parse_handoff_single_section():
    text = """# Handoff: main — @bob

## 2026-04-29

- [ ] task-001
"""
    h = parse_handoff(text)
    assert len(h.sections) == 1
    assert h.sections[0].date == "2026-04-29"
```

- [ ] **Step 2: 구현 — 날짜 섹션 분리 + 빈 섹션 (체크박스/free_notes 비워둠)**

`backend/app/services/handoff_parser_service.py` 의 `parse_handoff` 함수를 다음으로 교체 (헤더 처리는 보존):

```python
def parse_handoff(text: str) -> ParsedHandoff:
    """handoff 텍스트 → ParsedHandoff. sections 는 date desc."""
    lines = text.splitlines()
    branch: str | None = None
    author: str | None = None

    for line in lines:
        m = _HEADER_RE.match(line)
        if m:
            branch = m.group("branch")
            author = m.group("user")
            break

    if branch is None or author is None:
        raise MalformedHandoffError("missing or malformed `# Handoff: <branch> — @<user>` header")

    # 날짜 섹션으로 분할
    section_blocks: list[tuple[str, list[str]]] = []  # (date, body_lines)
    current_date: str | None = None
    current_body: list[str] = []
    for line in lines:
        date_match = _DATE_SECTION_RE.match(line)
        if date_match:
            if current_date is not None:
                section_blocks.append((current_date, current_body))
            current_date = date_match.group("date")
            current_body = []
        elif current_date is not None:
            current_body.append(line)
    if current_date is not None:
        section_blocks.append((current_date, current_body))

    if not section_blocks:
        raise MalformedHandoffError("no `## YYYY-MM-DD` section found")

    sections = [
        HandoffSection(date=date, checks=[], subtasks=[], free_notes=FreeNotes())
        for date, _body in section_blocks
    ]
    sections.sort(key=lambda s: s.date, reverse=True)

    return ParsedHandoff(branch=branch, author_git_login=author, sections=sections)
```

- [ ] **Step 3: Run test — pass**

```bash
pytest tests/test_handoff_parser_service.py -v
```

Expected: 7 tests pass (4 헤더 + 3 날짜 섹션).

- [ ] **Step 4: Commit**

```bash
git add backend/app/services/handoff_parser_service.py backend/tests/test_handoff_parser_service.py
git commit -m "feat(phase3): handoff_parser — 날짜 섹션 분리 + date desc 정렬"
```

---

## Task 7: handoff_parser_service — 체크박스 + 들여쓰기 서브태스크

**Files:**
- Modify: `backend/app/services/handoff_parser_service.py`
- Modify: `backend/tests/test_handoff_parser_service.py`

- [ ] **Step 1: 체크박스 test 추가**

`backend/tests/test_handoff_parser_service.py` 끝에 추가:

```python
def test_parse_handoff_active_section_top_level_checks():
    h = parse_handoff(FIXTURE)
    active = h.sections[0]  # 2026-04-26
    ids = [c.external_id for c in active.checks]
    assert ids == ["task-001", "task-007"]
    assert active.checks[0].checked is True
    assert active.checks[1].checked is False


def test_parse_handoff_check_extra_text_preserved():
    """`- [ ] task-007 (60% 완료)` → CheckItem.extra = '(60% 완료)'."""
    h = parse_handoff(FIXTURE)
    t007 = next(c for c in h.sections[0].checks if c.external_id == "task-007")
    assert "60% 완료" in t007.extra


def test_parse_handoff_subtasks_indent_two_or_more():
    """들여쓰기 2 이상 체크박스는 subtasks 로 분리, parent 는 직전 최상위."""
    h = parse_handoff(FIXTURE)
    active = h.sections[0]
    assert len(active.subtasks) == 4
    parents = {s.parent_external_id for s in active.subtasks}
    assert parents == {"task-007"}  # 직전 최상위가 task-007
    texts = [s.text for s in active.subtasks]
    assert texts == [
        "이메일 입력 필드",
        "validation 로직",
        "약관 동의 체크박스",
        "에러 메시지 i18n",
    ]
    assert active.subtasks[0].checked is True
    assert active.subtasks[2].checked is False


def test_parse_handoff_subtask_without_top_level_parent_has_none():
    text = """# Handoff: main — @x

## 2026-04-29

  - [ ] 어떤 부모 체크박스 없이 들여쓰기 2 로 시작
"""
    h = parse_handoff(text)
    active = h.sections[0]
    assert len(active.subtasks) == 1
    assert active.subtasks[0].parent_external_id is None


def test_parse_handoff_per_section_checks_isolated():
    """다른 날짜 섹션의 체크박스가 섞이지 않음."""
    h = parse_handoff(FIXTURE)
    older = h.sections[1]  # 2026-04-25
    ids = [c.external_id for c in older.checks]
    assert ids == ["task-001", "task-002"]
    assert older.subtasks == []
```

- [ ] **Step 2: 구현 — 섹션 본문에서 체크박스 + 서브태스크 추출**

`backend/app/services/handoff_parser_service.py` 안의 섹션 빌딩 로직을 다음으로 교체:

```python
def _parse_section_body(body_lines: list[str]) -> tuple[list[CheckItem], list[Subtask], FreeNotes]:
    """한 날짜 섹션의 본문 라인들 → (checks, subtasks, free_notes).

    free_notes 는 Task 8 에서 채움 — 본 task 는 빈 FreeNotes 반환.
    """
    checks: list[CheckItem] = []
    subtasks: list[Subtask] = []
    last_top_id: str | None = None

    for raw in body_lines:
        # 들여쓰기 0 인 최상위 체크박스
        top_match = _TOP_CHECK_RE.match(raw)
        if top_match:
            external_id = top_match.group("id")
            checks.append(
                CheckItem(
                    external_id=external_id,
                    checked=top_match.group("check").lower() == "x",
                    extra=top_match.group("extra").strip(),
                )
            )
            last_top_id = external_id
            continue

        # 들여쓰기 ≥ 2 (스페이스 2/4 또는 탭) 체크박스
        sub_match = _SUB_CHECK_RE.match(raw)
        if sub_match:
            subtasks.append(
                Subtask(
                    parent_external_id=last_top_id,
                    checked=sub_match.group("check").lower() == "x",
                    text=sub_match.group("text").strip(),
                )
            )

    return checks, subtasks, FreeNotes()
```

그리고 `parse_handoff` 안의 sections 빌딩 부분을 다음으로 교체:

```python
    sections = []
    for date, body in section_blocks:
        checks, subtasks, free_notes = _parse_section_body(body)
        sections.append(
            HandoffSection(
                date=date,
                checks=checks,
                subtasks=subtasks,
                free_notes=free_notes,
            )
        )
    sections.sort(key=lambda s: s.date, reverse=True)
```

- [ ] **Step 3: Run test — pass**

```bash
pytest tests/test_handoff_parser_service.py -v
```

Expected: 12 tests pass (7 기존 + 5 신규).

- [ ] **Step 4: Commit**

```bash
git add backend/app/services/handoff_parser_service.py backend/tests/test_handoff_parser_service.py
git commit -m "feat(phase3): handoff_parser — 최상위 체크박스 + 들여쓰기≥2 서브태스크 분리"
```

---

## Task 8: handoff_parser_service — 자유 영역 (마지막 커밋/다음/블로커)

**Files:**
- Modify: `backend/app/services/handoff_parser_service.py`
- Modify: `backend/tests/test_handoff_parser_service.py`

- [ ] **Step 1: 자유 영역 test 추가**

`backend/tests/test_handoff_parser_service.py` 끝에 추가:

```python
def test_parse_handoff_free_notes_active_section():
    h = parse_handoff(FIXTURE)
    active = h.sections[0]  # 2026-04-26
    assert active.free_notes.last_commit is not None
    assert "abc1234" in active.free_notes.last_commit
    assert "로그인 폼 검증 로직" in active.free_notes.last_commit
    assert active.free_notes.next is not None
    assert "task-007" in active.free_notes.next
    assert active.free_notes.blockers == "없음"


def test_parse_handoff_free_notes_older_section():
    h = parse_handoff(FIXTURE)
    older = h.sections[1]  # 2026-04-25
    assert older.free_notes.last_commit is not None
    assert "def5678" in older.free_notes.last_commit
    assert "초기 스캐폴딩" in older.free_notes.last_commit
    assert older.free_notes.next == "내일 task-007 진입"
    assert older.free_notes.blockers is not None
    assert "backend API 응답 포맷 미정" in older.free_notes.blockers


def test_parse_handoff_free_notes_partial_missing_ok():
    """### 다음 만 있고 ### 마지막 커밋 / ### 블로커 없어도 정상 파싱."""
    text = """# Handoff: main — @x

## 2026-04-29

- [ ] task-001

### 다음

내일 마무리
"""
    h = parse_handoff(text)
    fn = h.sections[0].free_notes
    assert fn.last_commit is None
    assert fn.next == "내일 마무리"
    assert fn.blockers is None


def test_parse_handoff_free_notes_section_terminates_at_next_h3_or_h2():
    """### 마지막 커밋 다음에 ### 다음 또는 ## 가 오면 거기서 끊김."""
    text = """# Handoff: main — @x

## 2026-04-29

- [ ] task-001

### 마지막 커밋

abc1234

### 다음

내일

## 2026-04-28

- [ ] task-old
"""
    h = parse_handoff(text)
    s_new = h.sections[0]
    assert s_new.free_notes.last_commit == "abc1234"
    assert s_new.free_notes.next == "내일"
    s_old = h.sections[1]
    assert s_old.free_notes.last_commit is None
```

- [ ] **Step 2: 구현 — `_parse_section_body` 에 free_notes 영역 추출 추가**

`backend/app/services/handoff_parser_service.py` 의 `_parse_section_body` 를 다음으로 교체:

```python
def _parse_section_body(body_lines: list[str]) -> tuple[list[CheckItem], list[Subtask], FreeNotes]:
    """한 날짜 섹션의 본문 라인들 → (checks, subtasks, free_notes).

    체크박스 / 서브태스크 는 첫 ### 헤더 등장 전까지 만 추출.
    `### 마지막 커밋` / `### 다음` / `### 블로커` 안의 텍스트는 다음 ### 또는 끝까지 모음.
    """
    checks: list[CheckItem] = []
    subtasks: list[Subtask] = []
    last_top_id: str | None = None

    free_notes_raw: dict[str, list[str]] = {"last_commit": [], "next": [], "blockers": []}
    current_free_key: str | None = None

    for raw in body_lines:
        h3 = _FREE_NOTE_HEADER_RE.match(raw)
        if h3:
            name = h3.group("name").strip()
            current_free_key = _FREE_NOTE_HEADERS.get(name)
            continue

        if current_free_key is not None:
            free_notes_raw[current_free_key].append(raw)
            continue

        # 체크박스 영역 (### 등장 전)
        top_match = _TOP_CHECK_RE.match(raw)
        if top_match:
            external_id = top_match.group("id")
            checks.append(
                CheckItem(
                    external_id=external_id,
                    checked=top_match.group("check").lower() == "x",
                    extra=top_match.group("extra").strip(),
                )
            )
            last_top_id = external_id
            continue

        sub_match = _SUB_CHECK_RE.match(raw)
        if sub_match:
            subtasks.append(
                Subtask(
                    parent_external_id=last_top_id,
                    checked=sub_match.group("check").lower() == "x",
                    text=sub_match.group("text").strip(),
                )
            )

    def _join(lines: list[str]) -> str | None:
        # 빈 줄 trim, 내용 없으면 None
        joined = "\n".join(lines).strip()
        return joined or None

    free_notes = FreeNotes(
        last_commit=_join(free_notes_raw["last_commit"]),
        next=_join(free_notes_raw["next"]),
        blockers=_join(free_notes_raw["blockers"]),
    )
    return checks, subtasks, free_notes
```

- [ ] **Step 3: Run test — pass**

```bash
pytest tests/test_handoff_parser_service.py -v
```

Expected: 16 tests pass (12 기존 + 4 신규).

- [ ] **Step 4: Commit**

```bash
git add backend/app/services/handoff_parser_service.py backend/tests/test_handoff_parser_service.py
git commit -m "feat(phase3): handoff_parser — 자유 영역 (마지막 커밋/다음/블로커) 추출"
```

---

## Task 9: 회귀 테스트 + handoff 갱신

**Files:**
- Modify: `handoffs/main.md`

- [ ] **Step 1: 전체 테스트 회귀**

```bash
cd /Users/arden/Documents/ardensdevspace/pslog/backend
source venv/bin/activate
pytest -v --tb=short
```

Expected: Phase 1 (41) + Phase 2 (32) + Phase 3 신규 (≥ 22) 모두 pass. Phase 3 신규 분포:
- plan_parser: 5 (정상) + 6 (edge) + 2 (중복) = 13
- handoff_parser: 4 (헤더) + 3 (날짜) + 5 (체크박스/서브태스크) + 4 (자유 영역) = 16
- 합계 ≥ 29 신규 (총 ≥ 102)

- [ ] **Step 2: 누락 점검 체크리스트**

설계서 §10.1 항목 매핑 확인:
- [ ] PLAN 정상 → `test_parse_plan_extracts_*`, `test_parse_plan_task_fields`
- [ ] PLAN 형식 어긋남 → `test_parse_plan_skips_lines_without_task_id`
- [ ] PLAN 빈 파일 → `test_parse_plan_empty_text`
- [ ] PLAN 노트 영역 무시 → `test_parse_plan_ignores_note_section`, `test_parse_plan_returns_to_non_task_section`
- [ ] PLAN external_id 중복 reject → `test_parse_plan_duplicate_external_id_raises`, `test_parse_plan_duplicate_across_task_sections_raises`
- [ ] handoff 다중 날짜 섹션 → `test_parse_handoff_two_date_sections`, `test_parse_handoff_sections_sorted_desc_regardless_of_input_order`
- [ ] handoff 체크박스 diff → `test_parse_handoff_active_section_top_level_checks`, `test_parse_handoff_per_section_checks_isolated`
- [ ] handoff 들여쓰기로 서브태스크 분리 → `test_parse_handoff_subtasks_indent_two_or_more`, `test_parse_handoff_subtask_without_top_level_parent_has_none`
- [ ] handoff 자유 영역 보존 → `test_parse_handoff_free_notes_*` (4건)

설계서 §8 파싱 에러 케이스 매핑:
- [ ] 형식 깨짐 (PLAN ID 없음 라인) → 해당 라인 skip + 정상 파싱 유지
- [ ] external_id 중복 → `DuplicateExternalIdError` raise
- [ ] handoff 헤더 없음 / 날짜 섹션 없음 → `MalformedHandoffError` raise

모든 항목 매핑됨.

- [ ] **Step 3: handoff 갱신**

`handoffs/main.md` 상단 (현재 `# Handoff: main — @ardensdevspace` 다음) 에 Phase 3 섹션 추가:

```markdown
## 2026-04-29 (Phase 3)

- [x] **Phase 3 완료** — PLAN/handoff 파서 (브랜치 `feature/phase-3-parsers`)
  - [x] `ParsedPlan` / `ParsedTask` Pydantic 스키마
  - [x] `ParsedHandoff` / `HandoffSection` / `CheckItem` / `Subtask` / `FreeNotes` Pydantic 스키마
  - [x] `plan_parser_service.parse_plan()` — `## 태스크` 섹션 제한, `[task-XXX]` 형식 + `@user` + `` `path` `` 추출, `DuplicateExternalIdError`
  - [x] `handoff_parser_service.parse_handoff()` — 헤더 / `## YYYY-MM-DD` 섹션 / 들여쓰기 0 체크박스 / 들여쓰기 ≥ 2 서브태스크 / `### 마지막 커밋·다음·블로커` 자유 영역, `MalformedHandoffError`
  - [x] sections date desc 정렬 (sections[0] = active)
  - [x] **N tests passing** (Phase 1+2+3)

### 마지막 커밋

- pslog: `<sha> docs(handoff): Phase 3 완료 + Phase 4 다음 할 일` (브랜치 `feature/phase-3-parsers`)
- 브랜치 base: `c3a2817` (main, Phase 2 머지 직후)
- 머지 전 PR 생성 + 사용자 검토 단계

### 다음 (Phase 4 — sync_service + git fetch)

- [ ] `git_repo_service` (GitHub Contents API + Compare API) — PAT Fernet 복호화 재사용
- [ ] `sync_service` — webhook → fetch → parse → DB 반영 + TaskEvent 생성
- [ ] `push_event_reaper` callback 주입 (Phase 2 stub 교체)
- [ ] 멱등성 (CRITICAL — 같은 webhook 2번 → 1번 반영)
- [ ] PLAN 에서 사라진 task → `archived_at` soft-delete
- [ ] 체크 → 언체크 (DONE → TODO 회귀) 처리

### 블로커

없음

### 메모 (2026-04-29 Phase 3 추가)

- **파서는 순수 함수**: DB / 외부 API 의존 없음 — 테스트는 testcontainers 미사용 (pytest 기본). 빠름.
- **들여쓰기 인식**: 스페이스 2/4 또는 탭. `_SUB_CHECK_RE` 가 `(?:    |\t|  )+` 패턴. PLAN.md 들여쓰기 정책 통일 필요 시 lint 추가.
- **자유 영역 boundary**: `### 마지막 커밋` 안의 raw 는 다음 `### ...` 또는 `## ...` 까지. 빈 줄 trim 후 None 또는 텍스트.
- **에러 분류 결정**: 형식 깨짐 라인은 skip (parsing-resilient), 결정적 fail (헤더/날짜 부재, ID 중복) 만 예외. Phase 4 sync_service 가 예외 잡아 `GitPushEvent.error` 기록.

---
```

- [ ] **Step 4: handoff commit**

```bash
git add handoffs/main.md
git commit -m "docs(handoff): Phase 3 완료 + Phase 4 다음 할 일"
```

- [ ] **Step 5: PR 생성**

```bash
git push -u origin feature/phase-3-parsers
gh pr create --title "feat: Phase 3 — PLAN/handoff 파서" --body "$(cat <<'EOF'
## Summary

- `plan_parser_service.parse_plan()` — `## 태스크` 섹션 제한, `[task-XXX]` + `@user` + `` `path` `` 추출, `DuplicateExternalIdError` raise
- `handoff_parser_service.parse_handoff()` — 헤더(branch+author), `## YYYY-MM-DD` 섹션 분해(date desc), 들여쓰기 0/≥2 체크박스 분리, `### 마지막 커밋·다음·블로커` 자유 영역
- 순수 함수 — DB/외부 API 의존 없음. 모든 단위 테스트는 텍스트 입력만 사용
- Phase 2 plan 누락 파일 함께 정리 (`docs/superpowers/plans/2026-04-29-phase-2-webhook-receive.md`)

## Test plan

- [ ] `cd backend && pytest tests/test_plan_parser_service.py tests/test_handoff_parser_service.py -v` — Phase 3 신규 ≥ 29건 pass
- [ ] `cd backend && pytest -v` — Phase 1 (41) + Phase 2 (32) + Phase 3 (≥ 29) 전부 pass, 회귀 0
- [ ] 설계서 §10.1 단위 테스트 항목 매핑 (handoffs/main.md 메모 참조)

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

Expected: PR URL 출력. 사용자 검토 후 머지.

---

## Phase 3 완료 기준 (Acceptance)

- [ ] `app.services.plan_parser_service.parse_plan(text) → ParsedPlan` 정상 동작
- [ ] `## 태스크` 섹션 외부 (`## 노트` 등) 의 체크박스는 무시
- [ ] PLAN 내 `external_id` 중복 시 `DuplicateExternalIdError` raise
- [ ] `app.services.handoff_parser_service.parse_handoff(text) → ParsedHandoff` 정상 동작
- [ ] `# Handoff:` 헤더 없거나 `## YYYY-MM-DD` 섹션 0개 → `MalformedHandoffError` raise
- [ ] `sections` 가 date desc 정렬, sections[0] = 최신 active
- [ ] 들여쓰기 0 인 `- [x]/[ ] task-XXX` 만 `checks` 로, ≥ 2 들여쓰기 체크박스는 `subtasks` 로 (parent = 직전 최상위)
- [ ] `### 마지막 커밋` / `### 다음` / `### 블로커` 자유 영역이 `FreeNotes` 에 raw 보존, 부분 누락 OK
- [ ] Phase 1+2 회귀 0 — 기존 73 테스트 모두 pass
- [ ] PR 생성됨, 사용자 검토 단계로 진입
