# Phase 5 Follow-up B2 — UI Closure + Discord Sync-Failure Alert (Design)

**Status**: Draft → 사용자 검토 후 implementation plan 작성 (`writing-plans`).

**Date**: 2026-05-01

**Goal**: Phase 5b handoff 메모에서 트래킹된 frontend 잔여 2건 + 작은 backend 추가 1건을 한 PR 로 닫음. Phase 6 (Discord 알림 통합 — 3 템플릿 + cooldown) 진입 전, 사용자 가시성 (실패 알림 + 누락 표시) 의 minimal viable layer 를 깔아둠.

**선행**: pslog `main` = `cd53696` (B1 PR #13 머지 직후). backend tests 175 baseline. alembic head = `a1b2c3d4e5f6`. 마이그레이션 추가 없음.

---

## 1. Scope

본 phase 는 3 deliverable:

1. **TaskCard ⚠️ handoff missing 배지** — SYNCED_FROM_PLAN task 의 `last_commit_sha` 가 어떤 Handoff 의 `commit_sha` 에도 매칭 안 될 때 "⚠️ 기록 빠짐" 표시.
2. **GitEventList 모달 + reprocess 호출 site** — 프로젝트별 sync 실패 이벤트 list 모달, 행마다 [재처리] 버튼 (B1 의 `useReprocessEvent` 훅 호출 site 마련).
3. **Discord sync-failure 알림 (minimal)** — `process_event` 의 except 분기 끝에서 `Project.discord_webhook_url` 이 set 이면 fire-and-forget 알림 1건. cooldown 없음, opt-in 토글 없음 (기존 webhook URL 그대로). Phase 6 의 3 템플릿 + cooldown 은 별개 phase.

본 phase 가 **하지 않는** 것:
- 새 마이그레이션 / 모델 컬럼
- error-log spec 진입 (task-automation Phase 4 안정화 grace 기간 적용 중)
- Phase 6 의 체크 변경 / handoff 누락 / 롤백 알림 3종 템플릿 (별도 phase)
- Phase 6 의 cooldown / 알림 disable 정책
- frontend 단위 테스트 인프라 도입 (Phase 5b 메모 그대로 — Vitest 미도입)

---

## 2. Important Contracts

### 2.1. "Handoff missing" 정의

SYNCED_FROM_PLAN task `t` 에 대해, **다음 모두를 만족할 때** `handoff_missing = true`:

- `t.source = 'SYNCED_FROM_PLAN'`
- `t.last_commit_sha IS NOT NULL`
- `t.archived_at IS NULL` (archived 는 표시 의미 없음 — UI 가 어차피 안 보임)
- `NOT EXISTS (SELECT 1 FROM handoffs h WHERE h.project_id = t.project_id AND h.commit_sha = t.last_commit_sha)`

위 4 조건 중 하나라도 false 면 `handoff_missing = false`. MANUAL task 는 항상 `false`.

**의도**: "이 task 가 마지막으로 변경된 commit 에 대한 handoff 가 없다 = 작업 기록이 빠졌다." 사용자 직관 1줄 매칭.

### 2.2. Backend 응답 변경

`TaskResponse` (`backend/app/schemas/task.py`) 에 1 필드 추가:

```python
handoff_missing: bool = False
```

값 계산 위치: `task_service` 에서 Task 조회/리스트 시 EXISTS 서브쿼리 또는 LEFT JOIN 으로 inline 계산. 50건 list 에 대해 1 query (N+1 회피).

권장 SQL 모양:

```sql
SELECT
  t.*,
  EXISTS (
    SELECT 1 FROM handoffs h
    WHERE h.project_id = t.project_id
      AND h.commit_sha = t.last_commit_sha
  ) AS has_handoff
FROM tasks t
WHERE t.project_id = :project_id
  AND t.archived_at IS NULL
ORDER BY ...
```

frontend 노출 시 `handoff_missing = (NOT has_handoff) AND last_commit_sha IS NOT NULL AND source = SYNCED_FROM_PLAN AND archived_at IS NULL`.

조회 위치 (task_service 내부):

- `list_tasks(project_id)` — 카드 list 노출용
- `get_task(task_id)` — 상세 노출용 (필요 시)
- `update_task` 의 응답 — 변경 후 일관 노출

### 2.3. 신규 endpoint — `GET /api/v1/projects/{id}/git-events`

**용도**: 프로젝트의 sync 실패 이벤트 list. v1 은 `failed_only=true` 만 의미 있음.

**요청**:
- `failed_only: bool = True` (기본값 — `processed_at IS NOT NULL AND error IS NOT NULL`)
- `limit: int = 50` (clamp 1~200, handoffs endpoint 와 동일 패턴)

**응답**: `list[GitEventSummary]`

```python
class GitEventSummary(BaseModel):
    id: UUID
    branch: str
    head_commit_sha: str
    pusher: str
    received_at: datetime
    processed_at: datetime | None  # failed event 면 not null
    error: str | None              # failed_only=true 면 not null
```

`commits` JSON / `before_commit_sha` 는 응답 미포함 (UI 불필요, payload 작게).

정렬: `received_at DESC`. 같은 시각 충돌 시 `id` 보조 정렬 (deterministic).

**권한**: 프로젝트 멤버 누구나 (read). 비-멤버는 404 (handoffs endpoint 와 동일).

**reprocess 자체**는 기존 endpoint (`POST /git-events/{event_id}/reprocess`) — Phase 5a + B1 그대로. 본 phase 는 list endpoint 만 신규.

### 2.4. Discord sync-failure 알림

**위치**: `backend/app/services/sync_service.py` `process_event` 함수의 except 분기. 기존 commit (event.error 기록) 직후, autoflush 복원 직후.

**조건 트리**:

1. except 분기 진입 (= sync 실패 확정)
2. `project.discord_webhook_url` 가 not None
3. event commit 성공

**메시지 포맷** (Discord 텍스트, 마크다운):

```
⚠️ **pslog sync 실패** — {project.name}
branch: `{event.branch}`
commit: `{event.head_commit_sha[:7]}`
error: ```{error_msg[:500]}```
```

`error_msg` = 기존 `f"{type(exc).__name__}: {exc}"` 그대로 재사용 (already 변수). 500자 trim — Discord 메시지 길이 제한 회피.

**호출**: `await discord_service.send_webhook(content, project.discord_webhook_url)`. 기존 primitive 그대로 (주간 리포트와 같은 함수). `try / except` wrapping — 알림 실패가 메인 처리에 영향 안 가도록 `logger.exception` 후 swallow.

**Cooldown / 중복 방지 (v1)**:
- 자연스러운 1회: except 분기는 한 event 당 1회 진입 (process_event idempotency + B1 의 FOR UPDATE 가드 보장).
- reprocess 후 또 실패 → 다시 1 알림 (의도된 동작 — 재시도 결과를 사용자가 알아야 함).
- burst 실패 (예: GitHub 인증 만료로 10 webhook 연속 실패) → 10 알림. v1 한계. Phase 6 cooldown 에서 닫음.

**테스트 시점에서의 환경 의존성**:
- 알림은 외부 HTTP — 테스트는 `monkeypatch.setattr(discord_service, "send_webhook", fake)` 로 mock.
- `discord_webhook_url IS NULL` case 는 호출 자체 안 함 (assertion).

---

## 3. Frontend Architecture

### 3.1. 타입 / API service

**`frontend/src/types/task.ts`** — 기존 `Task` 인터페이스 확장:

```typescript
export interface Task {
  // ... 기존 필드
  handoff_missing: boolean;
}
```

**`frontend/src/types/git.ts`** — 신규 인터페이스 추가:

```typescript
export interface GitEventSummary {
  id: string;            // UUID
  branch: string;
  head_commit_sha: string;
  pusher: string;
  received_at: string;   // ISO 8601
  processed_at: string | null;
  error: string | null;
}
```

**`frontend/src/services/api.ts`** — `git` 그룹에 method 1개 추가:

```typescript
git: {
  // ... 기존 5 method (Phase 5b)
  listGitEvents: async (
    projectId: string,
    opts: { failedOnly?: boolean; limit?: number } = {},
  ): Promise<GitEventSummary[]> => {
    const params = new URLSearchParams();
    if (opts.failedOnly !== undefined) params.set('failed_only', String(opts.failedOnly));
    if (opts.limit !== undefined) params.set('limit', String(opts.limit));
    const res = await axios.get(`/api/v1/projects/${projectId}/git-events?${params}`);
    return res.data;
  },
}
```

### 3.2. TanStack Query 훅 (`hooks/useGithubSettings.ts` 확장)

신규 훅 1개 + 기존 훅 1개 갱신:

```typescript
// 신규
export function useFailedGitEvents(projectId: string | undefined) {
  return useQuery({
    queryKey: ['git-events', projectId, 'failed'],
    queryFn: () => api.git.listGitEvents(projectId!, { failedOnly: true, limit: 50 }),
    enabled: !!projectId,
    staleTime: 30_000,  // 30초 — 발견성 vs 부하 균형
  });
}

// 기존 useReprocessEvent 의 onSuccess 에 invalidate 추가
const queryClient = useQueryClient();
return useMutation({
  mutationFn: ({ projectId, eventId }) => api.git.reprocessEvent(projectId, eventId),
  onSuccess: (_, { projectId }) => {
    queryClient.invalidateQueries({ queryKey: ['git-events', projectId, 'failed'] });
    // 기존 onSuccess 콜백 (handoffs invalidate 등) 그대로 유지
  },
});
```

### 3.3. `TaskCard.tsx` — ⚠️ 배지 추가

기존 `SYNCED_FROM_PLAN` 파란 배지 영역에 조건부 ⚠️ 배지 1개 추가:

```tsx
{task.handoff_missing && (
  <span
    className="ml-1 text-yellow-500"
    title="이 commit 의 handoff 기록이 없습니다"
  >
    ⚠️
  </span>
)}
```

위치: source 배지 (`SYNCED_FROM_PLAN`) 옆 인라인. 색상은 노란/주황 — 빨강은 status 변화에만 사용 (codebase 컨벤션 추측). tooltip 으로 의미 전달.

archived task 는 카드 자체가 안 보이므로 추가 가드 불필요. backend 가 archived = false 처리도 보냄.

### 3.4. `GitEventListModal.tsx` 신규

위치: `frontend/src/components/GitEventListModal.tsx`. Phase 5b `HandoffHistoryModal.tsx` 의 패턴 매칭.

**Props**:

```typescript
interface GitEventListModalProps {
  projectId: string;
  projectName: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}
```

**구조**:

- shadcn `Dialog` + `DialogContent` + `DialogHeader` + `DialogTitle`
- 헤더: `Sync 실패 이벤트 — {projectName}`
- `useFailedGitEvents(projectId)` 호출 — loading / empty / error / data 4 state 처리
- empty: "실패한 sync 이벤트가 없습니다." + 닫기 버튼
- data: shadcn `Table`. 컬럼:
  - 시각 (received_at, `MM/dd HH:mm`)
  - branch
  - short SHA (first 7)
  - error (first line, 50자 truncate, hover tooltip 으로 전체)
  - [재처리] 버튼 (`useReprocessEvent`)
- 재처리 버튼: `mutation.isPending` 일 때 disable + spinner. onSuccess → toast "재처리 큐 등록" + 자동 refetch (위 invalidateQueries). onError 분기:
  - 409 → toast "처리 중 — 잠시 후 다시 시도하세요"
  - 400 → toast "이미 성공적으로 처리됨"
  - 기타 → toast `error.response.data.detail || error.message`

**styling**: Phase 5b modal 패턴 그대로. lint 회피 위해 outer/inner split 필요 시 `GitEventList` (inner) + `GitEventListModal` (outer) 로 분리 — 단, useEffect 가 없으면 split 안 해도 됨.

### 3.5. `ProjectItem.tsx` — count badge + 메뉴 항목

**호출**:
- `useFailedGitEvents(projectId)` 컴포넌트 mount 시 호출. data 의 `length` = 카운트.

**dropdown trigger 빨간 점**:
- `data?.length > 0` 일 때 `···` 옆 작은 점 (`<span className="ml-1 inline-block w-1.5 h-1.5 rounded-full bg-red-500" />`) 또는 trigger 자체에 미세한 표시. 추천: trigger 자체가 작아 점 추가가 시각 노이즈 → **dropdown 항목 안에서만 카운트** + trigger 옆 점 1개 (둘 중 하나 선택 — 시각 검증 시 결정).

**dropdown 메뉴 항목** (OWNER 만 노출 — 기존 패턴 `{isOwner && ...}` 그대로):

```tsx
{isOwner && failedEvents && failedEvents.length > 0 && (
  <DropdownMenuItem onClick={() => setGitEventModalOpen(true)}>
    <span className="text-yellow-500">⚠️</span>
    <span className="ml-2">Sync 실패 ({failedEvents.length})</span>
  </DropdownMenuItem>
)}
```

`failedEvents.length === 0` 이면 메뉴 항목 자체 숨김 — 평소엔 dropdown 가벼움.

**기타**:
- 이미 mounted 된 `useFailedGitEvents` 의 staleTime 30초 → 사용자가 다른 페이지 다녀와도 합리적 fresh.
- modal `open={gitEventModalOpen}` state 는 `ProjectItem` 의 useState 로 관리 (기존 GitSettings/Handoff 모달과 같은 패턴).

### 3.6. shadcn / 스타일 / 린트

- `bun run build` clean 유지
- `bun run lint` — 신규 위배 추가 안 함. Phase 5b 의 `react-hooks/set-state-in-effect` 패턴 발생하면 outer/inner split.
- `console.log` 금지, `any` 금지 (CLAUDE.md).
- 인라인 스타일 금지 — Tailwind utility 만.

### 3.7. 시각 검증

- frontend 단위 테스트 미도입 (Phase 5b 메모 그대로). build/lint 가 회귀 게이트.
- PR 머지 전 사용자 dev server (`make backend` + `make frontend`) 직접 검증:
  - TaskCard ⚠️ 배지 표시/미표시 양쪽
  - dropdown 의 "Sync 실패 (N)" 항목 노출 (실패 0건 / 1+ 양쪽)
  - 모달 빈/데이터 상태, 재처리 버튼 동작 (mutation pending 표시 + onSuccess 토스트)
- e2e: 의도적으로 git_repo_url 잘못된 프로젝트로 push → sync 실패 → Discord 알림 + 모달에 row 등장 → reprocess 버튼.

---

## 4. Backend Architecture

### 4.1. 변경 파일

**수정**:
- `backend/app/schemas/task.py` — `TaskResponse` 에 `handoff_missing: bool` 추가
- `backend/app/schemas/git_event.py` — 신규 `GitEventSummary` Pydantic (또는 기존 `git_settings.py` 에 추가 — 더 좁은 범위)
- `backend/app/services/task_service.py` — `list_tasks` / `get_task` 의 SELECT 에 EXISTS 서브쿼리 추가 + 매핑
- `backend/app/api/v1/endpoints/git_settings.py` — `GET /git-events` 핸들러 추가
- `backend/app/services/sync_service.py` — except 분기에 Discord 알림 호출

**테스트 추가**:
- `backend/tests/test_task_service.py` — `handoff_missing` 계산 회귀 (handoff 있/없 + MANUAL/SYNCED + commit_sha NULL + archived 케이스)
- `backend/tests/test_git_settings_endpoint.py` — `GET /git-events` 멤버 권한 / failed_only 필터 / limit clamp / 빈 리스트 / 비-멤버 404
- `backend/tests/test_sync_service.py` — Discord alert 호출 검증 (success path 안 호출 / failure path 호출 / `discord_webhook_url` NULL 시 skip)

### 4.2. 마이그레이션

**없음**. 모든 기존 컬럼만 사용 (`Task.last_commit_sha`, `Task.archived_at`, `Task.source`, `Handoff.project_id`, `Handoff.commit_sha`, `Project.discord_webhook_url`).

### 4.3. SQL 성능 / 인덱스

`EXISTS` 서브쿼리는 `handoffs` 의 기존 인덱스 활용:
- Phase 1 의 `Handoff` UNIQUE `(project_id, commit_sha)` (Phase 1 모델 정의 시 추가됨, 마이그레이션 `c4dee7f06004`)

이 unique index 가 EXISTS 의 평가에 그대로 사용 — 추가 인덱스 불필요.

list 50 task → EXISTS 50회 → 각각 unique index lookup (O(log N)). 성능 영향 무시 가능.

### 4.4. 에러 정책

- `GET /git-events` 비-멤버: 404 (기존 패턴)
- Discord 알림 실패: silent — `logger.exception` 후 swallow. 메인 sync 실패 처리에 영향 없음.
- `discord_webhook_url` 가 잘못된 URL (Discord 측 4xx) 도 silent. Phase 6 에서 cooldown / disable 검토.

---

## 5. Test Plan

### 5.1. Backend 신규 (예상 +9 tests)

**task_service `handoff_missing` 계산** (3건):
- SYNCED + last_commit_sha set + handoff 존재 → `handoff_missing = false`
- SYNCED + last_commit_sha set + handoff 없음 → `handoff_missing = true`
- MANUAL → 항상 `false` / last_commit_sha NULL → `false` / archived → `false` (parametrize 또는 한 함수에서 3 assert)

**git-events endpoint** (3건):
- failed only filter + 빈 리스트
- 멤버 권한 + 비-멤버 404
- limit clamp (>200 → 200)

**sync_service Discord alert** (3건):
- success path → send_webhook 호출 안 함
- failure path + discord_webhook_url set → 호출 1회
- failure path + discord_webhook_url NULL → 호출 안 함

베이스라인 175 → **약 184 passing**. 위 3건 (task_service `handoff_missing`) 의 마지막을 한 함수로 합치면 7~8 — plan 단계에서 결정.

### 5.2. Frontend

- `bun run build` clean (TypeScript)
- `bun run lint` 신규 위배 0
- 시각 검증 (사용자 dev server 직접): TaskCard ⚠️ / dropdown count badge / 모달 4 state / 재처리 onSuccess 토스트.

### 5.3. e2e (사용자, PR 머지 전)

- pslog dev server + ngrok/cloudflared 로 webhook 받기
- 의도적 실패 시나리오 (잘못된 PAT 또는 access 끊긴 repo) → Discord 알림 도착 확인 → pslog 모달 진입 → reprocess 버튼 → 토스트 확인

---

## 6. Decision Log

- **A (handoff missing 의미) 채택** vs B/C — "기록 빠짐" 사용자 직관 1줄 매칭, backend SQL 1줄 EXISTS, frontend 1줄 조건부 렌더링. B (status 변화에도 unchanged) 는 새 task 의 ⚠️ 노이즈 회피하지만 의미가 좁아 사용자 설명 어려움. C (handoff 0건) 는 의미가 더 넓어 거짓 양성.
- **A (failed only 모달) 채택** vs B/C — 발견성/단순성 우선. push 이벤트 list view 는 Phase 6 또는 별도 admin tool. handoff 이력은 이미 별 모달.
- **B (count badge — 메뉴 항목 노출 / 0건 시 숨김) 채택** vs A/C — 평소 dropdown 가벼움 + 발생 시 가시성. trigger 옆 빨간 점은 시각 검증 시 결정 (이번 phase 의 nice-to-have).
- **Discord 알림 minimal 포함 (B2)** vs Phase 6 분리 — primitive (`send_webhook`) + 컬럼 (`discord_webhook_url`) 이미 존재, except 분기 ~10줄. 발견성 강 보강. Phase 6 의 3 템플릿 + cooldown 은 별개 phase 라는 합의.
- **Cooldown 미적용 (v1)** — 자연스러운 1회 (event 당 except 1회) 로 충분. burst 시나리오 (1일 100+ 실패) 는 v1 한계, Phase 6 에서 닫음.

---

## 7. Phase 6 와의 분리 (참고)

본 phase 가 Phase 6 의 일부 (`Project.discord_webhook_url` 사용 + sync 실패 알림) 를 미리 일부 닫지만, Phase 6 의 핵심은 그대로 남음:

- **체크 변경 알림 템플릿**: PLAN 의 `[ ]` → `[x]` 변화를 사용자별로 요약
- **handoff 누락 경고 템플릿**: 일정 시간 경과 후 handoff 없으면 알림
- **롤백 알림 템플릿**: PLAN 에서 task 가 `[x]` → `[ ]` 회귀하면 알림
- **cooldown 정책**: 같은 종류 알림 burst 차단 (3회 연속 실패 → disable)
- **알림 종류별 on/off** (선택)

본 phase 는 **failure mode 1종** 만 다룸. Phase 6 는 **success-flow 알림 3종 + 정책** 추가.

---

## 8. Open Questions

본 phase 진입 전 답할 것 없음. 시각 검증 시점에 결정할 작은 UI 디테일 2건:

1. dropdown trigger 옆 빨간 점 (visibility 강화) 추가 여부 — 사용자 검증 시 보고 결정
2. ⚠️ 배지 색상 (yellow vs orange vs red) — 기존 codebase status 색상 컨벤션과 충돌 안 나게

둘 다 plan 단계에서 결정 안 하고 implementation 또는 시각 검증 시점에 결정.
