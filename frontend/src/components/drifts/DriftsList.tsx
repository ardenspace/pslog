import { useState } from 'react';
import { useDrifts, useTransitionDriftStatus } from '@/hooks/useDrifts';
import type { DriftStatus, DriftSummary, DriftType } from '@/types/drift';

interface DriftsListProps {
  projectId: string;
  canEdit: boolean;
}

const STATUS_FILTER_OPTIONS: Array<{ value: DriftStatus | 'all'; label: string }> = [
  { value: 'all', label: '전체' },
  { value: 'open', label: 'OPEN' },
  { value: 'resolved', label: 'RESOLVED' },
  { value: 'ignored', label: 'IGNORED' },
];

const STATUS_BADGE: Record<DriftStatus, string> = {
  open: 'bg-brand-orange/20 text-brand-coffee border-brand-orange/50',
  resolved: 'bg-brand-neon/20 text-brand-coffee border-brand-neon/50',
  ignored: 'bg-black/5 text-brand-blue border-brand-blue/20',
};

const TYPE_LABEL: Record<DriftType, string> = {
  decision_not_promoted: '결정 미승격',
  status_contradiction: '상태 모순',
  task_not_prepared: '태스크 미준비',
};

function DriftRow({
  drift,
  canEdit,
  onAction,
  pending,
}: {
  drift: DriftSummary;
  canEdit: boolean;
  onAction: (action: 'ignore' | 'reopen') => void;
  pending: boolean;
}) {
  return (
    <div className="glass border-white/60 shadow-sm rounded-xl p-3 sm:p-4">
      <div className="flex items-start gap-3 flex-wrap">
        <span
          className={`px-2 py-0.5 text-[10px] font-bold uppercase border rounded-md ${
            STATUS_BADGE[drift.status]
          }`}
        >
          {drift.status}
        </span>
        <span className="px-2 py-0.5 text-[10px] font-bold border rounded-md bg-white/50 text-brand-blue border-brand-blue/20">
          {TYPE_LABEL[drift.type]}
        </span>
        <h4 className="font-bold text-xs sm:text-sm text-brand-blue break-words flex-1 min-w-0">
          {drift.branch}
          {drift.external_id && (
            <span className="text-brand-blue/60"> · {drift.external_id}</span>
          )}
        </h4>
      </div>
      <p className="mt-2 text-[12px] text-brand-blue/90 break-words">{drift.detail}</p>
      <div className="mt-2 flex items-center justify-between gap-3">
        <p className="text-[10px] text-brand-blue/60">
          최초: {new Date(drift.opened_at).toLocaleString()}
        </p>
        {canEdit && (
          <div className="flex gap-1.5">
            {drift.status !== 'ignored' && (
              <button
                type="button"
                disabled={pending}
                onClick={() => onAction('ignore')}
                className="px-3 py-1 text-[11px] font-bold rounded-full border border-brand-blue/20 bg-white/50 text-brand-blue hover:bg-white/70 disabled:opacity-50"
              >
                무시
              </button>
            )}
            {drift.status !== 'open' && (
              <button
                type="button"
                disabled={pending}
                onClick={() => onAction('reopen')}
                className="px-3 py-1 text-[11px] font-bold rounded-full border border-brand-orange/50 bg-brand-orange/10 text-brand-coffee hover:brightness-105 disabled:opacity-50"
              >
                다시 열기
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

export function DriftsList({ projectId, canEdit }: DriftsListProps) {
  const [statusFilter, setStatusFilter] = useState<DriftStatus | 'all'>('open');
  const apiStatus = statusFilter === 'all' ? undefined : statusFilter;
  const { data, isLoading, error } = useDrifts(projectId, { status: apiStatus });
  const transition = useTransitionDriftStatus(projectId);

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-1.5">
        {STATUS_FILTER_OPTIONS.map((opt) => (
          <button
            key={opt.value}
            type="button"
            onClick={() => setStatusFilter(opt.value)}
            className={`px-3 py-1.5 text-[12px] sm:text-sm font-medium transition-all rounded-full ${
              statusFilter === opt.value
                ? 'bg-brand-blue text-white shadow-md'
                : 'bg-white/50 text-brand-blue hover:bg-white/60 border border-white/60 shadow-sm'
            }`}
          >
            {opt.label}
          </button>
        ))}
      </div>

      {isLoading && <p className="text-muted-foreground font-medium">로딩 중...</p>}
      {error && (
        <p className="text-brand-orange font-bold text-sm">
          드리프트 목록을 불러오지 못했습니다.
        </p>
      )}
      {data && data.items.length === 0 && (
        <div className="border-2 border-dashed border-muted-foreground rounded p-6 text-center">
          <p className="text-muted-foreground font-medium text-sm">
            {statusFilter === 'open'
              ? '열린 드리프트가 없습니다. 🎉'
              : statusFilter === 'all'
                ? '드리프트가 없습니다. 🎉'
                : `${statusFilter.toUpperCase()} 상태 드리프트가 없습니다.`}
          </p>
        </div>
      )}
      {data && data.items.length > 0 && (
        <>
          <p className="text-[11px] text-muted-foreground">
            {data.items.length} / 총 {data.total} 건
          </p>
          <ul className="space-y-2">
            {data.items.map((drift) => (
              <li key={drift.id}>
                <DriftRow
                  drift={drift}
                  canEdit={canEdit}
                  pending={transition.isPending}
                  onAction={(action) =>
                    transition.mutate({ driftId: drift.id, action })
                  }
                />
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}
