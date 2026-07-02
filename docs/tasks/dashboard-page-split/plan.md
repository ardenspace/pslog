# dashboard-page-split plan   (spec: ./spec.md)

아키텍처 한 줄: characterization 을 먼저 분해 *전* 코드에 green 으로 박은 뒤, 훅 2개 + 표현 컴포넌트 5개를 추출하고 같은 테스트로 동치 증명.

파일:
- 신규 `frontend/vitest` 인프라 (devDeps + vite.config test 필드 + `src/test/setup.ts`)
- 신규 `src/pages/DashboardPage.test.tsx` (characterization 5경로)
- 신규 `src/hooks/useAutoSelectFirst.ts`, `src/hooks/useMobileSidebar.ts`
- 신규 `src/components/dashboard/` — ViewSwitcher / DashboardHeader / DashboardSidebar / WebhookSettings / SelectProjectPlaceholder
- 수정 `src/pages/DashboardPage.tsx`, `frontend/package.json`

## Step 분해

- [ ] Step 1 — 테스트 인프라: vitest/jsdom/RTL/user-event/jest-dom 설치, vite.config `test` 설정, `bun run test` 스크립트, 스모크 테스트 1개로 러너 확인.
  - 검증: `bun run test` green + `bun run build` green (tsc가 테스트 파일 포함해도 깨지지 않는지)
- [ ] Step 2 — characterization 5경로 작성, **분해 전 코드에서 green 확정** (핀 유효성).
  - 검증: `bun run test` 5+1 green
- [ ] Step 3 — 훅 추출: `useAutoSelectFirst` (워크스페이스/프로젝트 effect 2벌 통합), `useMobileSidebar` (미디어쿼리+스와이프 75줄).
  - 검증: `bun run test` 무수정 green + build green
- [ ] Step 4 — 표현 컴포넌트 추출: SelectProjectPlaceholder → ViewSwitcher → DashboardHeader → WebhookSettings(key-리셋, effect 제거) → DashboardSidebar.
  - 검증: 동일 (컴포넌트 하나 옮길 때마다 test+build)
- [ ] Step 5 — DashboardPage 최종 정리 (조립만, ~200줄) + lint 확인 (DashboardPage 에러 0).
  - 검증: `bun run test` + `bun run build` + `bun run lint 2>&1 | grep DashboardPage` 0건

롤백: 신규 파일 삭제 + DashboardPage 원복. devDeps 는 무해.
