"""add sales imports and daily product sales

Revision ID: 20260730_0007
Revises: 20260727_0006
Create Date: 2026-07-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260730_0007"
down_revision: str | None = "20260727_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sales_imports",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column(
            "business_id",
            sa.String(length=64),
            sa.ForeignKey("businesses.id"),
            nullable=False,
        ),
        sa.Column("source_filename", sa.String(length=500), nullable=False),
        sa.Column("source_type", sa.String(length=64), nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("imported_count", sa.Integer(), nullable=False),
        sa.Column("skipped_count", sa.Integer(), nullable=False),
        sa.Column("unique_ticket_count", sa.Integer(), nullable=False),
        sa.Column("unique_product_count", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("errors", postgresql.JSONB(), nullable=False),
        sa.Column("summary", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_sales_imports_business_created",
        "sales_imports",
        ["business_id", "created_at"],
    )
    op.create_index(
        "ix_sales_imports_business_period",
        "sales_imports",
        ["business_id", "period_start", "period_end"],
    )
    op.create_index("ix_sales_imports_business_id", "sales_imports", ["business_id"])
    op.create_index("ix_sales_imports_status", "sales_imports", ["status"])

    op.create_table(
        "product_sales_daily",
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
        sa.Column("external_product_id", sa.String(length=128), nullable=False),
        sa.Column("sku", sa.String(length=128), nullable=True),
        sa.Column("sale_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("quantity_sold", sa.Numeric(14, 3), nullable=False),
        sa.Column("revenue", sa.Numeric(14, 2), nullable=False),
        sa.Column("cost_amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("gross_margin", sa.Numeric(14, 2), nullable=False),
        sa.Column("ticket_count", sa.Integer(), nullable=False),
        sa.Column("line_count", sa.Integer(), nullable=False),
        sa.Column("zero_cost_line_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "business_id",
            "external_product_id",
            "sale_date",
            name="uq_product_sales_daily_business_product_date",
        ),
    )
    op.create_index(
        "ix_product_sales_daily_business_product_date",
        "product_sales_daily",
        ["business_id", "product_id", "sale_date"],
    )
    op.create_index(
        "ix_product_sales_daily_business_external_date",
        "product_sales_daily",
        ["business_id", "external_product_id", "sale_date"],
    )
    op.create_index(
        "ix_product_sales_daily_business_id",
        "product_sales_daily",
        ["business_id"],
    )
    op.create_index(
        "ix_product_sales_daily_source_import_id",
        "product_sales_daily",
        ["source_import_id"],
    )
    op.create_index("ix_product_sales_daily_product_id", "product_sales_daily", ["product_id"])
    op.create_index("ix_product_sales_daily_sale_date", "product_sales_daily", ["sale_date"])


def downgrade() -> None:
    op.drop_index("ix_product_sales_daily_sale_date", table_name="product_sales_daily")
    op.drop_index("ix_product_sales_daily_product_id", table_name="product_sales_daily")
    op.drop_index(
        "ix_product_sales_daily_source_import_id",
        table_name="product_sales_daily",
    )
    op.drop_index("ix_product_sales_daily_business_id", table_name="product_sales_daily")
    op.drop_index(
        "ix_product_sales_daily_business_external_date",
        table_name="product_sales_daily",
    )
    op.drop_index(
        "ix_product_sales_daily_business_product_date",
        table_name="product_sales_daily",
    )
    op.drop_table("product_sales_daily")

    op.drop_index("ix_sales_imports_status", table_name="sales_imports")
    op.drop_index("ix_sales_imports_business_id", table_name="sales_imports")
    op.drop_index("ix_sales_imports_business_period", table_name="sales_imports")
    op.drop_index("ix_sales_imports_business_created", table_name="sales_imports")
    op.drop_table("sales_imports")
