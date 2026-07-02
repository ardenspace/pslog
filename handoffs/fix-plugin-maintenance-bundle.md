# Handoff: fix/plugin-maintenance-bundle — @ardensdevspace

## 2026-07-02

- [ ] 플러그인 유지보수 묶음 (유지보수 리뷰 ①②③⑤⑥⑦⑧⑨⑬⑭ — brief: `docs/tasks/plugin-maintenance/brief.md`)

### 진단 (pslog-refactor 진입, light)

- 훅 컨텍스트가 3스킬 중 2개만 안내 (`bin/inject-pslog-context.js`) — refactor 누락.
- matcher `startup|resume` — clear/compact 후 컨텍스트 유실.
- 훅이 node 의존 — 파일 존재 확인 + 고정 JSON 출력뿐이라 POSIX sh로 충분.
- `.claude/skills/*.md` 루스 파일 — SKILL.md 디렉토리 구조가 아니라 하네스가 안 읽는 죽은 파일.
- `.claude/agent-memory/ui-expert/` — agents 정의 없는 잔재.
- 플러그인 README 없음, 버전 0.1.0 고정, cross-skill 참조 경로 암묵적, CI 없음, handoff 아카이빙 규칙 없음.

### 동작보존 계약 (⑥ 훅 sh 전환에만 적용)

- 무게: light
- 동치 증명: PLAN.md 있는 dir / 없는 dir 두 케이스에서 old(node)·new(sh) 훅 실행 → stdout JSON 파싱 결과 동일 구조 확인. (단 additionalContext 텍스트는 ①에 의해 의도적으로 변경 — 구조·발화 조건 동치만 증명.)
- 검증 명령: 수동 diff + `node -e` JSON.parse 스모크

### 결정

- rules 루스 파일은 docs 이동 대신 **진짜 스킬로 변환** (`.claude/skills/<name>/SKILL.md`) — 온디맨드 로드가 상시 CLAUDE.md 참조보다 토큰 효율적. 변환 직후 하네스 스킬 목록에 잡히는 것 확인.
- handoff 아카이빙: 30일 지난 섹션 → `handoffs/archive/` (파서는 `handoffs/` 직하만 읽음).
- CI에서 `bun run lint` 제외 — 기존 코드에 pre-existing 에러 7개 (SettingsPage 등). 정리 후 게이트 추가 예정.
- 버전 0.1.0 → 0.2.0 (plugin.json + marketplace.json 동시).

### 구현 완료 (brief DoD 대비)

- [x] 훅 sh 전환 + 3스킬 안내 + matcher 4종 — 동치 증명: PLAN.md 유/무 두 케이스 old/new 출력 비교, JSON 구조·발화조건 동일
- [x] handoff-format 아카이빙 규칙 / cross-skill 경로 명시 (refactor SKILL.md, decompose.md)
- [x] 플러그인 README + 루트 README 마켓플레이스 섹션
- [x] CI 워크플로 (backend pytest + frontend build + plugin 검증)
- [x] rules 스킬 변환, agent-memory 삭제, .pytest_cache gitignore
- [x] backend pytest 349 passed + pre-existing 실패 2건 원인 규명·수정 (사용자 승인) → 해당 파일 41 passed
  - env 의존: test_git_settings_endpoint — settings.pslog_public_url monkeypatch
  - 시한폭탄: test_log_query_service — 고정 날짜 → utcnow() 상대 시각 (파티션 +30일 윈도)
- [x] 결정 3건 DECISIONS.md 승격 (훅 POSIX sh / rules→스킬 / 테스트 고정날짜 금지)

### 다음

- CI green 확인 (PR에서). 이후 Track R (DashboardPage/git_settings/sync_service 리팩토링, heavy) + lint 에러 7건 정리 후 CI lint 게이트 추가.
