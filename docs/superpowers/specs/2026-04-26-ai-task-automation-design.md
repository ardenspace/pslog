# AI 태스크 자동화 설계서 (pslog)

작성일: 2026-04-26
개정: 2026-04-26 (Rev. 2 — 엔지니어링 리뷰 반영)

## 변경 이력

- **v1 (2026-04-26)**: 초안
- **v2 (2026-04-26)**: `/plan-eng-review` 1차 반영
  - Task 상태 모델: `status: TaskStatus` 재사용 (별도 `checked_at` 신설 X)
  - `TaskEventAction` enum 확장 (4개 추가)
  - `external_id` 프로젝트 내 UNIQUE 제약
  - `Project.webhook_secret`을 프로젝트별 Fernet 암호화
  - `Task.archived_at` 추가 (PLAN에서 삭제 케이스 soft-delete)
  - PLAN.md "골디락스 룰" 가이드라인 + handoff 서브 체크박스 패턴
  - app-chak 선행 작업을 Phase 0로 분리
  - Background task 부팅 reaper 명시
  - Webhook commits 길이>20 fallback (Compare API)
  - handoff 누락 정책 강화 (silent → 명시적 가시화)
  - 에러 로그 + Git 상관관계는 후속 문서로 분리

---

## 1. 배경

pslog는 현재 수동 Kanban 기반 B2B 태스크 관리 도구다. 본 기능은 pslog를 그대로 유지(리팩토링 방식)하면서 다음을 추가한다:

- 외부 프로젝트(예: `app-chak`) 레포의 **PLAN.md**를 pslog의 태스크 목록과 자동 동기화
- 팀원의 **git push** 이벤트로 태스크 체크 상태를 자동 갱신
- 팀원이 **다음 작업 재개 시** PLAN과 handoff 이력을 종합한 브리핑을 로컬 Gemma 4로 생성

핵심 원칙: **AI는 합성·요약·추천에만 사용**한다. 태스크 체크 같은 사실(state) 결정은 결정적 파서(마크다운/정규식)로 처리한다.

---

## 2. 사용자 시나리오

### 2.1 새 작업 시작
1. 팀원의 로컬 Claude Code가 pslog API의 브리핑 endpoint를 호출
2. pslog는 PLAN.md + 최근 handoff 섹션 + TaskEvent를 종합해 Gemma 4로 자연어 브리핑 생성
3. 팀원이 권한을 주면 Claude Code가 실제 코드 수정 진행

### 2.2 git push 발생 (자동 처리)
1. 팀원이 `feature/<branch>`에서 push (handoff 파일 갱신 포함)
2. GitHub → pslog webhook → handoff/PLAN 파일 파싱
3. 태스크 상태 갱신 (`status=DONE`), Discord 알림 발송
4. 작업 이력은 Handoff/TaskEvent 테이블에 보존

### 2.3 Project 최초 git 연동
1. pslog UI에서 GitHub repo URL, PLAN 경로, handoff 디렉토리 입력
2. pslog가 GitHub Webhook 자동 등록 (프로젝트별 secret 자동 생성)
3. PLAN.md 1회 fetch → 초기 Task 일괄 생성

### 2.4 그래뉼래리티 분리 — 마스터 vs handoff
- **마스터 PLAN.md** = 0.5~3일짜리 task 단위. pslog DB가 추적하는 유일한 단위.
- **handoff/{branch}.md** = 분/시간 단위 진행 메모. pslog DB는 마스터 task의 체크만 읽음. handoff 안의 서브 체크박스는 자유 영역(Gemma 브리핑의 컨텍스트로만 사용).

이 구조 덕에 (a) 머지 충돌은 마스터에서 거의 발생 안 함, (b) 각자 페이스대로 자유롭게 쪼갤 수 있음, (c) pslog Kanban은 의미 있는 단위만 보여줌.

---

## 3. 아키텍처 결정

