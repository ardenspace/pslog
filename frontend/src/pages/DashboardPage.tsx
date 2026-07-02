import { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import { useAuth } from '@/hooks/useAuth';
import { ROUTES, SITE_NAME } from '@/constants';
import { useWorkspaces } from '@/hooks/useWorkspaces';
import { useAutoSelectFirst } from '@/hooks/useAutoSelectFirst';
import { useMobileSidebar } from '@/hooks/useMobileSidebar';
import { useMyProjects, useProjectMembers, useUpdateProject } from '@/hooks/useProjects';
import { useTasks, useDeleteTask, useUpdateTask, useWeekTasks } from '@/hooks/useTasks';
import { useSendDiscordSummary } from '@/hooks/useDiscord';
import { useUIStore } from '@/stores/uiStore';
import { Button } from '@/components/ui/button';
import { KanbanBoard } from '@/components/board/KanbanBoard';
import { BoardHeader } from '@/components/board/BoardHeader';
import { TaskModal } from '@/components/board/TaskModal';
import { CreateProjectModal } from '@/components/workspace/CreateProjectModal';
import { ProjectItem } from '@/components/sidebar/ProjectItem';
import { ProjectMemberManager } from '@/components/project/ProjectMemberManager';
import { WeekView, getMonday } from '@/components/week/WeekView';
import { TaskTableView } from '@/components/table/TaskTableView';
import { ShareLinkManager } from '@/components/share/ShareLinkManager';
import { AlertModal } from '@/components/ui/AlertModal';
import { ErrorsList } from '@/components/errors/ErrorsList';
import { ErrorDetail } from '@/components/errors/ErrorDetail';
import { DriftsList } from '@/components/drifts/DriftsList';
import type { Task } from '@/types/task';

type ViewMode = 'board' | 'table' | 'week' | 'errors' | 'drift';

export function DashboardPage() {
  const { user, logout } = useAuth();
  const {
    selectedWorkspaceId,
    selectedProjectId,
    setSelectedWorkspace,
    setSelectedProject,
    taskFilters,
  } = useUIStore();

  const { data: workspaces } = useWorkspaces();
  useAutoSelectFirst(workspaces, selectedWorkspaceId, setSelectedWorkspace);

  const currentWorkspace = workspaces?.find((ws) => ws.id === selectedWorkspaceId);

  const { data: projects } = useMyProjects();
  const selectedProject = projects?.find((p) => p.id === selectedProjectId);
  const myRole = selectedProject?.my_role ?? 'viewer';

  const { data: members } = useProjectMembers(selectedProjectId);
  const { data: tasks, isLoading } = useTasks(selectedProjectId, {
    mine_only: taskFilters.mineOnly,
  });
  const deleteTaskMutation = useDeleteTask();
  const updateTaskMutation = useUpdateTask();
  const discordMutation = useSendDiscordSummary(selectedProjectId);
  const updateProjectMutation = useUpdateProject(selectedProject?.workspace_id ?? '');

  const [webhookUrl, setWebhookUrl] = useState('');
  const [isWebhookEditing, setWebhookEditing] = useState(false);

  useEffect(() => {
    setWebhookUrl(selectedProject?.discord_webhook_url ?? '');
    setWebhookEditing(false);
  }, [selectedProject?.id, selectedProject?.discord_webhook_url]);

  const handleSaveWebhookUrl = () => {
    if (!selectedProjectId) return;
    updateProjectMutation.mutate(
      { projectId: selectedProjectId, data: { discord_webhook_url: webhookUrl || null } },
      {
        onSuccess: () => setWebhookEditing(false),
      }
    );
  };

  const [viewMode, setViewMode] = useState<ViewMode>('board');
  const [weekStart, setWeekStart] = useState(() => getMonday(new Date()));
  const [isCreateTaskModalOpen, setCreateTaskModalOpen] = useState(false);
  const [isCreateProjectModalOpen, setCreateProjectModalOpen] = useState(false);
  const [isProjectMemberModalOpen, setProjectMemberModalOpen] = useState(false);
  const [isShareManagerOpen, setShareManagerOpen] = useState(false);
  const [selectedTask, setSelectedTask] = useState<Task | null>(null);
  const [selectedErrorGroupId, setSelectedErrorGroupId] = useState<string | null>(null);
  const [alertMessage, setAlertMessage] = useState<string | null>(null);
  const {
    isOpen: isMobileSidebarOpen,
    close: closeMobileSidebar,
    toggle: toggleMobileSidebar,
  } = useMobileSidebar();

  const weekStartStr = weekStart.toISOString().split('T')[0];
  const { data: weekTasks, isLoading: isWeekLoading } = useWeekTasks(
    viewMode === 'week' ? weekStartStr : null
  );

  useAutoSelectFirst(projects, selectedProjectId, setSelectedProject);

  useEffect(() => {
    // 프로젝트 변경 시 선택된 에러 그룹 초기화.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setSelectedErrorGroupId(null);
  }, [selectedProjectId]);

  const handleDeleteTask = (taskId: string) => {
    deleteTaskMutation.mutate(taskId);
  };

  const handleProjectSelect = (projectId: string) => {
    setSelectedProject(projectId);
    closeMobileSidebar();
  };

  const sidebarContent = (
    <>
      <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-3 truncate">
        {currentWorkspace?.name ?? '워크스페이스'}
      </p>

      <div className="flex flex-col flex-1 min-h-0 overflow-hidden">
        <p className="text-xs text-muted-foreground mb-1 font-medium">프로젝트</p>

        <ul className="flex flex-col gap-1 overflow-y-auto overflow-x-hidden pb-1 flex-1 min-h-0">
          {projects?.map((project) => (
            <ProjectItem
              key={project.id}
              project={project}
              isSelected={project.id === selectedProjectId}
              workspaceId={project.workspace_id}
              onSelect={handleProjectSelect}
            />
          ))}
          {!projects?.length && (
            <li>
              <p className="text-xs text-muted-foreground px-2 py-1.5 italic">프로젝트 없음</p>
            </li>
          )}
        </ul>
      </div>

      <div className="mt-auto pt-3 space-y-2 border-t border-brand-blue/10">
        {selectedWorkspaceId && (
          <button
            className="text-xs sm:text-sm text-brand-blue/70 hover:text-brand-blue flex items-center justify-center gap-1 px-2 py-2 rounded-xl border border-dashed border-brand-blue/30 hover:border-brand-blue hover:bg-brand-sky/20 transition-all font-medium w-full md:w-full"
            onClick={() => {
              setCreateProjectModalOpen(true);
              closeMobileSidebar();
            }}
          >
            <span className="text-base leading-none">+</span>
            <span>새 프로젝트</span>
          </button>
        )}

        {selectedProjectId && myRole === 'owner' && (
          <button
            className="text-xs sm:text-sm text-brand-blue/70 hover:text-brand-blue flex items-center justify-center gap-1 px-2 py-2 rounded-xl border border-dashed border-brand-blue/30 hover:border-brand-blue hover:bg-brand-sky/20 transition-all font-medium w-full md:w-full"
            onClick={() => {
              setProjectMemberModalOpen(true);
              closeMobileSidebar();
            }}
          >
            <span>프로젝트 멤버</span>
          </button>
        )}

        {selectedProjectId && myRole === 'owner' && (
          <div className="space-y-2">
            <div className="space-y-1">
              <label className="text-[10px] sm:text-xs font-medium text-brand-blue/70">
                Discord Webhook URL
              </label>
              <input
                type="url"
                placeholder="https://discord.com/api/webhooks/..."
                value={webhookUrl}
                onChange={(e) => {
                  setWebhookUrl(e.target.value);
                  setWebhookEditing(true);
                }}
                className="w-full text-xs px-2 py-1.5 border border-brand-blue/20 rounded-lg focus:border-brand-blue focus:ring-2 focus:ring-brand-blue/20 focus:outline-none bg-white/50 backdrop-blur-sm"
              />
              {isWebhookEditing && (
                <button
                  className="text-xs font-bold px-2 py-1.5 border border-brand-neon bg-brand-neon text-brand-coffee hover:brightness-110 rounded-lg transition-all w-full shadow-sm"
                  disabled={updateProjectMutation.isPending}
                  onClick={handleSaveWebhookUrl}
                >
                  {updateProjectMutation.isPending ? '저장 중...' : '저장'}
                </button>
              )}
            </div>
            <button
              className="text-xs sm:text-sm text-brand-blue/70 hover:text-brand-blue flex items-center justify-center gap-1 px-2 py-2 rounded-xl border border-dashed border-brand-blue/30 hover:border-brand-blue hover:bg-brand-sky/20 transition-all font-medium w-full md:w-full"
              disabled={discordMutation.isPending}
              onClick={() => {
                discordMutation.mutate(undefined, {
                  onSuccess: () => setAlertMessage('Discord 리포트가 전송되었습니다.'),
                  onError: (err) => {
                    const message = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
                      ?? 'Discord 리포트 전송에 실패했습니다.';
                    setAlertMessage(message);
                  },
                });
                closeMobileSidebar();
              }}
            >
              <span>{discordMutation.isPending ? '전송 중...' : 'Discord 리포트'}</span>
            </button>
          </div>
        )}
      </div>
    </>
  );

  return (
    <div className="min-h-screen md:h-screen flex flex-col bg-brand-cream overflow-hidden">
      {/* Header */}
      <header className="glass-panel flex-shrink-0 z-20 border-b border-white/40">
        <div className="px-4 py-3 sm:px-6">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:gap-6">
              <h1 className="text-xl sm:text-2xl font-bold tracking-tight text-brand-blue">{SITE_NAME}</h1>
              <div className="flex w-full sm:w-auto rounded-full p-1 bg-white/50 backdrop-blur-md border border-white/60 shadow-sm gap-1">
                <button
                  className={`flex-1 sm:flex-none px-3 sm:px-4 py-1.5 text-[12px] sm:text-sm font-medium transition-all rounded-full ${
                    viewMode === 'board'
                      ? 'bg-brand-blue text-white shadow-md'
                      : 'text-brand-blue hover:bg-white/60'
                  }`}
                  onClick={() => setViewMode('board')}
                >
                  Board
                </button>
                <button
                  className={`flex-1 sm:flex-none px-3 sm:px-4 py-1.5 text-[12px] sm:text-sm font-medium transition-all rounded-full ${
                    viewMode === 'table'
                      ? 'bg-brand-blue text-white shadow-md'
                      : 'text-brand-blue hover:bg-white/60'
                  }`}
                  onClick={() => setViewMode('table')}
                >
                  Table
                </button>
                <button
                  className={`flex-1 sm:flex-none px-3 sm:px-4 py-1.5 text-[12px] sm:text-sm font-medium transition-all rounded-full ${
                    viewMode === 'week'
                      ? 'bg-brand-blue text-white shadow-md'
                      : 'text-brand-blue hover:bg-white/60'
                  }`}
                  onClick={() => setViewMode('week')}
                >
                  Week
                </button>
                <button
                  className={`flex-1 sm:flex-none px-3 sm:px-4 py-1.5 text-[12px] sm:text-sm font-medium transition-all rounded-full ${
                    viewMode === 'errors'
                      ? 'bg-brand-blue text-white shadow-md'
                      : 'text-brand-blue hover:bg-white/60'
                  }`}
                  onClick={() => setViewMode('errors')}
                >
                  Errors
                </button>
                <button
                  className={`flex-1 sm:flex-none px-3 sm:px-4 py-1.5 text-[12px] sm:text-sm font-medium transition-all rounded-full ${
                    viewMode === 'drift'
                      ? 'bg-brand-blue text-white shadow-md'
                      : 'text-brand-blue hover:bg-white/60'
                  }`}
                  onClick={() => setViewMode('drift')}
                >
                  Drift
                </button>
              </div>
            </div>
            <div className="flex items-center justify-between gap-4 sm:justify-end">
              <button
                type="button"
                onClick={toggleMobileSidebar}
                className="md:hidden glass px-3 py-1.5 rounded-lg text-xs font-medium text-brand-blue"
              >
                메뉴
              </button>
              <span className="text-sm font-medium text-brand-blue/80 truncate max-w-[110px] sm:max-w-none">
                {user?.name}
                {user?.username && (
                  <span className="ml-1 text-xs font-mono text-brand-blue/60">
                    @{user.username}
                  </span>
                )}
              </span>
              <Link
                to={ROUTES.SETTINGS}
                className="rounded-full bg-white/40 hover:bg-brand-blue hover:text-white text-brand-blue font-medium px-4 py-1.5 transition-all shadow-sm border border-white/50 text-sm"
              >
                설정
              </Link>
              <Button
                variant="ghost"
                size="sm"
                onClick={logout}
                className="rounded-full bg-white/40 hover:bg-brand-orange hover:text-white text-brand-blue font-medium px-4 py-1.5 transition-all shadow-sm border border-white/50"
              >
                로그아웃
              </Button>
            </div>
          </div>
        </div>
      </header>

      {/* Body: Sidebar + Main */}
      <div className="relative flex flex-1 min-h-0 overflow-hidden">
        <aside className="hidden md:flex w-64 glass-panel border-r border-white/40 flex-shrink-0 flex-col p-5 min-h-0 z-10 relative">
          <div className="absolute inset-0 bg-gradient-to-b from-white/20 to-transparent pointer-events-none rounded-r-2xl" />
          <div className="relative z-10 flex flex-col h-full">
            {sidebarContent}
          </div>
        </aside>

        <div
          className={`fixed inset-0 z-40 md:hidden transition ${
            isMobileSidebarOpen ? 'pointer-events-auto' : 'pointer-events-none'
          }`}
        >
          <button
            type="button"
            aria-label="사이드바 닫기"
            onClick={closeMobileSidebar}
            className={`absolute inset-0 bg-brand-coffee/20 backdrop-blur-sm transition-opacity ${
              isMobileSidebarOpen ? 'opacity-100' : 'opacity-0'
            }`}
          />
          <aside
            className={`absolute left-0 top-0 h-full w-[82vw] max-w-xs glass-panel border-r border-white/40 p-4 flex flex-col min-h-0 transition-transform duration-300 ease-out ${
              isMobileSidebarOpen ? 'translate-x-0' : '-translate-x-full'
            }`}
          >
            {sidebarContent}
          </aside>
        </div>

        {/* Main content */}
        <main className="flex-1 min-h-0 overflow-auto p-4 sm:p-6 md:p-8">
          {viewMode === 'board' || viewMode === 'table' ? (
            !selectedProjectId ? (
              <div className="flex items-center justify-center h-full">
                <div className="text-center glass-panel p-8 sm:p-12 rounded-2xl max-w-sm">
                  <p className="text-brand-blue/70 font-medium text-sm sm:text-base">
                    ← 왼쪽에서 프로젝트를 선택하세요.
                  </p>
                </div>
              </div>
            ) : isLoading ? (
              <div className="flex items-center justify-center h-full">
                <p className="text-muted-foreground font-medium">로딩 중...</p>
              </div>
            ) : (
              <>
                <BoardHeader
                  projectName={selectedProject?.name || ''}
                  onCreateTask={() => setCreateTaskModalOpen(true)}
                />
                {myRole === 'owner' && (
                  <div className="mb-4">
                    <Button
                      type="button"
                      variant="outline"
                      className="rounded-full bg-white/40 border border-brand-blue/20 text-brand-blue hover:bg-white/80 font-bold text-xs sm:text-sm w-full sm:w-auto shadow-sm"
                      onClick={() => setShareManagerOpen(true)}
                    >
                      공유 링크 관리
                    </Button>
                  </div>
                )}
                {viewMode === 'board' ? (
                  <KanbanBoard
                    tasks={tasks || []}
                    onTaskClick={(task) => setSelectedTask(task)}
                    onTaskStatusChange={(taskId, newStatus) => {
                      updateTaskMutation.mutate({ taskId, data: { status: newStatus } });
                    }}
                    isDragDisabled={myRole === 'viewer'}
                  />
                ) : (
                  <TaskTableView
                    tasks={tasks || []}
                    onTaskClick={(task) => setSelectedTask(task)}
                  />
                )}
              </>
            )
          ) : viewMode === 'errors' ? (
            !selectedProjectId ? (
              <div className="flex items-center justify-center h-full">
                <div className="text-center glass-panel p-8 sm:p-12 rounded-2xl max-w-sm">
                  <p className="text-brand-blue/70 font-medium text-sm sm:text-base">
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
          ) : viewMode === 'drift' ? (
            !selectedProjectId ? (
              <div className="flex items-center justify-center h-full">
                <div className="text-center glass-panel p-8 sm:p-12 rounded-2xl max-w-sm">
                  <p className="text-brand-blue/70 font-medium text-sm sm:text-base">
                    ← 왼쪽에서 프로젝트를 선택하세요.
                  </p>
                </div>
              </div>
            ) : (
              <DriftsList
                projectId={selectedProjectId}
                canEdit={myRole === 'owner' || myRole === 'editor'}
              />
            )
          ) : isWeekLoading ? (
            <div className="flex items-center justify-center h-full">
              <p className="text-muted-foreground font-medium">로딩 중...</p>
            </div>
          ) : (
            <>
              <h2 className="text-lg sm:text-xl font-black mb-4">내 주간 태스크</h2>
              <WeekView
                tasks={weekTasks || []}
                weekStart={weekStart}
                onWeekChange={setWeekStart}
                onTaskClick={(task) => setSelectedTask(task)}
              />
            </>
          )}
        </main>
      </div>

      {/* Modals */}
      {selectedProjectId && user && (
        <TaskModal
          mode="create"
          projectId={selectedProjectId}
          members={members || []}
          currentUserId={user.id}
          isOpen={isCreateTaskModalOpen}
          onClose={() => setCreateTaskModalOpen(false)}
        />
      )}

      {selectedWorkspaceId && (
        <CreateProjectModal
          workspaceId={selectedWorkspaceId}
          isOpen={isCreateProjectModalOpen}
          onClose={() => setCreateProjectModalOpen(false)}
        />
      )}

      {selectedProjectId && user && myRole === 'owner' && (
        <ProjectMemberManager
          projectId={selectedProjectId}
          currentUserId={user.id}
          isOpen={isProjectMemberModalOpen}
          onClose={() => setProjectMemberModalOpen(false)}
        />
      )}

      {selectedProjectId && myRole === 'owner' && (
        <ShareLinkManager
          projectId={selectedProjectId}
          projectName={selectedProject?.name || ''}
          isOpen={isShareManagerOpen}
          onClose={() => setShareManagerOpen(false)}
        />
      )}

      <TaskModal
        mode="edit"
        task={selectedTask}
        myRole={myRole}
        members={members || []}
        isOpen={!!selectedTask}
        onClose={() => setSelectedTask(null)}
        onDelete={handleDeleteTask}
      />

      <AlertModal
        isOpen={!!alertMessage}
        title="알림"
        description={alertMessage ?? undefined}
        onClose={() => setAlertMessage(null)}
      />
    </div>
  );
}
