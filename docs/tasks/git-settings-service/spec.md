# git-settings-service spec — ⑪ git_settings 라우터 → 서비스 레이어   확정일: 2026-07-02

> Track R 리팩토링 (pslog-refactor 진입). 진단·동작보존 계약: `handoffs/refactor-git-settings-service.md`

## 1. 배경/문제

`app/api/v1/endpoints/git_settings.py` (341줄)가 CLAUDE.md "라우터에 비즈니스 로직 금지"를 위반:

- `register_webhook`: PAT 복호화 → hook 목록 조회/URL 매칭 → create/update 분기 → secret 재생성·저장까지 69줄이 라우터에.
- `list_handoffs` / `list_git_events`: select 쿼리 조립 + limit clamp가 라우터에.
- `reprocess_git_event`: in-flight(409) / already-succeeded(400) 상태 전이 판정이 라우터에.
- `patch_git_settings` / `reset_discord`: 필드 반영·PAT 암호화·카운터 리셋이 라우터에.

## 2. 목표 / 비목표

**목표** (= 완료 기준):
- 신규 `app/services/git_settings_service.py`로 위 비즈니스 로직 전부 이동. 라우터는 Depends 배선 + 서비스 호출 + 응답 조립 + 예외→HTTP 매핑만.
- `tests/test_git_settings_endpoint.py` 30케이스 **무수정** green (동작보존 계약).
- API 스키마/경로/권한/상태코드/detail 메시지 전부 불변.

**비목표** (YAGNI):
- 공용 `utils/exceptions.py` 신설 안 함 — 도메인 예외는 서비스 모듈 안에만 (필요해지면 그때 승격).
- 응답 포맷 표준화(`{"data": ...}`)로의 전환 안 함 — 행동 변경임.
- webhooks.py / sync_service 는 안 건드림 (⑫ 별도).

## 3. 설계(안)

```
app/services/git_settings_service.py   (신규)
├── public_webhook_url() -> str                          # settings 기반 콜백 URL
├── update_settings(db, project, data: dict) -> None     # 필드 반영 + PAT encrypt + commit/refresh
├── reset_discord(db, project) -> None                   # counter 0 + disabled_at NULL + commit/refresh
├── register_webhook(db, project) -> WebhookResult       # row lock → 검증 → GitHub create/update → secret 저장 + commit
├── list_handoffs(db, project_id, branch, limit) -> Sequence[Handoff]      # clamp + 쿼리
├── list_git_events(db, project_id, failed_only, limit) -> Sequence[GitPushEvent]
└── get_reprocessable_event(db, project_id, event_id) -> GitPushEvent      # 404/409/400 판정 + 상태 리셋 + commit
```

- `WebhookResult`: `(hook_id, was_existing, callback_url)` NamedTuple — 라우터가 `WebhookRegisterResponse` 조립.
- 도메인 예외 (서비스 모듈 내 정의):
  - `WebhookConfigError(msg)` → 라우터에서 400 (`git_repo_url 미설정` / `GitHub PAT 미설정`)
  - `PatDecryptError` → 500 (`PAT 복호화 실패`)
  - `EventNotFoundError` → 404, `EventInFlightError` → 409, `EventAlreadyProcessedError` → 400
  - detail 문자열은 현행 그대로 (테스트가 메시지를 검증할 수 있음).
- 라우터에 남는 것: `require_project_member` 권한 배선, `project_service.get_project` 404, `_build_git_settings_response` (Pydantic 응답 조립 = 표현 계층), `background_tasks.add_task(webhooks_module._run_sync_in_new_session, ...)` (모듈 참조 monkeypatch 계약).
- commit/refresh는 서비스가 담당 — `project_service` 관습과 동일.

## 4. 대안 & 결정 ★ (heavy 트리거 ②)

**에러 전달 방식**:
- A. 서비스가 `HTTPException` 직접 raise — 이동은 쉬우나 서비스가 HTTP 계층에 결합, "서비스 = 재사용 가능" 원칙 훼손.
- B. **[채택]** 서비스는 도메인 예외 raise, 라우터가 상태코드로 매핑 — 계층 분리 원칙 충족, 예외 종류가 6개뿐이라 매핑 비용 낮음.
- C. 결과 enum 반환 — 파이썬 관용에 안 맞고 장황.

**응답 빌더 위치**: `_build_git_settings_response`는 라우터 모듈에 존치 [채택] — Pydantic 스키마 조립은 표현 계층. 서비스로 옮기면 서비스가 schemas에 결합.

## 5. 영향/리스크

- API 계약 변경: 없음 (트리거 ① 해당 없음). migration 없음.
- 리스크: `register_webhook`의 row lock(`db.refresh(with_for_update)`) 순서가 동시성 테스트(`test_concurrent_register_webhook_serializes`)에 민감 — 락 획득을 서비스 진입 직후 그대로 유지.
- 롤백: 파일 2개 diff 원복이면 끝 (스키마/DB 무변경).

## 6. 의존/사인오프

- 대기자 없음 (트리거 ③ 해당 없음). Track R 내부 순서상 ⑫ sync_service 분리가 다음 — 이 spec의 경계(webhooks.py 불변)를 ⑫가 이어받음.