| 결정 | 선택 | 이유 |
|---|---|---|
| 리팩토링 vs 신규 프로젝트 | **리팩토링** | 기존 인증·권한·Project·Task·Discord 자산 재사용 |
| AI 위치 | **pslog 외부(팀원의 Claude) + 내부(Gemma 4 보조)** | 결정적 파이프라인 + 합성만 AI |
| AI 모델 (pslog 내부) | **로컬 Gemma 4 26B MoE (llama.cpp)** | API 비용 0, 사용자 머신에 이미 존재 |
| Git 통합 메커니즘 | **GitHub Webhook (모든 브랜치)** | 모든 push 캡처, GitHub Actions와 독립 |
| 외부 노출 | **Cloudflare Tunnel (기존 사용 중)** | 추가 인프라 불필요 |
| 계획서 형식 | **PLAN.md (마스터, 1개) + handoff-{branch}.md (브랜치별)** | 머지 충돌 회피 + 그래뉼래리티 분리 |
| 데이터 모델 접근법 | **Task 모델 확장 + Project Git-aware** | 단일 태스크 모델, UI 자연스러움 |
| 태스크 상태 표현 | **기존 `status: TaskStatus` 재사용** (`- [x]` ⟷ `DONE`) | 이중 진실 회피, UI 변경 없음 |
| TaskEvent 새 액션 | **enum 확장** | explicit > clever, 통계/쿼리 단순 |
| 태스크 ↔ 커밋 매칭 | **handoff 체크박스 우선, 보조로 커밋 메시지 컨벤션 + 파일 경로** | 결정적, AI 의존 없음 |
| Webhook secret | **프로젝트별 (Fernet 암호화)** | 격리, secret rotation 영향 최소 |
| 비동기 작업 처리 | **FastAPI BackgroundTask + 부팅 reaper** | 인프라 추가 없이 내구성 확보 |

---

## 4. 데이터 모델

### 4.1 기존 모델 확장

**`Project`** (5 필드 추가)
```python
git_repo_url: str | None              # "https://github.com/foo/app-chak"
git_default_branch: str = "main"
plan_path: str = "PLAN.md"
handoff_dir: str = "handoffs/"
last_synced_commit_sha: str | None
webhook_secret_encrypted: bytes | None  # Fernet 암호화, 자동 webhook 등록 시 랜덤 생성
```

**`Task`** (4 필드 추가)
```python
source: TaskSource                    # "manual" | "synced_from_plan"
external_id: str | None               # "task-001"
last_commit_sha: str | None           # 40자 hex full (GitHub head_commit.id). short SHA 금지 — error-log join key.
archived_at: datetime | None          # PLAN에서 제거된 synced 태스크 soft-delete
```

**제약 추가**
```sql
-- 같은 프로젝트 내에서 external_id 중복 금지 (NULL은 허용)
CREATE UNIQUE INDEX idx_task_project_external_id
  ON tasks (project_id, external_id)
  WHERE external_id IS NOT NULL;
```

**상태 매핑 규칙**
- PLAN/handoff에서 `- [x] task-001` → 해당 Task `status = DONE`
- PLAN/handoff에서 `- [ ] task-001` → 해당 Task `status` 변경 없음 (기존 TODO/DOING/BLOCKED 보존)
- 단, 직전이 `DONE`이었는데 `- [ ]`로 돌아오면 `status = TODO` (롤백 케이스)
- PLAN.md에서 `[task-001]`이 사라진 push → `archived_at = now()`, Discord 알림. hard-delete 안 함.

기존 데이터는 모두 유효 (`source` 기본값 = `"manual"`, `archived_at` = `NULL`).

### 4.2 신규 모델

**`Handoff`** — push마다 1행 INSERT
```python
id: UUID
project_id: UUID                      # FK
branch: str                           # "feature/login-redesign"
author_user_id: UUID | None           # pslog User 매칭 (nullable)
author_git_login: str
commit_sha: str                       # 이 handoff를 포함한 커밋. 40자 hex full (Phase 1 alembic CHECK 제약 대상)
pushed_at: datetime
raw_content: text                     # 파싱 전 원본 마크다운 (30일 보존, 그 후 GC)
parsed_tasks: JSON                    # [{"external_id": "task-001", "checked": true}, ...]
free_notes: JSON                      # {"last_commit": "...", "next": "...", "blockers": "...", "subtasks": [...]}

UNIQUE (project_id, commit_sha)       # 멱등성
```

> **GC 정책**: `raw_content`는 30일 후 NULL로 비움 (별도 cron job). `parsed_tasks`/`free_notes`는 영구 보존. 재처리 필요 시 GitHub에서 SHA로 재페치.

