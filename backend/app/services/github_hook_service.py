"""GitHub Hooks API 클라이언트.

설계서: 2026-04-26-ai-task-automation-design.md §5.3 (ProjectGitSettings 자동 webhook 등록), §9

GitHub API:
  GET    /repos/{owner}/{repo}/hooks
  POST   /repos/{owner}/{repo}/hooks
  PATCH  /repos/{owner}/{repo}/hooks/{hook_id}

PAT 인증 필수 — admin:repo_hook 권한 요구.
"""

from typing import Any

import httpx

from app.services.git_repo_service import auth_headers, parse_repo, raise_for_status


_GITHUB_API = "https://api.github.com"


async def list_hooks(
    repo_url: str,
    pat: str,
    *,
    timeout: float = 30.0,
) -> list[dict[str, Any]]:
    """GET /repos/{owner}/{repo}/hooks → hooks 배열."""
    owner, repo = parse_repo(repo_url)
    url = f"{_GITHUB_API}/repos/{owner}/{repo}/hooks"
    request = httpx.Request("GET", url, headers=auth_headers(pat))
    async with httpx.AsyncClient(timeout=timeout) as client:
        res = await client.send(request)
    raise_for_status(res, request)
    return res.json()


async def create_hook(
    repo_url: str,
    pat: str,
    *,
    callback_url: str,
    secret: str,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """POST /repos/{owner}/{repo}/hooks — push 이벤트 webhook 생성."""
    owner, repo = parse_repo(repo_url)
    url = f"{_GITHUB_API}/repos/{owner}/{repo}/hooks"
    body = {
        "name": "web",
        "active": True,
        "events": ["push", "pull_request"],
        "config": {
            "url": callback_url,
            "content_type": "json",
            "secret": secret,
            "insecure_ssl": "0",
        },
    }
    request = httpx.Request("POST", url, headers=auth_headers(pat), json=body)
    async with httpx.AsyncClient(timeout=timeout) as client:
        res = await client.send(request)
    raise_for_status(res, request)
    return res.json()


async def update_hook(
    repo_url: str,
    pat: str,
    *,
    hook_id: int,
    callback_url: str,
    secret: str,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """PATCH /repos/{owner}/{repo}/hooks/{hook_id} — config.secret 갱신."""
    owner, repo = parse_repo(repo_url)
    url = f"{_GITHUB_API}/repos/{owner}/{repo}/hooks/{hook_id}"
    body = {
        "active": True,
        "events": ["push", "pull_request"],
        "config": {
            "url": callback_url,
            "content_type": "json",
            "secret": secret,
            "insecure_ssl": "0",
        },
    }
    request = httpx.Request("PATCH", url, headers=auth_headers(pat), json=body)
    async with httpx.AsyncClient(timeout=timeout) as client:
        res = await client.send(request)
    raise_for_status(res, request)
    return res.json()
