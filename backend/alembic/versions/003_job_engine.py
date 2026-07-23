"""add job engine column for bulkcut vs creatomate

Revision ID: 003_job_engine
Revises: 002_item_previews
Create Date: 2026-07-22 16:10:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "003_job_engine"
down_revision: Union[str, None] = "002_item_previews"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("jobs") as batch:
        batch.add_column(
            sa.Column(
                "engine",
                sa.String(length=32),
                nullable=False,
                server_default="bulkcut",
            )
        )
        batch.create_index("ix_jobs_engine", ["engine"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("jobs") as batch:
        batch.drop_index("ix_jobs_engine")
        batch.drop_column("engine")
