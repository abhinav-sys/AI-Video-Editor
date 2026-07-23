"""jobstatus enum: add 'partial' (Postgres native enum)

Revision ID: 005_job_status_partial
Revises: 004_v2_infra
Create Date: 2026-07-23 12:00:00.000000
"""

from typing import Sequence, Union

from alembic import op

revision: str = "005_job_status_partial"
down_revision: Union[str, None] = "004_v2_infra"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    # IF NOT EXISTS keeps re-runs / partial applies idempotent on PG 9.1+
    op.execute("ALTER TYPE jobstatus ADD VALUE IF NOT EXISTS 'partial'")


def downgrade() -> None:
    # Postgres cannot remove enum values safely; leave no-op.
    pass
