# Phase 6 — Discord 알림 통합 본편 (Design)

**Status**: Draft → 사용자 검토 후 implementation plan 작성 (`writing-plans`).

**Date**: 2026-05-01

**Goal**: spec §11 (`2026-04-26-ai-task-automation-design.md`) 의 Phase 6 본편. push 1건 = 알림 1건 요약 (체크 변경 / 롤백 / archived / handoff 누락) + Discord webhook 3회 연속 실패 시 자동 disable + UI 재활성화. B2 의 sync-failure alert 도 같은 dispatcher 통과해 cooldown / disable 정책 통합 적용.

**선행**: pslog `main` = `29c7db7` (B2 PR #14 머지 직후). backend tests 184 baseline. alembic head = `a1b2c3d4e5f6`. 새 alembic revision 1건 (Project 2 컬럼 추가).

---

## 1. Scope

본 phase 의 deliverable:

1. **`notification_dispatcher` 신규 서비스** — 모든 Discord 알림의 1차 진입점. disable 체크, send 실패 시 counter 증가, 3회 연속 실패 시 자동 disable.
2. **Project 모델 2 컬럼 추가** — `discord_consecutive_failures: int = 0`, `discord_disabled_at: datetime | None`. alembic 마이그레이션 1건.
3. **Push 알림 (success path)** — 한 push 가 일으킨 모든 변경을 한 메시지로 요약. 체크 / 롤백 / archived / handoff 누락 4 카테고리 (변경 없는 카테고리는 줄 생략).
4. **`_apply_plan` / `_apply_handoff` 시그니처 변경** — 변경 요약 dict 도 return. `process_event` 가 모아서 dispatcher 호출.
5. **B2 sync-failure alert 리팩토링** — `sync_service` 의 직접 `send_webhook` 호출을 `notification_dispatcher` 경유로 변경. cooldown / disable 정책 자동 적용.
6. **Backend endpoint** — `POST /git-settings/discord-reset` (OWNER 전용, 비활성화 해제).
7. **`GitSettingsResponse` 확장** — `discord_enabled / discord_disabled_at / discord_consecutive_failures` 3 필드.
8. **Frontend** — `ProjectGitSettingsModal` 에 "Discord 알림" 섹션 (상태 + 재활성화 버튼).

본 phase 가 **하지 않는** 것:
- 알림 종류별 on/off (예: archived 만 끄기) — YAGNI. 종류별 컬럼 / 옵션 미도입.
- 신규 SYNCED task 생성 알림 — sprint 초 PLAN 작성 시 noise 폭발. 옵션 컬럼 안 만듦.
- Sliding-window cooldown — "3회 연속 실패 = disable" 만. burst 차단은 push 단위 묶음으로 자연 해소.
- Phase 7 (Gemma 브리핑) 진입 — 본 phase 안정화 후 별개 trigger.
- error-log spec 진입 — 별개 trigger.
- frontend 단위 테스트 인프라 도입 (Vitest 미도입 그대로).

---

## 2. Important Contracts

### 2.1. 알림 묶음 단위 — push 1건 = 알림 1건

같은 push 가 여러 변경 (체크 1+, 롤백 1+, archived 1+) 일으키면 **한 메시지** 로 요약. 사용자가 push/commit 단위로 사고 — 알림도 같은 단위가 자연스러움. burst 자연 차단 (push 빠르면 알림도 빠르지만 1건씩).

### 2.2. handoff 누락 정의 — 같은 push 안에서

`plan_changed = True AND handoff_present = False` → push 알림에 "⚠️ handoff 누락" 줄 추가.

- `plan_changed` = `_collect_changed_files` 결과에 `Project.plan_path` 포함
- `handoff_present` = `handoffs/{branch}.md` fetch 가 200 (정상 파싱) 또는 changed_files 에 handoff 경로 포함

spec §11.1 (`CLAUDE.md` 규칙) "**git push 직전 반드시** handoff commit" 정책 직접 매칭. 시간 기반 grace 또는 별도 background scheduler 없음 — 정책의 strictness 유지 (Decision Log 2026-04-26 Rev2).

별도 알림 아니고 그 push 알림 안에 한 줄 (옵션 A 결정과 일관).

### 2.3. webhook 실패 → 자동 disable

`Project.discord_consecutive_failures: int = 0` + `Project.discord_disabled_at: datetime | None`.

- send 성공 시: counter > 0 이면 0 reset
- send 실패 시 (httpx exception, 4xx/5xx 등 모든 실패): counter += 1
- counter >= 3 도달 시: `disabled_at = now()` 설정
- `disabled_at IS NOT NULL` → dispatcher 가 해당 project 의 모든 후속 알림 skip
- 사용자가 GitSettings 모달에서 "재활성화" 클릭 → counter = 0 + disabled_at = NULL
- 사용자가 PATCH `/git-settings` 으로 `discord_webhook_url` 갱신 → 같은 트랜잭션에서 자동 reset (URL 바뀌면 새 시작)

workers=1 가정 (spec §3) → race condition 없음. counter 갱신은 dispatcher 의 같은 함수 안에서 발생.

### 2.4. dispatcher 통합 — sync-failure alert 도 통과

B2 의 `process_event` except 분기 끝에서 직접 `discord_service.send_webhook(...)` 호출하던 부분을 `notification_dispatcher.dispatch_discord_alert(db, project, content)` 로 교체. 이렇게 하면 sync-failure 도 disable 정책 적용.

### 2.5. 알림 미발송 조건 (return early)

dispatcher 가 다음 경우 알림 안 함:
- `project.discord_webhook_url is None`
- `project.discord_disabled_at is not None`
- (caller 단계) push summary 가 빈 변경 + handoff_present → `_format_push_summary` 가 None return → caller 가 dispatcher 호출 안 함

### 2.6. 메시지 포맷 (5 종류)

#### Push summary (success path, 4 카테고리)

```
📦 alice 가 main 에 push (commit `abc1234`)
✅ 완료 (2):
  • [task-001] 구글 로그인 통합
  • [task-003] PDF export 버그 수정
↩️ 되돌림 (1):
  • [task-007] 결제 모듈 — DONE → TODO
🗑️ PLAN 에서 제거 (1):
  • [task-009] 구버전 마이그레이션 (archived)
⚠️ handoff 누락 — handoffs/main.md 갱신 필요
```

- 빈 카테고리 줄 자체 생략
- task ID + title (사용자가 어떤 task 인지 식별 가능)
- pusher = `event.pusher`
- branch = `event.branch`
- short commit = `event.head_commit_sha[:7]`
- 모든 카테고리 비고 + handoff 정상 → None return → 알림 안 함

#### Sync failure (B2 그대로, dispatcher 통과로만 변경)

```
⚠️ pslog sync 실패 — alice's project
branch: `main`
commit: `abc1234`
error: ```RuntimeError: github 502```
```

(B2 코드 그대로. dispatcher 경유로만 변경.)

### 2.7. 알림 발송 시점 — commit 후

`process_event` 의 `await db.commit()` (success path) 이후 dispatcher 호출. DB 일관 상태에서 발송. 알림 실패는 silent — 메인 처리 영향 없음.

**ORM expire 처리**:
- **success path**: commit 후에도 `expire_on_commit=False` 가 보호 → ORM 살아있음 → dispatcher 가 직접 `project.discord_webhook_url` 등 읽기 안전.
- **except path** (B1 함정 재발 위험): rollback 이 ORM 을 expire 시킴 (`expire_on_commit=False` 는 commit 만 보호, rollback 은 무관). 4 값 (project_name / event_branch / event_head_sha / discord_webhook_url) capture 는 B2 패턴 그대로 유지하되, dispatcher 호출 직전에 **`await db.refresh(project)` 1줄** 추가 — dispatcher 안에서 project.discord_webhook_url / .discord_disabled_at / .discord_consecutive_failures 읽기/쓰기가 안전해짐. capture 한 `discord_webhook_url` 은 refresh 비용 회피용 1차 게이트 (URL 미설정이면 refresh 도 skip).

---

## 3. Backend Architecture

### 3.1. 신규 서비스: `notification_dispatcher.py`

**책임**: Discord 알림 1점 진입. URL/disable 체크, send 호출, 실패 시 counter 갱신, 3회 도달 시 disable.

```python
"""Discord 알림 dispatcher — disable 정책 통합 진입점.

설계서: 2026-05-01-phase-6-discord-notifications-design.md §3.1
"""
import logging
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.project import Project
from app.services import discord_service

logger = logging.getLogger(__name__)

DISABLE_THRESHOLD = 3


async def dispatch_discord_alert(
    db: AsyncSession,
    project: Project,
    content: str,
) -> None:
    """Discord 알림 1점 진입. URL NULL / disabled 체크 + 실패 시 counter 갱신.

    실패가 메인 처리에 영향 안 가도록 catch — caller 는 await 만 하면 됨.
    counter 갱신은 같은 session 의 commit 으로 영속.
    """
    if project.discord_webhook_url is None:
        return
    if project.discord_disabled_at is not None:
        logger.info(
            "Discord disabled for project %s since %s — skip",
            project.id, project.discord_disabled_at,
        )
        return

    try:
        await discord_service.send_webhook(content, project.discord_webhook_url)
        # 성공 — counter > 0 이면 reset
        if project.discord_consecutive_failures > 0:
            project.discord_consecutive_failures = 0
            try:
                await db.commit()
            except Exception:
                logger.exception("Failed to reset failure counter for project %s", project.id)
    except Exception:
        logger.exception("Discord alert failed for project %s", project.id)
        project.discord_consecutive_failures += 1
        if project.discord_consecutive_failures >= DISABLE_THRESHOLD:
            project.discord_disabled_at = datetime.utcnow()
            logger.warning(
                "Discord auto-disabled for project %s after %d consecutive failures",
                project.id, project.discord_consecutive_failures,
            )
        try:
            await db.commit()
        except Exception:
            logger.exception("Failed to record Discord failure counter for project %s", project.id)
```

### 3.2. `Project` 모델 2 컬럼 추가

`backend/app/models/project.py`:

```python
discord_consecutive_failures: Mapped[int] = mapped_column(default=0, nullable=False)
discord_disabled_at: Mapped[datetime | None] = mapped_column(default=None)
```

`__init__` override 에 default 추가:
```python
kwargs.setdefault("discord_consecutive_failures", 0)
```

`discord_disabled_at` 은 NULL default 라 `__init__` 에 추가 안 함.

### 3.3. alembic 마이그레이션

새 revision (auto-generated 권장 — 두 컬럼 추가는 단순):
- `add_column projects.discord_consecutive_failures INTEGER NOT NULL DEFAULT 0`
- `add_column projects.discord_disabled_at TIMESTAMP NULL`
- downgrade: `drop_column` 양쪽

기존 row 는 default 0 / NULL 자동 적용 → 모든 프로젝트 활성 상태로 시작.

### 3.4. `sync_service` 변경

**`_apply_plan` 시그니처 변경**: `PlanChanges` dataclass return:

```python
from dataclasses import dataclass, field

@dataclass
class PlanChanges:
    checked: list[tuple[str, str]] = field(default_factory=list)    # [(external_id, title)]
    unchecked: list[tuple[str, str]] = field(default_factory=list)  # 롤백
    archived: list[tuple[str, str]] = field(default_factory=list)
    # 신규 task 는 알림 안 함 (sprint 초 noise 폭발 회피, YAGNI)

    def has_changes(self) -> bool:
        return bool(self.checked or self.unchecked or self.archived)
```

`_apply_plan` 내부 (체크 / 언체크 / archived 분기) 에서 `PlanChanges` 인스턴스 mutate 하면서 return.

**`_apply_handoff` 시그니처 변경**: 단순 `bool` return — handoff 정상 INSERT 됐으면 True. (UNIQUE conflict savepoint skip 도 True — handoff 가 DB 에 존재하면 OK).

**`process_event` 통합**:

```python
try:
    plan_changes: PlanChanges | None = None
    handoff_present = False

    if plan_changed and project.git_repo_url is not None:
        plan_text = await fetch_file(...)
        if plan_text is not None:
            plan_changes = await _apply_plan(...)
        # plan_text None 이면 plan_changes 도 None — 알림 카테고리 없음

    if handoff_changed and project.git_repo_url is not None:
        handoff_text = await fetch_file(...)
        if handoff_text is not None:
            await _apply_handoff(...)
            handoff_present = True

    project.last_synced_commit_sha = event.head_commit_sha
    event.processed_at = datetime.utcnow()
    await db.commit()

    # 알림 — commit 후 (DB 일관 상태)
    handoff_missing = plan_changed and not handoff_present
    if (plan_changes and plan_changes.has_changes()) or handoff_missing:
        content = _format_push_summary(
            project_name=project.name,
            pusher=event.pusher,
            branch=event.branch,
            short_sha=event.head_commit_sha[:7],
            plan_changes=plan_changes,
            handoff_missing=handoff_missing,
        )
        if content:
            await notification_dispatcher.dispatch_discord_alert(db, project, content)

except Exception as exc:
    # 기존 except 분기 — B2 의 4 값 capture 패턴 유지 (rollback 후 ORM expire)
    # ... rollback / autoflush=False / event.error 기록 / commit / autoflush=True ...

    # B1 의 rollback 후 ORM expire 함정: project 인스턴스는 expired 상태.
    # `expire_on_commit=False` 는 commit 만 보호 — rollback 은 그대로 expire.
    # dispatcher 가 project.discord_webhook_url / .discord_disabled_at 읽으므로 refresh 필요.
    if discord_webhook_url:  # 기존 capture 값으로 1차 게이트 (refresh 비용 회피)
        try:
            await db.refresh(project)  # ORM 살리기 — 이후 dispatcher 안전
            content = (
                f"⚠️ **pslog sync 실패** — {project_name}\n"
                f"branch: `{event_branch}`\n"
                f"commit: `{event_head_sha[:7]}`\n"
                f"error: ```{error_msg[:500]}```"
            )
            await notification_dispatcher.dispatch_discord_alert(db, project, content)
        except Exception:
            logger.exception("Failed to dispatch Discord alert for event %s", event_id)
```

`_format_push_summary` 신규 함수 (sync_service.py 내부 또는 별 utility):

```python
def _format_push_summary(
    *,
    project_name: str,
    pusher: str,
    branch: str,
    short_sha: str,
    plan_changes: PlanChanges | None,
    handoff_missing: bool,
) -> str | None:
    """push summary 메시지 생성. 모든 카테고리 비고 + handoff 정상 → None."""
    has_plan = plan_changes is not None and plan_changes.has_changes()
    if not has_plan and not handoff_missing:
        return None

    lines = [f"📦 {pusher} 가 {branch} 에 push (commit `{short_sha}`)"]

    if has_plan:
        if plan_changes.checked:
            lines.append(f"✅ 완료 ({len(plan_changes.checked)}):")
            for ext_id, title in plan_changes.checked:
                lines.append(f"  • [{ext_id}] {title}")
        if plan_changes.unchecked:
            lines.append(f"↩️ 되돌림 ({len(plan_changes.unchecked)}):")
            for ext_id, title in plan_changes.unchecked:
                lines.append(f"  • [{ext_id}] {title} — DONE → TODO")
        if plan_changes.archived:
            lines.append(f"🗑️ PLAN 에서 제거 ({len(plan_changes.archived)}):")
            for ext_id, title in plan_changes.archived:
                lines.append(f"  • [{ext_id}] {title} (archived)")

    if handoff_missing:
        lines.append(f"⚠️ handoff 누락 — handoffs/{branch}.md 갱신 필요")

    return "\n".join(lines)
```

### 3.5. 신규 endpoint — `POST /git-settings/discord-reset`

```python
@router.post(
    "/{project_id}/git-settings/discord-reset",
    response_model=GitSettingsResponse,
)
async def reset_discord(
    project_id: UUID,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
):
    """Discord 알림 비활성화 해제 — counter 0 + disabled_at NULL.
    설계서: 2026-05-01-phase-6-discord-notifications-design.md §3.5
    """
    project = await project_service.get_project(db, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    role = await get_effective_role(db, user.id, project_id)
    if role is None:
        raise HTTPException(status_code=404, detail="Project not found")
    if not can_manage(role):
        raise HTTPException(status_code=403, detail="Owner only")

    project.discord_consecutive_failures = 0
    project.discord_disabled_at = None
    await db.commit()
    await db.refresh(project)

    return _build_git_settings_response(project)  # 기존 응답 builder 재사용 또는 인라인
```

### 3.6. `GitSettingsResponse` 3 필드 추가

```python
class GitSettingsResponse(BaseModel):
    # ... 기존 필드
    discord_enabled: bool                       # url not None AND disabled_at is None
    discord_disabled_at: datetime | None
    discord_consecutive_failures: int           # UI 디버깅용 (선택 표시)
```

`discord_enabled` 는 backend 가 계산해서 response 만들 때 set (computed). `GET /git-settings` + `PATCH /git-settings` + `POST /discord-reset` 모두 이 응답 형태.

### 3.7. PATCH `/git-settings` 확장

`discord_webhook_url` 이 update 데이터에 포함되면 같은 트랜잭션에서 reset:

```python
if "discord_webhook_url" in data:
    project.discord_webhook_url = data["discord_webhook_url"]
    project.discord_consecutive_failures = 0
    project.discord_disabled_at = None
```

(URL 바뀌었으면 새로운 정상 상태로 시작 — disabled 상태가 stale URL 에 묶이지 않게.)

`GitSettingsUpdate` 스키마에 `discord_webhook_url: str | None = None` 추가 (현재는 PATCH 가 git 관련 필드만 처리 — Discord 도 같은 PATCH 로 받게 확장).

---

## 4. Frontend Architecture

### 4.1. 타입 / API service

`frontend/src/types/git.ts`:

```typescript
export interface GitSettings {
  // ... 기존
  discord_enabled: boolean;
  discord_disabled_at: string | null;       // ISO datetime
  discord_consecutive_failures: number;
}

export interface GitSettingsUpdate {
  // ... 기존
  discord_webhook_url?: string | null;
}
```

`frontend/src/services/api.ts` `git` 그룹에 method 1개 추가:

```typescript
resetDiscord: (projectId: string) =>
  apiClient.post<GitSettings>(`/projects/${projectId}/git-settings/discord-reset`),
```

### 4.2. TanStack Query hook

`frontend/src/hooks/useGithubSettings.ts` 끝에 추가:

```typescript
export function useResetDiscord(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => api.git.resetDiscord(projectId).then((r) => r.data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['projects', projectId, 'git-settings'] });
    },
  });
}
```

### 4.3. `ProjectGitSettingsModal.tsx` Discord 섹션

기존 git 설정 폼 끝에 새 섹션:

```tsx
<div className="border-t-2 border-black pt-4 mt-4">
  <h3 className="font-black text-sm mb-2">Discord 알림</h3>
  <label className="block text-xs font-medium mb-1">Webhook URL</label>
  <input
    type="text"
    value={discordUrl}
    onChange={(e) => setDiscordUrl(e.target.value)}
    placeholder="https://discord.com/api/webhooks/..."
    className="..."
  />
  <div className="mt-2 text-xs">
    상태: {settings.discord_enabled ? (
      <span className="text-green-700">✅ 활성</span>
    ) : settings.discord_disabled_at ? (
      <span className="text-red-700">
        ⚠️ 비활성화 ({settings.discord_consecutive_failures}회 연속 실패)
      </span>
    ) : (
      <span className="text-muted-foreground">⚪ 미설정</span>
    )}
  </div>
  {settings.discord_disabled_at && (
    <button
      type="button"
      onClick={() => resetDiscord.mutate()}
      disabled={resetDiscord.isPending}
      className="mt-2 px-2 py-1 text-xs border-2 border-black ..."
    >
      {resetDiscord.isPending ? '재활성화 중...' : '재활성화'}
    </button>
  )}
</div>
```

URL input 의 저장은 기존 `useUpdateGitSettings` 가 처리 — 폼 전체 Save 버튼이 PATCH `/git-settings` 호출. Discord URL 도 같은 PATCH 에 포함되어 backend 가 reset 까지 자동.

재활성화 onError 분기: alert (`detail || message || "재활성화 실패"`). onSuccess → alert "재활성화 완료" 또는 toast 자리 (alert 패턴 그대로).

### 4.4. 시각 검증 (사용자 dev server)

- Discord URL 비어있을 때: "⚪ 미설정"
- Discord URL 있고 정상 (`disabled_at IS NULL`): "✅ 활성", 재활성화 버튼 미노출
- 의도적 실패 3회 후: "⚠️ 비활성화 (3회 연속 실패)", 재활성화 버튼 노출 → 클릭 → 활성화 복구
- URL 변경 + 저장 → counter/disabled_at 자동 reset → "✅ 활성"
- handoff 누락한 PLAN-only push → push 알림에 "⚠️ handoff 누락" 줄
- 체크 + 롤백 + archived 섞인 push → 한 메시지에 모든 카테고리 표시

---

## 5. Test Plan

### 5.1. Backend 신규 (예상 +12 tests, 184 → 196)

**`test_notification_dispatcher.py`** (신규, 4건):
1. `discord_webhook_url IS NULL` → no-op (send_webhook 호출 안 함)
2. `discord_disabled_at IS NOT NULL` → no-op
3. send 성공 시 `discord_consecutive_failures > 0` 이면 0 reset
4. send 실패 시 counter +1; 3 도달 시 `disabled_at = now()`

**`test_sync_service.py` 추가** (3건):
1. 정상 push (체크 + 롤백 + archived 1+개씩) → dispatcher 호출, content 에 모든 카테고리 줄 포함
2. handoff 누락 push (PLAN 변경 + handoff fetch 404) → content 에 "⚠️ handoff 누락 — handoffs/{branch}.md 갱신 필요"
3. empty push (PLAN/handoff 변경 없음) → dispatcher 호출 안 함

**`test_sync_service.py` 갱신** (B2 의 sync-failure 3건):
- mock target 을 `discord_service.send_webhook` 직접 → `notification_dispatcher.dispatch_discord_alert` 로 변경 (또는 dispatcher 가 거치는 같은 send_webhook 으로 유지 — 둘 다 동작). 테스트 의도 보존.

**`test_git_settings_endpoint.py`** (3건):
1. POST `/discord-reset` 정상 (OWNER) → counter/disabled_at 0/NULL, response 의 `discord_enabled = true`
2. POST `/discord-reset` 비-OWNER (EDITOR/VIEWER) → 403
3. POST `/discord-reset` 비-멤버 → 404

**`test_phase6_migration.py`** (신규, 2건):
1. alembic upgrade head → `discord_consecutive_failures` / `discord_disabled_at` 컬럼 존재
2. 기존 row 의 default 값 (`0` / `NULL`) — 마이그레이션 회귀

### 5.2. Frontend

`bun run build` clean + `bun run lint` 신규 위배 0 (Phase 5b/B2 의 8 pre-existing 그대로).

### 5.3. e2e (사용자, PR 머지 전)

- Discord 채널 연결 후 의도적 PLAN.md 변경 push → pslog Discord 채널에 push summary 도착
- handoff 안 만들고 PLAN 만 변경 push → "⚠️ handoff 누락" 줄 포함 알림
- Discord webhook URL 잘못된 값으로 변경 후 push 3번 → 3번째 후 자동 disable, GitSettings 모달에 "⚠️ 비활성화" 표시
- 재활성화 버튼 클릭 → counter/disabled_at reset, 다음 push 부터 다시 알림

---

## 6. Decision Log

- **알림 묶음 단위**: push 1건 = 알림 1건 요약 (옵션 A) vs 종류별 분리 (B) vs per-task (C). A 채택 — burst 차단 자연 해소, 사용자가 push 단위 사고와 일치.
- **handoff 누락 트리거**: 같은 push 안에서 PLAN 변경 + handoff 부재 (옵션 A) vs 시간 grace (B) vs 매 push 평가 (C). A 채택 — spec §11.1 의 "**push 직전 반드시** handoff" 강제 정책 직접 매칭. grace 는 정책을 약화시킴 (Decision Log 2026-04-26 Rev2 "silent 옵션 X" 와 어긋남).
- **failure 추적 방식**: DB 2 컬럼 (옵션 A) vs in-memory (B) vs sliding window (C). A 채택 — workers=1 가정이라 race 없음, 영속적 (재시작 후 유지), UI 노출 가능.
- **Frontend 노출 위치**: GitSettings 모달 (옵션 A) vs ProjectItem dropdown (B) vs UI 없음 (C). A 채택 — webhook 설정과 같은 곳, 자연 발견성.
- **신규 task INSERT 알림**: 기본 OFF — sprint 초 PLAN 작성 시 noise 폭발. 옵션 컬럼 안 만듦 (YAGNI, 사용자 호소 시 후속).
- **알림 종류별 on/off 미도입** — `discord_webhook_url` NULL + `discord_disabled_at` 만으로 사용자 제어 가능. 종류별 필터는 사용자 호소 시 후속.
- **Sliding-window cooldown 미도입** — "3회 연속 실패 = disable" 만. burst 차단은 push 단위 묶음으로 자연 해소.
- **B2 sync-failure 리팩토링**: 같은 dispatcher 통과 → cooldown / disable 정책 통합. mock target 만 변경하면 됨 (or dispatcher 의 send_webhook 호출이 같은 것이라 mock 그대로도 동작).
- **알림 발송 시점**: `process_event` 의 `db.commit()` (success path) 또는 except 분기의 commit 직후 → DB 일관 상태. B1 의 rollback 후 ORM expire 함정은 success path 에서는 발생 안 함 (commit 직전까지 ORM 살아있음). except path 만 4 값 try block 전 capture 유지 (B2 패턴).
- **`PlanChanges` dataclass return**: `_apply_plan` mutation 외에 변경 요약 dict 도 return. 함수 순수성 유지 (mutable global state 안 씀). `process_event` 가 모아서 dispatcher 호출.
- **`_apply_handoff` 단순 `bool` return** — handoff 정상 INSERT/exists 됐으면 True. `process_event` 가 `handoff_missing = plan_changed and not handoff_present` 로 판정.

---

## 7. Phase 7 와의 관계 (참고)

본 phase 가 끝나면 spec §11 의 Phase 1~6 모두 완료 — task-automation 핵심 가치 + 사용성 + 알림 통합 완성. Phase 7 (Gemma 브리핑) 은 부가가치이므로 안정화 후 별개 trigger.

본 phase 의 `notification_dispatcher` 는 Phase 7 의 brief 알림에도 재사용 가능 (브리핑 완료 알림 등 — 후속 결정).

---

## 8. Open Questions

본 phase 진입 전 답할 것 없음. 시각 검증 시점 또는 사용자 피드백 후 결정할 항목 2건:

1. **알림 메시지 한국어 vs 영어** — 본 spec 은 한국어 예시. 사용자 채널이 영어 권 사용자 섞여있으면 후속 고려. 현재는 한국어 (codebase 컨벤션).
2. **재활성화 onSuccess 토스트** — 현재 alert. toast system 도입 시 일괄 교체 (Phase 5b/B2 패턴 일관).
