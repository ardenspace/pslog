"""phase7 drift detection: drifts table + projects.decisions_path

Revision ID: b9c0409cfb53
Revises: c1d2e3f4a5b6
Create Date: 2026-06-14 09:28:50.795123

설계서: 2026-06-14-decision-truth-loop-design.md §5.2
주의: autogenerate 가 런타임 log_events_* 파티션/타입 변경 노이즈를 대량 포함해
      drifts 테이블 + projects.decisions_path 두 변경만 남기고 손으로 정리함.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'b9c0409cfb53'
down_revision: Union[str, None] = 'c1d2e3f4a5b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    drift_type = postgresql.ENUM(
        "DECISION_NOT_PROMOTED", "STATUS_CONTRADICTION", name="drifttype"
    )
    drift_status = postgresql.ENUM(
        "OPEN", "RESOLVED", "IGNORED", name="driftstatus"
    )
    drift_type.create(op.get_bind(), checkfirst=True)
    drift_status.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "drifts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column(
            "type",
            postgresql.ENUM(
                "DECISION_NOT_PROMOTED", "STATUS_CONTRADICTION",
                name="drifttype", create_type=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            postgresql.ENUM(
                "OPEN", "RESOLVED", "IGNORED",
                name="driftstatus", create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("branch", sa.String(), nullable=False),
        sa.Column("external_id", sa.String(), nullable=True),
        sa.Column("dedup_key", sa.String(), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("opened_at", sa.DateTime(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.Column("last_seen_commit_sha", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "type", "dedup_key", name="uq_drift_dedup"),
    )

    op.add_column(
        "projects",
        sa.Column(
            "decisions_path", sa.String(),
            server_default="DECISIONS.md", nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("projects", "decisions_path")
    op.drop_table("drifts")
    op.execute("DROP TYPE IF EXISTS driftstatus")
    op.execute("DROP TYPE IF EXISTS drifttype")
