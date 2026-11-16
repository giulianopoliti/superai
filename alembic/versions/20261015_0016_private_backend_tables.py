"""private_backend_tables

Revision ID: 20261015_0016
Revises: 20261015_0015
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20261015_0016"
down_revision: str | Sequence[str] | None = "20261015_0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if op.get_context().dialect.name != "postgresql":
        return
    # Business data is exclusively accessed via FastAPI/SQLAlchemy. The browser
    # uses Supabase Auth, never the Data API. Keep the existing privileged server
    # connection and service_role, while denying anon/authenticated access.
    for table in BACKEND_TABLES:
        op.execute(f'ALTER TABLE public."{table}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'REVOKE ALL ON public."{table}" FROM PUBLIC, anon, authenticated')


def downgrade() -> None:
    raise RuntimeError("Downgrading must not restore public access to business data.")


BACKEND_TABLES = (
    "alembic_version",
    "businesses",
    "users",
    "conversation_messages",
    "reminders",
    "products",
    "product_families",
    "suppliers",
    "supplier_products",
    "catalog_imports",
    "supplier_offer_documents",
    "supplier_offer_items",
    "product_match_candidates",
    "product_match_feedback",
    "sales_imports",
    "product_sales_daily",
    "sales_item_lines",
    "catalog_vocabulary",
    "catalog_interpretations",
    "catalog_taxonomy_changes",
    "catalog_family_creation_suggestions",
    "catalog_family_change_suggestions",
    "catalog_family_suggestion_events",
    "web_user_access",
)
