import enum
import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class DriftType(str, enum.Enum):
    DECISION_NOT_PROMOTED = "decision_not_promoted"   # A
    STATUS_CONTRADICTION = "status_contradiction"     # B
    TASK_NOT_PREPARED = "task_not_prepared"           # C


class DriftStatus(str, enum.Enum):
    OPEN = "open"
    RESOLVED = "resolved"
    IGNORED = "ignored"


class Drift(Base):
    """PLAN/handoff/DECISIONS 정합성 위반 1건.

    설계서: 2026-06-14-decision-truth-loop-design.md §5.2
    멱등 키: UNIQUE(project_id, type, dedup_key). dedup_key = branch 또는 branch:external_id.
    """

    __tablename__ = "drifts"
    __table_args__ = (
        UniqueConstraint("project_id", "type", "dedup_key", name="uq_drift_dedup"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))

    type: Mapped[DriftType]
    status: Mapped[DriftStatus] = mapped_column(default=DriftStatus.OPEN)

    branch: Mapped[str]
    external_id: Mapped[str | None] = mapped_column(default=None)  # task-NNN (B), nullable
    dedup_key: Mapped[str]   # "branch" (A) 또는 "branch:task-NNN" (B)

    detail: Mapped[str] = mapped_column(Text)   # 사람용 설명 + 고칠 힌트

    opened_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(default=None)
    last_seen_commit_sha: Mapped[str | None] = mapped_column(default=None)

    def __init__(self, **kwargs: object) -> None:
        kwargs.setdefault("status", DriftStatus.OPEN)
        super().__init__(**kwargs)
