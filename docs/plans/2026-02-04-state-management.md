# 상태 관리 전략

## 개요

이 문서는 pslog 프론트엔드의 상태 관리 전략을 정의한다.

- **TanStack Query**: 서버 상태 (API 데이터)
- **Zustand**: 클라이언트 상태 (인증, UI)
- **useState**: 로컬 컴포넌트 상태

---

## 1. 상태 분류 원칙

| 상태 유형 | 도구 | 특징 | 예시 |
|----------|------|------|------|
| 서버 상태 | TanStack Query | API에서 가져온 데이터, 캐싱/재요청 자동 | tasks, projects |
| 인증 상태 | Zustand + persist | 영속 필요, 새로고침 후 유지 | user, token |
| 전역 UI 상태 | Zustand | 여러 컴포넌트 공유 | selectedProjectId, filters |
| 로컬 UI 상태 | useState | 단일 컴포넌트 내 | isModalOpen, inputValue |

### 판단 흐름

```
서버에서 오는 데이터인가?
  ├─ YES → TanStack Query
  └─ NO → 여러 컴포넌트가 공유하는가?
            ├─ YES → 새로고침 후에도 유지해야 하는가?
            │         ├─ YES → Zustand + persist
            │         └─ NO → Zustand
            └─ NO → useState
```

---

## 2. TanStack Query - 서버 상태

### Query Key 컨벤션

계층적 구조를 사용하여 캐시 무효화가 용이하도록 한다.

```typescript
// 워크스페이스
['workspaces']                                    // 목록
['workspaces', workspaceId]                       // 상세
['workspaces', workspaceId, 'members']            // 멤버 목록

// 프로젝트
['workspaces', workspaceId, 'projects']           // 워크스페이스의 프로젝트 목록
['projects', projectId]                           // 프로젝트 상세
['projects', projectId, 'members']                // 프로젝트 멤버 목록

// 태스크
['projects', projectId, 'tasks']                  // 프로젝트의 태스크 목록
['projects', projectId, 'tasks', { filters }]     // 필터 적용
['tasks', taskId]                                 // 태스크 상세
['tasks', 'week', weekStart]                      // 주간 태스크

// 공유 링크
['projects', projectId, 'share-links']            // 프로젝트의 공유 링크 목록
['public', 'share', token]                        // 공개 공유 데이터

// 사용자
['user', 'me']                                    // 현재 사용자
```

### Hooks 구조

```
src/hooks/
├── useAuth.ts              # 인증 (login, register, logout, me)
├── useWorkspaces.ts        # 워크스페이스 CRUD
├── useProjects.ts          # 프로젝트 CRUD
├── useTasks.ts             # 태스크 CRUD
├── useShareLinks.ts        # 공유 링크 CRUD
└── index.ts                # re-export
```

### 예시: useTasks

```typescript
// src/hooks/useTasks.ts
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '@/services/api';
import type { TaskCreate, TaskUpdate, TaskFilters } from '@/types';

// 프로젝트의 태스크 목록 조회
export function useTasks(projectId: string | null, filters?: TaskFilters) {
  return useQuery({
    queryKey: ['projects', projectId, 'tasks', filters],
    queryFn: () => api.tasks.list(projectId!, filters),
    enabled: !!projectId,
  });
}

// 태스크 상세 조회
export function useTask(taskId: string | null) {
  return useQuery({
    queryKey: ['tasks', taskId],
    queryFn: () => api.tasks.getById(taskId!),
    enabled: !!taskId,
  });
}

// 태스크 생성
export function useCreateTask(projectId: string) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (data: TaskCreate) => api.tasks.create(projectId, data),
    onSuccess: () => {
      // 해당 프로젝트의 태스크 목록 무효화
      queryClient.invalidateQueries({
        queryKey: ['projects', projectId, 'tasks'],
      });
    },
  });
}

// 태스크 수정
export function useUpdateTask() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ taskId, data }: { taskId: string; data: TaskUpdate }) =>
      api.tasks.update(taskId, data),
    onSuccess: (response, { taskId }) => {
      // 태스크 상세 캐시 업데이트
      queryClient.setQueryData(['tasks', taskId], response.data);
      // 목록도 무효화 (projectId를 응답에서 추출)
      const projectId = response.data.project_id;
      queryClient.invalidateQueries({
        queryKey: ['projects', projectId, 'tasks'],
      });
    },
  });
}

// 태스크 삭제
export function useDeleteTask() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (taskId: string) => api.tasks.delete(taskId),
    onSuccess: () => {
      // 전체 태스크 관련 쿼리 무효화
      queryClient.invalidateQueries({
        predicate: (query) =>
          query.queryKey[0] === 'projects' && query.queryKey[2] === 'tasks',
      });
    },
  });
}

// 주간 태스크 조회
export function useWeekTasks(weekStart: string) {
  return useQuery({
    queryKey: ['tasks', 'week', weekStart],
    queryFn: () => api.tasks.getWeek(weekStart),
  });
}
```