**`GitPushEvent`** — webhook raw 보존
```python
id: UUID
project_id: UUID
branch: str
head_commit_sha: str                  # webhook payload의 head_commit.id
commits: JSON                         # webhook payload의 commits 배열 (최대 20커밋)
commits_truncated: bool               # commits 길이 == 20일 때 true → Compare API 호출 트리거
pusher: str
received_at: datetime
processed_at: datetime | None
error: text | None

UNIQUE (project_id, head_commit_sha)
```

**`TaskEventAction` enum 확장** — alembic으로 PostgreSQL `ALTER TYPE ... ADD VALUE`
- 기존: `CREATED`, `UPDATED`, `STATUS_CHANGED`, `ASSIGNED`, `COMMENTED`, `DELETED`
- 신규: `SYNCED_FROM_PLAN`, `CHECKED_BY_COMMIT`, `UNCHECKED_BY_COMMIT`, `ARCHIVED_FROM_PLAN`

---

## 5. 컴포넌트 / 서비스

### 5.1 신규 백엔드 서비스

```
backend/app/services/
  github_webhook_service.py    ① 서명 검증 + GitPushEvent INSERT
  git_repo_service.py           ② 파일 fetch (GitHub Contents API + Compare API fallback)
  plan_parser_service.py        ③ PLAN.md → 태스크 목록 (정규식)
  handoff_parser_service.py     ④ handoff-{branch}.md → 체크 상태 + 자유 영역
  sync_service.py               ⑤ 파싱 결과 → Task/Handoff DB 반영
  brief_service.py              ⑥ Gemma 4로 브리핑 생성
  ollama_client.py              ⑦ Ollama HTTP 클라이언트
  push_event_reaper.py          ⑧ 부팅 시 미처리 GitPushEvent 재처리
```

각 서비스의 책임:

| # | 입력 | 출력 | 외부 의존 |
|---|---|---|---|
| ① | GitHub POST payload | GitPushEvent row | 없음 |
| ② | (project, sha, path) 또는 (project, base_sha, head_sha) | 파일 내용 / 변경 파일 목록 | GitHub API |
| ③ | PLAN.md 텍스트 | `[{external_id, title, assignee, paths}]` | 없음 |
| ④ | handoff 텍스트 | `{checks, free_notes, subtasks}` | 없음 |
| ⑤ | webhook 이벤트 | DB 변경 + TaskEvent | ②③④ |
| ⑥ | (project, user) | 자연어 브리핑 | ⑦ |
| ⑦ | prompt | completion | Ollama HTTP |
| ⑧ | 부팅 trigger | 미처리 이벤트 재처리 | ⑤ |

### 5.2 신규 API 엔드포인트

```
POST   /api/v1/webhooks/github               # GitHub webhook 수신
GET    /api/v1/projects/{id}/git-settings    # 현재 git 설정 조회
PATCH  /api/v1/projects/{id}/git-settings    # repo URL, plan_path 등 수정
GET    /api/v1/projects/{id}/handoffs        # 브랜치별 handoff 이력
GET    /api/v1/projects/{id}/brief           # 작업 재개 브리핑 (Gemma 4)
POST   /api/v1/projects/{id}/git-events/{id}/reprocess  # 수동 재처리
```

### 5.3 신규 프론트엔드

```
frontend/src/
  pages/
    ProjectGitSettings.tsx
    HandoffHistory.tsx
  components/
    TaskCard.tsx                  # 기존, source 배지 + ⚠️ handoff 누락 표시
    DailyBriefPanel.tsx
  hooks/
    useGithubSettings.ts
    useDailyBrief.ts
  services/
    githubApi.ts
```

---

## 6. 파일 형식 규약

### 6.1 PLAN.md (스프린트 마스터, 1개)

```markdown
# 스프린트: <이름>

## 태스크

- [ ] [task-001] 로그인 UI 리뉴얼 — @alice — `frontend/screens/Login.tsx`, `frontend/components/auth/`
- [ ] [task-002] JWT 토큰 만료 처리 — @bob — `backend/auth/`
- [ ] [task-003] 알림 모달 — @charlie — `frontend/components/Notification.tsx`

## 노트
<자유 메모, pslog는 무시>
```

**골디락스 룰 — 태스크 단위 가이드라인**

