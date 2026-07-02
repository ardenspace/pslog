# Handoff: refactor/git-settings-service — @ardensdevspace

## 2026-07-02

- [ ] ⑪ git_settings 라우터 → 서비스 레이어 (Track R, heavy, pslog-refactor 진입)

### 진단 (pslog-refactor)

- `app/api/v1/endpoints/git_settings.py` (341줄) — CLAUDE.md "라우터에 비즈니스 로직 금지" 위반:
  - `register_webhook` (:146-214) 비즈니스 로직 69줄 — PAT 복호화, hook 목록 조회/URL 매칭, create vs update 분기, secret 재생성, row lock.
  - `list_handoffs` / `list_git_events` — select 쿼리 조립 + 스키마 매핑이 라우터에 직접.
  - `reprocess_git_event` — in-flight 409 / already-succeeded 400 상태 전이 판정이 라우터에.
  - `patch_git_settings` / `reset_discord` — 필드 반영/카운터 리셋 로직이 라우터에.
- 영향 범위: API shape·경로·권한 디펜던시 불변. 신규 `services/git_settings_service.py` 1개 추가로 닫힘.
- 무게: heavy (트리거 ② 설계 분기 — 에러 매핑을 서비스/라우터 어디서 할지)

### 동작보존 계약 — git_settings

- 무게: heavy
- 동치 증명: `tests/test_git_settings_endpoint.py` 30 케이스 **무수정** before/after green
- characterization 신규: 없음 — 기존 엔드포인트 테스트 30개가 HTTP 경계에서 행동을 이미 핀고정
- before 기준선: 30 passed (2026-07-02, venv 재생성 후)
- 검증 명령: `cd backend && ./venv/bin/pytest tests/test_git_settings_endpoint.py -q`

### 결정

- 라우터에 `from app.config import settings` 를 `noqa: F401` 로 유지 — 테스트(`test_post_webhook_updates_existing_hook`)가 라우터 모듈 경로로 `settings.pslog_public_url` 을 monkeypatch. 테스트 무수정 계약 우선. (import 제거 시 이 경로가 사라져 fail — 전체 suite 에서 발견.)
- 끝검증에 **독립 리뷰어 서브에이전트** 최초 적용 (superpowers 방식 부분 도입) — 세션 히스토리 없이 spec/plan/diff 파일 + 동작보존 렌즈만 전달. 판정: 동치 PASS, Critical/Important 0, Minor 4 (상수 위치는 즉시 수정, logger 이름 변경·`get_reprocessable_event` 명명·B904 는 기록만).

### 구현 완료

- [x] Step 1~5 전부 — `services/git_settings_service.py` 신설 (도메인 예외 5종 + 함수 7개), 라우터는 배선+응답조립+예외→HTTP 매핑만 (341줄 → 240줄대)
- [x] 동치 증명: `test_git_settings_endpoint.py` 30개 무수정 green (매 Step + 최종), 전체 suite 351 passed
- [x] 독립 리뷰어 서브에이전트 리뷰 통과 (머지 가능 판정)

### 경계 (안 건드릴 것)

- API 요청/응답 스키마 (`schemas/git_settings.py`) 불변
- 라우터 경로/권한 디펜던시 (`require_project_member` 구성) 불변
- `webhooks._run_sync_in_new_session` BackgroundTask 참조 방식 불변 (테스트 monkeypatch 의존)
- `github_hook_service` / `project_service` / `crypto` 시그니처 불변
