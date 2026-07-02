# lint-gate brief — 잔여 lint 에러 정리 + CI 게이트

- 왜: CI frontend job 에 lint 게이트 부재 — pre-existing 에러 때문에 보류돼 있었음 (Track R 마지막 항목). ⑩에서 DashboardPage 건 해소 → 잔여 6건.
- 무엇:
  - `useAuth.ts:50` no-empty → 빈 catch 에 의도 주석 (logout API 실패 무시가 원래 의도)
  - `button.tsx:56` / `WeekView.tsx:169` react-refresh only-export → `buttonVariants` / `getMonday` 를 별도 파일로 분리 (import 경로만 갱신)
  - `SettingsPage:19` / `CustomSelect:32` / `DatePicker:37` set-state-in-effect → 각 코드 진단 후 파생/초기값/key 중 행동 보존이 증명 가능한 방식 (⑩의 draft 패턴 참고)
  - CI frontend job 에 `bun run lint` + `bun run test` 추가
  - (out: TaskModal exhaustive-deps 워닝 2건 — 행동 변경 위험, 범위 밖)
- 완료조건(DoD): ☐ `bun run lint` 에러 0 ☐ `bun run test` green ☐ `bun run build` green ☐ CI 4게이트(backend/frontend build·lint·test/plugin) green
- 영향파일: `useAuth.ts`, `button.tsx`(+신규 variants 파일), `WeekView.tsx`(+신규 유틸), `SettingsPage.tsx`, `CustomSelect.tsx`, `DatePicker.tsx`, `.github/workflows/ci.yml`
- 검증: `cd frontend && bun run lint && bun run test && bun run build` + PR CI
- 전제: vitest 인프라(#36)와 DashboardPage lint 해소(#36)가 main 에 있어야 CI 게이트가 성립 → **#36 머지 후 main 에서 브랜치**
