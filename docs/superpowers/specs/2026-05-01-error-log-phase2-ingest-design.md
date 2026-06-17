# Error-log Phase 2a — Ingest Endpoint + Token API (Design)

**Status**: Draft → 사용자 검토 후 implementation plan 작성 (`writing-plans`).

**Date**: 2026-05-01

**Goal**: error-log spec (`2026-04-26-error-log-design.md`) 의 Phase 2 본편을 두 sub-phase 로 분할한 첫 번째 — **2a: 토큰 API + ingest endpoint + rate limit**. app-chak 의 Phase 0 handler 가 미사용 상태로 대기 중 (`pslog_LOG_ENDPOINT` 비어있음) — 본 phase 머지 즉시 e2e 동작.

**선행**: pslog `main` = `7e51c20` (Phase 6 PR #15 머지 직후). backend tests 198 baseline. alembic head = `7c6e0c9bb915`. **마이그레이션 신규 없음** — 모든 컬럼 Phase 1 에 포함됨.

---

## 1. Scope

본 phase 의 deliverable:

1. **`log_ingest_service`** 신규 — 6 함수 (parse_token / verify_token / check_rate_limit / validate_event / insert_events / ingest_batch). 토큰 검증 + rate limit + partial validation + batch INSERT.
2. **`POST /api/v1/log-ingest`** endpoint — 외부 (app-chak) 가 호출. Bearer 토큰 인증. partial success 응답.
3. **`POST /api/v1/projects/{id}/log-tokens`** endpoint — OWNER 전용 토큰 발급. 응답에 평문 token 1회만.
4. **`DELETE /api/v1/projects/{id}/log-tokens/{token_id}`** endpoint — OWNER 전용. soft delete (`revoked_at = now()`).

본 phase 가 **하지 않는** 것:
- 모델 변경 / alembic (Phase 1 에 모두 포함)
- `log_fingerprint_reaper` (Phase 3 에 fingerprint_service 와 함께 묶음 — reaper 가 fingerprint logic 호출)
- `log_health_service` (unknown SHA 비율 / 시계 어긋남 — Phase 5 UI 와 함께 도입)
- Frontend `LogTokensPage` (Phase 5 UI 통합 phase)
- ErrorGroup / fingerprint 처리 (Phase 3)
- Discord 알림 (Phase 6 of error-log spec)
- `GET /log-tokens` 토큰 목록 조회 (Phase 5 LogTokensPage 와 함께)

본 phase 머지 후 e2e 가능: pslog API 로 토큰 발급 → app-chak `.env` 의 `pslog_LOG_INGEST_TOKEN` 설정 → handler 자동 활성 → LogEvent 가 pslog DB 에 들어옴 (fingerprint=NULL).

---

## 2. Important Contracts

### 2.1. 토큰 형식 — `<key_id>.<secret>`

- `key_id` = `LogIngestToken.id` (UUID 문자열, 36자)
- `secret` = 토큰 발급 시 backend 가 생성한 256-bit (32 bytes) 의 base64 url-safe 인코딩 (~43자)
- 평문 token = `f"{key_id}.{secret}"` — 응답에서 1회 노출, DB 에는 `bcrypt(secret)` 만 저장
- 검증: 헤더 `Authorization: Bearer <key_id>.<secret>` 파싱 → `key_id` 로 lookup → `bcrypt.checkpw(secret, row.secret_hash)`

### 2.2. bcrypt cost = 12 (default), `asyncio.to_thread` wrapping

- bcrypt cost factor 12 (≈250ms per verify) — 표준 보안.
- 동기 `bcrypt.checkpw` 를 async endpoint 에서 `await asyncio.to_thread(bcrypt.checkpw, ...)` 로 호출 — event loop block 회피.
- key_id lookup fail 시 **bcrypt 호출 안 함** (즉시 401) — timing attack 회피 + 비용 절약.
- batch handler (≥10건/2초 flush) 가정 분당 ~30 request → 250ms × 30 = 7.5초 의 CPU per minute (단일 worker). 충분.

### 2.3. Rate limit — token별 `rate_limit_per_minute` (기본 600)

- `LogIngestToken.rate_limit_per_minute` 컬럼 (Phase 1 에 이미 추가됨, default 600).
- Window granularity: 분 단위 (`window_start = received_at.replace(second=0, microsecond=0)`).
- `RateLimitWindow` PRIMARY KEY `(project_id, token_id, window_start)`.
- UPSERT pattern (PostgreSQL `INSERT ... ON CONFLICT DO UPDATE SET event_count = event_count + EXCLUDED.event_count`).
- `event_count + batch_size > rate_limit_per_minute` → 429 + `Retry-After: <seconds_until_next_minute>` (최대 60).
- batch_size 가 limit 을 한 번에 초과해도 그 batch 만 reject — partial accept 안 함 (단순화).

### 2.4. Partial validation — 응답 200 + rejected list

페이로드의 N건 중 일부만 invalid 일 때:

```json
// 200 — 부분 또는 전체 성공
{"accepted": 9, "rejected": [{"index": 3, "reason": "version_sha format invalid"}]}

// 400 — 모두 invalid 또는 payload 자체 깨짐
{"accepted": 0, "rejected": [...]}
```

각 event 에 대해 `validate_event(event_dict, index)` 가 `(LogEvent | None, dict | None)` 반환. valid 만 모아 `insert_events` 호출, invalid 는 rejected list 에 누적.

`insert_events` 자체는 단일 트랜잭션 (한 건이라도 DB 제약 fail 면 전체 rollback) — 이건 partial success 와 다른 layer. validate 가 통과한 events 가 DB INSERT 에 또 fail 하는 건 보통 의외 (CHECK 제약 / FK 등) — 500 으로 처리.

### 2.5. Wire format

요청 헤더:
- `Authorization: Bearer <key_id>.<secret>` (필수)
- `Content-Type: application/json` (필수)
- `Content-Encoding: gzip` (선택 — 본문 gzip 압축 시)
- `X-pslog-Dropped-Since-Last: <int>` (선택 — handler 가 drop 발생 시만)

본문 (spec §5.4 wire format 그대로):

```json
{
  "events": [
    {
      "level": "ERROR",
      "message": "...",
      "logger_name": "app.tasks.sync",
      "version_sha": "abc123...40자",
      "environment": "production",
      "hostname": "app-chak-01",
      "emitted_at": "2026-05-01T03:14:15.926Z",
      "exception_class": "ValueError",
      "exception_message": "...",
      "stack_trace": "...",
      "stack_frames": [{"filename": "...", "lineno": 42, "name": "..."}],
      "user_id_external": "user-123",
      "request_id": "req-abc",
      "extra": {}
    }
  ]
}
```

DB 컬럼명 = wire 키 이름 (LogEvent 모델 1:1). nullable 필드 (exception_*, stack_*, user_id_external, request_id, extra) 생략 OK. `extra` 는 자유 JSON, 4KB 초과 시 reject. `emitted_at` ISO8601 UTC.

### 2.6. version_sha 형식 검증

- `^[0-9a-f]{40}$` (lowercase hex full SHA) 또는 정확히 `"unknown"` (handler 가 APP_VERSION_SHA env var 못 읽었을 때).
- short SHA (예: `"abc1234"`) reject — 일관 검증을 위해.
- 형식 깨짐 → rejected list 에 `{"index": N, "reason": "version_sha format invalid"}`.

### 2.7. 응답 status code 매핑

| Code | When |
|---|---|
| 200 | 정상 ingest (전체 또는 부분 성공). body 에 `{accepted, rejected}` |
| 400 | gzip decode fail / JSON parse fail / events 키 없음 / 모든 event invalid |
| 401 | Authorization 헤더 없음 / 형식 깨짐 / key_id lookup fail / bcrypt fail / revoked_at set |
| 429 | rate limit 초과. response 헤더 `Retry-After: <seconds_until_next_minute>` |
| 500 | DB 쓰기 실패 (재전송 가능 — 멱등성 보장 안 됨, app-chak handler 가 backoff) |

401 메시지는 `"Invalid token"` 단일 — 사유 구분 안 함 (timing attack 회피).

### 2.8. `last_used_at` 갱신

verify_token 성공 시 `token.last_used_at = now()` 설정. 같은 트랜잭션의 ingest_batch 끝에서 `db.commit()` 으로 영속.

### 2.9. `X-pslog-Dropped-Since-Last` 헤더 처리

받으면 `logger.warning("token=%s dropped %d events since last", token.id, dropped)`. 본 phase 는 집계 안 함 (log_health_service 가 후속 phase 에서 추가). 헤더 없으면 무시.

### 2.10. 권한

- `POST /log-tokens`, `DELETE /log-tokens/{id}` → **OWNER 전용** (`can_manage`). 비-OWNER 403, 비-멤버 404.
- `POST /log-ingest` → **토큰 자체가 권한** (project 멤버십 검증 안 함 — 토큰 보유 = 권한). `LogIngestToken.project_id` 로 프로젝트 식별.

### 2.11. 토큰 폐기 (DELETE)

- soft delete: `revoked_at = now()`.
- hard delete 안 함 — past LogEvent 와 RateLimitWindow row 의 FK 보존.
- 이미 revoked 된 token 재 DELETE → 400 (`"Token already revoked"`).
- 다른 project 의 token id 또는 비-존재 → 404.

---

## 3. Backend Architecture

### 3.1. `log_ingest_service.py` 신규

**`parse_token(authorization_header: str | None) -> tuple[UUID, str]`**:
- `None` 또는 `"Bearer "` prefix 없음 → `HTTPException(401)`
- Token 부분에 `.` 1개만 있어야 함 (key_id 와 secret 분리). 형식 깨짐 → 401
- `key_id` UUID parse fail → 401
- 정상 → `(uuid_obj, secret_str)`

**`verify_token(db, key_id: UUID, secret: str) -> LogIngestToken`**:
- `db.get(LogIngestToken, key_id)` — None → 401
- `token.revoked_at is not None` → 401
- `await asyncio.to_thread(bcrypt.checkpw, secret.encode(), token.secret_hash.encode())` → False → 401
- 성공 시 `token.last_used_at = datetime.utcnow()` (in-memory mutation, commit 은 caller)
- Return token

**`check_rate_limit(db, project_id: UUID, token: LogIngestToken, batch_size: int, now: datetime) -> None`**:
- `window_start = now.replace(second=0, microsecond=0)`
- PostgreSQL UPSERT:
  ```sql
  INSERT INTO rate_limit_windows (project_id, token_id, window_start, event_count)
  VALUES (?, ?, ?, ?)
  ON CONFLICT (project_id, token_id, window_start)
  DO UPDATE SET event_count = rate_limit_windows.event_count + EXCLUDED.event_count
  RETURNING event_count
  ```
- 반환된 `event_count > token.rate_limit_per_minute` → `HTTPException(429, "Rate limit exceeded", headers={"Retry-After": str(seconds_until_next_minute)})`
- batch_size 단일 수가 limit 초과해도 same handling — 그 batch 자체 reject (다음 분에 재전송).

**`validate_event(event_dict: dict, index: int) -> tuple[LogEvent | None, dict | None]`**:
- Pydantic schema (`LogEventInput`, `extra='forbid'`) validate
- `version_sha` 정규식 (`^[0-9a-f]{40}$|^unknown$`) 검증
- `extra` size > 4KB → reject
- valid → `(LogEvent(...constructed...), None)`
- invalid → `(None, {"index": index, "reason": "<reason>"})`

**`insert_events(db, events: list[LogEvent]) -> int`**:
- `db.add_all(events); await db.flush()` — fingerprint=NULL 자동 (모델 default).
- 단일 트랜잭션. 한 건 fail 면 전체 rollback (caller 가 500 처리).

**`ingest_batch(db, *, key_id, secret, payload_dict, dropped_since_last, project_id_dest=None) -> tuple[int, list[dict]]`**:
- end-to-end 조립. 각 단계 fail 시 적절한 예외 raise.
- `payload_dict.get("events")` 가 list 가 아니면 raise 400 류 예외 (caller 가 endpoint 에서 catch).
- 각 event validate (partial). accepted 만 모아 `insert_events`.
- token.project_id 사용 (외부 입력 무시 — security).
- last_used_at 갱신 (verify_token 안에서 set, 여기서 commit).
- `X-pslog-Dropped-Since-Last` 가 있으면 `logger.warning`.

### 3.2. Endpoint: `POST /log-ingest`

`backend/app/api/v1/endpoints/log_ingest.py` (신규):

```python
@router.post("/log-ingest")
async def ingest_logs(
    request: Request,
    authorization: str | None = Header(default=None),
    content_encoding: str | None = Header(default=None),
    x_pslog_dropped_since_last: int | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
):
    """외부 앱(app-chak)이 로그 batch 를 push.

    설계서: 2026-05-01-error-log-phase2-ingest-design.md §2.5, §3.2
    """
    body = await request.body()

    if content_encoding == "gzip":
        try:
            body = gzip.decompress(body)
        except Exception:
            raise HTTPException(status_code=400, detail="gzip decode failed")

    try:
        payload = json.loads(body)
    except Exception:
        raise HTTPException(status_code=400, detail="invalid JSON body")

    try:
        key_id, secret = await log_ingest_service.parse_token(authorization)
        token = await log_ingest_service.verify_token(db, key_id, secret)
        # ingest_batch 가 rate limit + validate + insert + commit 처리
        accepted, rejected = await log_ingest_service.ingest_batch(
            db, token=token, payload_dict=payload,
            dropped_since_last=x_pslog_dropped_since_last,
        )
    except HTTPException:
        raise
    except Exception:
        logger.exception("log-ingest unexpected error")
        raise HTTPException(status_code=500, detail="Internal error")

    if accepted == 0 and rejected:
        return JSONResponse(
            status_code=400,
            content={"accepted": 0, "rejected": rejected},
        )
    return {"accepted": accepted, "rejected": rejected}
```

### 3.3. Endpoint: `POST /log-tokens` (OWNER)

`backend/app/api/v1/endpoints/log_tokens.py` (신규):

```python
@router.post(
    "/{project_id}/log-tokens",
    response_model=LogTokenResponse,
    status_code=201,
)
async def create_log_token(
    project_id: UUID,
    data: LogTokenCreate,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
):
    """토큰 발급 — 응답에 평문 token 1회만.
    설계서: §3.3
    """
    # project + OWNER 검증
    project = await project_service.get_project(db, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    role = await get_effective_role(db, user.id, project_id)
    if role is None:
        raise HTTPException(status_code=404, detail="Project not found")
    if not can_manage(role):
        raise HTTPException(status_code=403, detail="Owner only")

    secret = secrets.token_urlsafe(32)  # 256-bit
    secret_hash = bcrypt.hashpw(secret.encode(), bcrypt.gensalt(rounds=12)).decode()
    token = LogIngestToken(
        project_id=project_id,
        name=data.name,
        secret_hash=secret_hash,
        rate_limit_per_minute=data.rate_limit_per_minute or 600,
    )
    db.add(token)
    await db.commit()
    await db.refresh(token)

    plain_token = f"{token.id}.{secret}"
    return LogTokenResponse(
        id=token.id,
        name=token.name,
        token=plain_token,  # 평문, 1회만
        rate_limit_per_minute=token.rate_limit_per_minute,
        created_at=token.created_at,
    )
```

### 3.4. Endpoint: `DELETE /log-tokens/{token_id}` (OWNER)

```python
@router.delete(
    "/{project_id}/log-tokens/{token_id}",
    response_model=LogTokenRevokedResponse,
)
async def revoke_log_token(
    project_id: UUID,
    token_id: UUID,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
):
    """토큰 폐기 — soft delete (revoked_at = now)."""
    # project + OWNER 검증 (위와 동일 패턴)
    ...

    token = await db.get(LogIngestToken, token_id)
    if token is None or token.project_id != project_id:
        raise HTTPException(status_code=404, detail="Token not found")
    if token.revoked_at is not None:
        raise HTTPException(status_code=400, detail="Token already revoked")

    token.revoked_at = datetime.utcnow()
    await db.commit()
    await db.refresh(token)

    return LogTokenRevokedResponse(id=token.id, revoked_at=token.revoked_at)
```

### 3.5. Pydantic schemas

`backend/app/schemas/log_ingest.py`:

```python
class StackFrame(BaseModel):
    model_config = ConfigDict(extra="forbid")
    filename: str
    lineno: int
    name: str

class LogEventInput(BaseModel):
    """Wire format event — DB 컬럼명 = wire key 이름."""
    model_config = ConfigDict(extra="forbid")
    level: str  # 'DEBUG'/'INFO'/'WARNING'/'ERROR'/'CRITICAL' — service 가 LogLevel enum 으로 변환 (대소문자 정규화)
    message: str
    logger_name: str
    version_sha: str
    environment: str
    hostname: str
    emitted_at: datetime
    exception_class: str | None = None
    exception_message: str | None = None
    stack_trace: str | None = None
    stack_frames: list[StackFrame] | None = None
    user_id_external: str | None = None
    request_id: str | None = None
    extra: dict | None = None

class IngestPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    events: list[dict]  # raw dicts — service 가 per-event validate (partial success)

class RejectedEvent(BaseModel):
    index: int
    reason: str

class IngestResponse(BaseModel):
    accepted: int
    rejected: list[RejectedEvent]
```

`backend/app/schemas/log_token.py`:

```python
class LogTokenCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    rate_limit_per_minute: int | None = Field(default=None, ge=1, le=10000)

class LogTokenResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str
    token: str  # 평문 — 응답 1회만, DB 에는 secret_hash 만 저장
    rate_limit_per_minute: int
    created_at: datetime

class LogTokenRevokedResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    revoked_at: datetime
```

---

## 4. Test Plan

### 4.1. `test_log_ingest_service.py` (신규, 8건)

1. `parse_token` — 헤더 None → 401 / "Bearer xxx" (`.` 없음) → 401 / "Bearer abc.def" (key_id UUID parse fail) → 401 / 정상 → `(uuid, secret)`
2. `verify_token` — key_id lookup fail → 401 / revoked_at set → 401 / bcrypt fail → 401 / 정상 → token + last_used_at 갱신
3. `check_rate_limit` — 첫 호출 → INSERT row, event_count == batch_size / 같은 분 재호출 → UPDATE, event_count 누적 / limit 초과 → 429 + Retry-After
4. `check_rate_limit` cross-window — 다음 분 호출 → 새 row INSERT (event_count = batch_size)
5. `validate_event` — version_sha format invalid (short SHA) → reject / "unknown" 정상 / Pydantic schema 위배 (extra 필드) → reject / extra > 4KB → reject
6. `insert_events` — batch 5건 INSERT 성공, fingerprint NULL 보존
7. `ingest_batch` partial — 10 events 중 8 valid 2 invalid → accepted=8, rejected=[2건], DB 에 8 행 INSERT, last_used_at 갱신
8. `ingest_batch` X-pslog-Dropped-Since-Last 헤더 — logger.warning 호출 (caplog 검증)

### 4.2. `test_log_ingest_endpoint.py` (신규, 8건)

1. 정상 ingest — 200 + accepted/rejected, DB 에 LogEvent 행 존재
2. 모든 event invalid — 400 + rejected list (events 모두 version_sha invalid)
3. gzip body decode 정상 — 200
4. gzip decode fail (잘못된 byte) — 400
5. Authorization 헤더 없음 → 401 / "Bearer abc" (분리 fail) → 401 / 잘못된 secret (bcrypt fail) → 401 / revoked token → 401
6. rate limit 초과 → 429 + Retry-After 헤더 존재
7. payload JSON parse fail → 400 / events 키 없음 → 400
8. DB 쓰기 실패 (mock insert_events raise) → 500

### 4.3. `test_log_tokens_endpoint.py` (신규, 4건)

1. POST /log-tokens (OWNER) → 201 + 평문 token (`{key_id}.{secret}` 형식) + DB bcrypt hash 검증 (응답 secret 으로 bcrypt verify pass)
2. POST /log-tokens (EDITOR) → 403
3. DELETE /log-tokens/{id} (OWNER) → 200 + revoked_at set / DB 의 row 도 revoked_at NOT NULL
4. DELETE 이미 revoked 된 token → 400 ("Token already revoked")

### 4.4. e2e (사용자, PR 머지 전)

- pslog dev server + curl POST /log-tokens 로 토큰 발급 → 평문 받음
- app-chak 의 `.env` 에 `pslog_LOG_INGEST_TOKEN={token}` + `pslog_LOG_ENDPOINT=http://localhost:8081/api/v1/log-ingest` 설정
- app-chak 재시작 → handler 자동 활성
- app-chak 에서 의도적 `logger.error("test")` → pslog DB 의 log_events 테이블에 INSERT 확인
- gzip 압축 batch 와 plain JSON batch 둘 다 동작 검증

---

## 5. Decision Log

- **Phase split (2a / 2b)**: 2a = 토큰 API + ingest endpoint + rate limit (본 phase). 2b = log_fingerprint_reaper + log_health_service (Phase 3 또는 후속). 핵심 가치 (e2e 동작) 가 2a 만으로 전달 — 작은 PR 가능.
- **Partial validation (옵션 A)**: 200 + accepted/rejected list. spec §6.1 "나머지는 정상 처리" 직접 매칭. 모두 invalid 시 400.
- **Token별 rate_limit_per_minute (옵션 A)**: 환경 (dev/prod) 별 다른 limit. 컬럼 Phase 1 에 이미 추가됨. 기본 600/min.
- **bcrypt cost 12 + asyncio.to_thread (옵션 A)**: 표준 보안. 단일 worker 분당 ~30 request 가정 OK.
- **OWNER 전용 + backend only (옵션 A)**: 보수적 권한 + UI 미포함 (Phase 5 통합 시 LogTokensPage 추가).
- **`secrets.token_urlsafe(32)` for secret 생성**: 256-bit (32 bytes) base64 url-safe. ~43자.
- **bcrypt cost 11 vs 12**: 12 default. 만약 throughput 부족 호소 시 in-process 캐시 (key_id → verified) 도입 (TTL 60s, revoke 시 invalidation 추가). 본 phase 미도입 (YAGNI).
- **Soft delete (revoked_at)** vs hard delete: past LogEvent / RateLimitWindow FK 보존 위해 soft. revoked 토큰 verify_token 가 401 반환.
- **token.project_id 사용** (외부 input 무시): security — 클라이언트가 다른 project 의 LogEvent INSERT 못 하게 token 의 project_id 강제.
- **timing attack 회피**: 401 메시지 모두 `"Invalid token"` 단일. key_id lookup fail 분기에서 bcrypt 호출 안 해 응답 시간 차이 (~250ms) 노출되지만, dummy bcrypt 호출은 비용 / 복잡도 비효율 — v1 는 명확히 fast-path.

---

## 6. Phase 3 와의 관계 (참고)

본 phase 는 `LogEvent.fingerprint = NULL` 로만 INSERT. Phase 3 에서:
- `fingerprint_service.compute(event)` 신규 — 결정적 fingerprint
- `error_group_service.upsert(...)` — 신규/spike/regression 감지
- BackgroundTask 가 ingest 응답 후 fingerprint 처리 (현재는 ingest endpoint 가 BackgroundTask 안 띄움 — Phase 3 가 endpoint 수정해서 추가)
- `log_fingerprint_reaper` — 부팅 시 `fingerprinted_at IS NULL` 회수

본 phase 의 ingest endpoint 는 BackgroundTask 호출 안 함 (Phase 3 가 추가) — 깔끔한 단계 도입.

---

## 7. Open Questions

본 phase 진입 전 답할 것 없음. 시각 검증 / 사용자 피드백 후 결정할 항목 1건:

1. **last_used_at commit 시점 — same tx vs separate**: 현재 설계는 ingest_batch 의 commit 에 묶어 영속. concurrent ingest 가 같은 토큰으로 burst 시 last_used_at 가 race 로 한 번만 갱신될 수 있음 (큰 문제 없음 — 대략적 시각). 만약 정확성 호소 시 별도 background commit 으로 분리. 본 phase 는 묶음 (단순).
