# plugin-maintenance brief — 플러그인/하네스 유지보수 묶음

- 왜: 2026-07-02 유지보수 리뷰 — 배포된 플러그인 사용자에게 직접 영향 있는 훅/문서/설정 이슈 일괄 수정.
- 무엇 (리뷰 번호):
  1. **① 훅 스킬 안내 보완** — SessionStart 컨텍스트에 pslog-refactor 라우팅 추가 (3스킬 전부 안내).
  2. **② matcher 확장** — `startup|resume` → `startup|resume|clear|compact`.
  3. **⑥ 훅 node → POSIX sh** — `bin/inject-pslog-context.sh` 재작성, node 런타임 의존 제거. 발화 조건(PLAN.md 존재)·JSON 구조 동치.
  4. **⑤ handoff 아카이빙 규칙** — `handoff-format.md`에 "30일 지난 날짜 섹션은 `handoffs/archive/{branch}.md`로 이동" 규칙 추가.
  5. **⑦ 플러그인 README** — `plugins/pslog-workflow/README.md` 신설 (설치·3스킬·훅 동작) + 루트 README에 마켓플레이스 섹션.
  6. **⑧ 버전 범프** — plugin.json·marketplace.json `0.1.0` → `0.2.0`.
  7. **⑨ cross-skill 경로 명시** — pslog-refactor SKILL.md의 `pslog-workflow` references 참조를 `../pslog-workflow/references/…` 명시 경로로.
  8. **⑬ CI** — `.github/workflows/ci.yml`: backend pytest(testcontainers) + frontend `bun run build` + plugin/marketplace JSON 유효성 검사.
  9. **③ 죽은 rules 파일 → 진짜 스킬로** — `.claude/skills/{frontend,backend}-rules.md` 를 `.claude/skills/<name>/SKILL.md` 디렉토리 구조 + frontmatter(description 트리거)로 변환 — 코드 작성 시 온디맨드 로드되게.
  10. **⑭ 잔재 정리** — `.claude/agent-memory/` 삭제, `.pytest_cache/` gitignore 추가.
- out (안 건드림): Track R(⑩⑪⑫ DashboardPage/git_settings/sync_service), ④ PLAN.md 도그푸딩, 스킬 본문의 워크플로 로직·멈춤 규칙.
- 완료조건(DoD):
  - ☐ 훅: PLAN.md 있는 dir에서 sh 훅 실행 → 유효 JSON + 3스킬 언급 / 없는 dir → 출력 없음, exit 0
  - ☐ hooks.json이 sh 스크립트 가리키고 matcher 4종, `node` 의존 잔존 0
  - ☐ 모든 JSON(plugin/marketplace/hooks) 파싱 통과 + 버전 0.2.0
  - ☐ rules 스킬 2개가 SKILL.md 구조 + 유효 frontmatter, 루스 .md 삭제
  - ☐ frontend build green (로컬) / backend pytest green (CI에서 — 로컬은 기존 상태 유지 확인만)
  - ☐ CI 워크플로가 이 PR에서 green
- 영향파일: `plugins/pslog-workflow/bin/*`, `plugins/pslog-workflow/hooks/hooks.json`, `plugins/pslog-workflow/.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json`, `plugins/pslog-workflow/skills/*/SKILL.md`, `plugins/pslog-workflow/skills/pslog-workflow/references/handoff-format.md`, `plugins/pslog-workflow/README.md`(신규), `README.md`, `.github/workflows/ci.yml`(신규), `.claude/skills/*`, `.claude/agent-memory/`(삭제), `.gitignore`
- 검증: 위 DoD 명령 + 훅 before/after 출력 diff
