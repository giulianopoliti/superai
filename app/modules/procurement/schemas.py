from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(UTC)


def new_id() -> str:
    return str(uuid4())


class SupplierProductRelationship(StrEnum):
    EXACT_MATCH = "exact_match"
    SAME_PRODUCT_DIFFERENT_NAME = "same_product_different_name"
    COMPARABLE_ALTERNATIVE = "comparable_alternative"
    SIMILAR_BUT_NOT_EQUIVALENT = "similar_but_not_equivalent"
    NOT_SAME_PRODUCT = "not_same_product"
    NEW_PRODUCT = "new_product"
    UNKNOWN = "unknown"


class CatalogImportStatus(StrEnum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"


class SupplierOfferDocumentStatus(StrEnum):
    PENDING = "pending"
    EXTRACTED = "extracted"
    FAILED = "failed"


class ProductMatchCandidateStatus(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class Product(BaseModel):
    id: str = Field(default_factory=new_id)
    business_id: str
    name: str
    normalized_name: str
    external_product_id: str | None = None
    sku: str | None = None
    barcode: str | None = None
    brand: str | None = None
    category: str | None = None
    unit_size: Decimal | None = None
    unit: str | None = None
    sale_price: Decimal | None = None
    current_cost: Decimal | None = None
    margin_percentage: Decimal | None = None
    stock_quantity: Decimal | None = None
    active: bool = True
    source: str = "pos_csv"
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class Supplier(BaseModel):
    id: str = Field(default_factory=new_id)
    business_id: str
    name: str
    normalized_name: str
    notes: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class SupplierProduct(BaseModel):
    id: str = Field(default_factory=new_id)
    business_id: str
    supplier_id: str
    product_id: str
    supplier_product_name: str
    supplier_product_normalized_name: str
    cost_price: Decimal
    currency: str = "ARS"
    tax_included: bool | None = None
    package_quantity: int | None = None
    observed_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, object] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class ProductSupplierPrice(BaseModel):
    supplier_product_id: str
    supplier_id: str
    supplier_name: str
    product_id: str
    product_name: str
    supplier_product_name: str
    cost_price: Decimal
    currency: str
    observed_at: datetime


class ProductSupplierComparison(BaseModel):
    product_id: str
    product_name: str
    current_cost: Decimal | None
    sale_price: Decimal | None
    supplier_prices: list[ProductSupplierPrice]

    @property
    def best_supplier_price(self) -> ProductSupplierPrice | None:
        if not self.supplier_prices:
            return None
        return min(self.supplier_prices, key=lambda price: price.cost_price)


class CatalogImportResult(BaseModel):
    id: str = Field(default_factory=new_id)
    business_id: str
    source_filename: str
    source_type: str = "pos_csv"
    status: CatalogImportStatus = CatalogImportStatus.PENDING
    row_count: int = 0
    imported_count: int = 0
    skipped_count: int = 0
    errors: list[str] = Field(default_factory=list)
    summary: dict[str, object] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
    completed_at: datetime | None = None


class SupplierOfferDocument(BaseModel):
    id: str = Field(default_factory=new_id)
    business_id: str
    supplier_id: str
    source_filename: str
    document_type: str = "manual"
    extraction_status: SupplierOfferDocumentStatus = SupplierOfferDocumentStatus.PENDING
    extraction_provider: str = "manual"
    raw_text: str | None = None
    metadata: dict[str, object] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
    completed_at: datetime | None = None


class SupplierOfferItem(BaseModel):
    id: str = Field(default_factory=new_id)
    business_id: str
    supplier_offer_document_id: str
    supplier_id: str
    raw_name: str
    normalized_name: str
    brand: str | None = None
    unit_size: Decimal | None = None
    unit: str | None = None
    package_quantity: int | None = None
    offer_price: Decimal
    currency: str = "ARS"
    tax_included: bool | None = None
    page_number: int | None = None
    confidence_score: Decimal | None = None
    metadata: dict[str, object] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


class SupplierOfferImportResult(BaseModel):
    document: SupplierOfferDocument
    items: list[SupplierOfferItem]


class ExtractedSupplierOfferItem(BaseModel):
    raw_name: str
    brand: str | None = None
    unit_size: Decimal | None = None
    unit: str | None = None
    package_quantity: int | None = None
    offer_price: Decimal
    currency: str = "ARS"
    tax_included: bool | None = None
    price_type: str = "unit"
    page_number: int | None = None
    confidence_score: Decimal = Decimal("0.50")
    notes: str | None = None


class SupplierOfferExtraction(BaseModel):
    supplier_name: str | None = None
    source_filename: str | None = None
    raw_text: str | None = None
    items: list[ExtractedSupplierOfferItem]
    warnings: list[str] = Field(default_factory=list)


class ProductMatchCandidate(BaseModel):
    supplier_offer_item: SupplierOfferItem
    product: Product | None
    relationship_type: SupplierProductRelationship
    confidence_score: Decimal
    reasons: list[str] = Field(default_factory=list)
    cost_difference: Decimal | None = None
    cost_difference_percentage: Decimal | None = None
    estimated_margin_percentage: Decimal | None = None
    recommendation: str = "review"


class ProductMatchCandidateRecord(BaseModel):
    id: str = Field(default_factory=new_id)
    business_id: str
    supplier_offer_document_id: str
    supplier_offer_item_id: str
    product_id: str | None = None
    relationship_type: SupplierProductRelationship
    confidence_score: Decimal
    reasons: list[str] = Field(default_factory=list)
    cost_difference: Decimal | None = None
    cost_difference_percentage: Decimal | None = None
    estimated_margin_percentage: Decimal | None = None
    recommendation: str = "review"
    status: ProductMatchCandidateStatus = ProductMatchCandidateStatus.PENDING
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class ProductMatchFeedback(BaseModel):
    id: str = Field(default_factory=new_id)
    business_id: str
    product_match_candidate_id: str
    supplier_offer_item_id: str
    candidate_product_id: str | None = None
    relationship_type: SupplierProductRelationship
    accepted: bool
    reviewed_by_user_id: str | None = None
    notes: str | None = None
    created_at: datetime = Field(default_factory=utc_now)


class ProductMatchFeedbackRule(BaseModel):
    supplier_id: str
    supplier_offer_item_normalized_name: str
    candidate_product_id: str | None = None
    relationship_type: SupplierProductRelationship
    accepted: bool
    created_at: datetime


class ProductMatchReviewRequest(BaseModel):
    business_id: str
    reviewed_by_user_id: str | None = None
    notes: str | None = None
    relationship_type: SupplierProductRelationship | None = None


class ProductMatchCorrectionRequest(ProductMatchReviewRequest):
    product_id: str
    relationship_type: SupplierProductRelationship = (
        SupplierProductRelationship.SAME_PRODUCT_DIFFERENT_NAME
    )


class ProductMatchReviewCandidate(BaseModel):
    candidate: ProductMatchCandidateRecord
    supplier_offer_item: SupplierOfferItem
    product: Product | None = None


class ProductMatchReviewList(BaseModel):
    business_id: str
    supplier_offer_document_id: str
    candidates: list[ProductMatchReviewCandidate]


class SupplierOfferComparisonReport(BaseModel):
    business_id: str
    supplier_offer_document_id: str
    candidates: list[ProductMatchCandidate]

    @property
    def matched_count(self) -> int:
        return sum(1 for candidate in self.candidates if candidate.product is not None)


class CatalogImportPathRequest(BaseModel):
    business_id: str
    csv_path: str


class SupplierOfferJsonPathRequest(BaseModel):
    business_id: str
    json_path: str


class SupplierOfferCompareRequest(BaseModel):
    business_id: str
    max_candidates: int = 1
    persist_candidates: bool = True


class SupplierOfferCompareResponse(BaseModel):
    report: SupplierOfferComparisonReport
    persisted_count: int = 0
