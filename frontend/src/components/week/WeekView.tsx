import { useMemo } from 'react';
import { WeekRow } from './WeekRow';
import { getMonday } from './weekUtils';
import type { Task } from '@/types/task';

interface WeekViewProps {
  tasks: Task[];
  weekStart: Date;
  onWeekChange: (newStart: Date) => void;
  onTaskClick: (task: Task) => void;
}

function getWeekDates(start: Date): Date[] {
  const dates: Date[] = [];
  for (let i = 0; i < 7; i++) {
    const date = new Date(start);
    date.setDate(start.getDate() + i);
    dates.push(date);
  }
  return dates;
}

function toLocalDateKey(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

function isSameDay(d1: Date, d2: Date): boolean {
  return (
    d1.getFullYear() === d2.getFullYear() &&
    d1.getMonth() === d2.getMonth() &&
    d1.getDate() === d2.getDate()
  );
}

export function WeekView({ tasks, weekStart, onWeekChange, onTaskClick }: WeekViewProps) {
  const today = new Date();
  const weekDates = useMemo(() => getWeekDates(weekStart), [weekStart]);

  const tasksByDate = useMemo(() => {
    const map = new Map<string, Task[]>();

    weekDates.forEach((date) => {
      map.set(toLocalDateKey(date), []);
    });
    map.set('no-date', []);

    tasks.forEach((task) => {
      if (!task.due_date) {
        const list = map.get('no-date') || [];
        list.push(task);
        map.set('no-date', list);
      } else {
        // due_date is a date string like "2026-02-12" — use directly as key
        const key = task.due_date.split('T')[0];
        if (map.has(key)) {
          const list = map.get(key) || [];
          list.push(task);
          map.set(key, list);
        }
      }
    });

    return map;
  }, [tasks, weekDates]);

  const handlePrevWeek = () => {
    const newStart = new Date(weekStart);
    newStart.setDate(weekStart.getDate() - 7);
    onWeekChange(newStart);
  };

  const handleNextWeek = () => {
    const newStart = new Date(weekStart);
    newStart.setDate(weekStart.getDate() + 7);
    onWeekChange(newStart);
  };

  const handleThisWeek = () => {
    onWeekChange(getMonday(new Date()));
  };

  const weekEndDate = new Date(weekStart);
  weekEndDate.setDate(weekStart.getDate() + 6);

  const noDateTasks = tasksByDate.get('no-date') || [];

  return (
    <div>
      {/* Navigation bar */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between mb-4">
        <div className="flex items-center gap-1 overflow-x-auto p-1 bg-white/50 backdrop-blur-md rounded-full shadow-sm border border-white/60">
          <button
            onClick={handlePrevWeek}
            className="px-3 sm:px-4 py-1.5 text-[12px] sm:text-sm font-medium transition-all rounded-full text-brand-blue hover:bg-white/60 whitespace-nowrap"
          >
            ← 이전
          </button>
          <button
            onClick={handleThisWeek}
            className="px-3 sm:px-4 py-1.5 text-[12px] sm:text-sm font-medium transition-all rounded-full bg-brand-blue text-white shadow-md whitespace-nowrap"
          >
            이번 주
          </button>
          <button
            onClick={handleNextWeek}
            className="px-3 sm:px-4 py-1.5 text-[12px] sm:text-sm font-medium transition-all rounded-full text-brand-blue hover:bg-white/60 whitespace-nowrap"
          >
            다음 →
          </button>
        </div>
        <h3 className="font-black text-sm sm:text-lg">
          {weekStart.toLocaleDateString('ko-KR', { month: 'long', day: 'numeric' })} -{' '}
          {weekEndDate.toLocaleDateString('ko-KR', { month: 'long', day: 'numeric' })}
        </h3>
      </div>

      {/* Weekly rows grid */}
      <div className="glass-panel rounded-2xl overflow-hidden flex flex-col">
        {weekDates.map((date, i) => {
          const key = toLocalDateKey(date);
          const dayTasks = tasksByDate.get(key) || [];
          const isToday = isSameDay(date, today);
          const todayMidnight = new Date(today);
          todayMidnight.setHours(0, 0, 0, 0);
          const isPast = date < todayMidnight && !isToday;
          return (
            <WeekRow
              key={key}
              date={date}
              tasks={dayTasks}
              onTaskClick={onTaskClick}
              isToday={isToday}
              isPast={isPast}
              isLast={i === 6}
            />
          );
        })}
      </div>

      {/* No-due-date section */}
      {noDateTasks.length > 0 && (
        <div className="mt-4 glass-panel rounded-2xl overflow-hidden flex flex-col">
          <WeekRow
            date={null}
            label="마감일 없음"
            tasks={noDateTasks}
            onTaskClick={onTaskClick}
            isToday={false}
            isPast={false}
            isLast={true}
          />
        </div>
      )}
    </div>
  );
}
