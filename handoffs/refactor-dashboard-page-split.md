# Handoff: refactor/dashboard-page-split — @ardensdevspace

## 2026-07-02

- [ ] ⑩ DashboardPage 617줄 분해 (Track R, heavy, pslog-refactor 진입)

### 진단 (pslog-refactor)

- `frontend/src/pages/DashboardPage.tsx` (617줄) — 한 컴포넌트 6책임:
  - 워크스페이스/프로젝트 자동선택 effect 2벌 (`:40`, `:111`) — 구조 완전 평행 (DRY 위반)
  - webhook URL 편집 상태 + 저장 (`:74-90`) — lint 에러 `react-hooks/set-state-in-effect` (`:78`)
  - 모바일 사이드바 열림/미디어쿼리/엣지 스와이프 (`:140-215`, 75줄)
  - 뷰모드 스위처 버튼 5개 복붙 (`:337-386`) — 동일 클래스 문자열 5회
  - "프로젝트를 선택하세요" 빈 상태 블록 3회 복붙
  - 모달 5개 오케스트레이션
- 영향 범위: frontend 안에서 닫힘 (추출물은 신규 파일). 무게: heavy (트리거 ② 설계 분기 — 분해 단위 + 테스트 인프라 도입 결정)
- **frontend 테스트 러너 부재** — characterization 을 위해 vitest+RTL 도입 (사용자 승인 완료)

### 동작보존 계약 — DashboardPage

- 무게: heavy
- 동치 증명: **신규 characterization 테스트** (`src/pages/DashboardPage.test.tsx`) 가 분해 전 코드에서 green → 분해 후 **무수정** green + `bun run build` green
- characterization 범위 (분해 전 행동 핀고정, 5경로):
  1. 로드 후 첫 워크스페이스/프로젝트 자동 선택 + 사이드바 목록 렌더
  2. 프로젝트 0개 → "프로젝트 없음" + 선택 placeholder
  3. 뷰 전환 Board→Table/Week/Errors/Drift — 각 뷰 렌더
  4. 역할 게이트: viewer 는 공유링크/멤버/webhook 비노출, owner 는 노출
  5. webhook 입력 변경 → 저장 버튼 노출
- 검증 명령: `cd frontend && bun run test` (vitest run) + `bun run build`

### 경계 (안 건드릴 것)

- 하위 컴포넌트 (KanbanBoard/WeekView/ErrorsList/DriftsList/TaskModal 등) 내부 불변
- hooks (useTasks/useProjects/…) / services/api.ts / 스토어 로직 불변
- 렌더 결과 (마크업 구조·클래스·문구) 불변 — 단 lint 에러 `:78` 의 effect 는 key-리셋 패턴으로 대체 (동일 리셋 semantics, characterization 으로 증명)
- SettingsPage 등 다른 페이지의 lint 에러는 별도 단계 (lint 묶음)
