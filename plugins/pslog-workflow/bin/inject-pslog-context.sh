#!/bin/sh
# pslog-workflow SessionStart hook.
# pslog 관리 프로젝트(repo 루트에 PLAN.md 존재)에서만 트리거를 context에 주입한다.
# 아니면 아무것도 출력하지 않음(다른 프로젝트에서 무해). 파일은 절대 수정하지 않는다(read-only).
# POSIX sh — node 등 외부 런타임 의존 없음.

project_dir="${CLAUDE_PROJECT_DIR:-$PWD}"

[ -f "$project_dir/PLAN.md" ] || exit 0

printf '%s' '{"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":"이 프로젝트는 pslog-workflow 플러그인으로 관리된다. 세 스킬을 쓴다: (1) 새 feature 아이디어/기획에 들어가면 → pslog-planning 스킬(/pslog-workflow:pslog-planning) — 5렌즈 기획 → 실행계획.md → PLAN.md 분해. (2) PLAN.md의 task 하나를 잡아 코드로 옮기거나, 행동을 *바꾸는* 버그픽스/기능변경이면 → pslog-workflow 스킬(/pslog-workflow:pslog-workflow) — 무게 게이트(brief vs spec→plan) → 코드 → 검증. (3) 행동은 그대로 두고 구조만 바꾸는 리팩토링/정리면 → pslog-refactor 스킬(/pslog-workflow:pslog-refactor) — 진단 → 범위 확정 → 동작보존 계약 → pslog-workflow로 코드화. 각 단계 사람 승인 흐름을 따른다."}}'

exit 0
