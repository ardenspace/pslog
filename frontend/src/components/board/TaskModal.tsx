import { useState, useEffect, useRef } from 'react';
import { useCreateTask, useUpdateTask } from '@/hooks/useTasks';
import type { Task, TaskStatus } from '@/types/task';
import type { WorkspaceRole } from '@/types/workspace';
import type { ProjectMember } from '@/types/project';
import { CustomSelect } from '@/components/ui/CustomSelect';
import { DatePicker } from '@/components/ui/DatePicker';
import { ConfirmModal } from '@/components/ui/ConfirmModal';

interface TaskModalBaseProps {
  members: ProjectMember[];
  isOpen: boolean;
  onClose: () => void;
}

interface CreateModeProps extends TaskModalBaseProps {
  mode: 'create';
  projectId: string;
  currentUserId: string;
  task?: never;
  myRole?: never;
  onDelete?: never;
}

interface EditModeProps extends TaskModalBaseProps {
  mode: 'edit';
  task: Task | null;
  myRole: WorkspaceRole;
  onDelete?: (taskId: string) => void;
  projectId?: never;
  currentUserId?: never;
}

type TaskModalProps = CreateModeProps | EditModeProps;

const statusOptions: { value: TaskStatus; label: string }[] = [
  { value: 'todo', label: 'To Do' },
  { value: 'doing', label: 'Doing' },
  { value: 'done', label: 'Done' },
  { value: 'blocked', label: 'Blocked' },
];

const metaLabelClass = 'text-xs font-bold uppercase tracking-wider text-muted-foreground mb-1 block';
const metaInputClass =
  'border border-brand-blue/20 rounded-xl w-full px-3 py-2 text-sm focus:outline-none focus:shadow-sm disabled:bg-gray-100 disabled:cursor-not-allowed';

