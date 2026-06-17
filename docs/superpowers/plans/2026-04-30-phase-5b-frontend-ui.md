# Phase 5b — Frontend UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Phase 5a 의 backend endpoint 들을 호출하는 frontend UI. Sidebar 에서 진입하는 두 개의 모달 (`ProjectGitSettingsModal`, `HandoffHistoryModal`) + `TaskCard` 의 source 배지 표시 + Backend `TaskResponse` schema 가 4개 신규 필드 (Phase 1 모델 누락분) 노출. **Phase 5 핵심 가치 전달 완료** — 사용자가 pslog UI 에서 git 연동 설정 + webhook 자동 등록 + handoff 이력 조회 + sync 실패 재처리 가능.

**Architecture:** 코드베이스 컨벤션 우선 — 별도 페이지 X, **Sidebar `ProjectItem` 의 메뉴 확장 + 모달**. `services/api.ts` 단일 파일에 `git` 그룹 추가 (별도 `githubApi.ts` 안 만듦). `hooks/useGithubSettings.ts` 단일 파일에 5개 훅 (TanStack Query: useGitSettings, useUpdateGitSettings, useRegisterWebhook, useHandoffs, useReprocessEvent). `types/git.ts` 신규. backend small change — `TaskResponse` 에 4개 필드 추가 (`source / external_id / last_commit_sha / archived_at`) + 회귀 테스트.

**Tech Stack:** React 19, TypeScript 5+, Vite, Tailwind, shadcn/ui (Button/Input/Label/Card primitives), TanStack Query 5.90, Axios 1.13, React Hook Form 미사용 (기존 패턴 따라 `useState` + manual handlers). 외부 의존 추가 없음.

