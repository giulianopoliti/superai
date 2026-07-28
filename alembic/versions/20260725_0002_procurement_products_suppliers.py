"""add procurement products and suppliers

Revision ID: 20260725_0002
Revises: 20260720_0001
Create Date: 2026-07-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260725_0002"
down_revision: str | None = "20260720_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "products",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column(
            "business_id",
            sa.String(length=64),
            sa.ForeignKey("businesses.id"),
            nullable=False,
        ),
        sa.Column("external_product_id", sa.String(length=128), nullable=True),
        sa.Column("sku", sa.String(length=128), nullable=True),
        sa.Column("barcode", sa.String(length=64), nullable=True),
        sa.Column("name", sa.String(length=500), nullable=False),
        sa.Column("normalized_name", sa.String(length=500), nullable=False),
        sa.Column("brand", sa.String(length=255), nullable=True),
        sa.Column("category", sa.String(length=255), nullable=True),
        sa.Column("unit_size", sa.Numeric(12, 3), nullable=True),
        sa.Column("unit", sa.String(length=32), nullable=True),
        sa.Column("sale_price", sa.Numeric(14, 2), nullable=True),
        sa.Column("current_cost", sa.Numeric(14, 2), nullable=True),
        sa.Column("margin_percentage", sa.Numeric(8, 2), nullable=True),
        sa.Column("stock_quantity", sa.Numeric(14, 3), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "business_id",
            "external_product_id",
            name="uq_products_business_external",
        ),
        sa.UniqueConstraint("business_id", "barcode", name="uq_products_business_barcode"),
    )
    op.create_index("ix_products_business_id", "products", ["business_id"])
    op.create_index(
        "ix_products_business_normalized_name",
        "products",
        ["business_id", "normalized_name"],
    )

    op.create_table(
        "suppliers",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column(
            "business_id",
            sa.String(length=64),
            sa.ForeignKey("businesses.id"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("normalized_name", sa.String(length=255), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("business_id", "normalized_name", name="uq_suppliers_business_name"),
    )
    op.create_index("ix_suppliers_business_id", "suppliers", ["business_id"])

    op.create_table(
        "supplier_products",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column(
            "business_id",
            sa.String(length=64),
            sa.ForeignKey("businesses.id"),
            nullable=False,
        ),
        sa.Column(
            "supplier_id",
            sa.String(length=64),
            sa.ForeignKey("suppliers.id"),
            nullable=False,
        ),
        sa.Column(
            "product_id",
            sa.String(length=64),
            sa.ForeignKey("products.id"),
            nullable=False,
        ),
        sa.Column("supplier_product_name", sa.String(length=500), nullable=False),
        sa.Column("supplier_product_normalized_name", sa.String(length=500), nullable=False),
        sa.Column("cost_price", sa.Numeric(14, 2), nullable=False),
        sa.Column("currency", sa.String(length=8), nullable=False),
        sa.Column("tax_included", sa.Boolean(), nullable=True),
        sa.Column("package_quantity", sa.Integer(), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metadata", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_supplier_products_business_product",
        "supplier_products",
        ["business_id", "product_id"],
    )
    op.create_index(
        "ix_supplier_products_business_supplier",
        "supplier_products",
        ["business_id", "supplier_id"],
    )
    op.create_index("ix_supplier_products_business_id", "supplier_products", ["business_id"])
    op.create_index("ix_supplier_products_product_id", "supplier_products", ["product_id"])
    op.create_index("ix_supplier_products_supplier_id", "supplier_products", ["supplier_id"])


def downgrade() -> None:
    op.drop_index("ix_supplier_products_supplier_id", table_name="supplier_products")
    op.drop_index("ix_supplier_products_product_id", table_name="supplier_products")
    op.drop_index("ix_supplier_products_business_id", table_name="supplier_products")
    op.drop_index("ix_supplier_products_business_supplier", table_name="supplier_products")
    op.drop_index("ix_supplier_products_business_product", table_name="supplier_products")
    op.drop_table("supplier_products")
    op.drop_index("ix_suppliers_business_id", table_name="suppliers")
    op.drop_table("suppliers")
    op.drop_index("ix_products_business_normalized_name", table_name="products")
    op.drop_index("ix_products_business_id", table_name="products")
    op.drop_table("products")
