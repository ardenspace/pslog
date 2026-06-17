# 에러 로그 + Git 상관관계 설계서 (pslog)

작성일: 2026-04-26
선행: `2026-04-26-ai-task-automation-design.md` (Phase 4 이상 머지 완료 전제)

---

## 변경 이력

- **v1 (2026-04-26)**: 초안
- **v2 (2026-04-26)**: `/plan-eng-review` 1차 반영
  - Phase 2 fingerprint reaper + eventual consistency 명시
  - `version_sha` 형식 검증 + `unknown` 비율 경고
  - 토큰 포맷 `<key_id>.<secret>` (bcrypt hot path 회피)
  - rate limit = PostgreSQL 카운터 (다중 워커 정확성), spike 감지는 워커별 메모리(false negative 허용)
  - PostgreSQL 일별 파티션 + `DROP PARTITION` GC
  - ErrorGroup status 전이 ASCII 다이어그램
  - spike 알림 cooldown (`last_alerted_at` + 30분)
  - 핸들러 pslog 다운 시 정책 (큐 한도, backoff, atexit, drop 메트릭)
  - 풀텍스트 검색을 §12에서 Phase 5로 이동 (`pg_trgm` 인덱스)
  - 핸들러 배포 방식 = app-chak 레포 직접 복사 (§14 결정)
- **v3 (2026-04-27)**: 설계 검토 후 2개 결정 확정
  - `version_sha` = **40자 full SHA 강제** (또는 `unknown`). short SHA mismatch로 join 깨지는 문제 + 7자 충돌 위험 회피
  - **fingerprint 정규화 규칙 6개 명시** — 절대경로→상대경로, line 제거, 메모리 주소 마스킹, framework frame 스킵 등

---

## 1. 배경

pslog에 외부 프로젝트(예: `app-chak`)의 로그를 수집·조회하고, **에러를 git 작업 컨텍스트와 자동 연결**하는 기능을 추가한다. 시장의 일반 로그 도구(Sentry, Logtail, Datadog)와의 차별점:

- 본 시스템은 pslog가 이미 보유한 **Handoff / Task / TaskEvent**와 로그를 동일 SHA로 join한다.
- 결과: *"이 NullPointerException은 commit `abc1234` (alice의 task-007: 로그인 폼 검증)에서 처음 발생"* 같은 인사이트를 자동으로 만들어낸다.

**핵심 원칙:**
1. **AI는 합성·요약에만 사용.** 에러 그룹화(fingerprint), 빈도 집계, 알림 라우팅은 결정적 코드로 처리한다.
2. **단일 진실 — `version_sha`.** 로그 한 줄마다 배포 커밋 SHA가 박혀 들어와야 pslog가 git 컨텍스트와 join할 수 있다. **이 값의 신뢰성이 본 시스템 가치의 90%를 결정한다.**
3. **외부 의존 실패는 기능을 막지 않는다.** Discord/Gemma 다운 시에도 로그 수집/조회는 계속 작동한다.

---

## 2. 사용자 시나리오

### 2.1 일상 — 에러 빈도 추이 + 메시지 검색
1. 팀원이 pslog `Logs` 페이지에서 프로젝트 선택
2. 기본 뷰: 최근 24시간 에러를 fingerprint로 그룹화한 목록 (빈도/마지막 발생 시각/관련 브랜치)
3. 새 에러(처음 본 fingerprint)는 상단에 ⚡ 배지
4. 검색창에 키워드 입력 → 메시지/스택 부분 일치 결과 즉시 (`pg_trgm` 인덱스)

### 2.2 사고 대응 — 어떤 작업이 에러를 만들었나
1. Discord에 "🚨 새 에러: `KeyError: 'preference'`, 5분 내 12회" 알림 도착
2. 팀원이 알림 링크 → pslog `ErrorDetail` 화면
3. 화면에 같이 표시되는 정보:
   - 첫 발생: `2026-04-26 14:32`, commit `abc1234`
   - 해당 commit: alice가 push한 `feature/preference-update` 브랜치, task-007 작업 중
   - 직전 정상 commit: `def5678` (어제 17:00)
   - 해당 차이의 변경 파일: `backend/app/routers/preference.py`, `backend/app/models/user_preference.py`
4. 팀원이 1분 안에 원인 후보 좁힘

### 2.3 회고 — Gemma 요약 (선택)
1. 주간 회고 시 pslog가 "이번 주 신규 에러 N건, 가장 영향 큰 5건" Gemma 브리핑 생성

---

## 3. 아키텍처 결정

| 결정 | 선택 | 이유 |
|---|---|---|
| 로그 수집 방식 | **app-chak → pslog HTTP push (Python logging handler)** | 인프라 추가 0, 표준 logging 위에 얹음, 클라우드/로컬 어디서 돌아도 동일 |
| Handler 구현 | **`logging.Handler` 서브클래스 + 배치 큐** | 표준 라이브러리 위에 얇게 얹기, Sentry SDK 의존 X |
| Handler 배포 | **app-chak 레포에 단일 .py 모듈 복사** | 솔로/소규모 팀 단순함 우선, PyPI/submodule 인프라 비용 회피 |
| 전송 단위 | **배치 (≥10건 또는 ≥2초)** | 트래픽 절감 + 실시간성 균형 |
| 인증 | **`<key_id>.<secret>` 포맷, key_id로 O(1) lookup, secret bcrypt 검증** | bcrypt hot path 회피, 무효 토큰 즉시 401 |
| `version_sha` 주입 방식 | **앱 부팅 시 환경변수 (`APP_VERSION_SHA`), 40자 full SHA 강제** | Docker build arg = `git rev-parse HEAD` (short 금지 → join mismatch + 7자 충돌 회피) |
| `version_sha` 신뢰성 | **ingest 시 `^[0-9a-f]{40}$` 또는 `unknown`만 허용 + `unknown` 비율 모니터링** | 환경변수 누락 = join 가치 0이므로 가시화 필수 |
| 에러 그룹화 | **결정적 fingerprint** (예외 클래스 + 정규화된 앱코드 stack frame top 5 SHA1, 정규화 규칙 §4.1) | AI 의존 없음, 일관성, dev/prod 경로 차이 흡수 |
| Git 컨텍스트 join | **`LogEvent.version_sha` ↔ `Handoff.commit_sha` / `Task.last_commit_sha`** | 본 설계의 핵심 가치, 본 데이터로 즉시 가능 |
| 보존 / GC | **PostgreSQL 일별 range partition + `DROP PARTITION`** | DELETE GC로 인한 vacuum bloat 회피 |
| Rate limit | **PostgreSQL 카운터 테이블 (트랜잭션 정확)** | 다중 워커에서도 정확, Redis 의존 X |
| Spike 감지 | **워커별 메모리 카운터 (false negative 허용)** | 정확도가 사고 본질 X, 인프라 회피 |
| 알림 | **기존 `discord_service` 확장 + 신규 / spike / regression 별 cooldown** | 알림 폭격 방지 |
| 실시간성 | **폴링 (UI 5초 간격)** | WebSocket 인프라 추가 회피 |
| AI 사용 위치 | **요약/회고만 (Gemma 4)** | fingerprint·집계는 결정적 |

