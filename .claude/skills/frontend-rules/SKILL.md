---
name: frontend-rules
description: pslog frontend(React/TypeScript/Tailwind) 코드를 작성하거나 수정할 때 반드시 사용. 파일 구조, 컴포넌트/훅 패턴, API 클라이언트 중앙화, 타입/스타일 규칙을 담는다.
---

# Frontend Development Rules

Frontend 코드를 작성하거나 수정할 때 이 가이드를 따릅니다.

## File Structure
```
frontend/src/
├── components/
│   ├── ui/             # shadcn/ui 컴포넌트
│   ├── common/         # 공통 재사용 컴포넌트
│   └── features/       # 도메인별 컴포넌트 (tasks/, projects/)
├── pages/              # 페이지 컴포넌트
├── hooks/              # 커스텀 훅 (재사용)
├── stores/             # Zustand 상태 관리
├── services/           # API 클라이언트
├── types/              # 공통 타입 정의
├── utils/              # 유틸리티 함수
├── constants/          # 상수
└── lib/                # 외부 라이브러리 설정
```

## Component Patterns

### Props로 제어 가능하게
```tsx
// ✅ 재사용 가능한 컴포넌트
interface TaskCardProps {
  task: Task;
  onEdit?: (task: Task) => void;
  readOnly?: boolean;
}
export function TaskCard({ task, onEdit, readOnly = false }: TaskCardProps) {
  return (
    <Card>
      <h3>{task.title}</h3>
      {!readOnly && <Button onClick={() => onEdit?.(task)}>Edit</Button>}
    </Card>
  );
}
```

### Custom Hooks로 로직 분리
```tsx
// hooks/useWeekTasks.ts
export function useWeekTasks(weekStart: Date) {
  return useQuery({
    queryKey: ['tasks', 'week', weekStart],
    queryFn: () => api.tasks.getWeek(weekStart),
  });
}
```

### Constants 별도 관리
```tsx
// constants/taskStatus.ts
export const TASK_STATUS = {
  TODO: 'todo', DOING: 'doing', DONE: 'done', BLOCKED: 'blocked',
} as const;

export const TASK_STATUS_LABELS: Record<TaskStatus, string> = {
  todo: 'To Do', doing: 'In Progress', done: 'Done', blocked: 'Blocked',
};
```

## API Client (중앙화)
```tsx
// services/api.ts - 한 곳에서 모든 API 관리
const apiClient = axios.create({ baseURL: import.meta.env.VITE_API_URL });

export const api = {
  tasks: {
    getWeek: (weekStart: Date) =>
      apiClient.get<Task[]>('/tasks/week', { params: { week_start: weekStart } }),
    getById: (id: string) => apiClient.get<Task>(`/tasks/${id}`),
    update: (id: string, data: Partial<Task>) =>
      apiClient.put<Task>(`/tasks/${id}`, data),
  },
};
```

## Type Definitions
```tsx
// types/task.ts
export interface Task {
  id: string;
  title: string;
  status: TaskStatus;
  due_date: string | null;
  project: Project;
}
export type TaskStatus = 'todo' | 'doing' | 'done' | 'blocked';
```

## Styling Rules
```tsx
// ✅ Tailwind + cn() 유틸리티
import { cn } from '@/lib/utils';
<Card className={cn("p-4 hover:shadow-lg transition-shadow", isSelected && "border-primary")}>

// ❌ 인라인 스타일 금지
```

## Common Patterns

### Date Handling (통일)
```tsx
// utils/date.ts
import { startOfWeek, endOfWeek, format } from 'date-fns';
export const getWeekRange = (date: Date) => ({
  start: startOfWeek(date, { weekStartsOn: 1 }),
  end: endOfWeek(date, { weekStartsOn: 1 }),
});
export const formatDate = (date: Date) => format(date, 'yyyy-MM-dd');
```

### Permission Check (통일)
```tsx
// hooks/usePermission.ts
export function usePermission(resource: string, action: string) {
  const { user } = useAuth();
  return user.permissions.includes(`${resource}:${action}`);
}
```
