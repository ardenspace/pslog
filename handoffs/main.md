# Handoff: main — @ardensdevspace

## 2026-05-01 (밤) — Error-log Phase 5 Frontend Errors

- [x] DashboardPage 'errors' viewMode 추가 — Board / Table / Week / Errors 토글
- [x] ErrorsList — status 필터 (전체 / OPEN / REGRESSED / RESOLVED / IGNORED) + 목록 + 클릭 → 상세
- [x] ErrorDetail — 헤더 + audit 필드 + git 컨텍스트 + 최근 이벤트 + 액션 버튼 (resolve/ignore/reopen/unmute, OWNER 만)
- [x] GitContextPanel — first_seen 의 handoffs/tasks(archived 배지)/push event + previous_good_sha
- [x] LogLevelBadge / StackTraceViewer (작은 reusable; StackTraceViewer 는 v2 에서 사용)
- [x] useErrorGroups hooks — list / detail / transition (TanStack Query, list+detail invalidate)
- [x] api.errors / api.logs namespace + types/error.ts / types/log.ts
- [x] Project 타입에 last_synced_commit_sha 추가 (resolve 액션의 defaultResolveSha 자동 사용)

### 다음 (Frontend Logs sub-phase 또는 Frontend Ops sub-phase)

- [ ] LogsPage + LogSearchBox (pg_trgm) — Logs 토글 새 viewMode
- [ ] LogTokensPage + LogHealthBadge — 헤더 ⚠️ + 토큰 관리
- [ ] URL 라우팅 (deep-link `/projects/:id/errors/:groupId` ) — Discord 알림에서 직접 이동
- [ ] resolve 시 user-입력 sha (현재는 자동 last_synced_commit_sha)
- [ ] StackTraceViewer 실제 활용 — 별도 endpoint 또는 LogEventDetail schema

## 2026-05-01 (저녁) — Error-log Phase 5 Backend

