import axios from 'axios';
import type {
  AuthResponse,
  LoginRequest,
  RegisterRequest,
  User,
  UserUpdateRequest,
  Task,
  TaskCreate,
  TaskUpdate,
  Workspace,
  WorkspaceCreate,
  Project,
  ProjectCreate,
  ProjectMember,
  AddProjectMemberRequest,
  UpdateProjectMemberRequest,
  ShareLink,
  ShareLinkCreateRequest,
  GitSettings,
  GitSettingsUpdate,
  GitEventSummary,
  WebhookRegisterResponse,
  HandoffSummary,
  ReprocessResponse,
  ErrorGroupListResponse,
  ErrorGroupDetail,
  ErrorGroupStatus,
  ErrorGroupStatusUpdateRequest,
  ErrorGroupSummary,
  DriftListResponse,
  DriftSummary,
  DriftStatus,
  DriftStatusUpdateRequest,
  LogEventListResponse,
  LogLevel,
} from '@/types';

const apiClient = axios.create({
  baseURL: import.meta.env.VITE_API_URL || 'http://localhost:8000/api/v1',
  headers: {
    'Content-Type': 'application/json',
  },
});

// Request interceptor - 토큰 자동 첨부
apiClient.interceptors.request.use((config) => {
  const token = localStorage.getItem('access_token');
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// Response interceptor - 401 에러 처리
apiClient.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      localStorage.removeItem('access_token');
      window.location.href = '/login';
    }
    return Promise.reject(error);
  }
);

export const api = {
  auth: {
    register: (data: RegisterRequest) =>
      apiClient.post<AuthResponse>('/auth/register', data),
    login: (data: LoginRequest) =>
      apiClient.post<AuthResponse>('/auth/login', data),
    me: () => apiClient.get<User>('/auth/me'),
    updateMe: (data: UserUpdateRequest) =>
      apiClient.patch<User>('/auth/me', data),
    logout: () => apiClient.post('/auth/logout'),
  },

  workspaces: {
    list: () => apiClient.get<Workspace[]>('/workspaces'),
    create: (data: WorkspaceCreate) => apiClient.post<Workspace>('/workspaces', data),
    get: (id: string) => apiClient.get<Workspace>(`/workspaces/${id}`),
    update: (id: string, data: { name?: string; description?: string }) =>
      apiClient.patch<Workspace>(`/workspaces/${id}`, data),
    delete: (id: string) => apiClient.delete(`/workspaces/${id}`),
  },

  projects: {
    listMine: () => apiClient.get<Project[]>('/projects'),
    list: (workspaceId: string) =>
      apiClient.get<Project[]>(`/workspaces/${workspaceId}/projects`),
    create: (workspaceId: string, data: ProjectCreate) =>
      apiClient.post<Project>(`/workspaces/${workspaceId}/projects`, data),
    update: (workspaceId: string, projectId: string, data: { name?: string; description?: string; discord_webhook_url?: string | null }) =>
      apiClient.patch<Project>(`/workspaces/${workspaceId}/projects/${projectId}`, data),
    delete: (workspaceId: string, projectId: string) =>
      apiClient.delete(`/workspaces/${workspaceId}/projects/${projectId}`),
    get: (id: string) => apiClient.get<Project>(`/projects/${id}`),
    getMembers: (projectId: string) => apiClient.get<ProjectMember[]>(`/projects/${projectId}/members`),
    addMember: (projectId: string, data: AddProjectMemberRequest) =>
      apiClient.post<ProjectMember>(`/projects/${projectId}/members`, data),
    updateMemberRole: (projectId: string, userId: string, data: UpdateProjectMemberRequest) =>
      apiClient.patch<ProjectMember>(`/projects/${projectId}/members/${userId}`, data),
    removeMember: (projectId: string, userId: string) =>
      apiClient.delete(`/projects/${projectId}/members/${userId}`),
  },

  shareLinks: {
    list: (projectId: string) => apiClient.get<ShareLink[]>(`/projects/${projectId}/share-links`),
    create: (projectId: string, data?: ShareLinkCreateRequest) =>
      apiClient.post<ShareLink>(`/projects/${projectId}/share-links`, data ?? {}),
    deactivate: (shareLinkId: string) =>
      apiClient.patch<ShareLink>(`/share-links/${shareLinkId}/deactivate`),
    activate: (shareLinkId: string) =>
      apiClient.patch<ShareLink>(`/share-links/${shareLinkId}/activate`),
    delete: (shareLinkId: string) => apiClient.delete(`/share-links/${shareLinkId}`),
  },

  discord: {
    sendSummary: (projectId: string) =>
      apiClient.post<{ message: string }>(`/projects/${projectId}/discord-summary`),
  },

  git: {
    getSettings: (projectId: string) =>
      apiClient.get<GitSettings>(`/projects/${projectId}/git-settings`),
    updateSettings: (projectId: string, data: GitSettingsUpdate) =>
      apiClient.patch<GitSettings>(`/projects/${projectId}/git-settings`, data),
    registerWebhook: (projectId: string) =>
      apiClient.post<WebhookRegisterResponse>(`/projects/${projectId}/git-settings/webhook`),
    listHandoffs: (projectId: string, params?: { branch?: string; limit?: number }) =>
      apiClient.get<HandoffSummary[]>(`/projects/${projectId}/handoffs`, { params }),
    listGitEvents: (
      projectId: string,
      params?: { failed_only?: boolean; limit?: number },
    ) =>
      apiClient.get<GitEventSummary[]>(`/projects/${projectId}/git-events`, { params }),
    reprocessEvent: (projectId: string, eventId: string) =>
      apiClient.post<ReprocessResponse>(`/projects/${projectId}/git-events/${eventId}/reprocess`),
    resetDiscord: (projectId: string) =>
      apiClient.post<GitSettings>(`/projects/${projectId}/git-settings/discord-reset`),
  },

  tasks: {
    list: (projectId: string, filters?: { mine_only?: boolean; status?: string }) =>
      apiClient.get<Task[]>(`/projects/${projectId}/tasks`, { params: filters }),
    getWeek: (weekStart: string) =>
      apiClient.get<Task[]>('/tasks/week', { params: { week_start: weekStart } }),
    getById: (id: string) =>
      apiClient.get<Task>(`/tasks/${id}`),
    create: (projectId: string, data: TaskCreate) =>
      apiClient.post<Task>(`/projects/${projectId}/tasks`, data),
    update: (id: string, data: TaskUpdate) =>
      apiClient.put<Task>(`/tasks/${id}`, data),
    delete: (id: string) =>
      apiClient.delete(`/tasks/${id}`),
  },

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

  drifts: {
    list: (projectId: string, params?: { status?: DriftStatus }) =>
      apiClient.get<DriftListResponse>(`/projects/${projectId}/drifts`, { params }),
    transition: (
      projectId: string,
      driftId: string,
      data: DriftStatusUpdateRequest,
    ) =>
      apiClient.patch<DriftSummary>(
        `/projects/${projectId}/drifts/${driftId}`,
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
};

export default apiClient;
