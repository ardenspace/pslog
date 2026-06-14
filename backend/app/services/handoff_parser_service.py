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
    Decision,
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
    "결정": "decisions",
}
_FREE_NOTE_HEADER_RE = re.compile(r"^###\s+(?P<name>.+?)\s*$")
_DECISION_LINE_RE = re.compile(
    r"^-\s+(?:\[(?P<id>task-[A-Za-z0-9_-]+)\]\s+)?(?P<body>.+?)\s*$"
)
_PROMOTED_RE = re.compile(r"→\s*(DECISIONS|ADR(?:-[A-Za-z0-9_]+)?)\s*$")
_TOP_CHECK_RE = re.compile(
    r"^-\s+\[(?P<check>[ xX])\]\s+(?P<id>task-[A-Za-z0-9_-]+)\s*(?P<extra>.*?)\s*$"
)
_SUB_CHECK_RE = re.compile(
    r"^(?P<indent>(?:    |\t|  )+)-\s+\[(?P<check>[ xX])\]\s+(?P<text>.+?)\s*$"
)


def _parse_section_body(
    body_lines: list[str],
) -> tuple[list[CheckItem], list[Subtask], FreeNotes, list[Decision]]:
    """한 날짜 섹션의 본문 라인들 → (checks, subtasks, free_notes).

    체크박스 / 서브태스크 는 첫 ### 헤더 등장 전까지 만 추출.
    `### 마지막 커밋` / `### 다음` / `### 블로커` 안의 텍스트는 다음 ### 또는 끝까지 모음.
    그 외 `### 헤더` (스펙에 없는 사용자 임의 헤더) 는 자유 영역 수집을 일시 정지하지만
    체크박스로의 복귀는 막는다 — H3 영역 이후 라인은 모두 무시.
    """
    checks: list[CheckItem] = []
    subtasks: list[Subtask] = []
    last_top_id: str | None = None

    free_notes_raw: dict[str, list[str]] = {
        "last_commit": [], "next": [], "blockers": [], "decisions": [],
    }
    current_free_key: str | None = None
    # 첫 ### 헤더 등장 후 True — 체크박스 영역으로의 복귀 차단 (스펙: 체크박스는 ### 등장 전까지만).
    in_h3_zone = False

    for raw in body_lines:
        # HR 구분선 (`---`) 은 자유 영역 수집을 종료하고 이후 라인을 무시
        if raw.strip() == "---":
            current_free_key = None
            continue

        h3 = _FREE_NOTE_HEADER_RE.match(raw)
        if h3:
            in_h3_zone = True
            name = h3.group("name").strip()
            current_free_key = _FREE_NOTE_HEADERS.get(name)
            continue

        if current_free_key is not None:
            free_notes_raw[current_free_key].append(raw)
            continue

        # ### 영역 진입 후엔 체크박스 매칭 차단 — 알 수 없는 H3 아래 체크박스가 leak되지 않게.
        if in_h3_zone:
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

    return checks, subtasks, free_notes, decisions


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

    sections = []
    for date, body in section_blocks:
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
    sections.sort(key=lambda s: s.date, reverse=True)

    return ParsedHandoff(branch=branch, author_git_login=author, sections=sections)
