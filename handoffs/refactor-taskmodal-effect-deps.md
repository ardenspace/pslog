# Handoff: refactor/taskmodal-effect-deps — @ardensdevspace

## 2026-07-03

  - [ ] TaskModal exhaustive-deps 워닝 2건 해소 (행동 보존, light)

### 결정
- 진단: 워닝 2건 모두 폼 초기화 effect(TaskModal.tsx:104)의 dep 배열 삼항식 한 곳. 수정은 모드별 값을 effect 위에서 변수로 추출해 4-원소 dep 배열로 교체 — create 모드에서 task 는 항상 null, edit 모드에서 currentUserId 는 항상 undefined 라 발화 조건 동치.
- 동작보존 계약: 수정 전 TaskModal 초기화 characterization 3케이스(edit 오픈 로드 / create 오픈 리셋+담당자 기본값 / 다른 task 재오픈 갱신) green 선작성 → 수정 후 동일 green + lint 0 problems.
- 범위 밖: key 리마운트 재구조화, TaskModal 의 다른 구조 정리.
- 리뷰어 지적(PASS-with-nits): ① 계약 테스트가 create 쪽 dep(currentUserId)을 못 핀함 → 4번째 케이스(무관 rerender 유지 / currentUserId 변경 리셋) 추가로 닫음. ② `setAssigneeId(currentUserId ?? null)` 의 `?? null` 은 런타임 도달 불가 dead code 지만 추출 변수 타입(string | undefined) 제약상 불가피 — 그대로 둠. isOpen dep 은 테스트 미핀이나 발화 경로가 실사용상 도달 불가(edit 인스턴스는 isOpen 변화가 항상 task 변화와 동시).

### 다음
- brief DoD 승인 → 구현 → 끝검증(리뷰어 서브에이전트, reviewer-prompt.md)
