import { Link } from 'react-router-dom';
import { ROUTES, SITE_NAME } from '@/constants';
import { Button } from '@/components/ui/button';
import { ViewSwitcher } from '@/components/dashboard/ViewSwitcher';
import type { User } from '@/types/user';
import type { ViewMode } from '@/types/view';

interface DashboardHeaderProps {
  viewMode: ViewMode;
  onViewModeChange: (mode: ViewMode) => void;
  user: User | null | undefined;
  onToggleMobileSidebar: () => void;
  onLogout: () => void;
}

export function DashboardHeader({
  viewMode,
  onViewModeChange,
  user,
  onToggleMobileSidebar,
  onLogout,
}: DashboardHeaderProps) {
  return (
    <header className="glass-panel flex-shrink-0 z-20 border-b border-white/40">
      <div className="px-4 py-3 sm:px-6">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:gap-6">
            <h1 className="text-xl sm:text-2xl font-bold tracking-tight text-brand-blue">{SITE_NAME}</h1>
            <ViewSwitcher viewMode={viewMode} onChange={onViewModeChange} />
          </div>
          <div className="flex items-center justify-between gap-4 sm:justify-end">
            <button
              type="button"
              onClick={onToggleMobileSidebar}
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
              onClick={onLogout}
              className="rounded-full bg-white/40 hover:bg-brand-orange hover:text-white text-brand-blue font-medium px-4 py-1.5 transition-all shadow-sm border border-white/50"
            >
              로그아웃
            </Button>
          </div>
        </div>
      </div>
    </header>
  );
}