---

## 4. 데이터 모델

### 4.1 신규 모델

**`LogEvent`** — 수신한 로그 한 줄
```python
id: UUID
project_id: UUID                      # FK
level: LogLevel                       # DEBUG | INFO | WARNING | ERROR | CRITICAL
message: text                         # 본문
logger_name: str                      # 예: "app.routers.preference"
version_sha: str                      # 앱 부팅 시 주입된 git SHA (40자 hex full, 또는 "unknown")
environment: str                      # "production" | "staging" | "dev"
hostname: str                         # 발생 서버
emitted_at: datetime                  # 앱이 로그 찍은 시각 (앱 시계)
received_at: datetime                 # pslog가 받은 시각 (pslog 시계, 시계 어긋남 감지용)

# 에러 전용 (level >= ERROR일 때만 채워짐)
exception_class: str | None           # "KeyError"
exception_message: text | None        # "'preference'"
stack_trace: text | None              # 풀 스택 (raw)
stack_frames: JSON | None             # 정규화된 [{"file":"...","line":123,"func":"..."}] — 원본 보존(라인 포함)
fingerprint: str | None               # SHA1(정규화 규칙 적용 후, 아래 §fingerprint 정규화 규칙 참조)
fingerprinted_at: datetime | None     # fingerprint 계산 + ErrorGroup UPSERT 완료 시각

# 선택
user_id_external: str | None          # app-chak의 사용자 ID (PII 마스킹 후)
request_id: str | None                # 분산 트레이싱 ID
extra: JSON                           # 자유 영역 (앱이 추가 컨텍스트 첨부)
```

**파티셔닝 (PostgreSQL declarative partitioning)**
```sql
CREATE TABLE log_events (...)
  PARTITION BY RANGE (received_at);

-- 일별 파티션, pg_partman으로 자동 생성/회수
-- 보존: ERROR↑ 90일, 그 외 30일 (level별 별도 파티션 그룹은 복잡하므로
-- 단일 파티션 + level 인덱스로 처리, GC는 일별 DROP)
```

**인덱스 (성능 핵심)**
```sql
-- 각 일별 파티션에 자동 적용
CREATE INDEX idx_log_project_level_received ON log_events (project_id, level, received_at DESC);
CREATE INDEX idx_log_fingerprint ON log_events (project_id, fingerprint) WHERE fingerprint IS NOT NULL;
CREATE INDEX idx_log_version_sha ON log_events (project_id, version_sha);  -- git join key
CREATE INDEX idx_log_unfingerprinted ON log_events (project_id, id)
  WHERE level IN ('ERROR','CRITICAL') AND fingerprinted_at IS NULL;        -- reaper용

-- 풀텍스트 (에러 위주만 인덱싱, 디스크 절감)
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE INDEX idx_log_message_trgm ON log_events
  USING gin (message gin_trgm_ops)
  WHERE level >= 'WARNING';
```

**fingerprint 정규화 규칙** (Phase 3 acceptance criteria)

같은 버그를 다른 fingerprint로 분리하지 않으면서, 다른 버그를 같은 fingerprint로 합치지 않는 균형점:

1. **절대경로 → 프로젝트 루트 기준 상대경로**
   - `/Users/alice/app-chak/backend/routers/x.py` → `backend/routers/x.py`
   - `/app/backend/routers/x.py` (컨테이너) → `backend/routers/x.py`
   - 환경변수 `APP_PROJECT_ROOT` 또는 휴리스틱(첫 `backend/`/`src/` segment 기준)으로 추출
2. **line number 제거** — 한 줄 수정/리팩토링이 새 그룹 만드는 false split 회피
3. **메모리 주소 마스킹** — `<object at 0x7f8a...>` → `<object at 0xADDR>` (정규식 `0x[0-9a-f]+`)
4. **함수명은 유지** — lambda/closure 익명 이름(`<lambda>`, `<listcomp>`, `<genexpr>`)도 안정적이라 포함
5. **framework / stdlib frame은 top 5 산정에서 스킵**
   - 스킵 패턴: `site-packages/`, `dist-packages/`, `lib/python\d`, `asyncio/`, `uvicorn/`, `_bootstrap.py`
   - 앱 코드 frame만 카운트해서 top 5 추출 (앱 frame이 5개 미만이면 있는 만큼만)
6. **fingerprint 입력 문자열 포맷**
   ```
   <exception_class>|<rel_path1>:<func1>\n<rel_path2>:<func2>\n...
   ```
   `SHA1(위 문자열)` → 40자 hex.
   stack_frames가 비어있으면 `SHA1(exception_class + "|" + message 첫 줄)` fallback.

> stack_frames JSON은 원본 그대로(line 포함) 보존. fingerprint 계산은 정규화 사본으로만 수행.

**`ErrorGroup`** — fingerprint별 집계 (롤업 캐시)
```python
id: UUID
project_id: UUID
fingerprint: str                      # UNIQUE (project_id, fingerprint)
exception_class: str
exception_message_sample: text        # 가장 최근 메시지 1건
first_seen_at: datetime
first_seen_version_sha: str           # 처음 발생한 배포 — git 컨텍스트 join용 핵심
last_seen_at: datetime
last_seen_version_sha: str
event_count: bigint                   # 누적 발생 횟수 (BIGINT — 인기 fingerprint 안전)
status: ErrorGroupStatus              # OPEN | RESOLVED | IGNORED | REGRESSED
resolved_at: datetime | None
resolved_by_user_id: UUID | None
resolved_in_version_sha: str | None   # "이 커밋에서 해결됨" 마킹

# 알림 멱등성
last_alerted_new_at: datetime | None        # 신규 알림 시각 (group당 1회만)
last_alerted_spike_at: datetime | None      # spike 알림 — 30분 cooldown
last_alerted_regression_at: datetime | None # regression 알림 — group당 1회 per regression cycle
```

