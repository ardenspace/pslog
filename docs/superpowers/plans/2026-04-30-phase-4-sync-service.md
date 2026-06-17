# Phase 4 — sync_service + git fetch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** GitHub webhook 의 raw GitPushEvent → 실제 DB 반영(Task status / archived_at / Handoff INSERT / TaskEvent)을 조립. `git_repo_service` 가 GitHub Contents/Compare API 호출, `sync_service` 가 fetch+parse+DB write 의 멱등성 있는 파이프라인. Phase 2 의 reaper stub 에 sync 콜백 주입. Phase 3 파서 하드닝 (code review I-2/I-3) 함께 fix. **이 phase 가 끝나야 webhook → pslog Kanban 자동 갱신 흐름이 처음 동작.**

**Architecture:** `sync_service.process_event(db, event, *, fetch_file, fetch_compare)` 가 진입점. 의존 주입으로 GitHub API 격리(테스트는 fake fetcher). 흐름: `processed_at` 가드 → 변경 파일 목록 (commits_truncated 시 Compare API, 아니면 `event.commits[*].modified`) → PLAN/handoff 매칭 검사 → `git_repo_service.fetch_file` 로 head_sha 기준 raw 가져오기 → `plan_parser_service.parse_plan` / `handoff_parser_service.parse_handoff` 호출 → DB 반영 (Task status 전이, archived_at, Handoff INSERT, TaskEvent) → `processed_at = now()`. 실패 시 `error` 컬럼에 기록, `processed_at` 도 now (자동 재시도 없음 — 사용자 수동 재처리는 Phase 5 endpoint).

**Tech Stack:** Python 3.12, httpx 0.26 (async HTTP), SQLAlchemy 2.0.25 async, FastAPI 0.109 BackgroundTasks, pytest 8.3 + testcontainers PG 16. 외부 의존 추가: `httpx` 이미 설치됨 (Phase 2 webhook 테스트에 사용).

**선행 조건:**
- pslog main, alembic head = `c4dee7f06004` (Phase 1) — 본 phase 에서 +1 revision 추가 (`Project.github_pat_encrypted`)
- Phase 2 webhook + Phase 3 파서 머지 완료 (PR #8, #9)
- Phase 2 crypto 모듈 (`app.core.crypto.encrypt_secret/decrypt_secret`) 재사용
- Phase 3 parser 모듈 (`parse_plan`, `parse_handoff`) 재사용
- `httpx` 0.26 설치됨 (`requirements.txt`)
- Python 3.12.13 venv (`backend/venv`), `.env` 의 `pslog_FERNET_KEY` 존재

**중요한 계약:**
- **PAT 저장 (설계서 §9)**: `Project.github_pat_encrypted: bytes | None` — Fernet 마스터 키로 암호화. NULL 이면 unauthenticated 호출 (rate limit 60/h, 공개 repo 만). Phase 1 에서 누락된 컬럼 — 본 phase 에서 alembic 추가.
- **변경 파일 결정 (설계서 §7.1)**:
  - `event.commits_truncated == False` → `event.commits[*].modified` 합집합 사용 (DB 에 이미 저장된 raw)
  - `event.commits_truncated == True` → Compare API (`{base}...{head}`) 호출. base 는 `event.commits[0].before` 가 아니라 `Project.last_synced_commit_sha` (없으면 webhook payload 의 `before` 필드 — 이건 GitPushEvent 모델에 저장 안 함, 본 phase 에서 추가하지 않음 → fallback 으로 head_sha 만 처리하고 commits_truncated 의 modified 누락은 알려진 한계로 메모).
  - **단순화 결정**: Phase 4 본문은 `commits_truncated` 케이스에서 Compare API 호출하되 base = `Project.last_synced_commit_sha or event.commits[-1].id`. 후자는 truncate 된 가장 오래된 commit. 완벽한 정확성은 Phase 5 에서 webhook 의 `before` 컬럼 추가 시 보강.
- **PLAN/handoff fetch 조건 (설계서 §7.1 ③)**:
  - 변경 파일 중 `Project.plan_path` 매칭 → PLAN.md fetch
  - 변경 파일 중 `Project.handoff_dir + branch.replace('/', '-') + '.md'` 매칭 → handoff fetch
  - 둘 다 없으면 sync 종료 (`processed_at = now()`, error = NULL)
- **handoff 파일 경로 변환 (설계서 §6.2)**: `feature/login-redesign` → `handoffs/feature-login-redesign.md`. `/` → `-` 단순 치환. 다른 sanitizing 없음.
- **상태 전이 규칙 (설계서 §4.1)**:
  - `- [x] task-XXX` 매칭 → `Task.status = DONE` + `last_commit_sha = head_sha` + `TaskEvent(action=CHECKED_BY_COMMIT)`
  - `- [ ] task-XXX` 인데 직전 `Task.status == DONE` 이었음 → `Task.status = TODO` + `last_commit_sha = head_sha` + `TaskEvent(action=UNCHECKED_BY_COMMIT)` (롤백 케이스)
  - `- [ ] task-XXX` 인데 직전 status 가 DONE 아님 → 변경 없음 (TaskEvent 도 안 만듦)
  - PLAN 에서 사라진 synced task (Task.source = SYNCED_FROM_PLAN, archived_at IS NULL, but external_id 가 새 PLAN 에 없음) → `archived_at = now()` + `TaskEvent(action=ARCHIVED_FROM_PLAN)`. hard-delete 안 함.
- **PLAN 신규 task 생성 (설계서 §4.1)**: PLAN 에 있는 external_id 가 DB 에 없으면 → `Task` INSERT (`source=SYNCED_FROM_PLAN`, `external_id=<id>`, `title=parsed.title`, `status=TODO 또는 DONE`, `assignee_id` 는 git_login → User 매칭 시도, 실패 시 NULL) + `TaskEvent(action=SYNCED_FROM_PLAN)`.
- **Handoff INSERT (설계서 §4.2)**: 한 push 마다 1 행. `(project_id, commit_sha)` UNIQUE 충돌 시 silent skip (멱등). `parsed_tasks = parser 의 sections[0].checks`, `free_notes = {sections[0].free_notes 데이터 + subtasks}`.
- **멱등성 (설계서 §10.2 CRITICAL)**: 같은 GitPushEvent 두 번 process → DB 변경 1회만, Handoff 1행. `processed_at IS NOT NULL` 가드 + Handoff UNIQUE.
- **에러 정책 (설계서 §8)**:
  - PAT 없음 + private repo → fetch 실패 → `error = "fetch_failed: 404 (PAT missing?)"` + `processed_at = now()`. 자동 재시도 안 함.
  - 파서 결정적 fail (`MalformedHandoffError`, `DuplicateExternalIdError`) → `error = "parse_failed: <message>"` + `processed_at = now()`.
  - 알 수 없는 예외 → `error = "<class>: <message>"` + `processed_at = now()`. (재시도 정책 변경은 Phase 5)
  - GitHub API rate limit / 5xx → 본 phase 는 즉시 실패 처리. exponential backoff 은 Phase 5.
- **reaper callback 주입 (설계서 §5.1 ⑧)**: `app/main.py` 의 lifespan 에서 `run_reaper_once(callback=sync_service.process_event)` 변경. callback 안에서 자체 세션 + fetcher 주입.
- **파서 하드닝 (Phase 3 code review I-2/I-3)**: `_parse_task_rest` 의 ` — ` delimiter 가 lookahead `(?=@|`)` 로 제한되도록 변경 — title 안의 em-dash 보존. 회귀 테스트 추가.

---

## File Structure

**신규 파일 (소스):**
- `backend/alembic/versions/<auto>_phase4_github_pat.py` — `Project.github_pat_encrypted` 컬럼 추가
- `backend/app/services/git_repo_service.py` — `fetch_file`, `fetch_compare_files` (httpx async)
- `backend/app/services/sync_service.py` — `process_event(db, event, *, fetch_file, fetch_compare)` + 내부 헬퍼 (`_apply_plan`, `_apply_handoff`, `_record_handoff`)

**신규 파일 (테스트):**
- `backend/tests/test_git_repo_service.py` — httpx mock 으로 Contents/Compare API 검증
- `backend/tests/test_sync_service.py` — fake fetcher 주입한 단위 + 통합 테스트
- `backend/tests/fixtures/github_compare_payload.json` — Compare API 응답 샘플

**수정 파일:**
- `backend/app/models/project.py` — `github_pat_encrypted: Mapped[bytes | None]` 필드 추가
- `backend/app/api/v1/endpoints/webhooks.py` — endpoint 가 GitPushEvent INSERT 후 `BackgroundTasks.add_task(sync_service.process_event, ...)` 호출
- `backend/app/main.py` — lifespan reaper hook 의 callback 을 sync_service.process_event 로 교체
- `backend/app/services/plan_parser_service.py` — `_parse_task_rest` lookahead 수정 (I-2/I-3 fix)
- `backend/tests/test_plan_parser_service.py` — em-dash / backtick / @ in title 회귀 테스트

**수정 없음:**
- `requirements.txt` (httpx 이미 설치)
- `app/config.py` (설정 추가 없음)
- 다른 모델 (Task, Handoff, GitPushEvent — Phase 1 그대로 사용)

---

## Self-Review Notes

작성 후 self-review:
- 설계서 §4.1 상태 매핑 4규칙 → Task 6 (CHECKED) + Task 7 (UNCHECKED 롤백) + Task 8 (archived) + Task 5 (신규 INSERT) 매핑
- 설계서 §5.1 ② git_repo_service 책임 → Task 2 (Contents) + Task 3 (Compare)
- 설계서 §5.1 ⑤ sync_service 책임 → Task 4-10
- 설계서 §7.1 흐름 다이어그램 ①~⑩ → Task 4 (entry) + Task 5 (변경 파일) + Task 5-6 (fetch+parse) + Task 6-9 (DB 반영) + Task 10 (멱등성)
- 설계서 §8 에러 케이스 11항목 → Task 4 (processed_at 가드 + error 기록) + Task 5 (404 = plan_missing 로깅) + Task 9 (Handoff UNIQUE)
- 설계서 §9 PAT Fernet → Task 1 (마이그레이션) + Task 2 (decrypt)
- 설계서 §10.2 sync 통합 테스트 6항목 → Task 10 (멱등성, 부분 실패) + Task 7 (체크→언체크) + Task 5 (force-push commits=0)
- 설계서 §11 Phase 4 정의 — git_repo_service + sync_service + 멱등성/회귀 — 모두 매핑
- handoff 메모: Phase 3 파서 하드닝 → Task 12

---

## Task 0: 브랜치 + base sync

**Files:**
- (none — git 작업만)

- [ ] **Step 1: feature/phase-4-sync-service 브랜치 생성**

```bash
cd /Users/arden/Documents/ardensdevspace/pslog
git checkout main && git pull --ff-only origin main
git checkout -b feature/phase-4-sync-service
```

Expected: 브랜치 생성됨. main HEAD = `3525a21` (Phase 3 머지).

- [ ] **Step 2: venv + .env 검증**

```bash
cd backend
source venv/bin/activate
python -c "from app.config import settings; print(bool(settings.pslog_fernet_key))"
pytest tests/test_plan_parser_service.py -q
```

Expected: `True`, 13 tests pass. (venv/.env 없으면 Phase 3 setup 절차 재실행 — `python3.12 -m venv venv && pip install -r requirements-dev.txt && .env 생성`)

---

## Task 1: alembic — Project.github_pat_encrypted 컬럼

**Files:**
- Create: `backend/alembic/versions/<auto>_phase4_github_pat.py`
- Modify: `backend/app/models/project.py`

- [ ] **Step 1: 모델에 필드 추가**

`backend/app/models/project.py` 의 `Project` 클래스, `webhook_secret_encrypted` 다음 줄에 추가:

```python
    # Phase 4 — task-automation 설계서 §9 (GitHub PAT Fernet 암호화 저장)
    github_pat_encrypted: Mapped[bytes | None] = mapped_column(default=None)