1 태스크 = **0.5~3일** 작업 분량을 권장. 이유:
- 너무 작으면(1~2시간): 머지 충돌 잦음, PLAN.md 길이 폭발, pslog Kanban 가독성 ↓
- 너무 크면(1주일+): pslog Kanban에서 한 주 내내 안 움직이는 카드. 진행 추적 가치 ↓
- 작업 도중 너무 컸다는 게 드러나면 PR 안에서 PLAN.md를 쪼개는 것 자유롭게 허용 (git이라 자연스럽게 됨)

마스터를 더 잘게 쪼개고 싶으면 **handoff의 서브 체크박스(§6.2)에서 자유롭게 쪼갠다.** pslog DB는 마스터 단위만 추적.

**파싱 규칙**
- `- [ ]` / `- [x]` 체크박스 라인만 태스크로 인식
- `[task-XXX]` 형식의 ID 필수, 프로젝트 내 unique
- `@username` → assignee
- `` `path` `` (백틱) → 영향 파일/폴더
- `## 태스크` 헤더 아래 영역만 파싱, 그 외는 무시

### 6.2 handoff-{branch}.md (브랜치별, 팀원별)

위치: `app-chak/handoffs/feature-login-redesign.md` (브랜치명의 `/`는 `-`로 치환)

```markdown
# Handoff: feature/login-redesign — @alice

## 2026-04-26
- [x] task-001
- [ ] task-007 (60% 완료)
  - [x] 이메일 입력 필드        # ← 서브 체크박스: pslog DB에 저장 안 함
  - [x] validation 로직          #    raw로 보존 + Gemma 브리핑 컨텍스트로만 사용
  - [ ] 약관 동의 체크박스
  - [ ] 에러 메시지 i18n

### 마지막 커밋
abc1234 — 로그인 폼 검증 로직

### 다음
- task-007 마무리 후 PR

### 블로커
없음

---

## 2026-04-25
- [x] task-001 시작
...
```

**파싱 규칙**
- 최상위 `# Handoff: <branch> — @<user>` 헤더에서 브랜치/유저 추출
- `## YYYY-MM-DD` 섹션이 일자별 단위. 최신 날짜 섹션이 active
- 각 날짜 섹션 안의 **최상위 들여쓰기 0인** `- [x] task-XXX` / `- [ ] task-XXX` 체크박스만 pslog DB에 반영
- 들여쓰기 2 이상의 서브 체크박스는 `free_notes.subtasks`에 보존, DB의 Task 상태에는 영향 없음
- `### 마지막 커밋`, `### 다음`, `### 블로커` 자유 영역도 `free_notes`로 보존

---

## 7. 데이터 흐름

### 7.1 git push → 자동 동기화

```
[팀원] git push (handoff 갱신 포함)
   ↓
[GitHub] webhook POST /api/v1/webhooks/github
   ↓
[pslog] github_webhook_service
   ├── X-Hub-Signature-256 검증 (프로젝트별 secret 사용)
   ├── repo URL → Project 조회
   └── GitPushEvent INSERT (commits_truncated 플래그 설정)
   ↓
[즉시 200 응답]
   ↓
[FastAPI BackgroundTask] sync_service.process(event)
   ├── 변경 파일 목록 확보
   │   ├── commits_truncated == false → webhook payload의 commits[*].modified 사용
   │   └── commits_truncated == true  → Compare API (before...head_commit_sha) 호출
   ├── 변경 파일 중 handoffs/* 또는 PLAN.md 있는지 확인. 없으면 종료.
   ├── git_repo_service.fetch(PLAN.md, head_commit_sha) (변경됐으면)
   ├── git_repo_service.fetch(handoff-{branch}.md, head_commit_sha)
   ├── plan_parser_service.parse / handoff_parser_service.parse
   ├── DB 업데이트
   │   ├── Task.status (체크 변경 시 DONE / 롤백 시 TODO)
   │   ├── Task.last_commit_sha
   │   ├── Task.archived_at (PLAN에서 사라진 task)
   │   ├── Handoff INSERT (UNIQUE 충돌 시 skip — 멱등성)
   │   └── TaskEvent (CHECKED_BY_COMMIT 등)
   ├── discord_service.notify (체크 변경 요약 + handoff 누락 경고)
   └── GitPushEvent.processed_at = now()

[부팅 시] push_event_reaper
   └── processed_at IS NULL AND received_at < now() - 5min 인 이벤트 재처리
       └── 컨테이너 재시작/크래시로 누락된 이벤트 회수
```

