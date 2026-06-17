# Error-log Phase 5 Frontend — Errors UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** pslog 화면에서 에러 목록 + 상세 + git 컨텍스트를 보고 status 전이 (resolve/ignore/reopen/unmute) 액션을 할 수 있게 하는 frontend UI. 사용자가 pslog 켜서 "오늘 무슨 에러 났지" 보는 그 분기점.

**Architecture:** Phase 5 Backend (PR #20) 의 endpoint 4개 (`GET /errors`, `GET /errors/{id}`, `GET /logs`, `PATCH /errors/{id}`) 그대로 사용. DashboardPage 의 viewMode 토글 ('board' | 'table' | 'week') 에 'errors' 추가, list ↔ detail 은 `selectedErrorGroupId` state 로 전환 (URL 라우팅은 v2). 기존 neobrutalism 스타일 (border-2 black + 빨간 그림자) 유지.

**Tech Stack:** React 19, TypeScript 5+, Vite, Tailwind CSS, shadcn/ui, TanStack Query (서버 상태), Zustand (UI 상태), Axios. **frontend 테스트 러너 없음** — TypeScript type check + ESLint + 수동 smoke test.

**Scope OUT (v2 또는 다른 sub-phase)**:
- LogsPage / LogSearchBox (Frontend Logs sub-phase)
- LogTokensPage / LogHealthBadge (Frontend Ops sub-phase)
- ErrorTrendChart (시간별 발생 빈도 — backend aggregate endpoint 미구현, YAGNI)
- URL 라우팅 (deep-link 가능한 /errors/:groupId — v2)

---

## File Structure

| File | 역할 | new/modify |
|---|---|---|
| `frontend/src/types/error.ts` | ErrorGroupSummary / ErrorGroupDetail / GitContext / 응답 타입 | **new** |
| `frontend/src/types/log.ts` | LogEventSummary | **new** |
| `frontend/src/types/index.ts` | error/log re-export | modify |
| `frontend/src/services/api.ts` | `api.errors` / `api.logs` namespace 추가 | modify |
| `frontend/src/hooks/useErrorGroups.ts` | useErrorGroups / useErrorGroupDetail / useTransitionStatus | **new** |
| `frontend/src/components/errors/LogLevelBadge.tsx` | level (INFO/WARNING/ERROR/...) 배지 | **new** |
| `frontend/src/components/errors/StackTraceViewer.tsx` | 접고-펴는 스택 trace 뷰어 (`<details>`) | **new** |
| `frontend/src/components/errors/GitContextPanel.tsx` | first_seen 의 handoffs/tasks/push event + previous_good_sha | **new** |
| `frontend/src/components/errors/ErrorDetail.tsx` | 헤더 + recent events + GitContextPanel + 액션 버튼 (resolve/ignore/reopen/unmute) | **new** |
| `frontend/src/components/errors/ErrorsList.tsx` | status 필터 + 목록 + 클릭 → onSelectGroup | **new** |
| `frontend/src/pages/DashboardPage.tsx` | viewMode 에 'errors' 추가 + selectedErrorGroupId state + 토글 버튼 | modify |

순효과: ~+900 LOC frontend. 백엔드 변경 없음. 마이그레이션 없음.

---

## Task 1: Types + Service layer

**Files:**
- Create: `frontend/src/types/error.ts`
- Create: `frontend/src/types/log.ts`
- Modify: `frontend/src/types/index.ts`
- Modify: `frontend/src/services/api.ts`

- [ ] **Step 1: error.ts 작성**

`frontend/src/types/error.ts` 생성:

```typescript
// ErrorGroup status 전이 액션 (PATCH 요청 body)
export type ErrorGroupAction = 'resolve' | 'ignore' | 'reopen' | 'unmute';

// 백엔드 ErrorGroupStatus enum 의 wire value
export type ErrorGroupStatus = 'open' | 'resolved' | 'ignored' | 'regressed';

export interface ErrorGroupSummary {
  id: string;
  fingerprint: string;
  exception_class: string;
  exception_message_sample: string | null;
  event_count: number;
  status: ErrorGroupStatus;
  first_seen_at: string;          // ISO datetime
  first_seen_version_sha: string;
  last_seen_at: string;
  last_seen_version_sha: string;
  // Phase 5 Backend Task 3 fix #7 — audit 필드
  resolved_at: string | null;
  resolved_by_user_id: string | null;
  resolved_in_version_sha: string | null;
}

export interface ErrorGroupListResponse {
  items: ErrorGroupSummary[];
  total: number;
}

// GET /errors/{id} 응답 — git 컨텍스트 nested
export interface HandoffRef {
  id: string;
  commit_sha: string;
  branch: string;
  author_git_login: string;
  pushed_at: string;
}

export interface TaskRef {
  id: string;
  external_id: string | null;
  title: string;
  status: string;
  last_commit_sha: string | null;
  archived_at: string | null;     // null 아니면 archived 배지
}

export interface GitPushEventRef {
  id: string;
  head_commit_sha: string;
  branch: string;
  pusher: string;
  received_at: string;
}

export interface GitContextBundle {
  handoffs: HandoffRef[];
  tasks: TaskRef[];
  git_push_event: GitPushEventRef | null;
}

export interface GitContextWrapper {
  first_seen: GitContextBundle;
  previous_good_sha: string | null;
}

export interface ErrorGroupDetail {
  group: ErrorGroupSummary;
  recent_events: import('./log').LogEventSummary[];
  git_context: GitContextWrapper;
}

// PATCH /errors/{id} 요청 body
export interface ErrorGroupStatusUpdateRequest {
  action: ErrorGroupAction;
  resolved_in_version_sha?: string | null;
}
```

- [ ] **Step 2: log.ts 작성**

`frontend/src/types/log.ts` 생성:

```typescript
// 백엔드 LogLevel enum 의 wire value
export type LogLevel = 'DEBUG' | 'INFO' | 'WARNING' | 'ERROR' | 'CRITICAL';

export interface LogEventSummary {
  id: string;
  level: LogLevel;
  message: string;
  logger_name: string;
  version_sha: string;
  environment: string;
  hostname: string;
  emitted_at: string;
  received_at: string;
  fingerprint: string | null;
  exception_class: string | null;
  exception_message: string | null;
}

export interface LogEventListResponse {
  items: LogEventSummary[];
  total: number;
}
```

- [ ] **Step 3: types/index.ts re-export 추가**

`frontend/src/types/index.ts` 의 끝에 append (다른 export 들 옆):

```typescript
export * from './error';
export * from './log';
```

- [ ] **Step 4: api.ts 의 errors / logs namespace 추가**

`frontend/src/services/api.ts` 의 import 영역에 추가 (다른 type imports 옆):

```typescript
import type {
  // ... 기존 imports
  ErrorGroupListResponse,
  ErrorGroupDetail,
  ErrorGroupStatus,
  ErrorGroupStatusUpdateRequest,
  ErrorGroupSummary,
  LogEventListResponse,
  LogLevel,
} from '@/types';
```

`api` object 안 `tasks: { ... }` 다음에 namespace 2개 append (tasks 와 같은 들여쓰기):

```typescript
  errors: {
    list: (
      projectId: string,
      params?: { status?: ErrorGroupStatus; since?: string; offset?: number; limit?: number },
    ) =>
      apiClient.get<ErrorGroupListResponse>(`/projects/${projectId}/errors`, { params }),
    get: (projectId: string, groupId: string) =>
      apiClient.get<ErrorGroupDetail>(`/projects/${projectId}/errors/${groupId}`),
    transition: (
      projectId: string,
      groupId: string,
      data: ErrorGroupStatusUpdateRequest,
    ) =>
      apiClient.patch<ErrorGroupSummary>(
        `/projects/${projectId}/errors/${groupId}`,
        data,
      ),
  },

  logs: {
    list: (
      projectId: string,
      params?: { level?: LogLevel; since?: string; q?: string; offset?: number; limit?: number },
    ) =>
      apiClient.get<LogEventListResponse>(`/projects/${projectId}/logs`, { params }),
  },
```

- [ ] **Step 5: TypeScript build + lint 확인**

```bash
cd frontend && bun run build 2>&1 | tail -10
```
Expected: `✓ built` 또는 동등 — type errors 0.

```bash
cd frontend && bun run lint 2>&1 | tail -10
```
Expected: 깨끗 (warning 0 또는 기존 baseline 유지).

- [ ] **Step 6: Commit**

```bash
git add frontend/src/types/error.ts frontend/src/types/log.ts frontend/src/types/index.ts frontend/src/services/api.ts
git commit -m "feat(error-log/phase5-frontend-errors): types + api namespace (Task 1)"
```

---

## Task 2: useErrorGroups hooks

**Files:**
- Create: `frontend/src/hooks/useErrorGroups.ts`

- [ ] **Step 1: 훅 파일 작성**

`frontend/src/hooks/useErrorGroups.ts` 생성:

```typescript
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '@/services/api';
import type {
  ErrorGroupAction,
  ErrorGroupStatus,
} from '@/types/error';

interface ListFilters {
  status?: ErrorGroupStatus;
  since?: string;
  offset?: number;
  limit?: number;
}

export function useErrorGroups(projectId: string | null, filters?: ListFilters) {
  return useQuery({
    queryKey: ['projects', projectId, 'errors', filters],
    queryFn: () => api.errors.list(projectId!, filters).then((r) => r.data),
    enabled: !!projectId,
  });
}

export function useErrorGroupDetail(projectId: string | null, groupId: string | null) {
  return useQuery({
    queryKey: ['projects', projectId, 'errors', groupId, 'detail'],
    queryFn: () => api.errors.get(projectId!, groupId!).then((r) => r.data),
    enabled: !!projectId && !!groupId,
  });
}

export function useTransitionErrorStatus(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      groupId,
      action,
      resolved_in_version_sha,
    }: {
      groupId: string;
      action: ErrorGroupAction;
      resolved_in_version_sha?: string | null;
    }) =>
      api.errors.transition(projectId, groupId, {
        action,
        resolved_in_version_sha,
      }),
    onSuccess: () => {
      // list + detail 둘 다 invalidate (audit 필드 + status 변경 반영).
      queryClient.invalidateQueries({
        queryKey: ['projects', projectId, 'errors'],
      });
    },
  });
}
```

- [ ] **Step 2: build + lint 확인**

```bash
cd frontend && bun run build 2>&1 | tail -5
```
Expected: type errors 0.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/hooks/useErrorGroups.ts
git commit -m "feat(error-log/phase5-frontend-errors): useErrorGroups hooks (Task 2)"
```

---

## Task 3: 작은 reusable 컴포넌트 — LogLevelBadge + StackTraceViewer

**Files:**
- Create: `frontend/src/components/errors/LogLevelBadge.tsx`
- Create: `frontend/src/components/errors/StackTraceViewer.tsx`

- [ ] **Step 1: LogLevelBadge 작성**

`frontend/src/components/errors/LogLevelBadge.tsx` 생성:

```typescript
import type { LogLevel } from '@/types/log';

interface LogLevelBadgeProps {
  level: LogLevel;
  className?: string;
}

const LEVEL_STYLES: Record<LogLevel, string> = {
  DEBUG: 'bg-gray-100 text-gray-700 border-gray-400',
  INFO: 'bg-blue-100 text-blue-800 border-blue-400',
  WARNING: 'bg-yellow-100 text-yellow-800 border-yellow-500',
  ERROR: 'bg-red-100 text-red-800 border-red-500',
  CRITICAL: 'bg-purple-100 text-purple-800 border-purple-500',
};

export function LogLevelBadge({ level, className = '' }: LogLevelBadgeProps) {
  const style = LEVEL_STYLES[level];
  return (
    <span
      className={`inline-block px-1.5 py-0.5 text-[10px] font-bold uppercase border-2 rounded ${style} ${className}`}
    >
      {level}
    </span>
  );
}
```

- [ ] **Step 2: StackTraceViewer 작성**

`frontend/src/components/errors/StackTraceViewer.tsx` 생성:

```typescript
interface StackTraceViewerProps {
  trace: string | null;
  defaultOpen?: boolean;
}

export function StackTraceViewer({ trace, defaultOpen = false }: StackTraceViewerProps) {
  if (!trace) {
    return <p className="text-xs text-muted-foreground italic">스택 trace 없음</p>;
  }
  return (
    <details
      className="border-2 border-black/20 rounded bg-gray-50"
      open={defaultOpen}
    >
      <summary className="cursor-pointer px-2 py-1 text-xs font-bold hover:bg-gray-100 select-none">
        스택 trace 보기 ({trace.split('\n').length} 줄)
      </summary>
      <pre className="px-2 py-2 text-[11px] leading-snug font-mono whitespace-pre-wrap break-words overflow-x-auto max-h-96 overflow-y-auto">
        {trace}
      </pre>
    </details>
  );
}
```

- [ ] **Step 3: build + lint 확인**

```bash
cd frontend && bun run build 2>&1 | tail -5
```
Expected: type errors 0.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/errors/LogLevelBadge.tsx frontend/src/components/errors/StackTraceViewer.tsx
git commit -m "feat(error-log/phase5-frontend-errors): LogLevelBadge + StackTraceViewer (Task 3)"
```

---

## Task 4: GitContextPanel

**Files:**
- Create: `frontend/src/components/errors/GitContextPanel.tsx`

- [ ] **Step 1: 컴포넌트 작성**

`frontend/src/components/errors/GitContextPanel.tsx` 생성:

```typescript
import type { GitContextWrapper } from '@/types/error';

interface GitContextPanelProps {
  context: GitContextWrapper;
  firstSeenSha: string;
}

const SHORT_SHA = (sha: string) => sha.slice(0, 8);

function ShaBadge({ sha, label }: { sha: string; label?: string }) {
  if (sha === 'unknown') {
    return (
      <span className="inline-block px-1.5 py-0.5 text-[10px] font-mono border-2 border-yellow-500 bg-yellow-100 text-yellow-800 rounded">
        unknown {label ? `(${label})` : ''}
      </span>
    );
  }
  return (
    <code
      className="inline-block px-1.5 py-0.5 text-[10px] font-mono border-2 border-black/20 bg-white rounded"
      title={sha}
    >
      {SHORT_SHA(sha)}
    </code>
  );
}

export function GitContextPanel({ context, firstSeenSha }: GitContextPanelProps) {
  const { first_seen, previous_good_sha } = context;
  const hasAny =
    first_seen.handoffs.length > 0 ||
    first_seen.tasks.length > 0 ||
    first_seen.git_push_event !== null;

  return (
    <section className="border-2 border-black bg-white p-3 sm:p-4 shadow-[2px_2px_0px_0px_rgba(244,0,4,1)] rounded">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm sm:text-base font-black">Git 컨텍스트</h3>
        <ShaBadge sha={firstSeenSha} label="first seen" />
      </div>

      {!hasAny && (
        <p className="text-xs text-muted-foreground italic mb-3">
          이 SHA 에 대응되는 git 동기화 데이터가 없습니다.
        </p>
      )}

      {first_seen.git_push_event && (
        <div className="mb-3">
          <p className="text-[10px] text-muted-foreground font-bold mb-1">PUSH 이벤트</p>
          <div className="border border-black/20 rounded p-2 bg-gray-50 text-xs">
            <div className="flex flex-wrap items-center gap-2">
              <ShaBadge sha={first_seen.git_push_event.head_commit_sha} />
              <span className="text-muted-foreground">on</span>
              <code className="text-[11px] font-mono">{first_seen.git_push_event.branch}</code>
              <span className="text-muted-foreground">by</span>
              <span className="font-medium">{first_seen.git_push_event.pusher}</span>
            </div>
            <p className="text-[10px] text-muted-foreground mt-1">
              {new Date(first_seen.git_push_event.received_at).toLocaleString()}
            </p>
          </div>
        </div>
      )}

      {first_seen.handoffs.length > 0 && (
        <div className="mb-3">
          <p className="text-[10px] text-muted-foreground font-bold mb-1">
            HANDOFF ({first_seen.handoffs.length})
          </p>
          <ul className="space-y-1">
            {first_seen.handoffs.map((h) => (
              <li key={h.id} className="border border-black/20 rounded p-2 bg-gray-50 text-xs">
                <div className="flex flex-wrap items-center gap-2">
                  <ShaBadge sha={h.commit_sha} />
                  <code className="text-[11px] font-mono">{h.branch}</code>
                  <span className="font-medium">{h.author_git_login}</span>
                </div>
                <p className="text-[10px] text-muted-foreground mt-1">
                  {new Date(h.pushed_at).toLocaleString()}
                </p>
              </li>
            ))}
          </ul>
        </div>
      )}

      {first_seen.tasks.length > 0 && (
        <div className="mb-3">
          <p className="text-[10px] text-muted-foreground font-bold mb-1">
            TASK ({first_seen.tasks.length})
          </p>
          <ul className="space-y-1">
            {first_seen.tasks.map((t) => (
              <li key={t.id} className="border border-black/20 rounded p-2 bg-gray-50 text-xs">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="font-bold">{t.title}</span>
                  {t.archived_at && (
                    <span className="text-[10px] px-1 py-0.5 rounded bg-gray-200 text-gray-700 border border-gray-400">
                      archived
                    </span>
                  )}
                </div>
                <p className="text-[10px] text-muted-foreground mt-1">
                  {t.external_id ? `${t.external_id} · ` : ''}status: {t.status}
                  {t.last_commit_sha ? ` · ` : ''}
                  {t.last_commit_sha && <ShaBadge sha={t.last_commit_sha} />}
                </p>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="mt-3 pt-2 border-t border-black/10">
        <p className="text-[10px] text-muted-foreground font-bold mb-1">직전 정상 SHA</p>
        {previous_good_sha ? (
          <ShaBadge sha={previous_good_sha} label="last clean" />
        ) : (
          <span className="text-xs text-muted-foreground italic">
            없음 (이 fingerprint 의 첫 발생 이전 정상 이벤트 미존재)
          </span>
        )}
      </div>
    </section>
  );
}
```

- [ ] **Step 2: build + lint 확인**

```bash
cd frontend && bun run build 2>&1 | tail -5
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/errors/GitContextPanel.tsx
git commit -m "feat(error-log/phase5-frontend-errors): GitContextPanel — first_seen + previous_good_sha (Task 4)"
```

---

## Task 5: ErrorDetail (액션 버튼 포함)

**Files:**
- Create: `frontend/src/components/errors/ErrorDetail.tsx`

- [ ] **Step 1: ErrorDetail 작성**

`frontend/src/components/errors/ErrorDetail.tsx` 생성:

```typescript
import { useErrorGroupDetail, useTransitionErrorStatus } from '@/hooks/useErrorGroups';
import type { ErrorGroupAction, ErrorGroupStatus } from '@/types/error';
import { Button } from '@/components/ui/button';
import { GitContextPanel } from './GitContextPanel';
import { LogLevelBadge } from './LogLevelBadge';
import { StackTraceViewer } from './StackTraceViewer';

interface ErrorDetailProps {
  projectId: string;
  groupId: string;
  isOwner: boolean;
  defaultResolveSha: string | null;  // 프로젝트의 last_synced_commit_sha
  onBack: () => void;
}

const STATUS_STYLES: Record<ErrorGroupStatus, string> = {
  open: 'bg-red-100 text-red-800 border-red-500',
  resolved: 'bg-green-100 text-green-800 border-green-500',
  ignored: 'bg-gray-100 text-gray-700 border-gray-500',
  regressed: 'bg-orange-100 text-orange-800 border-orange-500',
};

const ACTIONS_BY_STATUS: Record<ErrorGroupStatus, ErrorGroupAction[]> = {
  open: ['resolve', 'ignore'],
  resolved: ['reopen'],
  ignored: ['unmute'],
  regressed: ['resolve', 'reopen'],
};

const ACTION_LABEL: Record<ErrorGroupAction, string> = {
  resolve: '해결됨으로 표시',
  ignore: '무시',
  reopen: '다시 열기',
  unmute: '무시 해제',
};

export function ErrorDetail({
  projectId,
  groupId,
  isOwner,
  defaultResolveSha,
  onBack,
}: ErrorDetailProps) {
  const { data, isLoading, error } = useErrorGroupDetail(projectId, groupId);
  const transitionMutation = useTransitionErrorStatus(projectId);

  if (isLoading) {
    return <p className="text-muted-foreground font-medium">로딩 중...</p>;
  }
  if (error || !data) {
    return (
      <div className="border-2 border-black bg-white p-4 rounded">
        <p className="text-sm text-red-700 font-bold">에러 그룹을 불러올 수 없습니다.</p>
        <Button onClick={onBack} className="mt-2 border-2 border-black font-bold">
          ← 목록으로
        </Button>
      </div>
    );
  }

  const { group, recent_events, git_context } = data;
  const actions = ACTIONS_BY_STATUS[group.status];

  const handleAction = (action: ErrorGroupAction) => {
    transitionMutation.mutate({
      groupId: group.id,
      action,
      // resolve 일 때만 sha 채움. user 가 명시 입력 UI 는 v2.
      resolved_in_version_sha: action === 'resolve' ? defaultResolveSha ?? null : null,
    });
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <Button onClick={onBack} className="border-2 border-black font-bold text-xs">
          ← 목록
        </Button>
        <span
          className={`px-2 py-0.5 text-[10px] font-bold uppercase border-2 rounded ${
            STATUS_STYLES[group.status]
          }`}
        >
          {group.status}
        </span>
        <span className="text-xs text-muted-foreground">
          누적 {group.event_count.toLocaleString()} 회
        </span>
      </div>

      <header className="border-2 border-black bg-white p-3 sm:p-4 shadow-[2px_2px_0px_0px_rgba(244,0,4,1)] rounded">
        <h2 className="text-base sm:text-lg font-black break-words">
          {group.exception_class}
        </h2>
        {group.exception_message_sample && (
          <p className="mt-1 text-sm break-words text-muted-foreground">
            {group.exception_message_sample}
          </p>
        )}
        <div className="mt-2 grid grid-cols-1 sm:grid-cols-2 gap-1 text-[11px] text-muted-foreground">
          <p>
            <span className="font-bold">최초:</span>{' '}
            {new Date(group.first_seen_at).toLocaleString()}
          </p>
          <p>
            <span className="font-bold">최근:</span>{' '}
            {new Date(group.last_seen_at).toLocaleString()}
          </p>
          {group.resolved_at && (
            <p>
              <span className="font-bold">해결됨:</span>{' '}
              {new Date(group.resolved_at).toLocaleString()}
              {group.resolved_in_version_sha && ` @ ${group.resolved_in_version_sha.slice(0, 8)}`}
            </p>
          )}
        </div>
      </header>

      {isOwner && actions.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {actions.map((action) => (
            <Button
              key={action}
              onClick={() => handleAction(action)}
              disabled={transitionMutation.isPending}
              className="border-2 border-black font-bold text-xs sm:text-sm"
            >
              {transitionMutation.isPending ? '처리 중...' : ACTION_LABEL[action]}
            </Button>
          ))}
        </div>
      )}

      <GitContextPanel context={git_context} firstSeenSha={group.first_seen_version_sha} />

      <section className="border-2 border-black bg-white p-3 sm:p-4 shadow-[2px_2px_0px_0px_rgba(244,0,4,1)] rounded">
        <h3 className="text-sm sm:text-base font-black mb-2">
          최근 이벤트 ({recent_events.length})
        </h3>
        {recent_events.length === 0 ? (
          <p className="text-xs text-muted-foreground italic">이벤트 없음</p>
        ) : (
          <ul className="space-y-2">
            {recent_events.map((evt) => (
              <li
                key={evt.id}
                className="border border-black/20 rounded p-2 bg-gray-50 text-xs space-y-1"
              >
                <div className="flex items-center gap-2 flex-wrap">
                  <LogLevelBadge level={evt.level} />
                  <code className="text-[10px] font-mono">{evt.logger_name}</code>
                  <span className="text-muted-foreground">·</span>
                  <code
                    className="text-[10px] font-mono"
                    title={evt.version_sha}
                  >
                    {evt.version_sha === 'unknown' ? 'unknown' : evt.version_sha.slice(0, 8)}
                  </code>
                  <span className="text-muted-foreground ml-auto">
                    {new Date(evt.received_at).toLocaleString()}
                  </span>
                </div>
                <p className="break-words">{evt.message}</p>
                {evt.exception_message && evt.exception_message !== evt.message && (
                  <p className="text-muted-foreground italic break-words">
                    {evt.exception_message}
                  </p>
                )}
                {/* StackTraceViewer 는 LogEventSummary 에 stack_trace 가 없어서 v2 — 상세 GET 응답에 포함시킬 때 사용 */}
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
```

(StackTraceViewer 는 backend 가 LogEventSummary 에 stack_trace 를 포함시키지 않음 — Phase 4 의 의도적 design [stack_trace 길이 제한]. v2 에서 별도 endpoint 또는 별도 schema 로 처리. import 만 해두지 말고 본 task 에서는 컴포넌트 미사용 — 다른 곳에서 쓰일 가능성 대비 export 만.)

- [ ] **Step 2: build + lint 확인**

```bash
cd frontend && bun run build 2>&1 | tail -5
```
Expected: type errors 0.

`StackTraceViewer` import 안 했으니 unused-import 경고 없을 것. 만약 import 했다면 제거.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/errors/ErrorDetail.tsx
git commit -m "feat(error-log/phase5-frontend-errors): ErrorDetail + status 전이 액션 (Task 5)"
```

---

## Task 6: ErrorsList — status 필터 + 클릭 이동

**Files:**
- Create: `frontend/src/components/errors/ErrorsList.tsx`

- [ ] **Step 1: ErrorsList 작성**

`frontend/src/components/errors/ErrorsList.tsx` 생성:

```typescript
import { useState } from 'react';
import { useErrorGroups } from '@/hooks/useErrorGroups';
import type { ErrorGroupStatus, ErrorGroupSummary } from '@/types/error';

interface ErrorsListProps {
  projectId: string;
  onSelectGroup: (groupId: string) => void;
}

const STATUS_FILTER_OPTIONS: Array<{ value: ErrorGroupStatus | 'all'; label: string }> = [
  { value: 'all', label: '전체' },
  { value: 'open', label: 'OPEN' },
  { value: 'regressed', label: 'REGRESSED' },
  { value: 'resolved', label: 'RESOLVED' },
  { value: 'ignored', label: 'IGNORED' },
];

const STATUS_BADGE: Record<ErrorGroupStatus, string> = {
  open: 'bg-red-100 text-red-800 border-red-500',
  resolved: 'bg-green-100 text-green-800 border-green-500',
  ignored: 'bg-gray-100 text-gray-700 border-gray-500',
  regressed: 'bg-orange-100 text-orange-800 border-orange-500',
};

function ErrorRow({
  group,
  onClick,
}: {
  group: ErrorGroupSummary;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="w-full text-left bg-white border-2 border-black shadow-[2px_2px_0px_0px_rgba(244,0,4,1)] hover:shadow-[4px_4px_0px_0px_rgba(244,0,4,1)] hover:-translate-x-0.5 hover:-translate-y-0.5 transition-all p-2.5 sm:p-3"
    >
      <div className="flex items-start gap-2 flex-wrap">
        <span
          className={`px-1.5 py-0.5 text-[10px] font-bold uppercase border-2 rounded ${
            STATUS_BADGE[group.status]
          }`}
        >
          {group.status}
        </span>
        <h4 className="font-bold text-xs sm:text-sm break-words flex-1 min-w-0">
          {group.exception_class}
        </h4>
        <span className="text-[11px] text-muted-foreground whitespace-nowrap">
          {group.event_count.toLocaleString()} 회
        </span>
      </div>
      {group.exception_message_sample && (
        <p className="mt-1 text-[11px] text-muted-foreground break-words line-clamp-2">
          {group.exception_message_sample}
        </p>
      )}
      <p className="mt-1 text-[10px] text-muted-foreground">
        최근: {new Date(group.last_seen_at).toLocaleString()}
      </p>
    </button>
  );
}

export function ErrorsList({ projectId, onSelectGroup }: ErrorsListProps) {
  const [statusFilter, setStatusFilter] = useState<ErrorGroupStatus | 'all'>('all');
  const apiStatus = statusFilter === 'all' ? undefined : statusFilter;
  const { data, isLoading, error } = useErrorGroups(projectId, { status: apiStatus, limit: 50 });

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-1.5">
        {STATUS_FILTER_OPTIONS.map((opt) => (
          <button
            key={opt.value}
            type="button"
            onClick={() => setStatusFilter(opt.value)}
            className={`px-2.5 py-1 text-xs font-bold border-2 border-black rounded transition-colors ${
              statusFilter === opt.value
                ? 'bg-black text-white'
                : 'bg-background hover:bg-yellow-100'
            }`}
          >
            {opt.label}
          </button>
        ))}
      </div>

      {isLoading && (
        <p className="text-muted-foreground font-medium">로딩 중...</p>
      )}
      {error && (
        <p className="text-red-700 font-bold text-sm">에러 목록을 불러오지 못했습니다.</p>
      )}
      {data && data.items.length === 0 && (
        <div className="border-2 border-dashed border-muted-foreground rounded p-6 text-center">
          <p className="text-muted-foreground font-medium text-sm">
            {statusFilter === 'all'
              ? '아직 에러가 없습니다. 🎉'
              : `${statusFilter.toUpperCase()} 상태 에러가 없습니다.`}
          </p>
        </div>
      )}
      {data && data.items.length > 0 && (
        <>
          <p className="text-[11px] text-muted-foreground">
            {data.items.length} / 총 {data.total} 건
          </p>
          <ul className="space-y-2">
            {data.items.map((group) => (
              <li key={group.id}>
                <ErrorRow group={group} onClick={() => onSelectGroup(group.id)} />
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}
```

- [ ] **Step 2: build + lint 확인**

```bash
cd frontend && bun run build 2>&1 | tail -5
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/errors/ErrorsList.tsx
git commit -m "feat(error-log/phase5-frontend-errors): ErrorsList + status 필터 (Task 6)"
```

---

## Task 7: DashboardPage 통합 — 'errors' viewMode 추가

**Files:**
- Modify: `frontend/src/pages/DashboardPage.tsx`

- [ ] **Step 1: ViewMode 타입 + state 추가**

`DashboardPage.tsx` 의 `type ViewMode = 'board' | 'table' | 'week';` 라인을 다음으로 교체:

```typescript
type ViewMode = 'board' | 'table' | 'week' | 'errors';
```

`useState<Task | null>(null);` 옆 (다른 selectedX state 들 옆) 에 추가:

```typescript
const [selectedErrorGroupId, setSelectedErrorGroupId] = useState<string | null>(null);
```

또한 viewMode 가 `errors` 로 바뀔 때 errorGroupId 가 stale 하지 않게 — 프로젝트 바뀌면 자동 클리어. `useEffect` 추가 (다른 useEffect 들 옆):

```typescript
useEffect(() => {
  // 프로젝트 변경 시 선택된 에러 그룹 초기화.
  setSelectedErrorGroupId(null);
}, [selectedProjectId]);
```

- [ ] **Step 2: ViewMode 토글 버튼 추가**

기존 토글 (`Board`, `Table`, `Week` 3개 버튼) 다음에 4번째 버튼 append. 같은 `border-l-2 border-black` 패턴:

기존 마지막 버튼 (`Week`) 의 `</button>` 직후, 같은 `<div>` 안에 추가:

```typescript
                <button
                  className={`flex-1 sm:flex-none px-2 sm:px-3 py-1.5 text-[11px] sm:text-sm font-bold border-l-2 border-black transition-colors ${
                    viewMode === 'errors'
                      ? 'bg-black text-white'
                      : 'bg-background hover:bg-yellow-100'
                  }`}
                  onClick={() => setViewMode('errors')}
                >
                  Errors
                </button>
```

- [ ] **Step 3: ErrorsList / ErrorDetail import + 메인 영역 분기 추가**

파일 상단에 import 추가 (다른 컴포넌트 imports 옆):

```typescript
import { ErrorsList } from '@/components/errors/ErrorsList';
import { ErrorDetail } from '@/components/errors/ErrorDetail';
```

기존 메인 영역의 큰 if/ternary 분기를 확장. `viewMode === 'board' || viewMode === 'table'` 분기 다음에 (`isWeekLoading` 분기 직전 위치) 새 `else if` 분기 추가. 가장 정확하게는 `isWeekLoading` 라인 바로 위에 다음 ternary 가지:

기존:
```typescript
          ) : isWeekLoading ? (
            <div className="flex items-center justify-center h-full">
              <p className="text-muted-foreground font-medium">로딩 중...</p>
            </div>
          ) : (
```

다음으로 변환:
```typescript
          ) : viewMode === 'errors' ? (
            !selectedProjectId ? (
              <div className="flex items-center justify-center h-full">
                <div className="text-center border-2 border-black shadow-[6px_6px_0px_0px_rgba(244,0,4,1)] bg-white p-6 sm:p-10 rounded">
                  <p className="text-muted-foreground font-medium text-sm sm:text-base">
                    ← 왼쪽에서 프로젝트를 선택하세요.
                  </p>
                </div>
              </div>
            ) : selectedErrorGroupId ? (
              <ErrorDetail
                projectId={selectedProjectId}
                groupId={selectedErrorGroupId}
                isOwner={myRole === 'owner'}
                defaultResolveSha={selectedProject?.last_synced_commit_sha ?? null}
                onBack={() => setSelectedErrorGroupId(null)}
              />
            ) : (
              <ErrorsList
                projectId={selectedProjectId}
                onSelectGroup={(groupId) => setSelectedErrorGroupId(groupId)}
              />
            )
          ) : isWeekLoading ? (
            <div className="flex items-center justify-center h-full">
              <p className="text-muted-foreground font-medium">로딩 중...</p>
            </div>
          ) : (
```

- [ ] **Step 4: Project 타입에 last_synced_commit_sha 노출 확인**

기존 `useMyProjects` 가 반환하는 Project 타입에 `last_synced_commit_sha` 가 있는지 확인. `frontend/src/types/project.ts` 를 grep:

```bash
grep -n "last_synced_commit_sha\|interface Project" frontend/src/types/project.ts
```

만약 `Project` 인터페이스에 없으면 추가 (타입 안전성 위해):

```typescript
export interface Project {
  // ... 기존 필드
  last_synced_commit_sha: string | null;
}
```

(이미 backend 응답에 포함되어 있으므로 타입만 맞추면 됨.)

만약 backend 가 이 필드를 응답에 포함하는지 의심되면, ProjectResponse schema 확인. 결국 `defaultResolveSha={selectedProject?.last_synced_commit_sha ?? null}` 가 컴파일되면 OK.

- [ ] **Step 5: build + lint + dev server smoke**

```bash
cd frontend && bun run build 2>&1 | tail -5
```
Expected: 성공.

```bash
cd frontend && bun run lint 2>&1 | tail -5
```
Expected: 0 error.

수동 smoke (선택, 시간 있을 때 — 실패 안 해도 머지 가능):

```bash
cd frontend && bun run dev
```
브라우저에서:
1. login → dashboard
2. 프로젝트 선택 → Errors 토글 클릭
3. 목록 화면 표시 확인 (실제 에러 데이터 없으면 "🎉" 메시지)
4. status 필터 클릭 시 fetch 다시 발생 (DevTools Network)
5. 에러 row 클릭 → 상세 화면
6. ← 목록 버튼 → 목록 복귀
7. (OWNER 만) resolve / ignore 버튼 클릭 → 상태 변경 + audit 필드 표시 확인

- [ ] **Step 6: Commit**

```bash
git add frontend/src/pages/DashboardPage.tsx frontend/src/types/project.ts
git commit -m "feat(error-log/phase5-frontend-errors): DashboardPage 'errors' viewMode 통합 (Task 7)"
```

(만약 project.ts 수정 안 했으면 그것 빼고 commit.)

---

## Task 8: handoff 업데이트 + PR

- [ ] **Step 1: 최종 build + lint 한 번 더**

```bash
cd frontend && bun run build 2>&1 | tail -5 && bun run lint 2>&1 | tail -5
```

- [ ] **Step 2: handoffs/main.md 헤드 업데이트**

`handoffs/main.md` 최상단에 새 entry 추가:

```markdown
## 2026-05-01 (밤) — Error-log Phase 5 Frontend Errors

- [x] DashboardPage 'errors' viewMode 추가 — Board / Table / Week / Errors 토글
- [x] ErrorsList — status 필터 (전체 / OPEN / REGRESSED / RESOLVED / IGNORED) + 목록 + 클릭 → 상세
- [x] ErrorDetail — 헤더 + audit 필드 + git 컨텍스트 + 최근 이벤트 + 액션 버튼 (resolve/ignore/reopen/unmute, OWNER 만)
- [x] GitContextPanel — first_seen 의 handoffs/tasks(archived 배지)/push event + previous_good_sha
- [x] LogLevelBadge / StackTraceViewer (작은 reusable; StackTraceViewer 는 v2 에서 사용)
- [x] useErrorGroups hooks — list / detail / transition (TanStack Query, list+detail invalidate)
- [x] api.errors / api.logs namespace + types/error.ts / types/log.ts

### 다음 (Frontend Logs sub-phase 또는 Frontend Ops sub-phase)

- [ ] LogsPage + LogSearchBox (pg_trgm) — Logs 토글 새 viewMode
- [ ] LogTokensPage + LogHealthBadge — 헤더 ⚠️ + 토큰 관리
- [ ] URL 라우팅 (deep-link `/projects/:id/errors/:groupId` ) — Discord 알림에서 직접 이동
- [ ] resolve 시 user-입력 sha (현재는 자동 last_synced_commit_sha)
- [ ] StackTraceViewer 실제 활용 — 별도 endpoint 또는 LogEventDetail schema
```

- [ ] **Step 3: branch push + PR 생성**

```bash
git add handoffs/main.md
git commit -m "docs(handoff): Error-log Phase 5 Frontend Errors 완료 + 다음 sub-phase 안내"
git push -u origin feature/error-log-phase5-frontend-errors
```

PR title: `feat(error-log/phase5-frontend-errors): ErrorsList + ErrorDetail + GitContextPanel + DashboardPage 통합`

PR body (heredoc):

```markdown
## Summary

Phase 5 Backend (PR #20) 의 endpoint 4개를 화면에 노출. pslog 켜서 "오늘 무슨 에러 났지" 보는 그 분기점.

DashboardPage 의 viewMode 에 'errors' 추가 — 기존 Board/Table/Week 와 같은 토글 패턴. ErrorsList ↔ ErrorDetail 은 selectedErrorGroupId state 로 전환 (URL 라우팅은 v2 — Discord 딥링크 추가될 때).

## 주요 컴포넌트

| 컴포넌트 | 책임 |
|---|---|
| `ErrorsList` | status 필터 (전체/OPEN/REGRESSED/RESOLVED/IGNORED) + 목록 + 클릭 → 상세 |
| `ErrorDetail` | 헤더 (status badge / 누적 회수) + audit 필드 + GitContextPanel + 최근 이벤트 + 액션 버튼 |
| `GitContextPanel` | first_seen 의 handoffs/tasks(archived 배지)/push event + previous_good_sha (직전 정상 SHA) |
| `LogLevelBadge` | 5 level (DEBUG/INFO/WARNING/ERROR/CRITICAL) 색상 배지 |
| `StackTraceViewer` | `<details>` 접기/펴기. v1 미사용 — v2 에서 stack_trace 별도 endpoint 시 활용 |

## 액션 버튼 (PATCH /errors)

OWNER 만:
- `OPEN` → 해결됨 / 무시
- `RESOLVED` → 다시 열기
- `IGNORED` → 무시 해제
- `REGRESSED` → 해결됨 / 다시 열기

resolve 액션은 자동으로 `defaultResolveSha = project.last_synced_commit_sha` 사용. 사용자가 수동 입력하는 UI 는 v2.

## API integration

- `api.errors.list / get / transition` (`useErrorGroups`, `useErrorGroupDetail`, `useTransitionErrorStatus`)
- `api.logs.list` (코드 추가만, UI 는 sub-phase 3 에서 사용)
- TanStack Query — transition 성공 시 list+detail invalidate (audit 필드 새 응답 반영)

## 의도적 v2

- URL 라우팅 (deep-link)
- StackTraceViewer 활용 (현재 LogEventSummary 에 stack_trace 미포함 — 의도적, 길이 제한)
- ErrorTrendChart (시간별 빈도) — backend aggregate endpoint 필요
- resolve sha 사용자 입력 UI

## Test plan

- [x] TypeScript build (tsc -b) 통과
- [x] ESLint 통과
- [ ] dev server 수동 smoke — 토글 전환 / 목록 → 상세 / 액션 버튼 / 권한 (EDITOR 시 액션 안 보임)
- [ ] Phase 5 Backend PR #20 의 결과 활용 — 실제 app-chak 에서 발생한 에러 dogfooding
```

PR open with `gh pr create --title "..." --body "$(cat <<'EOF' ... EOF)"`.

## Self-review checklist

- [x] 모든 새 파일이 file structure 표 안에 있음
- [x] 모든 컴포넌트 step 에 actual code (no placeholder)
- [x] DashboardPage 변경 — 충돌 가능 위치 (기존 ternary chain 의 정확한 가지) 명시
- [x] 백엔드 변경 0 — Phase 5 Backend 가 모든 endpoint 제공
- [x] 의도적 deferred 항목 4개 (URL 라우팅 / StackTraceViewer 활용 / ErrorTrendChart / resolve sha 입력) 모두 PR description 에 명시
