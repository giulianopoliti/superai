"""Add hierarchical auxiliary categories, preserving imported families.

Revision ID: 20261009_0012
Revises: 20261009_0011
"""

import sqlalchemy as sa

from alembic import op

revision = "20261009_0012"
down_revision = "20261009_0011"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("catalog_vocabulary", sa.Column("parent_id", sa.String(64), nullable=True))
    op.create_unique_constraint(
        "uq_catalog_vocab_business_id", "catalog_vocabulary", ["business_id", "id"]
    )
    op.create_foreign_key(
        "fk_catalog_vocab_business_parent",
        "catalog_vocabulary",
        "catalog_vocabulary",
        ["business_id", "parent_id"],
        ["business_id", "id"],
    )
    op.add_column("catalog_interpretations", sa.Column("category", sa.String(255), nullable=True))
    op.add_column(
        "catalog_interpretations", sa.Column("subcategory", sa.String(255), nullable=True)
    )


def downgrade():
    op.drop_column("catalog_interpretations", "subcategory")
    op.drop_column("catalog_interpretations", "category")
    op.drop_constraint("fk_catalog_vocab_business_parent", "catalog_vocabulary", type_="foreignkey")
    op.drop_constraint("uq_catalog_vocab_business_id", "catalog_vocabulary", type_="unique")
    op.drop_column("catalog_vocabulary", "parent_id")
