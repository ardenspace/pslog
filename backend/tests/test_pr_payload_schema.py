"""GitHubPullRequestPayload 파싱 단위 테스트."""

from app.schemas.webhook import GitHubPullRequestPayload

RAW = (
    '{"action":"opened",'
    '"repository":{"id":1,"full_name":"o/r","html_url":"https://github.com/o/r"},'
    '"pull_request":{"number":12,'
    '"head":{"ref":"feat/x","sha":"%s"},'
    '"base":{"ref":"main","sha":"%s"}}}'
) % ("a" * 40, "b" * 40)


def test_pr_payload_parses():
    p = GitHubPullRequestPayload.model_validate_json(RAW)
    assert p.action == "opened"
    assert p.repository.html_url == "https://github.com/o/r"
    assert p.pull_request.number == 12
    assert p.pull_request.head.ref == "feat/x"
    assert p.pull_request.head.sha == "a" * 40
    assert p.pull_request.base.ref == "main"
    assert p.pull_request.base.sha == "b" * 40
