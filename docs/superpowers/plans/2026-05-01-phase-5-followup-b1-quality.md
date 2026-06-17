# Phase 5 Follow-up B1 — Quality Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Phase 5a/5b code review 에서 트래킹된 backend quality 후속 4건을 한 PR 로 닫음 — race condition 2건 (I-2 register_webhook, I-4 process_event), 미사용 컬럼 1건 (M-6 last_synced_commit_sha), refactor 1건 (M-10 underscore import 위배). Phase 6 (Discord 알림) 진입 전 깔아둘 안전망.

**Architecture:** PostgreSQL row-level lock (`SELECT ... FOR UPDATE`) 로 race 차단 — 동시 호출이 row 점유로 직렬화. `process_event` 는 이미 단일 outer commit 구조 (line 70 / line 47) 라 entry 에서 lock 획득 → final commit 에서 release 패턴이 그대로 들어맞음. 기존 `processed_at IS NOT NULL → skip` 가드는 lock 획득 후 재확인으로 안전해짐. M-6 는 `_process_inner` 성공 시 `project.last_synced_commit_sha = event.head_commit_sha` 1줄 추가 (실패 path 는 기존 `event.error` 기록만 — 변경 없음). M-10 는 순수 rename refactor — `_auth_headers/_parse_repo/_raise_for_status` 의 leading underscore 제거 + 9개 callsite 갱신.

**Tech Stack:** FastAPI 0.115, SQLAlchemy 2.0 async (`with_for_update()`), pytest + testcontainers (real PostgreSQL row lock 검증), asyncio.Event 로 race 결정적 재현.

