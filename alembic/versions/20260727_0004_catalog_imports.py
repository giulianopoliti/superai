"""add catalog imports

Revision ID: 20260727_0004
Revises: 20260725_0003
Create Date: 2026-07-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260727_0004"
down_revision: str | None = "20260725_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "catalog_imports",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column(
            "business_id",
            sa.String(length=64),
            sa.ForeignKey("businesses.id"),
            nullable=False,
        ),
        sa.Column("source_filename", sa.String(length=500), nullable=False),
        sa.Column("source_type", sa.String(length=64), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("imported_count", sa.Integer(), nullable=False),
        sa.Column("skipped_count", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("errors", postgresql.JSONB(), nullable=False),
        sa.Column("summary", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_catalog_imports_business_id", "catalog_imports", ["business_id"])
    op.create_index("ix_catalog_imports_status", "catalog_imports", ["status"])
    op.create_index(
        "ix_catalog_imports_business_created",
        "catalog_imports",
        ["business_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_catalog_imports_business_created", table_name="catalog_imports")
    op.drop_index("ix_catalog_imports_status", table_name="catalog_imports")
    op.drop_index("ix_catalog_imports_business_id", table_name="catalog_imports")
    op.drop_table("catalog_imports")
