"""initial schema

Revision ID: 001_initial
Revises:
Create Date: 2026-07-22 10:50:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "upload_batches",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "jobs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("status", sa.Enum("queued", "parsing", "running", "completed", "failed", "cancelled", name="jobstatus"), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("instructions_json", sa.Text(), nullable=True),
        sa.Column("upload_id", sa.String(length=36), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("zip_path", sa.String(length=512), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_jobs_status"), "jobs", ["status"], unique=False)
    op.create_index(op.f("ix_jobs_upload_id"), "jobs", ["upload_id"], unique=False)

    op.create_table(
        "assets",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("job_id", sa.String(length=36), nullable=True),
        sa.Column("upload_id", sa.String(length=36), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("path", sa.String(length=512), nullable=False),
        sa.Column("kind", sa.Enum("logo", "watermark", "other", "video", name="assetkind"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_assets_job_id"), "assets", ["job_id"], unique=False)
    op.create_index(op.f("ix_assets_upload_id"), "assets", ["upload_id"], unique=False)

    op.create_table(
        "job_items",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("job_id", sa.String(length=36), nullable=False),
        sa.Column("input_path", sa.String(length=512), nullable=False),
        sa.Column("output_path", sa.String(length=512), nullable=True),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("status", sa.Enum("pending", "running", "completed", "failed", "cancelled", name="itemstatus"), nullable=False),
        sa.Column("progress", sa.Float(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_job_items_job_id"), "job_items", ["job_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_job_items_job_id"), table_name="job_items")
    op.drop_table("job_items")
    op.drop_index(op.f("ix_assets_upload_id"), table_name="assets")
    op.drop_index(op.f("ix_assets_job_id"), table_name="assets")
    op.drop_table("assets")
    op.drop_index(op.f("ix_jobs_upload_id"), table_name="jobs")
    op.drop_index(op.f("ix_jobs_status"), table_name="jobs")
    op.drop_table("jobs")
    op.drop_table("upload_batches")