### 예시: useProjects

```typescript
// src/hooks/useProjects.ts
export function useProjects(workspaceId: string | null) {
  return useQuery({
    queryKey: ['workspaces', workspaceId, 'projects'],
    queryFn: () => api.projects.list(workspaceId!),
    enabled: !!workspaceId,
  });
}

export function useProject(projectId: string | null) {
  return useQuery({
    queryKey: ['projects', projectId],
    queryFn: () => api.projects.getById(projectId!),
    enabled: !!projectId,
  });
}
```

---

## 3. Zustand - 클라이언트 상태

### Store 구조

```
src/stores/
├── authStore.ts            # 인증 상태 (영속)
├── uiStore.ts              # UI 상태 (부분 영속)
└── index.ts                # re-export
```

### authStore (기존 유지)

```typescript
// src/stores/authStore.ts
import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import type { User } from '@/types';

interface AuthState {
  user: User | null;
  token: string | null;
  isAuthenticated: boolean;
  setAuth: (user: User, token: string) => void;
  logout: () => void;
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      user: null,
      token: null,
      isAuthenticated: false,
      setAuth: (user, token) => {
        localStorage.setItem('access_token', token);
        set({ user, token, isAuthenticated: true });
      },
      logout: () => {
        localStorage.removeItem('access_token');
        set({ user: null, token: null, isAuthenticated: false });
      },
    }),
    {
      name: 'auth-storage',
      partialize: (state) => ({
        user: state.user,
        token: state.token,
        isAuthenticated: state.isAuthenticated,
      }),
    }
  )
);
```

### uiStore (추가)

```typescript
// src/stores/uiStore.ts
import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import type { TaskStatus } from '@/types';

interface TaskFilters {
  mineOnly: boolean;
  status: TaskStatus | null;
  assigneeId: string | null;
}

interface UIState {
  // 선택된 컨텍스트 (영속)
  selectedWorkspaceId: string | null;
  selectedProjectId: string | null;

  // 필터 상태 (비영속)
  taskFilters: TaskFilters;

  // 액션
  setSelectedWorkspace: (id: string | null) => void;
  setSelectedProject: (id: string | null) => void;
  setTaskFilters: (filters: Partial<TaskFilters>) => void;
  resetTaskFilters: () => void;
}

const defaultFilters: TaskFilters = {
  mineOnly: false,
  status: null,
  assigneeId: null,
};

export const useUIStore = create<UIState>()(
  persist(
    (set) => ({
      selectedWorkspaceId: null,
      selectedProjectId: null,
      taskFilters: defaultFilters,

      setSelectedWorkspace: (id) =>
        set({ selectedWorkspaceId: id, selectedProjectId: null }),
      setSelectedProject: (id) =>
        set({ selectedProjectId: id }),
      setTaskFilters: (filters) =>
        set((state) => ({
          taskFilters: { ...state.taskFilters, ...filters },
        })),
      resetTaskFilters: () =>
        set({ taskFilters: defaultFilters }),
    }),
    {
      name: 'ui-storage',
      // 선택된 workspace/project만 영속화
      partialize: (state) => ({
        selectedWorkspaceId: state.selectedWorkspaceId,
        selectedProjectId: state.selectedProjectId,
      }),
    }
  )
);
```

### 영속화 정책

| 상태 | persist | 이유 |
|------|---------|------|
| authStore 전체 | O | 새로고침 후에도 로그인 유지 |
| selectedWorkspaceId | O | 마지막 선택한 워크스페이스 기억 |
| selectedProjectId | O | 마지막 선택한 프로젝트 기억 |
| taskFilters | X | 매번 기본값으로 시작 (의도적) |

---

## 4. 데이터 흐름

### 컴포넌트 사용 패턴

