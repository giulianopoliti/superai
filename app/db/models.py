from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
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


class ProductModel(Base):
    __tablename__ = "products"
    __table_args__ = (
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
    __table_args__ = (
        Index("ix_catalog_imports_business_created", "business_id", "created_at"),
    )

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
    cost_difference_percentage: Mapped[Decimal | None] = mapped_column(
        Numeric(8, 2), nullable=True
    )
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