> **HTTP 응답 정책**: 서명 검증 실패는 401, 알 수 없는 repo는 200 + 경고 로그(GitHub 재전송 방지), DB 쓰기 실패는 500 (GitHub 자동 재시도). 처리 단계 실패는 GitPushEvent.error에 기록하고 사용자가 UI에서 재처리.

### 7.2 작업 재개 → 브리핑

```
[팀원의 Claude Code]
   GET /api/v1/projects/{id}/brief?user=alice&branch=feature/login-redesign
   ↓
[pslog] brief_service
   ├── 캐시 확인 (project_id, user, branch) — TTL 5분
   ├── single-flight lock (중복 호출 시 결과 공유, in-process — workers=1 가정)
   ├── PLAN에서 alice의 미완료 태스크
   ├── 최근 N일 handoff 섹션 (subtasks 포함)
   ├── 어제~오늘 TaskEvent
   └── 위 컨텍스트 → Gemma 4 프롬프트
   ↓
[Gemma 4] 자연어 브리핑 생성 (타임아웃 30초)
   ↓
[pslog] 캐시 후 응답
   ↓
[Claude Code] 사용자에게 브리핑 표시 + 권한 요청
```

> **추론 시간 가정 검증**: Phase 7 진입 전 맥미니에서 Gemma 4 26B MoE 실측 필요. 30초를 빈번히 초과하면 비동기 응답(`202 Accepted` + job_id 폴링) 패턴으로 전환.

---

## 8. 에러 처리

| 위치 | 케이스 | 대응 |
|---|---|---|
| Webhook | 서명 검증 실패 | 401, 보안 로그 |
| Webhook | 알 수 없는 repo | 200 + 경고 로그 (재전송 방지) |
| Webhook | DB 쓰기 실패 | 500 → GitHub 자동 재시도 |
| Webhook | commits 길이 == 20 | commits_truncated=true 마킹, sync 단계에서 Compare API 호출 |
| Fetch | PLAN.md 없음 | skip + Project에 `plan_missing=true` 마킹 |
| Fetch | handoff 없음 | **항상** Discord 경고 + UI에 ⚠️ 표시 (silent 옵션 X — 사용자 정책 위반 가시화) |
| Fetch | API rate limit | exponential backoff (10s/60s/300s) → 실패 시 GitPushEvent.error에 기록, 사용자 재처리 가능 |
| Fetch | rate limit 80% 도달 | UI 경고 배지 + 관리자 Discord 알림 |
| 파싱 | 형식 깨짐 | 파싱 가능한 부분만 처리, raw 보존, error 필드에 사유 |
| 파싱 | task ID가 PLAN에 없음 | Task 미생성, Handoff.parsed_tasks에 orphan 표시. PLAN 추가 후 다음 push 때 매칭 |
| 파싱 | external_id 중복 (PLAN 내) | 파서 단계에서 reject + Discord 경고, DB UNIQUE가 2차 방어 |
| 동기화 | 같은 task 동시 체크 | last-write-wins (commit_sha 시각 기준), 양쪽 TaskEvent 보존 |
| 동기화 | 체크 → 언체크 (롤백) | 정상 처리 (`status: DONE → TODO`), Discord에 "되돌림" 알림 |
| 동기화 | PLAN에서 task 삭제 | `archived_at = now()`, hard-delete 안 함, Discord 알림 |
| 동기화 | 컨테이너 재시작/크래시 | push_event_reaper가 부팅 시 미처리 이벤트 회수 |
| Gemma | Ollama 다운 | fallback 텍스트 + DB raw 데이터 |
| Gemma | 타임아웃 (>30s) | 부분 응답 반환, 캐시 안 함 — Phase 7에서 비동기 응답 패턴 검토 |
| Discord | webhook 무효 | silent (1회) → 3회 연속 실패 시 disable + UI 경고 |

**전체 원칙**
- 외부 의존(GitHub, Gemma, Discord) 실패는 pslog 코어를 막지 않는다.
- 모든 push는 GitPushEvent에 raw로 보존 → 코드 수정 후 재처리 가능.
- 사용자가 UI에서 "이 push 다시 처리" 수동 트리거 가능.
- handoff 누락 같은 **정책 위반은 silent로 처리하지 않고 항상 가시화**한다.