**`status` 전이 다이어그램**

```
        new event           sample
        (first time)        cleared
            │                  ▲
            ▼                  │
       ┌─────────┐  resolve  ┌──────────┐
       │  OPEN   │─────────► │ RESOLVED │
       │         │◄───────── │          │
       └─────────┘  reopen   └──────────┘
          │                       │
          │ ignore /              │ event w/
          │ unmute                │ newer SHA
          ▼                       ▼
       ┌─────────┐            ┌──────────────┐
       │ IGNORED │            │  REGRESSED   │
       └─────────┘            └──────────────┘
                                     │   ▲
                                     │   │ event w/
                          resolve    │   │ newer SHA
                                     ▼   │
                               ┌──────────┐
                               │ RESOLVED │
                               └──────────┘
```

전이 규칙:
- 모든 전이는 `TaskEvent`처럼 `ErrorGroupEvent` 테이블에 기록 (감사 추적). *추후 추가, 본 설계 v1에서는 status 컬럼만.*
- IGNORED → 알림 발송 안 함, 새 이벤트 받기는 함 (event_count 갱신)
- REGRESSED → status=OPEN과 거의 같지만 UI에서 강조 + Discord 강조 알림

**`LogIngestToken`** — 프로젝트별 토큰
```python
id: UUID                              # = key_id, 토큰 평문의 prefix로 사용
project_id: UUID
name: str                             # "app-chak production", "app-chak staging"
secret_hash: str                      # bcrypt(secret_part)
created_at: datetime
last_used_at: datetime | None
revoked_at: datetime | None
rate_limit_per_minute: int = 600      # 토큰별 분당 이벤트 한도
```

**토큰 평문 포맷**: `<key_id>.<secret>` (예: `f3c1a2b4-....abcd-ef.s3cr3tRandomBase64String`).
- 발급 시 1회만 평문 응답, 이후 DB는 `secret_hash`만 보유.
- ingest 시 `key_id`로 O(1) lookup → 무효 시 즉시 401 (bcrypt 호출 X).
- 유효 lookup 후 `bcrypt.checkpw(secret, secret_hash)` (timing-safe).

**`RateLimitWindow`** — 분당 카운터
```python
project_id: UUID
token_id: UUID
window_start: datetime                # 분 단위 truncate
event_count: int

PRIMARY KEY (project_id, token_id, window_start)
```
ingest마다 `INSERT ... ON CONFLICT (project_id, token_id, window_start) DO UPDATE SET event_count = event_count + ?`. 다중 워커에서도 정확. 24시간 지난 row는 GC.

### 4.2 기존 모델과의 join

설계의 핵심 — **이 join이 본 시스템 가치의 90%다.**

```
LogEvent.version_sha
    │
    ├──► Handoff.commit_sha       (이 커밋이 어떤 push에 속하는지)
    │       └──► Handoff.author_user_id, branch, free_notes
    │
    ├──► Task.last_commit_sha     (이 커밋이 어떤 task의 진행을 마킹했는지)
    │       └──► Task.title, assignee, external_id
    │
    └──► GitPushEvent.head_commit_sha (이 push의 전체 commits 배열)
            └──► commits[*].message, commits[*].modified
```

pslog API `GET /api/v1/projects/{id}/errors/{group_id}` 응답에 위 join 결과를 immediately 포함.

`version_sha == "unknown"` 또는 join 결과 0인 LogEvent는 "git 동기화 데이터 없음" 마킹 — UI에서 별도 표시.

`Task.archived_at IS NOT NULL`인 row도 join에 **포함** — 과거 archived task에서 발생한 에러의 git 컨텍스트 보존. UI GitContextPanel에 `(archived)` 배지로 구분 표시.

---

## 5. 컴포넌트 / 서비스

### 5.1 신규 백엔드 서비스

```
backend/app/services/
  log_ingest_service.py         ① 토큰 검증 + 배치 INSERT + rate limit
  fingerprint_service.py        ② 예외 → 결정적 fingerprint 계산
  error_group_service.py        ③ ErrorGroup 롤업 (UPSERT) + status 전이
  log_query_service.py          ④ 조회 (필터/페이지네이션) + git context join + 풀텍스트
  log_alert_service.py          ⑤ 신규 / spike / regression 감지 + cooldown → Discord
  log_gc_service.py             ⑥ 일별 파티션 DROP + RateLimitWindow GC
  log_summary_service.py        ⑦ Gemma 4 주간 회고 (Phase 후반)
  log_fingerprint_reaper.py     ⑧ 부팅 시 미처리 LogEvent (`fingerprinted_at IS NULL`) 회수
  log_health_service.py         ⑨ unknown SHA 비율 모니터링 → 경고
```

각 서비스의 책임:

| # | 입력 | 출력 | 외부 의존 |
|---|---|---|---|
| ① | POST 배치 페이로드 | LogEvent 다중 INSERT | 없음 |
| ② | 예외 클래스 + stack_frames | fingerprint (str) | 없음 |
| ③ | LogEvent (ERROR↑) | ErrorGroup UPSERT, status 전이 | ② |
| ④ | (project, 필터) | LogEvent 페이지 + git 컨텍스트 | sync_service의 데이터 |
| ⑤ | ErrorGroup 변경 | Discord 메시지 (cooldown 적용) | discord_service |
| ⑥ | cron trigger | DROP PARTITION + RateLimitWindow DELETE | 없음 |
| ⑦ | (project, 기간) | 자연어 회고 | ollama_client |
| ⑧ | 부팅 trigger | 미처리 LogEvent 재처리 | ②③⑤ |
| ⑨ | hourly cron | unknown 비율 / 시계 어긋남 검사 | discord_service |

### 5.2 신규 API 엔드포인트

