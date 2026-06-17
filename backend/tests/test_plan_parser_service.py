"""plan_parser_service — PLAN.md 파싱 단위 테스트.

설계서: 2026-04-26-ai-task-automation-design.md §6.1, §10.1
"""

from pathlib import Path

import pytest

from app.services.plan_parser_service import (
    DuplicateExternalIdError,
    parse_plan,
)


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


def test_parse_plan_title_preserves_em_dash():
    """code review I-2: title 안의 ' — ' 이 truncate 되지 않아야."""
    text = """## 태스크

- [ ] [task-001] 로그인 — 1단계 — @alice — `frontend/Login.tsx`
"""
    plan = parse_plan(text)
    t = plan.tasks[0]
    assert t.title == "로그인 — 1단계"
    assert t.assignee == "alice"
    assert t.paths == ["frontend/Login.tsx"]


def test_parse_plan_title_with_backtick_in_text_does_not_become_path():
    """code review I-3: 백틱이 title 영역에 있으면 path 로 추출되지 않아야."""
    text = """## 태스크

- [ ] [task-002] Use `helper()` for stuff — @bob — `backend/main.py`
"""
    plan = parse_plan(text)
    t = plan.tasks[0]
    assert t.title == "Use `helper()` for stuff"
    assert t.assignee == "bob"
    assert t.paths == ["backend/main.py"]


def test_parse_plan_title_with_at_in_text_does_not_become_assignee():
    """title 안의 '@' 가 assignee 로 잘못 추출되지 않아야."""
    text = """## 태스크

- [ ] [task-003] Mention @bot in docs — @real_user
"""
    plan = parse_plan(text)
    t = plan.tasks[0]
    assert t.assignee == "real_user"
    assert "@bot" in t.title


def test_parse_plan_detects_deep_marker():
    text = "## 태스크\n- [ ] [task-007] (deep) 결제 재시도 — @me — `x.py`\n- [ ] [task-008] 오타 — @me\n"
    parsed = parse_plan(text)
    by_id = {t.external_id: t for t in parsed.tasks}
    assert by_id["task-007"].deep is True
    assert by_id["task-008"].deep is False
