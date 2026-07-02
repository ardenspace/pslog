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

### 결정

- webhook 편집 상태는 **페이지 소유 유지** — 사이드바가 데스크톱+모바일 2벌 렌더되고 두 인스턴스가 상태를 공유하는 게 현 행동 (characterization 5 가 핀). 컴포넌트 내부 state 로 내리면 동치가 깨짐.
- lint `:78` 해소는 key-리셋 대신 **프로젝트별 draft 파생 상태** — key 는 컴포넌트 소유 state 전제라 위 결정과 충돌. draft `{projectId, value, saved}` 로 effect 없이 기존 리셋 semantics (프로젝트 전환/저장 성공) 재현. spec §4 의 key-리셋 채택을 구현 중 대체.
- 독립 리뷰 Important 반영: 원본 effect 의 리셋 트리거는 3종 (프로젝트 전환 / 저장 성공 refetch / **서버 값 외부 변경**) — draft 에 `baseUrl`(생성 시점 서버 값)을 넣어 서버 값이 바뀌면 draft 자동 무효화. characterization 6·7(전환 리셋 / 저장 성공) + Table 뷰 핀 추가, 8개 전부 **원본 페이지에서도 green** 재확인 (핀 유효성).
- 재검증 리뷰(2차) 반영: baseUrl 분기가 핀되지 않음을 mutation test 로 실증 → 테스트 8(편집 중 외부 변경 리셋, queryClient invalidate 로 refetch 유발) 추가. mutation(조건 제거) 시 테스트 8 이 실패함을 확인 — 핀 실효성 증명.
- 알려진 사각: auto-select 경유 프로젝트 전환은 draft 명시 리셋 경로를 안 탐 (baseUrl 무효화가 방어) / matchMedia 스텁이 `matches:false` 고정이라 데스크톱 전환·스와이프 경로는 테스트 밖 (jsdom 한계) / ABA(서버 값 A→B→A 왕복 시 무효화된 draft 부활) — 원본과 어긋나는 이론적 Low 케이스, 발현 조건이 비현실적이라 문서화로 수용.
- Node 22+ 내장 localStorage 전역이 jsdom 을 가림 (`--localstorage-file` 미설정) → test setup 에 인메모리 Storage 스텁.
- characterization 은 사이드바 2벌 렌더로 인한 **중복 매치 개수 자체를 핀**으로 사용 (프로젝트명 3회 = 사이드바 2 + BoardHeader 1).

### 구현 완료

- [x] vitest+RTL 인프라 (`bun run test`) + characterization 5경로 — 분해 전 green 선확정
- [x] 훅 2개 (useAutoSelectFirst — 평행 effect 2벌 통합 / useMobileSidebar — 스와이프 75줄)
- [x] 컴포넌트 5개 (ViewSwitcher/DashboardHeader/DashboardSidebar/WebhookSettings/SelectProjectPlaceholder) + types/view.ts
- [x] DashboardPage 617줄 → 350줄, lint 에러 0 (`set-state-in-effect` 해소)
- [x] 동치 증명: characterization 무수정 green + build green

### 경계 (안 건드릴 것)

- 하위 컴포넌트 (KanbanBoard/WeekView/ErrorsList/DriftsList/TaskModal 등) 내부 불변
- hooks (useTasks/useProjects/…) / services/api.ts / 스토어 로직 불변
- 렌더 결과 (마크업 구조·클래스·문구) 불변 — 단 lint 에러 `:78` 의 effect 는 key-리셋 패턴으로 대체 (동일 리셋 semantics, characterization 으로 증명)
- SettingsPage 등 다른 페이지의 lint 에러는 별도 단계 (lint 묶음)
