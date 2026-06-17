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


class Decision(BaseModel):
    """`### 결정` 서브섹션의 한 항목 — 구현 중 기획과 달라진 결정.

    형식: `- [task-NNN] <무엇 바꿈> — <왜> → DECISIONS|ADR-NNN`
    """

    model_config = ConfigDict(extra="forbid")

    external_id: str | None    # "task-001" — 없을 수도 있음(브랜치 전체 결정)
    text: str                  # 마커 제외 본문
    promoted: bool             # `→ DECISIONS` / `→ ADR` 마커 존재 여부


class HandoffSection(BaseModel):
    """`## YYYY-MM-DD` 한 섹션."""

    model_config = ConfigDict(extra="forbid")

    date: str                                    # "2026-04-26" (검증된 ISO 날짜 형식)
    checks: list[CheckItem] = Field(default_factory=list)
    subtasks: list[Subtask] = Field(default_factory=list)
    free_notes: FreeNotes = Field(default_factory=FreeNotes)
    decisions: list[Decision] = Field(default_factory=list)


class ParsedHandoff(BaseModel):
    """handoff 파일 전체 파싱 결과 — sections[0] 이 최신(active)."""

    model_config = ConfigDict(extra="forbid")

    branch: str                # "feature/login-redesign" — `/` 보존
    author_git_login: str      # "alice"
    sections: list[HandoffSection] = Field(default_factory=list)  # date desc
