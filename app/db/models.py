from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import JSON


def utc_now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class BusinessModel(Base):
    __tablename__ = "businesses"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    timezone: Mapped[str] = mapped_column(String(64), default="America/Buenos_Aires")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class UserModel(Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("business_id", "id", name="uq_users_business_id_id"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    business_id: Mapped[str] = mapped_column(String(64), index=True)
    display_name: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(64), default="operator")
    phone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class WebUserAccessModel(Base):
    __tablename__ = "web_user_access"
    __table_args__ = (
        ForeignKeyConstraint(
            ["business_id", "user_id"], ["users.business_id", "users.id"],
            name="fk_web_access_business_user", ondelete="CASCADE",
        ),
    )

    auth_subject: Mapped[str] = mapped_column(String(36), primary_key=True)
    business_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("businesses.id"), primary_key=True
    )
    user_id: Mapped[str] = mapped_column(String(64))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class BackgroundJobModel(Base):
    __tablename__ = "background_jobs"
    __table_args__ = (
        UniqueConstraint("business_id", "created_by_user_id", "job_type", "idempotency_key",
                         name="uq_background_jobs_idempotency"),
        ForeignKeyConstraint(
            ["business_id", "created_by_user_id"], ["users.business_id", "users.id"],
            name="fk_background_jobs_business_creator",
        ),
        CheckConstraint("status IN ('queued','running','succeeded','failed')",
                        name="ck_background_jobs_status"),
        Index("ix_background_jobs_acquire", "status", "created_at"),
        Index("ix_background_jobs_lease", "status", "lease_expires_at"),
        Index("ix_background_jobs_owner", "business_id", "created_by_user_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    business_id: Mapped[str] = mapped_column(String(64), ForeignKey("businesses.id"))
    created_by_user_id: Mapped[str] = mapped_column(String(64))
    job_type: Mapped[str] = mapped_column(String(64), default="supplier_offer_analysis_v1")
    idempotency_key: Mapped[str] = mapped_column(String(128))
    request_fingerprint: Mapped[str] = mapped_column(String(64))
    file_key: Mapped[str] = mapped_column(String(36))
    file_sha256: Mapped[str] = mapped_column(String(64))
    source_filename: Mapped[str] = mapped_column(String(255))
    parameters: Mapped[dict] = mapped_column(JSON().with_variant(JSONB, "postgresql"))
    status: Mapped[str] = mapped_column(String(16), default="queued")
    phase: Mapped[str] = mapped_column(String(32), default="queued")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    attempt_token: Mapped[str | None] = mapped_column(String(36), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    checkpoint: Mapped[dict | None] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), nullable=True
    )
    result: Mapped[dict | None] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), nullable=True
    )
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ConversationMessageModel(Base):
    __tablename__ = "conversation_messages"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    business_id: Mapped[str] = mapped_column(String(64), index=True)
    external_user_id: Mapped[str] = mapped_column(String(255))
    channel: Mapped[str] = mapped_column(String(64))
    direction: Mapped[str] = mapped_column(String(32))
    message_type: Mapped[str] = mapped_column(String(64))
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload: Mapped[dict[str, object]] = mapped_column(JSON().with_variant(JSONB, "postgresql"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ReminderModel(Base):
    __tablename__ = "reminders"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    business_id: Mapped[str] = mapped_column(String(64), index=True)
    created_by_external_user_id: Mapped[str] = mapped_column(String(255))
    title: Mapped[str] = mapped_column(String(500))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    recurrence_rule: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class ProductFamilyModel(Base):
    __tablename__ = "product_families"
    __table_args__ = (
        UniqueConstraint("business_id", "id", name="uq_product_families_business_id_id"),
        UniqueConstraint(
            "business_id",
            "external_family_id",
            name="uq_product_families_business_external",
        ),
        Index("ix_product_families_business_normalized_name", "business_id", "normalized_name"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    business_id: Mapped[str] = mapped_column(String(64), ForeignKey("businesses.id"), index=True)
    external_family_id: Mapped[str] = mapped_column(String(128))
    name: Mapped[str] = mapped_column(String(255))
    normalized_name: Mapped[str] = mapped_column(String(255))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class ProductModel(Base):
    __tablename__ = "products"
    __table_args__ = (
        UniqueConstraint("business_id", "id", name="uq_products_business_id_id"),
        UniqueConstraint(
            "business_id",
            "external_product_id",
            name="uq_products_business_external",
        ),
        Index("ix_products_business_barcode", "business_id", "barcode"),
        Index("ix_products_business_normalized_name", "business_id", "normalized_name"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    business_id: Mapped[str] = mapped_column(String(64), ForeignKey("businesses.id"), index=True)
    external_product_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    sku: Mapped[str | None] = mapped_column(String(128), nullable=True)
    barcode: Mapped[str | None] = mapped_column(String(64), nullable=True)
    name: Mapped[str] = mapped_column(String(500))
    normalized_name: Mapped[str] = mapped_column(String(500))
    brand: Mapped[str | None] = mapped_column(String(255), nullable=True)
    category: Mapped[str | None] = mapped_column(String(255), nullable=True)
    family_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("product_families.id"), nullable=True, index=True
    )
    external_family_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    unit_size: Mapped[Decimal | None] = mapped_column(Numeric(12, 3), nullable=True)
    unit: Mapped[str | None] = mapped_column(String(32), nullable=True)
    sale_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    current_cost: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    margin_percentage: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    stock_quantity: Mapped[Decimal | None] = mapped_column(Numeric(14, 3), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    source: Mapped[str] = mapped_column(String(64), default="pos_csv")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class ProductAnalysisPreferenceModel(Base):
    __tablename__ = "product_analysis_preferences"
    __table_args__ = (
        ForeignKeyConstraint(
            ["business_id", "product_id"], ["products.business_id", "products.id"],
            name="fk_analysis_preference_business_product",
        ),
    )

    business_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    product_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    exclude_from_sales_rankings: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )
    updated_by_user_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class CatalogVocabularyModel(Base):
    __tablename__ = "catalog_vocabulary"
    __table_args__ = (
        UniqueConstraint("business_id", "id", name="uq_catalog_vocab_business_id"),
        ForeignKeyConstraint(
            ["business_id", "parent_id"],
            ["catalog_vocabulary.business_id", "catalog_vocabulary.id"],
            name="fk_catalog_vocab_business_parent",
        ),
        UniqueConstraint(
            "business_id", "attribute", "normalized_label", name="uq_catalog_vocab_business_label"
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    business_id: Mapped[str] = mapped_column(String(64), ForeignKey("businesses.id"), index=True)
    attribute: Mapped[str] = mapped_column(String(32))
    parent_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    label: Mapped[str] = mapped_column(String(255))
    normalized_label: Mapped[str] = mapped_column(String(255))
    aliases: Mapped[list] = mapped_column(JSON().with_variant(JSONB, "postgresql"))
    status: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class CatalogInterpretationNeedModel(Base):
    __tablename__ = "catalog_interpretation_needs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["business_id", "product_id"], ["products.business_id", "products.id"],
            name="fk_catalog_need_business_product",
        ),
    )

    business_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    product_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_hash: Mapped[str] = mapped_column(String(64))
    version: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="pending")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class CatalogInterpretationModel(Base):
    __tablename__ = "catalog_interpretations"
    __table_args__ = (
        UniqueConstraint(
            "business_id", "id", "product_id", name="uq_catalog_interpretation_business_id_product"
        ),
        ForeignKeyConstraint(
            ["business_id", "product_id"],
            ["products.business_id", "products.id"],
            name="fk_catalog_interpretation_business_product",
        ),
        UniqueConstraint(
            "business_id",
            "product_id",
            "source_hash",
            "config_hash",
            name="uq_catalog_interpretation_source_config",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    business_id: Mapped[str] = mapped_column(String(64), index=True)
    product_id: Mapped[str] = mapped_column(String(64))
    source_hash: Mapped[str] = mapped_column(String(64))
    config_hash: Mapped[str] = mapped_column(String(64))
    source: Mapped[dict] = mapped_column(JSON().with_variant(JSONB, "postgresql"))
    product_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    category: Mapped[str | None] = mapped_column(String(255), nullable=True)
    subcategory: Mapped[str | None] = mapped_column(String(255), nullable=True)
    brand: Mapped[str | None] = mapped_column(String(255), nullable=True)
    line: Mapped[str | None] = mapped_column(String(255), nullable=True)
    variant: Mapped[str | None] = mapped_column(String(255), nullable=True)
    packaging: Mapped[str | None] = mapped_column(String(255), nullable=True)
    content_value: Mapped[Decimal | None] = mapped_column(Numeric(12, 3), nullable=True)
    content_unit: Mapped[str | None] = mapped_column(String(32), nullable=True)
    package_quantity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    minimum_order_quantity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    standard_name: Mapped[str] = mapped_column(String(1000))
    status: Mapped[str] = mapped_column(String(32))
    attributes: Mapped[dict] = mapped_column(JSON().with_variant(JSONB, "postgresql"))
    request: Mapped[dict] = mapped_column(JSON().with_variant(JSONB, "postgresql"))
    response: Mapped[dict] = mapped_column(JSON().with_variant(JSONB, "postgresql"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class CatalogTaxonomyChangeModel(Base):
    __tablename__ = "catalog_taxonomy_changes"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    business_id: Mapped[str] = mapped_column(String(64), ForeignKey("businesses.id"), index=True)
    status: Mapped[str] = mapped_column(String(32))
    operations: Mapped[list] = mapped_column(JSON().with_variant(JSONB, "postgresql"))
    evidence: Mapped[dict] = mapped_column(JSON().with_variant(JSONB, "postgresql"))
    before: Mapped[dict] = mapped_column(JSON().with_variant(JSONB, "postgresql"))
    after: Mapped[dict] = mapped_column(JSON().with_variant(JSONB, "postgresql"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class FamilyReviewMixin:
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    business_id: Mapped[str] = mapped_column(String(64), ForeignKey("businesses.id"))
    status: Mapped[str] = mapped_column(String(32), default="pending")
    reason: Mapped[str] = mapped_column(Text)
    evidence: Mapped[dict] = mapped_column(JSON().with_variant(JSONB, "postgresql"))
    evaluation_metadata: Mapped[dict] = mapped_column(JSON().with_variant(JSONB, "postgresql"))
    policy_version: Mapped[str] = mapped_column(String(120))
    reviewed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    review_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    application_reference: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class CatalogFamilyCreationSuggestionModel(FamilyReviewMixin, Base):
    __tablename__ = "catalog_family_creation_suggestions"
    __table_args__ = (
        UniqueConstraint("business_id", "id", name="uq_family_creation_business_id"),
        ForeignKeyConstraint(["business_id", "reviewed_by"], ["users.business_id", "users.id"]),
        ForeignKeyConstraint(
            ["business_id", "created_family_id"],
            ["product_families.business_id", "product_families.id"],
        ),
        CheckConstraint(
            "status IN ('pending','approved','rejected','applied','stale')",
            name="ck_family_creation_status",
        ),
        CheckConstraint(
            "status NOT IN ('approved','rejected','applied') OR "
            "(reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL)",
            name="ck_family_creation_review",
        ),
        CheckConstraint(
            "status != 'applied' OR (created_family_id IS NOT NULL "
            "AND application_reference IS NOT NULL AND applied_at IS NOT NULL)",
            name="ck_family_creation_applied",
        ),
        Index("ix_family_creation_business_status", "business_id", "status", "created_at"),
        Index(
            "uq_family_creation_open_name",
            "business_id",
            "normalized_name",
            unique=True,
            postgresql_where=text("status IN ('pending','approved')"),
            sqlite_where=text("status IN ('pending','approved')"),
        ),
    )
    proposed_name: Mapped[str] = mapped_column(String(255))
    normalized_name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text)
    includes: Mapped[list] = mapped_column(JSON().with_variant(JSONB, "postgresql"))
    excludes: Mapped[list] = mapped_column(JSON().with_variant(JSONB, "postgresql"))
    definition_hash: Mapped[str] = mapped_column(String(64))
    created_family_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class CatalogFamilyChangeSuggestionModel(FamilyReviewMixin, Base):
    __tablename__ = "catalog_family_change_suggestions"
    __table_args__ = (
        UniqueConstraint("business_id", "id", name="uq_family_change_business_id"),
        ForeignKeyConstraint(
            ["business_id", "product_id"], ["products.business_id", "products.id"]
        ),
        ForeignKeyConstraint(
            ["business_id", "interpretation_id", "product_id"],
            [
                "catalog_interpretations.business_id",
                "catalog_interpretations.id",
                "catalog_interpretations.product_id",
            ],
        ),
        ForeignKeyConstraint(
            ["business_id", "original_family_id"],
            ["product_families.business_id", "product_families.id"],
        ),
        ForeignKeyConstraint(
            ["business_id", "target_family_id"],
            ["product_families.business_id", "product_families.id"],
        ),
        ForeignKeyConstraint(
            ["business_id", "target_creation_suggestion_id"],
            [
                "catalog_family_creation_suggestions.business_id",
                "catalog_family_creation_suggestions.id",
            ],
        ),
        ForeignKeyConstraint(["business_id", "reviewed_by"], ["users.business_id", "users.id"]),
        CheckConstraint(
            "(target_family_id IS NOT NULL AND target_creation_suggestion_id IS NULL) OR "
            "(target_family_id IS NULL AND target_creation_suggestion_id IS NOT NULL)",
            name="ck_family_change_one_target",
        ),
        CheckConstraint(
            "original_family_id IS NULL OR target_family_id IS NULL OR "
            "original_family_id != target_family_id",
            name="ck_family_change_distinct_target",
        ),
        CheckConstraint(
            "status IN ('pending','approved','rejected','applied','stale')",
            name="ck_family_change_status",
        ),
        CheckConstraint(
            "status NOT IN ('approved','rejected','applied') OR "
            "(reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL)",
            name="ck_family_change_review",
        ),
        CheckConstraint(
            "status != 'applied' OR (application_reference IS NOT NULL AND applied_at IS NOT NULL)",
            name="ck_family_change_applied",
        ),
        Index("ix_family_change_business_status", "business_id", "status", "created_at"),
        Index("ix_family_change_business_creation", "business_id", "target_creation_suggestion_id"),
        Index(
            "uq_family_change_open_product",
            "business_id",
            "product_id",
            unique=True,
            postgresql_where=text("status IN ('pending','approved')"),
            sqlite_where=text("status IN ('pending','approved')"),
        ),
    )
    product_id: Mapped[str] = mapped_column(String(64))
    interpretation_id: Mapped[str] = mapped_column(String(64))
    original_family_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    original_family_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    target_family_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    target_creation_suggestion_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_hash: Mapped[str] = mapped_column(String(64))
    source_snapshot: Mapped[dict] = mapped_column(JSON().with_variant(JSONB, "postgresql"))


class CatalogFamilySuggestionEventModel(Base):
    __tablename__ = "catalog_family_suggestion_events"
    __table_args__ = (
        UniqueConstraint("business_id", "idempotency_key", name="uq_family_event_business_key"),
        ForeignKeyConstraint(
            ["business_id", "change_suggestion_id"],
            [
                "catalog_family_change_suggestions.business_id",
                "catalog_family_change_suggestions.id",
            ],
        ),
        ForeignKeyConstraint(
            ["business_id", "creation_suggestion_id"],
            [
                "catalog_family_creation_suggestions.business_id",
                "catalog_family_creation_suggestions.id",
            ],
        ),
        ForeignKeyConstraint(["business_id", "actor_id"], ["users.business_id", "users.id"]),
        CheckConstraint(
            "(change_suggestion_id IS NOT NULL AND creation_suggestion_id IS NULL) OR "
            "(change_suggestion_id IS NULL AND creation_suggestion_id IS NOT NULL)",
            name="ck_family_event_one_subject",
        ),
        CheckConstraint(
            "(actor_type = 'system' AND actor_id IS NULL) OR "
            "(actor_type = 'user' AND actor_id IS NOT NULL)",
            name="ck_family_event_actor",
        ),
        Index("ix_family_event_business_created", "business_id", "created_at"),
    )
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    business_id: Mapped[str] = mapped_column(String(64), ForeignKey("businesses.id"))
    change_suggestion_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    creation_suggestion_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    event_type: Mapped[str] = mapped_column(String(64))
    actor_type: Mapped[str] = mapped_column(String(16))
    actor_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    before: Mapped[dict] = mapped_column(JSON().with_variant(JSONB, "postgresql"))
    after: Mapped[dict] = mapped_column(JSON().with_variant(JSONB, "postgresql"))
    reason: Mapped[str] = mapped_column(Text)
    idempotency_key: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class SupplierModel(Base):
    __tablename__ = "suppliers"
    __table_args__ = (
        UniqueConstraint("business_id", "normalized_name", name="uq_suppliers_business_name"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    business_id: Mapped[str] = mapped_column(String(64), ForeignKey("businesses.id"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    normalized_name: Mapped[str] = mapped_column(String(255))
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class SupplierProductModel(Base):
    __tablename__ = "supplier_products"
    __table_args__ = (
        Index("ix_supplier_products_business_product", "business_id", "product_id"),
        Index("ix_supplier_products_business_supplier", "business_id", "supplier_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    business_id: Mapped[str] = mapped_column(String(64), ForeignKey("businesses.id"), index=True)
    supplier_id: Mapped[str] = mapped_column(String(64), ForeignKey("suppliers.id"), index=True)
    product_id: Mapped[str] = mapped_column(String(64), ForeignKey("products.id"), index=True)
    supplier_product_name: Mapped[str] = mapped_column(String(500))
    supplier_product_normalized_name: Mapped[str] = mapped_column(String(500))
    cost_price: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    currency: Mapped[str] = mapped_column(String(8), default="ARS")
    tax_included: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    package_quantity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    price_type: Mapped[str] = mapped_column(String(16), default="unit", server_default="unit")
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    metadata_: Mapped[dict[str, object]] = mapped_column(
        "metadata", JSON().with_variant(JSONB, "postgresql"), default=dict
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class CatalogImportModel(Base):
    __tablename__ = "catalog_imports"
    __table_args__ = (Index("ix_catalog_imports_business_created", "business_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    business_id: Mapped[str] = mapped_column(String(64), ForeignKey("businesses.id"), index=True)
    source_filename: Mapped[str] = mapped_column(String(500))
    source_type: Mapped[str] = mapped_column(String(64))
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    imported_count: Mapped[int] = mapped_column(Integer, default=0)
    skipped_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(32), index=True)
    errors: Mapped[list[object]] = mapped_column(JSON().with_variant(JSONB, "postgresql"))
    summary: Mapped[dict[str, object]] = mapped_column(JSON().with_variant(JSONB, "postgresql"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SupplierOfferDocumentModel(Base):
    __tablename__ = "supplier_offer_documents"
    __table_args__ = (
        Index("ix_supplier_offer_documents_business_created", "business_id", "created_at"),
        Index("ix_supplier_offer_documents_business_supplier", "business_id", "supplier_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    business_id: Mapped[str] = mapped_column(String(64), ForeignKey("businesses.id"), index=True)
    supplier_id: Mapped[str] = mapped_column(String(64), ForeignKey("suppliers.id"), index=True)
    source_filename: Mapped[str] = mapped_column(String(500))
    document_type: Mapped[str] = mapped_column(String(64))
    extraction_status: Mapped[str] = mapped_column(String(32), index=True)
    extraction_provider: Mapped[str] = mapped_column(String(64))
    raw_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_: Mapped[dict[str, object]] = mapped_column(
        "metadata", JSON().with_variant(JSONB, "postgresql"), default=dict
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SupplierOfferItemModel(Base):
    __tablename__ = "supplier_offer_items"
    __table_args__ = (
        Index(
            "ix_supplier_offer_items_business_document",
            "business_id",
            "supplier_offer_document_id",
        ),
        Index("ix_supplier_offer_items_business_supplier", "business_id", "supplier_id"),
        Index("ix_supplier_offer_items_business_normalized_name", "business_id", "normalized_name"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    business_id: Mapped[str] = mapped_column(String(64), ForeignKey("businesses.id"), index=True)
    supplier_offer_document_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("supplier_offer_documents.id"), index=True
    )
    supplier_id: Mapped[str] = mapped_column(String(64), ForeignKey("suppliers.id"), index=True)
    raw_name: Mapped[str] = mapped_column(String(500))
    normalized_name: Mapped[str] = mapped_column(String(500))
    brand: Mapped[str | None] = mapped_column(String(255), nullable=True)
    unit_size: Mapped[Decimal | None] = mapped_column(Numeric(12, 3), nullable=True)
    unit: Mapped[str | None] = mapped_column(String(32), nullable=True)
    package_quantity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    offer_price: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    price_type: Mapped[str] = mapped_column(String(16), default="unit", server_default="unit")
    currency: Mapped[str] = mapped_column(String(8), default="ARS")
    tax_included: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    confidence_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 4), nullable=True)
    metadata_: Mapped[dict[str, object]] = mapped_column(
        "metadata", JSON().with_variant(JSONB, "postgresql"), default=dict
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ProductMatchCandidateModel(Base):
    __tablename__ = "product_match_candidates"
    __table_args__ = (
        Index(
            "ix_product_match_candidates_business_document",
            "business_id",
            "supplier_offer_document_id",
        ),
        Index(
            "ix_product_match_candidates_business_item",
            "business_id",
            "supplier_offer_item_id",
        ),
        Index("ix_product_match_candidates_business_status", "business_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    business_id: Mapped[str] = mapped_column(String(64), ForeignKey("businesses.id"), index=True)
    supplier_offer_document_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("supplier_offer_documents.id"), index=True
    )
    supplier_offer_item_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("supplier_offer_items.id"), index=True
    )
    product_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("products.id"), nullable=True, index=True
    )
    relationship_type: Mapped[str] = mapped_column(String(64))
    confidence_score: Mapped[Decimal] = mapped_column(Numeric(5, 4))
    reasons: Mapped[list[object]] = mapped_column(JSON().with_variant(JSONB, "postgresql"))
    cost_difference: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    cost_difference_percentage: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    estimated_margin_percentage: Mapped[Decimal | None] = mapped_column(
        Numeric(8, 2), nullable=True
    )
    recommendation: Mapped[str] = mapped_column(String(32), default="review")
    status: Mapped[str] = mapped_column(String(32), index=True, default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class ProductMatchFeedbackModel(Base):
    __tablename__ = "product_match_feedback"
    __table_args__ = (
        Index(
            "ix_product_match_feedback_business_candidate",
            "business_id",
            "product_match_candidate_id",
        ),
        Index(
            "ix_product_match_feedback_business_item",
            "business_id",
            "supplier_offer_item_id",
        ),
        Index(
            "ix_product_match_feedback_business_product",
            "business_id",
            "candidate_product_id",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    business_id: Mapped[str] = mapped_column(String(64), ForeignKey("businesses.id"), index=True)
    product_match_candidate_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("product_match_candidates.id"), index=True
    )
    supplier_offer_item_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("supplier_offer_items.id"), index=True
    )
    candidate_product_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("products.id"), nullable=True, index=True
    )
    relationship_type: Mapped[str] = mapped_column(String(64))
    accepted: Mapped[bool] = mapped_column(Boolean)
    reviewed_by_user_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class SalesImportModel(Base):
    __tablename__ = "sales_imports"
    __table_args__ = (
        Index("ix_sales_imports_business_created", "business_id", "created_at"),
        Index("ix_sales_imports_business_period", "business_id", "period_start", "period_end"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    business_id: Mapped[str] = mapped_column(String(64), ForeignKey("businesses.id"), index=True)
    source_filename: Mapped[str] = mapped_column(String(500))
    source_type: Mapped[str] = mapped_column(String(64), default="pos_sales_items_csv")
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    imported_count: Mapped[int] = mapped_column(Integer, default=0)
    skipped_count: Mapped[int] = mapped_column(Integer, default=0)
    unique_ticket_count: Mapped[int] = mapped_column(Integer, default=0)
    unique_product_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(32), index=True)
    errors: Mapped[list[object]] = mapped_column(JSON().with_variant(JSONB, "postgresql"))
    summary: Mapped[dict[str, object]] = mapped_column(JSON().with_variant(JSONB, "postgresql"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SalesItemLineModel(Base):
    __tablename__ = "sales_item_lines"
    __table_args__ = (
        UniqueConstraint(
            "business_id",
            "external_sale_item_id",
            name="uq_sales_item_lines_business_external_item",
        ),
        Index("ix_sales_item_lines_business_sold_at", "business_id", "sold_at"),
        Index("ix_sales_item_lines_business_sale_date", "business_id", "sale_date"),
        Index(
            "ix_sales_item_lines_business_product_date",
            "business_id",
            "product_id",
            "sale_date",
        ),
        Index(
            "ix_sales_item_lines_business_external_product_date",
            "business_id",
            "external_product_id",
            "sale_date",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    business_id: Mapped[str] = mapped_column(String(64), ForeignKey("businesses.id"), index=True)
    source_import_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("sales_imports.id"), index=True
    )
    product_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("products.id"), nullable=True, index=True
    )
    external_sale_item_id: Mapped[str] = mapped_column(String(128))
    external_sale_id: Mapped[str] = mapped_column(String(128), index=True)
    external_product_id: Mapped[str] = mapped_column(String(128))
    sku: Mapped[str | None] = mapped_column(String(128), nullable=True)
    sold_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    sale_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    quantity: Mapped[Decimal] = mapped_column(Numeric(14, 3), default=0)
    revenue: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    unit_cost: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    cost_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    gross_margin: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    raw_payload: Mapped[dict[str, object]] = mapped_column(JSON().with_variant(JSONB, "postgresql"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ProductSalesDailyModel(Base):
    __tablename__ = "product_sales_daily"
    __table_args__ = (
        UniqueConstraint(
            "business_id",
            "external_product_id",
            "sale_date",
            name="uq_product_sales_daily_business_product_date",
        ),
        Index(
            "ix_product_sales_daily_business_product_date",
            "business_id",
            "product_id",
            "sale_date",
        ),
        Index(
            "ix_product_sales_daily_business_external_date",
            "business_id",
            "external_product_id",
            "sale_date",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    business_id: Mapped[str] = mapped_column(String(64), ForeignKey("businesses.id"), index=True)
    source_import_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("sales_imports.id"), index=True
    )
    product_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("products.id"), nullable=True, index=True
    )
    external_product_id: Mapped[str] = mapped_column(String(128))
    sku: Mapped[str | None] = mapped_column(String(128), nullable=True)
    sale_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    quantity_sold: Mapped[Decimal] = mapped_column(Numeric(14, 3), default=0)
    revenue: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    cost_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    gross_margin: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    ticket_count: Mapped[int] = mapped_column(Integer, default=0)
    line_count: Mapped[int] = mapped_column(Integer, default=0)
    zero_cost_line_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )
