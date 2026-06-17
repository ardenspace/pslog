# 분해 규칙 — 실행계획 → PLAN.md

## 골디락스
- 1 task = **0.5~3일** 분량. 너무 작으면(시간 단위) 합치고, 크면(주 단위) 쪼갠다.

## task 줄 포맷 (PLAN.md `## 태스크` 아래)
`- [ ] [task-NNN] <제목> (E#) — @assignee — `영향파일``
- `[task-NNN]` 프로젝트 내 unique.
- `(E#)` — 실행계획 §5 lock-in 백링크 (해당 task 가 그 결정을 구현할 때).
- `@assignee` — 역할 담당.
- backtick 영향파일.
- heavy task 면 `(deep)` 마커도 (부모 무게 게이트 — pslog-workflow `references/weight-gate.md`).

## lane
- 병렬 가능 그룹을 PLAN 노트에 `병렬 가능: lane A (task-001/002) …` 로. 실행계획 §8 의존성에서 도출.

## 기록 (C4)
- PLAN.md 수정은 **제안 후 사용자 확인** (자동 수정 금지). 기존 `weight-gate.md` 규약과 동일.
- 작성 후 PLAN.md 최상단에 실행계획 링크 + 역할 줄.
