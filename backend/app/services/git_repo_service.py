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


def parse_repo(repo_url: str) -> tuple[str, str]:
    """`https://github.com/owner/repo[.git][/]` → (owner, repo). 대소문자 보존."""
    m = _REPO_RE.match(repo_url.strip())
    if not m:
        raise ValueError(f"unsupported repo url: {repo_url!r}")
    return m.group("owner"), m.group("repo")


def auth_headers(pat: str | None) -> dict[str, str]:
    # GitHub API 는 모든 요청에 User-Agent 필수.
    # `httpx.Request(...) + client.send(request)` 사용 시 default User-Agent 자동 주입 안 됨 → 403.
    headers = {
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "pslog/0.1.0",
    }
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
