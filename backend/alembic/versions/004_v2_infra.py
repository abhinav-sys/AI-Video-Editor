"""job lease fields, partial status, item template_json

Revision ID: 004_v2_infra
Revises: 003_job_engine
Create Date: 2026-07-23 11:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "004_v2_infra"
down_revision: Union[str, None] = "003_job_engine"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("jobs") as batch:
        batch.add_column(sa.Column("started_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("worker_id", sa.String(length=128), nullable=True))

    with op.batch_alter_table("job_items") as batch:
        batch.add_column(sa.Column("template_json", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("job_items") as batch:
        batch.drop_column("template_json")
    with op.batch_alter_table("jobs") as batch:
        batch.drop_column("worker_id")
        batch.drop_column("heartbeat_at")
        batch.drop_column("started_at")