---

## 9. 보안

- **Webhook 서명 검증**: 프로젝트별 `webhook_secret` (Fernet 암호화 저장), `X-Hub-Signature-256` HMAC 검증. 자동 webhook 등록 시 32-byte 랜덤 생성. 한 프로젝트 secret 유출이 다른 프로젝트로 전파되지 않음.
- **Fernet 마스터 키**: `PSLOG_FERNET_KEY` 환경변수, 맥미니의 시스템 keychain 또는 `/etc/pslog/secrets/`에 0400 권한으로 보관. 키 회전 절차는 별도 운영 문서.
- **GitHub PAT**: Project별로 Fernet 암호화 저장 (기존 pslog 패턴 따름)
- **Cloudflare Tunnel**: 외부 노출은 기존 터널 재사용, 직접 IP 노출 없음
- **Ollama**: localhost(맥미니) 내부 통신만, 외부 접근 차단
- **Brief API 권한**: 호출자는 Project 멤버여야 함 (기존 permission_service 재활용)

---

## 10. 테스트 전략

### 10.1 파서 단위 테스트 (최우선)
- `plan_parser_service`: 정상 / 형식 어긋남 / 빈 파일 / 노트 영역 무시 / external_id 중복 reject
- `handoff_parser_service`: 다중 날짜 섹션, 체크박스 diff, 들여쓰기로 서브 체크박스 분리, 자유 영역 보존

### 10.2 서비스 통합 테스트
- `sync_service`:
  - **멱등성 (CRITICAL)**: 동일 webhook 2번 → 변경 1번만, Handoff 1행만
  - 부분 실패 시 GitPushEvent 잔존 + 재처리 가능
  - PLAN에서 task 삭제 → `archived_at` 설정, hard-delete 없음
  - 체크 → 언체크 (`status: DONE → TODO`) 회귀
  - force-push로 commits 길이 == 0 케이스
  - commits_truncated == true 시 Compare API 호출 검증
- `github_webhook_service`:
  - signature 검증 실패 시 401 + body 미저장
  - 프로젝트별 secret 격리 (다른 프로젝트 secret으로 검증 시 실패)

### 10.3 마이그레이션 회귀 테스트 (CRITICAL)
- 기존 production-like 데이터셋(스냅샷) 위에서 alembic 적용
- 적용 후 모든 기존 API 응답이 byte-equal로 동일한지 검증 (`source` 기본값 = `"manual"`, 기존 Task 동작 변화 없음)
- TaskEventAction enum 확장이 기존 enum 행 모두 보존하는지 확인
- 롤백(`alembic downgrade -1`) 정상 동작 확인

### 10.4 Reaper 테스트
- `processed_at IS NULL AND received_at < now() - 5min` 인 GitPushEvent를 만들고 reaper 실행 → 처리됨 확인
- 처리 중 크래시 시뮬레이션 → 다음 부팅에서 재처리 확인

### 10.5 Gemma 모킹
- `ollama_client`를 인터페이스로, 테스트는 `FakeOllamaClient` 고정 응답
- single-flight lock 동시 호출 N개 → Gemma 호출 1번만 검증
- 실제 호출은 manual smoke test로만

### 10.6 프론트 단위 테스트
- ProjectGitSettings 폼 검증 (repo URL 형식, plan_path 빈 값 거부)
- DailyBriefPanel 렌더링 (브리핑/로딩/에러 상태)
- TaskCard handoff 누락 ⚠️ 표시 조건

---

## 11. 단계적 도입 (마이그레이션)

이 설계는 한 번에 다 구현하지 않는다. 단계 분할:

**Phase 0 — app-chak 선행 작업** (pslog 변경 아님, app-chak 레포 측)
- `CLAUDE.md`에 pslog 연동 규칙 추가 (§11.1 참조)
- 초기 `PLAN.md` 작성 (마스터 태스크 목록)
- `handoffs/` 디렉토리 생성 + README.md (사용 가이드)
- Phase 1~4 통합 테스트의 전제 조건. 본 설계가 pslog 단독으로는 작동하지 않음을 명시.

**Phase 1 — 모델/마이그레이션 (pslog)**
- alembic revision: Project/Task 필드 추가, Handoff/GitPushEvent 테이블 신설
- TaskEventAction enum `ALTER TYPE ... ADD VALUE`
- external_id UNIQUE 인덱스 추가
- 기존 데이터 무결성 검증 (`source="manual"` 기본값, 회귀 테스트)

