# sync-service-split spec — ⑫ sync_service 분리   확정일: 2026-07-02

> Track R 리팩토링 (pslog-refactor 진입). 진단·동작보존 계약: `handoffs/refactor-sync-service-split.md`

## 1. 배경/문제

`app/services/sync_service.py` (562줄)가 오케스트레이션·PLAN 반영·handoff 반영·Discord 포매팅·유틸 5책임을 한 파일에 담음 (CLAUDE.md "한 파일은 하나의 책임"). `drift_service` 는 private 헬퍼(`_decrypt_pat`, `_handoff_file_path`)를 지연 import 로 침범 — 모듈 경계가 이미 새고 있음.

## 2. 목표 / 비목표

**목표** (= 완료 기준):
- sync_service 를 응집 단위 4모듈로 분리. 각 모듈은 한 책임.
- drift_service 의 private 심볼 침범 해소 — 공용 헬퍼를 public 함수로 승격해 정상 import.
- `tests/test_sync_service.py` 38케이스 **무수정** green + 전체 suite green.
- `process_event` 의 기존 import 경로 (`from app.services.sync_service import process_event`) 불변.

**비목표** (YAGNI):
- 로직/알림 문구/로그/트랜잭션 타이밍 변경 없음.
- parser·drift_service·notification_dispatcher 내부 변경 없음 (drift 의 import 문만 갱신).
- 지연 import 전면 제거 목표 아님 — 분리로 자연 해소되는 것만.

## 3. 설계(안)

```
app/services/
├── sync_service.py          (유지, ~230줄) process_event + _process_inner + _collect_changed_files
│                            — 오케스트레이션 + 변경파일 수집 (진입점 경로 불변)
├── plan_sync_service.py     (신규) PlanChanges + apply_plan  (구 _apply_plan)
├── handoff_sync_service.py  (신규) apply_handoff  (구 _apply_handoff)
├── push_summary_service.py  (신규) format_push_summary + resolve_skip_branches
└── git_sync_helpers.py      (신규) decrypt_pat + handoff_file_path
                             — sync 와 drift 가 공유하는 헬퍼 (public 승격)
```

- 이동하며 언더스코어 제거 (모듈 경계를 넘는 순간 public API).
- `drift_service` 지연 import 2곳 → `from app.services.git_sync_helpers import ...` 로 교체 (순환 의존이 사라지므로 top-level import 가능하면 승격, 안 되면 지연 유지).
- `_collect_changed_files` 는 sync_service 존치 — process_event 전용이고 event/project 페어에 강결합.
- `PlanChanges` 는 plan_sync_service 에 정의, sync_service 가 import (알림 조립에 사용).

## 4. 대안 & 결정 ★

**분리 단위**:
- A. 2분할 (오케스트레이션 / 적용 로직 전부) — 파일 수는 적지만 "적용" 모듈이 다시 3책임.
- B. **[채택]** 4분할 (plan/handoff/포매팅/공용헬퍼) — 책임당 1모듈, drift 침범 해소가 자연스럽게 포함.
- C. 패키지화 (`app/services/sync/…`) — 구조는 좋으나 import 경로가 전부 바뀌어 diff 확대, flat 관습(services/ 직하)과 어긋남.

**헬퍼 배치**: `git_repo_service` 에 합치는 안 검토 → 기각. fetch 계층(HTTP)과 프로젝트 규약(경로/복호화)은 다른 관심사. `fingerprint_processor.py` 선례가 있어 `*_service` 아닌 `git_sync_helpers.py` 명명 허용.

## 5. 영향/리스크

- 트리거 ①: drift_service 가 의존하는 심볼 이동 — import 경로 갱신으로 흡수, 시그니처 불변.
- 리스크: 지연 import 를 top-level 로 올릴 때 순환 의존 재발 가능 → 각 Step 에서 import 성공을 먼저 확인.
- migration 없음. 롤백 = 신규 파일 삭제 + sync_service/drift_service 원복.

## 6. 의존/사인오프

- 대기자 없음. ⑪ (PR #34) 과 파일 겹침 없음 — 머지 순서 무관.
