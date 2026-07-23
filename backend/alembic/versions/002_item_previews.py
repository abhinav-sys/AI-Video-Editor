"""job item preview fields

Revision ID: 002_item_previews
Revises: 001_initial
Create Date: 2026-07-22 15:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "002_item_previews"
down_revision: Union[str, None] = "001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("job_items", sa.Column("occurrences_replaced", sa.Integer(), nullable=True))
    op.add_column("job_items", sa.Column("preview_before_path", sa.String(length=512), nullable=True))
    op.add_column("job_items", sa.Column("preview_after_path", sa.String(length=512), nullable=True))


def downgrade() -> None:
    op.drop_column("job_items", "preview_after_path")
    op.drop_column("job_items", "preview_before_path")
    op.drop_column("job_items", "occurrences_replaced")
