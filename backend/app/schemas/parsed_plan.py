"""PLAN.md 파서 출력 모델.

설계서: 2026-04-26-ai-task-automation-design.md §6.1
"""

from pydantic import BaseModel, ConfigDict, Field


class ParsedTask(BaseModel):
    r"""PLAN.md 의 한 체크박스 라인 — `- [ ] [task-XXX] <title> — @<user> — \`<path>\`...`"""

    model_config = ConfigDict(extra="forbid")

    external_id: str           # "task-001" — 프로젝트 내 unique
    title: str                 # "로그인 UI 리뉴얼"
    checked: bool              # PLAN 자체의 [x]/[ ]
    assignee: str | None       # "alice" 또는 None
    paths: list[str] = Field(default_factory=list)  # backtick 으로 감싼 파일/디렉토리
    deep: bool = False         # PLAN 라인에 (deep) 마커가 있으면 True (무게: 깊은 준비 필요)


class ParsedPlan(BaseModel):
    """PLAN.md 전체 파싱 결과."""

    model_config = ConfigDict(extra="forbid")

    sprint_name: str | None    # "# 스프린트: <이름>" 헤더에서 추출. 없으면 None
    tasks: list[ParsedTask] = Field(default_factory=list)
