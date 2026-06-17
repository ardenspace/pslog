"""add task_not_prepared and tasks_dir

Revision ID: 78d23ecb3732
Revises: b9c0409cfb53
Create Date: 2026-06-17 21:00:12.860735

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '78d23ecb3732'
down_revision: Union[str, None] = 'b9c0409cfb53'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1) drifttype enum 에 값 추가 (대문자 NAME — 기존 DECISION_NOT_PROMOTED 와 동일 케이싱)
    op.execute("ALTER TYPE drifttype ADD VALUE IF NOT EXISTS 'TASK_NOT_PREPARED'")
    # 2) projects.tasks_dir
    op.add_column(
        "projects",
        sa.Column("tasks_dir", sa.String(), nullable=False, server_default="docs/tasks/"),
    )


def downgrade() -> None:
    op.drop_column("projects", "tasks_dir")
    # PostgreSQL enum 값 제거는 비파괴적으로 어려움 — 값은 그대로 둔다(관용).
