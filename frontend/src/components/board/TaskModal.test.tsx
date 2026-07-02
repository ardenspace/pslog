/**
 * TaskModal 초기화 characterization — dep 배열 교체(refactor/taskmodal-effect-deps) 전 행동 핀고정.
 * 계약: 이 테스트가 교체 전/후 무수정 green = 행동 동치.
 * brief: docs/tasks/taskmodal-effect-deps/brief.md
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { TaskModal } from '@/components/board/TaskModal';
import type { Task, TaskStatus } from '@/types/task';
import type { ProjectMember } from '@/types/project';

vi.mock('@/services/api', () => ({
  api: {
    tasks: { create: vi.fn(), update: vi.fn() },
  },
}));

function makeMember(overrides: Partial<ProjectMember> & { user_id: string; name: string }): ProjectMember {
  const { name, ...rest } = overrides;
  return {
    id: `m-${overrides.user_id}`,
    role: 'editor',
    created_at: '2026-01-01T00:00:00Z',
    user: { id: overrides.user_id, name, email: `${overrides.user_id}@example.com` },
    ...rest,
  };
}

const members = [
  makeMember({ user_id: 'u1', name: '아든' }),
  makeMember({ user_id: 'u2', name: '세종' }),
];

function makeTask(overrides: Partial<Task>): Task {
  return {
    id: 't1',
    project_id: 'p1',
    title: '기존 제목',
    description: null,
    status: 'todo' as TaskStatus,
    due_date: null,
    assignee_id: null,
    reporter_id: null,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    assignee: null,
    reporter: null,
    source: 'manual',
    external_id: null,
    last_commit_sha: null,
    archived_at: null,
    handoff_missing: false,
    ...overrides,
  };
}

function renderModal(ui: React.ReactElement) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const wrap = (el: React.ReactElement) => (
    <QueryClientProvider client={queryClient}>{el}</QueryClientProvider>
  );
  const result = render(wrap(ui));
  return { ...result, rerenderModal: (el: React.ReactElement) => result.rerender(wrap(el)) };
}

describe('TaskModal 초기화 (characterization)', () => {
  it('edit 모드 오픈 시 task 값이 폼에 로드된다', () => {
    renderModal(
      <TaskModal
        mode="edit"
        task={makeTask({
          title: '결제 웹훅 재시도',
          description: '지수 백오프로',
          status: 'doing',
          due_date: '2026-07-10T00:00:00Z',
          assignee_id: 'u2',
        })}
        myRole="owner"
        members={members}
        isOpen={true}
        onClose={() => {}}
      />
    );

    expect(screen.getByDisplayValue('결제 웹훅 재시도')).toBeInTheDocument();
    expect(screen.getByDisplayValue('지수 백오프로')).toBeInTheDocument();
    expect(screen.getByText('Doing')).toBeInTheDocument();
    expect(screen.getByText('세종')).toBeInTheDocument();
    expect(screen.getByText('2026. 7. 10')).toBeInTheDocument();
  });

  it('create 모드 오픈 시 폼이 리셋되고 담당자 기본값은 currentUserId', () => {
    renderModal(
      <TaskModal
        mode="create"
        projectId="p1"
        currentUserId="u1"
        members={members}
        isOpen={true}
        onClose={() => {}}
      />
    );

    expect(screen.getByPlaceholderText('태스크 제목')).toHaveValue('');
    expect(screen.getByText('To Do')).toBeInTheDocument();
    expect(screen.getByText('아든 (나)')).toBeInTheDocument();
  });

  it('열린 채 다른 task 로 바뀌면 폼이 새 task 값으로 갱신된다', () => {
    const { rerenderModal } = renderModal(
      <TaskModal
        mode="edit"
        task={makeTask({ id: 't1', title: '첫 번째 태스크' })}
        myRole="owner"
        members={members}
        isOpen={true}
        onClose={() => {}}
      />
    );
    expect(screen.getByDisplayValue('첫 번째 태스크')).toBeInTheDocument();

    rerenderModal(
      <TaskModal
        mode="edit"
        task={makeTask({ id: 't2', title: '두 번째 태스크', status: 'blocked' })}
        myRole="owner"
        members={members}
        isOpen={true}
        onClose={() => {}}
      />
    );
    expect(screen.getByDisplayValue('두 번째 태스크')).toBeInTheDocument();
    expect(screen.getByText('Blocked')).toBeInTheDocument();
  });
});
