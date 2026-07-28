"""allow duplicate product barcodes

Revision ID: 20260725_0003
Revises: 20260725_0002
Create Date: 2026-07-25
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260725_0003"
down_revision: str | None = "20260725_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("uq_products_business_barcode", "products", type_="unique")
    op.create_index("ix_products_business_barcode", "products", ["business_id", "barcode"])


def downgrade() -> None:
    op.drop_index("ix_products_business_barcode", table_name="products")
    op.create_unique_constraint(
        "uq_products_business_barcode",
        "products",
        ["business_id", "barcode"],
    )