**Phase 2 — Webhook 수신만**
- `/webhooks/github` endpoint
- 서명 검증 (프로젝트별 secret), GitPushEvent INSERT만
- push_event_reaper 부팅 hook
- 처리 로직은 아직 없음 (raw 수신 검증)

**Phase 3 — PLAN/handoff 파서**
- `plan_parser_service`, `handoff_parser_service` 단위 테스트와 함께
- 파일 fetch 없이 텍스트 입력으로 검증 가능
- 들여쓰기 기반 서브 체크박스 분리 검증

**Phase 4 — 동기화 (핵심 가치 전달)**
- `git_repo_service` (GitHub Contents API + Compare API)
- `sync_service` 조립
- 실제 webhook → DB 반영 E2E 동작
- 멱등성/마이그레이션 회귀 테스트 통과 필수

**Phase 5 — UI (설정 페이지)**
- ProjectGitSettings — repo URL, PLAN 경로 입력
- 자동 webhook 등록 (GitHub API 호출, 프로젝트별 secret 자동 생성)
- TaskCard에 source 배지 + handoff 누락 ⚠️
- HandoffHistory 페이지

**Phase 6 — Discord 알림 통합**
- 기존 discord_service 확장: 체크 변경 요약, handoff 누락 경고, 롤백 알림 템플릿

**Phase 7 — Gemma 브리핑 (선택)**
- 진입 전 맥미니에서 Gemma 4 26B MoE 추론 시간 실측
- `ollama_client`, `brief_service` (single-flight lock 포함)
- DailyBriefPanel UI
- 30초 빈번 초과 시 비동기 응답(`202` + 폴링) 패턴 채택
- Phase 7은 핵심이 아니므로 1~6 안정화 후 도입

**Phase 8+ — 후속**
- 에러 로그 + Git 상관관계 (별도 설계서)
- handoff 갱신 강제 lint hook
- raw_content 30일 GC 작업

각 Phase는 독립적으로 머지 가능. 0~4까지가 핵심 가치 전달, 5~6는 사용성, 7은 부가가치.

### 11.1 app-chak `CLAUDE.md`에 추가할 규칙 (Phase 0 산출물)

```markdown
## pslog 연동 규칙

### handoff 파일 갱신 (필수)
1. 작업 시작 시 `handoffs/{현재브랜치}.md` 파일 확인. 없으면 생성.
2. 작업 진행하며 해당 파일의 오늘 날짜 섹션을 갱신.
3. **git push 직전 반드시** 해당 파일에 변경사항을 commit.
4. 마스터 PLAN의 task ID(`task-XXX`)는 들여쓰기 0의 체크박스로, 개인 서브 작업은 들여쓰기 2의 체크박스로 표기.

### PLAN.md 작성
스프린트 시작 시 `PLAN.md`에 마스터 태스크 목록을 작성한다. 형식:
- 체크박스로 시작 (`- [ ]`)
- 태스크 ID는 `[task-NNN]` (프로젝트 내 unique)
- assignee는 `@username`
- 영향 파일은 backtick으로 감싸 명시
- 1 태스크 = 0.5~3일 분량 권장 (너무 작거나 크면 pslog 추적 가치 ↓)

### 강제
- handoff 미갱신 push는 pslog Discord에 ⚠️ 알림이 발송됨
- PR 머지 거부 lint hook은 추후 도입
```

---

## 12. 비범위 (Out of Scope)

다음은 본 설계에서 제외 (필요시 별도 스펙):