```typescript
// src/pages/BoardPage.tsx
import { useState } from 'react';
import { useUIStore } from '@/stores/uiStore';
import { useTasks, useCreateTask } from '@/hooks/useTasks';

function BoardPage() {
  // ─── Zustand: 전역 UI 상태 ─────────────────────────
  const {
    selectedProjectId,
    taskFilters,
    setTaskFilters,
  } = useUIStore();

  // ─── TanStack Query: 서버 상태 ─────────────────────
  const {
    data: tasks,
    isLoading,
    error,
  } = useTasks(selectedProjectId, taskFilters);

  const createTaskMutation = useCreateTask(selectedProjectId!);

  // ─── useState: 로컬 UI 상태 ────────────────────────
  const [isCreateModalOpen, setCreateModalOpen] = useState(false);

  // ─── 핸들러 ────────────────────────────────────────
  const handleCreateTask = (data: TaskCreate) => {
    createTaskMutation.mutate(data, {
      onSuccess: () => setCreateModalOpen(false),
    });
  };

  const handleToggleMineOnly = () => {
    setTaskFilters({ mineOnly: !taskFilters.mineOnly });
  };

  // ─── 렌더링 ────────────────────────────────────────
  if (!selectedProjectId) {
    return <div>프로젝트를 선택해주세요</div>;
  }

  if (isLoading) return <Spinner />;
  if (error) return <ErrorMessage error={error} />;

  return (
    <div>
      <header>
        <FilterToggle
          label="내 태스크만"
          checked={taskFilters.mineOnly}
          onChange={handleToggleMineOnly}
        />
        <Button onClick={() => setCreateModalOpen(true)}>
          새 태스크
        </Button>
      </header>

      <KanbanBoard tasks={tasks ?? []} />

      <CreateTaskModal
        open={isCreateModalOpen}
        onClose={() => setCreateModalOpen(false)}
        onSubmit={handleCreateTask}
        isLoading={createTaskMutation.isPending}
      />
    </div>
  );
}
```

### 데이터 흐름 다이어그램

```
┌─────────────────────────────────────────────────────────────────┐
│                         Component                                │
│                                                                  │
│   ┌──────────────┐    ┌──────────────┐    ┌──────────────────┐  │
│   │   useState   │    │   Zustand    │    │  TanStack Query  │  │
│   │  (로컬 UI)   │    │ (전역 UI)    │    │    (서버)        │  │
│   │              │    │              │    │                  │  │
│   │ isModalOpen  │    │ projectId    │    │ tasks, projects  │  │
│   │ inputValue   │    │ filters      │    │ isLoading, error │  │
│   └──────────────┘    └──────────────┘    └──────────────────┘  │
│         │                    │                      │            │
│         └────────────────────┴──────────────────────┘            │
│                              │                                   │
│                         Render                                   │
└─────────────────────────────────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│              User Action (클릭, 입력, 드래그 등)                  │
└─────────────────────────────────────────────────────────────────┘
                               │
              ┌────────────────┼────────────────┐
              ▼                ▼                ▼
        ┌──────────┐    ┌──────────┐    ┌──────────────┐
        │ setState │    │ Zustand  │    │   Mutation   │
        │ (로컬)   │    │  action  │    │  (서버 전송) │
        └──────────┘    └──────────┘    └──────────────┘
                                              │
                                              ▼
                                    ┌──────────────────┐
                                    │ invalidateQueries│
                                    │  (캐시 무효화)   │
                                    └──────────────────┘
                                              │
                                              ▼
                                    ┌──────────────────┐
                                    │   자동 리페치    │
                                    │  (UI 업데이트)   │
                                    └──────────────────┘
```

---

## 5. 파일 구조 (최종)

```
src/
├── hooks/
│   ├── index.ts
│   ├── useAuth.ts
│   ├── useWorkspaces.ts
│   ├── useProjects.ts
│   ├── useTasks.ts
│   └── useShareLinks.ts
│
├── stores/
│   ├── index.ts
│   ├── authStore.ts
│   └── uiStore.ts
│
├── services/
│   └── api.ts              # axios 클라이언트 + API 함수
│
└── types/
    ├── index.ts
    ├── auth.ts
    ├── user.ts
    ├── workspace.ts
    ├── project.ts
    ├── task.ts
    └── share-link.ts
```

---

## 관련 문서

- `docs/plans/2026-02-04-api-spec.md`: API 스펙
- `docs/plans/2026-02-04-data-model-design.md`: 데이터 모델
