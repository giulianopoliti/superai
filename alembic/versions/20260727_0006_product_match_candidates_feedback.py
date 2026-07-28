"""add product match candidates and feedback

Revision ID: 20260727_0006
Revises: 20260727_0005
Create Date: 2026-07-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260727_0006"
down_revision: str | None = "20260727_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "product_match_candidates",
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
            "supplier_offer_item_id",
            sa.String(length=64),
            sa.ForeignKey("supplier_offer_items.id"),
            nullable=False,
        ),
        sa.Column(
            "product_id",
            sa.String(length=64),
            sa.ForeignKey("products.id"),
            nullable=True,
        ),
        sa.Column("relationship_type", sa.String(length=64), nullable=False),
        sa.Column("confidence_score", sa.Numeric(5, 4), nullable=False),
        sa.Column("reasons", postgresql.JSONB(), nullable=False),
        sa.Column("cost_difference", sa.Numeric(14, 2), nullable=True),
        sa.Column("cost_difference_percentage", sa.Numeric(8, 2), nullable=True),
        sa.Column("estimated_margin_percentage", sa.Numeric(8, 2), nullable=True),
        sa.Column("recommendation", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_product_match_candidates_business_document",
        "product_match_candidates",
        ["business_id", "supplier_offer_document_id"],
    )
    op.create_index(
        "ix_product_match_candidates_business_item",
        "product_match_candidates",
        ["business_id", "supplier_offer_item_id"],
    )
    op.create_index(
        "ix_product_match_candidates_business_status",
        "product_match_candidates",
        ["business_id", "status"],
    )
    op.create_index(
        "ix_product_match_candidates_business_id",
        "product_match_candidates",
        ["business_id"],
    )
    op.create_index(
        "ix_product_match_candidates_supplier_offer_document_id",
        "product_match_candidates",
        ["supplier_offer_document_id"],
    )
    op.create_index(
        "ix_product_match_candidates_supplier_offer_item_id",
        "product_match_candidates",
        ["supplier_offer_item_id"],
    )
    op.create_index(
        "ix_product_match_candidates_product_id",
        "product_match_candidates",
        ["product_id"],
    )
    op.create_index(
        "ix_product_match_candidates_status",
        "product_match_candidates",
        ["status"],
    )

    op.create_table(
        "product_match_feedback",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column(
            "business_id",
            sa.String(length=64),
            sa.ForeignKey("businesses.id"),
            nullable=False,
        ),
        sa.Column(
            "product_match_candidate_id",
            sa.String(length=64),
            sa.ForeignKey("product_match_candidates.id"),
            nullable=False,
        ),
        sa.Column(
            "supplier_offer_item_id",
            sa.String(length=64),
            sa.ForeignKey("supplier_offer_items.id"),
            nullable=False,
        ),
        sa.Column(
            "candidate_product_id",
            sa.String(length=64),
            sa.ForeignKey("products.id"),
            nullable=True,
        ),
        sa.Column("relationship_type", sa.String(length=64), nullable=False),
        sa.Column("accepted", sa.Boolean(), nullable=False),
        sa.Column("reviewed_by_user_id", sa.String(length=64), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_product_match_feedback_business_candidate",
        "product_match_feedback",
        ["business_id", "product_match_candidate_id"],
    )
    op.create_index(
        "ix_product_match_feedback_business_item",
        "product_match_feedback",
        ["business_id", "supplier_offer_item_id"],
    )
    op.create_index(
        "ix_product_match_feedback_business_product",
        "product_match_feedback",
        ["business_id", "candidate_product_id"],
    )
    op.create_index(
        "ix_product_match_feedback_business_id",
        "product_match_feedback",
        ["business_id"],
    )
    op.create_index(
        "ix_product_match_feedback_product_match_candidate_id",
        "product_match_feedback",
        ["product_match_candidate_id"],
    )
    op.create_index(
        "ix_product_match_feedback_supplier_offer_item_id",
        "product_match_feedback",
        ["supplier_offer_item_id"],
    )
    op.create_index(
        "ix_product_match_feedback_candidate_product_id",
        "product_match_feedback",
        ["candidate_product_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_product_match_feedback_candidate_product_id",
        table_name="product_match_feedback",
    )
    op.drop_index(
        "ix_product_match_feedback_supplier_offer_item_id",
        table_name="product_match_feedback",
    )
    op.drop_index(
        "ix_product_match_feedback_product_match_candidate_id",
        table_name="product_match_feedback",
    )
    op.drop_index("ix_product_match_feedback_business_id", table_name="product_match_feedback")
    op.drop_index(
        "ix_product_match_feedback_business_product",
        table_name="product_match_feedback",
    )
    op.drop_index("ix_product_match_feedback_business_item", table_name="product_match_feedback")
    op.drop_index(
        "ix_product_match_feedback_business_candidate",
        table_name="product_match_feedback",
    )
    op.drop_table("product_match_feedback")

    op.drop_index("ix_product_match_candidates_status", table_name="product_match_candidates")
    op.drop_index(
        "ix_product_match_candidates_product_id",
        table_name="product_match_candidates",
    )
    op.drop_index(
        "ix_product_match_candidates_supplier_offer_item_id",
        table_name="product_match_candidates",
    )
    op.drop_index(
        "ix_product_match_candidates_supplier_offer_document_id",
        table_name="product_match_candidates",
    )
    op.drop_index(
        "ix_product_match_candidates_business_id",
        table_name="product_match_candidates",
    )
    op.drop_index(
        "ix_product_match_candidates_business_status",
        table_name="product_match_candidates",
    )
    op.drop_index(
        "ix_product_match_candidates_business_item",
        table_name="product_match_candidates",
    )
    op.drop_index(
        "ix_product_match_candidates_business_document",
        table_name="product_match_candidates",
    )
    op.drop_table("product_match_candidates")