export function TaskModal(props: TaskModalProps) {
  const { members, isOpen, onClose } = props;
  const isCreateMode = props.mode === 'create';

  const createTask = useCreateTask(isCreateMode ? props.projectId : '');
  const updateTask = useUpdateTask();

  const modalRef = useRef<HTMLDivElement>(null);
  const [title, setTitle] = useState('');
  const [description, setDescription] = useState('');
  const [status, setStatus] = useState<TaskStatus>('todo');
  const [dueDate, setDueDate] = useState('');
  const [assigneeId, setAssigneeId] = useState<string | null>(null);
  const [isSaving, setIsSaving] = useState(false);
  const [showSaved, setShowSaved] = useState(false);
  const [isDeleteConfirmOpen, setDeleteConfirmOpen] = useState(false);

  const [original, setOriginal] = useState({
    title: '',
    description: '',
    status: 'todo' as TaskStatus,
    dueDate: '',
    assigneeId: null as string | null,
  });

  // 모드별 초기화 소스 — create 에선 task 가 항상 null, edit 에선 currentUserId 가
  // 항상 undefined 라 아래 dep 배열의 발화 조건은 모드별 소스 하나로 좁혀진다.
  const currentUserId = isCreateMode ? props.currentUserId : undefined;
  const task = isCreateMode ? null : props.task;

  // Initialize form state
  useEffect(() => {
    if (!isOpen) return;

    if (isCreateMode) {
      // Create mode: reset to defaults
      setTitle('');
      setDescription('');
      setStatus('todo');
      setDueDate('');
      setAssigneeId(currentUserId ?? null);
      setIsSaving(false);
      setShowSaved(false);
    } else if (task) {
      // Edit mode: load task data
      const taskDueDate = task.due_date ? task.due_date.split('T')[0] : '';
      setTitle(task.title);
      setDescription(task.description || '');
      setStatus(task.status);
      setDueDate(taskDueDate);
      setAssigneeId(task.assignee_id);
      setOriginal({
        title: task.title,
        description: task.description || '',
        status: task.status,
        dueDate: taskDueDate,
        assigneeId: task.assignee_id,
      });
      setIsSaving(false);
      setShowSaved(false);
      setDeleteConfirmOpen(false);
    }
  }, [isOpen, isCreateMode, currentUserId, task]);

  const canEdit = isCreateMode || props.myRole === 'owner' || props.myRole === 'editor';
  const canDelete = !isCreateMode && props.myRole === 'owner';

  if (!isOpen) return null;
  if (!isCreateMode && !task) return null;

  const hasChanges =
    title !== original.title ||
    description !== original.description ||
    status !== original.status ||
    dueDate !== original.dueDate ||
    assigneeId !== original.assigneeId;

  const handleCreate = async () => {
    if (!title.trim()) return;
    setIsSaving(true);
    try {
      await createTask.mutateAsync({
        title,
        description: description || undefined,
        status,
        due_date: dueDate || undefined,
        assignee_id: assigneeId || undefined,
      });
      onClose();
    } catch {
      // keep modal open on error
    } finally {
      setIsSaving(false);
    }
  };

  const handleClose = async () => {
    if (isSaving) return;

    if (isCreateMode) {
      onClose();
      return;
    }

    // Edit mode: auto-save on close if changed
    if (hasChanges && canEdit && task) {
      setIsSaving(true);
      try {
        await updateTask.mutateAsync({
          taskId: task.id,
          data: {
            title,
            description: description || null,
            status,
            due_date: dueDate || null,
            assignee_id: assigneeId,
          },
        });
        setShowSaved(true);
        setTimeout(() => {
          setIsSaving(false);
          onClose();
        }, 400);
      } catch {
        setIsSaving(false);
        onClose();
      }
    } else {
      onClose();
    }
  };

  const handleBackdropClick = (e: React.MouseEvent) => {
    if (modalRef.current && !modalRef.current.contains(e.target as Node)) {
      handleClose();
    }
  };

  const handleConfirmDelete = () => {
    if (isCreateMode || !props.onDelete || !task) {
      setDeleteConfirmOpen(false);
      return;
    }

    props.onDelete(task.id);
    setDeleteConfirmOpen(false);
    onClose();
  };

  const memberOptions = [
    { value: '', label: '담당자 없음' },
    ...members.map((m) => {
      const isSelf = isCreateMode && m.user_id === props.currentUserId;
      return {
        value: m.user_id,
        label: isSelf ? `${m.user.name} (나)` : m.user.name,
      };
    }),
  ];

  return (
    <>
      <div
        className="fixed inset-0 bg-brand-coffee/20 backdrop-blur-sm flex items-center justify-center z-50 p-3 sm:p-4"
        onClick={handleBackdropClick}
      >
        <div
          ref={modalRef}
          className="bg-brand-cream rounded-3xl shadow-xl border border-brand-blue/10 w-full max-w-3xl max-h-[92vh] overflow-y-auto"
        >
        <div className="p-4 sm:p-6 pt-4 sm:pt-6">
          {/* 2-column body */}
          <div className="flex flex-col md:flex-row gap-4 md:gap-6">
            {/* Left: title + description */}
            <div className="flex-1 flex flex-col gap-4 min-w-0">
              <input
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                disabled={!canEdit || isSaving}
                placeholder="태스크 제목"
                className="border-0 border-b border-brand-blue/20 rounded-xl w-full px-0 py-2 font-black text-lg sm:text-xl focus:outline-none bg-transparent disabled:bg-transparent placeholder:text-gray-400"
                autoFocus={isCreateMode}
              />

              <div>
                <label className={metaLabelClass}>설명</label>
                <textarea
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  disabled={!canEdit || isSaving}
                  placeholder="설명 (선택)"
                  className={`${metaInputClass} min-h-[280px] resize-none`}
                />
              </div>
            </div>

            {/* Right: metadata */}
            <div className="w-full md:w-56 flex-shrink-0 flex flex-col gap-4 border-t-2 md:border-t-0 md:border-l-2 border-brand-blue/20 pt-4 md:pt-0 md:pl-6">
              <div>
                <label className={metaLabelClass}>상태</label>
                <CustomSelect
                  value={status}
                  onChange={(v) => setStatus(v as TaskStatus)}
                  options={statusOptions}
                  disabled={!canEdit || isSaving}
                />
              </div>

              <div>
                <label className={metaLabelClass}>담당자</label>
                <CustomSelect
                  value={assigneeId || ''}
                  onChange={(v) => setAssigneeId(v || null)}
                  options={memberOptions}
                  disabled={!canEdit || isSaving}
                />
              </div>

              <div>
                <label className={metaLabelClass}>마감일</label>
                <DatePicker
                  value={dueDate}
                  onChange={setDueDate}
                  disabled={!canEdit || isSaving}
                  placeholder="날짜 선택"
                  align="right"
                />
              </div>

              {!canEdit && (
                <div className="mt-auto">
                  <span className="text-xs bg-white/80 text-brand-blue border border-brand-blue/20 font-bold px-2 py-1 inline-block">
                    읽기 전용
                  </span>
                </div>
              )}
            </div>
          </div>

          {/* Footer */}
          <div className="flex flex-col gap-3 sm:gap-4 sm:flex-row sm:items-center sm:justify-between mt-6 pt-4 border-t-2 border-brand-blue/20">
            <div>
              {canDelete && !isCreateMode && props.onDelete && task && (
                <button
                  type="button"
                  disabled={isSaving}
                  onClick={() => setDeleteConfirmOpen(true)}
                  className="bg-brand-orange text-white border border-brand-blue/20 font-bold px-4 py-2 text-xs sm:text-sm hover:bg-red-600 transition-colors shadow-sm disabled:opacity-50"
                >
                  삭제
                </button>
              )}
            </div>

            <div className="flex-1 flex justify-center">
              {isSaving && !showSaved && (
                <span className="text-xs font-bold text-muted-foreground">
                  {isCreateMode ? '생성 중...' : '저장 중...'}
                </span>
              )}
              {showSaved && (
                <span className="text-xs font-bold text-green-700">저장됨 ✓</span>
              )}
            </div>

            <div className="flex gap-2 w-full sm:w-auto justify-end">
              {isCreateMode ? (
                <>
                  <button
                    type="button"
                    onClick={handleClose}
                    disabled={isSaving}
                    className="border border-brand-blue/20 font-bold px-4 py-2 text-xs sm:text-sm hover:bg-white/60 transition-colors flex-1 sm:flex-none"
                  >
                    취소
                  </button>
                  <button
                    type="button"
                    onClick={handleCreate}
                    disabled={isSaving || !title.trim()}
                    className="bg-brand-blue text-white border border-brand-blue/20 font-bold px-4 py-2 text-xs sm:text-sm hover:bg-brand-neon hover:text-brand-blue transition-colors shadow-sm disabled:opacity-50 flex-1 sm:flex-none"
                  >
                    생성
                  </button>
                </>
              ) : (
                <button
                  type="button"
                  onClick={handleClose}
                  disabled={isSaving}
                  className="border border-brand-blue/20 font-bold px-4 py-2 text-xs sm:text-sm hover:bg-white/60 transition-colors w-full sm:w-auto"
                >
                  닫기
                </button>
              )}
            </div>
          </div>
        </div>
        </div>
      </div>
      <ConfirmModal
        isOpen={isDeleteConfirmOpen}
        title="태스크를 삭제할까요?"
        description="삭제 후에는 복구할 수 없습니다."
        confirmText="삭제"
        cancelText="취소"
        confirmVariant="destructive"
        onConfirm={handleConfirmDelete}
        onCancel={() => setDeleteConfirmOpen(false)}
      />
    </>
  );
}
