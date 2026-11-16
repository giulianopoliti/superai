"""Persist interpretation needs without scheduling paid work."""

import sqlalchemy as sa

from alembic import op

revision = "20261021_0018"
down_revision = "20261015_0017"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "catalog_interpretation_needs",
        sa.Column("business_id", sa.String(64), primary_key=True),
        sa.Column("product_id", sa.String(64), primary_key=True),
        sa.Column("source_hash", sa.String(64), nullable=False),
        sa.Column("version", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["business_id", "product_id"], ["products.business_id", "products.id"],
            name="fk_catalog_need_business_product",
        ),
    )
    if op.get_context().dialect.name == "postgresql":
        op.execute("ALTER TABLE catalog_interpretation_needs ENABLE ROW LEVEL SECURITY")
        op.execute("REVOKE ALL ON catalog_interpretation_needs FROM PUBLIC, anon, authenticated")


def downgrade():
    op.drop_table("catalog_interpretation_needs")
