# git-settings-service plan   (spec: ./spec.md)

아키텍처 한 줄: 라우터의 비즈니스 로직을 `git_settings_service` 모듈 함수로 이동, 라우터는 배선+응답조립+예외→HTTP 매핑만.

파일:
- 신규 `backend/app/services/git_settings_service.py`
- 수정 `backend/app/api/v1/endpoints/git_settings.py`

## Step 분해

- [ ] Step 1 — 서비스 모듈 신설: 도메인 예외 6종 + `public_webhook_url` + 읽기 경로 2개(`list_handoffs`, `list_git_events`) 이동. 라우터의 두 GET이 서비스 호출로 전환.
  - 검증: `./venv/bin/pytest tests/test_git_settings_endpoint.py -q` (30 green)
- [ ] Step 2 — 쓰기 경로 이동: `update_settings`, `reset_discord`. PATCH/discord-reset 라우터 전환.
  - 검증: 동일 명령 30 green
- [ ] Step 3 — `register_webhook` 이동 (row lock → 검증 → GitHub 분기 → secret 저장 + commit, `WebhookResult` NamedTuple 반환). 라우터는 예외→400/500 매핑 + 응답 조립.
  - 검증: 동일 명령 (동시성 `test_concurrent_register_webhook_serializes` 포함 green)
- [ ] Step 4 — `get_reprocessable_event` 이동 (404/409/400 판정 + 상태 리셋 + commit). 라우터는 예외 매핑 + BackgroundTask 등록 유지.
  - 검증: 동일 명령 30 green
- [ ] Step 5 — 라우터 정리: 미사용 import 제거, 최종 전체 확인.
  - 검증: `./venv/bin/pytest -q` 전체 suite green

롤백: 각 Step은 파일 2개 diff — `git checkout -- <files>` 원복. DB/스키마 무변경.