**선행 조건:**
- pslog `main`, alembic head = `a1b2c3d4e5f6` (Phase 5a 머지 완료, Phase 5b 머지 완료 — PR #11 + PR #12)
- 170 backend tests baseline (Phase 1+2+3+4+5a+5b 전부)
- Python 3.12.13 venv (`backend/venv`), `.env` 의 `pslog_FERNET_KEY` 존재

**중요한 계약:**

- **I-2 (register_webhook race)**:
  - 두 OWNER 가 거의 동시에 `POST /git-settings/webhook` 호출 → 둘 다 `list_hooks` 의 "matching 없음" 분기 → GitHub 에 hook 2개 생성, DB 의 `webhook_secret_encrypted` 는 마지막 writer 만 보존. 결과: GitHub-side hook 의 secret 과 DB secret mismatch → signature verification 영구 실패.
  - **Fix**: endpoint 진입 직후 `SELECT * FROM projects WHERE id = ? FOR UPDATE` 로 project row lock 획득 (트랜잭션 종료까지). 동시 T2 는 lock 대기 → T1 commit 후 진행 → 이미 갱신된 hook 을 PATCH 하는 정상 분기로 떨어짐.
  - 권한 확인 / project 존재 확인 후 → FOR UPDATE re-read → 실제 작업.
- **I-4 (process_event race)**:
  - 사용자가 webhook 직후 BackgroundTask 가 sync 중에 `POST /reprocess` 클릭 → 두 process_event 가 같은 event 를 동시 처리. UNIQUE 제약이 Handoff/Task 중복 INSERT 는 막아주지만 TaskEvent 는 UNIQUE 없음 → 중복 row 가 쌓임.
  - **Fix (2 layer)**:
    - **Layer 1 (endpoint)**: `reprocess` 가 `processed_at IS NULL` 인 event 를 거부 (409 Conflict — "still processing — wait for reaper or completion"). 가장 흔한 사용자 시나리오 (in-flight 중 클릭) 차단.
    - **Layer 2 (service)**: `process_event` 진입 시 `db.refresh(event, with_for_update={"nowait": False})` — row lock 획득 후 `processed_at` 재확인. 동시 T2 는 lock 대기 → T1 final commit 후 진행 → 갱신된 `processed_at IS NOT NULL` 보고 즉시 return.
  - 두 layer 다 적용 — endpoint 가 first line of defense, service 가 last line.
- **M-6 (last_synced_commit_sha write)**:
  - Phase 1 에서 컬럼 추가됐지만 어디서도 `UPDATE` 안 함 — `commits_truncated` Compare API 의 base fallback 으로만 read. 첫 truncated push 가 정확한 base 없이 fallback 으로 떨어짐.
  - **Fix**: `process_event` 의 `_process_inner` 가 정상 종료 (success path) 시 `project.last_synced_commit_sha = event.head_commit_sha`. 실패 path (try except 의 except 분기) 에서는 update 안 함 — 다음 reprocess 가 실패 이전의 base 로 retry 가능.
  - **No-PLAN/no-handoff skip path 도 update**: changed_files 에 PLAN/handoff 없어 `_process_inner` 가 early return 한 경우도 "이 commit 은 검사 완료" 의미 — update. 다음 truncated push 가 이 SHA 를 base 로 사용 가능.
- **M-10 (underscore import 위배)**:
  - `github_hook_service.py` 가 `from app.services.git_repo_service import _auth_headers, _parse_repo, _raise_for_status` — Python 컨벤션상 `_` 로 시작하는 이름은 module-private. cross-module import 는 위반.
  - **Fix**: 3 함수의 leading underscore 제거 → `auth_headers / parse_repo / raise_for_status`. `git_repo_service` 내부 callsite 6건 + `github_hook_service` 의 import + callsite 6건 갱신. `_REPO_RE` 는 module 내부에서만 쓰이므로 그대로 둠.
- **에러 정책**:
  - I-2 / I-4 race fix 는 사용자 가시 변경 없음 — 같은 입력에 같은 결과, 다만 중복 처리/secret mismatch 가 사라짐
  - I-4 endpoint layer 는 기존 400 (already succeeded) 외에 **409 (still processing)** 가 추가됨 — `frontend/src/hooks/useGithubSettings.ts` 의 `useReprocessEvent` mutation 은 자동 onError 로 토스트 노출 (현재 기준 변경 없음)
  - M-6 는 사용자 가시 변경 없음 (DB read 만 영향)
  - M-10 은 변경 없음 (rename)

---

## File Structure

**수정 파일 (소스):**
- `backend/app/services/sync_service.py` — process_event entry 의 FOR UPDATE refresh + last_synced_commit_sha write
- `backend/app/api/v1/endpoints/git_settings.py` — register_webhook FOR UPDATE re-read + reprocess endpoint 의 409 처리
- `backend/app/services/git_repo_service.py` — 3 함수 rename (private→public) + 내부 callsite 6건
- `backend/app/services/github_hook_service.py` — import + callsite 6건 갱신

**수정 파일 (테스트):**
- `backend/tests/test_sync_service.py` — last_synced_commit_sha update 검증 2건 + 동시 process_event race 검증 1건
- `backend/tests/test_git_settings_endpoint.py` — reprocess 409 검증 1건 + 동시 register_webhook race 검증 1건
- `backend/tests/test_git_repo_service.py` — public 이름 import 갱신 (테스트 자체는 변경 안 됨, 직접 import 안 함)
- `backend/tests/test_github_hook_service.py` — 변경 없음 (public API 만 import 중)

**신규 파일:** 없음. 마이그레이션 없음.

---

## Self-Review Notes

- **Race test 결정성**: `asyncio.Event` + slow fetcher 로 T1 이 lock 보유 중에 T2 가 lock 대기 진입하도록 강제. 이렇게 안 하면 testcontainers PostgreSQL 가 빨라서 race 가 우연히 안 일어나고 PASS 할 수 있음 — fix 없이도 PASS = 무력한 테스트. fix 없이 FAIL 이 보장돼야 TDD 의미.
- **with_for_update API**: SQLAlchemy 2.0 async — `db.refresh(obj, with_for_update={"nowait": False})` 또는 `select(Model).where(...).with_for_update()`. 후자는 새 query — 기존 obj 가 expire 됨. 본 plan 은 `db.refresh` 사용 (in-memory state 유지).
- **M-6 경계**: PLAN/handoff 가 changed_files 에 없어 early return 한 case 도 last_synced 갱신 — head 가 깨끗하게 처리된 commit 임. 단 "no PLAN/handoff" 는 sync 가 의미 있는 일을 안 했단 뜻이라 갱신 안 해도 무방. 결정: **갱신함** (검사 완료 = 다음 base 후보). 기존 fallback 체인 (`event.before` → `last_synced` → `commits[-1]` → `head`) 의 두 번째 우선순위가 이제 정상 동작.
- **Lock 보유 시간**: register_webhook 은 GitHub API 3 호출 (list + create/update) — 수 초. project row lock 을 그동안 보유. 해당 project 의 다른 OWNER 작업 (PATCH git-settings 등) 도 같은 row 를 read 만 한다면 영향 없음 (FOR UPDATE 는 다른 FOR UPDATE 만 block, plain SELECT 는 통과). 다만 다른 endpoint 가 같은 project 를 UPDATE 하려 하면 대기. 이건 의도된 직렬화.

---

### Task 1: M-10 — `_auth_headers / _parse_repo / _raise_for_status` public promote (rename refactor)

**Files:**
- Modify: `backend/app/services/git_repo_service.py:20-51` (3 함수 정의 + 내부 callsite 6건)
- Modify: `backend/app/services/github_hook_service.py:17` (import) + `:30,32,35,48,61,64,78,90,93` (callsite 9건)

- [ ] **Step 1: Run baseline tests**

```bash
cd backend && source venv/bin/activate && pytest tests/test_git_repo_service.py tests/test_github_hook_service.py -v
```

Expected: 모두 PASS (Phase 4 의 git_repo_service 8건 + Phase 5a 의 github_hook_service ~7건). 합계는 `pytest --collect-only` 로 확인.

- [ ] **Step 2: `git_repo_service.py` 의 3 함수 rename**

`backend/app/services/git_repo_service.py` 변경:

```python
def parse_repo(repo_url: str) -> tuple[str, str]:
    """`https://github.com/owner/repo[.git][/]` → (owner, repo). 대소문자 보존."""
    m = _REPO_RE.match(repo_url.strip())
    if not m:
        raise ValueError(f"unsupported repo url: {repo_url!r}")
    return m.group("owner"), m.group("repo")


def auth_headers(pat: str | None) -> dict[str, str]:
    headers = {"Accept": "application/vnd.github.v3+json"}
    if pat:
        headers["Authorization"] = f"token {pat}"
    return headers


def raise_for_status(res: httpx.Response, request: httpx.Request) -> None:
    """raise_for_status() 대체 — mock 환경에서도 안전하게 HTTPStatusError를 발생시킨다.

    Phase 5a code review I-1: Authorization 헤더는 raised exception 에 포함되지 않게 sanitize.
    """
    if res.status_code >= 400:
        sanitized_headers = {
            k: v for k, v in request.headers.items() if k.lower() != "authorization"
        }
        sanitized_request = httpx.Request(
            request.method, request.url, headers=sanitized_headers
        )
        raise httpx.HTTPStatusError(
            message=f"HTTP {res.status_code}",
            request=sanitized_request,
            response=res,
        )
```

같은 파일의 `fetch_file` (line 63, 65, 70) + `fetch_compare_files` (line 86, 88, 91) 내부 callsite 도 prefix `_` 제거:

```python
async def fetch_file(
    repo_url: str,
    pat: str | None,
    sha: str,
    path: str,
    *,
    timeout: float = 30.0,
) -> str | None:
    """GitHub Contents API → 파일 raw text. 404 → None. 5xx → HTTPStatusError raise."""
    owner, repo = parse_repo(repo_url)
    url = f"{_GITHUB_API}/repos/{owner}/{repo}/contents/{path}?ref={sha}"
    request = httpx.Request("GET", url, headers=auth_headers(pat))
    async with httpx.AsyncClient(timeout=timeout) as client:
        res = await client.send(request)
    if res.status_code == 404:
        return None
    raise_for_status(res, request)
    data = res.json()
    if data.get("encoding") == "base64":
        return base64.b64decode(data["content"]).decode("utf-8")
    return data.get("content", "")


async def fetch_compare_files(
    repo_url: str,
    pat: str | None,
    base_sha: str,
    head_sha: str,
    *,
    timeout: float = 30.0,
) -> list[str]:
    """GitHub Compare API → `files[*].filename` 리스트. 404 / 5xx → HTTPStatusError raise."""
    owner, repo = parse_repo(repo_url)
    url = f"{_GITHUB_API}/repos/{owner}/{repo}/compare/{base_sha}...{head_sha}"
    request = httpx.Request("GET", url, headers=auth_headers(pat))
    async with httpx.AsyncClient(timeout=timeout) as client:
        res = await client.send(request)
    raise_for_status(res, request)
    data = res.json()
    return [f["filename"] for f in data.get("files", [])]
```

`_GITHUB_API` 와 `_REPO_RE` 는 module-private 그대로 유지 (외부 import 없음).

- [ ] **Step 3: `github_hook_service.py` 의 import + callsite 갱신**

`backend/app/services/github_hook_service.py` 의 line 17 import 와 본문 callsite 6건 모두 prefix `_` 제거:

```python
from app.services.git_repo_service import auth_headers, parse_repo, raise_for_status
```

본문에서 `_auth_headers(pat)` → `auth_headers(pat)`, `_parse_repo(repo_url)` → `parse_repo(repo_url)`, `_raise_for_status(res, request)` → `raise_for_status(res, request)` 로 모두 변경 (line 30, 32, 35, 48, 61, 64, 78, 90, 93 — 총 9개 사용처).

- [ ] **Step 4: `git_repo_service` import 위반 검사**

```bash
cd backend && grep -rn "from app.services.git_repo_service import _" app/ tests/
```

Expected: 0 matches (다른 파일이 underscore-prefixed 이름을 import 하지 않는지 회귀 확인).

- [ ] **Step 5: 영향 받는 테스트 실행**

```bash
cd backend && pytest tests/test_git_repo_service.py tests/test_github_hook_service.py tests/test_sync_service.py tests/test_git_settings_endpoint.py -v
```

Expected: 모두 PASS. rename 만 했고 동작 변경 없음.

- [ ] **Step 6: Commit**

```bash
cd /Users/arden/Documents/ardensdevspace/pslog
git add backend/app/services/git_repo_service.py backend/app/services/github_hook_service.py
git commit -m "refactor(b1/M-10): git_repo_service 의 auth_headers/parse_repo/raise_for_status public promote

- 3 함수의 leading underscore 제거 — github_hook_service 의 cross-module
  underscore-import 위반 해소 (Phase 5a code review M-10)
- _GITHUB_API / _REPO_RE 는 module-internal 그대로 유지
- 9개 callsite 갱신, 동작 변경 없음"
```

---

### Task 2: M-6 — `sync_service` 가 성공 시 `Project.last_synced_commit_sha` 갱신

**Files:**
- Modify: `backend/app/services/sync_service.py:52-71` (process_event 의 try block)
- Modify: `backend/tests/test_sync_service.py` (회귀 테스트 추가 — 2건)

- [ ] **Step 1: Failing test — 정상 처리 후 last_synced_commit_sha 갱신 (PLAN 변경 path)**

`backend/tests/test_sync_service.py` 끝에 추가:

```python
# ---------------------------------------------------------------------------
# B1 / M-6: last_synced_commit_sha update on success
# ---------------------------------------------------------------------------


async def test_process_event_updates_last_synced_on_plan_success(
    async_session: AsyncSession,
):
    """정상 처리 (PLAN 변경 reflect) 후 project.last_synced_commit_sha == event.head_commit_sha."""
    proj = await _seed_project(async_session)
    head = "f" * 40
    event = await _seed_event(
        async_session,
        proj,
        head_sha=head,
        commits=[{"id": head, "modified": ["PLAN.md"], "added": []}],
    )

    async def fake_fetch_file(repo, pat, sha, path):
        return "## 태스크\n\n- [ ] [task-001] T — @alice"

    async def fake_compare(repo, pat, base, head_sha):  # noqa: ARG001
        return ["PLAN.md"]

    await process_event(
        async_session, event, fetch_file=fake_fetch_file, fetch_compare=fake_compare,
    )

    await async_session.refresh(proj)
    assert proj.last_synced_commit_sha == head
    await async_session.refresh(event)
    assert event.processed_at is not None
    assert event.error is None


async def test_process_event_does_not_update_last_synced_on_failure(
    async_session: AsyncSession,
):
    """fetch_file 가 raise → process_event 가 catch + event.error 기록.
    이 case 에서는 last_synced_commit_sha 갱신 X (재처리 시 정확한 base 보존)."""
    proj = await _seed_project(async_session)
    proj.last_synced_commit_sha = "a" * 40  # 이전 처리분
    await async_session.commit()
    await async_session.refresh(proj)

    head = "b" * 40
    event = await _seed_event(
        async_session,
        proj,
        head_sha=head,
        commits=[{"id": head, "modified": ["PLAN.md"], "added": []}],
    )

    async def fake_fetch_file(repo, pat, sha, path):
        raise RuntimeError("github 502")

    async def fake_compare(repo, pat, base, head_sha):  # noqa: ARG001
        return ["PLAN.md"]

    await process_event(
        async_session, event, fetch_file=fake_fetch_file, fetch_compare=fake_compare,
    )

    await async_session.refresh(proj)
    assert proj.last_synced_commit_sha == "a" * 40  # 이전 값 유지
    await async_session.refresh(event)
    assert event.error is not None
    assert "RuntimeError" in event.error
```

- [ ] **Step 2: Verify failure**

```bash
cd backend && pytest tests/test_sync_service.py::test_process_event_updates_last_synced_on_plan_success -v
```

Expected: FAIL — `assert None == 'fffff...'` (현재 코드는 last_synced_commit_sha write 안 함).

```bash
pytest tests/test_sync_service.py::test_process_event_does_not_update_last_synced_on_failure -v
```

Expected: PASS — 현재 코드도 실패 path 에서 update 안 함 (애초에 어디서도 안 함). 이 테스트는 fix 후에도 같은 결과를 보장하는 가드 테스트.

- [ ] **Step 3: Implement — process_event 성공 path 에 last_synced 갱신**

`backend/app/services/sync_service.py` 의 try block (현재 line 52-55) 변경:

```python
    try:
        await _process_inner(db, event, project, fetch_file=fetch_file, fetch_compare=fetch_compare)
        # M-6: 성공 시 project.last_synced_commit_sha 를 head 로 갱신.
        # commits_truncated 의 Compare API base 로 사용됨 (sync_service._collect_changed_files).
        # 실패 path (except 분기) 에서는 갱신 안 함 — 재처리 시 직전 성공 커밋 base 가 유지됨.
        project.last_synced_commit_sha = event.head_commit_sha
        event.processed_at = datetime.utcnow()
        await db.commit()
    except Exception as exc:
```

- [ ] **Step 4: Run new tests + 기존 sync_service 테스트 전체 회귀**

```bash
cd backend && pytest tests/test_sync_service.py -v
```

Expected: 모두 PASS. 신규 2건 + 기존 sync_service 테스트 (~34건) 전부 통과. 특히 기존 `test_collect_changed_files_skips_null_sha_before` (line 784) 처럼 last_synced_commit_sha 를 명시적으로 수동 set 해 사용하는 테스트가 회귀하지 않는지 확인.

- [ ] **Step 5: Commit**

```bash
cd /Users/arden/Documents/ardensdevspace/pslog
git add backend/app/services/sync_service.py backend/tests/test_sync_service.py
git commit -m "fix(b1/M-6): sync_service 가 성공 시 Project.last_synced_commit_sha 갱신

- _process_inner 성공 시 project.last_synced_commit_sha = event.head_commit_sha
- 실패 path 는 그대로 — 직전 base 가 retry 시까지 유지
- 회귀 테스트: success path 갱신 + failure path 비갱신 (Phase 5a code review M-6)"
```

---

### Task 3A: I-4 (Layer 1) — reprocess endpoint 의 in-flight 거부 (409)

**Files:**
- Modify: `backend/app/api/v1/endpoints/git_settings.py:243-262` (reprocess endpoint)
- Modify: `backend/tests/test_git_settings_endpoint.py` (회귀 테스트 추가 — 1건)

- [ ] **Step 1: Failing test — `processed_at IS NULL` 인 event 의 reprocess 는 409**

`backend/tests/test_git_settings_endpoint.py` 의 reprocess 섹션 (line 461 근처) 끝에 추가:

```python
async def test_reprocess_409_when_still_in_flight(
    client_with_db, async_session: AsyncSession
):
    """processed_at IS NULL (= 초기 BackgroundTask 가 아직 처리 중) 인 event 의 reprocess 는 409.
    User 가 webhook 직후 BackgroundTask 가 끝나기 전 클릭한 case 차단 (B1 / I-4 layer 1)."""
    user, proj = await _seed_user_project(async_session)
    event = GitPushEvent(
        project_id=proj.id, branch="main", head_commit_sha="a" * 40,
        commits=[], commits_truncated=False, pusher="alice",
        processed_at=None,  # 아직 처리 중
        error=None,
    )
    async_session.add(event)
    await async_session.commit()
    await async_session.refresh(event)

    token = _auth_token(user)
    res = await client_with_db.post(
        f"/api/v1/projects/{proj.id}/git-events/{event.id}/reprocess",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 409
    assert "still" in res.json()["detail"].lower() or "processing" in res.json()["detail"].lower()
```

- [ ] **Step 2: Verify failure**

```bash
cd backend && pytest tests/test_git_settings_endpoint.py::test_reprocess_409_when_still_in_flight -v
```

Expected: FAIL — 현재 endpoint 는 `processed_at IS NULL` 케이스를 fall through 시켜 200 + reset 처리. 신규 가드가 없어 200 이 반환됨.

- [ ] **Step 3: Implement — endpoint 에 in-flight 가드 추가**

`backend/app/api/v1/endpoints/git_settings.py` 의 reprocess 핸들러 (line 247 근처, 기존 400 가드 직전) 변경:

```python
    event = await db.get(GitPushEvent, event_id)
    if event is None or event.project_id != project_id:
        raise HTTPException(status_code=404, detail="Event not found")

    # B1 / I-4 layer 1: in-flight 거부.
    # processed_at IS NULL = 초기 BackgroundTask 가 아직 처리 중이거나 reaper 회수 대기.
    # 이 시점에 reprocess 트리거하면 두 process_event 가 같은 event 로 동시 실행 → TaskEvent 중복.
    # reaper 가 5분 grace 후 회수 가능하므로 사용자는 기다리거나 재처리 대신 reaper 에 위임.
    if event.processed_at is None:
        raise HTTPException(
            status_code=409,
            detail="Event is still being processed — wait for completion or reaper",
        )

    if event.processed_at is not None and event.error is None:
        raise HTTPException(
            status_code=400,
            detail="Event already processed successfully — nothing to reprocess",
        )
```

- [ ] **Step 4: Run reprocess 관련 테스트 전체**

```bash
cd backend && pytest tests/test_git_settings_endpoint.py -k reprocess -v
```

Expected: 모두 PASS — 신규 409 + 기존 reprocess 5건 (success / 400 already-succeeded / 404 cross-project / 403 non-owner / 404 non-member).

- [ ] **Step 5: Commit**

```bash
cd /Users/arden/Documents/ardensdevspace/pslog
git add backend/app/api/v1/endpoints/git_settings.py backend/tests/test_git_settings_endpoint.py
git commit -m "fix(b1/I-4 layer 1): reprocess endpoint 가 in-flight event 거부 (409)

- processed_at IS NULL 인 event 는 409 — 초기 BackgroundTask 가 아직 처리 중
- 사용자 가 webhook 직후 클릭하는 가장 흔한 race 시나리오 차단
- service-layer FOR UPDATE 가드는 별도 commit 에서 추가 (Phase 5a code review I-4)"
```

---

### Task 3B: I-4 (Layer 2) — `process_event` 의 SELECT FOR UPDATE 재진입 가드

**Files:**
- Modify: `backend/app/services/sync_service.py:31-50` (process_event entry)
- Modify: `backend/tests/test_sync_service.py` (회귀 테스트 추가 — 1건, 결정적 race)

- [ ] **Step 1: Failing test — 동시 process_event 호출이 직렬화되어 fetch 1번만**

`backend/tests/test_sync_service.py` 끝에 추가 (Task 2 의 신규 테스트들 다음):

```python
# ---------------------------------------------------------------------------
# B1 / I-4 layer 2: process_event SELECT FOR UPDATE 재진입 가드
# ---------------------------------------------------------------------------

import asyncio


async def test_concurrent_process_event_only_runs_once(
    async_session: AsyncSession, postgres_container,
):
    """같은 event 를 두 session 이 동시에 process_event 호출 → fetch 는 1번만 실행.
    FOR UPDATE row lock 으로 T2 가 T1 final commit 까지 대기 → processed_at 보고 즉시 return.
    fix 없으면 두 호출 다 fetch → counter == 2.

    별도 session 두 개를 직접 만들어 실제 row-level lock 동작 검증 (testcontainers PG).
    """
    from sqlalchemy.ext.asyncio import AsyncSession as _AsyncSession
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    # async_session fixture 와 같은 DB 에 별도 engine + sessionmaker 두 개
    dsn = postgres_container.get_connection_url().replace(
        "postgresql://", "postgresql+psycopg://"
    )
    engine_a = create_async_engine(dsn, future=True)
    engine_b = create_async_engine(dsn, future=True)
    Maker_a = async_sessionmaker(engine_a, class_=_AsyncSession, expire_on_commit=False)
    Maker_b = async_sessionmaker(engine_b, class_=_AsyncSession, expire_on_commit=False)

    proj = await _seed_project(async_session)
    head = "c" * 40
    event = await _seed_event(
        async_session,
        proj,
        head_sha=head,
        commits=[{"id": head, "modified": ["PLAN.md"], "added": []}],
    )

    counter = {"n": 0}
    release = asyncio.Event()
    t1_inside_fetch = asyncio.Event()

    async def slow_fetch_file(repo, pat, sha, path):
        counter["n"] += 1
        if counter["n"] == 1:
            t1_inside_fetch.set()
        await release.wait()
        return "## 태스크\n\n- [ ] [task-001] T — @alice"

    async def fake_compare(repo, pat, base, head_sha):  # noqa: ARG001
        return ["PLAN.md"]

    async def runner(maker):
        async with maker() as db:
            ev = await db.get(GitPushEvent, event.id)
            await process_event(
                db, ev, fetch_file=slow_fetch_file, fetch_compare=fake_compare,
            )

    async def releaser():
        # T1 이 fetch 까지 들어간 시점에 T2 도 entry 에서 FOR UPDATE 대기 중이도록 시간 둠.
        await t1_inside_fetch.wait()
        await asyncio.sleep(0.3)
        release.set()

    await asyncio.gather(runner(Maker_a), runner(Maker_b), releaser())

    # 핵심 검증: fetch 는 1번만 호출됨 — T2 는 lock 대기 → processed_at 보고 return
    assert counter["n"] == 1, (
        f"expected fetch to be called once but was called {counter['n']} times — "
        "FOR UPDATE re-read 가 process_event entry 에 적용되지 않음"
    )

    # cleanup
    await engine_a.dispose()
    await engine_b.dispose()
```

이 테스트가 동작하려면 `postgres_container` fixture 가 conftest 에 노출되어 있어야 함. 현재 conftest 가 그것을 노출하는지 확인:

```bash
cd backend && grep -n "postgres_container" tests/conftest.py
```

Expected: postgres_container fixture 가 있거나, async_session 만 있고 container 는 module-level. 만약 없으면 conftest 에서 export 필요.

- [ ] **Step 2: conftest 의 postgres_container fixture 확인 (또는 추가)**

위 grep 결과에 따라 분기:

**(A) `postgres_container` 가 fixture 로 이미 있는 경우** — 다음 단계로 진행.

**(B) module-level container 만 있는 경우** — `tests/conftest.py` 를 보고 fixture 추가:

```python
# tests/conftest.py — 기존 _container 객체에 fixture wrapper 추가 (없을 때만)
@pytest.fixture(scope="session")
def postgres_container():
    return _container  # 기존 module-level testcontainer 객체
```

(`_container` 의 실제 변수 이름은 conftest 에서 확인하여 그대로 사용.)

- [ ] **Step 3: Verify failure**

```bash
cd backend && pytest tests/test_sync_service.py::test_concurrent_process_event_only_runs_once -v -s
```

Expected: FAIL — fix 없이 counter == 2 (두 session 모두 fetch 진입).

만약 race 가 운 좋게 안 나서 counter == 1 이 우연히 나오면 (testcontainers 가 너무 빨라서), `await asyncio.sleep(0.3)` 의 sleep duration 을 늘려 T2 의 entry 가 lock 에 걸릴 시간을 더 확보 — 0.5s 또는 1.0s.

- [ ] **Step 4: Implement — process_event 진입에 FOR UPDATE refresh**

`backend/app/services/sync_service.py` 의 process_event 함수 (line 31-50) 변경:

```python
async def process_event(
    db: AsyncSession,
    event: GitPushEvent,
    *,
    fetch_file: FetchFile,
    fetch_compare: FetchCompare,
) -> None:
    """진입점. 멱등 + 결정적 — 같은 event 두 번 호출해도 DB 변경 1회만.

    B1 / I-4 layer 2: 진입 시 row-level lock 획득 (FOR UPDATE) 후 processed_at 재확인.
    동시 호출 시 후행 caller 는 lock 대기 → 선행 caller commit 후 processed_at 갱신본 보고 return.
    final commit 시 lock release. process_event 가 단일 outer commit 구조라서 그대로 적용 가능.
    """
    # FOR UPDATE 로 row 점유 — 동시 caller 차단.
    # SQLAlchemy 2.0: db.refresh(obj, with_for_update=...). nowait=False 로 lock 대기.
    await db.refresh(event, with_for_update={"nowait": False})

    if event.processed_at is not None:
        logger.info("event %s already processed at %s — skip", event.id, event.processed_at)
        return

    project = await db.get(Project, event.project_id)
    if project is None:
        event.processed_at = datetime.utcnow()
        event.error = "project not found"
        await db.commit()
        return

    event_id = event.id  # 세션 poison 후 expire 대비

    try:
        await _process_inner(db, event, project, fetch_file=fetch_file, fetch_compare=fetch_compare)
        # M-6: 성공 시 project.last_synced_commit_sha 를 head 로 갱신.
        project.last_synced_commit_sha = event.head_commit_sha
        event.processed_at = datetime.utcnow()
        await db.commit()
    except Exception as exc:
        # ... 기존 except 분기 그대로 유지
```

(Task 2 에서 이미 추가한 `project.last_synced_commit_sha = ...` 라인은 그대로 유지.)

- [ ] **Step 5: Run race test + 회귀 전체**

```bash
cd backend && pytest tests/test_sync_service.py -v
```

Expected: 모두 PASS. 신규 race 테스트가 결정적으로 PASS (counter == 1).

- [ ] **Step 6: Commit**

```bash
cd /Users/arden/Documents/ardensdevspace/pslog
git add backend/app/services/sync_service.py backend/tests/test_sync_service.py backend/tests/conftest.py
git commit -m "fix(b1/I-4 layer 2): process_event entry 에 SELECT FOR UPDATE 재진입 가드

- db.refresh(event, with_for_update=...) 로 row lock 획득
- 동시 호출 시 후행 caller 가 선행 caller final commit 까지 대기 → processed_at 갱신본 보고 즉시 return
- 결정적 race 테스트: slow_fetch + asyncio.Event 로 두 session 직렬화 검증
- final commit 시 lock release (process_event 가 단일 outer commit 구조)"
```

---

### Task 4: I-2 — `register_webhook` 의 SELECT FOR UPDATE 재진입 가드

**Files:**
- Modify: `backend/app/api/v1/endpoints/git_settings.py:113-178` (register_webhook handler)
- Modify: `backend/tests/test_git_settings_endpoint.py` (회귀 테스트 추가 — 1건, 결정적 race)

- [ ] **Step 1: Failing test — 동시 register_webhook 가 직렬화되어 list_hooks 1번만**

`backend/tests/test_git_settings_endpoint.py` 끝에 추가:

```python
# ---------------------------------------------------------------------------
# B1 / I-2: register_webhook SELECT FOR UPDATE 재진입 가드
# ---------------------------------------------------------------------------

import asyncio


async def test_concurrent_register_webhook_serializes(
    async_session: AsyncSession, monkeypatch: pytest.MonkeyPatch, postgres_container,
):
    """같은 project 에 대한 두 OWNER POST /webhook 호출이 직렬화 →
    선행 호출의 secret rotate 가 끝난 후 후행 호출이 실행됨.
    fix 없으면 둘 다 list_hooks → 둘 다 'no matching hook' 분기 → create_hook 2번.
    fix 후 후행은 선행이 만든 hook 을 보고 update_hook 분기로 떨어짐.

    검증: create_hook + update_hook 호출 합계 = 2 (선행 create + 후행 update),
          create_hook 은 정확히 1번. fix 없으면 create_hook 2번.
    """
    from cryptography.fernet import Fernet
    from sqlalchemy.ext.asyncio import AsyncSession as _AsyncSession
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from httpx import ASGITransport, AsyncClient

    monkeypatch.setenv("pslog_FERNET_KEY", Fernet.generate_key().decode())
    import importlib
    import app.config as _config
    importlib.reload(_config)
    import app.core.crypto as _crypto
    importlib.reload(_crypto)

    user, proj = await _seed_user_project(async_session)
    proj.git_repo_url = "https://github.com/ardenspace/app-chak"
    proj.github_pat_encrypted = _crypto.encrypt_secret("ghp_test_token")
    await async_session.commit()

    proj_id = proj.id

    # 선행 호출이 list_hooks 진입한 시점에 후행도 endpoint 진입까지 가도록 강제.
    # FOR UPDATE 가 적용됐으면 후행은 SELECT FOR UPDATE 에서 대기 → 선행 commit 후 진행 → list_hooks 가 새 hook 보고 update_hook.
    list_calls = {"n": 0}
    create_calls = {"n": 0}
    update_calls = {"n": 0}
    stored_hook: dict[str, object] = {}
    t1_inside_list = asyncio.Event()
    release = asyncio.Event()

    async def fake_list_hooks(repo, pat):
        list_calls["n"] += 1
        if list_calls["n"] == 1:
            t1_inside_list.set()
            await release.wait()
            return []
        # 후행 호출 — 선행이 만든 hook 이 이미 있어야 함
        return [stored_hook] if stored_hook else []

    async def fake_create_hook(repo, pat, *, callback_url, secret):
        create_calls["n"] += 1
        h = {"id": 88888, "config": {"url": callback_url}}
        stored_hook.update(h)
        return h

    async def fake_update_hook(repo, pat, *, hook_id, callback_url, secret):
        update_calls["n"] += 1
        return {"id": hook_id, "config": {"url": callback_url}}

    import app.services.github_hook_service as hook_mod
    monkeypatch.setattr(hook_mod, "list_hooks", fake_list_hooks)
    monkeypatch.setattr(hook_mod, "create_hook", fake_create_hook)
    monkeypatch.setattr(hook_mod, "update_hook", fake_update_hook)

    # 별도 session 두 개로 endpoint 호출. ASGITransport 로 router 직접.
    dsn = postgres_container.get_connection_url().replace(
        "postgresql://", "postgresql+psycopg://"
    )
    engine_a = create_async_engine(dsn, future=True)
    engine_b = create_async_engine(dsn, future=True)
    Maker_a = async_sessionmaker(engine_a, class_=_AsyncSession, expire_on_commit=False)
    Maker_b = async_sessionmaker(engine_b, class_=_AsyncSession, expire_on_commit=False)

    from app.main import app
    from app.database import get_db

    token = _auth_token(user)

    async def call(maker):
        async def override():
            async with maker() as db:
                yield db
        app.dependency_overrides[get_db] = override
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            return await ac.post(
                f"/api/v1/projects/{proj_id}/git-settings/webhook",
                headers={"Authorization": f"Bearer {token}"},
            )

    async def releaser():
        await t1_inside_list.wait()
        await asyncio.sleep(0.3)
        release.set()

    # 직렬화: app.dependency_overrides 가 module-global → 두 호출이 dict 를 공유
    # 그래도 lock 자체는 DB row 에 걸리므로 race 검증 가능.
    # 단, override 가 둘 다 같은 maker 를 가리키지 않게 별도 호출에서 set/clear.
    res_a_task = asyncio.create_task(call(Maker_a))
    await asyncio.sleep(0.05)  # T1 이 endpoint 진입 시작
    res_b_task = asyncio.create_task(call(Maker_b))
    rel_task = asyncio.create_task(releaser())

    res_a, res_b, _ = await asyncio.gather(res_a_task, res_b_task, rel_task)
    app.dependency_overrides.clear()

    assert res_a.status_code == 200
    assert res_b.status_code == 200

    # 핵심 검증: create_hook 정확히 1번. fix 없으면 둘 다 create_hook 진입 → 2번.
    assert create_calls["n"] == 1, (
        f"expected create_hook to be called once but was called {create_calls['n']} times — "
        "FOR UPDATE re-read 가 register_webhook 에 적용되지 않음"
    )
    # 후행은 update_hook 분기로 떨어져야 함
    assert update_calls["n"] == 1

    await engine_a.dispose()
    await engine_b.dispose()
```

**참고**: 위 테스트는 dependency_overrides 가 module-global 이라는 한계가 있어 직렬화 패턴이 깔끔하지 않음. 실패 시 단순화 fallback: 두 별도 session 으로 직접 service-layer 함수 호출 (endpoint 우회) 하는 패턴으로 변경 — 그래도 register_webhook 의 FOR UPDATE 검증 의도는 보존됨. 이 fallback 은 Step 3 implementation 후 결정.

- [ ] **Step 2: Verify failure**

```bash
cd backend && pytest tests/test_git_settings_endpoint.py::test_concurrent_register_webhook_serializes -v -s
```

Expected: FAIL — `create_calls["n"] == 2` (둘 다 create_hook 진입).

- [ ] **Step 3: Implement — register_webhook 진입에 FOR UPDATE refresh**

`backend/app/api/v1/endpoints/git_settings.py` 의 register_webhook handler (line 113-178) 변경 — `if not project.git_repo_url:` 검사 직전에 FOR UPDATE re-read 추가:

```python
@router.post(
    "/{project_id}/git-settings/webhook",
    response_model=WebhookRegisterResponse,
)
async def register_webhook(
    project_id: UUID,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
):
    """GitHub repo 에 push webhook 자동 등록 (또는 갱신).

    - 같은 callback url 의 hook 이 있으면 PATCH (config.secret 갱신)
    - 없으면 POST (신규 등록)
    - 새 webhook_secret 항상 생성 — 기존 secret 무효화 (regenerate 의 부수 효과)

    B1 / I-2: 두 OWNER 가 동시 호출하면 둘 다 'no matching' 분기 진입 → GitHub 측에 hook 2개,
    DB 의 webhook_secret 는 last writer 만 보존 → GitHub-side hook 의 secret 과 mismatch.
    project row 에 SELECT FOR UPDATE 로 직렬화 — 선행 caller 의 commit 까지 후행이 대기.
    """
    project = await project_service.get_project(db, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    role = await get_effective_role(db, user.id, project_id)
    if role is None:
        raise HTTPException(status_code=404, detail="Project not found")
    if not can_manage(role):
        raise HTTPException(status_code=403, detail="Owner only")

    # B1 / I-2: 권한 확인 후 row lock 획득. final commit 시 release.
    # 동시 호출 시 후행은 여기서 대기 → 선행 commit 후 webhook_secret_encrypted 갱신본 보고 진행.
    await db.refresh(project, with_for_update={"nowait": False})

    if not project.git_repo_url:
        raise HTTPException(status_code=400, detail="git_repo_url 미설정")
    if project.github_pat_encrypted is None:
        raise HTTPException(status_code=400, detail="GitHub PAT 미설정")

    # ... 이하 기존 로직 그대로
```

(Step 3 의 실제 변경은 line 132 `if not can_manage(role):` 직후, `if not project.git_repo_url:` 직전에 `await db.refresh(project, with_for_update=...)` 한 줄 + comment 추가.)

- [ ] **Step 4: Run race test + git_settings 회귀 전체**

```bash
cd backend && pytest tests/test_git_settings_endpoint.py -v
```

Expected: 모두 PASS — 신규 race 테스트가 결정적으로 PASS, 기존 webhook 등록 테스트 (`test_post_webhook_creates_new_hook`, `test_post_webhook_updates_existing_hook`, `test_post_webhook_400_when_repo_or_pat_missing`, `test_post_webhook_403_for_non_owner`, `test_post_webhook_404_for_non_member`) 도 회귀 없음.

만약 race 테스트가 dependency_overrides 한계로 deterministic 하지 않으면 (Step 1 의 참고 사항), 다음 fallback 으로 단순화:

```python
# fallback: endpoint 우회. 두 session 으로 같은 register_webhook 본문 시뮬레이션.
# project_service + github_hook_service 직접 호출 — FOR UPDATE 자체 검증.
```

이 fallback 은 endpoint coverage 는 잃지만 row-lock 동작 자체는 그대로 검증.

- [ ] **Step 5: Commit**

```bash
cd /Users/arden/Documents/ardensdevspace/pslog
git add backend/app/api/v1/endpoints/git_settings.py backend/tests/test_git_settings_endpoint.py
git commit -m "fix(b1/I-2): register_webhook 진입에 SELECT FOR UPDATE 재진입 가드

- 권한 확인 후 db.refresh(project, with_for_update=...) 로 project row lock 획득
- 동시 OWNER 호출 시 후행은 선행 final commit 까지 대기 → 갱신된 webhook hook 보고 update 분기
- create_hook 중복 호출 + secret mismatch 차단
- 결정적 race 테스트: slow_list_hooks + asyncio.Event 로 두 session 직렬화 검증"
```

---

### Task 5: 최종 회귀 + PR 준비

- [ ] **Step 1: 전체 backend 테스트 실행**

```bash
cd backend && pytest -v 2>&1 | tail -30
```

Expected: 모두 PASS. 합계 = 170 (Phase 5b baseline) + 6 신규 (M-6 success / M-6 failure / I-4 layer 1 409 / I-4 layer 2 race / I-2 race / Task 1 은 회귀만이라 +0) = **176 tests**.

`tail -30` 의 마지막 줄 형태 예시:

```
======================== 176 passed in 12.34s ========================
```

만약 fail 이 있으면 즉시 정지 + 디버깅 (race 테스트의 sleep duration 조정 등).

- [ ] **Step 2: lint 확인**

```bash
cd backend && python -m ruff check app/ tests/
```

Expected: 변경 부분에서 신규 lint 위배 없음. 사전 위배가 있어도 본 PR scope 밖 — 그대로 둠.

- [ ] **Step 3: handoff 갱신**

`handoffs/main.md` 상단 (가장 최근 항목 위) 에 새 섹션 추가:

```markdown
## 2026-05-01 (Phase 5 follow-up B1)

- [x] **B1 — Quality fixes (race + 미사용 컬럼 + refactor)** — 브랜치 `feature/phase-5-followup-b1-quality`
  - [x] **M-10**: `auth_headers / parse_repo / raise_for_status` public promote (rename, 9 callsite)
  - [x] **M-6**: `sync_service` 가 success 시 `Project.last_synced_commit_sha = event.head_commit_sha` write — `commits_truncated` Compare API base 정확화
  - [x] **I-4 layer 1**: `reprocess` endpoint 가 `processed_at IS NULL` event 거부 (409)
  - [x] **I-4 layer 2**: `process_event` entry 에 `db.refresh(event, with_for_update=...)` — 동시 호출 직렬화
  - [x] **I-2**: `register_webhook` entry 에 `db.refresh(project, with_for_update=...)` — 동시 OWNER 호출 직렬화
  - [x] **검증**: backend 176 tests pass (170 baseline + 6 신규). race 테스트 2건은 `asyncio.Event` + slow fetcher 로 결정적 직렬화 검증 (fix 없으면 FAIL 보장).

### 마지막 커밋

- pslog: `<sha> docs(handoff): Phase 5 follow-up B1 완료 + B2 다음 할 일`
- 브랜치 base: `27e8b56` (main, Makefile chore 직후)

### 다음 (B2 — Phase 5b UI 후속 / 그 후 Phase 6)

**B2 — Phase 5b UI 후속** (별도 plan):
- [ ] TaskCard ⚠️ handoff 누락 표시 — backend 필드 또는 계산 추가 (Task `last_commit_sha` join → Handoff 존재 여부)
- [ ] GitEventList 모달 + `useReprocessEvent` 호출 site (sync 실패 이벤트 list — 현재 `useReprocessEvent` 훅만 만들어둠)

**Phase 6 — Discord 알림 통합** (B2 머지 후):
- [ ] `discord_service` 확장 (체크 변경 / handoff 누락 / 롤백 알림 3종)
- [ ] `sync_service` 가 BackgroundTask 로 알림 트리거
- [ ] cooldown 정책 (3회 연속 실패 → disable)

### 블로커

없음

### 메모 (2026-05-01 B1 추가)

- **`db.refresh(obj, with_for_update={"nowait": False})` 패턴**: SQLAlchemy 2.0 async 에서 in-memory ORM object 의 row lock 재획득 표준 방식. `select(...).with_for_update()` 는 새 query 라 obj 가 expire — refresh 가 더 적합. 본 phase 의 두 race fix (I-2/I-4) 모두 이 패턴 사용.
- **race 테스트 결정성**: testcontainers PG 가 빨라서 단순 `asyncio.gather` 두 호출은 race 가 우연히 안 일어나고 PASS 함 — fix 없는 코드도 PASS = 무력. `asyncio.Event` + slow 가짜 fetcher 로 T1 이 work 도중에 T2 가 entry lock 에서 대기하도록 강제. 이래야 fix 없는 코드는 FAIL 보장.
- **M-6 갱신 시점**: `_process_inner` 가 PLAN/handoff 변경 없어 early return 하는 path 도 갱신 — head 가 깨끗하게 검사된 commit 이라 다음 truncated push 의 base 후보로 유효. 단 success path 만 갱신 — failure path 는 직전 base 보존 (재처리 시 정확).
- **process_event 의 multi-flush vs single-commit**: `_apply_plan` 내부의 `await db.flush()` 들은 SQL 만 보내고 commit 은 안 함 — row lock 유지. 실제 `db.commit()` 은 process_event outer 의 try (line 70) 또는 except (line 70 변형) 에서 한 번만 — 이 구조라 entry FOR UPDATE 가 final commit 까지 lock 유지 가능. 만약 향후 inner commit 이 추가되면 lock 모델 재검토 필요.
- **next 할 일은 B2** (Phase 5b UI 후속). B2 머지 후 Phase 6 (Discord 알림) 진입.
```

`27e8b56` 의 자리는 base commit SHA 로 자동 채울 것 — 실제로는 `git rev-parse main` 결과 (PR 생성 직전 main HEAD).

- [ ] **Step 4: handoff commit + push + PR**

```bash
cd /Users/arden/Documents/ardensdevspace/pslog
git add handoffs/main.md docs/superpowers/plans/2026-05-01-phase-5-followup-b1-quality.md
git commit -m "docs(handoff+plan): Phase 5 follow-up B1 완료 + B2/Phase 6 다음 할 일"

git push -u origin feature/phase-5-followup-b1-quality

gh pr create \
  --title "fix(b1): Phase 5 follow-up — race fixes (I-2/I-4) + last_synced_commit_sha (M-6) + underscore promote (M-10)" \
  --body "$(cat <<'EOF'
## Summary

Phase 5a/5b code review 에서 트래킹된 backend quality 후속 4건을 한 PR 로 닫음 — Phase 6 (Discord 알림) 진입 전 안전망.

- **I-2** (`register_webhook` race): `db.refresh(project, with_for_update=...)` 로 OWNER 동시 호출 직렬화. 기존엔 둘 다 'no matching hook' 분기 → GitHub 측 hook 2개, DB secret mismatch.
- **I-4 layer 1** (`reprocess` endpoint): `processed_at IS NULL` event 거부 (409). 사용자가 webhook 직후 BackgroundTask 가 끝나기 전 클릭하는 가장 흔한 race 시나리오 차단.
- **I-4 layer 2** (`process_event` service): entry 에 `db.refresh(event, with_for_update=...)` — DB-level 직렬화. 결정적 race 테스트로 검증.
- **M-6** (`last_synced_commit_sha` write): success path 에서 `project.last_synced_commit_sha = event.head_commit_sha`. Phase 1 부터 미사용이던 컬럼 — `commits_truncated` Compare API base fallback 정확화.
- **M-10** (underscore import 위배): `auth_headers / parse_repo / raise_for_status` public promote (`github_hook_service` 의 cross-module underscore-import 해소).

## Test plan

- [x] backend 176 tests pass (170 baseline + 6 신규)
- [x] race 테스트 2건은 `asyncio.Event` + slow fetcher 로 결정적 직렬화 검증 (fix 없으면 FAIL 보장)
- [x] ruff lint 신규 위배 없음
- [ ] e2e webhook 동작은 dev 환경에서 사용자 직접 (수신 endpoint 변경 없음 — race fix 만이라 영향 최소)

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Self-Review Pass

**1. Spec coverage** — 각 fix 의 origin (Phase 5a/5b code review note) 대응:
- I-2 (Task 4) ✓
- I-4 (Task 3A + 3B) ✓
- M-6 (Task 2) ✓
- M-10 (Task 1) ✓

**2. Placeholder scan** — 모든 step 에 실제 코드/명령. "TBD" / "implement later" 없음. handoff Step 3 의 `<sha>` 만 의도된 자리표시 (commit 후 채움).

**3. Type / signature consistency**:
- `auth_headers / parse_repo / raise_for_status` 의 시그니처는 underscore 만 떼고 동일.
- `db.refresh(obj, with_for_update={"nowait": False})` 시그니처는 SQLAlchemy 2.0 async 표준 — `process_event` 와 `register_webhook` 양쪽에서 동일 패턴.
- M-6 의 `project.last_synced_commit_sha = event.head_commit_sha` 는 기존 컬럼 (str | None) + str (40 hex) 호환.

**4. 의존 순서 (Task ordering)**:
- Task 1 (M-10) 은 동작 변경 없어 독립.
- Task 2 (M-6) 와 Task 3B (I-4 layer 2) 는 둘 다 process_event 본문 수정 — Task 2 가 먼저, 3B 는 Task 2 의 변경된 try block 위에 FOR UPDATE refresh 한 줄 추가. 순서 맞음.
- Task 3A 와 3B 는 분리 가능 (endpoint vs service) — 별도 commit 으로 트레이서빌리티 확보.
- Task 4 (I-2) 는 git_settings.py 의 register_webhook 만 — 독립.

문제 없음. 진행 가능.