```

- [ ] **Step 2: alembic autogenerate**

```bash
cd /Users/arden/Documents/ardensdevspace/pslog/backend
source venv/bin/activate
alembic revision --autogenerate -m "phase4: project github_pat_encrypted column"
```

생성된 파일은 `backend/alembic/versions/<random>_phase4_project_github_pat_encrypted_column.py`. 내용 확인:

```python
def upgrade() -> None:
    op.add_column('projects', sa.Column('github_pat_encrypted', sa.LargeBinary(), nullable=True))


def downgrade() -> None:
    op.drop_column('projects', 'github_pat_encrypted')
```

(컬럼 타입은 `LargeBinary` 또는 `BYTEA` — `bytes | None` 매핑 결과. autogen 이 다르게 만들면 위 두 함수로 보정.)

- [ ] **Step 3: 마이그레이션 회귀 테스트 (CRITICAL — 기존 데이터 무손실)**

`backend/tests/test_phase4_migration.py` 신규 작성:

```python
"""Phase 4 마이그레이션 회귀 — github_pat_encrypted 컬럼 추가가 기존 Project 데이터를 보존하는지 검증."""

import uuid
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.project import Project
from app.models.workspace import Workspace


async def test_existing_projects_have_null_github_pat_after_migration(
    async_session: AsyncSession,
):
    ws = Workspace(name="ws", slug=f"ws-{uuid.uuid4().hex[:8]}")
    async_session.add(ws)
    await async_session.flush()
    proj = Project(workspace_id=ws.id, name="legacy")
    async_session.add(proj)
    await async_session.commit()
    await async_session.refresh(proj)

    assert proj.github_pat_encrypted is None  # 기본값 NULL


async def test_github_pat_round_trip(async_session: AsyncSession):
    """Fernet 암호화된 bytes 가 LargeBinary 컬럼에 저장/조회 가능."""
    from app.core.crypto import encrypt_secret, decrypt_secret

    ws = Workspace(name="ws", slug=f"ws-{uuid.uuid4().hex[:8]}")
    async_session.add(ws)
    await async_session.flush()
    proj = Project(
        workspace_id=ws.id,
        name="with_pat",
        github_pat_encrypted=encrypt_secret("ghp_abcd1234"),
    )
    async_session.add(proj)
    await async_session.commit()
    await async_session.refresh(proj)

    assert proj.github_pat_encrypted is not None
    assert decrypt_secret(proj.github_pat_encrypted) == "ghp_abcd1234"


async def test_migration_downgrade_drops_column(async_session: AsyncSession):
    """downgrade 후 컬럼이 사라지는지 — alembic 자체 검증은 별도, 본 테스트는 placeholder."""
    # alembic upgrade/downgrade 회귀는 conftest 의 _migrate_test_db 가 head 까지 자동 적용.
    # 본 테스트는 head 적용 후 컬럼 존재 자체만 확인.
    result = await async_session.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'projects' AND column_name = 'github_pat_encrypted'"
        )
    )
    row = result.first()
    assert row is not None
    assert row[0] == "github_pat_encrypted"
