# Phase 2 — Webhook 수신 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** GitHub push webhook 수신 endpoint 구축 — 프로젝트별 Fernet 암호화 secret으로 서명 검증, GitPushEvent INSERT만 (멱등성), 부팅 시 미처리 이벤트 회수 reaper. 실제 처리 로직(파싱·sync)은 Phase 4.

**Architecture:** Fernet 마스터 키(`PSLOG_FERNET_KEY`)로 프로젝트별 webhook secret 암복호화 → `X-Hub-Signature-256` HMAC 검증 → `GitPushEvent` raw 보존(UNIQUE `project_id+head_commit_sha` 멱등). FastAPI lifespan에 reaper task hook — `processed_at IS NULL AND received_at < now() - 5min` 회수. Process callback은 pluggable, Phase 2에는 no-op stub만 wired.

**Tech Stack:** FastAPI 0.109, SQLAlchemy 2.0.25 async, asyncpg, `cryptography` (Fernet), pytest 8.3 + testcontainers PG 16.

**선행 조건:**
- pslog main, alembic head = `c4dee7f06004` (Phase 1 완료, PR #7 머지)
- Phase 1 모델 그대로 사용: `Project.webhook_secret_encrypted`, `Project.git_repo_url`, `GitPushEvent` 6개 필드
- Python 3.12.13 venv (homebrew python@3.12), `requirements.txt` 핀 유지

**중요한 계약:**
- `head_commit_sha` 40자 hex full (Phase 1 CHECK 제약). webhook payload `head_commit.id` 그대로 받음.
- `commits_truncated = (len(payload.commits) == 20)` — GitHub webhook은 commits 최대 20개. Compare API 호출은 Phase 4 책임.
- 응답 정책 (설계서 §7.1, §8):
  - 서명 검증 실패 → **401**
  - 알 수 없는 repo (Project 매칭 실패) → **200 + warning 로그** (GitHub 재전송 방지)
  - DB 쓰기 실패 → **500** (GitHub 자동 재시도)
  - 중복 commit_sha → **200** (UNIQUE 충돌 silent skip, 멱등성)
- Project lookup key: `payload.repository.html_url` 또는 `clone_url` 와 `Project.git_repo_url` 매칭 (https URL 정규화 필요)
- Reaper: Phase 2 동안 process callback은 no-op (logging만). Phase 4 sync_service.process_event 가 inject됨.

---

## File Structure

**신규 파일 (소스):**
- `backend/app/core/crypto.py` — Fernet master key 로드 + `encrypt_secret`, `decrypt_secret`, `generate_webhook_secret`
- `backend/app/services/github_webhook_service.py` — `verify_signature`, `find_project_by_repo_url`, `record_push_event`
- `backend/app/services/push_event_reaper.py` — `reap_pending_events`, `start_reaper_loop`
- `backend/app/schemas/webhook.py` — Pydantic GitHub push payload
- `backend/app/api/v1/endpoints/discord.py` — 기존 discord-summary endpoint 이전 (webhooks.py 분리)

**신규 파일 (테스트):**
- `backend/tests/test_crypto.py`
- `backend/tests/test_webhook_schema.py`
- `backend/tests/test_github_webhook_service.py`
- `backend/tests/test_webhook_endpoint.py`
- `backend/tests/test_push_event_reaper.py`
- `backend/tests/fixtures/github_push_payload.json` — 실제 GitHub 형식 fixture

**수정 파일:**
- `backend/requirements.txt` — `cryptography==44.0.0` 추가 (Fernet)
- `backend/app/config.py` — `pslog_fernet_key: str` 필드 추가
- `backend/app/api/v1/endpoints/webhooks.py` — discord 코드 제거, `POST /webhooks/github` 만 남김 (prefix `/webhooks`)
- `backend/app/api/v1/router.py` — discord_router 추가
- `backend/app/main.py` — lifespan에 reaper task

---

## Self-Review Notes

작성 후 한 번 self-review:
- 설계서 §5.2 endpoint 목록 → Phase 2 범위는 `POST /api/v1/webhooks/github` 만 (Task 6 커버)
- 설계서 §8 webhook 에러 케이스 4개 (서명/unknown repo/DB 쓰기/commits 길이 20) → Task 5, 6에 매핑
- 설계서 §9 보안: Fernet 마스터 키 + 프로젝트별 secret + HMAC → Task 0, 1, 3 매핑
- 설계서 §10.2 webhook_service 테스트 (signature 격리) → Task 3 + Task 9
- 설계서 §10.4 Reaper 테스트 (5분 컷오프, 크래시 회복) → Task 7 + Task 9
- handoff 2026-04-28 메모: enum 대문자 NAME, `mapped_column(default=)` Python init 미적용 → 본 phase에는 신규 enum 없음, GitPushEvent INSERT 시 모든 필드 명시 채움

---

## Task 0: Fernet 의존성 + 설정

**Files:**
- Modify: `backend/requirements.txt`
- Modify: `backend/app/config.py`

- [ ] **Step 1: 의존성 추가 (failing — 아직 핀 추가 전)**

`backend/requirements.txt` 끝에 추가:

```
# Crypto (Phase 2 — webhook secret 암복호화)
cryptography==44.0.0
```

- [ ] **Step 2: pip 설치**

```bash
cd /Users/arden/Documents/ardensdevspace/pslog/backend
source venv/bin/activate
pip install -r requirements-dev.txt
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Expected: 44자 base64 키 출력 (예: `Yt9...=`)

- [ ] **Step 3: config 필드 추가 (failing test 먼저)**

Create `backend/tests/test_crypto.py`:

```python
"""Phase 2 — Fernet 마스터 키 + per-project secret 암복호화 테스트."""

import os

import pytest
from cryptography.fernet import Fernet

from app.core.crypto import (
    decrypt_secret,
    encrypt_secret,
    generate_webhook_secret,
)


def _set_master_key(monkeypatch: pytest.MonkeyPatch) -> str:
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("PSLOG_FERNET_KEY", key)
    # config singleton reload — settings reads env at instantiation
    import importlib
    import app.config
    importlib.reload(app.config)
    return key


def test_encrypt_decrypt_round_trip(monkeypatch: pytest.MonkeyPatch):
    _set_master_key(monkeypatch)
    plaintext = "super-secret-webhook-token-32-bytes!"
    blob = encrypt_secret(plaintext)
    assert isinstance(blob, bytes)
    assert blob != plaintext.encode()
    assert decrypt_secret(blob) == plaintext


def test_decrypt_with_wrong_master_key_raises(monkeypatch: pytest.MonkeyPatch):
    _set_master_key(monkeypatch)
    blob = encrypt_secret("hello")

    # rotate to a different master key
    monkeypatch.setenv("PSLOG_FERNET_KEY", Fernet.generate_key().decode())
    import importlib
    import app.config
    importlib.reload(app.config)
    import app.core.crypto
    importlib.reload(app.core.crypto)

    from app.core.crypto import decrypt_secret as decrypt2
    from cryptography.fernet import InvalidToken
    with pytest.raises(InvalidToken):
        decrypt2(blob)


def test_generate_webhook_secret_length(monkeypatch: pytest.MonkeyPatch):
    _set_master_key(monkeypatch)
    s = generate_webhook_secret()
    # token_urlsafe(32) → 43자 url-safe base64
    assert isinstance(s, str)
    assert len(s) >= 43
```

- [ ] **Step 4: Run test — 실패 확인**

```bash
cd /Users/arden/Documents/ardensdevspace/pslog/backend
pytest tests/test_crypto.py -v
```

Expected: ImportError (`app.core.crypto` 모듈 없음)

- [ ] **Step 5: config.py 에 pslog_fernet_key 필드 추가**

`backend/app/config.py` 의 `Settings` 클래스에 한 줄 추가 (existing 필드 사이):

```python
    # Crypto (Phase 2 — Fernet 마스터 키)
    pslog_fernet_key: str
```

`.env` 파일이 없으면 임시로 생성:
```bash
cd /Users/arden/Documents/ardensdevspace/pslog/backend
python -c "from cryptography.fernet import Fernet; print('PSLOG_FERNET_KEY=' + Fernet.generate_key().decode())" >> .env
```

- [ ] **Step 6: Commit**

```bash
git add backend/requirements.txt backend/app/config.py
git commit -m "feat(phase2): cryptography 의존성 + PSLOG_FERNET_KEY 설정"
```

---

## Task 1: crypto 모듈 (Fernet encrypt/decrypt/generate)

**Files:**
- Create: `backend/app/core/crypto.py`
- Test: `backend/tests/test_crypto.py` (Task 0에서 작성)

- [ ] **Step 1: crypto 모듈 작성**

Create `backend/app/core/crypto.py`:

```python
"""Fernet 마스터 키 기반 secret 암복호화.

설계서: 2026-04-26-ai-task-automation-design.md §9
- 마스터 키: PSLOG_FERNET_KEY 환경변수 (32-byte url-safe base64)
- 프로젝트별 webhook secret을 이 마스터 키로 암호화하여 Project.webhook_secret_encrypted 에 저장
- 키 회전 절차는 별도 운영 문서
"""

import secrets

from cryptography.fernet import Fernet

from app.config import settings


def _fernet() -> Fernet:
    """매번 새 Fernet 인스턴스 — settings 변경(테스트 reload) 반영."""
    return Fernet(settings.pslog_fernet_key.encode())


def encrypt_secret(plaintext: str) -> bytes:
    """평문 secret → Fernet 암호문 bytes."""
    return _fernet().encrypt(plaintext.encode())


def decrypt_secret(token: bytes) -> str:
    """Fernet 암호문 → 평문 string. 잘못된 키/변조 시 InvalidToken raise."""
    return _fernet().decrypt(token).decode()


def generate_webhook_secret() -> str:
    """프로젝트별 webhook secret 생성 — 32-byte 랜덤 url-safe base64.

    GitHub webhook secret 권장 길이(>=20 bytes) 충족.
    """
    return secrets.token_urlsafe(32)
```

- [ ] **Step 2: Run test — pass 확인**

```bash
cd /Users/arden/Documents/ardensdevspace/pslog/backend
pytest tests/test_crypto.py -v
```

Expected: 3 tests pass

- [ ] **Step 3: Commit**

```bash
git add backend/app/core/crypto.py backend/tests/test_crypto.py
git commit -m "feat(phase2): Fernet 기반 webhook secret 암복호화 모듈"
```

---

## Task 2: GitHub push payload 스키마

**Files:**
- Create: `backend/app/schemas/webhook.py`
- Create: `backend/tests/fixtures/github_push_payload.json`
- Create: `backend/tests/test_webhook_schema.py`

- [ ] **Step 1: GitHub push payload fixture 저장**

Create `backend/tests/fixtures/github_push_payload.json` — 실제 GitHub Webhooks doc의 축약 버전:

```json
{
  "ref": "refs/heads/feature/login-redesign",
  "before": "0000000000000000000000000000000000000000",
  "after": "abcdef0123456789abcdef0123456789abcdef01",
  "repository": {
    "id": 12345,
    "full_name": "ardenspace/app-chak",
    "html_url": "https://github.com/ardenspace/app-chak",
    "clone_url": "https://github.com/ardenspace/app-chak.git",
    "default_branch": "main"
  },
  "pusher": {
    "name": "alice",
    "email": "alice@example.com"
  },
  "head_commit": {
    "id": "abcdef0123456789abcdef0123456789abcdef01",
    "message": "feat: login form refactor",
    "timestamp": "2026-04-29T10:00:00+09:00",
    "author": {"name": "alice", "email": "alice@example.com"}
  },
  "commits": [
    {
      "id": "abcdef0123456789abcdef0123456789abcdef01",
      "message": "feat: login form refactor",
      "timestamp": "2026-04-29T10:00:00+09:00",
      "author": {"name": "alice", "email": "alice@example.com"},
      "added": [],
      "removed": [],
      "modified": ["frontend/Login.tsx", "handoffs/feature-login-redesign.md"]
    }
  ]
}
```

- [ ] **Step 2: Failing test 작성**

Create `backend/tests/test_webhook_schema.py`:

```python
"""GitHub push webhook payload Pydantic 스키마 테스트."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.schemas.webhook import GitHubPushPayload


FIXTURE = Path(__file__).parent / "fixtures" / "github_push_payload.json"


def test_parse_valid_payload():
    payload = GitHubPushPayload.model_validate(json.loads(FIXTURE.read_text()))
    assert payload.ref == "refs/heads/feature/login-redesign"
    assert payload.repository.html_url == "https://github.com/ardenspace/app-chak"
    assert payload.head_commit.id == "abcdef0123456789abcdef0123456789abcdef01"
    assert payload.pusher.name == "alice"
    assert len(payload.commits) == 1


def test_branch_property_strips_refs_heads():
    payload = GitHubPushPayload.model_validate(json.loads(FIXTURE.read_text()))
    assert payload.branch == "feature/login-redesign"


def test_head_commit_required():
    data = json.loads(FIXTURE.read_text())
    data.pop("head_commit")
    with pytest.raises(ValidationError):
        GitHubPushPayload.model_validate(data)


def test_commits_truncated_at_20():
    """webhook 은 commits 최대 20개. 본 테스트는 길이 검증 — truncated 플래그 자체는 service 단계."""
    data = json.loads(FIXTURE.read_text())
    one = data["commits"][0]
    data["commits"] = [one] * 25
    payload = GitHubPushPayload.model_validate(data)
    assert len(payload.commits) == 25  # schema는 길이 제한 안 함; service에서 commits_truncated 결정
```

- [ ] **Step 3: Run test — 실패 확인**

```bash
pytest tests/test_webhook_schema.py -v
```

Expected: ImportError

- [ ] **Step 4: 스키마 작성**

Create `backend/app/schemas/webhook.py`:

```python
"""GitHub push webhook payload Pydantic 스키마.

설계서: 2026-04-26-ai-task-automation-design.md §7.1
GitHub Webhooks "push" 이벤트 — 본 모듈은 phase 2 범위에 필요한 필드만 수신.
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class GitHubRepository(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int
    full_name: str
    html_url: str
    clone_url: str | None = None
    default_branch: str | None = None


class GitHubAuthor(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    email: str | None = None


class GitHubCommit(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    message: str
    timestamp: str
    author: GitHubAuthor
    added: list[str] = Field(default_factory=list)
    removed: list[str] = Field(default_factory=list)
    modified: list[str] = Field(default_factory=list)


class GitHubPusher(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    email: str | None = None


class GitHubHeadCommit(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    message: str
    timestamp: str


class GitHubPushPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    ref: str
    before: str
    after: str
    repository: GitHubRepository
    pusher: GitHubPusher
    head_commit: GitHubHeadCommit
    commits: list[GitHubCommit] = Field(default_factory=list)

    @property
    def branch(self) -> str:
        """`refs/heads/<branch>` → `<branch>`."""
        prefix = "refs/heads/"
        return self.ref[len(prefix):] if self.ref.startswith(prefix) else self.ref

    def to_commits_json(self) -> list[dict[str, Any]]:
        """GitPushEvent.commits 컬럼에 그대로 저장할 직렬화."""
        return [c.model_dump() for c in self.commits]
```

- [ ] **Step 5: Run test — pass**

```bash
pytest tests/test_webhook_schema.py -v
```

Expected: 4 tests pass

- [ ] **Step 6: Commit**

```bash
git add backend/app/schemas/webhook.py backend/tests/test_webhook_schema.py backend/tests/fixtures/
git commit -m "feat(phase2): GitHub push webhook payload Pydantic 스키마"
```

---

## Task 3: HMAC 서명 검증 함수 (단위)

**Files:**
- Create: `backend/app/services/github_webhook_service.py` (서명 검증 함수만 우선)
- Create: `backend/tests/test_github_webhook_service.py`

- [ ] **Step 1: Failing test 작성 (서명 검증만)**

Create `backend/tests/test_github_webhook_service.py`:

```python
"""github_webhook_service — 서명 검증 / repo 매칭 / GitPushEvent INSERT 단위 테스트.

설계서: 2026-04-26-ai-task-automation-design.md §10.2
"""

import hashlib
import hmac
import json
from pathlib import Path

import pytest

from app.services.github_webhook_service import verify_signature


FIXTURE = (Path(__file__).parent / "fixtures" / "github_push_payload.json").read_bytes()


def _sign(body: bytes, secret: str) -> str:
    """`X-Hub-Signature-256` 형식과 동일하게 HMAC-SHA256 생성."""
    mac = hmac.new(secret.encode(), body, hashlib.sha256)
    return f"sha256={mac.hexdigest()}"


def test_verify_signature_pass():
    secret = "the-shared-webhook-secret"
    sig = _sign(FIXTURE, secret)
    assert verify_signature(FIXTURE, sig, secret) is True


def test_verify_signature_fail_wrong_secret():
    sig = _sign(FIXTURE, "the-real-secret")
    assert verify_signature(FIXTURE, sig, "different-secret") is False


def test_verify_signature_fail_tampered_body():
    secret = "the-shared-webhook-secret"
    sig = _sign(FIXTURE, secret)
    tampered = FIXTURE + b"x"
    assert verify_signature(tampered, sig, secret) is False


def test_verify_signature_missing_prefix():
    """`sha256=` prefix 없으면 reject."""
    secret = "the-shared-webhook-secret"
    mac = hmac.new(secret.encode(), FIXTURE, hashlib.sha256).hexdigest()
    assert verify_signature(FIXTURE, mac, secret) is False  # prefix 빠짐


def test_verify_signature_empty_signature():
    assert verify_signature(FIXTURE, "", "secret") is False
    assert verify_signature(FIXTURE, None, "secret") is False  # type: ignore[arg-type]
```

- [ ] **Step 2: Run test — 실패 확인**

```bash
pytest tests/test_github_webhook_service.py::test_verify_signature_pass -v
```

Expected: ImportError

- [ ] **Step 3: 서명 검증 구현**

Create `backend/app/services/github_webhook_service.py`:

```python
"""GitHub webhook 수신 서비스.

설계서: 2026-04-26-ai-task-automation-design.md §5.1, §7.1
- `verify_signature`: X-Hub-Signature-256 HMAC-SHA256 검증 (constant-time compare)
- `find_project_by_repo_url`: payload.repository.html_url → Project lookup
- `record_push_event`: GitPushEvent INSERT (UNIQUE 충돌 silent skip)
"""

import hashlib
import hmac


def verify_signature(body: bytes, signature: str | None, secret: str) -> bool:
    """`X-Hub-Signature-256` HMAC-SHA256 검증. constant-time compare.

    GitHub 형식: `sha256=<hex>`. prefix 없거나 None이면 fail.
    """
    if not signature or not signature.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    received = signature[len("sha256="):]
    return hmac.compare_digest(expected, received)
```

- [ ] **Step 4: Run test — pass**

```bash
pytest tests/test_github_webhook_service.py -v
```

Expected: 5 tests pass

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/github_webhook_service.py backend/tests/test_github_webhook_service.py
git commit -m "feat(phase2): X-Hub-Signature-256 HMAC 검증 (constant-time)"
```

---

## Task 4: Project repo URL 매칭

**Files:**
- Modify: `backend/app/services/github_webhook_service.py`
- Modify: `backend/tests/test_github_webhook_service.py`

- [ ] **Step 1: Failing test 추가**

`backend/tests/test_github_webhook_service.py` 끝에 추가:

```python
import uuid
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.project import Project
from app.models.workspace import Workspace
from app.services.github_webhook_service import find_project_by_repo_url


async def _seed_workspace_with_project(
    db: AsyncSession, repo_url: str | None
) -> Project:
    ws = Workspace(name="ws", slug=f"ws-{uuid.uuid4().hex[:8]}")
    db.add(ws)
    await db.flush()
    proj = Project(workspace_id=ws.id, name="p", git_repo_url=repo_url)
    db.add(proj)
    await db.commit()
    await db.refresh(proj)
    return proj


async def test_find_project_exact_match(async_session: AsyncSession):
    proj = await _seed_workspace_with_project(
        async_session, "https://github.com/ardenspace/app-chak"
    )
    found = await find_project_by_repo_url(
        async_session, "https://github.com/ardenspace/app-chak"
    )
    assert found is not None
    assert found.id == proj.id


async def test_find_project_normalizes_trailing_slash_and_git_suffix(
    async_session: AsyncSession,
):
    """webhook payload 의 html_url vs clone_url 차이 흡수.

    Project 측에 `https://github.com/foo/bar` 만 들어있어도 webhook 의 `.../bar.git` 매칭.
    """
    proj = await _seed_workspace_with_project(
        async_session, "https://github.com/ardenspace/app-chak"
    )
    for variant in [
        "https://github.com/ardenspace/app-chak.git",
        "https://github.com/ardenspace/app-chak/",
        "HTTPS://GitHub.com/ardenspace/app-chak",
    ]:
        found = await find_project_by_repo_url(async_session, variant)
        assert found is not None and found.id == proj.id, f"variant {variant} mismatch"


async def test_find_project_unknown_repo_returns_none(async_session: AsyncSession):
    await _seed_workspace_with_project(async_session, "https://github.com/a/known")
    found = await find_project_by_repo_url(async_session, "https://github.com/x/y")
    assert found is None


async def test_find_project_skips_null_git_repo_url(async_session: AsyncSession):
    """git_repo_url이 None인 Project는 매칭 후보에서 제외."""
    await _seed_workspace_with_project(async_session, None)
    found = await find_project_by_repo_url(async_session, "https://github.com/x/y")
    assert found is None
```

- [ ] **Step 2: Run test — 실패 확인**

```bash
pytest tests/test_github_webhook_service.py::test_find_project_exact_match -v
```

Expected: ImportError (`find_project_by_repo_url` 없음)

- [ ] **Step 3: 구현 추가**

`backend/app/services/github_webhook_service.py` 끝에 추가:

```python
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.project import Project


def _normalize_repo_url(url: str) -> str:
    """`.git` suffix / trailing `/` / case 정규화 — html_url vs clone_url 흡수."""
    u = url.strip().lower()
    if u.endswith(".git"):
        u = u[:-4]
    if u.endswith("/"):
        u = u[:-1]
    return u


async def find_project_by_repo_url(
    db: AsyncSession, repo_url: str
) -> Project | None:
    """payload.repository.html_url 또는 clone_url → Project lookup.

    매칭 실패 시 None — 호출자(endpoint)는 200 + 경고 로그로 처리.
    """
    target = _normalize_repo_url(repo_url)
    # 정규화 후 비교: lower(rtrim('.git')+rtrim('/'))
    # PostgreSQL 함수 표현식보다 Python 측 정규화가 단순 — 후보 수 적음 가정.
    stmt = select(Project).where(Project.git_repo_url.is_not(None))
    rows = (await db.execute(stmt)).scalars().all()
    for proj in rows:
        if proj.git_repo_url and _normalize_repo_url(proj.git_repo_url) == target:
            return proj
    return None
```

- [ ] **Step 4: Run test — pass**

```bash
pytest tests/test_github_webhook_service.py -v
```

Expected: 9 tests pass (5 기존 + 4 신규)

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/github_webhook_service.py backend/tests/test_github_webhook_service.py
git commit -m "feat(phase2): Project repo URL lookup (html_url/clone_url 흡수)"
```

---

## Task 5: GitPushEvent INSERT (멱등성 + commits_truncated)

**Files:**
- Modify: `backend/app/services/github_webhook_service.py`
- Modify: `backend/tests/test_github_webhook_service.py`

- [ ] **Step 1: Failing test 추가**

`backend/tests/test_github_webhook_service.py` 끝에 추가:

```python
import json
from sqlalchemy import select

from app.models.git_push_event import GitPushEvent
from app.schemas.webhook import GitHubPushPayload
from app.services.github_webhook_service import record_push_event


def _payload(commits_count: int = 1, head_id: str | None = None) -> GitHubPushPayload:
    raw = json.loads(FIXTURE.decode())
    one = raw["commits"][0]
    raw["commits"] = [one] * commits_count
    if head_id is not None:
        raw["head_commit"]["id"] = head_id
        raw["after"] = head_id
    return GitHubPushPayload.model_validate(raw)


async def test_record_push_event_inserts_row(async_session: AsyncSession):
    proj = await _seed_workspace_with_project(
        async_session, "https://github.com/ardenspace/app-chak"
    )
    payload = _payload()
    event = await record_push_event(async_session, proj, payload)

    assert event is not None
    assert event.project_id == proj.id
    assert event.head_commit_sha == payload.head_commit.id
    assert event.branch == "feature/login-redesign"
    assert event.pusher == "alice"
    assert event.commits_truncated is False
    assert event.processed_at is None
    assert event.error is None
    assert event.commits is not None and len(event.commits) == 1


async def test_record_push_event_truncated_flag_at_20(async_session: AsyncSession):
    proj = await _seed_workspace_with_project(
        async_session, "https://github.com/ardenspace/app-chak"
    )
    payload = _payload(commits_count=20)
    event = await record_push_event(async_session, proj, payload)
    assert event.commits_truncated is True


async def test_record_push_event_idempotent_on_duplicate_sha(
    async_session: AsyncSession,
):
    """UNIQUE (project_id, head_commit_sha) 충돌 시 silent skip — 같은 객체 또는 None 반환."""
    proj = await _seed_workspace_with_project(
        async_session, "https://github.com/ardenspace/app-chak"
    )
    payload = _payload(head_id="a" * 40)

    first = await record_push_event(async_session, proj, payload)
    assert first is not None

    # 같은 head_commit_sha 로 두 번째 호출
    second = await record_push_event(async_session, proj, payload)
    # 새 row INSERT 안 됨
    rows = (
        await async_session.execute(
            select(GitPushEvent).where(GitPushEvent.project_id == proj.id)
        )
    ).scalars().all()
    assert len(rows) == 1
    # second는 기존 row 반환 또는 None — 둘 다 허용 (silent 의미)
    if second is not None:
        assert second.id == first.id
```

- [ ] **Step 2: Run test — 실패 확인**

```bash
pytest tests/test_github_webhook_service.py::test_record_push_event_inserts_row -v
```

Expected: ImportError (`record_push_event` 없음)

- [ ] **Step 3: 구현 추가**

`backend/app/services/github_webhook_service.py` 끝에 추가:

```python
from sqlalchemy.exc import IntegrityError

from app.models.git_push_event import GitPushEvent
from app.schemas.webhook import GitHubPushPayload


# GitHub Webhooks API 가 commits 배열을 최대 20개로 잘라서 전달.
# len == 20 이면 truncated 가능성 — Phase 4 sync_service 가 Compare API 로 보정.
GITHUB_WEBHOOK_COMMITS_CAP = 20


async def record_push_event(
    db: AsyncSession,
    project: Project,
    payload: GitHubPushPayload,
) -> GitPushEvent | None:
    """GitPushEvent INSERT. UNIQUE 충돌 시 None 반환 (멱등성).

    Phase 2 범위: raw 보존만. processed_at / error 는 Phase 4 sync_service 가 채움.
    """
    event = GitPushEvent(
        project_id=project.id,
        branch=payload.branch,
        head_commit_sha=payload.head_commit.id,
        commits=payload.to_commits_json(),
        commits_truncated=len(payload.commits) >= GITHUB_WEBHOOK_COMMITS_CAP,
        pusher=payload.pusher.name,
    )
    db.add(event)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        # UNIQUE (project_id, head_commit_sha) 충돌 → 기존 row 반환
        existing = (
            await db.execute(
                select(GitPushEvent).where(
                    GitPushEvent.project_id == project.id,
                    GitPushEvent.head_commit_sha == payload.head_commit.id,
                )
            )
        ).scalar_one_or_none()
        return existing
    await db.refresh(event)
    return event
```

- [ ] **Step 4: Run test — pass**

```bash
pytest tests/test_github_webhook_service.py -v
```

Expected: 12 tests pass (9 기존 + 3 신규)

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/github_webhook_service.py backend/tests/test_github_webhook_service.py
git commit -m "feat(phase2): GitPushEvent INSERT — 멱등성 + commits_truncated"
```

---

## Task 6: webhook endpoint + discord 분리

**Files:**
- Create: `backend/app/api/v1/endpoints/discord.py` (기존 코드 이전)
- Modify: `backend/app/api/v1/endpoints/webhooks.py` (GitHub 전용으로 교체)
- Modify: `backend/app/api/v1/router.py`
- Create: `backend/tests/test_webhook_endpoint.py`

- [ ] **Step 1: Failing endpoint 테스트 작성**

Create `backend/tests/test_webhook_endpoint.py`:

```python
"""POST /api/v1/webhooks/github e2e 테스트.

설계서: 2026-04-26-ai-task-automation-design.md §7.1, §8 (응답 정책)
- 401: 서명 검증 실패
- 200 + 경고 로그: 알 수 없는 repo (GitHub 재전송 방지)
- 200: 정상 + GitPushEvent INSERT
- 200: 중복 commit_sha (멱등성)
"""

import hashlib
import hmac
import json
import uuid
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import encrypt_secret
from app.models.git_push_event import GitPushEvent
from app.models.project import Project
from app.models.workspace import Workspace


FIXTURE = (Path(__file__).parent / "fixtures" / "github_push_payload.json").read_bytes()


@pytest.fixture()
async def client_with_db(async_session: AsyncSession, monkeypatch: pytest.MonkeyPatch):
    """PSLOG_FERNET_KEY + DB override 적용한 ASGI 클라이언트."""
    monkeypatch.setenv("PSLOG_FERNET_KEY", Fernet.generate_key().decode())
    import importlib
    import app.config
    importlib.reload(app.config)
    import app.core.crypto
    importlib.reload(app.core.crypto)

    from app.main import app
    from app.database import get_db

    async def override_get_db():
        yield async_session

    app.dependency_overrides[get_db] = override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()


async def _seed_project_with_secret(
    db: AsyncSession, repo_url: str, secret: str | None
) -> Project:
    ws = Workspace(name="ws", slug=f"ws-{uuid.uuid4().hex[:8]}")
    db.add(ws)
    await db.flush()
    proj = Project(
        workspace_id=ws.id,
        name="p",
        git_repo_url=repo_url,
        webhook_secret_encrypted=encrypt_secret(secret) if secret else None,
    )
    db.add(proj)
    await db.commit()
    await db.refresh(proj)
    return proj


def _sign(body: bytes, secret: str) -> str:
    mac = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={mac}"


async def test_webhook_valid_signature_returns_200(
    client_with_db, async_session: AsyncSession
):
    secret = "valid-secret"
    proj = await _seed_project_with_secret(
        async_session, "https://github.com/ardenspace/app-chak", secret
    )
    sig = _sign(FIXTURE, secret)
    res = await client_with_db.post(
        "/api/v1/webhooks/github",
        content=FIXTURE,
        headers={
            "X-Hub-Signature-256": sig,
            "X-GitHub-Event": "push",
            "Content-Type": "application/json",
        },
    )
    assert res.status_code == 200

    rows = (
        await async_session.execute(
            select(GitPushEvent).where(GitPushEvent.project_id == proj.id)
        )
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].head_commit_sha == json.loads(FIXTURE)["head_commit"]["id"]


async def test_webhook_invalid_signature_returns_401(
    client_with_db, async_session: AsyncSession
):
    proj = await _seed_project_with_secret(
        async_session, "https://github.com/ardenspace/app-chak", "real-secret"
    )
    bad_sig = _sign(FIXTURE, "wrong-secret")
    res = await client_with_db.post(
        "/api/v1/webhooks/github",
        content=FIXTURE,
        headers={"X-Hub-Signature-256": bad_sig, "X-GitHub-Event": "push"},
    )
    assert res.status_code == 401

    # body 미저장 — DB row 없어야
    rows = (
        await async_session.execute(
            select(GitPushEvent).where(GitPushEvent.project_id == proj.id)
        )
    ).scalars().all()
    assert len(rows) == 0


async def test_webhook_unknown_repo_returns_200(
    client_with_db, async_session: AsyncSession
):
    """알 수 없는 repo: 200 + 경고 로그 (GitHub 재전송 방지)."""
    # 다른 repo URL의 Project 만 있음
    await _seed_project_with_secret(
        async_session, "https://github.com/other/repo", "secret"
    )
    res = await client_with_db.post(
        "/api/v1/webhooks/github",
        content=FIXTURE,
        headers={"X-Hub-Signature-256": "sha256=anything", "X-GitHub-Event": "push"},
    )
    assert res.status_code == 200

    rows = (await async_session.execute(select(GitPushEvent))).scalars().all()
    assert len(rows) == 0


async def test_webhook_missing_signature_returns_401(
    client_with_db, async_session: AsyncSession
):
    await _seed_project_with_secret(
        async_session, "https://github.com/ardenspace/app-chak", "secret"
    )
    res = await client_with_db.post(
        "/api/v1/webhooks/github",
        content=FIXTURE,
        headers={"X-GitHub-Event": "push"},
    )
    assert res.status_code == 401


async def test_webhook_duplicate_push_idempotent(
    client_with_db, async_session: AsyncSession
):
    secret = "valid-secret"
    proj = await _seed_project_with_secret(
        async_session, "https://github.com/ardenspace/app-chak", secret
    )
    sig = _sign(FIXTURE, secret)
    headers = {"X-Hub-Signature-256": sig, "X-GitHub-Event": "push"}

    res1 = await client_with_db.post(
        "/api/v1/webhooks/github", content=FIXTURE, headers=headers
    )
    res2 = await client_with_db.post(
        "/api/v1/webhooks/github", content=FIXTURE, headers=headers
    )
    assert res1.status_code == 200
    assert res2.status_code == 200

    rows = (
        await async_session.execute(
            select(GitPushEvent).where(GitPushEvent.project_id == proj.id)
        )
    ).scalars().all()
    assert len(rows) == 1


async def test_webhook_project_without_secret_returns_401(
    client_with_db, async_session: AsyncSession
):
    """git_repo_url은 매칭되지만 webhook_secret_encrypted 가 NULL → 401."""
    await _seed_project_with_secret(
        async_session, "https://github.com/ardenspace/app-chak", secret=None
    )
    res = await client_with_db.post(
        "/api/v1/webhooks/github",
        content=FIXTURE,
        headers={"X-Hub-Signature-256": "sha256=x", "X-GitHub-Event": "push"},
    )
    assert res.status_code == 401
```

- [ ] **Step 2: Run test — 실패 확인**

```bash
pytest tests/test_webhook_endpoint.py -v
```

Expected: 404 (endpoint 없음) 또는 ImportError

- [ ] **Step 3: 기존 webhooks.py → discord.py 이전**

Create `backend/app/api/v1/endpoints/discord.py`:

```python
"""Discord 통합 endpoint — 주간 요약 webhook 발송.

(2026-04-29 phase 2 분리: GitHub webhook 수신과 책임 분리)
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import CurrentUser
from app.models.workspace import WorkspaceRole
from app.services import project_service
from app.services.discord_service import build_project_summary, send_webhook
from app.services.permission_service import get_effective_role

router = APIRouter(prefix="/projects", tags=["discord"])


@router.post("/{project_id}/discord-summary")
async def send_discord_summary(
    project_id: UUID,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
):
    """Discord로 주간 요약 리포트 전송 (Owner만)"""
    project = await project_service.get_project(db, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    role = await get_effective_role(db, user.id, project_id)
    if role != WorkspaceRole.OWNER:
        raise HTTPException(status_code=403, detail="Only owner can send Discord summary")

    if not project.discord_webhook_url:
        raise HTTPException(status_code=400, detail="Discord webhook URL이 설정되지 않았습니다. 사이드바에서 설정해주세요.")

    summary = await build_project_summary(project_id, db, sender_name=user.name)
    await send_webhook(summary, project.discord_webhook_url)

    return {"message": "Discord summary sent successfully"}
```

- [ ] **Step 4: webhooks.py 교체 (GitHub 전용)**

`backend/app/api/v1/endpoints/webhooks.py` 전체 교체:

```python
"""GitHub push webhook 수신 endpoint.

설계서: 2026-04-26-ai-task-automation-design.md §5.2, §7.1, §8
응답 정책:
  - 401: 서명 검증 실패 (또는 secret 없음 / signature 헤더 없음)
  - 200 + 경고 로그: 알 수 없는 repo (GitHub 재전송 방지)
  - 200: 정상 + GitPushEvent INSERT (중복 commit_sha 도 200, 멱등성)
  - 500: DB 쓰기 실패 (GitHub 자동 재시도)
"""

import logging

from cryptography.fernet import InvalidToken
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt_secret
from app.database import get_db
from app.schemas.webhook import GitHubPushPayload
from app.services.github_webhook_service import (
    find_project_by_repo_url,
    record_push_event,
    verify_signature,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/github")
async def receive_github_push(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_hub_signature_256: str | None = Header(default=None, alias="X-Hub-Signature-256"),
    x_github_event: str | None = Header(default=None, alias="X-GitHub-Event"),
):
    """GitHub push webhook 수신.

    흐름: body 읽기 → payload 파싱 → repo 매칭 → secret decrypt → 서명 검증 → INSERT.
    Phase 2 범위: raw 보존만. 파싱/sync는 Phase 4.
    """
    body = await request.body()

    # push 이벤트만 처리 — 다른 이벤트는 200 ACK + skip
    if x_github_event != "push":
        return {"status": "ignored", "event": x_github_event}

    try:
        payload = GitHubPushPayload.model_validate_json(body)
    except ValueError:
        # 깨진 payload — 400 (재전송 의미 없음)
        raise HTTPException(status_code=400, detail="Invalid push payload")

    project = await find_project_by_repo_url(db, payload.repository.html_url)
    if project is None:
        # 알 수 없는 repo: 200 + 경고 로그 (재전송 방지)
        logger.warning(
            "github webhook for unknown repo: %s", payload.repository.html_url
        )
        return {"status": "unknown_repo"}

    if project.webhook_secret_encrypted is None:
        # repo는 등록됐지만 secret 미설정 — 검증 불가, 401
        logger.warning("project %s has git_repo_url but no webhook secret", project.id)
        raise HTTPException(status_code=401, detail="Webhook secret not configured")

    try:
        secret = decrypt_secret(project.webhook_secret_encrypted)
    except InvalidToken:
        logger.error(
            "failed to decrypt webhook secret for project %s — Fernet master key mismatch",
            project.id,
        )
        raise HTTPException(status_code=500, detail="Secret decryption failed")

    if not verify_signature(body, x_hub_signature_256, secret):
        logger.warning(
            "github webhook signature verification failed for project %s", project.id
        )
        raise HTTPException(status_code=401, detail="Invalid signature")

    event = await record_push_event(db, project, payload)
    return {"status": "received", "event_id": str(event.id) if event else None}
```

- [ ] **Step 5: router 에 discord 추가**

`backend/app/api/v1/router.py` 수정:

```python
from fastapi import APIRouter

from app.api.v1.endpoints.auth import router as auth_router
from app.api.v1.endpoints.tasks import router as tasks_router
from app.api.v1.endpoints.workspaces import router as workspaces_router
from app.api.v1.endpoints.projects import router as projects_router
from app.api.v1.endpoints.share_links import router as share_links_router
from app.api.v1.endpoints.discord import router as discord_router
from app.api.v1.endpoints.webhooks import router as webhooks_router

api_v1_router = APIRouter()
api_v1_router.include_router(auth_router)
api_v1_router.include_router(tasks_router)
api_v1_router.include_router(workspaces_router)
api_v1_router.include_router(projects_router)
api_v1_router.include_router(share_links_router)
api_v1_router.include_router(discord_router)
api_v1_router.include_router(webhooks_router)
```

- [ ] **Step 6: Run test — pass**

```bash
pytest tests/test_webhook_endpoint.py -v
```

Expected: 6 tests pass

- [ ] **Step 7: 회귀 테스트 — 기존 모든 테스트 통과 확인**

```bash
pytest -v
```

Expected: 41 (Phase 1) + 신규 phase 2 테스트 모두 pass. discord-summary endpoint 라우팅도 그대로 작동 (URL 변동 없음).

- [ ] **Step 8: Commit**

```bash
git add backend/app/api/v1/endpoints/discord.py \
        backend/app/api/v1/endpoints/webhooks.py \
        backend/app/api/v1/router.py \
        backend/tests/test_webhook_endpoint.py
git commit -m "feat(phase2): POST /api/v1/webhooks/github + discord endpoint 분리"
```

---

## Task 7: push_event_reaper

**Files:**
- Create: `backend/app/services/push_event_reaper.py`
- Create: `backend/tests/test_push_event_reaper.py`

- [ ] **Step 1: Failing test 작성**

Create `backend/tests/test_push_event_reaper.py`:

```python
"""push_event_reaper — 부팅 시 미처리 GitPushEvent 회수.

설계서: 2026-04-26-ai-task-automation-design.md §5.1 (⑧), §7.1, §10.4
- `processed_at IS NULL AND received_at < now() - 5min` 인 이벤트 재처리
- Phase 2: process callback이 None이면 logging only (sync 로직은 Phase 4)
"""

import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.git_push_event import GitPushEvent
from app.models.project import Project
from app.models.workspace import Workspace
from app.services.push_event_reaper import REAPER_GRACE, reap_pending_events


async def _seed_project(db: AsyncSession) -> Project:
    ws = Workspace(name="ws", slug=f"ws-{uuid.uuid4().hex[:8]}")
    db.add(ws)
    await db.flush()
    proj = Project(workspace_id=ws.id, name="p")
    db.add(proj)
    await db.commit()
    await db.refresh(proj)
    return proj


async def _seed_event(
    db: AsyncSession,
    project: Project,
    *,
    received_at: datetime,
    processed_at: datetime | None = None,
    head_sha: str | None = None,
) -> GitPushEvent:
    event = GitPushEvent(
        project_id=project.id,
        branch="main",
        head_commit_sha=head_sha or ("a" * 40),
        commits=[],
        commits_truncated=False,
        pusher="alice",
        received_at=received_at,
        processed_at=processed_at,
    )
    db.add(event)
    await db.commit()
    await db.refresh(event)
    return event


async def test_reaper_finds_old_unprocessed_events(async_session: AsyncSession):
    proj = await _seed_project(async_session)
    old = await _seed_event(
        async_session,
        proj,
        received_at=datetime.utcnow() - timedelta(minutes=10),
        head_sha="a" * 40,
    )
    # 5분 미만 — 처리 중일 수 있음, skip
    fresh = await _seed_event(
        async_session,
        proj,
        received_at=datetime.utcnow() - timedelta(minutes=2),
        head_sha="b" * 40,
    )

    found_ids: list[uuid.UUID] = []

    async def callback(event: GitPushEvent) -> None:
        found_ids.append(event.id)

    count = await reap_pending_events(async_session, callback)
    assert count == 1
    assert found_ids == [old.id]


async def test_reaper_skips_processed_events(async_session: AsyncSession):
    proj = await _seed_project(async_session)
    await _seed_event(
        async_session,
        proj,
        received_at=datetime.utcnow() - timedelta(hours=1),
        processed_at=datetime.utcnow() - timedelta(minutes=30),
        head_sha="c" * 40,
    )

    callback_invocations = 0

    async def callback(event: GitPushEvent) -> None:
        nonlocal callback_invocations
        callback_invocations += 1

    count = await reap_pending_events(async_session, callback)
    assert count == 0
    assert callback_invocations == 0


async def test_reaper_with_no_callback_just_logs(
    async_session: AsyncSession, caplog: pytest.LogCaptureFixture
):
    """Phase 2: callback=None 이면 logging만 — Phase 4 에서 sync_service.process_event 주입."""
    import logging

    proj = await _seed_project(async_session)
    await _seed_event(
        async_session,
        proj,
        received_at=datetime.utcnow() - timedelta(minutes=15),
        head_sha="d" * 40,
    )

    with caplog.at_level(logging.INFO, logger="app.services.push_event_reaper"):
        count = await reap_pending_events(async_session, None)

    assert count == 1
    assert any("pending push event" in rec.message for rec in caplog.records)


async def test_reaper_grace_constant_is_5_minutes():
    assert REAPER_GRACE == timedelta(minutes=5)
```

- [ ] **Step 2: Run test — 실패 확인**

```bash
pytest tests/test_push_event_reaper.py -v
```

Expected: ImportError

- [ ] **Step 3: 구현**

Create `backend/app/services/push_event_reaper.py`:

```python
"""부팅 시 미처리 GitPushEvent 회수.

설계서: 2026-04-26-ai-task-automation-design.md §5.1 (⑧), §7.1
컨테이너 재시작/크래시로 BackgroundTask 가 실행되지 못한 이벤트 보존.

쿼리: processed_at IS NULL AND received_at < now() - 5min
   → 5분은 정상 처리 grace period (현재 처리 중인 BackgroundTask 와 충돌 회피)

Phase 2 범위: 쿼리 + callback 호출만. callback이 None 이면 logging only.
Phase 4 에서 sync_service.process_event 가 callback 으로 주입됨.
"""

import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal
from app.models.git_push_event import GitPushEvent

logger = logging.getLogger(__name__)


REAPER_GRACE = timedelta(minutes=5)


ProcessCallback = Callable[[GitPushEvent], Awaitable[None]]


async def reap_pending_events(
    db: AsyncSession,
    callback: ProcessCallback | None,
) -> int:
    """미처리 이벤트 조회 → callback 호출. 처리된 이벤트 수 반환.

    callback이 None 이면 로깅만 — Phase 2 placeholder.
    callback 안에서 예외 raise 시 다음 이벤트로 진행 (개별 격리).
    """
    cutoff = datetime.utcnow() - REAPER_GRACE
    stmt = (
        select(GitPushEvent)
        .where(GitPushEvent.processed_at.is_(None))
        .where(GitPushEvent.received_at < cutoff)
        .order_by(GitPushEvent.received_at)
    )
    rows = (await db.execute(stmt)).scalars().all()

    for event in rows:
        if callback is None:
            logger.info(
                "pending push event %s (project=%s, branch=%s, sha=%s) — Phase 2 stub",
                event.id,
                event.project_id,
                event.branch,
                event.head_commit_sha,
            )
            continue
        try:
            await callback(event)
        except Exception:
            logger.exception(
                "reaper callback failed for event %s — leaving processed_at NULL",
                event.id,
            )

    return len(rows)


async def run_reaper_once() -> int:
    """app startup 에서 호출 — 자체 세션 열고 reaper 1회 실행."""
    async with AsyncSessionLocal() as session:
        return await reap_pending_events(session, callback=None)
```

- [ ] **Step 4: Run test — pass**

```bash
pytest tests/test_push_event_reaper.py -v
```

Expected: 4 tests pass

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/push_event_reaper.py backend/tests/test_push_event_reaper.py
git commit -m "feat(phase2): push_event_reaper — 미처리 이벤트 회수 (5min grace)"
```

---

## Task 8: 부팅 hook 통합 (lifespan)

**Files:**
- Modify: `backend/app/main.py`

- [ ] **Step 1: 부팅 시 reaper 1회 호출 통합**

`backend/app/main.py` 의 `lifespan` 수정:

```python
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.api.v1.router import api_v1_router
from app.services.discord_service import start_weekly_scheduler
from app.services.push_event_reaper import run_reaper_once

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: 미처리 push event 회수 (Phase 2 — Phase 4에 sync 주입)
    try:
        reaped = await run_reaper_once()
        if reaped:
            logger.info("startup reaper picked up %d pending push events", reaped)
    except Exception:
        # 부팅을 막지 않음 — DB 미준비 등
        logger.exception("startup reaper failed")

    # Startup: 주간 리포트 스케줄러 시작
    scheduler_task = asyncio.create_task(start_weekly_scheduler())
    yield
    # Shutdown: 스케줄러 정리
    scheduler_task.cancel()


app = FastAPI(
    title="pslog API",
    description="B2B Task Management & Collaboration Tool",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins.split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
async def root():
    return {"message": "pslog API is running"}


@app.head("/health")
@app.get("/health")
async def health():
    return {"status": "ok"}


app.include_router(api_v1_router, prefix="/api/v1")
```

- [ ] **Step 2: 회귀 — 부팅 import 검증**

```bash
cd /Users/arden/Documents/ardensdevspace/pslog/backend
python -c "from app.main import app; print('startup import OK')"
```

Expected: `startup import OK`

- [ ] **Step 3: Commit**

```bash
git add backend/app/main.py
git commit -m "feat(phase2): startup hook — reaper 1회 호출 (DB 실패 시 부팅 진행)"
```

---

## Task 9: e2e + 회귀 테스트

**Files:**
- 모든 테스트 일괄 실행 — 신규 코드 없음

- [ ] **Step 1: 전체 테스트 스위트 실행**

```bash
cd /Users/arden/Documents/ardensdevspace/pslog/backend
pytest -v --tb=short
```

Expected: Phase 1 (41 tests) + Phase 2 신규 테스트 모두 pass

- [ ] **Step 2: 누락 점검 체크리스트**

설계서 §10.2 Webhook 테스트 항목 매핑 확인:
- [ ] signature 검증 실패 시 401 + body 미저장 → `test_webhook_invalid_signature_returns_401`
- [ ] 프로젝트별 secret 격리 → `test_webhook_valid_signature_returns_200` + `test_webhook_invalid_signature_returns_401` (다른 secret으로 검증 시 fail)

설계서 §10.4 Reaper 테스트 항목:
- [ ] 5분 컷오프 → `test_reaper_finds_old_unprocessed_events` (10분 vs 2분)
- [ ] 처리 완료 이벤트 skip → `test_reaper_skips_processed_events`
- [ ] callback 격리 → `test_reaper_with_no_callback_just_logs`

설계서 §8 응답 정책:
- [ ] 401 (서명 실패) → `test_webhook_invalid_signature_returns_401`, `test_webhook_missing_signature_returns_401`
- [ ] 200 + 경고 (unknown repo) → `test_webhook_unknown_repo_returns_200`
- [ ] 200 (정상) → `test_webhook_valid_signature_returns_200`
- [ ] commits_truncated 마킹 → `test_record_push_event_truncated_flag_at_20`
- [ ] 멱등성 → `test_webhook_duplicate_push_idempotent`

모든 항목 매핑됨.

- [ ] **Step 3: 부팅 e2e 수동 검증**

```bash
cd /Users/arden/Documents/ardensdevspace/pslog/backend
source venv/bin/activate
# .env 의 PSLOG_FERNET_KEY 가 설정돼 있어야
uvicorn app.main:app --host 127.0.0.1 --port 8000 &
SERVER_PID=$!
sleep 3
curl -s http://127.0.0.1:8000/health
echo
# webhook endpoint 가 마운트됐는지 확인 (signature 없으면 401 정상)
curl -s -o /dev/null -w "%{http_code}\n" -X POST \
  -H "X-GitHub-Event: push" \
  -H "Content-Type: application/json" \
  -d '{}' http://127.0.0.1:8000/api/v1/webhooks/github
# Expected: 400 (invalid payload) — endpoint 존재 + 404 아님
kill $SERVER_PID
```

Expected: `/health` → `{"status":"ok"}`, webhook endpoint → 400 (payload 깨짐, 라우팅 정상)

- [ ] **Step 4: 최종 commit (changelog 또는 handoff 업데이트)**

`handoffs/main.md` 상단에 Phase 2 완료 섹션 추가 (날짜는 작업일 기준 — 2026-04-29 또는 그 이후):

```markdown
## YYYY-MM-DD

- [x] **Phase 2 완료** — webhook 수신 endpoint + 서명 검증 + reaper
  - [x] Fernet 마스터 키 (`PSLOG_FERNET_KEY`) + crypto 모듈 (encrypt/decrypt/generate)
  - [x] GitHubPushPayload Pydantic 스키마 + branch property
  - [x] github_webhook_service: HMAC 검증 (constant-time) + repo URL 정규화 매칭 + GitPushEvent INSERT (멱등성)
  - [x] commits_truncated 플래그 (len >= 20)
  - [x] discord endpoint 분리 (webhooks.py 는 GitHub 전용으로 정리)
  - [x] push_event_reaper (5min grace) + main.py lifespan 통합
  - [x] N tests passing (Phase 1 41 + Phase 2 신규)

### 마지막 커밋
- pslog: `<sha> docs(handoff): Phase 2 완료` 

### 다음 (Phase 3 — 파서)
- [ ] plan_parser_service (PLAN.md → 태스크 목록, 정규식)
- [ ] handoff_parser_service (체크박스 + 자유 영역 분리)
- [ ] 들여쓰기 기반 서브 체크박스 분리

### 블로커
없음
```

```bash
git add handoffs/main.md
git commit -m "docs(handoff): Phase 2 완료 + Phase 3 다음 할 일"
```

- [ ] **Step 5: PR 생성 준비 (사용자 확인 후)**

PR 생성은 본 plan 범위 밖 — 사용자 검토 후 별도 진행.

---

## Phase 2 완료 기준 (Acceptance)

- [ ] `POST /api/v1/webhooks/github` 가 라우팅됨 (200/401/400 응답 정책 준수)
- [ ] `Project.webhook_secret_encrypted` 의 Fernet 암복호화 round-trip 동작
- [ ] `X-Hub-Signature-256` HMAC-SHA256 검증 (constant-time)
- [ ] 정상 webhook → `GitPushEvent` 1행 INSERT, `processed_at IS NULL`
- [ ] 같은 `head_commit_sha` 두 번 → 1행만 (UNIQUE 멱등성)
- [ ] `commits` 길이 == 20 → `commits_truncated = True`
- [ ] 알 수 없는 repo → 200 + WARNING 로그 (DB 미저장)
- [ ] 부팅 시 `processed_at IS NULL AND received_at < now() - 5min` 이벤트 발견 시 logging (Phase 4 에서 callback 주입)
- [ ] 기존 41 테스트 무회귀
- [ ] discord-summary endpoint URL (`/api/v1/projects/{id}/discord-summary`) 변동 없음