```
POST   /api/v1/log-ingest                          # 외부 앱(app-chak)이 호출 — 배치 push
GET    /api/v1/projects/{id}/logs                  # 로그 조회 (level/시간/브랜치/메시지 검색)
GET    /api/v1/projects/{id}/errors                # ErrorGroup 목록
GET    /api/v1/projects/{id}/errors/{group_id}     # ErrorGroup 상세 + git 컨텍스트
PATCH  /api/v1/projects/{id}/errors/{group_id}     # status 변경 (resolve/ignore/reopen)
POST   /api/v1/projects/{id}/log-tokens            # 토큰 발급 (응답에 평문 1회만)
DELETE /api/v1/projects/{id}/log-tokens/{token_id} # 토큰 폐기
GET    /api/v1/projects/{id}/log-summary           # Gemma 주간 회고 (Phase 후반)
GET    /api/v1/projects/{id}/log-health            # unknown SHA 비율, 시계 어긋남, 24h 송신량
```

### 5.3 신규 프론트엔드

```
frontend/src/
  pages/
    LogsPage.tsx                    # 실시간 로그 스트림 + 필터 + 메시지 검색창
    ErrorsPage.tsx                  # ErrorGroup 목록 (기본 뷰)
    ErrorDetailPage.tsx             # group 상세 + git 컨텍스트 패널
    LogTokensPage.tsx               # 토큰 관리
    LogHealthBadge.tsx              # 헤더 — unknown SHA 비율 ⚠️ 표시
  components/
    LogLevelBadge.tsx
    GitContextPanel.tsx             # commit/branch/handoff/task 카드
    StackTraceViewer.tsx
    ErrorTrendChart.tsx
    LogSearchBox.tsx                # pg_trgm ILIKE 검색
  hooks/
    useLogs.ts
    useErrorGroups.ts
    useLogHealth.ts
  services/
    logsApi.ts
```

### 5.4 app-chak 측 라이브러리 (Phase 0 산출물)

배포: **app-chak 레포에 직접 복사** (단일 모듈로 시작, 필요 시 패키지화).

위치: `app-chak/backend/app/utils/pslog_log_handler.py`

```python
# logging.Handler 서브클래스, 표준 logging 위에 얹음
# - 배치 큐 (≥10건 또는 ≥2초 flush)
# - HTTP POST + gzip
# - PIIFilter (별도 logging.Filter)
```

**핸들러 동작 정책 (pslog 다운/오프라인 시):**
- in-memory 큐 한도: **1000 events 또는 5MB**, 초과 시 가장 오래된 것부터 drop + `dropped_count` 카운터 증가
- HTTP 5xx/timeout: exponential backoff (1s → 5s → 30s → 5min, 이후 5min 유지)
- atexit flush 타임아웃: **5초** (앱 종료를 막지 않음)
- 디스크 임시 저장: **default off** (디스크 권한/용량 문제 회피, 옵션으로 켤 수 있게 인터페이스만 제공)
- drop 발생 시 다음 성공 송신 페이로드의 헤더 `X-pslog-Dropped-Since-Last: <count>`에 누적 — pslog `log_health_service`가 집계 후 UI 표시

**Wire format (HTTP POST 페이로드):**

요청 헤더:
- `Authorization: Bearer <key_id>.<secret>`
- `Content-Type: application/json`
- `Content-Encoding: gzip`
- `X-pslog-Dropped-Since-Last: <int>` (optional, drop 발생 시만)

본문:

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
      "emitted_at": "2026-04-27T03:14:15.926Z",
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

필드 세만틱은 §4.1 `LogEvent` 모델과 1:1 (DB 컬럼명 = wire 키 이름). nullable 필드는 생략 가능. `emitted_at`은 ISO8601 UTC. `extra`는 자유 JSON, 최대 4KB (pslog 측 검증).

`logging.config.dictConfig`에 등록만 하면 자동 동작:

```python
LOGGING = {
    "filters": {"pii": {"()": "app.utils.pslog_log_handler.PIIFilter"}},
    "handlers": {
        "pslog": {
            "class": "app.utils.pslog_log_handler.pslogHandler",
            "endpoint": os.environ["pslog_LOG_ENDPOINT"],
            "token": os.environ["pslog_LOG_INGEST_TOKEN"],   # "<key_id>.<secret>"
            "version_sha": os.environ.get("APP_VERSION_SHA", "unknown"),
            "environment": os.environ.get("APP_ENV", "production"),
            "level": "INFO",
            "filters": ["pii"],
        },
    },
    "root": {"handlers": ["pslog", "console"], "level": "INFO"},
}
```

React Native(프론트) 측은 별도 — Phase 8 이후.

---

## 6. 데이터 흐름

### 6.1 로그 수집 흐름

```
[app-chak] logger.error(...)
   ↓
[pslog_log_handler] PIIFilter → 배치 큐
   │  ├── 큐 크기 ≥ 10 또는
   │  ├── 마지막 flush 후 ≥ 2초 또는
   │  └── 프로세스 종료 (atexit, 5초 타임아웃)
   ↓
[HTTP POST] /api/v1/log-ingest (Bearer <key_id>.<secret>, gzip JSON)
   ↓
[pslog] log_ingest_service
   ├── 토큰 파싱: key_id . secret 분리
   ├── key_id로 LogIngestToken lookup → 없으면 즉시 401 (bcrypt 호출 X)
   ├── bcrypt.checkpw(secret, row.secret_hash) → 실패 시 401
   ├── version_sha 형식 검증 (`^[0-9a-f]{40}$` 또는 정확히 "unknown" — short SHA reject)
   │   └── 형식 깨짐 → 400 + 어떤 이벤트가 잘못됐는지 응답, 나머지는 정상 처리
   ├── RateLimitWindow UPSERT (project_id, token_id, 분 truncate)
   │   └── event_count > rate_limit_per_minute → 429 + Retry-After
   ├── 배치 INSERT (LogEvent N건, 단일 트랜잭션, fingerprint=NULL)
   └── 200 OK
   ↓
[pslog BackgroundTask] (level >= ERROR인 이벤트만)
   ├── fingerprint_service.compute(event)
   ├── error_group_service.upsert(fingerprint, event)
   │   ├── 신규 fingerprint → ErrorGroup INSERT, status=OPEN
   │   ├── 기존 RESOLVED → status=REGRESSED
   │   └── 기존 OPEN/REGRESSED → event_count++, last_seen_* 갱신
   ├── LogEvent.fingerprinted_at = now()
   └── log_alert_service.maybe_notify(group, event)

[부팅 시] log_fingerprint_reaper
   └── idx_log_unfingerprinted 스캔 → 미처리 ERROR↑ 이벤트 재처리
       └── 컨테이너 재시작/크래시로 누락된 이벤트 회수

[hourly cron] log_health_service
   └── 최근 1h LogEvent 중 version_sha == "unknown" 비율 > 5%
       → Discord 경고 + UI LogHealthBadge ⚠️
```

