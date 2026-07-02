import { ProjectItem } from '@/components/sidebar/ProjectItem';
import {
  WebhookSettings,
  type WebhookSettingsProps,
} from '@/components/dashboard/WebhookSettings';
import type { Project } from '@/types/project';

interface DashboardSidebarProps {
  workspaceName: string | undefined;
  projects: Project[] | undefined;
  selectedWorkspaceId: string | null;
  selectedProjectId: string | null;
  isOwner: boolean;
  onProjectSelect: (projectId: string) => void;
  onCreateProject: () => void;
  onManageMembers: () => void;
  webhook: WebhookSettingsProps;
}

/**
 * 사이드바 내용 — 데스크톱 aside 와 모바일 드로어 양쪽에서 렌더된다 (상태는 페이지 소유).
 */
export function DashboardSidebar({
  workspaceName,
  projects,
  selectedWorkspaceId,
  selectedProjectId,
  isOwner,
  onProjectSelect,
  onCreateProject,
  onManageMembers,
  webhook,
}: DashboardSidebarProps) {
  return (
    <>
      <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-3 truncate">
        {workspaceName ?? '워크스페이스'}
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
              onSelect={onProjectSelect}
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
            onClick={onCreateProject}
          >
            <span className="text-base leading-none">+</span>
            <span>새 프로젝트</span>
          </button>
        )}

        {selectedProjectId && isOwner && (
          <button
            className="text-xs sm:text-sm text-brand-blue/70 hover:text-brand-blue flex items-center justify-center gap-1 px-2 py-2 rounded-xl border border-dashed border-brand-blue/30 hover:border-brand-blue hover:bg-brand-sky/20 transition-all font-medium w-full md:w-full"
            onClick={onManageMembers}
          >
            <span>프로젝트 멤버</span>
          </button>
        )}

        {selectedProjectId && isOwner && <WebhookSettings {...webhook} />}
      </div>
    </>
  );
}
