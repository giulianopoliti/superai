"""add supplier price types

Revision ID: 20260918_0010
Revises: 20260813_0009
Create Date: 2026-09-18
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260918_0010"
down_revision: str | None = "20260813_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "supplier_offer_items",
        sa.Column(
            "price_type",
            sa.String(length=16),
            nullable=False,
            server_default="unit",
        ),
    )
    op.add_column(
        "supplier_products",
        sa.Column(
            "price_type",
            sa.String(length=16),
            nullable=False,
            server_default="unit",
        ),
    )


def downgrade() -> None:
    op.drop_column("supplier_products", "price_type")
    op.drop_column("supplier_offer_items", "price_type")