- **에러 로그 + Git 상관관계 (기능 #2)**: **별도 설계서 `2026-04-26-error-log-design.md`에서 다룸.** 본 설계의 Phase 4 안정화 후 작성. 본 설계의 `Handoff.commit_sha` / `Task.last_commit_sha`가 그쪽의 join key로 사용될 예정이므로 SHA 인덱스 확실히 유지.
- **pslog에서 PLAN.md 직접 편집 후 git에 commit**: 단방향(읽기) 유지, 양방향 동기화 미고려
- **GitLab/Bitbucket 지원**: GitHub 우선
- **handoff 갱신 강제 lint hook**: 규칙은 CLAUDE.md에 명시 + Discord 가시화로 보강. 자동 강제는 후속.
- **PR/Issue 동기화**: push 이벤트만 사용, PR 코멘트/리뷰는 미사용
- **다중 PLAN.md / 모노레포 부분 동기화**: 단일 PLAN.md만 지원
- **`raw_content` 30일 GC job**: 운영 안정 후 별도 cron 작업으로 추가

---

## 13. Open Questions

향후 구현 단계에서 확정 필요한 항목:

1. **GitHub 인증**: PAT vs GitHub App. App이 멀티 repo 권한 관리 더 깔끔하나 초기 설정 복잡. → Phase 5 진입 시 결정.
2. **로컬 clone 캐시 위치**: 맥미니 디스크의 어느 경로에 pslog가 fetch한 repo를 둘지. → Phase 4. Contents API 우선이라면 불필요할 수도.
3. **Brief API 호출자 인증**: 기존 pslog JWT 재사용 vs 별도 토큰 발급. → Phase 7.
4. **Gemma 4 프롬프트 구조**: 시스템 프롬프트 + few-shot 예제 포맷 → Phase 7 구현 시점에 튜닝.
5. **Fernet 키 회전 절차**: 운영 문서 별도 작성 시점.
6. **Brief single-flight lock 다중 워커 대응**: §7.2 lock은 workers=1 가정 하 in-process 메모리 lock. 다중 워커 전환 시 워커별 lock 분리 → Gemma 중복 호출. 다중 워커 가는 시점에 PostgreSQL advisory lock 또는 Redis로 승격. → 운영 결정 시점.

---

## 14. 결정 사항 요약 (Decision Log)

- 2026-04-26: 리팩토링 방식 채택 (신규 프로젝트 X)
- 2026-04-26: PLAN(마스터 1개) + handoff(브랜치별) 이원 파일 구조
- 2026-04-26: 그래뉼래리티 분리 — 마스터 = 0.5~3일 task, handoff 서브 체크박스 = 분 단위 자유 영역
- 2026-04-26: GitHub Webhook (모든 브랜치)
- 2026-04-26: AI = 합성/브리핑만, 결정적 동작은 파서로
- 2026-04-26: Gemma 4 26B MoE (로컬, llama.cpp) 사용
- 2026-04-26: Task 모델 확장 + Project Git-aware
- 2026-04-26: 단계적 도입 (Phase 0~7+)
- 2026-04-26 (Rev2): **Task 상태는 기존 `status: TaskStatus` 재사용** — `- [x]` ⟷ `DONE`. 별도 `checked_at` 필드 신설 안 함 (이중 진실 회피).
- 2026-04-26 (Rev2): **`TaskEventAction` enum 확장 4개** — `SYNCED_FROM_PLAN`, `CHECKED_BY_COMMIT`, `UNCHECKED_BY_COMMIT`, `ARCHIVED_FROM_PLAN`. JSON 우겨넣기 X.
- 2026-04-26 (Rev2): **`Task.archived_at`** soft-delete — PLAN에서 task 사라져도 hard-delete 안 함.
- 2026-04-26 (Rev2): **`external_id` 프로젝트 내 UNIQUE 제약** — 파서 단계 + DB 인덱스 이중 방어.
- 2026-04-26 (Rev2): **`Project.webhook_secret` 프로젝트별 (Fernet)** — 글로벌 단일 secret 폐기, 격리.
- 2026-04-26 (Rev2): **Background task + 부팅 reaper** — Celery/Redis 추가 없이 내구성 확보.
- 2026-04-26 (Rev2): **handoff 누락은 항상 Discord 경고 + UI ⚠️** — silent 옵션 X.
- 2026-04-26 (Rev2): **app-chak 선행 작업을 Phase 0로 분리** — 본 설계가 별도 repo 변경에 의존함을 명시.
- 2026-04-26 (Rev2): **commit_sha / last_commit_sha = 40자 hex full** — GitHub webhook payload 그대로, short SHA reject. error-log 설계서의 join key 계약. Phase 1 alembic에 `CHECK(commit_sha ~ '^[0-9a-f]{40}$' OR commit_sha IS NULL)` 제약 추가.
- 2026-04-26 (Rev2): **에러 로그 + Git 상관관계는 후속 설계서** — 본 설계 Phase 4 안정화 후 작성.
