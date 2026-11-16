"""web_user_access

Revision ID: 20261015_0015
Revises: 20261014_0014
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20261015_0015"
down_revision: str | Sequence[str] | None = "20261014_0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "web_user_access",
        sa.Column("auth_subject", sa.String(36), nullable=False),
        sa.Column("business_id", sa.String(64), nullable=False),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("auth_subject", "business_id"),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"]),
        sa.ForeignKeyConstraint(
            ["business_id", "user_id"],
            ["users.business_id", "users.id"],
            name="fk_web_access_business_user",
            ondelete="CASCADE",
        ),
    )
    # The browser may authenticate with Supabase but cannot read or grant access.
    # Backend SQLAlchemy uses the existing privileged DB connection.
    if op.get_context().dialect.name == "postgresql":
        op.execute("ALTER TABLE public.web_user_access ENABLE ROW LEVEL SECURITY")
        op.execute("REVOKE ALL ON public.web_user_access FROM PUBLIC, anon, authenticated")


def downgrade() -> None:
    op.drop_table("web_user_access")
