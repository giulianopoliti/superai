"""background_analysis_jobs

Revision ID: 20261015_0017
Revises: 20261015_0016
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20261015_0017"
down_revision: str | Sequence[str] | None = "20261015_0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "background_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("business_id", sa.String(64), sa.ForeignKey("businesses.id"), nullable=False),
        sa.Column("created_by_user_id", sa.String(64), nullable=False),
        sa.Column("job_type", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("file_key", sa.String(36), nullable=False),
        sa.Column("file_sha256", sa.String(64), nullable=False),
        sa.Column("source_filename", sa.String(255), nullable=False),
        sa.Column("parameters", _json(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("phase", sa.String(32), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("attempt_token", sa.String(36), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("checkpoint", _json(), nullable=True),
        sa.Column("result", _json(), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("error_message", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "business_id",
            "created_by_user_id",
            "job_type",
            "idempotency_key",
            name="uq_background_jobs_idempotency",
        ),
        sa.ForeignKeyConstraint(
            ["business_id", "created_by_user_id"],
            ["users.business_id", "users.id"],
            name="fk_background_jobs_business_creator",
        ),
        sa.CheckConstraint(
            "status IN ('queued','running','succeeded','failed')", name="ck_background_jobs_status"
        ),
    )
    op.create_index("ix_background_jobs_acquire", "background_jobs", ["status", "created_at"])
    op.create_index("ix_background_jobs_lease", "background_jobs", ["status", "lease_expires_at"])
    op.create_index(
        "ix_background_jobs_owner",
        "background_jobs",
        ["business_id", "created_by_user_id", "created_at"],
    )
    if op.get_context().dialect.name == "postgresql":
        op.execute("ALTER TABLE public.background_jobs ENABLE ROW LEVEL SECURITY")
        op.execute("REVOKE ALL ON public.background_jobs FROM PUBLIC, anon, authenticated")


def downgrade() -> None:
    raise RuntimeError("Drain and back up all accepted jobs before a manual schema rollback.")


def _json():
    from sqlalchemy.dialects.postgresql import JSONB

    return sa.JSON().with_variant(JSONB, "postgresql")
