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


def test_parse_handoff_unknown_h3_does_not_leak_checkboxes_into_checks():
    """스펙 외 ### 헤더 아래 체크박스가 최상위 checks 로 leak되면 안 됨 (code review I-1)."""
    text = """# Handoff: main — @x

## 2026-04-29

- [x] task-001
- [ ] task-002

### 알 수 없는 사용자 헤더

- [x] task-999

### 다음

내일
"""
    h = parse_handoff(text)
    ids = [c.external_id for c in h.sections[0].checks]
    assert ids == ["task-001", "task-002"]
    assert "task-999" not in ids
    # 자유 영역은 정상 동작 — `### 다음` 은 알려진 헤더이므로 채집됨
    assert h.sections[0].free_notes.next == "내일"


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


def test_parse_handoff_decisions_does_not_leak_into_checks():
    """### 결정 아래 `- [task-NNN]` 라인이 최상위 checks 로 leak 되면 안 됨."""
    text = """# Handoff: feat/x — @alice

## 2026-06-14
- [x] task-001

### 결정
- [task-002] 뭔가 바꿈 — 이유
"""
    h = parse_handoff(text)
    ids = [c.external_id for c in h.sections[0].checks]
    assert ids == ["task-001"]
    assert "task-002" not in ids