- [x] PATCH /errors/{group_id} — status 전이 (resolve/ignore/reopen/unmute), OWNER 전용, action 기반 (status 직접 X)
- [x] GET /log-tokens — 토큰 목록, OWNER 전용, secret 절대 비노출, include_revoked 필터
- [x] GET /log-health — unknown SHA 비율 + clock drift + 24h 송신량, 멤버 누구나 (VIEWER 포함)
- [x] error_group_service.transition_status — _LEGAL_TRANSITIONS 매트릭스 + audit 필드 (resolved_at / resolved_by / resolved_in_version_sha) 자동 채움/클리어
- [x] log_health_service.compute_health — 단일 SQL 3 집계 (total / unknown / drift)
- [x] ErrorGroupSummary 에 audit 필드 (resolved_*) 추가 (Task 3 review #7)
- [x] service 가 flush, endpoint 가 commit (Task 2 review I-1, `upsert` 와 같은 파일 컨벤션)
- [x] test_compute_health_excludes_old 에서 yesterday 파티션 수동 생성 (alembic 은 today+30 days 만 생성)
- [x] **검증**: backend **309 tests pass** (275 baseline + 34 신규)

### 다음 (Phase 5 Frontend Errors — sub-phase 2)

- [ ] ErrorsPage / ErrorDetailPage / GitContextPanel 등 — 별도 PR
- [ ] PATCH endpoint 사용한 status 전이 UI (resolve/ignore/reopen 버튼)
- [ ] LogHealthBadge — 헤더의 ⚠️ 표시 (unknown_sha_ratio_24h > 0.05 시)

## 2026-05-01 (Error-log Phase 4 — Query API + Git Context Join)

- [x] **Error-log Phase 4** — 브랜치 `feature/error-log-phase4-query`
  - [x] **`log_query_service`** (5 함수): `list_groups` (status/since 필터, offset/limit + total) / `get_group_detail` (group + recent 50 events + nested git_context + previous_good_sha) / `list_logs` (level/since/q + pg_trgm) / `_find_previous_good_sha` (LEFT JOIN + IS NULL 단일 SQL self-join) / `_collect_git_context` (3 단일 SQL — handoffs/tasks(archived 포함)/push_events first by received_at asc).
  - [x] **`GET /api/v1/projects/{id}/errors`**: ErrorGroup 목록 + 필터 (status / since) + offset/limit. 멤버 권한 (VIEWER 포함, 운영 투명성).
  - [x] **`GET /api/v1/projects/{id}/errors/{group_id}`**: 상세 + recent 50 events + git 컨텍스트 (nested first_seen + previous_good_sha). 다른 project group → 404. nested dict → Pydantic schema 명시 변환 (handoffs/tasks 각 model_validate, push_event None ternary).
  - [x] **`GET /api/v1/projects/{id}/logs`**: LogEvent raw + level/since/q. q 지정 시 자동 level >= WARNING 강제 (Phase 1 의 `idx_log_message_trgm` partial 활용). q + level 동시 시 q 우선 (단순화). q `min_length=2` Pydantic 검증.
  - [x] **archived task 포함** (spec §4.2): UI 가 후속 (archived) 배지 표시.
  - [x] **마이그레이션 신규 없음** — Phase 1 alembic 의 모든 모델 + 인덱스 (`idx_log_message_trgm` partial gin) 활용.
  - [x] **deviation 1 (Task 3 main `a2937f2`)**: `_find_previous_good_sha` 의 `.distinct()` 제거 — PostgreSQL `SELECT DISTINCT col1 ... ORDER BY col2` 비-select 컬럼 거부 → InvalidColumnReferenceError. `LIMIT 1` 으로 functional equivalence (1 row → 1 SHA).
  - [x] **deviation 2 (Task 3)**: 테스트 SHA `g`*40 / `t`*40 → `b`*40 / `c`*40 — `log_events_version_sha_check` constraint (`^[0-9a-f]{40}$|^unknown$`) 가 hex 만 허용. plan 의 SHA 가 invalid 였음.
  - [x] **deviation 3 (Task 4)**: `test_list_logs_filter_by_since` 의 hardcoded `2026-04-30` → `datetime.utcnow()` 상대값. 이유: `log_events` 가 daily range partition + alembic 가 today+30일만 생성 → 2026-04-30 partition 없음 → `CheckViolationError`. **time bomb 회피**: 미래 날짜에서도 안정적.
  - [x] **polish 적용** (`0f8419d` Task 3, `5f7015b` Task 4): `_collect_git_context` 의 push_events Python sort → SQL ORDER BY ASC LIMIT 1 ("3 단일 SQL" 일관성) / 중간 imports → 상단 (PEP 8) / `_WARNING_OR_HIGHER` 모듈 상수 (Phase 1 partial index 와 동일 set, searchable 계약) / `if q:` → `if q is not None and q.strip():` (서비스 단독 안전성) / row-level vs SHA-aggregate semantic 코멘트 / environment 가정 한계 코멘트.
  - [x] **검증**: backend **275 tests pass** (256 baseline + 19 신규: 11 service + 5 errors endpoint + 3 logs endpoint).

### 마지막 커밋

- pslog: `<sha> docs(handoff+plan): Error-log Phase 4 완료 + Phase 5/6 다음 할 일`
- 브랜치 base: `0f1cb10` (main, Error-log Phase 3 PR #17 머지 직후)

### 다음 (Phase 5 — UI)

- LogsPage / ErrorsPage / ErrorDetailPage / GitContextPanel / LogTokensPage / LogHealthBadge
- PATCH /errors/{group_id} (사용자 status 전이 — resolve/ignore/reopen) + 권한 OWNER
- GET /log-tokens 목록 endpoint (UI 가 호출)
- GET /log-health (unknown SHA 비율 모니터링)

또는 Phase 6 (알림 본편 — spike/regression) — 사용자 dogfooding 후 결정.

### Phase 5 시작 전 권장 follow-up (codebase-wide DRY)

- **`require_project_member` Depends 헬퍼**: 권한 체크 5줄 패턴이 8+ endpoint 반복 (Phase 4 + git_settings.py 등). FastAPI Depends 로 통합. CLAUDE.md DRY 규칙. Phase 4 reviewer 가 raise.
- **conftest.py 통합**: `client_with_db` / `_seed_user_project` / `_auth_token` 가 6+ test 파일 동일 정의. 공통 conftest 로 이동. Phase 4 reviewer 가 raise.
- **`datetime.utcnow()` deprecation**: codebase-wide 패턴 — Python 3.13+ 또는 `-W error` 시 fail. `datetime.now(timezone.utc).replace(tzinfo=None)` 으로 sweep PR.

이 3건 Phase 5 시작 전 별도 refactor PR 권장 (각 endpoint 독립 변경).

### 블로커

없음

### 메모 (2026-05-01 Error-log Phase 4 추가)

- **직전 정상 SHA = LEFT JOIN + IS NULL 패턴**: 단일 SQL 로 같은 environment 의 target_fp 가 발생 안 했던 가장 최근 SHA 찾음. SQLAlchemy `LogEvent.__table__.alias()` 로 self-join. 2-step Python filter 대비 효율 ↑. **DISTINCT 불가** — ORDER BY col 이 SELECT col 과 다르면 PG 거부, `LIMIT 1` 으로 functional equivalence.
- **Git 컨텍스트 3 단일 SQL**: `IN (version_shas)` 로 handoffs/tasks/push_events bulk fetch. push_events 는 SQL `ORDER BY received_at asc LIMIT 1` (Python sort 회피 — Task 3 review polish). nested 응답 1 endpoint round-trip. archived task 포함 (spec §4.2 — UI 가 후속 (archived) 배지).
- **pg_trgm partial index 활용**: `idx_log_message_trgm WHERE level >= WARNING` (Phase 1 alembic). q 지정 시 자동 WARNING+ 강제 — 인덱스 partial WHERE 매칭. q + level 동시 시 q 우선 (단순화). `_WARNING_OR_HIGHER = (WARNING, ERROR, CRITICAL)` 상수로 인덱스 계약 searchable.
- **environment 필터 v1 미포함**: ErrorGroup 자체엔 environment 컬럼 없음 (같은 fingerprint 가 여러 env 발생 가능). 후속 호소 시 EXISTS subquery 또는 컬럼 추가.
- **VIEWER 권한 조회 가능**: 운영 투명성 — 에러 정보는 일반 사용자에게도 노출 가치 있음. PATCH (Phase 5) 만 OWNER.
- **previous_good_sha 의 environment**: first_event 의 environment (recent 50 중 oldest). 한 fingerprint 가 여러 environment 발생 시 부정확 가능 — recent 50 안에 진짜 first event 누락되면 잘못된 env 사용. 후속: ErrorGroup 에 first_environment 컬럼 추가 검토 (코멘트로 기록).
- **`__table__.alias()` 패턴**: SQLAlchemy 2.0 의 self-join 표준 패턴. ORM model 이 아닌 table 객체를 alias 해서 사용. raw SQL 안 쓰고도 LEFT JOIN + IS NULL 가능.
- **partition 시한폭탄 학습** (Task 4 deviation): `log_events` 는 daily range partition + 동적 alembic 가 today+30일만 생성. 테스트 hardcoded 미래/과거 날짜는 시간이 지나면 `CheckViolationError`. **`datetime.utcnow()` 상대값 + `timedelta` 사용 권장**.
- **DISTINCT + ORDER BY 비-select 컬럼 PG 제약 학습**: `SELECT DISTINCT col1 ... ORDER BY col2` 거부 (`InvalidColumnReferenceError`). `LIMIT 1` 으로 단일 row 보장이면 DISTINCT 불필요.
- **next 가능 옵션**: Phase 5 (UI 통합 — 대규모 frontend phase) 또는 Phase 6 (알림 본편). dogfooding 으로 사용자 우선순위 평가.

---

## 2026-05-01 (Error-log Phase 3 — Fingerprint + ErrorGroup + Reaper + B-lite Alert)

- [x] **Error-log Phase 3** — 브랜치 `feature/error-log-phase3-fingerprint`
  - [x] **`fingerprint_service`** (`58f0219`): 결정적 SHA1 (정규화 6 규칙 — APP_PROJECT_ROOT env var + 휴리스틱 / line 제거 / 메모리 주소 마스킹 / 함수명 유지 / framework 스킵 / 입력 포맷). Fallback: stack_frames None 또는 모두 framework. config 에 `app_project_root: str = "backend/"` 추가.
  - [x] **`error_group_service`** (`9845336`): ErrorGroup UPSERT + 자동 status 전이 (신규→OPEN, RESOLVED→REGRESSED). race-free — `with_for_update` (B1 패턴) + UNIQUE conflict SAVEPOINT (Phase 2 record_push_event 패턴). 동시 UPDATE 직렬화 deterministic 4회 반복 검증.
  - [x] **`log_alert_service.notify_new_error`** (`bc1fb0f`, B-lite scope): 신규 fingerprint 1회 Discord 알림. notification_dispatcher 통과 (Phase 6 disable 정책 자동). cooldown — last_alerted_new_at IS NULL 1차 게이트 + race 방지 2차 체크.
  - [x] **`fingerprint_processor`** (`1e5c157`): composition (fingerprint → group → fingerprinted_at + commit → 신규면 alert). exception_class None → "UnknownError".
  - [x] **Ingest endpoint BackgroundTask 통합** (`9523d48`): ingest_batch 시그니처에 accepted_event_ids 추가. endpoint 가 ERROR↑ 분류 SELECT + add_task. _process_log_event_in_new_session helper (자체 session + 멱등 — fingerprinted_at 체크). Phase 4 fresh-session 패턴.
  - [x] **`log_fingerprint_reaper` + lifespan hook** (`e3570ab`): 부팅 시 1회, chunked 100/batch, idx_log_unfingerprinted partial index 사용 (Phase 1 alembic). push_event_reaper 패턴.
  - [x] **마이그레이션 신규 없음** — Phase 1 alembic 이 모든 모델 + 인덱스 (`idx_log_unfingerprinted` partial 포함) 이미 포함.
  - [x] **deviation 1**: Task 5 endpoint test 가 `_process_log_event_in_new_session` helper 자체를 patch (spec 의 `fingerprint_processor.process` patch 대신) — helper 가 AsyncSessionLocal 열기 전 차단해야 test container 환경에서 동작. 더 깔끔한 isolation point.
  - [x] **deviation 2**: Task 6 reaper test 의 seed 가 fixed `exception_message` 사용 (varying message 대신) — fingerprint fallback 의 결정성 (`SHA1(class|msg_first_line)`) 에 맞춤 (같은 message → 같은 fingerprint → 1 group).
  - [x] **검증**: backend **256 tests pass** (230 baseline + 26 신규: 9 fingerprint + 6 group + 3 alert + 4 processor + 1 endpoint + 3 reaper). race fix 4회 deterministic. e2e 사용자 직접 검증 필요 (app-chak logger.error → Discord 알림 도착).

### 마지막 커밋

- pslog: `<sha> docs(handoff+plan): Error-log Phase 3 완료 + Phase 4 다음 할 일`
- 브랜치 base: `90cce78` (main, Error-log Phase 2a PR #16 머지 직후)

### 다음 (Error-log Phase 4 — 조회 API + Git 컨텍스트 join)

본 phase 가 ErrorGroup 데이터 채워줌 — Phase 4 가 사용자 노출 (조회):
- `log_query_service` + `GET /errors`, `GET /errors/{group_id}` (Handoff/Task/GitPushEvent join)
- 직전 정상 SHA 찾기 알고리즘 (해당 fingerprint 가 *없는* 가장 최근 SHA, 같은 environment)
- 풀텍스트 검색 endpoint (pg_trgm, message gin_trgm_ops 인덱스 Phase 1 에 이미)
- 핵심 가치 전달 — UI 없이 API 만으로도 curl 검증 가능

또는 Phase 5 (UI) — Phase 4 + 5 같이 묶음 가능.

### 블로커

없음

### 메모 (2026-05-01 Error-log Phase 3 추가)

- **race-free UPSERT 패턴 (조합)**: `with_for_update()` 가 같은 group 동시 UPDATE 직렬화, 신규 INSERT race 는 `begin_nested() + IntegrityError catch + SELECT fallback` (Phase 2 record_push_event 패턴 재사용). 두 mechanism 가 다른 case 커버 — 같이 쓰면 race-free. concurrent test 4회 deterministic 검증.
- **commit 후 알림 패턴 (Phase 6 학습 적용)**: fingerprint_processor 가 db.commit() 후 notify_new_error 호출. DB 일관 상태에서 발송. alert_service 자체도 commit 마킹 — `last_alerted_new_at = now`.
- **BackgroundTask + reaper 멱등 패턴**: 양쪽에서 `event.fingerprinted_at IS NOT NULL` 체크. 둘 중 하나만 처리. reaper 의 chunked 100/batch + idx partial index 가 large backlog 안전.
- **fresh session per event (Phase 4 학습)**: reaper 와 BackgroundTask 모두 같은 패턴 — 단일 event poison 이 다음 event 처리에 영향 없음. reaper 는 lookup_db (per-batch) + inner_db (per-event) 분리.
- **fingerprint 정규화 결정성**: line 제거 + 메모리 주소 마스킹 + framework 스킵 — 같은 버그를 다른 group 으로 분리 안 하고, 다른 버그를 같은 group 으로 합치지 않음. spec §4.1 의 균형점.
- **Fallback 의도**: stack_frames None 또는 모두 framework 라도 ErrorGroup 만들어짐 — fingerprint 약하지만 사용자 가시화 (사고 가능). spec §7.
- **B-lite alert scope**: 신규 fingerprint 1종만. spike (메모리 카운터 + 30분 cooldown) / regression (자동 transition 알림) 은 Phase 6 본편. error_group_service.upsert 가 `transitioned_to_regression` 신호 이미 return — Phase 6 alert_service 가 사용 예정.
- **deviation 1 (Task 5 endpoint test)**: `_process_log_event_in_new_session` helper 자체를 patch — helper 가 AsyncSessionLocal 열기 전 차단. spec 의 inner function patch 보다 cleaner isolation.
- **deviation 2 (Task 6 reaper test seed)**: fixed exception_message 사용 — fingerprint fallback (`SHA1(class|msg_first_line)`) 의 결정성에 맞춤. varying message 면 다른 group 분리됨.
- **마이그레이션 신규 없음 학습 (Phase 6 / Phase 2a 와 같은 패턴)**: Phase 1 통합 alembic 이 모든 모델/인덱스 포함. error-log 의 본 phase 도 schema 변경 0 — 순수 service/endpoint 레이어.
- **next 가능 옵션**: Phase 4 (조회 + git join) 또는 Phase 4+5 (조회 + UI) 묶음. Phase 6 알림 본편 (spike/regression) 은 사용자 dogfooding 후 결정.

---

## 2026-05-01 (Error-log Phase 2a — Ingest endpoint + Token API)

- [x] **Error-log Phase 2a — Ingest endpoint + Token API** — 브랜치 `feature/error-log-phase2-ingest`
  - [x] **Pydantic schemas**: `LogEventInput / IngestPayload / RejectedEvent / IngestResponse / StackFrame` (log_ingest), `LogTokenCreate / LogTokenResponse / LogTokenRevokedResponse` (log_token). 모두 `extra="forbid"`.
  - [x] **`log_ingest_service` (6 함수)**: parse_token (Bearer <key_id>.<secret>) / verify_token (asyncio.to_thread bcrypt + last_used_at 갱신) / check_rate_limit (PostgreSQL UPSERT, 분 truncate, 429 + Retry-After) / validate_event (Pydantic + version_sha 형식 + extra 4KB + tz strip) / insert_events (batch INSERT, fingerprint=NULL) / ingest_batch (composition + commit).
  - [x] **POST `/api/v1/log-ingest`**: gzip 지원, partial success 200 + rejected list, 모든 invalid 400, 인증 401 (timing attack 회피 — 모두 "Invalid token"), rate 429 + Retry-After, DB fail 500.
  - [x] **POST `/api/v1/projects/{id}/log-tokens`** (OWNER): `secrets.token_urlsafe(32)` + bcrypt cost 12, 평문 token 응답 1회만.
  - [x] **DELETE `/api/v1/projects/{id}/log-tokens/{id}`** (OWNER): soft delete (revoked_at = now), 이미 revoked 400, 다른 project token 404.
  - [x] **deviation 1 (722928e)**: `validate_event` 가 `emitted_at.replace(tzinfo=None)` 로 tz strip — Pydantic 이 `"...Z"` 를 tz-aware 로 파싱하지만 DB 컬럼이 TIMESTAMP WITHOUT TIME ZONE. real bug fix, validate_event 레벨 (모든 caller 자동 적용).
  - [x] **deviation 2 (722928e)**: `ingest_batch` 도 `token.last_used_at = now` set — verify_token 도 set 하므로 production path 에서 중복. 단순 in-memory overwrite, harmless. 테스트가 verify_token bypass 할 때도 통과.
  - [x] **마이그레이션 신규 없음** — Phase 1 alembic 이 모든 컬럼 (`LogIngestToken / RateLimitWindow / LogEvent + rate_limit_per_minute`) 이미 포함.
  - [x] **검증**: backend **230 tests pass** (198 baseline + 32 신규: 14 service + 4 token + 8 ingest endpoint + 6 validate). app-chak handler 가 미사용 상태로 대기 중 (`pslog_LOG_ENDPOINT` 비어있음) — 본 phase 머지 즉시 e2e 가능 (토큰 발급 → app-chak `.env` 설정 → 자동 활성).

### 마지막 커밋

- pslog: `<sha> docs(handoff+plan): Error-log Phase 2a 완료 + Phase 2b/3 다음 할 일`
- 브랜치 base: `7e51c20` (main, Phase 6 PR #15 머지 직후)

### 다음 (Error-log Phase 2b / Phase 3)

- **Phase 2b** (운영 인프라): `log_fingerprint_reaper` (부팅 시 `fingerprinted_at IS NULL` 회수 — Phase 3 의 fingerprint_service 와 같이) + `log_health_service` (hourly cron — unknown SHA 비율 / 시계 어긋남 추적). reaper 는 Phase 3 의존성 — Phase 3 와 묶음 권장.
- **Phase 3** (fingerprint + ErrorGroup): `fingerprint_service` (예외 → 결정적 fingerprint) + `error_group_service` (UPSERT + 신규/spike/regression 감지 + cooldown). ingest endpoint 가 BackgroundTask 로 fingerprint 처리 trigger. 핵심 가치 (에러 그룹화) 전달.

### 블로커

없음

### 메모 (2026-05-01 Error-log Phase 2a 추가)

- **bcrypt cost 12 + asyncio.to_thread 패턴**: 동기 bcrypt.checkpw 를 async endpoint 에서 호출 시 event loop block — `await asyncio.to_thread(bcrypt.checkpw, ...)` 로 wrapping. test 에선 cost 4 사용 (속도 — `bcrypt.gensalt(rounds=4)`).
- **timing attack 회피 — fast-path 선택**: 401 detail 모두 "Invalid token" 통일했지만 key_id lookup fail 시 bcrypt 호출 안 함 (~250ms 응답 시간 차이 노출). dummy bcrypt 호출은 비용 / 복잡도 비효율 — v1 는 fast-path 우선. Phase 2b 또는 보안 호소 시 dummy bcrypt 보강.
- **`secrets.token_urlsafe(32)` for secret 생성**: 256-bit (32 bytes) base64 url-safe (~43자). DB 에는 bcrypt(secret, cost=12) 만 저장.
- **Soft delete (revoked_at)**: past LogEvent / RateLimitWindow 의 FK 보존. hard delete 안 함. revoked 토큰은 verify_token 가 401 반환.
- **token.project_id 강제 사용** (security): ingest 시 클라이언트가 다른 project 의 LogEvent INSERT 못 하게 token 의 project_id 만 사용. 외부 input 무시 (extra="forbid" 가 1차 방어, validate_event 가 2차 강제).
- **PostgreSQL UPSERT pattern (RateLimitWindow)**: `pg_insert(...).on_conflict_do_update(set_={"event_count": Model.event_count + N}).returning(...)` — 단일 SQL, race-free. set_ 의 `Model.event_count` 는 EXISTING row 의 값 참조 (NOT EXCLUDED).
- **Partial validation 200 + rejected list**: 페이로드 N건 중 일부만 invalid → 200 + `{accepted, rejected}`. 모두 invalid → 400. spec §6.1 "나머지는 정상 처리" 직접 매칭.
- **gzip middleware 미사용**: FastAPI 기본 GZip middleware 는 응답 압축만 처리. 요청 body decompress 는 endpoint 가 직접 `gzip.decompress(body)` — 명시적이고 단순.
- **timezone-aware datetime 함정 학습**: Pydantic 이 ISO `"...Z"` 를 tz-aware datetime 으로 파싱. DB 컬럼이 `TIMESTAMP WITHOUT TIME ZONE` 이면 INSERT 실패. 해결: validate_event 가 `emitted_at.replace(tzinfo=None)` 로 strip. 향후 다른 datetime 필드 추가 시 같은 패턴 주의.
- **마이그레이션 신규 없음 학습 (Phase 6 와 같은 패턴)**: Phase 1 의 통합 alembic (`c4dee7f06004`) 이 task-automation + error-log 의 모든 모델/컬럼을 한 번에 추가. error-log 본 phase 는 schema 변경 0 — 순수 service/endpoint 레이어.
- **next 가능 옵션**: Phase 3 (fingerprint + ErrorGroup) 진입. ingest endpoint 에 BackgroundTask 추가해 fingerprint 처리 trigger. log_fingerprint_reaper 도 같이 묶음.

---

## 2026-05-01 (Phase 6 — Discord 알림 통합 본편)

- [x] **Phase 6 — Discord 알림 통합 본편** — 브랜치 `feature/phase-6-discord-notifications`
  - [x] **Project 모델 +2 컬럼** (`discord_consecutive_failures` / `discord_disabled_at`) — alembic 1건 (`7c6e0c9bb915_phase6_discord_counter`, server_default='0'). 기존 row 자동 활성 상태.
  - [x] **`notification_dispatcher` 신규 서비스** — 모든 Discord 알림 1점 진입. URL NULL / disabled_at set → no-op. 성공 시 counter reset, 실패 시 +1 (3 도달 시 disabled_at = now). 알림 실패 silent.
  - [x] **Push 알림 (success path)** — `_apply_plan` 가 `PlanChanges` dataclass return, `_apply_handoff` 가 bool return. `_process_inner` 가 (plan_changes, handoff_present, plan_changed) tuple return. `_format_push_summary` 가 4 카테고리 (체크/롤백/archived/handoff 누락) — 모든 카테고리 비고 + handoff 정상 → None return → 알림 안 함. 신규 INSERT 알림 안 함 (sprint 초 noise YAGNI). `handoff_path` 정확화 polish 적용 (custom handoff_dir / 슬래시 branch 케이스).
  - [x] **B2 sync-failure 리팩토링** — `process_event` except 분기의 직접 `send_webhook` → dispatcher 경유. **B1 의 rollback ORM expire 함정 학습**: `db.refresh(project)` 1줄 추가 (capture 한 webhook URL 이 truthy 일 때만 refresh — 비용 회피).
  - [x] **`POST /git-settings/discord-reset`** (OWNER 전용) — counter / disabled_at reset.
  - [x] **`GitSettingsResponse` 3 필드** (`discord_enabled` / `discord_disabled_at` / `discord_consecutive_failures`). `_build_git_settings_response` 헬퍼로 GET/PATCH/discord-reset DRY.
  - [x] **`project_service.update_project` 자동 reset** — `discord_webhook_url` 변경 감지 시 같은 트랜잭션에서 reset (DashboardPage 의 PATCH /projects 경로 자동 적용).
  - [x] **Frontend `ProjectGitSettingsModal` Discord 섹션** — 상태 (활성/비활성화/미설정) + 비활성화 시각 + 재활성화 버튼. URL 입력은 DashboardPage 그대로 (UI 변경 없음).
  - [x] **테스트 deviation**: B2 의 `test_discord_alert_not_called_on_success_path` 가 PLAN-only push 를 가정 → Phase 6 의 새 contract (PLAN-only = handoff_missing alert 발사) 와 충돌. 테스트 이름/입력 변경 (`test_discord_alert_not_called_on_success_path_without_relevant_files`, `README.md` only) 으로 원래 의도 (truly no-op push) 보존. 더 정확한 회귀.
  - [x] **검증**: backend **198 tests pass** (184 baseline + 14 신규: 2 migration + 4 dispatcher + 3 push summary + 4 git_settings + 1 project_service). frontend `bun run build` clean, `bun run lint` 8 pre-existing only. **시각 검증 + e2e 사용자 직접** (PR 본문 체크리스트).

### 마지막 커밋

- pslog: `<sha> docs(handoff+plan): Phase 6 완료 + Phase 7 (선택) 다음 할 일`
- 브랜치 base: `29c7db7` (main, B2 PR #14 머지 직후)

### 다음 (Phase 7 — Gemma 브리핑, 선택) 또는 error-log spec

spec §11 의 마지막 phase. 진입 전 맥미니에서 Gemma 4 26B MoE 추론 시간 실측 필요 (30초 빈번 초과 시 비동기 응답 패턴). Phase 1~6 안정화 + 1주 무중단 검증 후 별개 trigger.

또는: **error-log spec 진입** (`2026-04-26-error-log-design.md`). task-automation Phase 4 (sync_service) 안정화 1주 충족 — 진입 가능.

### 블로커

없음

### 메모 (2026-05-01 Phase 6 추가)

- **`PlanChanges` dataclass return 패턴**: `_apply_plan` 가 mutation 외에 변경 요약 dict 도 return — `process_event` 가 모아서 dispatcher 호출. mutable global state 안 씀, 함수 순수성 유지. 향후 알림 종류 추가 시 카테고리만 늘리면 됨.
- **`_handoff_file_path` 정확화 (polish)**: 초기 구현이 `f"handoffs/{branch}.md"` hardcoded 였으나 custom `handoff_dir` 또는 슬래시 branch (예: `feature/foo` → `feature-foo.md`) 케이스 부정확. caller 가 `_handoff_file_path(project, branch)` 결과 전달하도록 변경. 회귀 테스트는 default 값 (handoffs/main.md) 그대로 → 통과 유지.
- **B1 의 rollback ORM expire 함정 재발 회피**: success path 는 commit 직전까지 ORM 살아있어 dispatcher 가 직접 project 접근 안전. except path 는 rollback 이 expire 시키므로 dispatcher 호출 직전 `db.refresh(project)` 필수. capture 된 webhook URL 은 1차 게이트로 사용 (truthy 일 때만 refresh — 비용 회피).
- **신규 INSERT 알림 미도입 결정**: sprint 초 PLAN 작성 시 노이즈 폭발. 사용자 호소 시 후속 옵션 컬럼 추가.
- **알림 종류별 on/off 미도입**: `discord_webhook_url` NULL 또는 `discord_disabled_at` set 만으로 사용자 제어. 종류별 필터는 사용자 호소 시 후속.
- **DashboardPage 의 URL 입력 + GitSettings 의 상태 표시 분리**: 기존 UX 그대로 (URL 변경 코스트 ↓), 백엔드 `update_project` 가 URL 변경 감지 시 자동 reset 으로 일관 보장. URL 입력 모달 안으로 이동은 UX 결정 후속.
- **alembic autogenerate 함정 학습**: live DB 와 마이그레이션 history 가 drift 면 autogenerate 가 노이즈 (예: log_events partition drops, index 변동) 추가. 본 phase 의 Task 1 에서 implementer 가 autogenerate 결과를 수동으로 정리해 add_column 2건만 남김. 향후 마이그레이션도 같은 패턴 — autogen 결과를 항상 검토.
- **B2 test deviation 처리**: 새 contract (Phase 6 의 success path 알림) 가 기존 테스트의 가정과 충돌하면 테스트 이름/입력을 새 의도로 갱신 (band-aid 아님). 본 phase 에서 `test_discord_alert_not_called_on_success_path` → `..._without_relevant_files` (PLAN-only → README-only) 로 갱신. spec reviewer 가 합리적 deviation 으로 승인.
- **next 가능 옵션**: Phase 7 (Gemma 브리핑) 또는 error-log spec. Phase 4 안정화 1주 충족 — error-log 진입 trigger 도 가능.

---

## 2026-05-01 (Phase 5 follow-up B2)

- [x] **B2 — UI Closure + Discord sync-failure 알림** — 브랜치 `feature/phase-5-followup-b2-ui`
  - [x] **TaskCard ⚠️ 기록 빠짐 배지** (`0eb412d` backend + `7069b4d` frontend): backend `TaskResponse.handoff_missing` 필드 + `task_service._annotate_handoff_missing` (단일 query 로 N+1 회피, cross-project 안전 — week tasks 도 처리). 조건: SYNCED + last_commit_sha set + handoff 없음 + not archived. frontend 1줄 조건부 노란 배지.
  - [x] **GitEventListModal + reprocess 호출 site** (`cd97275` backend + `658c3d8`/`64f5624` frontend): backend 신규 endpoint `GET /git-events?failed_only=true`, frontend HandoffHistoryModal 패턴 매칭 (custom modal — shadcn Dialog 아님), 행마다 [재처리] 버튼. B1 의 `useReprocessEvent` hook 이 이번에 wired up. onError 분기: 409/400/기타 alert.
  - [x] **ProjectItem count badge + 메뉴 항목** (`64f5624`): trigger `···` 우상단 빨간 점, 메뉴 항목 "⚠️ Sync 실패 (N)" — 0건 시 둘 다 숨김. OWNER 만 fetch.
  - [x] **Discord sync-failure 알림 (minimal)** (`cfb14b9`): `process_event` except 분기 끝에서 fire-and-forget Discord 알림. `Project.discord_webhook_url` set 인 경우만, 알림 실패 silent. cooldown 없음 — Phase 6 의 cooldown + 3 템플릿은 본편 phase. **rollback 후 expire 회피** 위해 webhook URL / project name / branch / head SHA 를 try block 전에 capture.
  - [x] **검증**: backend **184 tests pass** (175 B1 baseline + 9 신규: handoff_missing 3 + git-events endpoint 3 + Discord alert 3). frontend `bun run build` clean, `bun run lint` 8 pre-existing (out of scope). **시각 검증은 사용자 dev server 직접** (PR 본문 체크리스트).

### 마지막 커밋

- pslog: `<sha> docs(handoff+plan): Phase 5 follow-up B2 완료 + Phase 6 다음 할 일`
- 브랜치 base: `cd53696` (main, B1 PR #13 머지 직후)

### 다음 (Phase 6 — Discord 알림 통합 본편)

- [ ] **`discord_service` 확장 — 3 템플릿**:
  - 체크 변경 알림 (PLAN 의 `[ ]` → `[x]` 변화 사용자별 요약)
  - handoff 누락 경고 (일정 시간 경과 후 handoff 없으면 알림)
  - 롤백 알림 (PLAN 에서 task `[x]` → `[ ]` 회귀)
- [ ] **`sync_service` 가 알림 트리거** (DB 변경 후 fire-and-forget BackgroundTask)
- [ ] **cooldown 정책** (spec §8 — 3회 연속 실패 시 disable, burst 차단)
- [ ] **알림 종류별 on/off** (선택 — Project 설정 1 컬럼)

### 블로커

없음

### 메모 (2026-05-01 B2 추가)

- **handoff_missing annotate 패턴 결정**: SQLAlchemy `column_property` 또는 `select` 의 EXISTS 라벨 대신 — list[Task] fetch 후 별도 query 1건으로 `(project_id, commit_sha) in handoffs` 매칭. 결과를 비-mapped Python 인스턴스 attribute (`task.handoff_missing = bool`) 로 set. Pydantic `from_attributes=True` 가 mapped 여부 무관하게 attribute 읽음. cross-project (week tasks) 도 안전 — `Handoff.project_id.in_(...)` + `Handoff.commit_sha.in_(...)` 후 `(project_id, commit_sha) in existing_pairs` 로 정확 매칭 (Cartesian over-fetch 안전).
- **GitEventListModal 패턴 결정**: codebase 가 shadcn `Dialog` 가 아닌 custom modal (HandoffHistoryModal — 검정 테두리 + 빨강 그림자) 컨벤션. 이번에도 같은 스타일 매칭. 토스트 system 도 미도입 → `alert()` 로 대체. 향후 toast 도입 시 일괄 교체.
- **Discord 알림 expired-object 함정**: `process_event` except 분기는 `db.rollback()` 을 호출 — `expire_on_commit=False` 가 commit 만 보호하고 rollback 은 그대로 expire 시킴. 알림 송신 시 `project.discord_webhook_url` / `project.name` / `event.branch` / `event.head_commit_sha` 접근하면 `MissingGreenlet` 발생 (async lazy load). 해결: 4 값을 try block 진입 전 (project 로드 직후) 에 캡처해두고 except 분기에서 캡처본 사용. 향후 sync_service 에 다른 비-async 외부 호출 추가될 때 같은 패턴 주의.
- **count badge UX 결정**: dropdown 외부 (trigger ··· 우상단 빨간 점) + dropdown 내부 (메뉴 항목 카운트) 양쪽 표시. 0건 시 둘 다 숨김. Owner 만 fetch (비-OWNER 는 메뉴 자체 미노출).
- **B1 polish 의 detail 메시지 ("please try again shortly") 가 이번에 wired up**: GitEventListModal 의 onError 분기에서 409 → "처리 중입니다 — 잠시 후 다시 시도해 주세요" alert. backend detail 은 영문이지만 frontend 에서 한글로 변환 (사용자 직접 노출 메시지).
- **next 할 일은 Phase 6** (Discord 3 템플릿 + cooldown). B2 가 sync-failure 1 alert 만 깔아둠 — failure 외 success-flow 알림은 Phase 6 본편.

---

## 2026-05-01 (Phase 5 follow-up B1)

- [x] **B1 — Quality fixes (race + 미사용 컬럼 + refactor)** — 브랜치 `feature/phase-5-followup-b1-quality`
  - [x] **M-10**: `auth_headers / parse_repo / raise_for_status` public promote (rename, 9 callsite — `github_hook_service` 의 cross-module underscore-import 위반 해소). Commit `dc718bf`.
  - [x] **M-6**: `sync_service` 가 success 시 `Project.last_synced_commit_sha = event.head_commit_sha` write — `commits_truncated` Compare API base 정확화. 실패 path 는 그대로 (재처리 시 직전 base 보존). 회귀 테스트 2건. Commit `e0da55e`.
  - [x] **I-4 layer 1**: `reprocess` endpoint 가 `processed_at IS NULL` event 거부 (409 Conflict) — webhook 직후 BackgroundTask 가 끝나기 전 사용자 클릭 race 차단 (가장 흔한 시나리오). Commit `0a78a0a`.
  - [x] **I-4 layer 2**: `process_event` entry 에 `db.refresh(event, with_for_update={"nowait": False})` — DB row lock 으로 동시 호출 직렬화. 결정적 race 테스트 (`asyncio.Event` + slow fetcher 로 두 session lock 경쟁 — fix 없이는 counter==2 + UNIQUE violation 동시 발생). Commit `786b49c`.
  - [x] **I-2**: `register_webhook` 진입에 `db.refresh(project, with_for_update={"nowait": False})` — 동시 OWNER 호출 시 후행이 선행 final commit 까지 대기 → 갱신된 hook 보고 `update_hook` 분기로 떨어짐. fix 없으면 `create_hook` 2번 + DB/GitHub secret mismatch. 결정적 race 테스트. Commit `1644fea`.
  - [x] **Polish (code review nit fix)**: "reaper" 단어 → "please try again shortly" (B2 GitEventList 모달이 detail 렌더 예정 — 내부 jargon 제거), test 의 `Maker_a/b → maker_a/b` (CLAUDE.md snake_case 준수). Commit `37f1afc`.
  - [x] **검증**: backend **175 tests pass** (170 baseline + 5 신규: M-6 success / M-6 failure / I-4 409 / I-4 race / I-2 race). race 테스트 2건은 3-5회 반복 실행해 flake 없음 확인. **시각 검증 / e2e webhook 수신 테스트는 사용자 dev server 직접** (수신 endpoint 변경 없음, race fix 만이라 영향 최소).

### 마지막 커밋

- pslog: `<sha> docs(handoff): Phase 5 follow-up B1 완료 + B2/Phase 6 다음 할 일`
- 브랜치 base: `27e8b56` (main, Makefile chore 직후)

### 다음 (B2 — Phase 5b UI 후속 / 그 후 Phase 6)

**B2 — Phase 5b UI 후속** (별도 plan):
- [ ] **TaskCard ⚠️ handoff 누락 표시** — 데이터 정의 (Task `last_commit_sha` join → Handoff 존재 여부, backend 필드 또는 계산 추가)
- [ ] **GitEventList 모달 + `useReprocessEvent` 호출 site** — sync 실패 이벤트 list 모달 (현재 `useReprocessEvent` 훅만 만들어둠, 호출 site 미구현). 이번 polish 로 detail 메시지가 user-friendly 해졌으니 그대로 토스트 노출 가능.

**Phase 6 — Discord 알림 통합** (B2 머지 후):
- [ ] `discord_service` 확장 — 체크 변경 요약 / handoff 누락 경고 / 롤백 알림 템플릿 3종
- [ ] `sync_service` 가 알림 트리거 (DB 변경 후 fire-and-forget BackgroundTask)
- [ ] `Project.discord_webhook_url` 미설정 시 silent skip
- [ ] cooldown 정책 (spec §8 — 3회 연속 실패 시 disable)

### 블로커

없음

### 메모 (2026-05-01 B1 추가)

- **`db.refresh(obj, with_for_update={"nowait": False})` 패턴**: SQLAlchemy 2.0 async 에서 in-memory ORM object 의 row lock 재획득 표준 방식. `select(...).with_for_update()` 는 새 query 라 obj 가 expire — refresh 가 더 적합. 본 phase 의 두 race fix (I-2/I-4) 모두 이 패턴 사용. dict-form `{"nowait": False}` 는 SQLAlchemy 2.0 에서 그대로 동작 (boolean `True` fallback 도 가능).
- **race 테스트 결정성**: testcontainers PG 가 빨라서 단순 `asyncio.gather` 두 호출은 race 가 우연히 안 일어나고 PASS — fix 없는 코드도 PASS = 무력 테스트. `asyncio.Event` (`t1_inside_fetch`/`t1_inside_list` + `release`) + slow 가짜 fetcher 로 T1 이 work 도중에 T2 가 entry FOR UPDATE 에서 대기하도록 강제. 두 별도 engine (per-test DB 의 `upgraded_db["async_url"]` 두 번 바인딩) 으로 PG row lock 실제 경쟁 검증. 검증된 패턴 — 향후 race 테스트는 같은 구조 재사용.
- **lock 보유 시간**: `register_webhook` 의 lock 은 GitHub `list_hooks + create/update_hook` 호출 동안 보유 — 정상 200ms~1s, 최악 30s timeout. 같은 project row 의 다른 write (PATCH git-settings 등) 가 그 동안 block. setup endpoint 라 OK (저빈도). 만약 hot path 에 같은 패턴 적용한다면 advisory lock / two-phase 검토.
- **M-6 갱신 시점 결정**: `_process_inner` 가 PLAN/handoff 변경 없어 early return 한 path 도 `last_synced_commit_sha` 갱신 — head 가 깨끗하게 검사된 commit 이라 다음 truncated push 의 base 후보로 유효. 단 success path 만 갱신, failure path 는 직전 base 보존 (재처리 정확성).
- **process_event 의 multi-flush vs single-commit**: `_apply_plan` 내부의 `await db.flush()` 들은 SQL 만 보내고 commit 안 함 — row lock 유지. `_apply_handoff` 의 `async with db.begin_nested()` 는 SAVEPOINT (외부 tx lock 유지). 실제 `db.commit()` 은 `process_event` outer 에서 한 번 — 이 구조라 entry FOR UPDATE 가 final commit 까지 lock 유지 가능. 향후 inner commit 추가되면 lock 모델 재검토.
- **Code review nit followup (B2 또는 후속)**:
  - sync_service.py module-level docstring (line 1-13) 의 step list 가 `last_synced_commit_sha` 갱신 누락 — 1줄 추가하면 정확. 본 phase 안 함 (M-6 commit 의 inline 코멘트로 충분).
  - test_git_settings_endpoint.py 의 race 테스트 docstring narrative 가 약간 idealized — 실제 race 는 lock 으로 만들어내는 ordering 임. 기능 영향 없음.
  - test_sync_service.py 의 `t1_inside_fetch` event 이름 — 50ms head-start 로 reliably T1 이지만 보장은 아님. 기능 OK.
- **next 할 일은 B2** (Phase 5b UI 후속). B2 머지 후 Phase 6 (Discord 알림) 진입.

---

## 2026-04-30 (Phase 5b)

- [x] **Phase 5b 완료** — Frontend UI (브랜치 `feature/phase-5b-frontend-ui`)
  - [x] **Backend small change**: `TaskResponse` 에 Phase 1 모델 누락 4 필드 노출 (`source / external_id / last_commit_sha / archived_at`) + 회귀 테스트 1건
  - [x] frontend `types/task.ts` 확장 + `TASK_SOURCE` enum (`'manual'` / `'synced_from_plan'`) + `types/git.ts` 신규 (5 인터페이스: GitSettings/Update/WebhookRegisterResponse/HandoffSummary/ReprocessResponse)
  - [x] `services/api.ts` 의 `git` 그룹 5 method (getSettings / updateSettings / registerWebhook / listHandoffs / reprocessEvent)
  - [x] `hooks/useGithubSettings.ts` — TanStack Query 5 훅 (`useGitSettings`, `useUpdateGitSettings`, `useRegisterWebhook`, `useHandoffs`, `useReprocessEvent`)
  - [x] `ProjectGitSettingsModal.tsx` — outer + inner `GitSettingsForm` (lint 회피 split). repo URL / PAT / plan_path / handoff_dir 폼 + Save + Webhook 등록 버튼 (`admin:repo_hook` 가이드 + GitHub PAT 페이지 링크)
  - [x] `HandoffHistoryModal.tsx` — branch 필터 + 표 (날짜 / 브랜치 / 작성자 / commit / tasks count) + 빈 상태 안내
  - [x] `TaskCard.tsx` — `SYNCED_FROM_PLAN` 배지 (파란색, `MANUAL` 은 기본 표시 X)
  - [x] `ProjectItem.tsx` 메뉴 통합 — 기존 `···` dropdown 에 "Git 연동 설정" + "Handoff 이력" 추가. 비-OWNER 는 메뉴 자체 숨김 (`{isOwner && ...}`).
  - [x] code review (sonnet) APPROVED — fixed I-1 (`webhookDisabled` 가 live `repoUrl` state 사용 — 사용자 즉시 enable) + M-3 (TASK_SOURCE 상수) + M-4 (dropdown min-w 140px) + M-1 (stale comment).
  - [x] **검증**: backend 170 tests pass, frontend `bun run build` clean, `bun run lint` 8 pre-existing (out of scope). **시각 검증은 사용자 dev server 직접** (PR 본문 체크리스트).

### 마지막 커밋

- pslog: `<sha> docs(handoff): Phase 5b 완료 + Phase 6 다음 할 일`
- 브랜치 base: `900fa20` (main, Phase 5a 머지 직후)

### 다음 (Phase 6 — Discord 알림 통합 또는 Phase 5 follow-up)

**옵션 A — Phase 6 (Discord 알림 통합, spec §11)**:
- [ ] `discord_service` 확장 — 체크 변경 요약 / handoff 누락 경고 / 롤백 알림 템플릿 3종
- [ ] `sync_service` 가 알림 트리거 (DB 변경 후 fire-and-forget BackgroundTask)
- [ ] `Project.discord_webhook_url` 미설정 시 silent skip
- [ ] cooldown 정책 (spec §8 — 3회 연속 실패 시 disable)

**옵션 B — Phase 5 follow-up fixes** (Phase 5a/5b code review 트래킹):
- [ ] **I-2 fix**: `register_webhook` SELECT FOR UPDATE → 동시 호출 race 차단
- [ ] **I-4 fix**: `process_event` CAS 가드 → reprocess race 차단
- [ ] **M-6 fix**: `sync_service` 가 처리 완료 시 `Project.last_synced_commit_sha` update (Phase 1 부터 미사용 컬럼)
- [ ] **M-10 fix**: `_auth_headers / _parse_repo / _raise_for_status` 를 public 으로 promote (`github_hook_service` 의 underscore import 위배 해소)
- [ ] **TaskCard ⚠️ handoff 누락 표시**: 데이터 정의 (Task `last_commit_sha` join → Handoff 존재 여부 — backend 필드 또는 계산 추가)
- [ ] **GitEventList 모달 + reprocess 호출 site**: `useReprocessEvent` hook 만 만들어둠 — 호출 site (sync 실패 이벤트 list 모달) 미구현

### 블로커

없음

### 메모 (2026-04-30 Phase 5b 추가)

- **Modal 진입 패턴**: spec §5.3 의 `pages/` 권고 무시, 코드베이스 컨벤션 (sidebar dropdown + 모달) 따름. `EditProjectModal` 스타일/구조 정확 매칭.
- **PAT 평문 입력 → setPat('') after save**: 응답에 PAT 절대 포함 안 됨 (Phase 5a backend redact 검증). frontend 도 console.log / queryKey 등 어디에도 PAT 누출 안 됨 (review 검증).
- **Outer + Inner `GitSettingsForm` split**: `useEffect([settings])` + `setState` 패턴이 `react-hooks/set-state-in-effect` lint 위배 → outer 가 settings fetch, inner 가 props 로 받아 useState 직접 초기화. `key={projectId}` 로 다른 project 전환 시 remount.
- **비-OWNER 메뉴 숨김**: backend 가 403 으로 차단할 수 있지만 frontend 에서 메뉴 자체를 숨겨 UX 개선 (plan 의 "단순화" 보다 한 단계 더). `ProjectItem.tsx` 의 `{isOwner && ...}`.
- **Vitest 미도입**: frontend 단위 테스트 셋업 본 phase 안 함. tsc/lint 만으로 회귀 확인. 향후 별도 phase.
- **시각 검증**: subagent 환경에서 dev server 띄우기 어려움 → 사용자가 PR 머지 전 직접 (`bun run dev` + `uvicorn`).
- **HandoffHistory + Reprocess 분리**: `useReprocessEvent` 훅 만들어둠. 호출 site 는 후속 (handoffs vs git-events 다른 list — 같은 모달에 합치면 복잡).
- **TASK_SOURCE 상수 사용**: TaskCard 에서 string literal `'synced_from_plan'` 대신 `TASK_SOURCE.SYNCED_FROM_PLAN` 사용 (typo / 리팩토링 안전).

---

## 2026-04-30 (Phase 5a)

- [x] **Phase 5a 완료** — Backend endpoints + 자동 webhook 등록 (브랜치 `feature/phase-5a-backend-endpoints`)
  - [x] `GitPushEvent.before_commit_sha` 컬럼 + CHECK 제약 (alembic `a1b2c3d4e5f6`, vanity SHA 수동 작성). `record_push_event` 가 payload.before 저장 (40자 hex 만), `sync_service._collect_changed_files` 가 base 우선 사용 (priority chain: before → last_synced → commits[-1] → head). `0*40` null-sha 는 skip (I-5 fix).
  - [x] `GET /api/v1/projects/{id}/git-settings` (멤버) — `git_repo_url / plan_path / handoff_dir / last_synced_commit_sha / has_webhook_secret / has_github_pat / public_webhook_url`. 평문 secret 절대 노출 안 함 (raw_text assertion 으로 검증).
  - [x] `PATCH /api/v1/projects/{id}/git-settings` (OWNER) — 부분 갱신, github_pat 입력 시 즉시 Fernet encrypt + `extra="forbid"` 스키마.
  - [x] `POST /api/v1/projects/{id}/git-settings/webhook` (OWNER) — `github_hook_service.list_hooks/create_hook/update_hook` (admin:repo_hook 권한 사용). 같은 callback url 의 hook 매칭 시 PATCH (secret rotate), 없으면 POST. URL 매칭은 lowercase + trailing `/` strip (I-3 fix). `_raise_for_status` 가 Authorization 헤더 sanitize (I-1 fix — PAT exc.request.headers leak 차단).
  - [x] `GET /api/v1/projects/{id}/handoffs?branch=...&limit=...` (멤버) — pushed_at desc, raw_content 제외, limit clamp 1~200.
  - [x] `POST /api/v1/projects/{id}/git-events/{event_id}/reprocess` (OWNER) — 처리 실패 이벤트 reset + Phase 4 의 `_run_sync_in_new_session` BackgroundTask 재호출.
  - [x] `pslog_public_url` settings + `.env` (기본값 `http://localhost:8000` — prod 는 Cloudflare Tunnel URL).
  - [x] code review (opus) APPROVED — fixed I-1/I-3/I-5 + missing 404 tests. **169 tests passing** (Phase 1+2+3+4 137 + Phase 5a 32).

### 마지막 커밋

- pslog: `<sha> docs(handoff): Phase 5a 완료 + Phase 5b 다음 할 일`
- 브랜치 base: `44590c6` (main, Phase 4 머지 직후)

### 다음 (Phase 5b — Frontend UI)

- [ ] `frontend/src/services/githubApi.ts` — git-settings / handoffs / reprocess axios 호출 (Phase 5a endpoint 호출)
- [ ] `frontend/src/hooks/useGithubSettings.ts` — TanStack Query 훅 (GET 캐시 + PATCH/POST mutation)
- [ ] `frontend/src/pages/ProjectGitSettings.tsx` — repo URL / PAT / plan_path / handoff_dir 입력 폼 + "Webhook 등록" / "재등록" 버튼. PAT 발급 가이드 (admin:repo_hook 스코프).
- [ ] `frontend/src/pages/HandoffHistory.tsx` — 브랜치별 이력 + 재처리 버튼 (sync 실패 이벤트)
- [ ] `frontend/src/components/TaskCard.tsx` 수정 — `source` 배지 (`MANUAL` / `SYNCED_FROM_PLAN`) + handoff 누락 ⚠️ 표시. **데이터 정의 필요** (Phase 5b 진입 시 결정).
- [ ] dev server (vite) + 브라우저 수동 검증

### 블로커

없음

### 메모 (2026-04-30 Phase 5a 추가)

- **`pslog_public_url` 기본값 localhost**: prod 배포 시 Cloudflare Tunnel URL 로 환경변수 override 필수. 자동 webhook 등록이 localhost 로 callback 등록하면 GitHub 이 호출 못 함 — 수동 e2e 검증 시 주의.
- **PAT 권한 범위**: GitHub PAT 는 `admin:repo_hook` 스코프 필요 (자동 webhook 등록용). Phase 5b ProjectGitSettings UI 에 도움말 텍스트 필수.
- **webhook 자동 등록 = secret rotate**: 매 호출마다 새 secret 생성. 기존 hook 있으면 PATCH 로 secret 갱신 — UI 에서 "재등록" 버튼이 사실상 "secret rotate" 효과 임을 명시.
- **Vanity revision id `a1b2c3d4e5f6`**: 수동 작성 SHA. autogen 의 random hex 와 다른 패턴이지만 chain 정상 (`down_revision = '274c0ed55105'`). 후속 마이그레이션은 다시 autogen 으로.
- **code review followup (Phase 5b/6 트래킹)**:
  - **I-2 (concurrent webhook registration race)**: 두 OWNER 가 동시 `POST /webhook` 호출 → DB 의 webhook_secret 가 stale 될 수 있음 (call A 의 secret 이 commit 마지막에 들어가지만 GitHub side 는 call B 의 secret). 현재는 narrow window. Phase 5b UI 에서 button debounce + post-merge 에 SELECT FOR UPDATE 적용 검토.
  - **I-4 (reprocess race)**: 사용자가 in-flight sync 와 동시에 재처리 트리거 → 두 process_event 동시 실행. UNIQUE 제약이 일부 보호하지만 TaskEvent 중복 가능. Phase 5b UI 에서 "처리 중" 상태 표시 + post-merge 에 CAS 가드 검토.
  - **M-6 (last_synced_commit_sha 미사용)**: Phase 1 에서 컬럼 추가됐지만 어디서도 write 안 함. sync_service 가 처리 완료 시 update 해야 하는데 누락. Phase 5b 또는 별도 fix PR.
  - **M-10 (private import from git_repo_service)**: `github_hook_service` 가 `_auth_headers / _parse_repo / _raise_for_status` (underscore = module-private 위배) 를 import. 후속 refactor 에서 promote 또는 두 모듈 합치기 검토.

---

## 2026-04-30 (Phase 4)

- [x] **Phase 4 완료** — sync_service + git fetch (브랜치 `feature/phase-4-sync-service`)
  - [x] `Project.github_pat_encrypted` 컬럼 추가 (alembic `274c0ed55105`, Phase 1 누락분 보강) + 회귀 테스트
  - [x] `git_repo_service` — `fetch_file` (Contents API + base64 decode + 404→None) + `fetch_compare_files` (Compare API). httpx mock 으로 8 단위 테스트 (httpx.Response `_request` 누락 회피로 explicit Request + `_raise_for_status` 헬퍼 채택)
  - [x] `sync_service.process_event(db, event, *, fetch_file, fetch_compare)` — 의존 주입 / 멱등 가드 / 변경 파일 검사 (commits[*].modified ∪ added 또는 truncated 시 Compare API)
  - [x] PLAN: 신규 task INSERT (`SYNCED_FROM_PLAN`), 체크 → DONE (`CHECKED_BY_COMMIT`), 언체크 (DONE→TODO 롤백 — `UNCHECKED_BY_COMMIT`), PLAN 에서 사라진 task → `archived_at` (`ARCHIVED_FROM_PLAN`), **PLAN 에 다시 등장 → un-archive (히스토리 보존)**
  - [x] handoff: `Handoff` INSERT 1행 (UNIQUE `(project_id, commit_sha)` SAVEPOINT 멱등 — Phase 2 패턴), `parsed_tasks` / `free_notes` / `raw_content` 보존, `MalformedHandoffError` 시 `event.error` 기록
  - [x] webhook endpoint: `BackgroundTasks.add_task(_run_sync_in_new_session, event.id)` — 자체 세션 + 실제 fetcher 주입, 예외는 `logger.exception` 로 보존
  - [x] reaper callback: lifespan 에서 **이벤트마다 fresh session** 으로 sync_service 호출 (한 이벤트 poison 이 다음 이벤트로 전파 안 되게)
  - [x] **plan_parser 하드닝** (Phase 3 code review I-2/I-3): title 안의 em-dash / 백틱 / `@` 가 잘못 추출되지 않게 positional 파싱 (`_TITLE_DELIMITER_RE = " — (?=@|\`)"`)
  - [x] **code review 3-bug fix** (final review): I-1 (un-archive on PLAN re-add), I-2 (poisoned session 후 commit 실패 — `rollback` + `autoflush=False` + `event` mutate + commit), I-3 (reaper 공유 세션 → per-event session)
  - [x] **137 tests passing** (Phase 1 41 + Phase 2 32 + Phase 3 30 + Phase 4 34)

### 마지막 커밋

- pslog: `<sha> docs(handoff): Phase 4 완료 + Phase 5 다음 할 일` (브랜치 `feature/phase-4-sync-service`)
- 브랜치 base: `3525a21` (main, Phase 3 머지 직후)
- 머지 전 PR 생성 + 사용자 검토 단계

### 다음 (Phase 5 — UI + 자동 webhook 등록)

- [ ] `ProjectGitSettings.tsx` — repo URL / PAT / plan_path / handoff_dir 입력 폼
- [ ] 자동 webhook 등록 (GitHub API `POST /repos/{owner}/{repo}/hooks`, 프로젝트별 secret 자동 생성)
- [ ] `TaskCard.tsx` — `source` 배지 + handoff 누락 ⚠️ 표시
- [ ] `HandoffHistory.tsx` — 브랜치별 handoff 이력
- [ ] `POST /api/v1/projects/{id}/git-events/{id}/reprocess` — 사용자 수동 재처리 (sync 실패 이벤트)
- [ ] commits_truncated base 정확화 — `GitPushEvent.before_commit_sha` 컬럼 추가 (현재 fallback `commits[-1].id` 는 head 와 동일 — 빈 diff. 실제 영향은 truncated push 가 PLAN/handoff 변경한 케이스로 한정)

### 블로커

없음

### 메모 (2026-04-30 Phase 4 추가)

- **GitHub PAT NULL 처리**: PAT 없으면 unauthenticated 호출. 공개 repo 만 가능, rate limit 60/h. app-chak 같은 private repo 에선 PAT 필수. Phase 5 UI 에서 PAT 입력 강제 유도.
- **commits_truncated base fallback**: 정확한 `before` 가 webhook payload 에 있지만 GitPushEvent 컬럼에 저장 안 함 (Phase 2 plan 누락). 본 phase 에선 `Project.last_synced_commit_sha or commits[-1].id` fallback. `commits[-1]` 은 GitHub webhook 규칙상 head 와 같아 빈 diff — Phase 5 에서 `before_commit_sha` 추가로 보강.
- **BackgroundTask vs reaper**: webhook endpoint 가 BackgroundTask 로 sync 시작 → 정상 흐름. 컨테이너 재시작 시 in-flight 손실 → reaper 가 5분 grace 후 회수. **reaper 가 sync_service.process_event 를 callback 으로 받음 — 같은 코드 경로**. processed_at 가드로 idempotent.
- **error 정책 (자동 재시도 안 함)**: sync 실패 시 `event.error` 기록 + `processed_at = now()`. 사용자 수동 재처리 endpoint 는 Phase 5. 그동안 reaper 는 `processed_at IS NULL` 만 픽업 — 자동 무한 retry 회피.
- **poisoned session 패턴**: `_apply_plan` 안에서 IntegrityError 가 나면 SQLAlchemy 가 session 을 rollback-required 상태로 마킹. 그 위에서 `event.error` 세팅 후 commit 시도 → `PendingRollbackError`. 해결: rollback → `autoflush=False` → event mutate → commit. autoflush 잠금은 commit 직전 stale state 자동 flush 회피용.
- **un-archive 정책**: spec §4.1 은 archived → re-add 케이스 명시 안 함. pslog 에서는 history 보존 (TaskEvent / Comment / assignee) 위해 같은 row 의 `archived_at = None` 으로 처리. 재 INSERT 안 함. partial UNIQUE `(project_id, external_id) WHERE external_id IS NOT NULL` 가 자동으로 catch 했음 — 이걸 발견해 정책 명문화.
- **plan_parser title 파싱 변경**: `_TITLE_DELIMITER_RE = re.compile(r" — (?=@|\`)")` lookahead. title 안에 단독 ` — ` 또는 백틱 가능. assignee/path 는 delimiter 이후 영역에서만 검색. Phase 3 spec 의 §6.1 라인 형식과 호환 유지 — 13 기존 테스트 무회귀.
- **Handoff `parsed_tasks` 형식**: `[{external_id, checked, extra}]` (sections[0] 만). `free_notes = {last_commit, next, blockers, subtasks: [{parent_external_id, checked, text}]}`. 다중 날짜 history 는 `raw_content` 에 보존 — Phase 7 brief_service 가 활용.
- **Handoff UNIQUE conflict 테스트 deviation**: 원안의 "다른 GitPushEvent + 같은 head_sha" 케이스가 Phase 1 의 `uq_git_push_project_head` UNIQUE 에 막힘. 대신 Handoff row 를 미리 seed 하고 process_event 가 SAVEPOINT silent skip 하는지 직접 검증 — 더 직접적.

---

## 2026-04-30

- [x] **Phase 3 완료** — PLAN/handoff 파서 (브랜치 `feature/phase-3-parsers`)
  - [x] `ParsedPlan` / `ParsedTask` Pydantic 스키마 (`extra="forbid"`)
  - [x] `ParsedHandoff` / `HandoffSection` / `CheckItem` / `Subtask` / `FreeNotes` Pydantic 스키마
  - [x] `plan_parser_service.parse_plan()` — `## 태스크` 섹션 제한, `[task-XXX]` 형식 + `@user` + `` `path` `` 추출, `DuplicateExternalIdError` raise
  - [x] `handoff_parser_service.parse_handoff()` — 헤더 / `## YYYY-MM-DD` 섹션 / 들여쓰기 0 체크박스 / 들여쓰기 ≥ 2 서브태스크 / `### 마지막 커밋·다음·블로커` 자유 영역, `MalformedHandoffError`
  - [x] sections date desc 정렬 (sections[0] = active)
  - [x] `---` HR 구분선 처리 (실제 handoff 관례 — date 섹션간 분리자가 trailing whitespace 로 들어가지 않게)
  - [x] 알 수 없는 `### 헤더` 아래 체크박스 leak 차단 (code review I-1)
  - [x] **103 tests passing** (Phase 1 41 + Phase 2 32 + Phase 3 30: 13 plan_parser + 17 handoff_parser)

### 마지막 커밋

- pslog: `<sha> docs(handoff): Phase 3 완료 + Phase 4 다음 할 일` (브랜치 `feature/phase-3-parsers`)
- 브랜치 base: `c3a2817` (main, Phase 2 머지 직후)
- 머지 전 PR 생성 + 사용자 검토 단계

### 다음 (Phase 4 — sync_service + git fetch)

- [ ] `git_repo_service` (GitHub Contents API + Compare API) — PAT Fernet 복호화 재사용
- [ ] `sync_service` — webhook → fetch → parse → DB 반영 + TaskEvent 생성
- [ ] `push_event_reaper` callback 주입 (Phase 2 stub 교체)
- [ ] 멱등성 (CRITICAL — 같은 webhook 2번 → 1번 반영)
- [ ] PLAN 에서 사라진 task → `archived_at` soft-delete
- [ ] 체크 → 언체크 (DONE → TODO 회귀) 처리
- [ ] **Phase 3 파서 하드닝 (code review I-2 / I-3)**: title 안의 em-dash 가 잘리는 문제 + assignee/path 정규식이 title 영역까지 스캔하는 문제. 위치 기반 (positional) 파싱으로 sync_service 작성과 함께 보강.

### 블로커

없음

### 메모 (2026-04-30 Phase 3 추가)

- **파서는 순수 함수**: DB / 외부 API 의존 없음 — 테스트는 testcontainers 미사용 (pytest 기본). 0.08s 만에 30 tests 완료.
- **들여쓰기 인식**: `(?:    |\t|  )+` — 스페이스 2/4 또는 탭. code review M-1 에서 3-space 들여쓰기는 silent drop 됨 지적. PLAN 작성 가이드에 들여쓰기 규약(2 또는 4 스페이스, 또는 탭) lint 추가 검토.
- **`---` HR 처리**: 실제 `handoffs/main.md` 가 date 섹션 사이에 `---` 구분선 사용 (3 occurrences). 이게 마지막 free-note 영역 (`### 블로커`) 의 raw 에 trailing 으로 따라붙어 `"없음\n\n---"` 문제 발생. `_parse_section_body` 에서 `---` 만나면 `current_free_key = None` 으로 reset.
- **알 수 없는 ### 헤더 leak (I-1 fix)**: `_FREE_NOTE_HEADERS` dict 외의 H3 (예: `### 회의록`) 가 등장하면 `current_free_key = None` 이 되어 그 아래 체크박스가 다시 `_TOP_CHECK_RE` 매칭으로 빠져 `checks` 에 leak되던 문제. `in_h3_zone` 플래그로 H3 진입 후 체크박스 매칭 차단. 회귀 테스트 추가.
- **Em-dash 전용 헤더 RE**: `_HEADER_RE` 가 `—` (U+2014) 만 허용 — `--` ASCII 허용 안 함. Phase 4 sync_service 의 에러 메시지에 명시 필요.
- **에러 분류 결정**: 형식 깨짐 라인은 skip (parsing-resilient), 결정적 fail (헤더/날짜 부재, ID 중복) 만 예외. Phase 4 sync_service 가 예외 잡아 `GitPushEvent.error` 기록.

---

## 2026-04-29

- [x] **Phase 2 완료** — webhook 수신 endpoint + 서명 검증 + reaper (브랜치 `feature/phase-2-webhook-receive`)
  - [x] Fernet 마스터 키 (`pslog_FERNET_KEY`) + `app/core/crypto.py` (encrypt_secret / decrypt_secret / generate_webhook_secret)
  - [x] `cryptography==44.0.0` 의존성 핀
  - [x] `GitHubPushPayload` Pydantic 스키마 (6 nested models, `extra="ignore"`, `branch` property, `to_commits_json()`)
  - [x] github_webhook_service: HMAC-SHA256 (constant-time) + repo URL 정규화 매칭 (.git/trailing-slash/case 흡수) + GitPushEvent INSERT (UNIQUE 충돌 SAVEPOINT silent skip)
  - [x] commits_truncated 플래그 (len >= 20, `GITHUB_WEBHOOK_COMMITS_CAP` 상수)
  - [x] discord-summary endpoint 분리 → `app/api/v1/endpoints/discord.py` (URL 변동 없음)
  - [x] `webhooks.py`는 GitHub 전용으로 정리, `POST /api/v1/webhooks/github` 마운트
  - [x] 응답 정책: 401 (서명 실패/secret 없음), 200 (정상/unknown repo silent ACK/중복 멱등), 500 (Fernet 복호화 실패)
  - [x] `push_event_reaper` (`REAPER_GRACE = 5min`, callback pluggable — Phase 4 sync 주입), `run_reaper_once()` lifespan hook
  - [x] alembic `fileConfig(disable_existing_loggers=True)` 함정 conftest 회피 (`_reenable_app_loggers` + `caplog` autouse handler)
  - [x] **73 tests passing** (Phase 1 41 + Phase 2 신규 32: 3 crypto + 4 schema + 13 service + 8 endpoint + 4 reaper)

### 마지막 커밋

- pslog: `6ed9053 feat(phase2): startup hook — reaper 1회 호출 (DB 실패 시 부팅 진행)` (브랜치 `feature/phase-2-webhook-receive`)
- 브랜치 base: `e1aa4f1` (main, Phase 1 머지 직후)
- 머지 전 PR 생성 + 사용자 검토 단계

### 다음 (Phase 3 — PLAN/handoff 파서)

- [ ] `plan_parser_service` (PLAN.md → `[{external_id, title, assignee, paths}]`, 정규식)
- [ ] `handoff_parser_service` (체크박스 + `### 마지막 커밋`/`### 다음`/`### 블로커` 자유 영역)
- [ ] 들여쓰기 0인 최상위 체크박스만 DB 반영, 들여쓰기 ≥ 2는 `free_notes.subtasks`로 보존
- [ ] `external_id` 중복 reject (PLAN 단계 + DB UNIQUE 2차 방어)
- [ ] 텍스트 입력만으로 단위 테스트 — 파일 fetch는 Phase 4

### 블로커

없음

### 메모 (2026-04-29 추가)

- **`record_push_event` SAVEPOINT 패턴**: UNIQUE 충돌 시 plan 의 flat rollback 대신 `async with db.begin_nested()` 채택. 이유: 테스트의 함수-스코프 `async_session` 이 외부 ORM 객체(`proj` 등)를 보존해야 함. flat rollback 시 `MissingGreenlet` 발생. 프로덕션은 `Depends(get_db)` 가 요청별 fresh 세션이라 둘 다 정상이지만 SAVEPOINT 가 더 일반적이고 안전함.
- **Fernet 키 회전 운영 절차 미정**: `pslog_FERNET_KEY` 회전 시 모든 `webhook_secret_encrypted` 가 복호화 불가 → 운영 문서 별도 작성 필요. 첫 프로덕션 배포 전 잠금.
- **`InvalidToken` 핸들러**: 현재 endpoint 가 `cryptography` 직접 import. Phase 4 sync_service 진입 시 service 레이어로 wrapper 옮길지 검토 (router 가 외부 라이브러리에 직접 의존하지 않게).
- **알림 정책**: Phase 2 는 webhook 수신만. unknown repo 200 ACK 는 GitHub 재전송 방지 의도 — 운영 시 unknown repo 가 빈번하면 webhook 등록 실수 의심. log 모니터링 기준 추가 필요.
- **alembic + python logging 함정**: `alembic.ini` 의 `[loggers]` 섹션은 `disable_existing_loggers=True` 기본값 — `app.*` 로거 silent disable. 본 phase 에서 conftest 회피 추가. 후속 plan 작성 시 logging 단위 테스트는 이 패턴 주의.

---

## 2026-04-28

- [x] **Phase 1 완료** — pslog 본체 alembic 마이그레이션 + pytest 인프라 (브랜치 `feature/phase-1-models-migrations`)
  - [x] 테스트 인프라: pytest 8.3.4 + pytest-asyncio + testcontainers[postgres] (Docker로 PG 16 띄움) + psycopg, async DB fixture, 격리 패턴 (function-scope CREATE/DROP per-test DB)
  - [x] enum 확장: TaskSource, LogLevel, ErrorGroupStatus, TaskEventAction +4값 (모두 대문자 NAME 박힘 — SQLAlchemy 기본 + 기존 `taskstatus` 패턴 일관)
  - [x] Project +6 필드 (git_repo_url, git_default_branch, plan_path, handoff_dir, last_synced_commit_sha, webhook_secret_encrypted) + CHECK 40자 hex on last_synced_commit_sha
  - [x] Task +4 필드 (source, external_id, last_commit_sha, archived_at) + UNIQUE 부분 인덱스 + CHECK 40자 hex on last_commit_sha
  - [x] 신규 모델 6개: Handoff, GitPushEvent, LogIngestToken, RateLimitWindow (composite PK), ErrorGroup, LogEvent (각 모델 정의 + 모든 SHA 컬럼에 CHECK 제약)
  - [x] pg_trgm extension + log_events 일별 파티션 (PARTITION BY RANGE received_at, PK (id, received_at)) + 다음 30일 pre-create
  - [x] 인덱스 5종 (project_level_received / fingerprint partial / version_sha / unfingerprinted partial / message gin_trgm_ops partial)
  - [x] 단일 alembic revision (`c4dee7f06004_phase1_logs_handoffs_git`)
  - [x] 회귀 테스트: 기존 데이터 보존, alembic up/down roundtrip, CHECK/UNIQUE 동작, ORM round-trip enum, 파티셔닝 검증
  - [x] env.py에 신규 6 모델 import (autogenerate 함정 회피)
  - [x] **41 tests passing** (3 smoke + 3 enum + 4 모델 검증 + 14 신규 모델 + 6 constraint + 4 migration + 3 partitioning + 1 ORM round-trip + 3 기존)

### 마지막 커밋

- pslog: `27352db test+fix(phase1): final review fixups (test_partitioning, env.py imports, ORM round-trip)` (브랜치 `feature/phase-1-models-migrations`)
- 브랜치 base: `2d374e9 chore: .worktrees/ 디렉토리 ignore` (main)
- 머지 전 PR 생성 + 사용자 검토 단계

### 다음 (Phase 2 — Webhook 수신만)

- [ ] `POST /api/v1/webhooks/github` endpoint
- [ ] 서명 검증 (프로젝트별 secret, Fernet 복호화)
- [ ] GitPushEvent INSERT 만 (처리 로직 X — Phase 4에서 sync_service)
- [ ] push_event_reaper 부팅 hook (`processed_at IS NULL AND received_at < now() - 5min` 회수)
- [ ] commits_truncated 플래그 처리 (length == 20)

### 블로커

없음

### 메모 (2026-04-28 추가)

- **Subagent-Driven Development 페이스**: 14 task를 묶음 처리 (Task 2-3 / 4-6 / 7-9 / 10-11 / 12 / 13). 단순 모델 정의는 한 implementer에 batch dispatch + spec/quality 묶음 review. 토큰/시간 효율 좋음 (개별 dispatch 대비 ~1/3).
- **enum 케이스 결정**: SQLAlchemy 2.0 + `class Foo(str, enum.Enum): MANUAL = "manual"` 매핑은 DB에 enum **NAME** (대문자) 박음. value 아님. 기존 `taskstatus`/`taskeventaction`이 대문자로 박혀있어서 이 패턴 일관 유지. ORM round-trip 테스트로 검증 완료.
- **`mapped_column(default=X)` Python init-time 미적용**: SQLAlchemy 2.0 `default=`는 INSERT 시점만 주입. Python `__init__` 시점엔 None. 우리 default 검증 테스트(Project/Task/LogIngestToken/ErrorGroup) 통과 위해 `__init__` override 패턴 추가 (`kwargs.setdefault`). plan 작성 시 SQLAlchemy 의미 혼동했던 부분 — 후속 plan 작성 시 주의.
- **pg_partman 미도입**: 30일 pre-create 만. Phase 7 진입 시 일별 자동 GC 도입.
- **Python 3.12.13 venv (homebrew python@3.12)**: 맥미니에 처음 pslog 백엔드 셋업. `backend/runtime.txt` 의 `python-3.12.12` 와 정합. `requirements.txt` 핀 그대로 (pydantic 2.5.3 + sqlalchemy 2.0.25 등). Python 3.14 시도 시 pydantic-core/greenlet 빌드 실패 — 3.12 권장.
- **Phase 2 진입 전 Fernet 마스터 키 환경변수**: `pslog_FERNET_KEY` 셋업 필요 (webhook_secret_encrypted 복호화).
- **task-automation Phase 4 안정화 후** error-log Phase 2(ingest endpoint) 진입 가능 (선행 의존: Handoff/Task의 commit_sha join key 안정 필요).

---

## 2026-04-27

- [x] 두 설계서 + 어제 handoff 파일 git 커밋 (pslog `7f7f692`)
- [x] 두 설계서 교차 일관성 보강 — Plan 에이전트 독립 리뷰 후 4개 warning 패치
  - [x] error-log §5.4 wire format 명세 추가 (요청 헤더 + JSON 본문)
  - [x] error-log §4.2 archived task의 git 컨텍스트 join 정책 명시
  - [x] task-automation `commit_sha`/`last_commit_sha` 40자 hex full 계약 명시 + Decision Log 항목 (Phase 1 alembic CHECK 제약 대상)
  - [x] task-automation §13 Open Q #6 — Brief single-flight lock workers=1 가정 + 다중 워커 승격 경로
- [x] **Phase 0 완료 (app-chak 레포)** — PR #1 머지
  - [x] `CLAUDE.md` `## pslog 연동 규칙` 섹션
  - [x] `PLAN.md` 초안 (첫 마스터 태스크 = pslog 연동 자체, 5/2~5/3 기획 회의 후 추가)
  - [x] `handoffs/README.md` + 본 브랜치 handoff
  - [x] `Dockerfile` + `docker-compose.yml` `APP_VERSION_SHA` build arg 주입
  - [x] `backend/app/utils/pslog_log_handler.py` — `PIIFilter` + `pslogHandler` (배치 큐 / gzip / Bearer / 5xx exponential backoff / 4xx silent drop / 큐 한도 1000건·5MB / `X-pslog-Dropped-Since-Last` 헤더 / atexit 5초)
  - [x] `configure_logging()` 확장 + `main.py` 에서 settings 의 모든 비밀 키를 PIIFilter `exact_secrets` 로 전달
  - [x] 단위 테스트 27개, 전체 backend 127/127 통과 회귀 없음

### 마지막 커밋

- pslog: `7f7f692 docs: 에러 로그 설계서 + 두 설계서 교차 일관성 보강` (origin/main)
- app-chak: PR #1 머지 — `feat: pslog 에러 로그 핸들러 + 연동 인프라 (Phase 0)` (origin/main)

### 다음 (Phase 1 — pslog 본체 alembic 마이그레이션)

- [ ] 신규 테이블 모델
  - [ ] `LogEvent` (PostgreSQL 일별 range partition + DROP PARTITION GC)
  - [ ] `ErrorGroup` (status enum: OPEN/RESOLVED/IGNORED/REGRESSED)
  - [ ] `LogIngestToken` (`<key_id>.<secret>` 포맷, bcrypt secret_hash)
  - [ ] `RateLimitWindow` (PostgreSQL UPSERT 기반)
  - [ ] `Handoff` (project_id, branch, commit_sha UNIQUE)
  - [ ] `GitPushEvent`
- [ ] 기존 모델 확장
  - [ ] `Task` 4 필드 추가: `source`, `external_id`, `last_commit_sha`, `archived_at`
  - [ ] `Project` Git-aware 필드 추가 (`repo_url`, `handoff_dir`, `last_synced_commit_sha`, `webhook_secret_encrypted`)
  - [ ] `external_id` UNIQUE 부분 인덱스
- [ ] CHECK 제약: `commit_sha ~ '^[0-9a-f]{40}$' OR commit_sha IS NULL` (Decision Log 2026-04-26 Rev2)
- [ ] 마이그레이션 회귀 테스트 (CRITICAL — 기존 데이터 무손실)

### 블로커

없음

### 메모 (2026-04-27 추가)

- **archived task join 정책 (PR 리뷰 결정)**: `Task.archived_at IS NOT NULL` row 도 LogEvent git 컨텍스트 join 에 포함, UI 에서 `(archived)` 배지 — Phase 4 GitContextPanel 구현 시 반영.
- **app-chak self-hosted runner Docker 이슈**: `~/.docker/config.json` 의 `credsStore: "desktop"` 가 비대화형 launchd 세션에서 keychain unlock 실패. 제거 + URL inline `x-access-token:$GITHUB_TOKEN` 으로 우회. pslog 본체도 self-hosted runner 가면 동일 함정 — 운영 노트 참고.
- **pslog_log_handler `exact_secrets` 패턴**: app-chak 은 `JWT_SECRET_KEY` + Google/Kakao/OpenWeather/Solar/Places API 키 6종을 통째로 PIIFilter 에 넣음. pslog 본체도 동일 패턴 적용 권고.
- **pslog 측 ingest endpoint** (`/api/v1/log-ingest`) 미구현 상태 — app-chak 은 `pslog_LOG_ENDPOINT` 비워둬서 핸들러 자동 비활성. Phase 2 진입 후 e2e 검증.
- **2026-05-02~03 주말 확장 기획 회의** — 회의 후 app-chak `PLAN.md` 에 마스터 태스크 추가, pslog 측에서 sync 동작 실제 테스트 가능.

---

## 2026-04-26

- [x] AI 태스크 자동화 설계서 v2 (`docs/superpowers/specs/2026-04-26-ai-task-automation-design.md`)
  - [x] 그래뉼래리티 분리 (마스터 = 0.5~3일, handoff 서브 체크박스 = 자유 영역)
  - [x] Task 상태 모델 통합 (`status` enum 재사용, 별도 `checked_at` 신설 X)
  - [x] `TaskEventAction` enum 확장 4종
  - [x] `Task.archived_at` (PLAN 삭제 soft-delete)
  - [x] `external_id` 프로젝트 내 UNIQUE 제약
  - [x] `Project.webhook_secret_encrypted` per-project (Fernet)
  - [x] Background task 부팅 reaper
  - [x] Webhook commits 길이>20 fallback (Compare API)
  - [x] handoff 누락 정책 강화 (silent → 항상 가시화)
  - [x] Phase 0 (app-chak 선행 작업) 분리
  - [x] 마이그레이션 회귀 테스트 CRITICAL

- [x] 에러 로그 + Git 상관관계 설계서 v3 (`docs/superpowers/specs/2026-04-26-error-log-design.md`)
  - [x] `LogEvent.fingerprinted_at` + 부팅 reaper
  - [x] `version_sha` 형식 검증 + `unknown` 비율 헬스체크
  - [x] 토큰 포맷 `<key_id>.<secret>` (bcrypt hot path 회피)
  - [x] `RateLimitWindow` PostgreSQL UPSERT (다중 워커 정확)
  - [x] PostgreSQL 일별 range partition + `DROP PARTITION` GC
  - [x] `ErrorGroup` status 전이 ASCII 다이어그램
  - [x] 알림 cooldown 3종 (신규/spike/regression)
  - [x] 핸들러 pslog 다운 정책 (큐 1000건/5MB, backoff, atexit, drop_count 헤더)
  - [x] `pg_trgm` 풀텍스트 검색 Phase 5 격상
  - [x] 핸들러 배포 방식 결정 (app-chak 레포 직접 복사)

### 마지막 커밋

아직 커밋 X — 두 설계서 + 본 handoff 파일이 untracked 상태.
직전 main HEAD: `3daf363 refactor: 사용하지 않는 agents 파일 정리`

### 다음 (내일 이어서)

- [ ] 두 설계서 + handoff 파일 git 커밋
- [ ] **Phase 0 시작 (app-chak 레포 측)** — `/Users/arden/Documents/ardensdevspace/app-chak/`
  - [ ] `CLAUDE.md`에 pslog 연동 규칙 추가 (task-automation §11.1)
  - [ ] 초기 `PLAN.md` 작성 (마스터 태스크 목록, 골디락스 룰 0.5~3일 적용)
  - [ ] `handoffs/` 디렉토리 + 사용 가이드 README
  - [ ] `APP_VERSION_SHA` 환경변수 주입 메커니즘 (Docker build arg)
  - [ ] `backend/app/utils/pslog_log_handler.py` 단일 모듈 작성
    - [ ] `logging.Handler` 서브클래스 + 배치 큐
    - [ ] `PIIFilter` (이메일/JWT/password/Bearer 패턴)
    - [ ] HTTP backoff (1s/5s/30s/5min)
    - [ ] atexit 5초 타임아웃
    - [ ] drop_count 헤더 (`X-pslog-Dropped-Since-Last`)
- [ ] Phase 0 끝나면 pslog Phase 1 (alembic 마이그레이션) 진입

### 블로커

없음

### 메모

- 두 설계서 진행 순서: **task-automation 먼저 Phase 4 안정화 → error-log 진입.** error-log는 task-automation의 `Handoff.commit_sha` / `Task.last_commit_sha`를 join key로 사용하므로 선행 의존.
- pslog 운영 가정: **uvicorn `workers=1`** (맥미니 단일 머신). 다중 워커 필요해지면 spike 감지 부정확 — 운영 문서에 박아둘 것.
- PII 필터 패턴 셋은 Phase 0 시점에 app-chak 코드 실제로 보고 확정 (현재 미정).
- 두 문서 모두 한국어, 14개 섹션 구조, Decision Log로 끝나는 동일 포맷 유지.
- `/plan-eng-review` 2회 거치며 발견된 가장 큰 함정: **task-automation에서 잡은 reaper 패턴을 error-log 초안에서 또 빼먹었음.** 새 백그라운드 작업 추가할 때마다 reaper 체크리스트화 필요.