> **Eventual consistency**: ingest 직후 ErrorGroup 조회 시 아직 없을 수 있음. UI는 최신 LogEvent의 `fingerprinted_at`이 NULL이면 "처리 중..." 표시. 일반적으로 < 1초.

### 6.2 조회 + Git 컨텍스트 join

```
[UI] GET /api/v1/projects/{id}/errors/abc-group-id
   ↓
[pslog] log_query_service.get_group_detail
   ├── ErrorGroup row 조회
   ├── 최근 N개 LogEvent (이 fingerprint)
   ├── version_sha 집합 추출 → 각 SHA에 대해:
   │   ├── Handoff WHERE commit_sha IN (...)
   │   ├── Task WHERE last_commit_sha IN (...)
   │   └── GitPushEvent WHERE head_commit_sha IN (...)
   │   └── join 결과 0 또는 SHA == "unknown" → "git 동기화 데이터 없음" 마킹
   ├── first_seen_version_sha 의 직전 정상 SHA 찾기
   │   └── (해당 fingerprint가 *없는* 가장 최근 SHA, 같은 environment)
   └── 응답 조립
   ↓
[UI] ErrorDetailPage 렌더
   ├── 본문 (메시지, 스택)
   ├── 빈도 차트 (시간별 발생)
   └── GitContextPanel
       ├── 첫 발생: commit + author + branch + task
       ├── 직전 정상: commit + diff 링크
       └── 최근 발생들의 author 분포
```

### 6.3 알림 라우팅 (cooldown 포함)

```
new fingerprint           → 조건: ErrorGroup.last_alerted_new_at IS NULL
                           → Discord: 🆕 새 에러 — {class}: {message}
                                      첫 발생: {commit_short} ({author}, {branch})
                                      pslog에서 보기: {url}
                           → set last_alerted_new_at = now()

frequency spike           → 조건: 5분 윈도우 N >= baseline × 3 AND
                                  (last_alerted_spike_at IS NULL OR
                                   last_alerted_spike_at < now() - 30min)
                           → Discord: ⚡ 급증 — {class} 5분 내 {N}회
                                      평소 {baseline}/h → 현재 {current}/h
                           → set last_alerted_spike_at = now()

regression                → 조건: status: RESOLVED → REGRESSED 전이 직후
                                  (last_alerted_regression_at IS NULL 또는
                                   직전 RESOLVED 이후)
                           → Discord: 🔁 재발 — {class} (RESOLVED 였음)
                                      {resolved_in_sha} 에서 해결 마킹됨
                                      이번 발생: {commit_short} ({author})
                           → set last_alerted_regression_at = now()

resolved (사용자 액션)    → 알림 없음 (UI 액션이 본인 명시적 결정)
```

> **spike 감지의 정확도**: 다중 워커 환경에서 baseline 카운터는 워커별 분리 → false negative만 발생 (알림 누락 가능, 잘못된 알림 X). 사고 본질적으로 acceptable.

---

## 7. 에러 처리 (pslog 측)

| 위치 | 케이스 | 대응 |
|---|---|---|
| Ingest | 토큰 형식 깨짐 (key_id 부분 없음) | 401 |
| Ingest | key_id 무효 | 401 즉시 (bcrypt 호출 X) |
| Ingest | secret bcrypt 검증 실패 | 401, 인증 실패 카운터 증가 (브루트포스 방어) |
| Ingest | rate limit 초과 | 429 + `Retry-After` 헤더, 클라이언트 backoff 책임 |
| Ingest | `version_sha` 형식 깨짐 (40자 hex/`unknown` 외) | 400 + 어떤 필드 잘못됐는지 응답, 나머지 이벤트 정상 처리. short SHA 송신은 운영 가이드 위반. |
| Ingest | 페이로드 형식 오류 | 400 + 잘못된 인덱스 응답, 나머지 정상 처리 |
| Ingest | 단일 이벤트 INSERT 실패 | 배치 abort 대신 실패 이벤트만 skip + 응답에 보고 |
| Ingest | 페이로드 ≥ 5MB | 413, 클라이언트가 분할 책임 |
| Health | unknown SHA 비율 > 5% (1h) | Discord 경고 + UI LogHealthBadge |
| Health | emitted_at vs received_at 차이 > 1h | UI 시계 경고 (보정은 안 함) |
| Health | drop_count 누적 (X-pslog-Dropped-Since-Last) | UI 표시 + Discord (1일 1회) |
| Fingerprint | stack_frames 비어있음 | `SHA1(exception_class + "|" + message 첫 줄)` 으로 fallback |
| Fingerprint | 정규화 후 앱 frame 0개 (모두 framework) | 가용한 frame top 5로 정규화(스킵 무시) — 그룹화는 약하지만 ErrorGroup은 만들어짐 |
| Fingerprint | 부팅 시 미처리 이벤트 | log_fingerprint_reaper가 회수 |
| Group | UPSERT 동시성 충돌 | UNIQUE(project_id, fingerprint) → ON CONFLICT DO UPDATE |
| Alert | Discord webhook 실패 | 기존 discord_service의 실패 처리 재사용 (3회 silent → disable) |
| Alert | spike 알림 폭격 | 30분 cooldown (`last_alerted_spike_at`) |
| GC | 파티션 DROP 시 진행 중 쿼리 | DROP은 sub-second, 큰 락 없음. 실패 시 다음날 재시도. |
| GC | RateLimitWindow 누적 | 일별 cron에서 24h+ row DELETE |
| Query | version_sha join 실패 (Handoff 없음) | git 컨텍스트 패널 비우고 "git 동기화 데이터 없음" 표시 |
| Query | unknown SHA만 있는 group | 모든 join 비움, 명시적 안내 |
| Gemma | Ollama 다운 | 회고는 fallback 텍스트 ("AI 요약 사용 불가, raw 데이터 N건") |

**전체 원칙**
- 수집은 **항상 받는다.** 처리/알림 실패가 원본 보존을 막지 않는다.
- 토큰 검증은 timing-safe 비교 (`hmac.compare_digest` 또는 `bcrypt.checkpw`).
- pslog 자체 로그는 본 시스템에 넣지 **않는다** (재귀 폭발 방지).
- `version_sha == "unknown"` 비율이 일정 기준 초과 시 운영자가 즉시 알게 가시화.

---

## 8. 보안 / 프라이버시

