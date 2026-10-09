"""add raw sales item lines

Revision ID: 20260731_0008
Revises: 20260730_0007
Create Date: 2026-07-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260731_0008"
down_revision: str | None = "20260730_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sales_item_lines",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column(
            "business_id",
            sa.String(length=64),
            sa.ForeignKey("businesses.id"),
            nullable=False,
        ),
        sa.Column(
            "source_import_id",
            sa.String(length=64),
            sa.ForeignKey("sales_imports.id"),
            nullable=False,
        ),
        sa.Column(
            "product_id",
            sa.String(length=64),
            sa.ForeignKey("products.id"),
            nullable=True,
        ),
        sa.Column("external_sale_item_id", sa.String(length=128), nullable=False),
        sa.Column("external_sale_id", sa.String(length=128), nullable=False),
        sa.Column("external_product_id", sa.String(length=128), nullable=False),
        sa.Column("sku", sa.String(length=128), nullable=True),
        sa.Column("sold_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sale_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("quantity", sa.Numeric(14, 3), nullable=False),
        sa.Column("revenue", sa.Numeric(14, 2), nullable=False),
        sa.Column("unit_cost", sa.Numeric(14, 2), nullable=False),
        sa.Column("cost_amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("gross_margin", sa.Numeric(14, 2), nullable=False),
        sa.Column("raw_payload", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "business_id",
            "external_sale_item_id",
            name="uq_sales_item_lines_business_external_item",
        ),
    )
    op.create_index(
        "ix_sales_item_lines_business_id",
        "sales_item_lines",
        ["business_id"],
    )
    op.create_index(
        "ix_sales_item_lines_source_import_id",
        "sales_item_lines",
        ["source_import_id"],
    )
    op.create_index("ix_sales_item_lines_product_id", "sales_item_lines", ["product_id"])
    op.create_index(
        "ix_sales_item_lines_external_sale_id",
        "sales_item_lines",
        ["external_sale_id"],
    )
    op.create_index("ix_sales_item_lines_sold_at", "sales_item_lines", ["sold_at"])
    op.create_index("ix_sales_item_lines_sale_date", "sales_item_lines", ["sale_date"])
    op.create_index(
        "ix_sales_item_lines_business_sold_at",
        "sales_item_lines",
        ["business_id", "sold_at"],
    )
    op.create_index(
        "ix_sales_item_lines_business_sale_date",
        "sales_item_lines",
        ["business_id", "sale_date"],
    )
    op.create_index(
        "ix_sales_item_lines_business_product_date",
        "sales_item_lines",
        ["business_id", "product_id", "sale_date"],
    )
    op.create_index(
        "ix_sales_item_lines_business_external_product_date",
        "sales_item_lines",
        ["business_id", "external_product_id", "sale_date"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_sales_item_lines_business_external_product_date",
        table_name="sales_item_lines",
    )
    op.drop_index(
        "ix_sales_item_lines_business_product_date",
        table_name="sales_item_lines",
    )
    op.drop_index("ix_sales_item_lines_business_sale_date", table_name="sales_item_lines")
    op.drop_index("ix_sales_item_lines_business_sold_at", table_name="sales_item_lines")
    op.drop_index("ix_sales_item_lines_sale_date", table_name="sales_item_lines")
    op.drop_index("ix_sales_item_lines_sold_at", table_name="sales_item_lines")
    op.drop_index("ix_sales_item_lines_external_sale_id", table_name="sales_item_lines")
    op.drop_index("ix_sales_item_lines_product_id", table_name="sales_item_lines")
    op.drop_index("ix_sales_item_lines_source_import_id", table_name="sales_item_lines")
    op.drop_index("ix_sales_item_lines_business_id", table_name="sales_item_lines")
    op.drop_table("sales_item_lines")
