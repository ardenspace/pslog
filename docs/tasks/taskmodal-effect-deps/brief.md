# taskmodal-effect-deps brief — exhaustive-deps 워닝 2건 해소

- 왜: lint-gate(#37)에서 범위 밖으로 남긴 마지막 lint 워닝 — frontend lint 를 0 problems 로.
- 무엇:
  - `TaskModal.tsx:104` dep 배열의 삼항식 → effect 위에서 모드별 변수 추출로 교체:
    `currentUserId = isCreateMode ? props.currentUserId : undefined` / `task = isCreateMode ? null : props.task` (기존 108줄 `task` 선언을 위로 이동), deps `[isOpen, isCreateMode, currentUserId, task]`
  - 동치 논거: create 모드에서 `task` 불변(null), edit 모드에서 `currentUserId` 불변(undefined) → 발화 조건이 기존 삼항 dep 과 동일. eslint-disable 불사용.
  - 계약: 수정 **전** `TaskModal.test.tsx` characterization 3케이스 green 선작성 — ① edit 오픈 시 task 값 로드 ② create 오픈 시 리셋 + 담당자=currentUserId ③ 다른 task 로 재오픈 시 갱신
  - (out: key 리마운트 재구조화, TaskModal 기타 정리)
- 완료조건(DoD): ☐ characterization 3케이스 수정 전 green ☐ 수정 후 동일 green ☐ `bun run lint` **0 problems** (에러·워닝 모두 0) ☐ `bun run test` green ☐ `bun run build` green ☐ CI green
- 영향파일: `frontend/src/components/board/TaskModal.tsx`, `frontend/src/components/board/TaskModal.test.tsx`(신규)
- 검증: `cd frontend && bun run lint && bun run test && bun run build` + PR CI