- **토큰**: `<key_id>.<secret>` 포맷. key_id는 평문 lookup, secret은 bcrypt 해시 저장. 발급 시 1회만 평문 노출. 환경변수 `pslog_LOG_INGEST_TOKEN`으로 app-chak에 주입.
- **토큰 발급 권한**: 프로젝트 OWNER/MAINTAINER 권한자만 (`permission_service` 재활용). 멤버는 발급 불가.
- **PII 마스킹**: app-chak 측 logging filter (`PIIFilter`)에서 password/token/email 패턴 마스킹. 송신 측 책임 (pslog가 다시 마스킹하면 이중 처리 + 누락 위험). **단** pslog 측 `log_ingest_service`는 의심 패턴(JWT 형태, 32자 hex, `password=`)을 휴리스틱 감지해 카운터 증가 + 일정 기준 초과 시 운영자에 경고 (마스킹은 안 함, 가시화만).
- **Rate limit**: 토큰별 분당 600건 (≈10 EPS) 기본. 초과 시 429. UI에서 한도 조정.
- **조회 권한**: `permission_service` 재활용 — Project 멤버만 조회.
- **Cloudflare Tunnel**: 기존 터널 그대로. ingest endpoint도 동일 경로로 노출.
- **로그 자체의 민감도**: stack trace에 파일 경로/내부 구조 노출 가능. 접근 제어로만 보호 (마스킹은 비현실적).

---

## 9. 성능

- **수집 처리량 가정**: 프로젝트 1개당 평균 1 EPS, 피크 50 EPS. PostgreSQL 단일 인스턴스로 여유.
- **uvicorn workers**: pslog는 `workers=1` 가정 (맥미니 단일 머신, 단일 프로세스). 다중 워커 필요 시 운영 문서 별도 — 본 설계는 rate limit만 정확하고(PostgreSQL), spike 감지는 워커별 메모리(false negative 허용).
- **인덱스 비용**: `idx_log_project_level_received` 가 핵심 hot path. `idx_log_version_sha` 는 join용 필수. `idx_log_message_trgm`은 부분 인덱스(`level >= WARNING`)로 디스크 절감.
- **배치 INSERT**: 단일 트랜잭션으로 N건. `INSERT ... VALUES (...), (...), ...`.
- **ErrorGroup UPSERT**: PostgreSQL `INSERT ... ON CONFLICT (project_id, fingerprint) DO UPDATE` 단일 쿼리.
- **bcrypt hot path 회피**: `<key_id>.<secret>` 포맷으로 key_id로 O(1) lookup → bcrypt는 유효 토큰일 때만 1회. 무효 토큰은 ms 단위 거부.
- **Rate limit**: PostgreSQL UPSERT 트랜잭션 1회 추가. 측정 시 미미 (< 1ms).
- **Spike 감지**: 워커별 메모리 dict + lock. 다중 워커 시 부정확 (acceptable).
- **GC**: `DROP PARTITION` 사용 → sub-second, vacuum bloat 없음. `DELETE` 폴백 시에만 청크 1000건씩.
- **풀텍스트 검색**: `pg_trgm` GIN 인덱스 + `ILIKE %keyword%`. WARNING↑만 인덱싱 (디스크 절감).
- **조회 페이지네이션**: cursor-based (`received_at < ? AND id < ?`), offset 사용 X.
- **git 컨텍스트 join 캐시**: 5분 TTL — 워커별 메모리. 다중 워커 시 효과 1/N. workers=1 가정에선 단순. 효과 미미하면 Phase 7 운영 후 제거 가능.

---

## 10. 테스트 전략