**선행 조건:**
- pslog main, alembic head = `a1b2c3d4e5f6` (Phase 5a 머지 완료, PR #11)
- bun 설치됨 (frontend package manager)
- backend Phase 5a 의 5개 endpoint 정상 동작 (PR #11 머지)
- 169 backend tests baseline

**중요한 계약:**

- **TaskCard ⚠️ 표시는 Phase 5b 범위 밖**: source 배지만 표시. handoff 누락 ⚠️ 는 데이터 정의 (어떤 join 으로 결정?) 가 미정 — 후속 phase 또는 별도 PR.
- **Modal 진입 패턴** (코드베이스 컨벤션):
  - Sidebar `ProjectItem` 에 메뉴 추가 (기존 "프로젝트 편집" 처럼) — "Git 연동 설정", "Handoff 이력"
  - 각 모달은 `useState` 로 open/close, 부모 (DashboardPage) 가 state 보유
  - 또는 `ProjectItem` 자체에서 모달 state 관리 (기존 EditProjectModal 패턴)
  - **결정**: `ProjectItem` 자체에서 관리 (기존 패턴 따름)
- **권한 분기**: Phase 5a backend 는 OWNER 만 PATCH/POST 허용. Frontend 도 OWNER 가 아니면 폼 disable + 안내 텍스트. `useProjectMembers` 또는 `useEffectiveRole` 훅으로 role 조회 — 기존 패턴 확인 필요. **결정**: 단순화 — 폼은 항상 표시하되 PATCH/POST 호출 시 backend 가 403 응답 → toast 에 "Owner 권한 필요" 표시. (frontend 조건부 disable 은 후속 polish.)
- **Webhook 등록 flow**:
  1. 사용자가 ProjectGitSettingsModal 에서 repo URL + PAT 입력 → "저장" → PATCH /git-settings
  2. 별도 "Webhook 등록 / 재등록" 버튼 → POST /git-settings/webhook
  3. 응답 토스트: `was_existing` 면 "기존 hook 갱신 (secret rotated)", 아니면 "신규 등록"
  4. 모달 안 helper text: "PAT 는 admin:repo_hook 스코프 필요" + GitHub PAT 페이지 링크
- **HandoffHistory flow**:
  1. Sidebar 에서 진입 → 현재 프로젝트의 handoff 목록
  2. 브랜치 필터 dropdown (전체 / 특정 브랜치)
  3. 각 항목: branch / commit_sha (앞 7자) / pushed_at / parsed_tasks_count / author_git_login
  4. **재처리 버튼은 별개 — handoff 가 아니라 GitPushEvent 단위**. Phase 5b 는 handoff 목록만, 재처리 UI 는 별도 GitEventList 모달 (또는 후속). **결정**: Phase 5b 에서는 handoff 목록만, reprocess UI 는 후속 (handoffs vs git-events 두 list 가 같은 모달이면 복잡). 단, reprocess endpoint 의 회귀 검증을 위해 hook (`useReprocessEvent`) 은 만들어둠 — 콜 site 는 후속 PR.
- **Backend small change**:
  - `app/schemas/task.py` 의 `TaskResponse` 에 4 필드 추가:
    - `source: TaskSource = TaskSource.MANUAL`
    - `external_id: str | None = None`
    - `last_commit_sha: str | None = None`
    - `archived_at: datetime | None = None`
  - 기존 회귀 0 — 모든 기존 테스트가 새 필드를 무시 (Pydantic from_attributes).
  - frontend `types/task.ts` 의 `Task` 인터페이스 동기화.
- **검증 (subagent 한계)**:
  - subagent 는 `bun run build` (tsc + vite) + `bun run lint` 통과만 검증
  - **시각 검증 (실제 모달 표시 / 색상 / 레이아웃) 은 사용자 직접 dev server (`bun run dev`) 에서 브라우저로**
  - subagent 는 dev server 실행 안 함 — 환경/포트 의존
- **Vitest 미도입**: 본 phase 에서 frontend 단위 테스트 셋업 없음. 향후 별도 phase. tsc/lint 만으로 회귀 확인.

---

## File Structure

**신규 파일 (frontend):**
- `frontend/src/types/git.ts` — `GitSettings`, `GitSettingsUpdate`, `WebhookRegisterResponse`, `HandoffSummary`, `ReprocessResponse`
- `frontend/src/hooks/useGithubSettings.ts` — 5 TanStack Query hooks
- `frontend/src/components/sidebar/ProjectGitSettingsModal.tsx` — repo URL / PAT / plan_path / handoff_dir 입력 + Save + Webhook 등록 버튼
- `frontend/src/components/sidebar/HandoffHistoryModal.tsx` — 브랜치 필터 + 목록

**수정 파일 (frontend):**
- `frontend/src/services/api.ts` — `git` 그룹 추가 (5 method)
- `frontend/src/types/task.ts` — `Task` 에 4 필드 추가 (`source`, `external_id`, `last_commit_sha`, `archived_at`)
- `frontend/src/types/index.ts` — `git.ts` re-export
- `frontend/src/components/sidebar/ProjectItem.tsx` — 메뉴에 "Git 연동 설정" / "Handoff 이력" 추가, 두 모달 호출
- `frontend/src/components/board/TaskCard.tsx` — `source` 배지 (MANUAL 작은 회색 / SYNCED_FROM_PLAN 파란색)

**수정 파일 (backend):**
- `backend/app/schemas/task.py` — `TaskResponse` 에 4 필드 추가
- `backend/tests/test_tasks_endpoint.py` 또는 `test_task_model.py` — 응답에 4 필드 노출 검증 1건 추가

**수정 없음:**
- `backend/app/services/`, `app/api/v1/endpoints/tasks.py` (기존 그대로 — Pydantic from_attributes 가 자동 노출)
- React Router 라우트 (모달 패턴이라 추가 라우트 없음)
- `package.json` (의존성 추가 없음)

---

## Self-Review Notes

작성 후 self-review:
- 설계서 §5.3 frontend 구조 5항목 → ProjectGitSettings (Task 5), HandoffHistory (Task 6), TaskCard 수정 (Task 7) — DailyBriefPanel/useDailyBrief 는 Phase 7 (Gemma)
- handoff 메모 (Phase 5a) Phase 5b 항목 7개 → 모두 매핑
- 코드베이스 컨벤션: api.ts 단일 파일 / hooks 도메인별 / sidebar 모달 패턴 → 따름
- backend small change 필요 (TaskResponse 누락 필드) → Task 1
- 검증: 사용자 dev server (subagent 한계 명시)

---

## Task 0: 브랜치 + bun + 기준 검증

- [ ] **Step 1: 브랜치 생성**

```bash
cd /Users/arden/Documents/ardensdevspace/pslog
git checkout main && git pull --ff-only origin main
git checkout -b feature/phase-5b-frontend-ui
```

Expected: main HEAD = `900fa20` (Phase 5a 머지).

- [ ] **Step 2: frontend deps 설치**

```bash
cd frontend
bun install 2>&1 | tail -3
```

Expected: deps 설치 완료.

- [ ] **Step 3: backend baseline + frontend tsc/lint baseline**

```bash
cd /Users/arden/Documents/ardensdevspace/pslog/backend
source venv/bin/activate
pytest -q 2>&1 | tail -3

cd /Users/arden/Documents/ardensdevspace/pslog/frontend
bun run build 2>&1 | tail -10
bun run lint 2>&1 | tail -10
```

Expected:
- backend: 169 tests pass
- frontend: build 통과, lint 0 errors (또는 기존 warning 만)

---

## Task 1: Backend `TaskResponse` schema 확장

**Files:**
- Modify: `backend/app/schemas/task.py`
- Modify: `backend/tests/test_tasks_endpoint.py` (or create regression test)

- [ ] **Step 1: schema 수정**

`backend/app/schemas/task.py` 의 `TaskResponse` 에 4 필드 추가 (기존 `model_config` 위 line):

```python
class TaskResponse(BaseModel):
    id: UUID
    project_id: UUID
    title: str
    description: str | None
    status: TaskStatus
    due_date: date | None
    assignee_id: UUID | None
    reporter_id: UUID | None
    created_at: datetime
    updated_at: datetime
    assignee: UserBrief | None = None
    reporter: UserBrief | None = None
    # Phase 5b — frontend 가 source 배지 / git 연동 정보 표시 (Phase 1 모델 누락분 노출)
    source: TaskSource = TaskSource.MANUAL
    external_id: str | None = None
    last_commit_sha: str | None = None
    archived_at: datetime | None = None

    model_config = {"from_attributes": True}
```

상단 import 에 `TaskSource` 추가:

```python
from app.models.task import TaskSource, TaskStatus
```

- [ ] **Step 2: 회귀 테스트 추가**

`backend/tests/test_tasks_endpoint.py` 가 있으면 그곳에, 없으면 `test_task_model.py` 끝에 추가:

```python
async def test_task_response_exposes_phase1_fields(async_session: AsyncSession):
    """Phase 5b: TaskResponse 가 source / external_id / last_commit_sha / archived_at 노출."""
    from app.models.workspace import Workspace
    from app.models.project import Project
    from app.models.task import Task, TaskSource
    from app.schemas.task import TaskResponse
    import uuid

    ws = Workspace(name="ws", slug=f"ws-{uuid.uuid4().hex[:8]}")
    async_session.add(ws)
    await async_session.flush()
    proj = Project(workspace_id=ws.id, name="p")
    async_session.add(proj)
    await async_session.flush()
    task = Task(
        project_id=proj.id,
        title="t",
        source=TaskSource.SYNCED_FROM_PLAN,
        external_id="task-001",
        last_commit_sha="a" * 40,
    )
    async_session.add(task)
    await async_session.commit()
    await async_session.refresh(task)

    response = TaskResponse.model_validate(task)
    assert response.source == TaskSource.SYNCED_FROM_PLAN
    assert response.external_id == "task-001"
    assert response.last_commit_sha == "a" * 40
    assert response.archived_at is None
```

(기존 file 의 `async_session` import 여부 확인 — 없으면 fixture import 추가.)

- [ ] **Step 3: 회귀 실행**

```bash
cd /Users/arden/Documents/ardensdevspace/pslog/backend
source venv/bin/activate
pytest -q
```

Expected: 170 tests pass (169 + 1 new), 회귀 0.

- [ ] **Step 4: Commit**

```bash
git add backend/app/schemas/task.py backend/tests/test_task_model.py
git commit -m "feat(phase5b): TaskResponse 에 Phase 1 누락 필드 노출 (source/external_id/last_commit_sha/archived_at)"
```

---

## Task 2: Frontend types — `Task` 확장 + 신규 `types/git.ts`

**Files:**
- Modify: `frontend/src/types/task.ts`
- Create: `frontend/src/types/git.ts`
- Modify: `frontend/src/types/index.ts`

- [ ] **Step 1: `types/task.ts` 확장**

`frontend/src/types/task.ts` 의 `Task` interface 에 4 필드 추가, `TaskSource` enum 추가:

```typescript
import type { UserBrief } from './user';

export const TASK_STATUS = {
  TODO: 'todo',
  DOING: 'doing',
  DONE: 'done',
  BLOCKED: 'blocked',
} as const;

export type TaskStatus = (typeof TASK_STATUS)[keyof typeof TASK_STATUS];

export const TASK_SOURCE = {
  MANUAL: 'manual',
  SYNCED_FROM_PLAN: 'synced_from_plan',
} as const;

export type TaskSource = (typeof TASK_SOURCE)[keyof typeof TASK_SOURCE];

export interface Task {
  id: string;
  project_id: string;
  title: string;
  description: string | null;
  status: TaskStatus;
  due_date: string | null;
  assignee_id: string | null;
  reporter_id: string | null;
  created_at: string;
  updated_at: string;
  assignee: UserBrief | null;
  reporter: UserBrief | null;
  // Phase 5b — backend Phase 1 모델 필드 노출
  source: TaskSource;
  external_id: string | null;
  last_commit_sha: string | null;
  archived_at: string | null;
}

export interface TaskCreate {
  title: string;
  description?: string | null;
  status?: TaskStatus;
  due_date?: string | null;
  assignee_id?: string | null;
}

export interface TaskUpdate {
  title?: string;
  description?: string | null;
  status?: TaskStatus;
  due_date?: string | null;
  assignee_id?: string | null;
}
```

(backend `TaskSource` enum value 는 `"manual"` / `"synced_from_plan"` — 소문자. SQLAlchemy 매핑은 NAME 대문자지만 Pydantic serialize 는 value 소문자.)

- [ ] **Step 2: `types/git.ts` 신규**

Create `frontend/src/types/git.ts`:

```typescript
export interface GitSettings {
  git_repo_url: string | null;
  git_default_branch: string;
  plan_path: string;
  handoff_dir: string;
  last_synced_commit_sha: string | null;
  has_webhook_secret: boolean;
  has_github_pat: boolean;
  public_webhook_url: string;
}

export interface GitSettingsUpdate {
  git_repo_url?: string | null;
  git_default_branch?: string | null;
  plan_path?: string | null;
  handoff_dir?: string | null;
  github_pat?: string | null;  // 평문 입력 — 즉시 backend Fernet encrypt
}

export interface WebhookRegisterResponse {
  webhook_id: number;
  was_existing: boolean;
  public_webhook_url: string;
}

export interface HandoffSummary {
  id: string;
  branch: string;
  author_git_login: string;
  commit_sha: string;
  pushed_at: string;  // ISO datetime
  parsed_tasks_count: number;
}

export interface ReprocessResponse {
  event_id: string;
  status: string;
}
```

- [ ] **Step 3: `types/index.ts` 에 re-export 추가**

`frontend/src/types/index.ts` 끝에 추가:

```typescript
export * from './git';
```

- [ ] **Step 4: tsc 검증**

```bash
cd /Users/arden/Documents/ardensdevspace/pslog/frontend
bun run build 2>&1 | tail -10
```

Expected: build 통과 (types-only 변경 — runtime 영향 없음).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/types/task.ts frontend/src/types/git.ts frontend/src/types/index.ts
git commit -m "feat(phase5b): frontend types — Task source 필드 + GitSettings/Handoff/Reprocess"
```

---

## Task 3: `services/api.ts` 에 `git` 그룹 추가

**Files:**
- Modify: `frontend/src/services/api.ts`

- [ ] **Step 1: api.ts 의 `api` object 에 `git` 그룹 추가**

기존 `api` object 의 `tasks` 다음에 (또는 적절한 위치에) `git` 그룹 추가. 상단 import 에 git 타입 추가:

```typescript
import type {
  // ... 기존 타입
  GitSettings,
  GitSettingsUpdate,
  WebhookRegisterResponse,
  HandoffSummary,
  ReprocessResponse,
} from '@/types';
```

`api` object 의 끝(닫는 `}` 직전)에 추가:

```typescript
  git: {
    getSettings: (projectId: string) =>
      apiClient.get<GitSettings>(`/projects/${projectId}/git-settings`),
    updateSettings: (projectId: string, data: GitSettingsUpdate) =>
      apiClient.patch<GitSettings>(`/projects/${projectId}/git-settings`, data),
    registerWebhook: (projectId: string) =>
      apiClient.post<WebhookRegisterResponse>(`/projects/${projectId}/git-settings/webhook`),
    listHandoffs: (projectId: string, params?: { branch?: string; limit?: number }) =>
      apiClient.get<HandoffSummary[]>(`/projects/${projectId}/handoffs`, { params }),
    reprocessEvent: (projectId: string, eventId: string) =>
      apiClient.post<ReprocessResponse>(`/projects/${projectId}/git-events/${eventId}/reprocess`),
  },
```

(method 순서: backend endpoint 정의 순서 — get / patch / post webhook / get handoffs / post reprocess.)

- [ ] **Step 2: tsc 검증**

```bash
cd /Users/arden/Documents/ardensdevspace/pslog/frontend
bun run build 2>&1 | tail -5
```

Expected: build 통과.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/services/api.ts
git commit -m "feat(phase5b): api.git — git-settings / handoffs / reprocess axios 래퍼"
```

---

## Task 4: `hooks/useGithubSettings.ts` — TanStack Query 훅

**Files:**
- Create: `frontend/src/hooks/useGithubSettings.ts`

- [ ] **Step 1: 훅 파일 작성**

Create `frontend/src/hooks/useGithubSettings.ts`:

```typescript
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/services/api';
import type { GitSettingsUpdate } from '@/types';

export function useGitSettings(projectId: string | null) {
  return useQuery({
    queryKey: ['projects', projectId, 'git-settings'],
    queryFn: () => api.git.getSettings(projectId!).then((r) => r.data),
    enabled: !!projectId,
  });
}

export function useUpdateGitSettings(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (data: GitSettingsUpdate) =>
      api.git.updateSettings(projectId, data).then((r) => r.data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['projects', projectId, 'git-settings'] });
    },
  });
}

export function useRegisterWebhook(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => api.git.registerWebhook(projectId).then((r) => r.data),
    onSuccess: () => {
      // has_webhook_secret 가 true 로 바뀜 — settings 다시 fetch
      queryClient.invalidateQueries({ queryKey: ['projects', projectId, 'git-settings'] });
    },
  });
}

export function useHandoffs(
  projectId: string | null,
  params?: { branch?: string; limit?: number },
) {
  return useQuery({
    queryKey: ['projects', projectId, 'handoffs', params?.branch ?? 'all', params?.limit ?? 50],
    queryFn: () => api.git.listHandoffs(projectId!, params).then((r) => r.data),
    enabled: !!projectId,
  });
}

export function useReprocessEvent(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (eventId: string) =>
      api.git.reprocessEvent(projectId, eventId).then((r) => r.data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['projects', projectId, 'handoffs'] });
    },
  });
}
```

- [ ] **Step 2: tsc 검증**

```bash
cd /Users/arden/Documents/ardensdevspace/pslog/frontend
bun run build 2>&1 | tail -5
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/hooks/useGithubSettings.ts
git commit -m "feat(phase5b): useGithubSettings — TanStack Query 훅 5종 (settings/webhook/handoffs/reprocess)"
```

---

## Task 5: `ProjectGitSettingsModal.tsx`

**Files:**
- Create: `frontend/src/components/sidebar/ProjectGitSettingsModal.tsx`

`EditProjectModal.tsx` 패턴 확인 후 일관 유지. 본 task 는 그 패턴을 따른 modal — repo URL / PAT / plan_path / handoff_dir 폼 + 저장 + Webhook 등록 버튼.

- [ ] **Step 1: 기존 `EditProjectModal.tsx` 패턴 확인**

```bash
cat /Users/arden/Documents/ardensdevspace/pslog/frontend/src/components/sidebar/EditProjectModal.tsx
```

(패턴 파악 — useState / form submit / 모달 backdrop / shadcn Button/Input/Label 사용.)

- [ ] **Step 2: 모달 컴포넌트 작성**

Create `frontend/src/components/sidebar/ProjectGitSettingsModal.tsx`:

```typescript
import { useEffect, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { useGitSettings, useUpdateGitSettings, useRegisterWebhook } from '@/hooks/useGithubSettings';

interface Props {
  projectId: string;
  open: boolean;
  onClose: () => void;
}

export function ProjectGitSettingsModal({ projectId, open, onClose }: Props) {
  const { data: settings, isLoading } = useGitSettings(open ? projectId : null);
  const updateMutation = useUpdateGitSettings(projectId);
  const registerMutation = useRegisterWebhook(projectId);

  const [repoUrl, setRepoUrl] = useState('');
  const [planPath, setPlanPath] = useState('PLAN.md');
  const [handoffDir, setHandoffDir] = useState('handoffs/');
  const [pat, setPat] = useState('');  // 평문 입력 — backend 가 즉시 Fernet encrypt
  const [error, setError] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<string | null>(null);

  useEffect(() => {
    if (settings) {
      setRepoUrl(settings.git_repo_url ?? '');
      setPlanPath(settings.plan_path);
      setHandoffDir(settings.handoff_dir);
    }
  }, [settings]);

  if (!open) return null;

  const handleSave = async () => {
    setError(null);
    setFeedback(null);
    try {
      await updateMutation.mutateAsync({
        git_repo_url: repoUrl || null,
        plan_path: planPath,
        handoff_dir: handoffDir,
        github_pat: pat || undefined,  // 빈 문자열 보내지 말 것 (기존 PAT 유지)
      });
      setPat('');  // 입력 비우기
      setFeedback('저장됨');
    } catch (e: unknown) {
      const message = e instanceof Error ? e.message : '저장 실패';
      setError(message);
    }
  };

  const handleRegisterWebhook = async () => {
    setError(null);
    setFeedback(null);
    try {
      const res = await registerMutation.mutateAsync();
      setFeedback(
        res.was_existing
          ? `기존 webhook 갱신 (secret rotated, hook_id=${res.webhook_id})`
          : `webhook 신규 등록 완료 (hook_id=${res.webhook_id})`
      );
    } catch (e: unknown) {
      const message = e instanceof Error ? e.message : 'webhook 등록 실패';
      setError(message);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
      onClick={onClose}
    >
      <div
        className="w-full max-w-lg rounded-lg bg-white p-6 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 className="mb-4 text-xl font-semibold">Git 연동 설정</h2>

        {isLoading ? (
          <div className="py-8 text-center text-gray-500">불러오는 중...</div>
        ) : (
          <div className="space-y-4">
            <div>
              <Label htmlFor="repo-url">Git repo URL</Label>
              <Input
                id="repo-url"
                value={repoUrl}
                onChange={(e) => setRepoUrl(e.target.value)}
                placeholder="https://github.com/owner/repo"
              />
            </div>

            <div>
              <Label htmlFor="plan-path">PLAN.md 경로</Label>
              <Input
                id="plan-path"
                value={planPath}
                onChange={(e) => setPlanPath(e.target.value)}
              />
            </div>

            <div>
              <Label htmlFor="handoff-dir">handoff 디렉토리</Label>
              <Input
                id="handoff-dir"
                value={handoffDir}
                onChange={(e) => setHandoffDir(e.target.value)}
              />
            </div>

            <div>
              <Label htmlFor="pat">
                GitHub PAT
                {settings?.has_github_pat && (
                  <span className="ml-2 text-xs text-green-600">(저장됨 — 새 값 입력 시 덮어씀)</span>
                )}
              </Label>
              <Input
                id="pat"
                type="password"
                value={pat}
                onChange={(e) => setPat(e.target.value)}
                placeholder="ghp_..."
              />
              <p className="mt-1 text-xs text-gray-500">
                <code>admin:repo_hook</code> 스코프 필요 (자동 webhook 등록용).{' '}
                <a
                  href="https://github.com/settings/tokens/new?scopes=admin:repo_hook"
                  target="_blank"
                  rel="noreferrer"
                  className="text-blue-600 underline"
                >
                  PAT 발급 페이지
                </a>
              </p>
            </div>

            <div className="border-t pt-4">
              <p className="mb-2 text-sm">
                <span className="font-medium">Webhook 상태:</span>{' '}
                {settings?.has_webhook_secret ? (
                  <span className="text-green-600">등록됨</span>
                ) : (
                  <span className="text-gray-500">미등록</span>
                )}
              </p>
              <p className="mb-3 text-xs text-gray-500">
                Callback URL: <code>{settings?.public_webhook_url}</code>
              </p>
              <Button
                onClick={handleRegisterWebhook}
                disabled={registerMutation.isPending || !settings?.git_repo_url || !settings?.has_github_pat}
                variant="outline"
              >
                {settings?.has_webhook_secret ? 'Webhook 재등록 (secret rotate)' : 'Webhook 등록'}
              </Button>
            </div>

            {error && (
              <div className="rounded bg-red-50 p-3 text-sm text-red-700">{error}</div>
            )}
            {feedback && (
              <div className="rounded bg-green-50 p-3 text-sm text-green-700">{feedback}</div>
            )}
          </div>
        )}

        <div className="mt-6 flex justify-end gap-2">
          <Button variant="outline" onClick={onClose}>
            닫기
          </Button>
          <Button onClick={handleSave} disabled={updateMutation.isPending}>
            저장
          </Button>
        </div>
      </div>
    </div>
  );
}
```

(클래스명/색상 등은 기존 EditProjectModal 패턴과 잘 안 맞으면 그쪽에 맞춰 조정.)

- [ ] **Step 3: tsc 검증**

```bash
cd /Users/arden/Documents/ardensdevspace/pslog/frontend
bun run build 2>&1 | tail -5
```

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/sidebar/ProjectGitSettingsModal.tsx
git commit -m "feat(phase5b): ProjectGitSettingsModal — repo URL/PAT/path 입력 + webhook 등록 버튼"
```

---

## Task 6: `HandoffHistoryModal.tsx` + `ProjectItem` 통합

**Files:**
- Create: `frontend/src/components/sidebar/HandoffHistoryModal.tsx`
- Modify: `frontend/src/components/sidebar/ProjectItem.tsx`

- [ ] **Step 1: HandoffHistoryModal 작성**

Create `frontend/src/components/sidebar/HandoffHistoryModal.tsx`:

```typescript
import { useState } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { useHandoffs } from '@/hooks/useGithubSettings';

interface Props {
  projectId: string;
  open: boolean;
  onClose: () => void;
}

export function HandoffHistoryModal({ projectId, open, onClose }: Props) {
  const [branchFilter, setBranchFilter] = useState('');
  const { data: handoffs, isLoading } = useHandoffs(
    open ? projectId : null,
    branchFilter ? { branch: branchFilter, limit: 100 } : { limit: 100 },
  );

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
      onClick={onClose}
    >
      <div
        className="w-full max-w-2xl rounded-lg bg-white p-6 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 className="mb-4 text-xl font-semibold">Handoff 이력</h2>

        <div className="mb-4 flex items-end gap-2">
          <div className="flex-1">
            <Label htmlFor="branch-filter">브랜치 필터</Label>
            <Input
              id="branch-filter"
              value={branchFilter}
              onChange={(e) => setBranchFilter(e.target.value)}
              placeholder="비우면 전체"
            />
          </div>
        </div>

        {isLoading ? (
          <div className="py-8 text-center text-gray-500">불러오는 중...</div>
        ) : handoffs && handoffs.length > 0 ? (
          <div className="max-h-[60vh] overflow-y-auto rounded border">
            <table className="w-full text-sm">
              <thead className="sticky top-0 border-b bg-gray-50">
                <tr>
                  <th className="px-3 py-2 text-left">날짜</th>
                  <th className="px-3 py-2 text-left">브랜치</th>
                  <th className="px-3 py-2 text-left">작성자</th>
                  <th className="px-3 py-2 text-left">commit</th>
                  <th className="px-3 py-2 text-right">tasks</th>
                </tr>
              </thead>
              <tbody>
                {handoffs.map((h) => (
                  <tr key={h.id} className="border-b last:border-b-0">
                    <td className="px-3 py-2 text-gray-600">
                      {new Date(h.pushed_at).toLocaleString('ko-KR', {
                        year: 'numeric',
                        month: '2-digit',
                        day: '2-digit',
                        hour: '2-digit',
                        minute: '2-digit',
                      })}
                    </td>
                    <td className="px-3 py-2 font-mono text-xs">{h.branch}</td>
                    <td className="px-3 py-2">@{h.author_git_login}</td>
                    <td className="px-3 py-2 font-mono text-xs">{h.commit_sha.slice(0, 7)}</td>
                    <td className="px-3 py-2 text-right text-gray-600">
                      {h.parsed_tasks_count}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="py-8 text-center text-gray-500">
            handoff 이력 없음 — webhook 설정 후 첫 push 가 들어오면 여기 표시됩니다.
          </div>
        )}

        <div className="mt-6 flex justify-end">
          <Button variant="outline" onClick={onClose}>
            닫기
          </Button>
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: ProjectItem.tsx 수정**

`frontend/src/components/sidebar/ProjectItem.tsx` 의 메뉴/드롭다운에 두 모달 트리거 추가. **현재 파일 구조 확인 후 진행** — 기존 EditProjectModal 호출 패턴 옆에 두 모달 더 추가:

1. `useState` 두 개 추가 (`gitSettingsOpen`, `handoffOpen`)
2. 메뉴 (또는 트리거 button) 에 두 항목 추가:
   - "Git 연동 설정" — `setGitSettingsOpen(true)`
   - "Handoff 이력" — `setHandoffOpen(true)`
3. JSX 끝에 두 모달 렌더링:

```tsx
<ProjectGitSettingsModal
  projectId={project.id}
  open={gitSettingsOpen}
  onClose={() => setGitSettingsOpen(false)}
/>
<HandoffHistoryModal
  projectId={project.id}
  open={handoffOpen}
  onClose={() => setHandoffOpen(false)}
/>
```

(정확한 메뉴 통합 방식은 기존 코드 구조 따름. 본 plan 은 그 결정을 implementer 에게 일임.)

- [ ] **Step 3: tsc 검증**

```bash
cd /Users/arden/Documents/ardensdevspace/pslog/frontend
bun run build 2>&1 | tail -5
```

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/sidebar/HandoffHistoryModal.tsx \
        frontend/src/components/sidebar/ProjectItem.tsx
git commit -m "feat(phase5b): HandoffHistoryModal + ProjectItem 메뉴 통합"
```

---

## Task 7: `TaskCard` source 배지

**Files:**
- Modify: `frontend/src/components/board/TaskCard.tsx`

- [ ] **Step 1: 현재 TaskCard 확인**

```bash
cat /Users/arden/Documents/ardensdevspace/pslog/frontend/src/components/board/TaskCard.tsx
```

- [ ] **Step 2: source 배지 추가**

`TaskCard.tsx` 안에서 task 의 title 옆 또는 footer 에 작은 배지 추가:

```tsx
{task.source === 'synced_from_plan' && (
  <span
    className="ml-1 rounded bg-blue-100 px-1.5 py-0.5 text-[10px] font-medium text-blue-700"
    title="PLAN.md 에서 자동 동기화된 태스크"
  >
    PLAN
  </span>
)}
```

`MANUAL` 은 기본 — 배지 표시 안 함 (시각적 노이즈 회피).

배지 위치는 기존 카드 구조에 맞게 implementer 가 결정 (title 옆 / footer / corner — 기존 패턴 따름).

- [ ] **Step 3: tsc + lint 검증**

```bash
cd /Users/arden/Documents/ardensdevspace/pslog/frontend
bun run build 2>&1 | tail -5
bun run lint 2>&1 | tail -10
```

Expected: build/lint 통과.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/board/TaskCard.tsx
git commit -m "feat(phase5b): TaskCard — SYNCED_FROM_PLAN 배지 (MANUAL 은 기본)"
```

---

## Task 8: 회귀 + handoff + PR

**Files:**
- Modify: `handoffs/main.md`

- [ ] **Step 1: 전체 회귀 — backend + frontend**

```bash
cd /Users/arden/Documents/ardensdevspace/pslog/backend
source venv/bin/activate
pytest -q 2>&1 | tail -3

cd /Users/arden/Documents/ardensdevspace/pslog/frontend
bun run build 2>&1 | tail -5
bun run lint 2>&1 | tail -10
```

Expected: backend 170 tests pass, frontend build + lint 통과.

- [ ] **Step 2: 사용자 시각 검증 (subagent 한계 — 사용자 직접)**

> **본 step 은 subagent 가 실행하지 않음.** PR 본문에 dev server 검증 체크리스트 포함 — 사용자가 PR 머지 전 직접 확인:
>
> ```bash
> cd /Users/arden/Documents/ardensdevspace/pslog/frontend
> bun run dev
> ```
>
> 1. 로그인 → 프로젝트 선택
> 2. Sidebar ProjectItem 메뉴 → "Git 연동 설정" 클릭 → 모달 열림 확인
> 3. repo URL / PAT 입력 → "저장" → 토스트 또는 feedback 메시지 확인
> 4. "Webhook 등록" 버튼 클릭 → 응답 메시지 확인 (실제 GitHub API 호출 — PAT 가 admin:repo_hook 권한 있어야)
> 5. Sidebar ProjectItem 메뉴 → "Handoff 이력" → 모달 열림 + 빈 상태 표시
> 6. (실 push 들어오면) 이력 row 표시 확인
> 7. 칸반 보드의 task 카드 — `source = 'synced_from_plan'` 인 task 에 PLAN 배지 표시 확인

- [ ] **Step 3: handoff 갱신**

`handoffs/main.md` 상단에 Phase 5b 섹션 추가:

```markdown
## 2026-04-30 (Phase 5b)

- [x] **Phase 5b 완료** — Frontend UI (브랜치 `feature/phase-5b-frontend-ui`)
  - [x] `TaskResponse` schema 확장 (Phase 1 모델 누락 필드 노출): `source`, `external_id`, `last_commit_sha`, `archived_at`
  - [x] frontend `types/git.ts` 신규 + `Task` 인터페이스에 4 필드 + `TaskSource` enum
  - [x] `services/api.ts` 의 `git` 그룹 — getSettings / updateSettings / registerWebhook / listHandoffs / reprocessEvent
  - [x] `hooks/useGithubSettings.ts` — 5 TanStack Query 훅 (useGitSettings, useUpdateGitSettings, useRegisterWebhook, useHandoffs, useReprocessEvent)
  - [x] `ProjectGitSettingsModal.tsx` — repo URL / PAT / plan_path / handoff_dir 폼 + 저장 + Webhook 등록 버튼 (`admin:repo_hook` 가이드 + GitHub PAT 페이지 링크)
  - [x] `HandoffHistoryModal.tsx` — branch 필터 + handoff 목록 (날짜 / 브랜치 / 작성자 / commit / tasks count)
  - [x] `TaskCard.tsx` — `SYNCED_FROM_PLAN` 배지 (`MANUAL` 은 기본 표시 X)
  - [x] sidebar `ProjectItem` 에 두 모달 트리거 메뉴 추가
  - [x] **검증**: backend 170 tests pass, frontend build + lint 통과. **시각 검증은 사용자 dev server 직접** (PR 본문 체크리스트 포함).

### 마지막 커밋

- pslog: `<sha> docs(handoff): Phase 5b 완료 + Phase 6 다음 할 일`
- 브랜치 base: `900fa20` (main, Phase 5a 머지 직후)

### 다음 (Phase 6 — Discord 알림 통합 / 또는 follow-up fixes)

**옵션 A — Phase 6 (Discord 알림 통합, spec §11)**:
- [ ] `discord_service` 확장 — 체크 변경 요약 / handoff 누락 경고 / 롤백 알림 템플릿 3종
- [ ] `sync_service` 가 알림 트리거 (DB 변경 후 fire-and-forget BackgroundTask)
- [ ] `Project.discord_webhook_url` 미설정 시 silent skip
- [ ] cooldown 정책 (spec §8 — 3회 연속 실패 시 disable)

**옵션 B — Phase 5 follow-up fixes** (Phase 5a code review 트래킹):
- [ ] **I-2 fix**: `register_webhook` SELECT FOR UPDATE → 동시 호출 race 차단
- [ ] **I-4 fix**: `process_event` CAS 가드 → reprocess race 차단
- [ ] **M-6 fix**: `sync_service` 가 처리 완료 시 `Project.last_synced_commit_sha` update
- [ ] **M-10 fix**: `_auth_headers / _parse_repo / _raise_for_status` 를 public 으로 promote
- [ ] **TaskCard ⚠️ handoff 누락 표시**: 데이터 정의 (Task `last_commit_sha` join → Handoff 존재 여부 — backend 필드 또는 계산 추가)
- [ ] **GitEventList 모달 + reprocess 호출 site**: HandoffHistory 와 분리 (handoffs vs git-events 다른 list)

### 블로커

없음

### 메모 (2026-04-30 Phase 5b 추가)

- **Modal 진입 패턴**: 코드베이스 컨벤션 (별도 페이지 X, sidebar `ProjectItem` 의 메뉴 + 모달). spec §5.3 의 `pages/` 권고와 차이 — 코드베이스 일관성 우선.
- **PAT 평문 입력 → 즉시 backend Fernet encrypt**: frontend 에서는 `setPat('')` 로 입력 비움 (response 에 PAT 절대 포함 안 됨 — Phase 5a redact 검증).
- **TaskCard ⚠️ skip**: handoff 누락 데이터 정의 (어떤 join? 며칠 윈도우?) 미정 — Phase 5 follow-up 로 트래킹.
- **Vitest 미도입**: frontend 단위 테스트 셋업 본 phase 안 함. tsc/lint 만으로 회귀 확인. 향후 별도 phase.
- **시각 검증 limitation**: subagent 환경에서 dev server / 브라우저 검증 어려움 — 사용자가 PR 머지 전 직접. 본 plan 의 "Step 2 사용자 시각 검증" 체크리스트 7개 항목.
- **HandoffHistory + Reprocess**: 같은 모달 안 합치지 않음 — handoff 는 처리 결과, git-event 는 raw 수신 이벤트, 다른 list. reprocess 호출 site 는 후속 (`useReprocessEvent` hook 만 만들어둠).
- **Webhook 등록 버튼 UX**: `git_repo_url` + `has_github_pat` 둘 다 있어야 enabled. 한쪽이라도 없으면 disabled (modal 안에서 시각적으로 안내).

---
```

- [ ] **Step 4: handoff + plan commit + push + PR**

```bash
git add handoffs/main.md docs/superpowers/plans/2026-04-30-phase-5b-frontend-ui.md
git commit -m "docs(handoff+plan): Phase 5b 완료 + 다음 할 일"

git push -u origin feature/phase-5b-frontend-ui

gh pr create --title "feat: Phase 5b — Frontend UI" --body "$(cat <<'EOF'
## Summary

Phase 5 의 frontend 절반 — 사용자가 pslog UI 에서 git 연동 설정 + webhook 자동 등록 + handoff 이력 조회 가능. Phase 5 핵심 가치 전달 완료.

- Backend `TaskResponse` 에 Phase 1 모델 누락 4 필드 노출 (`source / external_id / last_commit_sha / archived_at`)
- frontend `types/git.ts` + `Task` 확장
- `services/api.ts` `git` 그룹
- `hooks/useGithubSettings.ts` 5 훅
- `ProjectGitSettingsModal` — repo URL / PAT / plan_path / handoff_dir 폼 + Webhook 등록 (PAT admin:repo_hook 가이드)
- `HandoffHistoryModal` — branch 필터 + 목록 표
- `TaskCard` — `SYNCED_FROM_PLAN` 배지

## Test plan (자동)

- [x] `pytest -q` — backend 170 tests pass
- [x] `bun run build` — frontend tsc + vite 통과
- [x] `bun run lint` — eslint 통과

## Test plan (수동 — 시각 검증 필수)

> subagent 가 실행 못 하므로 머지 전 사용자 직접 dev server 검증 필요:

```bash
cd frontend && bun run dev
```

- [ ] 로그인 → 프로젝트 선택
- [ ] Sidebar ProjectItem 메뉴 → "Git 연동 설정" 클릭 → 모달 열림
- [ ] repo URL / PAT / plan_path 입력 → "저장" → 성공 메시지
- [ ] "Webhook 등록" 버튼 → 응답 표시 (실 GitHub API 호출 — PAT 권한 필요)
- [ ] Sidebar ProjectItem 메뉴 → "Handoff 이력" → 빈 상태 또는 row 표시
- [ ] 칸반 보드 — `source = synced_from_plan` 인 task 에 PLAN 배지

## Phase 5 follow-up 트래킹 (post-merge)

- I-2 (concurrent webhook race), I-4 (reprocess race), M-6 (last_synced_commit_sha 미사용), M-10 (private import)
- TaskCard ⚠️ handoff 누락 표시 — 데이터 정의 후속

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Phase 5b 완료 기준 (Acceptance)

- [ ] `TaskResponse` 가 `source / external_id / last_commit_sha / archived_at` 노출 + 회귀 테스트
- [ ] frontend `Task` 타입에 4 필드 + `TaskSource` enum
- [ ] `services/api.ts` `git` 그룹 5 method
- [ ] `useGithubSettings.ts` 5 훅 (queryKey 일관성, invalidate 정확)
- [ ] `ProjectGitSettingsModal` 가 repo URL / PAT / paths 입력 + Save + Webhook 등록 + admin:repo_hook 가이드 텍스트 포함
- [ ] `HandoffHistoryModal` 가 branch 필터 + 목록 (날짜/브랜치/작성자/commit/count)
- [ ] `TaskCard` 가 `SYNCED_FROM_PLAN` 배지 표시 (`MANUAL` 기본)
- [ ] sidebar `ProjectItem` 에 두 모달 트리거
- [ ] backend 170 tests pass, frontend `bun run build` + `bun run lint` 통과
- [ ] 사용자 시각 검증 체크리스트 PR 본문 포함
