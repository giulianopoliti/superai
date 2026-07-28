"""add supplier offer documents and items

Revision ID: 20260727_0005
Revises: 20260727_0004
Create Date: 2026-07-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260727_0005"
down_revision: str | None = "20260727_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "supplier_offer_documents",
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
        sa.Column("source_filename", sa.String(length=500), nullable=False),
        sa.Column("document_type", sa.String(length=64), nullable=False),
        sa.Column("extraction_status", sa.String(length=32), nullable=False),
        sa.Column("extraction_provider", sa.String(length=64), nullable=False),
        sa.Column("raw_text", sa.Text(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_supplier_offer_documents_business_created",
        "supplier_offer_documents",
        ["business_id", "created_at"],
    )
    op.create_index(
        "ix_supplier_offer_documents_business_supplier",
        "supplier_offer_documents",
        ["business_id", "supplier_id"],
    )
    op.create_index(
        "ix_supplier_offer_documents_business_id",
        "supplier_offer_documents",
        ["business_id"],
    )
    op.create_index(
        "ix_supplier_offer_documents_supplier_id",
        "supplier_offer_documents",
        ["supplier_id"],
    )
    op.create_index(
        "ix_supplier_offer_documents_extraction_status",
        "supplier_offer_documents",
        ["extraction_status"],
    )

    op.create_table(
        "supplier_offer_items",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column(
            "business_id",
            sa.String(length=64),
            sa.ForeignKey("businesses.id"),
            nullable=False,
        ),
        sa.Column(
            "supplier_offer_document_id",
            sa.String(length=64),
            sa.ForeignKey("supplier_offer_documents.id"),
            nullable=False,
        ),
        sa.Column(
            "supplier_id",
            sa.String(length=64),
            sa.ForeignKey("suppliers.id"),
            nullable=False,
        ),
        sa.Column("raw_name", sa.String(length=500), nullable=False),
        sa.Column("normalized_name", sa.String(length=500), nullable=False),
        sa.Column("brand", sa.String(length=255), nullable=True),
        sa.Column("unit_size", sa.Numeric(12, 3), nullable=True),
        sa.Column("unit", sa.String(length=32), nullable=True),
        sa.Column("package_quantity", sa.Integer(), nullable=True),
        sa.Column("offer_price", sa.Numeric(14, 2), nullable=False),
        sa.Column("currency", sa.String(length=8), nullable=False),
        sa.Column("tax_included", sa.Boolean(), nullable=True),
        sa.Column("page_number", sa.Integer(), nullable=True),
        sa.Column("confidence_score", sa.Numeric(5, 4), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_supplier_offer_items_business_document",
        "supplier_offer_items",
        ["business_id", "supplier_offer_document_id"],
    )
    op.create_index(
        "ix_supplier_offer_items_business_supplier",
        "supplier_offer_items",
        ["business_id", "supplier_id"],
    )
    op.create_index(
        "ix_supplier_offer_items_business_normalized_name",
        "supplier_offer_items",
        ["business_id", "normalized_name"],
    )
    op.create_index(
        "ix_supplier_offer_items_business_id",
        "supplier_offer_items",
        ["business_id"],
    )
    op.create_index(
        "ix_supplier_offer_items_supplier_offer_document_id",
        "supplier_offer_items",
        ["supplier_offer_document_id"],
    )
    op.create_index(
        "ix_supplier_offer_items_supplier_id",
        "supplier_offer_items",
        ["supplier_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_supplier_offer_items_supplier_id", table_name="supplier_offer_items")
    op.drop_index(
        "ix_supplier_offer_items_supplier_offer_document_id",
        table_name="supplier_offer_items",
    )
    op.drop_index("ix_supplier_offer_items_business_id", table_name="supplier_offer_items")
    op.drop_index(
        "ix_supplier_offer_items_business_normalized_name",
        table_name="supplier_offer_items",
    )
    op.drop_index("ix_supplier_offer_items_business_supplier", table_name="supplier_offer_items")
    op.drop_index("ix_supplier_offer_items_business_document", table_name="supplier_offer_items")
    op.drop_table("supplier_offer_items")
    op.drop_index(
        "ix_supplier_offer_documents_extraction_status",
        table_name="supplier_offer_documents",
    )
    op.drop_index(
        "ix_supplier_offer_documents_supplier_id",
        table_name="supplier_offer_documents",
    )
    op.drop_index(
        "ix_supplier_offer_documents_business_id",
        table_name="supplier_offer_documents",
    )
    op.drop_index(
        "ix_supplier_offer_documents_business_supplier",
        table_name="supplier_offer_documents",
    )
    op.drop_index(
        "ix_supplier_offer_documents_business_created",
        table_name="supplier_offer_documents",
    )
    op.drop_table("supplier_offer_documents")
