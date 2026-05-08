"""add_handoff_skip_branches

Revision ID: c1d2e3f4a5b6
Revises: e1f2a3b4c5d6
Create Date: 2026-05-08 13:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c1d2e3f4a5b6'
down_revision: Union[str, None] = 'e1f2a3b4c5d6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # handoff 누락 알림 스킵 브랜치 — 쉼표/줄바꿈 split, default '' (main 은 코드 레벨 하드코드 스킵)
    op.add_column('projects', sa.Column(
        'handoff_skip_branches', sa.Text(),
        nullable=False, server_default='',
    ))


def downgrade() -> None:
    op.drop_column('projects', 'handoff_skip_branches')
