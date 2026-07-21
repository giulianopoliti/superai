"""initial persistence tables

Revision ID: 20260720_0001
Revises:
Create Date: 2026-07-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260720_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "businesses",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column(
            "timezone",
            sa.String(length=64),
            nullable=False,
            server_default="America/Buenos_Aires",
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "users",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column(
            "business_id",
            sa.String(length=64),
            sa.ForeignKey("businesses.id"),
            nullable=False,
        ),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=64), nullable=False, server_default="operator"),
        sa.Column("phone", sa.String(length=64), nullable=True),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_users_business_id", "users", ["business_id"])
    op.create_table(
        "conversation_messages",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("business_id", sa.String(length=64), nullable=False, index=True),
        sa.Column("external_user_id", sa.String(length=255), nullable=False),
        sa.Column("channel", sa.String(length=64), nullable=False),
        sa.Column("direction", sa.String(length=32), nullable=False),
        sa.Column("message_type", sa.String(length=64), nullable=False),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "reminders",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("business_id", sa.String(length=64), nullable=False, index=True),
        sa.Column("created_by_external_user_id", sa.String(length=255), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("recurrence_rule", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, index=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_reminders_business_status", "reminders", ["business_id", "status"])


def downgrade() -> None:
    op.drop_index("ix_reminders_business_status", table_name="reminders")
    op.drop_table("reminders")
    op.drop_table("conversation_messages")
    op.drop_index("ix_users_business_id", table_name="users")
    op.drop_table("users")
    op.drop_table("businesses")
