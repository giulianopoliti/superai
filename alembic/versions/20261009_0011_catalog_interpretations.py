"""Add auxiliary catalog vocabulary and Decisions interpretations.

Revision ID: 20261009_0011
Revises: 20260918_0010
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "20261009_0011"
down_revision = "20260918_0010"
branch_labels = None
depends_on = None


def upgrade():
    op.create_unique_constraint("uq_products_business_id_id", "products", ["business_id", "id"])
    for table in _tables():
        table.create(op.get_bind())


def _tables():
    metadata = sa.MetaData()
    sa.Table("businesses", metadata, sa.Column("id", sa.String(64), primary_key=True))
    sa.Table(
        "products",
        metadata,
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("business_id", sa.String(64)),
        sa.UniqueConstraint("business_id", "id"),
    )
    json_type = sa.JSON().with_variant(JSONB, "postgresql")
    vocabulary = sa.Table(
        "catalog_vocabulary",
        metadata,
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "business_id", sa.String(64), sa.ForeignKey("businesses.id"), nullable=False, index=True
        ),
        sa.Column("attribute", sa.String(32), nullable=False),
        sa.Column("label", sa.String(255), nullable=False),
        sa.Column("normalized_label", sa.String(255), nullable=False),
        sa.Column("aliases", json_type, nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "business_id", "attribute", "normalized_label", name="uq_catalog_vocab_business_label"
        ),
    )
    interpretations = sa.Table(
        "catalog_interpretations",
        metadata,
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("run_id", sa.String(64), nullable=False, index=True),
        sa.Column("business_id", sa.String(64), nullable=False, index=True),
        sa.Column("product_id", sa.String(64), nullable=False),
        sa.Column("source_hash", sa.String(64), nullable=False),
        sa.Column("config_hash", sa.String(64), nullable=False),
        sa.Column("source", json_type, nullable=False),
        *[
            sa.Column(field, sa.String(255), nullable=True)
            for field in ("product_type", "brand", "line", "variant", "packaging")
        ],
        sa.Column("content_value", sa.Numeric(12, 3), nullable=True),
        sa.Column("content_unit", sa.String(32), nullable=True),
        sa.Column("package_quantity", sa.Integer(), nullable=True),
        sa.Column("minimum_order_quantity", sa.Integer(), nullable=True),
        sa.Column("standard_name", sa.String(1000), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        *[
            sa.Column(field, json_type, nullable=False)
            for field in ("attributes", "request", "response")
        ],
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["business_id", "product_id"],
            ["products.business_id", "products.id"],
            name="fk_catalog_interpretation_business_product",
        ),
        sa.UniqueConstraint(
            "business_id",
            "product_id",
            "source_hash",
            "config_hash",
            name="uq_catalog_interpretation_source_config",
        ),
    )
    return [vocabulary, interpretations]


def downgrade():
    op.drop_table("catalog_interpretations")
    op.drop_table("catalog_vocabulary")
    op.drop_constraint("uq_products_business_id_id", "products", type_="unique")
