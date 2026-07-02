# dashboard-page-split spec — ⑩ DashboardPage 분해   확정일: 2026-07-02

> Track R 리팩토링 (pslog-refactor 진입). 진단·동작보존 계약: `handoffs/refactor-dashboard-page-split.md`

## 1. 배경/문제

`DashboardPage.tsx` 617줄이 6책임(자동선택 effect 2벌, webhook 편집, 모바일 사이드바 75줄, 뷰 스위처 복붙 5회, 빈 상태 복붙 3회, 모달 5개)을 한 컴포넌트에 담음. lint 에러 1건(`set-state-in-effect`, `:78`) 포함. frontend 에 테스트 러너가 없어 안전망 부재.

## 2. 목표 / 비목표

**목표** (= 완료 기준):
- vitest + RTL 도입 (`bun run test`), characterization 5경로가 분해 **전** green → 분해 **후 무수정** green.
- DashboardPage 를 조립 책임만 남기고 (~200줄) 훅 2개 + 컴포넌트 5개로 분해.
- DashboardPage 의 lint 에러 0 (`:78` effect 를 key-리셋 패턴으로 대체 — 동일 semantics).
- `bun run build` green.

**비목표** (YAGNI):
- 하위 컴포넌트/훅/api/스토어 내부 변경 없음. 마크업·클래스·문구 변경 없음.
- 다른 페이지 lint 에러 수정 없음 (별도 lint 묶음 단계).
- CI 에 test/lint 게이트 추가는 lint 묶음 단계에서 일괄.

## 3. 설계(안)

```
src/hooks/useAutoSelectFirst.ts        (신규) 목록 로드 후 유효 선택 보장 — 워크스페이스/프로젝트 공용 (DRY)
src/hooks/useMobileSidebar.ts          (신규) isOpen + 데스크톱 전환 시 닫힘 + 엣지 스와이프 열기
src/components/dashboard/ViewSwitcher.tsx            (신규) VIEW_MODES 배열 map — 버튼 5개 복붙 제거
src/components/dashboard/DashboardHeader.tsx         (신규) 타이틀 + ViewSwitcher + 메뉴/유저/설정/로그아웃
src/components/dashboard/DashboardSidebar.tsx        (신규) 워크스페이스명 + 프로젝트 목록 + 액션 버튼 + WebhookSettings
src/components/dashboard/WebhookSettings.tsx         (신규) webhook 입력/저장 + Discord 리포트 버튼
src/components/dashboard/SelectProjectPlaceholder.tsx (신규) "← 왼쪽에서 프로젝트를 선택하세요" — 복붙 3회 제거
src/pages/DashboardPage.tsx            (수정) 데이터 훅 + 뷰 분기 + 모달 오케스트레이션만 (~200줄)
src/pages/DashboardPage.test.tsx       (신규) characterization 5경로
```

- 상태 소유권: 모달 open 상태·selectedTask 등은 페이지에 존치 (모달이 페이지 레벨 렌더). Sidebar/Header 는 콜백 props.
- `WebhookSettings` 는 `key={project.id + ':' + (project.discord_webhook_url ?? '')}` 로 마운트 리셋 — 기존 effect(`:77-80`)의 리셋 조건(프로젝트 변경/저장 성공)과 동일 semantics, effect 제거로 lint 에러 해소.
- 테스트 전략: `vi.mock('@/services/api')` 로 axios 경계 목킹, QueryClientProvider + MemoryRouter 래핑, zustand 스토어는 테스트 간 리셋. jsdom 환경 (`vitest.config` 는 vite.config 의 test 필드).

## 4. 대안 & 결정 ★ (heavy 트리거 ②)

**분해 단위**:
- A. 훅만 추출 (UI 존치) — 줄수 감소 미미 (~450줄 잔존), 복붙 미해소.
- B. **[채택]** 훅 2 + 표현 컴포넌트 5 — 각 책임 1파일, 복붙 3종 전부 해소, 페이지는 조립만.
- C. 뷰 분기까지 `DashboardMain` 으로 추출 — 조립 책임까지 쪼개져 페이지가 빈 껍데기, props 중계만 늘어남 (props drilling 금지 원칙과 긴장).

**characterization 계층**: 페이지 통합 렌더 (RTL) [채택] — 분해는 내부 경계 재배치라 단위 테스트는 분해 후 구조에 종속됨. 페이지 레벨이어야 전/후 무수정 동치 증명 가능. api 목킹 계층은 `services/api.ts` (axios 인스턴스 경계).

**lint `:78` 처리**: effect 유지(에러 억제) vs key-리셋 [채택] — key-리셋이 React 관용이고 effect 제거로 에러가 구조적으로 해소. 동작보존은 characterization 5(webhook 편집)로 증명.

## 5. 영향/리스크

- 계약 변경 없음 (트리거 ① 해당 없음) — 신규 파일 + 페이지 내부 재배치.
- 리스크: 테스트 인프라 신규 도입 — 목킹 경계가 흔들리면 characterization 자체가 불안정. → 분해 **전** 코드에서 5경로 green 을 먼저 확정(핀 유효성)한 뒤 분해 시작.
- 롤백: 신규 파일 삭제 + DashboardPage 원복. devDependencies 추가는 무해.

## 6. 의존/사인오프

- 대기자 없음. 다음 단계(lint 묶음)가 이 브랜치의 vitest 인프라를 CI 게이트(test+lint)에 얹음.
