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
# title delimiter — ` — ` 다음에 `@` 또는 `` ` `` 가 와야 진짜 delimiter (code review I-2/I-3)
_TITLE_DELIMITER_RE = re.compile(r" — (?=@|`)")


def _parse_task_rest(rest: str) -> tuple[str, str | None, list[str]]:
    r"""`<title> — @user — `path`, `path`` → (title, assignee, paths).

    Phase 3 code review I-2/I-3 fix: positional 파싱.
    - title 은 첫 ` — @` 또는 ` — ` ` 이전까지 (em-dash + backtick/at 조합만 진짜 delimiter)
    - title 영역에 단독 ` — ` 또는 백틱/@ 있어도 truncate 안 함
    - assignee/path 는 title 영역 이후에서만 검색
    """
    delim = _TITLE_DELIMITER_RE.search(rest)
    if delim is None:
        return rest.strip(), None, []

    title = rest[: delim.start()].strip()
    after = rest[delim.start():]
    assignee_match = _ASSIGNEE_RE.search(after)
    assignee = assignee_match.group(1) if assignee_match else None
    paths = _PATH_RE.findall(after)
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
        deep = bool(re.search(r"\(deep\)", m.group("rest")))
        tasks.append(
            ParsedTask(
                external_id=external_id,
                title=title,
                checked=m.group("check").lower() == "x",
                assignee=assignee,
                paths=paths,
                deep=deep,
            )
        )

    return ParsedPlan(sprint_name=sprint_name, tasks=tasks)