### 10.1 단위 테스트 (최우선)
- `fingerprint_service` (정규화 규칙 §4.1 6개 항목 각각 검증):
  - 동일 예외 + 줄번호만 다름 → 같은 fingerprint (규칙 #2)
  - 절대경로만 다름 (dev `/Users/alice/...` vs container `/app/...`) → 같은 fingerprint (규칙 #1)
  - 메모리 주소만 다름 (`<X at 0x7f...>`) → 같은 fingerprint (규칙 #3)
  - framework frame이 top에 끼어도 앱 frame 같으면 같은 fingerprint (규칙 #5)
  - 클래스 다름 → 다른 fingerprint
  - stack 비어있음 → message fallback
- `log_alert_service`:
  - 신규 fingerprint → 알림 1회만 (`last_alerted_new_at` 멱등)
  - spike → 30분 cooldown 정확
  - regression → 직전 RESOLVED 이후 1회만

### 10.2 통합 테스트
- `log_ingest_service`:
  - 배치 100건 INSERT 후 1건이 형식 오류 → 99건 저장, 1건 실패 보고
  - 토큰 `<key_id>.<secret>` 포맷, key_id 무효 시 bcrypt 호출 안 함 검증 (mock으로 카운팅)
  - `version_sha` 형식 깨진 이벤트만 reject, 나머지 정상 처리
  - 동일 토큰 동시 호출 N개 → rate limit 정확 (PostgreSQL UPSERT)
  - 토큰 폐기 후 호출 → 401
  - 5MB 초과 페이로드 → 413
  - PII 의심 패턴 카운터 증가 검증
- `error_group_service`:
  - 신규 → INSERT, 동일 fingerprint 동시 INSERT → UPSERT 정확
  - RESOLVED 상태에서 새 발생 → REGRESSED 전이 + 알림 1회
  - 모든 status 전이 (다이어그램의 모든 엣지): OPEN→RESOLVED, RESOLVED→OPEN, OPEN→IGNORED, IGNORED→OPEN, RESOLVED→REGRESSED, REGRESSED→RESOLVED
- `log_query_service`:
  - version_sha join — Handoff/Task 모두 있는 경우, 일부만 있는 경우, 전혀 없는 경우, "unknown" 케이스 각각 응답 포맷 검증
  - 직전 정상 SHA 찾기 — 해당 fingerprint가 한 번도 없었던 케이스, 늘 있었던 케이스
  - 풀텍스트 검색 (`pg_trgm`) — 부분 일치, 한글, 특수문자
- `log_fingerprint_reaper`:
  - `fingerprinted_at IS NULL`인 LogEvent를 만들고 reaper 실행 → 처리 + ErrorGroup 갱신 확인
  - 처리 중 크래시 시뮬레이션 → 다음 부팅에서 재처리 확인 **(CRITICAL)**

### 10.3 E2E (pslog + 가짜 app-chak)
- 가짜 클라이언트가 1000건 배치 push → pslog에서 ErrorGroup N개로 정확히 그룹화
- 동일 commit_sha의 LogEvent 발생 → pslog git_context_panel에 Handoff/Task 자동 표출
- pslog 일시 다운 → 핸들러 큐 보존 → pslog 복구 후 재송신, drop_count 헤더 정확

### 10.4 마이그레이션 회귀 테스트
- 본 설계는 기존 pslog 모델을 **건드리지 않음** — Project/Task 컬럼 추가 없음. 신규 테이블만.
- 그래도 alembic up/down 정상 검증.
- 파티션 자동 생성 (pg_partman 또는 수동 cron) 정상 동작 확인.

### 10.5 Handler 라이브러리 테스트 (app-chak 측)
- `pslog_log_handler`:
  - flush 트리거: 크기 ≥ 10건, 시간 ≥ 2초, atexit 5초 타임아웃
  - HTTP 5xx/timeout 시 backoff (1s/5s/30s/5min)
  - 큐 한도 초과 시 가장 오래된 것부터 drop, drop_count 정확
  - pslog 다운 → 큐 보존 → 복구 → 다음 송신에 `X-pslog-Dropped-Since-Last` 헤더 정확
  - gzip 압축 검증
- `PIIFilter`: 이메일/JWT/password 키 패턴 마스킹

### 10.6 다중 워커 시나리오 (정책 검증)
- workers=2 환경에서 rate limit이 정확한지 (PostgreSQL UPSERT) — pass
- workers=2 환경에서 spike 감지 false negative 발생 — expected, 문서화

### 10.7 GC 부하 테스트 (Phase 7)
- 일별 파티션 DROP의 OLTP 영향 측정 — 0에 가까울 것
- DELETE fallback 청크 1000건 시 vacuum 영향 측정

### 10.8 Gemma 회고 (선택, Phase 후반)
- `FakeOllamaClient` 고정 응답
- 실제 호출은 manual smoke test

---

## 11. 단계적 도입 (마이그레이션)

선행 조건: `2026-04-26-ai-task-automation-design.md` Phase 4 머지 완료. 이유: `version_sha` join에 Handoff/Task의 commit_sha가 필요. **"안정화"의 정의**: Phase 4 회귀 테스트 모두 그린 + 1주 운영 무중단.

**Phase 0 — Handler 라이브러리 + app-chak 준비 (병렬 가능)**
- `pslog_log_handler.py` 단일 모듈 작성 (app-chak 레포 `backend/app/utils/`에 직접 복사)
- `PIIFilter` 패턴 셋 정의 (app-chak 코드 보고 결정 — 이메일/JWT/password/Bearer)
- app-chak에 환경변수 `APP_VERSION_SHA` 주입 메커니즘 구축 (Docker build arg = `git rev-parse HEAD` **40자 full**, short SHA 금지)
- app-chak에 환경변수 `APP_PROJECT_ROOT` 주입 (fingerprint 절대경로→상대경로 정규화에 사용)
- app-chak Python logging 설정에 핸들러 등록 (Phase 2 후 활성화)

**Phase 1 — 모델/마이그레이션 (pslog)**
- LogEvent (일별 파티션) / ErrorGroup / LogIngestToken / RateLimitWindow 테이블
- 인덱스 5종 (trgm 포함)
- pg_partman 설치 또는 수동 파티션 cron
- 토큰 발급/폐기 API + 권한 (OWNER/MAINTAINER)

**Phase 2 — Ingest endpoint**
- `POST /log-ingest` — `<key_id>.<secret>` 토큰 검증
- `version_sha` 형식 검증
- rate limit (PostgreSQL UPSERT)
- 처리는 동기 INSERT만, fingerprint/group은 BackgroundTask
- log_fingerprint_reaper 부팅 hook
- log_health_service hourly cron

**Phase 3 — Fingerprint + ErrorGroup**
- `fingerprint_service`, `error_group_service`
- 단위 테스트 우선
- 신규 / spike / regression 감지 알고리즘 + cooldown

**Phase 4 — 조회 API + Git 컨텍스트 join (핵심 가치 전달)**
- `log_query_service`
- `GET /errors`, `GET /errors/{id}` (Handoff/Task join 포함)
- 직전 정상 SHA 찾기 알고리즘
- 풀텍스트 검색 엔드포인트 (pg_trgm)

**Phase 5 — UI**
- ErrorsPage (목록)
- ErrorDetailPage + GitContextPanel
- LogsPage (실시간 폴링 + 메시지 검색창)
- LogTokensPage
- LogHealthBadge (헤더의 unknown SHA 비율 표시)

**Phase 6 — Discord 알림**
- 기존 discord_service에 메시지 템플릿 3종 (신규/spike/regression)
- cooldown 로직 정확 검증

**Phase 7 — GC + 운영**
- 일별 파티션 자동 DROP
- RateLimitWindow GC
- rate limit / 토큰 사용량 어드민 대시보드
- GC 부하 측정 + 필요 시 튜닝

**Phase 8 — Gemma 주간 회고 (선택)**
- `log_summary_service` + `ollama_client` 재활용
- task-automation 설계의 Gemma Phase와 같은 시점에 진행 가능 (인프라 공유)

각 Phase는 독립적으로 머지 가능. **0~4가 핵심 가치 전달 (이 시점에 사용자가 원한 "에러 ↔ git 작업 추적"이 동작).**

---

## 12. 비범위 (Out of Scope)

- **트레이싱 / APM**: 본 설계는 로그 + 에러만. 분산 트레이싱 (OpenTelemetry)은 별도 스펙.
- **메트릭**: 카운터/게이지/히스토그램은 본 설계 X. Prometheus 등 별도.
- **React Native 프론트 에러 수집**: app-chak이 Expo + RN인데, 본 설계는 백엔드 로그만 다룸. 프론트 측은 후속 (Sentry RN SDK 또는 자체 핸들러).
- **다중 환경 분리 보존 정책** (production 90일 / staging 7일): 환경 무관 단일 정책으로 시작.
- **알림 채널 라우팅 룰**: 모든 에러는 프로젝트의 Discord webhook으로 직진. Slack/이메일/PagerDuty는 후속.
- **자동 issue 생성** (GitHub Issue 자동 작성): 후속.
- **에러 → pslog Task 자동 생성**: 후속. 처음엔 사용자가 수동으로 ErrorGroup을 보고 Task 만듦.
- **다중 워커 환경 정확한 spike 감지**: workers=1 가정. 다중 워커 필요 시 Redis 도입 별도 검토.
- **`ErrorGroupEvent` 감사 로그 테이블**: status 전이 이력 별도 추적은 본 설계 v1에 미포함, 후속.

---

## 13. Open Questions

향후 구현 단계에서 확정 필요한 항목:

1. **rate limit 정책의 세부 수치**: 600/min이 적절한지 app-chak 실측 후 조정 → Phase 2 후반.
2. **빈도 급증 baseline 계산법**: 단순 5분 슬라이딩 vs 시간대별 baseline (24h 패턴) → Phase 3 구현 시점에 결정.
3. **로그 보존 기간 (30/90일)**: 디스크 사용량 실측 후 조정 가능 → Phase 7.
4. **PII 필터 패턴 셋 최종**: Phase 0 시점에 app-chak 코드 보고 정의.
5. **첫 발생 vs 직전 정상 SHA 알고리즘**: 같은 fingerprint가 *없었던* 가장 최근 SHA를 어떻게 효율적으로 찾을지 (전수 스캔 vs 캐시 vs `first_seen_at` 기준 단순 휴리스틱) → Phase 4.
6. **`ErrorGroupEvent` 감사 테이블 추가 시점**: 본 설계 v1 이후 운영 1개월 후 필요성 재검토.
7. **`APP_PROJECT_ROOT` 추출 휴리스틱 fallback**: 환경변수 누락 시 첫 `backend/` 또는 `src/` segment를 root로 보는 fallback이 정확한지 → Phase 3 운영 후 튜닝.
8. **fingerprint 재계산 마이그레이션**: 정규화 규칙 변경 시 기존 LogEvent의 fingerprint를 재계산할지(이력 보존) vs 그대로 둘지(신규부터만 적용) → 규칙 변경이 실제 발생할 때 결정.

---

## 14. 결정 사항 요약 (Decision Log)

- 2026-04-26: 본 설계는 task-automation Phase 4 후 시작 (선행 의존)
- 2026-04-26: 수집 = HTTP push (Python logging handler), 인프라 추가 0
- 2026-04-26: 배치 ≥10건 또는 ≥2초, gzip JSON
- 2026-04-26: `version_sha` 환경변수 주입이 git 컨텍스트 join의 핵심 키
- 2026-04-26: fingerprint = exception_class + 정규화 stack frame top 5 SHA1
- 2026-04-26: 알림 = 기존 discord_service 재활용 (별도 채널 X)
- 2026-04-26: ErrorGroup status 4종 (OPEN/RESOLVED/IGNORED/REGRESSED), regression 자동 감지
- 2026-04-26: pslog 자체 로그는 본 시스템에 넣지 않음 (재귀 방지)
- 2026-04-26: PII 마스킹은 송신 측(app-chak) 책임, 수신 측은 의심 패턴 가시화만
- 2026-04-26: AI는 주간 회고에만 (Gemma 4), fingerprint/집계는 결정적
- 2026-04-26 (Rev2): **Phase 2의 fingerprint 처리는 BackgroundTask + 부팅 reaper** — `LogEvent.fingerprinted_at` 컬럼 + `idx_log_unfingerprinted`로 회귀 처리. eventual consistency 명시.
- 2026-04-26 (Rev2): **`version_sha` 형식 검증 + `unknown` 비율 모니터링** — 5% 초과 시 Discord 경고 + UI LogHealthBadge.
- 2026-04-26 (Rev2): **토큰 포맷 = `<key_id>.<secret>`** — key_id로 O(1) lookup, secret만 bcrypt. hot path bcrypt 부하 회피.
- 2026-04-26 (Rev2): **rate limit = PostgreSQL `RateLimitWindow` UPSERT** — 다중 워커에서도 정확. spike 감지는 워커별 메모리(false negative 허용).
- 2026-04-26 (Rev2): **PostgreSQL 일별 range partition + `DROP PARTITION` GC** — DELETE vacuum bloat 회피.
- 2026-04-26 (Rev2): **ErrorGroup status 전이 ASCII 다이어그램 §4.1에 명시** — 모든 엣지(REGRESSED→RESOLVED 등) 정의.
- 2026-04-26 (Rev2): **알림 cooldown** — `last_alerted_new_at` (1회만), `last_alerted_spike_at` (30분), `last_alerted_regression_at` (사이클당 1회).
- 2026-04-26 (Rev2): **핸들러 pslog 다운 정책** — 큐 1000건/5MB, backoff 1s→5s→30s→5min, atexit 5초 타임아웃, drop_count 헤더 보고.
- 2026-04-26 (Rev2): **풀텍스트 검색 `pg_trgm` Phase 5에 포함** — 비범위에서 빼고 핵심 기능으로 격상. WARNING↑만 인덱싱.
- 2026-04-26 (Rev2): **handler 배포 = app-chak 레포 직접 복사** — Open Q에서 Decision으로 승격. PyPI/submodule 인프라 회피.
- 2026-04-26 (Rev2): **토큰 발급 권한 = OWNER/MAINTAINER만** — 멤버는 발급 불가, permission_service 재활용.
- 2026-04-26 (Rev2): **uvicorn workers=1 가정** — 운영 문서에 명시. 다중 워커 시 spike 감지 부정확 acceptable.
- 2026-04-27 (Rev3): **`version_sha` = 40자 full SHA 강제** — short SHA(7~12자) 송신 reject (400). 이유: ① `Handoff.commit_sha`(40자)와 `=` join 보장 ② 7자 short는 대형 repo에서 충돌 사례 존재(birthday) ③ `git rev-parse HEAD` 한 줄로 운영 가이드 단순화. `unknown` 만 예외.
- 2026-04-27 (Rev3): **fingerprint 정규화 규칙 6개 명시** (§4.1) — ① 절대경로→상대경로(`APP_PROJECT_ROOT` 기준) ② line number 제거 ③ 메모리 주소 마스킹(`0xADDR`) ④ 함수명 유지(lambda/closure 포함) ⑤ framework/stdlib frame은 top 5 산정에서 스킵 ⑥ 입력 포맷 `<class>|<rel_path>:<func>\n...`. 이유: dev/prod 경로 차이 + 줄 수정으로 인한 false split 방지. Phase 3 단위 테스트의 acceptance criteria로 사용.
