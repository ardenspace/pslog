# Handoff: refactor/sync-service-split — @ardensdevspace

## 2026-07-02

- [ ] ⑫ sync_service 분리 (Track R, heavy, pslog-refactor 진입)

### 진단 (pslog-refactor)

- `app/services/sync_service.py` (562줄) — 한 파일에 5책임:
  - 오케스트레이션: `process_event` (117줄 — 성공/실패/알림 3중 분기), `_process_inner`
  - PLAN 반영: `_apply_plan` (141줄) + `PlanChanges`
  - handoff 반영: `_apply_handoff`
  - Discord 포매팅: `_format_push_summary`, `_resolve_skip_branches`
  - 유틸: `_collect_changed_files`, `_decrypt_pat`, `_handoff_file_path`
- 교차 결합: `drift_service` 가 `sync_service._decrypt_pat` / `_handoff_file_path` 를 **지연 import** (`:178`, `:241`) — private 심볼의 모듈 경계 침범. 함수 내부 지연 import 6곳이 순환 의존 신호.
- 영향 범위: 테스트는 `process_event` 만 import (블랙박스) — 내부 분리는 테스트에 안 번짐. app 소비자는 `webhooks.py`/`main.py` (`process_event`), `drift_service` (private 헬퍼 2개).
- 무게: heavy (트리거 ① — drift_service 가 의존하는 공유 심볼 이동)

### 동작보존 계약 — sync_service

- 무게: heavy
- 동치 증명: `tests/test_sync_service.py` 38 케이스 **무수정** before/after green + 전체 suite green (drift/webhook 테스트 포함)
- characterization 신규: 없음 — 38개가 process_event 를 통해 전 경로(멱등/성공/실패/알림/동시성) 핀고정
- before 기준선: 38 passed (2026-07-02)
- 검증 명령: `cd backend && ./venv/bin/pytest tests/test_sync_service.py -q`

### 결정

- `sync_service._apply_plan` 을 **별칭으로 유지** (`from plan_sync_service import apply_plan as _apply_plan`) — 테스트(`test_process_event_records_error_even_when_session_poisoned`)가 이 모듈 속성을 monkeypatch 시임으로 사용. 테스트 무수정 계약 우선.
- plan 의 Step 2↔3 순서 교차 — `push_summary_service` 가 `PlanChanges` 를 import 하므로 `plan_sync_service` 를 먼저 신설.
- drift_service 의 git_sync_helpers import 는 top-level 로 승격 — 순환 의존 원인(sync_service 경유)이 사라짐.

### 구현 완료

- [x] 4모듈 분리: `git_sync_helpers` / `plan_sync_service` / `handoff_sync_service` / `push_summary_service` 신설, sync_service 562줄 → 272줄 (오케스트레이션 + `_collect_changed_files`)
- [x] drift_service private 심볼 침범 해소 (지연 import 2곳 → public top-level)
- [x] 동치 증명: `test_sync_service.py` 38개 무수정 green, 전체 suite 351 passed

### 경계 (안 건드릴 것)

- `process_event` 시그니처·semantics (webhooks.py/main.py 의 import 경로 포함) 불변
- 알림 문구·로그 메시지·commit/rollback 타이밍·row lock 불변
- parser (`plan_parser_service`/`handoff_parser_service`) / `drift_service` 로직 불변 (import 경로만 갱신)
- git_settings (⑪ 별도 PR #34) 불변
