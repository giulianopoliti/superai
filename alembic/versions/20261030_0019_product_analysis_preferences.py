"""product_analysis_preferences

Revision ID: 20261030_0019
Revises: 20261021_0018
"""

import sqlalchemy as sa

from alembic import op

revision = "20261030_0019"
down_revision = "20261021_0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "product_analysis_preferences",
        sa.Column("business_id", sa.String(64), primary_key=True),
        sa.Column("product_id", sa.String(64), primary_key=True),
        sa.Column("exclude_from_sales_rankings", sa.Boolean(), nullable=False,
                  server_default=sa.false()),
        sa.Column("updated_by_user_id", sa.String(64), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["business_id", "product_id"], ["products.business_id", "products.id"],
            name="fk_analysis_preference_business_product",
        ),
    )
    if op.get_context().dialect.name == "postgresql":
        op.execute("ALTER TABLE product_analysis_preferences ENABLE ROW LEVEL SECURITY")
        op.execute("REVOKE ALL ON product_analysis_preferences FROM PUBLIC, anon, authenticated")


def downgrade() -> None:
    op.drop_table("product_analysis_preferences")
