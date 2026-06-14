"""github_hook_service — GitHub Hooks API 단위 테스트.

설계서: 2026-04-26-ai-task-automation-design.md §5.3 (자동 webhook 등록), §9
"""

import json
from pathlib import Path

import httpx
import pytest

from app.services.github_hook_service import (
    create_hook,
    list_hooks,
    update_hook,
)


_REPO = "https://github.com/ardenspace/app-chak"
_HOOKS_FIXTURE = (
    Path(__file__).parent / "fixtures" / "github_hooks_payload.json"
).read_text()


async def test_list_hooks_returns_array(monkeypatch: pytest.MonkeyPatch):
    captured: dict[str, object] = {}

    async def fake_send(self, request: httpx.Request, **_kwargs):
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        return httpx.Response(status_code=200, json=json.loads(_HOOKS_FIXTURE))

    monkeypatch.setattr(httpx.AsyncClient, "send", fake_send)
    hooks = await list_hooks(_REPO, "ghp_abc")
    assert isinstance(hooks, list)
    assert len(hooks) == 1
    assert hooks[0]["id"] == 12345678
    assert "/repos/ardenspace/app-chak/hooks" in captured["url"]
    assert captured["headers"]["authorization"] == "token ghp_abc"


async def test_create_hook_posts_correct_body(monkeypatch: pytest.MonkeyPatch):
    captured: dict[str, object] = {}

    async def fake_send(self, request: httpx.Request, **_kwargs):
        captured["url"] = str(request.url)
        captured["method"] = request.method
        captured["body"] = request.content
        return httpx.Response(
            status_code=201,
            json={
                "id": 99999,
                "config": {"url": "https://forps.example.com/api/v1/webhooks/github"},
            },
        )

    monkeypatch.setattr(httpx.AsyncClient, "send", fake_send)
    hook = await create_hook(
        _REPO,
        "ghp_abc",
        callback_url="https://forps.example.com/api/v1/webhooks/github",
        secret="my-secret",
    )
    assert hook["id"] == 99999
    assert captured["method"] == "POST"
    assert "/repos/ardenspace/app-chak/hooks" in captured["url"]
    body = json.loads(captured["body"])
    assert body["name"] == "web"
    assert body["active"] is True
    assert body["events"] == ["push", "pull_request"]
    assert body["config"]["url"] == "https://forps.example.com/api/v1/webhooks/github"
    assert body["config"]["secret"] == "my-secret"
    assert body["config"]["content_type"] == "json"


async def test_update_hook_patches_secret(monkeypatch: pytest.MonkeyPatch):
    captured: dict[str, object] = {}

    async def fake_send(self, request: httpx.Request, **_kwargs):
        captured["url"] = str(request.url)
        captured["method"] = request.method
        captured["body"] = request.content
        return httpx.Response(
            status_code=200,
            json={"id": 12345678, "config": {"url": "https://forps.example.com/api/v1/webhooks/github"}},
        )

    monkeypatch.setattr(httpx.AsyncClient, "send", fake_send)
    hook = await update_hook(
        _REPO, "ghp_abc",
        hook_id=12345678,
        callback_url="https://forps.example.com/api/v1/webhooks/github",
        secret="rotated-secret",
    )
    assert hook["id"] == 12345678
    assert captured["method"] == "PATCH"
    assert "/repos/ardenspace/app-chak/hooks/12345678" in captured["url"]
    body = json.loads(captured["body"])
    assert body["config"]["secret"] == "rotated-secret"


async def test_create_hook_5xx_raises(monkeypatch: pytest.MonkeyPatch):
    async def fake_send(self, request: httpx.Request, **_kwargs):
        return httpx.Response(status_code=502, json={"message": "Bad Gateway"})

    monkeypatch.setattr(httpx.AsyncClient, "send", fake_send)
    with pytest.raises(httpx.HTTPStatusError):
        await create_hook(_REPO, "ghp_abc", callback_url="x", secret="y")
