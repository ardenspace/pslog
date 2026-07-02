import { useState, useEffect } from 'react';
import { useAuth } from '@/hooks/useAuth';
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
import { ProjectMemberManager } from '@/components/project/ProjectMemberManager';
import { WeekView } from '@/components/week/WeekView';
import { getMonday } from '@/components/week/weekUtils';
import { TaskTableView } from '@/components/table/TaskTableView';
import { ShareLinkManager } from '@/components/share/ShareLinkManager';
import { AlertModal } from '@/components/ui/AlertModal';
import { ErrorsList } from '@/components/errors/ErrorsList';
import { ErrorDetail } from '@/components/errors/ErrorDetail';
import { DriftsList } from '@/components/drifts/DriftsList';
import { DashboardHeader } from '@/components/dashboard/DashboardHeader';
import { DashboardSidebar } from '@/components/dashboard/DashboardSidebar';
import { SelectProjectPlaceholder } from '@/components/dashboard/SelectProjectPlaceholder';
import type { Task } from '@/types/task';
import type { ViewMode } from '@/types/view';

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
  useAutoSelectFirst(projects, selectedProjectId, setSelectedProject);
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

  // webhook 편집 상태 — 데스크톱/모바일 사이드바 두 인스턴스가 공유하므로 페이지 소유.
  // 원본 effect 의 리셋 트리거 3종(프로젝트 전환 / 저장 성공 refetch / 서버 값 외부 변경)을
  // effect 없이 파생으로 재현: draft 는 생성 시점 서버 값(baseUrl)이 유지되는 동안만 유효.
  const [webhookDraft, setWebhookDraft] = useState<{
    projectId: string;
    value: string;
    saved: boolean;
    baseUrl: string;
  } | null>(null);
  const serverWebhookUrl = selectedProject?.discord_webhook_url ?? '';
  const isDraftForCurrent =
    selectedProject != null &&
    webhookDraft?.projectId === selectedProject.id &&
    webhookDraft.baseUrl === serverWebhookUrl;
  const webhookUrl = isDraftForCurrent ? webhookDraft.value : serverWebhookUrl;
  const isWebhookEditing = isDraftForCurrent && !webhookDraft.saved;

  const handleChangeWebhookUrl = (value: string) => {
    if (!selectedProjectId) return;
    setWebhookDraft({
      projectId: selectedProjectId,
      value,
      saved: false,
      baseUrl: serverWebhookUrl,
    });
  };

  const handleSaveWebhookUrl = () => {
    if (!selectedProjectId) return;
    updateProjectMutation.mutate(
      { projectId: selectedProjectId, data: { discord_webhook_url: webhookUrl || null } },
      {
        onSuccess: () =>
          setWebhookDraft((draft) => (draft ? { ...draft, saved: true } : null)),
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
    setWebhookDraft(null);
    closeMobileSidebar();
  };

  const handleSendDiscordReport = () => {
    discordMutation.mutate(undefined, {
      onSuccess: () => setAlertMessage('Discord 리포트가 전송되었습니다.'),
      onError: (err) => {
        const message = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
          ?? 'Discord 리포트 전송에 실패했습니다.';
        setAlertMessage(message);
      },
    });
    closeMobileSidebar();
  };

  const sidebarContent = (
    <DashboardSidebar
      workspaceName={currentWorkspace?.name}
      projects={projects}
      selectedWorkspaceId={selectedWorkspaceId}
      selectedProjectId={selectedProjectId}
      isOwner={myRole === 'owner'}
      onProjectSelect={handleProjectSelect}
      onCreateProject={() => {
        setCreateProjectModalOpen(true);
        closeMobileSidebar();
      }}
      onManageMembers={() => {
        setProjectMemberModalOpen(true);
        closeMobileSidebar();
      }}
      webhook={{
        webhookUrl,
        isEditing: isWebhookEditing,
        isSaving: updateProjectMutation.isPending,
        isSendingReport: discordMutation.isPending,
        onChange: handleChangeWebhookUrl,
        onSave: handleSaveWebhookUrl,
        onSendReport: handleSendDiscordReport,
      }}
    />
  );

  return (
    <div className="min-h-screen md:h-screen flex flex-col bg-brand-cream overflow-hidden">
      <DashboardHeader
        viewMode={viewMode}
        onViewModeChange={setViewMode}
        user={user}
        onToggleMobileSidebar={toggleMobileSidebar}
        onLogout={logout}
      />

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
              <SelectProjectPlaceholder />
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
              <SelectProjectPlaceholder />
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
              <SelectProjectPlaceholder />
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
