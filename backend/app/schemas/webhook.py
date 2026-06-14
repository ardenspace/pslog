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


class GitHubPullRequestRef(BaseModel):
    """pull_request.head / pull_request.base — ref(브랜치명) + sha."""

    model_config = ConfigDict(extra="ignore")

    ref: str
    sha: str


class GitHubPullRequestBody(BaseModel):
    model_config = ConfigDict(extra="ignore")

    number: int
    head: GitHubPullRequestRef
    base: GitHubPullRequestRef


class GitHubPullRequestPayload(BaseModel):
    """GitHub "pull_request" 이벤트 — 결정 미승격(A) 평가에 필요한 필드만.

    설계서: 2026-06-14-decision-truth-loop-design.md §5.3 (Q1: PR 웹훅).
    """

    model_config = ConfigDict(extra="ignore")

    action: str
    repository: GitHubRepository
    pull_request: GitHubPullRequestBody
