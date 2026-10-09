"""add product families

Revision ID: 20260813_0009
Revises: 20260731_0008
Create Date: 2026-08-13
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260813_0009"
down_revision: str | None = "20260731_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "product_families",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column(
            "business_id",
            sa.String(length=64),
            sa.ForeignKey("businesses.id"),
            nullable=False,
        ),
        sa.Column("external_family_id", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("normalized_name", sa.String(length=255), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "business_id",
            "external_family_id",
            name="uq_product_families_business_external",
        ),
    )
    op.create_index(
        "ix_product_families_business_id",
        "product_families",
        ["business_id"],
    )
    op.create_index(
        "ix_product_families_business_normalized_name",
        "product_families",
        ["business_id", "normalized_name"],
    )

    op.add_column("products", sa.Column("family_id", sa.String(length=64), nullable=True))
    op.add_column(
        "products",
        sa.Column("external_family_id", sa.String(length=128), nullable=True),
    )
    op.create_foreign_key(
        "products_family_id_fkey",
        "products",
        "product_families",
        ["family_id"],
        ["id"],
    )
    op.create_index("ix_products_family_id", "products", ["family_id"])


def downgrade() -> None:
    op.drop_index("ix_products_family_id", table_name="products")
    op.drop_constraint("products_family_id_fkey", "products", type_="foreignkey")
    op.drop_column("products", "external_family_id")
    op.drop_column("products", "family_id")
    op.drop_index(
        "ix_product_families_business_normalized_name",
        table_name="product_families",
    )
    op.drop_index("ix_product_families_business_id", table_name="product_families")
    op.drop_table("product_families")
