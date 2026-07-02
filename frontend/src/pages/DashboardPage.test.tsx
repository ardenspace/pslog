/**
 * DashboardPage characterization — 분해(refactor/dashboard-page-split) 전 행동 핀고정.
 * 계약: 이 테스트가 분해 전/후 무수정 green = 행동 동치.
 * spec: docs/tasks/dashboard-page-split/spec.md
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { DashboardPage } from '@/pages/DashboardPage';
import { api } from '@/services/api';
import { useAuthStore } from '@/stores/authStore';
import { useUIStore } from '@/stores/uiStore';
import type { Project, User, Workspace } from '@/types';

vi.mock('@/services/api', () => ({
  api: {
    auth: { me: vi.fn(), logout: vi.fn() },
    workspaces: { list: vi.fn() },
    projects: { listMine: vi.fn(), getMembers: vi.fn() },
    tasks: { list: vi.fn(), getWeek: vi.fn() },
    errors: { list: vi.fn() },
    drifts: { list: vi.fn() },
    discord: { sendSummary: vi.fn() },
  },
}));

const mocked = vi.mocked(api, true);

const user: User = {
  id: 'u1',
  email: 'arden@example.com',
  username: 'arden',
  name: '아든',
  created_at: '2026-01-01T00:00:00Z',
};

const workspace: Workspace = {
  id: 'w1',
  name: '워크스페이스원',
  slug: 'ws-one',
  description: null,
  my_role: 'owner',
  member_count: 1,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
};

function makeProject(overrides: Partial<Project>): Project {
  return {
    id: 'p1',
    workspace_id: 'w1',
    name: '프로젝트일',
    description: null,
    discord_webhook_url: null,
    last_synced_commit_sha: null,
    my_role: 'owner',
    task_count: 0,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    ...overrides,
  };
}

// axios 응답 껍데기 — 훅들이 r.data 만 사용
function ok<T>(data: T) {
  return Promise.resolve({ data } as never);
}

function setupApi({ projects }: { projects: Project[] }) {
  mocked.auth.me.mockReturnValue(ok(user) as never);
  mocked.workspaces.list.mockReturnValue(ok([workspace]) as never);
  mocked.projects.listMine.mockReturnValue(ok(projects) as never);
  mocked.projects.getMembers.mockReturnValue(ok([]) as never);
  mocked.tasks.list.mockReturnValue(ok([]) as never);
  mocked.tasks.getWeek.mockReturnValue(ok([]) as never);
  mocked.errors.list.mockReturnValue(ok({ items: [], total: 0 }) as never);
  mocked.drifts.list.mockReturnValue(ok({ items: [], total: 0 }) as never);
}

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <DashboardPage />
      </MemoryRouter>
    </QueryClientProvider>
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  window.localStorage.clear();
  useAuthStore.setState({ user, token: 't', isAuthenticated: true });
  useUIStore.setState({
    selectedWorkspaceId: null,
    selectedProjectId: null,
    taskFilters: { mineOnly: false, status: null },
  });
});

describe('DashboardPage characterization', () => {
  it('1. 로드 후 첫 워크스페이스/프로젝트를 자동 선택하고 사이드바에 목록을 렌더한다', async () => {
    setupApi({
      projects: [
        makeProject({ id: 'p1', name: '프로젝트일' }),
        makeProject({ id: 'p2', name: '프로젝트이' }),
      ],
    });
    renderPage();

    await waitFor(() => {
      expect(useUIStore.getState().selectedWorkspaceId).toBe('w1');
      expect(useUIStore.getState().selectedProjectId).toBe('p1');
    });
    // 사이드바는 데스크톱 + 모바일 드로어로 2회 렌더 — 중복 개수도 현 행동의 핀
    expect(await screen.findAllByText('워크스페이스원')).toHaveLength(2);
    // 선택된 p1 은 사이드바 2회 + BoardHeader 1회 = 3
    expect(screen.getAllByText('프로젝트일')).toHaveLength(3);
    expect(screen.getAllByText('프로젝트이')).toHaveLength(2);
  });

  it('2. 프로젝트가 없으면 "프로젝트 없음"과 선택 안내를 보여준다', async () => {
    setupApi({ projects: [] });
    renderPage();

    expect(await screen.findAllByText('프로젝트 없음')).toHaveLength(2);
    expect(
      screen.getByText('← 왼쪽에서 프로젝트를 선택하세요.')
    ).toBeInTheDocument();
  });

  it('3. 뷰 전환 — Week 는 주간 헤딩, Errors/Drift 는 각 목록 API 를 호출한다', async () => {
    setupApi({ projects: [makeProject({})] });
    renderPage();
    const u = userEvent.setup();

    await waitFor(() => {
      expect(useUIStore.getState().selectedProjectId).toBe('p1');
    });

    await u.click(screen.getByRole('button', { name: 'Week' }));
    expect(await screen.findByText('내 주간 태스크')).toBeInTheDocument();

    await u.click(screen.getByRole('button', { name: 'Errors' }));
    await waitFor(() => {
      expect(mocked.errors.list).toHaveBeenCalledWith('p1', expect.anything());
    });

    await u.click(screen.getByRole('button', { name: 'Drift' }));
    await waitFor(() => {
      expect(mocked.drifts.list).toHaveBeenCalledWith('p1', expect.anything());
    });
  });

  it('4. viewer 역할이면 owner 전용 컨트롤이 렌더되지 않는다', async () => {
    setupApi({ projects: [makeProject({ my_role: 'viewer' })] });
    renderPage();

    await waitFor(() => {
      expect(useUIStore.getState().selectedProjectId).toBe('p1');
    });
    await screen.findAllByText('프로젝트일');

    expect(screen.queryByText('공유 링크 관리')).not.toBeInTheDocument();
    expect(screen.queryByText('프로젝트 멤버')).not.toBeInTheDocument();
    expect(screen.queryByText('Discord Webhook URL')).not.toBeInTheDocument();
  });

  it('5. owner 가 webhook URL 을 수정하면 저장 버튼이 나타난다', async () => {
    setupApi({ projects: [makeProject({})] });
    renderPage();
    const u = userEvent.setup();

    const inputs = await screen.findAllByPlaceholderText(
      'https://discord.com/api/webhooks/...'
    );
    expect(inputs).toHaveLength(2); // 데스크톱 + 모바일 사이드바
    expect(screen.queryByRole('button', { name: '저장' })).not.toBeInTheDocument();

    await u.type(inputs[0], 'https://discord.com/api/webhooks/abc');
    // isWebhookEditing 은 페이지 공유 상태 — 저장 버튼도 양쪽 사이드바에 나타남
    expect(await screen.findAllByRole('button', { name: '저장' })).toHaveLength(2);

    // owner 전용 컨트롤 노출 (4번의 양성 케이스)
    expect(screen.getByText('공유 링크 관리')).toBeInTheDocument();
    expect(screen.getAllByText('프로젝트 멤버')).toHaveLength(2);
  });
});
