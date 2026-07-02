# sync-service-split plan   (spec: ./spec.md)

아키텍처 한 줄: sync_service 의 5책임을 4모듈로 분리 — 오케스트레이션(존치) / plan 반영 / handoff 반영 / 포매팅 / 공용 헬퍼, `process_event` 경로 불변.

파일:
- 신규 `backend/app/services/git_sync_helpers.py`, `plan_sync_service.py`, `handoff_sync_service.py`, `push_summary_service.py`
- 수정 `backend/app/services/sync_service.py`, `drift_service.py`

## Step 분해

- [ ] Step 1 — `git_sync_helpers.py` 신설: `decrypt_pat`, `handoff_file_path` (public 승격). sync_service 는 위임, drift_service 지연 import 2곳 교체 (top-level 승격 시도 → 순환이면 지연 유지).
  - 검증: `./venv/bin/pytest tests/test_sync_service.py tests/test_drift_service.py -q`
- [ ] Step 2 — `push_summary_service.py` 신설: `format_push_summary`, `resolve_skip_branches`. sync_service 호출부 교체.
  - 검증: `tests/test_sync_service.py` 38 green
- [ ] Step 3 — `plan_sync_service.py` 신설: `PlanChanges` + `apply_plan`. sync_service 는 import.
  - 검증: 동일
- [ ] Step 4 — `handoff_sync_service.py` 신설: `apply_handoff`. sync_service 잔여 정리 (지연 import 중 자연 해소분 top-level 화).
  - 검증: 동일
- [ ] Step 5 — 전체 확인: 미사용 import/데드 심볼 제거.
  - 검증: `./venv/bin/pytest -q` 전체 suite green

롤백: 신규 파일 삭제 + `git checkout -- sync_service.py drift_service.py`. DB/스키마 무변경.
