import type { ViewMode } from '@/types/view';

const VIEW_MODES: Array<{ mode: ViewMode; label: string }> = [
  { mode: 'board', label: 'Board' },
  { mode: 'table', label: 'Table' },
  { mode: 'week', label: 'Week' },
  { mode: 'errors', label: 'Errors' },
  { mode: 'drift', label: 'Drift' },
];

interface ViewSwitcherProps {
  viewMode: ViewMode;
  onChange: (mode: ViewMode) => void;
}

export function ViewSwitcher({ viewMode, onChange }: ViewSwitcherProps) {
  return (
    <div className="flex w-full sm:w-auto rounded-full p-1 bg-white/50 backdrop-blur-md border border-white/60 shadow-sm gap-1">
      {VIEW_MODES.map(({ mode, label }) => (
        <button
          key={mode}
          className={`flex-1 sm:flex-none px-3 sm:px-4 py-1.5 text-[12px] sm:text-sm font-medium transition-all rounded-full ${
            viewMode === mode
              ? 'bg-brand-blue text-white shadow-md'
              : 'text-brand-blue hover:bg-white/60'
          }`}
          onClick={() => onChange(mode)}
        >
          {label}
        </button>
      ))}
    </div>
  );
}
