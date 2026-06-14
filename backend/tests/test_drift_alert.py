"""format_drift_alert — Discord 알림 문자열 생성 단위 테스트."""

from app.models.drift import Drift, DriftType
from app.services import drift_service


def test_format_drift_alert_empty_returns_none():
    assert drift_service.format_drift_alert([]) is None


def test_format_drift_alert_includes_branch_and_detail():
    d = Drift(
        type=DriftType.STATUS_CONTRADICTION, branch="feat/x",
        external_id="task-007", dedup_key="feat/x:task-007",
        detail="PLAN DONE인데 handoff 미완",
    )
    out = drift_service.format_drift_alert([d])
    assert out is not None
    assert "feat/x" in out
    assert "handoff 미완" in out
    assert "status_contradiction" in out
