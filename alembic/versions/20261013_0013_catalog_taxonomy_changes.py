"""Audit automatic auxiliary taxonomy changes.

Revision ID: 20261013_0013
Revises: 20261009_0012
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "20261013_0013"
down_revision = "20261009_0012"
branch_labels = None
depends_on = None


def upgrade():
    data = sa.JSON().with_variant(JSONB, "postgresql")
    op.create_table(
        "catalog_taxonomy_changes",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("business_id", sa.String(64), sa.ForeignKey("businesses.id"), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("operations", data, nullable=False),
        sa.Column("evidence", data, nullable=False),
        sa.Column("before", data, nullable=False),
        sa.Column("after", data, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_catalog_taxonomy_changes_business_id", "catalog_taxonomy_changes", ["business_id"]
    )


def downgrade():
    op.drop_table("catalog_taxonomy_changes")