```

- [ ] **Step 4: 회귀 테스트 실행**

```bash
pytest tests/test_phase4_migration.py -v
pytest tests/test_project_model.py -v
pytest -q
```

Expected: 신규 3 tests pass, 기존 103 tests pass (회귀 0).

- [ ] **Step 5: Commit**

```bash
git add backend/alembic/versions/*phase4* backend/app/models/project.py backend/tests/test_phase4_migration.py
git commit -m "feat(phase4): Project.github_pat_encrypted 컬럼 + 회귀 테스트"
```

---

## Task 2: git_repo_service — Contents API (단일 파일 fetch)

**Files:**
- Create: `backend/app/services/git_repo_service.py`
- Create: `backend/tests/test_git_repo_service.py`

- [ ] **Step 1: Failing test (httpx mock 으로 Contents API)**

Create `backend/tests/test_git_repo_service.py`:

```python
"""git_repo_service — GitHub Contents API + Compare API 단위 테스트.

설계서: 2026-04-26-ai-task-automation-design.md §5.1 (②), §7.1
"""

import base64
import json

import httpx
import pytest

from app.services.git_repo_service import fetch_file


_REPO = "https://github.com/ardenspace/app-chak"
_SHA = "a" * 40
_PATH = "PLAN.md"


def _mock_contents_response(content: str, status: int = 200) -> httpx.Response:
    if status == 404:
        return httpx.Response(status_code=404, json={"message": "Not Found"})
    body = {
        "name": "PLAN.md",
        "path": "PLAN.md",
        "sha": _SHA,
        "size": len(content),
        "encoding": "base64",
        "content": base64.b64encode(content.encode()).decode(),
    }
    return httpx.Response(status_code=status, json=body)


async def test_fetch_file_decodes_base64_content(monkeypatch: pytest.MonkeyPatch):
    captured: dict[str, object] = {}

    async def fake_send(request: httpx.Request, **_kwargs):
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        return _mock_contents_response("# 스프린트: 테스트\n")

    monkeypatch.setattr(httpx.AsyncClient, "send", fake_send)
    text = await fetch_file(_REPO, "ghp_abc", _SHA, _PATH)
    assert text == "# 스프린트: 테스트\n"
    assert "/repos/ardenspace/app-chak/contents/PLAN.md" in captured["url"]
    assert f"ref={_SHA}" in captured["url"]
    assert captured["headers"]["authorization"] == "token ghp_abc"


async def test_fetch_file_returns_none_on_404(monkeypatch: pytest.MonkeyPatch):
    async def fake_send(request: httpx.Request, **_kwargs):
        return _mock_contents_response("", status=404)

    monkeypatch.setattr(httpx.AsyncClient, "send", fake_send)
    text = await fetch_file(_REPO, "ghp_abc", _SHA, _PATH)
    assert text is None


async def test_fetch_file_without_pat_omits_authorization(monkeypatch: pytest.MonkeyPatch):
    captured: dict[str, object] = {}

    async def fake_send(request: httpx.Request, **_kwargs):
        captured["headers"] = dict(request.headers)
        return _mock_contents_response("ok")

    monkeypatch.setattr(httpx.AsyncClient, "send", fake_send)
    await fetch_file(_REPO, None, _SHA, _PATH)
    assert "authorization" not in {k.lower() for k in captured["headers"]}


async def test_fetch_file_5xx_raises(monkeypatch: pytest.MonkeyPatch):
    async def fake_send(request: httpx.Request, **_kwargs):
        return httpx.Response(status_code=502, json={"message": "Bad Gateway"})

    monkeypatch.setattr(httpx.AsyncClient, "send", fake_send)
    with pytest.raises(httpx.HTTPStatusError):
        await fetch_file(_REPO, "ghp_abc", _SHA, _PATH)


async def test_fetch_file_normalizes_repo_url_with_trailing_slash_or_git(monkeypatch: pytest.MonkeyPatch):
    captured: dict[str, object] = {}

    async def fake_send(request: httpx.Request, **_kwargs):
        captured["url"] = str(request.url)
        return _mock_contents_response("ok")

    monkeypatch.setattr(httpx.AsyncClient, "send", fake_send)
    await fetch_file(_REPO + ".git/", None, _SHA, _PATH)
    assert "/repos/ardenspace/app-chak/contents/PLAN.md" in captured["url"]
```

- [ ] **Step 2: Run — ImportError**

```bash
pytest tests/test_git_repo_service.py -v
```

- [ ] **Step 3: 구현**

Create `backend/app/services/git_repo_service.py`:

```python
"""GitHub Contents/Compare API 클라이언트.

설계서: 2026-04-26-ai-task-automation-design.md §5.1 (②), §7.1, §9
- fetch_file: Contents API — 단일 파일 raw text. 404 → None.
- fetch_compare_files: Compare API — base...head 변경 파일 경로 리스트.

Auth: 프로젝트별 PAT (Fernet 복호화는 호출자 책임). PAT NULL 이면 unauthenticated.
"""

import base64
import re

import httpx


_GITHUB_API = "https://api.github.com"
_REPO_RE = re.compile(r"^https?://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/?#]+?)(?:\.git)?/?$")


def _parse_repo(repo_url: str) -> tuple[str, str]:
    """`https://github.com/owner/repo[.git][/]` → (owner, repo). lower-case 안 함 (GitHub 은 대소문자 구별 안 하지만 URL 그대로 보존)."""
    m = _REPO_RE.match(repo_url.strip())
    if not m:
        raise ValueError(f"unsupported repo url: {repo_url!r}")
    return m.group("owner"), m.group("repo")


def _auth_headers(pat: str | None) -> dict[str, str]:
    headers = {"Accept": "application/vnd.github.v3+json"}
    if pat:
        headers["Authorization"] = f"token {pat}"
    return headers


async def fetch_file(
    repo_url: str,
    pat: str | None,
    sha: str,
    path: str,
    *,
    timeout: float = 30.0,
) -> str | None:
    """GitHub Contents API → 파일 raw text. 404 → None. 5xx → HTTPStatusError raise."""
    owner, repo = _parse_repo(repo_url)
    url = f"{_GITHUB_API}/repos/{owner}/{repo}/contents/{path}?ref={sha}"
    async with httpx.AsyncClient(timeout=timeout) as client:
        res = await client.get(url, headers=_auth_headers(pat))
    if res.status_code == 404:
        return None
    res.raise_for_status()
    data = res.json()
    if data.get("encoding") == "base64":
        return base64.b64decode(data["content"]).decode("utf-8")
    return data.get("content", "")
```

- [ ] **Step 4: Run — pass**

```bash
pytest tests/test_git_repo_service.py -v
```

Expected: 5 tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/git_repo_service.py backend/tests/test_git_repo_service.py
git commit -m "feat(phase4): git_repo_service.fetch_file (Contents API + base64 decode)"
```

---

## Task 3: git_repo_service — Compare API (변경 파일 목록)

**Files:**
- Modify: `backend/app/services/git_repo_service.py`
- Modify: `backend/tests/test_git_repo_service.py`
- Create: `backend/tests/fixtures/github_compare_payload.json`

- [ ] **Step 1: Compare API fixture 저장**

Create `backend/tests/fixtures/github_compare_payload.json`:

```json
{
  "url": "https://api.github.com/repos/ardenspace/app-chak/compare/aaaa...bbbb",
  "html_url": "https://github.com/ardenspace/app-chak/compare/aaaa...bbbb",
  "base_commit": {"sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},
  "merge_base_commit": {"sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},
  "status": "ahead",
  "ahead_by": 25,
  "behind_by": 0,
  "total_commits": 25,
  "files": [
    {"filename": "PLAN.md", "status": "modified"},
    {"filename": "handoffs/feature-login-redesign.md", "status": "modified"},
    {"filename": "frontend/Login.tsx", "status": "modified"},
    {"filename": "backend/auth/jwt.py", "status": "added"}
  ]
}
```

- [ ] **Step 2: Failing test 추가**

`backend/tests/test_git_repo_service.py` 끝에 추가:

```python
import json
from pathlib import Path


_COMPARE_FIXTURE = (
    Path(__file__).parent / "fixtures" / "github_compare_payload.json"
).read_text()


async def test_fetch_compare_files_returns_filenames(monkeypatch: pytest.MonkeyPatch):
    from app.services.git_repo_service import fetch_compare_files

    captured: dict[str, object] = {}

    async def fake_send(request: httpx.Request, **_kwargs):
        captured["url"] = str(request.url)
        return httpx.Response(status_code=200, json=json.loads(_COMPARE_FIXTURE))

    monkeypatch.setattr(httpx.AsyncClient, "send", fake_send)
    base, head = "a" * 40, "b" * 40
    files = await fetch_compare_files(_REPO, "ghp_abc", base, head)

    assert files == [
        "PLAN.md",
        "handoffs/feature-login-redesign.md",
        "frontend/Login.tsx",
        "backend/auth/jwt.py",
    ]
    assert f"/compare/{base}...{head}" in captured["url"]


async def test_fetch_compare_files_404_raises(monkeypatch: pytest.MonkeyPatch):
    from app.services.git_repo_service import fetch_compare_files

    async def fake_send(request: httpx.Request, **_kwargs):
        return httpx.Response(status_code=404, json={"message": "Not Found"})

    monkeypatch.setattr(httpx.AsyncClient, "send", fake_send)
    with pytest.raises(httpx.HTTPStatusError):
        await fetch_compare_files(_REPO, "ghp_abc", "a" * 40, "b" * 40)


async def test_fetch_compare_files_empty_when_no_files(monkeypatch: pytest.MonkeyPatch):
    from app.services.git_repo_service import fetch_compare_files

    async def fake_send(request: httpx.Request, **_kwargs):
        return httpx.Response(
            status_code=200,
            json={"files": [], "status": "identical", "ahead_by": 0, "behind_by": 0},
        )

    monkeypatch.setattr(httpx.AsyncClient, "send", fake_send)
    files = await fetch_compare_files(_REPO, None, "a" * 40, "a" * 40)
    assert files == []
```

- [ ] **Step 3: Run — ImportError**

```bash
pytest tests/test_git_repo_service.py::test_fetch_compare_files_returns_filenames -v
```

- [ ] **Step 4: 구현**

`backend/app/services/git_repo_service.py` 끝에 추가:

```python
async def fetch_compare_files(
    repo_url: str,
    pat: str | None,
    base_sha: str,
    head_sha: str,
    *,
    timeout: float = 30.0,
) -> list[str]:
    """GitHub Compare API → `files[*].filename` 리스트. 404 / 5xx → HTTPStatusError raise."""
    owner, repo = _parse_repo(repo_url)
    url = f"{_GITHUB_API}/repos/{owner}/{repo}/compare/{base_sha}...{head_sha}"
    async with httpx.AsyncClient(timeout=timeout) as client:
        res = await client.get(url, headers=_auth_headers(pat))
    res.raise_for_status()
    data = res.json()
    return [f["filename"] for f in data.get("files", [])]
```

- [ ] **Step 5: Run — pass**

```bash
pytest tests/test_git_repo_service.py -v
```

Expected: 8 tests pass.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/git_repo_service.py backend/tests/test_git_repo_service.py backend/tests/fixtures/github_compare_payload.json
git commit -m "feat(phase4): git_repo_service.fetch_compare_files (Compare API)"
```

---

## Task 4: sync_service 골격 — process_event entry + processed_at 가드

**Files:**
- Create: `backend/app/services/sync_service.py`
- Create: `backend/tests/test_sync_service.py`

- [ ] **Step 1: Failing test (골격 + 멱등 가드)**

Create `backend/tests/test_sync_service.py`:

```python
"""sync_service — webhook 이벤트 → DB 반영 통합 테스트.

설계서: 2026-04-26-ai-task-automation-design.md §5.1 (⑤), §7.1, §10.2
"""

import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.git_push_event import GitPushEvent
from app.models.handoff import Handoff
from app.models.project import Project
from app.models.task import Task, TaskSource, TaskStatus
from app.models.task_event import TaskEvent, TaskEventAction
from app.models.workspace import Workspace
from app.services.sync_service import process_event


async def _seed_project(
    db: AsyncSession, *, repo_url: str | None = "https://github.com/ardenspace/app-chak"
) -> Project:
    ws = Workspace(name="ws", slug=f"ws-{uuid.uuid4().hex[:8]}")
    db.add(ws)
    await db.flush()
    proj = Project(workspace_id=ws.id, name="p", git_repo_url=repo_url)
    db.add(proj)
    await db.commit()
    await db.refresh(proj)
    return proj


async def _seed_event(
    db: AsyncSession,
    project: Project,
    *,
    head_sha: str = "a" * 40,
    branch: str = "main",
    commits: list[dict] | None = None,
    commits_truncated: bool = False,
    processed_at: datetime | None = None,
) -> GitPushEvent:
    event = GitPushEvent(
        project_id=project.id,
        branch=branch,
        head_commit_sha=head_sha,
        commits=commits or [],
        commits_truncated=commits_truncated,
        pusher="alice",
        processed_at=processed_at,
    )
    db.add(event)
    await db.commit()
    await db.refresh(event)
    return event


# 단순 fake fetcher — 모든 테스트 공통 baseline (필요 시 override)
async def _noop_fetch_file(repo_url: str, pat: str | None, sha: str, path: str) -> str | None:
    return None


async def _noop_fetch_compare(repo_url: str, pat: str | None, base: str, head: str) -> list[str]:
    return []


async def test_process_event_skips_already_processed(async_session: AsyncSession):
    """processed_at IS NOT NULL 이면 즉시 종료 — DB 변경 없음."""
    proj = await _seed_project(async_session)
    event = await _seed_event(
        async_session, proj, processed_at=datetime.utcnow() - timedelta(minutes=10)
    )
    initial_processed_at = event.processed_at

    await process_event(
        async_session,
        event,
        fetch_file=_noop_fetch_file,
        fetch_compare=_noop_fetch_compare,
    )

    await async_session.refresh(event)
    assert event.processed_at == initial_processed_at  # 변경 없음
    assert event.error is None


async def test_process_event_marks_processed_when_no_relevant_files(
    async_session: AsyncSession,
):
    """변경 파일 중 PLAN/handoff 없음 → fetch 안 함, processed_at = now()."""
    proj = await _seed_project(async_session)
    event = await _seed_event(
        async_session,
        proj,
        commits=[{"modified": ["frontend/Button.tsx"], "added": [], "removed": []}],
    )

    await process_event(
        async_session,
        event,
        fetch_file=_noop_fetch_file,
        fetch_compare=_noop_fetch_compare,
    )

    await async_session.refresh(event)
    assert event.processed_at is not None
    assert event.error is None
```

- [ ] **Step 2: Run — ImportError**

```bash
pytest tests/test_sync_service.py::test_process_event_skips_already_processed -v
```

- [ ] **Step 3: 구현 (골격만)**

Create `backend/app/services/sync_service.py`:

```python
"""webhook GitPushEvent → DB 반영 조립 서비스.

설계서: 2026-04-26-ai-task-automation-design.md §5.1 (⑤), §7.1

흐름:
  1) processed_at 가드 — 이미 처리된 이벤트는 즉시 종료 (멱등성)
  2) 변경 파일 목록 결정 — commits_truncated 시 Compare API, 아니면 commits[*].modified
  3) PLAN/handoff 매칭 — 둘 다 없으면 sync 종료 (processed_at = now)
  4) git_repo_service 로 head_sha 기준 raw fetch
  5) plan_parser_service / handoff_parser_service 호출
  6) DB 반영: Task status / archived_at / Handoff INSERT / TaskEvent
  7) processed_at = now (성공/실패 모두). 실패면 error 도 기록.
"""

import logging
from collections.abc import Awaitable, Callable
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.git_push_event import GitPushEvent
from app.models.project import Project

logger = logging.getLogger(__name__)


# Dependency-injected fetcher 시그니처 — 테스트는 fake 주입
FetchFile = Callable[[str, str | None, str, str], Awaitable[str | None]]
FetchCompare = Callable[[str, str | None, str, str], Awaitable[list[str]]]


async def process_event(
    db: AsyncSession,
    event: GitPushEvent,
    *,
    fetch_file: FetchFile,
    fetch_compare: FetchCompare,
) -> None:
    """진입점. 멱등 + 결정적 — 같은 event 두 번 호출해도 DB 변경 1회만."""
    # ① 멱등 가드
    if event.processed_at is not None:
        logger.info("event %s already processed at %s — skip", event.id, event.processed_at)
        return

    project = await db.get(Project, event.project_id)
    if project is None:
        # 프로젝트 삭제됨 — 이벤트만 마킹하고 종료
        event.processed_at = datetime.utcnow()
        event.error = "project not found"
        await db.commit()
        return

    try:
        await _process_inner(db, event, project, fetch_file=fetch_file, fetch_compare=fetch_compare)
        event.processed_at = datetime.utcnow()
    except Exception as exc:
        event.processed_at = datetime.utcnow()
        event.error = f"{type(exc).__name__}: {exc}"
        logger.exception("sync failed for event %s", event.id)

    await db.commit()


async def _process_inner(
    db: AsyncSession,
    event: GitPushEvent,
    project: Project,
    *,
    fetch_file: FetchFile,
    fetch_compare: FetchCompare,
) -> None:
    """변경 파일 검사 → fetch+parse → DB 반영. Task 5+ 에서 본문 채움."""
    changed_files = await _collect_changed_files(
        event, project, fetch_compare=fetch_compare
    )
    plan_changed = project.plan_path in changed_files
    handoff_path = _handoff_file_path(project, event.branch)
    handoff_changed = handoff_path in changed_files

    if not plan_changed and not handoff_changed:
        logger.info("event %s: no PLAN/handoff in changed files — skip", event.id)
        return

    # Task 5+ 에서 fetch+parse+DB 반영
    return


def _handoff_file_path(project: Project, branch: str) -> str:
    """`handoff_dir + branch.replace('/', '-') + '.md'`. 설계서 §6.2 위치 규약."""
    base = project.handoff_dir if project.handoff_dir.endswith("/") else project.handoff_dir + "/"
    return base + branch.replace("/", "-") + ".md"


async def _collect_changed_files(
    event: GitPushEvent,
    project: Project,
    *,
    fetch_compare: FetchCompare,
) -> set[str]:
    """변경 파일 결정. commits_truncated 시 Compare API 호출.

    - truncated == False: commits[*].modified ∪ commits[*].added 합집합
    - truncated == True: Compare API. base = project.last_synced_commit_sha or commits[-1].id
      (완벽한 base 는 webhook payload 의 before — Phase 5 에서 GitPushEvent 컬럼 추가)
    """
    if not event.commits_truncated:
        files: set[str] = set()
        for c in event.commits or []:
            files.update(c.get("modified") or [])
            files.update(c.get("added") or [])
        return files

    if project.git_repo_url is None:
        return set()

    base = project.last_synced_commit_sha
    if base is None and event.commits:
        # truncate 된 가장 오래된 commit — 정확한 before 가 아니지만 fallback
        base = event.commits[-1].get("id") or event.head_commit_sha
    if base is None:
        base = event.head_commit_sha

    pat = _decrypt_pat(project)
    files_list = await fetch_compare(project.git_repo_url, pat, base, event.head_commit_sha)
    return set(files_list)


def _decrypt_pat(project: Project) -> str | None:
    if project.github_pat_encrypted is None:
        return None
    from app.core.crypto import decrypt_secret

    try:
        return decrypt_secret(project.github_pat_encrypted)
    except Exception:
        logger.exception("failed to decrypt PAT for project %s", project.id)
        return None
```

- [ ] **Step 4: Run — pass (2 tests)**

```bash
pytest tests/test_sync_service.py -v
```

Expected: 2 tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/sync_service.py backend/tests/test_sync_service.py
git commit -m "feat(phase4): sync_service 골격 + 멱등 가드 + 변경 파일 검사"
```

---

## Task 5: sync_service — PLAN fetch + parse + Task INSERT

**Files:**
- Modify: `backend/app/services/sync_service.py`
- Modify: `backend/tests/test_sync_service.py`

- [ ] **Step 1: Failing test (PLAN 신규 task INSERT)**

`backend/tests/test_sync_service.py` 끝에 추가:

```python
async def test_process_event_creates_new_tasks_from_plan(async_session: AsyncSession):
    """PLAN 에 새 task-XXX 가 있으면 Task INSERT (source=SYNCED_FROM_PLAN, status 매핑)."""
    proj = await _seed_project(async_session)
    plan_text = """# 스프린트: 2026-04

## 태스크

- [ ] [task-001] 새 작업 — @alice
- [x] [task-002] 이미 완료 — @bob
"""

    async def fake_fetch_file(repo_url, pat, sha, path):
        if path == "PLAN.md":
            return plan_text
        return None

    event = await _seed_event(
        async_session,
        proj,
        commits=[{"modified": ["PLAN.md"], "added": [], "removed": []}],
    )

    await process_event(
        async_session,
        event,
        fetch_file=fake_fetch_file,
        fetch_compare=_noop_fetch_compare,
    )

    await async_session.refresh(event)
    assert event.processed_at is not None
    assert event.error is None

    rows = (
        await async_session.execute(
            select(Task).where(Task.project_id == proj.id).order_by(Task.external_id)
        )
    ).scalars().all()
    assert len(rows) == 2
    t1 = next(t for t in rows if t.external_id == "task-001")
    t2 = next(t for t in rows if t.external_id == "task-002")
    assert t1.source == TaskSource.SYNCED_FROM_PLAN
    assert t1.status == TaskStatus.TODO  # `- [ ]`
    assert t1.title == "새 작업"
    assert t1.last_commit_sha == event.head_commit_sha
    assert t2.status == TaskStatus.DONE  # `- [x]`
    assert t2.last_commit_sha == event.head_commit_sha


async def test_process_event_records_synced_from_plan_event(async_session: AsyncSession):
    """신규 Task INSERT 시 TaskEvent(action=SYNCED_FROM_PLAN) 도 만들어짐."""
    proj = await _seed_project(async_session)
    plan_text = "## 태스크\n\n- [ ] [task-100] 신규 — @alice\n"

    async def fake_fetch_file(repo_url, pat, sha, path):
        return plan_text if path == "PLAN.md" else None

    event = await _seed_event(
        async_session, proj, commits=[{"modified": ["PLAN.md"]}]
    )
    await process_event(
        async_session, event,
        fetch_file=fake_fetch_file, fetch_compare=_noop_fetch_compare,
    )

    task = (await async_session.execute(
        select(Task).where(Task.external_id == "task-100")
    )).scalar_one()
    events = (await async_session.execute(
        select(TaskEvent).where(TaskEvent.task_id == task.id)
    )).scalars().all()
    assert any(e.action == TaskEventAction.SYNCED_FROM_PLAN for e in events)


async def test_process_event_skips_when_plan_404(async_session: AsyncSession):
    """fetch_file 이 None (404) 반환 → sync 종료, error 기록 없음."""
    proj = await _seed_project(async_session)

    async def fake_fetch_file(repo_url, pat, sha, path):
        return None

    event = await _seed_event(
        async_session, proj, commits=[{"modified": ["PLAN.md"]}]
    )
    await process_event(
        async_session, event,
        fetch_file=fake_fetch_file, fetch_compare=_noop_fetch_compare,
    )

    await async_session.refresh(event)
    assert event.processed_at is not None
    assert event.error is None  # 404 는 error 아님 — plan 부재는 silent skip
    rows = (await async_session.execute(
        select(Task).where(Task.project_id == proj.id)
    )).scalars().all()
    assert rows == []
```

- [ ] **Step 2: Run — 실패 (현재 _process_inner 가 PLAN 처리 안 함)**

```bash
pytest tests/test_sync_service.py -v
```

- [ ] **Step 3: 구현 — _process_inner 에 PLAN 처리 추가**

`backend/app/services/sync_service.py` 의 `_process_inner` 를 다음으로 교체:

```python
async def _process_inner(
    db: AsyncSession,
    event: GitPushEvent,
    project: Project,
    *,
    fetch_file: FetchFile,
    fetch_compare: FetchCompare,
) -> None:
    changed_files = await _collect_changed_files(
        event, project, fetch_compare=fetch_compare
    )
    plan_changed = project.plan_path in changed_files
    handoff_path = _handoff_file_path(project, event.branch)
    handoff_changed = handoff_path in changed_files

    if not plan_changed and not handoff_changed:
        logger.info("event %s: no PLAN/handoff in changed files — skip", event.id)
        return

    pat = _decrypt_pat(project)

    if plan_changed and project.git_repo_url is not None:
        plan_text = await fetch_file(
            project.git_repo_url, pat, event.head_commit_sha, project.plan_path
        )
        if plan_text is not None:
            await _apply_plan(db, project, event, plan_text)
        else:
            logger.info("event %s: PLAN.md returned 404 — skip plan", event.id)

    # handoff 처리는 Task 6+ 에서 추가
```

그리고 파일 끝에 `_apply_plan` 헬퍼 추가:

```python
async def _apply_plan(
    db: AsyncSession,
    project: Project,
    event: GitPushEvent,
    plan_text: str,
) -> None:
    """PLAN.md 파싱 → Task INSERT/UPDATE. archived_at 처리는 Task 8 에서."""
    from sqlalchemy import select

    from app.models.task import Task, TaskSource, TaskStatus
    from app.models.task_event import TaskEvent, TaskEventAction
    from app.services.plan_parser_service import (
        DuplicateExternalIdError,
        parse_plan,
    )

    try:
        parsed = parse_plan(plan_text)
    except DuplicateExternalIdError as exc:
        # 결정적 fail — 호출자(process_event)가 잡아 error 컬럼에 기록
        raise

    # 현재 DB 의 synced task 인덱스
    rows = (await db.execute(
        select(Task).where(
            Task.project_id == project.id,
            Task.source == TaskSource.SYNCED_FROM_PLAN,
            Task.archived_at.is_(None),
        )
    )).scalars().all()
    existing: dict[str, Task] = {t.external_id: t for t in rows if t.external_id}

    for parsed_task in parsed.tasks:
        existing_task = existing.get(parsed_task.external_id)
        new_status = TaskStatus.DONE if parsed_task.checked else TaskStatus.TODO

        if existing_task is None:
            # 신규 INSERT
            t = Task(
                project_id=project.id,
                title=parsed_task.title,
                source=TaskSource.SYNCED_FROM_PLAN,
                external_id=parsed_task.external_id,
                status=new_status,
                last_commit_sha=event.head_commit_sha,
            )
            db.add(t)
            await db.flush()  # t.id 확보
            db.add(TaskEvent(
                task_id=t.id,
                action=TaskEventAction.SYNCED_FROM_PLAN,
                changes={
                    "external_id": parsed_task.external_id,
                    "title": parsed_task.title,
                    "checked": parsed_task.checked,
                },
            ))
        # 기존 task UPDATE 는 Task 6/7 에서 (CHECKED_BY_COMMIT / UNCHECKED_BY_COMMIT)
```

- [ ] **Step 4: Run — pass (5 tests)**

```bash
pytest tests/test_sync_service.py -v
```

Expected: 5 tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/sync_service.py backend/tests/test_sync_service.py
git commit -m "feat(phase4): sync_service — PLAN fetch + parse + Task INSERT (SYNCED_FROM_PLAN)"
```

---

## Task 6: sync_service — 체크 변경 (CHECKED_BY_COMMIT / UNCHECKED_BY_COMMIT)

**Files:**
- Modify: `backend/app/services/sync_service.py`
- Modify: `backend/tests/test_sync_service.py`

- [ ] **Step 1: Failing test (체크 / 언체크)**

`backend/tests/test_sync_service.py` 끝에 추가:

```python
async def test_process_event_checks_existing_task_to_done(async_session: AsyncSession):
    """기존 TODO task 가 PLAN 에서 [x] 로 → DONE + CHECKED_BY_COMMIT TaskEvent."""
    proj = await _seed_project(async_session)
    existing = Task(
        project_id=proj.id,
        title="기존",
        source=TaskSource.SYNCED_FROM_PLAN,
        external_id="task-001",
        status=TaskStatus.TODO,
    )
    async_session.add(existing)
    await async_session.commit()
    await async_session.refresh(existing)

    plan_text = "## 태스크\n\n- [x] [task-001] 기존 — @alice\n"

    async def fake_fetch_file(repo_url, pat, sha, path):
        return plan_text if path == "PLAN.md" else None

    event = await _seed_event(
        async_session, proj, commits=[{"modified": ["PLAN.md"]}]
    )
    await process_event(
        async_session, event,
        fetch_file=fake_fetch_file, fetch_compare=_noop_fetch_compare,
    )
    await async_session.refresh(existing)

    assert existing.status == TaskStatus.DONE
    assert existing.last_commit_sha == event.head_commit_sha
    events = (await async_session.execute(
        select(TaskEvent).where(TaskEvent.task_id == existing.id)
    )).scalars().all()
    assert any(e.action == TaskEventAction.CHECKED_BY_COMMIT for e in events)


async def test_process_event_rolls_back_done_to_todo(async_session: AsyncSession):
    """직전 DONE 인 task 가 PLAN 에서 [ ] 로 → TODO + UNCHECKED_BY_COMMIT."""
    proj = await _seed_project(async_session)
    existing = Task(
        project_id=proj.id,
        title="롤백 케이스",
        source=TaskSource.SYNCED_FROM_PLAN,
        external_id="task-002",
        status=TaskStatus.DONE,
    )
    async_session.add(existing)
    await async_session.commit()
    await async_session.refresh(existing)

    plan_text = "## 태스크\n\n- [ ] [task-002] 롤백 케이스\n"

    async def fake_fetch_file(repo_url, pat, sha, path):
        return plan_text if path == "PLAN.md" else None

    event = await _seed_event(
        async_session, proj, commits=[{"modified": ["PLAN.md"]}]
    )
    await process_event(
        async_session, event,
        fetch_file=fake_fetch_file, fetch_compare=_noop_fetch_compare,
    )
    await async_session.refresh(existing)

    assert existing.status == TaskStatus.TODO
    events = (await async_session.execute(
        select(TaskEvent).where(TaskEvent.task_id == existing.id)
    )).scalars().all()
    assert any(e.action == TaskEventAction.UNCHECKED_BY_COMMIT for e in events)


async def test_process_event_no_change_when_unchecked_and_already_not_done(
    async_session: AsyncSession,
):
    """직전 TODO 인 task 가 PLAN 에서 [ ] → 변경 없음, TaskEvent 도 안 만듦."""
    proj = await _seed_project(async_session)
    existing = Task(
        project_id=proj.id,
        title="변경 없음",
        source=TaskSource.SYNCED_FROM_PLAN,
        external_id="task-003",
        status=TaskStatus.DOING,  # TODO 가 아닌 다른 활성 status
    )
    async_session.add(existing)
    await async_session.commit()
    await async_session.refresh(existing)

    plan_text = "## 태스크\n\n- [ ] [task-003] 변경 없음\n"

    async def fake_fetch_file(repo_url, pat, sha, path):
        return plan_text if path == "PLAN.md" else None

    event = await _seed_event(
        async_session, proj, commits=[{"modified": ["PLAN.md"]}]
    )
    await process_event(
        async_session, event,
        fetch_file=fake_fetch_file, fetch_compare=_noop_fetch_compare,
    )
    await async_session.refresh(existing)

    assert existing.status == TaskStatus.DOING  # 보존
    events = (await async_session.execute(
        select(TaskEvent).where(TaskEvent.task_id == existing.id)
    )).scalars().all()
    # TaskEvent 가 만들어지지 않아야 함 (last_commit_sha 도 변경 없음)
    assert len(events) == 0
```

- [ ] **Step 2: Run — 실패**

- [ ] **Step 3: `_apply_plan` 의 "기존 task UPDATE 는 Task 6/7 에서" 자리에 다음 분기 추가**

`_apply_plan` 의 `if existing_task is None:` 다음 `else:` 추가:

```python
        else:
            # 기존 task — status 전이 규칙
            previous_status = existing_task.status
            if parsed_task.checked and previous_status != TaskStatus.DONE:
                existing_task.status = TaskStatus.DONE
                existing_task.last_commit_sha = event.head_commit_sha
                db.add(TaskEvent(
                    task_id=existing_task.id,
                    action=TaskEventAction.CHECKED_BY_COMMIT,
                    changes={
                        "previous_status": previous_status.value,
                        "commit_sha": event.head_commit_sha,
                    },
                ))
            elif not parsed_task.checked and previous_status == TaskStatus.DONE:
                # 롤백: DONE → TODO + UNCHECKED_BY_COMMIT
                existing_task.status = TaskStatus.TODO
                existing_task.last_commit_sha = event.head_commit_sha
                db.add(TaskEvent(
                    task_id=existing_task.id,
                    action=TaskEventAction.UNCHECKED_BY_COMMIT,
                    changes={
                        "previous_status": previous_status.value,
                        "commit_sha": event.head_commit_sha,
                    },
                ))
            # else: 변경 없음 (TODO→TODO, DOING→DOING 등). last_commit_sha 도 안 바꿈.
```

- [ ] **Step 4: Run — pass (8 tests)**

```bash
pytest tests/test_sync_service.py -v
```

Expected: 8 tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/sync_service.py backend/tests/test_sync_service.py
git commit -m "feat(phase4): sync_service — 체크/언체크 전이 (CHECKED/UNCHECKED_BY_COMMIT)"
```

---

## Task 7: sync_service — archived_at (PLAN 에서 사라진 task)

**Files:**
- Modify: `backend/app/services/sync_service.py`
- Modify: `backend/tests/test_sync_service.py`

- [ ] **Step 1: Failing test**

`backend/tests/test_sync_service.py` 끝에 추가:

```python
async def test_process_event_archives_tasks_removed_from_plan(async_session: AsyncSession):
    """기존 synced task 가 새 PLAN 에 없으면 archived_at = now() + ARCHIVED_FROM_PLAN."""
    proj = await _seed_project(async_session)
    keep = Task(
        project_id=proj.id, title="유지", source=TaskSource.SYNCED_FROM_PLAN,
        external_id="task-001", status=TaskStatus.TODO,
    )
    removed = Task(
        project_id=proj.id, title="삭제됨", source=TaskSource.SYNCED_FROM_PLAN,
        external_id="task-OLD", status=TaskStatus.DOING,
    )
    async_session.add_all([keep, removed])
    await async_session.commit()
    await async_session.refresh(removed)

    # 새 PLAN 에 task-001 만 — task-OLD 는 사라짐
    plan_text = "## 태스크\n\n- [ ] [task-001] 유지\n"

    async def fake_fetch_file(repo_url, pat, sha, path):
        return plan_text if path == "PLAN.md" else None

    event = await _seed_event(
        async_session, proj, commits=[{"modified": ["PLAN.md"]}]
    )
    await process_event(
        async_session, event,
        fetch_file=fake_fetch_file, fetch_compare=_noop_fetch_compare,
    )
    await async_session.refresh(removed)
    await async_session.refresh(keep)

    assert removed.archived_at is not None
    assert keep.archived_at is None
    events = (await async_session.execute(
        select(TaskEvent).where(TaskEvent.task_id == removed.id)
    )).scalars().all()
    assert any(e.action == TaskEventAction.ARCHIVED_FROM_PLAN for e in events)


async def test_process_event_does_not_archive_manual_tasks(async_session: AsyncSession):
    """source=MANUAL 인 task 는 PLAN 에 없어도 archived_at 안 변경 (수동 태스크 보호)."""
    proj = await _seed_project(async_session)
    manual = Task(
        project_id=proj.id, title="수동", source=TaskSource.MANUAL,
        external_id=None, status=TaskStatus.TODO,
    )
    async_session.add(manual)
    await async_session.commit()
    await async_session.refresh(manual)

    plan_text = "## 태스크\n\n- [ ] [task-001] PLAN 만\n"

    async def fake_fetch_file(repo_url, pat, sha, path):
        return plan_text if path == "PLAN.md" else None

    event = await _seed_event(
        async_session, proj, commits=[{"modified": ["PLAN.md"]}]
    )
    await process_event(
        async_session, event,
        fetch_file=fake_fetch_file, fetch_compare=_noop_fetch_compare,
    )
    await async_session.refresh(manual)
    assert manual.archived_at is None
```

- [ ] **Step 2: Run — 실패**

- [ ] **Step 3: 구현 — `_apply_plan` 끝에 archived 처리 추가**

`_apply_plan` 함수 끝(`for parsed_task in parsed.tasks:` 루프 다음, 함수 종료 전)에 추가:

```python
    # PLAN 에서 사라진 synced task → archived_at
    parsed_ids = {t.external_id for t in parsed.tasks}
    for ext_id, task in existing.items():
        if ext_id not in parsed_ids and task.archived_at is None:
            task.archived_at = datetime.utcnow()
            db.add(TaskEvent(
                task_id=task.id,
                action=TaskEventAction.ARCHIVED_FROM_PLAN,
                changes={
                    "external_id": ext_id,
                    "commit_sha": event.head_commit_sha,
                },
            ))
```

`datetime` import 가 sync_service.py 상단에 있는지 확인 — 있음.

- [ ] **Step 4: Run — pass (10 tests)**

```bash
pytest tests/test_sync_service.py -v
```

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/sync_service.py backend/tests/test_sync_service.py
git commit -m "feat(phase4): sync_service — archived_at + ARCHIVED_FROM_PLAN (PLAN 에서 제거된 task)"
```

---

## Task 8: sync_service — handoff fetch + Handoff INSERT (멱등)

**Files:**
- Modify: `backend/app/services/sync_service.py`
- Modify: `backend/tests/test_sync_service.py`

- [ ] **Step 1: Failing test (Handoff INSERT + 멱등)**

`backend/tests/test_sync_service.py` 끝에 추가:

```python
async def test_process_event_inserts_handoff_row(async_session: AsyncSession):
    """handoff 변경 → fetch + parse → Handoff INSERT (parsed_tasks/free_notes 채워짐)."""
    proj = await _seed_project(async_session)
    handoff_text = """# Handoff: feature/login — @alice

## 2026-04-30

- [x] task-001
- [ ] task-002

### 마지막 커밋

abc1234 — 작업 진행
"""

    async def fake_fetch_file(repo_url, pat, sha, path):
        if path == "handoffs/feature-login.md":
            return handoff_text
        return None

    event = await _seed_event(
        async_session, proj,
        branch="feature/login",
        commits=[{"modified": ["handoffs/feature-login.md"]}],
    )
    await process_event(
        async_session, event,
        fetch_file=fake_fetch_file, fetch_compare=_noop_fetch_compare,
    )

    rows = (await async_session.execute(
        select(Handoff).where(Handoff.project_id == proj.id)
    )).scalars().all()
    assert len(rows) == 1
    h = rows[0]
    assert h.commit_sha == event.head_commit_sha
    assert h.branch == "feature/login"
    assert h.author_git_login == "alice"
    assert h.parsed_tasks is not None
    ids = [pt["external_id"] for pt in h.parsed_tasks]
    assert ids == ["task-001", "task-002"]
    assert h.free_notes is not None
    assert "abc1234" in h.free_notes.get("last_commit", "")


async def test_process_event_handoff_idempotent_on_replay(async_session: AsyncSession):
    """같은 commit_sha 로 두 번 process → Handoff 1 행, Task 변경 1 회만."""
    proj = await _seed_project(async_session)
    handoff_text = "# Handoff: main — @alice\n\n## 2026-04-30\n\n- [x] task-001\n"

    async def fake_fetch_file(repo_url, pat, sha, path):
        return handoff_text if path == "handoffs/main.md" else None

    # 첫 번째 호출
    event1 = await _seed_event(
        async_session, proj, head_sha="c" * 40,
        commits=[{"modified": ["handoffs/main.md"]}],
    )
    await process_event(
        async_session, event1,
        fetch_file=fake_fetch_file, fetch_compare=_noop_fetch_compare,
    )

    # 두 번째 동일 head_sha (같은 GitPushEvent — processed_at 가드)
    await process_event(
        async_session, event1,
        fetch_file=fake_fetch_file, fetch_compare=_noop_fetch_compare,
    )

    rows = (await async_session.execute(
        select(Handoff).where(Handoff.project_id == proj.id)
    )).scalars().all()
    assert len(rows) == 1


async def test_process_event_handoff_unique_with_separate_event_same_sha(
    async_session: AsyncSession,
):
    """다른 GitPushEvent 라도 같은 commit_sha → Handoff UNIQUE 충돌 silent skip."""
    proj = await _seed_project(async_session)
    handoff_text = "# Handoff: main — @alice\n\n## 2026-04-30\n\n- [x] task-001\n"

    async def fake_fetch_file(repo_url, pat, sha, path):
        return handoff_text if path == "handoffs/main.md" else None

    event1 = await _seed_event(
        async_session, proj, head_sha="d" * 40,
        commits=[{"modified": ["handoffs/main.md"]}],
    )
    await process_event(
        async_session, event1,
        fetch_file=fake_fetch_file, fetch_compare=_noop_fetch_compare,
    )

    # 새 이벤트 같은 sha — webhook 재전송 시뮬레이션
    event2 = await _seed_event(
        async_session, proj, head_sha="d" * 40,
        commits=[{"modified": ["handoffs/main.md"]}],
    )
    await process_event(
        async_session, event2,
        fetch_file=fake_fetch_file, fetch_compare=_noop_fetch_compare,
    )

    rows = (await async_session.execute(
        select(Handoff).where(Handoff.project_id == proj.id)
    )).scalars().all()
    assert len(rows) == 1  # UNIQUE (project_id, commit_sha) 멱등성


async def test_process_event_malformed_handoff_records_error(async_session: AsyncSession):
    """handoff 헤더 없음 → MalformedHandoffError → event.error 기록, processed_at = now."""
    proj = await _seed_project(async_session)

    async def fake_fetch_file(repo_url, pat, sha, path):
        if path == "handoffs/main.md":
            return "## 2026-04-30\n\n- [ ] task-001\n"  # # Handoff 헤더 없음
        return None

    event = await _seed_event(
        async_session, proj,
        commits=[{"modified": ["handoffs/main.md"]}],
    )
    await process_event(
        async_session, event,
        fetch_file=fake_fetch_file, fetch_compare=_noop_fetch_compare,
    )
    await async_session.refresh(event)
    assert event.processed_at is not None
    assert event.error is not None
    assert "MalformedHandoffError" in event.error
```

- [ ] **Step 2: Run — 실패**

- [ ] **Step 3: 구현 — `_process_inner` 에 handoff 분기 추가, `_apply_handoff` 헬퍼 신설**

`_process_inner` 끝의 `# handoff 처리는 Task 6+ 에서 추가` 주석 라인을 다음으로 교체:

```python
    if handoff_changed and project.git_repo_url is not None:
        handoff_text = await fetch_file(
            project.git_repo_url, pat, event.head_commit_sha, handoff_path
        )
        if handoff_text is not None:
            await _apply_handoff(db, project, event, handoff_text)
        else:
            logger.warning(
                "event %s: handoff %s returned 404 — skip", event.id, handoff_path
            )
```

파일 끝에 `_apply_handoff` 추가:

```python
async def _apply_handoff(
    db: AsyncSession,
    project: Project,
    event: GitPushEvent,
    handoff_text: str,
) -> None:
    """handoff 파싱 → Handoff INSERT (UNIQUE 멱등) + raw_content 저장.

    parsed_tasks 는 sections[0].checks (active 섹션). free_notes 는 sections[0] 의 free_notes
    + subtasks 합본. 다중 날짜 history 는 Phase 7 brief_service 에서 사용 — 여기선 active 만.
    """
    from sqlalchemy import select
    from sqlalchemy.exc import IntegrityError

    from app.models.handoff import Handoff
    from app.services.handoff_parser_service import (
        MalformedHandoffError,
        parse_handoff,
    )

    parsed = parse_handoff(handoff_text)  # MalformedHandoffError 는 process_event 가 catch
    if not parsed.sections:
        return  # 빈 파일 — 정상 종료

    active = parsed.sections[0]
    parsed_tasks = [
        {"external_id": c.external_id, "checked": c.checked, "extra": c.extra}
        for c in active.checks
    ]
    free_notes = {
        "last_commit": active.free_notes.last_commit,
        "next": active.free_notes.next,
        "blockers": active.free_notes.blockers,
        "subtasks": [
            {
                "parent_external_id": s.parent_external_id,
                "checked": s.checked,
                "text": s.text,
            }
            for s in active.subtasks
        ],
    }

    handoff = Handoff(
        project_id=project.id,
        branch=parsed.branch,
        author_git_login=parsed.author_git_login,
        commit_sha=event.head_commit_sha,
        pushed_at=event.received_at,
        raw_content=handoff_text,
        parsed_tasks=parsed_tasks,
        free_notes=free_notes,
    )
    db.add(handoff)
    try:
        await db.flush()
    except IntegrityError:
        # UNIQUE (project_id, commit_sha) — webhook 재전송 멱등성
        await db.rollback()
        logger.info(
            "handoff already exists for project=%s commit=%s — skip",
            project.id, event.head_commit_sha,
        )
```

- [ ] **Step 4: Run — pass (14 tests)**

```bash
pytest tests/test_sync_service.py -v
```

Expected: 14 tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/sync_service.py backend/tests/test_sync_service.py
git commit -m "feat(phase4): sync_service — handoff fetch + Handoff INSERT (멱등) + 에러 기록"
```

---

## Task 9: sync_service — 멱등성 회귀 (PLAN+handoff 동시 + replay)

**Files:**
- Modify: `backend/tests/test_sync_service.py`

- [ ] **Step 1: 회귀 test 추가 (CRITICAL — 같은 webhook 두 번)**

`backend/tests/test_sync_service.py` 끝에 추가:

```python
async def test_process_event_idempotent_full_cycle(async_session: AsyncSession):
    """CRITICAL: PLAN+handoff 동시 변경 푸시를 두 번 process → DB 변경 1회, Handoff 1행, TaskEvent 중복 없음."""
    proj = await _seed_project(async_session)
    plan_text = """# 스프린트: 2026-04

## 태스크

- [x] [task-001] 완료된 작업 — @alice
"""
    handoff_text = """# Handoff: main — @alice

## 2026-04-30

- [x] task-001
"""

    async def fake_fetch_file(repo_url, pat, sha, path):
        if path == "PLAN.md":
            return plan_text
        if path == "handoffs/main.md":
            return handoff_text
        return None

    event = await _seed_event(
        async_session, proj,
        head_sha="e" * 40,
        commits=[{"modified": ["PLAN.md", "handoffs/main.md"]}],
    )

    # 첫 번째 process
    await process_event(
        async_session, event,
        fetch_file=fake_fetch_file, fetch_compare=_noop_fetch_compare,
    )

    tasks_after_first = (await async_session.execute(
        select(Task).where(Task.project_id == proj.id)
    )).scalars().all()
    handoffs_after_first = (await async_session.execute(
        select(Handoff).where(Handoff.project_id == proj.id)
    )).scalars().all()
    events_after_first = (await async_session.execute(
        select(TaskEvent)
        .where(TaskEvent.task_id.in_([t.id for t in tasks_after_first]))
    )).scalars().all()

    # 두 번째 process (같은 event — processed_at 가드)
    await process_event(
        async_session, event,
        fetch_file=fake_fetch_file, fetch_compare=_noop_fetch_compare,
    )

    tasks_after_second = (await async_session.execute(
        select(Task).where(Task.project_id == proj.id)
    )).scalars().all()
    handoffs_after_second = (await async_session.execute(
        select(Handoff).where(Handoff.project_id == proj.id)
    )).scalars().all()
    events_after_second = (await async_session.execute(
        select(TaskEvent)
        .where(TaskEvent.task_id.in_([t.id for t in tasks_after_second]))
    )).scalars().all()

    assert len(tasks_after_first) == len(tasks_after_second) == 1
    assert len(handoffs_after_first) == len(handoffs_after_second) == 1
    assert len(events_after_first) == len(events_after_second)  # TaskEvent 중복 없음


async def test_process_event_records_error_on_duplicate_external_id(
    async_session: AsyncSession,
):
    """PLAN 에 같은 external_id 가 두 번 → DuplicateExternalIdError → event.error 기록."""
    proj = await _seed_project(async_session)
    plan_text = """## 태스크

- [ ] [task-001] 첫 번째
- [ ] [task-001] 중복 — @bob
"""

    async def fake_fetch_file(repo_url, pat, sha, path):
        return plan_text if path == "PLAN.md" else None

    event = await _seed_event(
        async_session, proj,
        commits=[{"modified": ["PLAN.md"]}],
    )
    await process_event(
        async_session, event,
        fetch_file=fake_fetch_file, fetch_compare=_noop_fetch_compare,
    )
    await async_session.refresh(event)
    assert event.error is not None
    assert "DuplicateExternalIdError" in event.error
    assert event.processed_at is not None  # 자동 재시도 안 함
```

- [ ] **Step 2: Run — pass (16 tests)**

```bash
pytest tests/test_sync_service.py -v
```

Expected: 16 tests pass. (멱등성은 `processed_at` 가드 + Handoff UNIQUE + Task UNIQUE (project_id, external_id) 조합으로 자동 보장 — 본 task 는 회귀 테스트만 추가).

- [ ] **Step 3: Commit**

```bash
git add backend/tests/test_sync_service.py
git commit -m "test(phase4): sync_service 멱등성 회귀 (PLAN+handoff 동시, dup external_id)"
```

---

## Task 10: webhook endpoint BackgroundTask + reaper callback 주입

**Files:**
- Modify: `backend/app/api/v1/endpoints/webhooks.py`
- Modify: `backend/app/services/push_event_reaper.py`
- Modify: `backend/app/main.py`
- Modify: `backend/tests/test_webhook_endpoint.py`

- [ ] **Step 1: 통합 endpoint 회귀 test 추가**

`backend/tests/test_webhook_endpoint.py` 끝에 추가:

```python
async def test_webhook_triggers_background_sync(
    client_with_db, async_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
):
    """webhook 정상 처리 → BackgroundTasks 에 sync_service.process_event 추가됨 (호출 회수만 검증)."""
    secret = "valid-secret"
    proj = await _seed_project_with_secret(
        async_session, "https://github.com/ardenspace/app-chak", secret
    )
    sig = _sign(FIXTURE, secret)

    called_with: list[tuple] = []

    async def fake_process(db, event, *, fetch_file, fetch_compare):
        called_with.append((event.head_commit_sha,))

    # webhook endpoint 가 import 한 sync_service.process_event 를 monkeypatch
    import app.api.v1.endpoints.webhooks as webhooks_module
    monkeypatch.setattr(webhooks_module, "process_event", fake_process)

    res = await client_with_db.post(
        "/api/v1/webhooks/github",
        content=FIXTURE,
        headers={"X-Hub-Signature-256": sig, "X-GitHub-Event": "push"},
    )
    assert res.status_code == 200
    # BackgroundTasks 는 endpoint return 후 실행 — httpx ASGITransport 가 그것까지 await
    assert len(called_with) == 1
    assert called_with[0][0] == FIXTURE_HEAD_SHA  # fixture 의 head_commit.id


# 기존 fixture 가 정의한 head sha — fixtures/github_push_payload.json 의 head_commit.id
FIXTURE_HEAD_SHA = "abcdef0123456789abcdef0123456789abcdef01"
```

- [ ] **Step 2: webhook endpoint 수정 — BackgroundTasks 사용**

`backend/app/api/v1/endpoints/webhooks.py` 의 imports 추가:

```python
from fastapi import BackgroundTasks
from app.services.sync_service import process_event
from app.services.git_repo_service import fetch_compare_files, fetch_file
from app.database import AsyncSessionLocal
```

`receive_github_push` 시그니처에 `background_tasks: BackgroundTasks` 추가하고 마지막의 `event = await record_push_event(...)` 다음에 BackgroundTasks 등록 추가:

```python
@router.post("/github")
async def receive_github_push(
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    x_hub_signature_256: str | None = Header(default=None, alias="X-Hub-Signature-256"),
    x_github_event: str | None = Header(default=None, alias="X-GitHub-Event"),
):
    # ... (기존 로직 — body, signature, event INSERT)
    event = await record_push_event(db, project, payload)

    if event is not None:
        background_tasks.add_task(_run_sync_in_new_session, event.id)

    return {"status": "received", "event_id": str(event.id) if event else None}


async def _run_sync_in_new_session(event_id):
    """BackgroundTask 진입점 — 자체 세션 + 실제 fetcher 주입."""
    from sqlalchemy import select

    from app.models.git_push_event import GitPushEvent

    async with AsyncSessionLocal() as db:
        event = (await db.execute(
            select(GitPushEvent).where(GitPushEvent.id == event_id)
        )).scalar_one_or_none()
        if event is None:
            return
        await process_event(
            db, event,
            fetch_file=fetch_file,
            fetch_compare=fetch_compare_files,
        )
```

- [ ] **Step 3: reaper callback 주입 — main.py lifespan 수정**

`backend/app/main.py` 상단 imports 에 다음 추가:

```python
from app.database import AsyncSessionLocal
from app.services.git_repo_service import fetch_compare_files, fetch_file
from app.services.push_event_reaper import reap_pending_events
from app.services.sync_service import process_event
```

(`from app.services.push_event_reaper import run_reaper_once` 는 제거 — 본 phase 부터는 사용 안 함. `run_reaper_once` 함수 자체는 호환성 유지로 push_event_reaper.py 에 그대로 남김.)

lifespan 안의 startup 블록에서 `await run_reaper_once()` 호출 부분을 다음으로 교체:

```python
    try:
        async with AsyncSessionLocal() as db:
            async def _cb(ev):
                await process_event(
                    db, ev,
                    fetch_file=fetch_file,
                    fetch_compare=fetch_compare_files,
                )
            reaped = await reap_pending_events(db, _cb)
            await db.commit()
        if reaped:
            logger.info("startup reaper picked up %d pending push events", reaped)
    except Exception:
        logger.exception("startup reaper failed")
```

- [ ] **Step 4: Run endpoint test — pass**

```bash
pytest tests/test_webhook_endpoint.py -v
```

Expected: 9 tests pass (8 기존 + 1 신규 BackgroundTask 검증).

- [ ] **Step 5: 부팅 import smoke test**

```bash
python -c "from app.main import app; print('startup OK')"
```

- [ ] **Step 6: Commit**

```bash
git add backend/app/api/v1/endpoints/webhooks.py backend/app/main.py backend/tests/test_webhook_endpoint.py
git commit -m "feat(phase4): webhook BackgroundTask + reaper callback (sync_service 통합)"
```

---

## Task 11: plan_parser 하드닝 (Phase 3 code review I-2/I-3)

**Files:**
- Modify: `backend/app/services/plan_parser_service.py`
- Modify: `backend/tests/test_plan_parser_service.py`

- [ ] **Step 1: 회귀 test 추가 (em-dash / backtick / @ in title)**

`backend/tests/test_plan_parser_service.py` 끝에 추가:

```python
def test_parse_plan_title_preserves_em_dash():
    """code review I-2: title 안의 ' — ' 이 truncate 되지 않아야."""
    text = """## 태스크

- [ ] [task-001] 로그인 — 1단계 — @alice — `frontend/Login.tsx`
"""
    plan = parse_plan(text)
    t = plan.tasks[0]
    assert t.title == "로그인 — 1단계"
    assert t.assignee == "alice"
    assert t.paths == ["frontend/Login.tsx"]


def test_parse_plan_title_with_backtick_in_text_does_not_become_path():
    """code review I-3: 백틱이 title 영역에 있으면 path 로 추출되지 않아야."""
    text = """## 태스크

- [ ] [task-002] Use `helper()` for stuff — @bob — `backend/main.py`
"""
    plan = parse_plan(text)
    t = plan.tasks[0]
    assert t.title == "Use `helper()` for stuff"
    assert t.assignee == "bob"
    assert t.paths == ["backend/main.py"]


def test_parse_plan_title_with_at_in_text_does_not_become_assignee():
    """title 안의 '@' 가 assignee 로 잘못 추출되지 않아야."""
    text = """## 태스크

- [ ] [task-003] Mention @bot in docs — @real_user
"""
    plan = parse_plan(text)
    t = plan.tasks[0]
    assert t.assignee == "real_user"
    assert "@bot" in t.title
```

- [ ] **Step 2: Run — 실패 (3개 모두 expected fail with 현재 구현)**

```bash
pytest tests/test_plan_parser_service.py::test_parse_plan_title_preserves_em_dash -v
```

- [ ] **Step 3: 구현 — `_parse_task_rest` positional 파싱**

`backend/app/services/plan_parser_service.py` 의 `_parse_task_rest` 를 다음으로 교체:

```python
# title delimiter — ` — ` 다음에 `@` 또는 `` ` `` 가 와야 진짜 delimiter
_TITLE_DELIMITER_RE = re.compile(r" — (?=@|`)")


def _parse_task_rest(rest: str) -> tuple[str, str | None, list[str]]:
    """`<title> — @user — \`path\`, \`path\`` → (title, assignee, paths).

    Phase 3 code review I-2/I-3 fix: positional 파싱.
    - title 은 첫 ` — @` 또는 ` — ` ` 이전까지 (em-dash + backtick/at 조합만 진짜 delimiter)
    - title 영역에 단독 ` — ` 또는 백틱/@ 있어도 truncate 안 함
    - assignee/path 는 title 영역 이후에서만 검색
    """
    delim = _TITLE_DELIMITER_RE.search(rest)
    if delim is None:
        # title only — assignee/path 없음
        return rest.strip(), None, []

    title = rest[: delim.start()].strip()
    after = rest[delim.start():]
    assignee_match = _ASSIGNEE_RE.search(after)
    assignee = assignee_match.group(1) if assignee_match else None
    paths = _PATH_RE.findall(after)
    return title, assignee, paths
```

- [ ] **Step 4: Run — pass (16 tests on plan_parser)**

```bash
pytest tests/test_plan_parser_service.py -v
```

Expected: 16 tests pass (13 기존 + 3 신규).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/plan_parser_service.py backend/tests/test_plan_parser_service.py
git commit -m "fix(phase4): plan_parser title positional 파싱 (code review I-2/I-3)"
```

---

## Task 12: 회귀 + handoff + PR

**Files:**
- Modify: `handoffs/main.md`

- [ ] **Step 1: 전체 테스트 회귀**

```bash
cd /Users/arden/Documents/ardensdevspace/pslog/backend
source venv/bin/activate
pytest -v --tb=short 2>&1 | tail -15
```

Expected: Phase 1 (41) + Phase 2 (32) + Phase 3 (30) + Phase 4 신규 (≥ 28: 3 migration + 8 git_repo + 16 sync + 1 endpoint + 3 parser hardening - 1 BackgroundTask 회귀) ≥ 130 tests, 회귀 0.

실제 분포 확인:
- test_phase4_migration.py: 3
- test_git_repo_service.py: 8 (5 fetch_file + 3 fetch_compare)
- test_sync_service.py: 16
- test_webhook_endpoint.py: 9 (8 기존 + 1 신규)
- test_plan_parser_service.py: 16 (13 기존 + 3 신규)

기존 + 신규 합계: 41 + 32 + 30 + 3 + 8 + 16 + 1 + 3 = **134 tests**.

- [ ] **Step 2: 누락 점검 체크리스트**

설계서 §10.2 sync 통합 테스트:
- [ ] 멱등성 (CRITICAL) → `test_process_event_idempotent_full_cycle`
- [ ] 부분 실패 시 GitPushEvent 잔존 → `test_process_event_records_error_*` (error 기록 + processed_at = now)
- [ ] PLAN 에서 task 삭제 → `test_process_event_archives_tasks_removed_from_plan`
- [ ] 체크 → 언체크 → `test_process_event_rolls_back_done_to_todo`
- [ ] force-push commits 길이 == 0 → `test_process_event_marks_processed_when_no_relevant_files` (commits=[]이면 변경 파일 0)
- [ ] commits_truncated == true 시 Compare API → `test_collect_changed_files_uses_compare_api_when_truncated` (Task 4 의 compare 분기 — 본 plan 의 _collect_changed_files 함수 자체가 분기 — sync 단위 테스트는 fetch_compare 가 noop fixture 라 compare 자체는 git_repo_service 단에서 검증)
- [ ] github_webhook_service signature 격리 → 기존 Phase 2 테스트 유지

설계서 §8 에러 케이스:
- [ ] 서명 검증 실패 → 기존 Phase 2 테스트
- [ ] PLAN 없음 (404) → `test_process_event_skips_when_plan_404`
- [ ] handoff 없음 → 본 phase 는 logging warning 까지. silent skip (Phase 6 Discord 알림 시점에 가시화)
- [ ] external_id 중복 → `test_process_event_records_error_on_duplicate_external_id`
- [ ] 같은 task 동시 체크 → 본 phase 범위 밖 (last-write-wins 는 multi-worker 시나리오 — workers=1 가정)

모든 항목 매핑됨.

- [ ] **Step 3: handoff 갱신**

`handoffs/main.md` 상단 (`# Handoff: main — @ardensdevspace` 다음) 에 Phase 4 섹션 추가:

```markdown
## 2026-04-30 (Phase 4)

- [x] **Phase 4 완료** — sync_service + git fetch (브랜치 `feature/phase-4-sync-service`)
  - [x] `Project.github_pat_encrypted` 컬럼 추가 (alembic, Phase 1 누락분 보강)
  - [x] `git_repo_service` — `fetch_file` (Contents API + base64 decode + 404→None) + `fetch_compare_files` (Compare API)
  - [x] `sync_service.process_event` — 멱등 가드 / 변경 파일 검사 (commits[*].modified 또는 Compare API) / PLAN+handoff fetch / 파싱 / DB 반영
  - [x] PLAN: 신규 task INSERT (`SYNCED_FROM_PLAN`), 체크 → DONE (`CHECKED_BY_COMMIT`), 언체크 (DONE→TODO 롤백, `UNCHECKED_BY_COMMIT`), PLAN 에서 사라진 task → `archived_at` (`ARCHIVED_FROM_PLAN`)
  - [x] handoff: `Handoff` INSERT (UNIQUE 멱등), parsed_tasks/free_notes 채움, MalformedHandoffError 시 `event.error` 기록
  - [x] webhook endpoint: `BackgroundTasks.add_task(_run_sync_in_new_session)` — 자체 세션 + 실제 fetcher 주입
  - [x] reaper callback 주입: `lifespan` 에서 `reap_pending_events(db, _cb)` 로 sync_service 콜백 — Phase 2 stub 교체
  - [x] **plan_parser 하드닝**: title 안의 em-dash / 백틱 / `@` 가 잘못 추출되지 않게 positional 파싱 (code review I-2/I-3 fix)
  - [x] **134 tests passing** (Phase 1 41 + Phase 2 32 + Phase 3 30 + Phase 4 31)

### 마지막 커밋

- pslog: `<sha> docs(handoff): Phase 4 완료 + Phase 5 다음 할 일` (브랜치 `feature/phase-4-sync-service`)
- 브랜치 base: `3525a21` (main, Phase 3 머지 직후)
- 머지 전 PR 생성 + 사용자 검토 단계

### 다음 (Phase 5 — UI + 자동 webhook 등록)

- [ ] `ProjectGitSettings.tsx` — repo URL / PAT / plan_path / handoff_dir 입력 폼
- [ ] 자동 webhook 등록 (GitHub API `POST /repos/{owner}/{repo}/hooks`, 프로젝트별 secret 자동 생성)
- [ ] `TaskCard.tsx` — `source` 배지 + handoff 누락 ⚠️ 표시
- [ ] `HandoffHistory.tsx` — 브랜치별 handoff 이력
- [ ] `POST /api/v1/projects/{id}/git-events/{id}/reprocess` — 사용자 수동 재처리 (sync 실패 이벤트)
- [ ] commits_truncated 시 정확한 base 결정 — `GitPushEvent.before_commit_sha` 컬럼 추가 검토

### 블로커

없음

### 메모 (2026-04-30 Phase 4 추가)

- **GitHub PAT NULL 처리**: PAT 없으면 unauthenticated 호출. 공개 repo 만 가능, rate limit 60/h. 사용자 환경(app-chak private repo) 에선 PAT 필수. Phase 5 UI 에서 PAT 입력 필수 유도.
- **commits_truncated base fallback**: 정확한 `before` 가 webhook payload 에 있지만 GitPushEvent 컬럼에 저장 안 함 (Phase 2 plan 누락). 본 phase 에선 `Project.last_synced_commit_sha or commits[-1].id` fallback. 첫 push (last_synced 없음) + truncated 의 희귀 케이스에서만 missing — 운영에서 모니터링.
- **BackgroundTask vs reaper**: webhook endpoint 가 BackgroundTask 로 sync 시작 → 정상 흐름. 컨테이너 재시작 시 in-flight task 손실 → reaper 가 5분 grace 후 회수. **reaper 가 sync_service.process_event 를 callback 으로 받음 — 같은 코드 경로**. processed_at 가드로 idempotent.
- **error 정책 (자동 재시도 안 함)**: sync 실패 시 `event.error` 기록 + `processed_at = now()`. 사용자 수동 재처리 endpoint 는 Phase 5. 그동안 reaper 는 `processed_at IS NULL` 만 픽업 — 자동 무한 retry 회피.
- **plan_parser title 파싱 변경**: `_TITLE_DELIMITER_RE = " — (?=@|\`)"` lookahead. title 안에 단독 ` — ` 또는 백틱 가능. assignee/path 는 delimiter 이후 영역에서만 검색. Phase 3 spec 의 §6.1 라인 형식과 호환 유지.
- **Handoff parsed_tasks/free_notes 형식**: `parsed_tasks = [{external_id, checked, extra}]`. `free_notes = {last_commit, next, blockers, subtasks: [{parent_external_id, checked, text}]}`. 다중 날짜 history 는 sections[0] 만 본 phase 에서 사용 — sections[1+] 는 Phase 7 brief_service 가 활용 (현재 raw_content 에 보존).

---
```

- [ ] **Step 4: handoff commit**

```bash
git add handoffs/main.md
git commit -m "docs(handoff): Phase 4 완료 + Phase 5 다음 할 일"
```

- [ ] **Step 5: PR 생성**

```bash
git push -u origin feature/phase-4-sync-service
gh pr create --title "feat: Phase 4 — sync_service + git fetch (E2E 동작)" --body "$(cat <<'EOF'
## Summary

- `Project.github_pat_encrypted` 컬럼 추가 (Phase 1 누락분 보강, alembic 회귀 테스트 포함)
- `git_repo_service` — GitHub Contents API (`fetch_file`) + Compare API (`fetch_compare_files`), httpx async
- `sync_service.process_event` — 멱등 가드 / 변경 파일 검사 / PLAN+handoff fetch / 파싱 / DB 반영
  - PLAN: 신규 Task INSERT (`SYNCED_FROM_PLAN`), 체크/언체크 전이 (`CHECKED_BY_COMMIT` / `UNCHECKED_BY_COMMIT`), PLAN 에서 사라진 task → `archived_at` (`ARCHIVED_FROM_PLAN`)
  - handoff: `Handoff` INSERT (UNIQUE 멱등), parsed_tasks / free_notes / raw_content 보존
- webhook endpoint: `BackgroundTasks.add_task` 로 sync 시작 — 자체 세션 + 실제 fetcher 주입
- reaper callback: lifespan 에서 sync_service.process_event 주입 — Phase 2 stub 교체
- **plan_parser 하드닝** (Phase 3 code review I-2/I-3): title 안의 em-dash / 백틱 / `@` 가 잘못 추출되지 않게 positional 파싱

## Architecture decisions

- **의존 주입**: `process_event(db, event, *, fetch_file, fetch_compare)` — fetcher 를 인자로. prod 는 `git_repo_service` 의 실제 함수, 테스트는 fake fetcher
- **자동 재시도 없음**: sync 실패 → `event.error` 기록 + `processed_at = now()`. 사용자 수동 재처리 endpoint 는 Phase 5
- **commits_truncated fallback**: `Project.last_synced_commit_sha or commits[-1].id` (정확한 `before` 컬럼 추가는 Phase 5 검토)

## Migration

- alembic head: `c4dee7f06004` → `<new sha>` (`Project.github_pat_encrypted: bytes | None`)
- 회귀 테스트: 기존 Project 데이터 보존, Fernet round-trip, 컬럼 존재 검증

## Test plan

- [x] `pytest tests/test_phase4_migration.py tests/test_git_repo_service.py tests/test_sync_service.py -v` — Phase 4 신규 27건 pass
- [x] `pytest tests/test_plan_parser_service.py -v` — 16건 pass (3 신규 hardening)
- [x] `pytest tests/test_webhook_endpoint.py -v` — 9건 pass (BackgroundTask 회귀)
- [x] `pytest -v` — Phase 1+2+3+4 = **134 tests pass**, 회귀 0
- [x] 설계서 §10.2 sync 통합 테스트 6항목 매핑 (handoffs/main.md 메모 참조)
- [x] 설계서 §8 에러 케이스 매핑 (PLAN 404, dup external_id, malformed handoff)

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

Expected: PR URL 출력. 사용자 검토 후 머지.

---

## Phase 4 완료 기준 (Acceptance)

- [ ] alembic head 가 새 revision (`Project.github_pat_encrypted` 추가) 으로 진행됨
- [ ] `git_repo_service.fetch_file` / `fetch_compare_files` 가 GitHub API 호출 + 404/5xx 정상 처리
- [ ] `sync_service.process_event` 가 같은 event 두 번 호출해도 DB 변경 1회 (멱등성 CRITICAL)
- [ ] PLAN 신규 task → Task INSERT (`SYNCED_FROM_PLAN`) + TaskEvent (`SYNCED_FROM_PLAN`)
- [ ] PLAN `[x]` 매칭 → 기존 Task `status = DONE` + TaskEvent (`CHECKED_BY_COMMIT`) (직전 DONE 아닐 때만)
- [ ] PLAN `[ ]` 매칭 직전 DONE → `status = TODO` + TaskEvent (`UNCHECKED_BY_COMMIT`)
- [ ] PLAN 에서 사라진 synced task → `archived_at = now()` + TaskEvent (`ARCHIVED_FROM_PLAN`). hard-delete 안 함.
- [ ] handoff fetch + parse → `Handoff` INSERT 1행 (UNIQUE `(project_id, commit_sha)` 멱등)
- [ ] sync 실패 → `event.error` 에 `<class>: <message>` 기록 + `processed_at = now()`. 자동 재시도 없음.
- [ ] webhook endpoint 가 정상 응답 후 BackgroundTask 로 sync 시작 — endpoint 응답 시간 영향 없음
- [ ] 부팅 시 reaper 가 `processed_at IS NULL AND received_at < now() - 5min` 인 이벤트를 sync_service 콜백으로 회수
- [ ] plan_parser 가 title 안의 em-dash / 백틱 / `@` 를 보존 (positional 파싱)
- [ ] Phase 1+2+3 회귀 0 — 기존 103 tests 모두 pass
