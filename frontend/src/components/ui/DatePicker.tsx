import { useState, useRef, useEffect } from 'react';
import { DayPicker } from 'react-day-picker';
import { format, parse, isValid } from 'date-fns';
import { ko } from 'date-fns/locale';
import 'react-day-picker/style.css';

interface DatePickerProps {
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
  placeholder?: string;
  align?: 'left' | 'right';
}

export function DatePicker({
  value,
  onChange,
  disabled = false,
  placeholder = '날짜 선택',
  align = 'left',
}: DatePickerProps) {
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  const selectedDate: Date | undefined = (() => {
    if (!value) return undefined;
    const parsed = parse(value, 'yyyy-MM-dd', new Date());
    return isValid(parsed) ? parsed : undefined;
  })();

  const displayValue = selectedDate
    ? format(selectedDate, 'yyyy. M. d', { locale: ko })
    : null;

  // disabled 로 전환되는 순간 닫음 — open 은 handleToggle(!disabled 가드)로만
  // true 가 되므로, 이 조건은 열린 채 disabled 가 된 전환 시점에만 참이다.
  if (disabled && open) {
    setOpen(false);
  }

  useEffect(() => {
    if (disabled) return;

    const handleMouseDown = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };

    document.addEventListener('mousedown', handleMouseDown);
    return () => document.removeEventListener('mousedown', handleMouseDown);
  }, [disabled]);

  useEffect(() => {
    if (!open) return;

    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setOpen(false);
      }
    };

    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [open]);

  const handleToggle = () => {
    if (!disabled) setOpen((prev) => !prev);
  };

  const handleSelect = (date: Date | undefined) => {
    if (date) {
      onChange(format(date, 'yyyy-MM-dd'));
    } else {
      onChange('');
    }
    setOpen(false);
  };

  const handleClear = () => {
    onChange('');
    setOpen(false);
  };

  return (
    <div ref={containerRef} className="relative">
      {/* Trigger */}
      <button
        type="button"
        onClick={handleToggle}
        disabled={disabled}
        className={[
          'w-full flex items-center justify-between rounded-xl border border-white/60 shadow-sm transition-all',
          disabled ? 'opacity-50 bg-white/50 cursor-not-allowed' : 'glass hover:bg-white/60 cursor-pointer',
        ].join(' ')}
      >
        <span
          className={[
            'px-3 py-2 text-sm font-bold flex-1 text-left',
            !displayValue ? 'text-brand-blue/50' : 'text-brand-blue',
          ].join(' ')}
        >
          {displayValue ?? placeholder}
        </span>
        <span className="px-3 py-2 border-l border-brand-blue/10 flex items-center text-brand-blue">
          {/* Calendar SVG icon */}
          <svg
            className="w-4 h-4"
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
            aria-hidden="true"
          >
            <rect x="3" y="4" width="18" height="18" rx="0" strokeWidth={2} />
            <line x1="3" y1="9" x2="21" y2="9" strokeWidth={2} />
            <line x1="8" y1="2" x2="8" y2="6" strokeWidth={2} strokeLinecap="round" />
            <line x1="16" y1="2" x2="16" y2="6" strokeWidth={2} strokeLinecap="round" />
          </svg>
        </span>
      </button>

      {/* Calendar popup */}
      {open && (
        <div
          className={`absolute top-full z-50 mt-1 glass-panel rounded-2xl p-4 ${
            align === 'right' ? 'right-0' : 'left-0'
          }`}
        >
          <DayPicker
            mode="single"
            selected={selectedDate}
            onSelect={handleSelect}
            locale={ko}
            classNames={{
              root: 'text-brand-blue relative',
              months: 'flex flex-col',
              month: 'space-y-4',
              month_caption: 'flex justify-center items-center pt-1 relative',
              caption_label: 'font-bold text-sm',
              nav: 'flex items-center justify-between absolute top-0 w-full',
              button_previous: 'p-1 hover:bg-brand-sky/20 transition-colors rounded-full cursor-pointer text-brand-blue',
              button_next: 'p-1 hover:bg-brand-sky/20 transition-colors rounded-full cursor-pointer text-brand-blue',
              chevron: 'w-4 h-4 fill-brand-blue',
              month_grid: 'w-full border-collapse',
              weekdays: 'flex mt-2',
              weekday: 'text-brand-blue/60 font-bold text-[11px] w-8 text-center pb-2 uppercase',
              weeks: '',
              week: 'flex w-full mt-1',
              day: 'w-8 h-8 text-center text-sm p-0 relative',
              day_button: 'w-8 h-8 font-medium hover:bg-brand-sky/20 rounded-full transition-colors flex items-center justify-center text-xs cursor-pointer',
              selected: 'bg-brand-blue text-white font-bold rounded-full hover:bg-brand-blue hover:text-white',
              today: 'text-brand-orange font-bold',
              outside: 'text-brand-blue/30 opacity-50',
              disabled: 'text-brand-blue/30 opacity-30 cursor-not-allowed',
            }}
          />
          {value && (
            <button
              type="button"
              onClick={handleClear}
              className="mt-4 w-full rounded-full border border-brand-blue/20 text-xs font-bold py-1.5 hover:bg-brand-sky/20 transition-colors text-brand-blue"
            >
              날짜 지우기
            </button>
          )}
        </div>
      )}
    </div>
  );
}
