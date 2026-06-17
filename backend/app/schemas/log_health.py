"""log-health API 의 Pydantic schemas.

설계서: 2026-04-26-error-log-design.md §7 (Health 표).
"""

from pydantic import BaseModel


class LogHealthResponse(BaseModel):
    """24h 윈도우 헬스 메트릭. cron 또는 사용자 GET 호출.

    unknown_sha_ratio = (version_sha == 'unknown' 이벤트 수) / (전체 이벤트 수)
    clock_drift_count = abs(received_at - emitted_at) > 1h 인 이벤트 수
    total_events = 24h 내 LogEvent 총 수

    v2 추가 예정: `dropped_count_total` (X-pslog-Dropped-Since-Last 헤더 저장 인프라 필요).
    """
    total_events_24h: int
    unknown_sha_count_24h: int
    unknown_sha_ratio_24h: float  # 0.0 ~ 1.0
    clock_drift_count_24h: int
    threshold_unknown_ratio: float = 0.05  # 설계서: > 5% 시 경고
