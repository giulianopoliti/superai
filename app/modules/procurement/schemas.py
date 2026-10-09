from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated
from uuid import uuid4

from pydantic import BaseModel, Field, WithJsonSchema, computed_field

from app.modules.procurement.analysis_contracts import AnalysisSummary, PendingBlock
from app.modules.procurement.pricing import SupplierPriceType, normalize_offer_price
from app.modules.sales.schemas import ProductSalesSummary


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
    family_id: str | None = None
    external_family_id: str | None = None
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


class ProductFamily(BaseModel):
    id: str = Field(default_factory=new_id)
    business_id: str
    external_family_id: str
    name: str
    normalized_name: str
    active: bool = True
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
    price_type: SupplierPriceType = SupplierPriceType.UNIT
    observed_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, object] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @computed_field
    @property
    def comparable_unit_cost(self) -> Decimal | None:
        return normalize_offer_price(
            offer_price=self.cost_price,
            package_quantity=self.package_quantity,
            price_type=self.price_type,
        )


class ProductSupplierPrice(BaseModel):
    supplier_product_id: str
    supplier_id: str
    supplier_name: str
    product_id: str
    product_name: str
    supplier_product_name: str
    cost_price: Decimal
    currency: str
    tax_included: bool | None = None
    package_quantity: int | None = None
    price_type: SupplierPriceType = SupplierPriceType.UNIT
    observed_at: datetime

    @computed_field
    @property
    def comparable_unit_cost(self) -> Decimal | None:
        return normalize_offer_price(
            offer_price=self.cost_price,
            package_quantity=self.package_quantity,
            price_type=self.price_type,
        )


class ProductSupplierComparison(BaseModel):
    product_id: str
    product_name: str
    current_cost: Decimal | None
    sale_price: Decimal | None
    supplier_prices: list[ProductSupplierPrice]

    @property
    def best_supplier_price(self) -> ProductSupplierPrice | None:
        comparable_prices = [
            price for price in self.supplier_prices if price.comparable_unit_cost is not None
        ]
        if not comparable_prices:
            return None
        return min(
            comparable_prices,
            key=lambda price: price.comparable_unit_cost or Decimal("Infinity"),
        )


class SupplierPriceUpsertItem(BaseModel):
    product_id: str
    cost_price: Decimal
    supplier_product_name: str | None = None
    currency: str = "ARS"
    tax_included: bool | None = None
    package_quantity: int | None = None
    price_type: SupplierPriceType = SupplierPriceType.UNIT
    observed_at: datetime | None = None
    metadata: dict[str, object] = Field(default_factory=dict)


class SupplierPriceUpsertRequest(BaseModel):
    business_id: str
    supplier_name: str
    items: list[SupplierPriceUpsertItem]


class SupplierPriceUpsertResponse(BaseModel):
    supplier: Supplier
    prices: list[SupplierProduct]


class SupplierPriceComparisonNeed(BaseModel):
    product_id: str
    quantity: Decimal | None = None


class SupplierPriceComparisonRequest(BaseModel):
    business_id: str
    product_ids: list[str] = Field(default_factory=list)
    needs: list[SupplierPriceComparisonNeed] = Field(default_factory=list)
    supplier_ids: list[str] = Field(default_factory=list)


class SupplierPriceComparisonCell(BaseModel):
    supplier_product_id: str
    supplier_id: str
    supplier_name: str
    supplier_product_name: str
    cost_price: Decimal | None
    quoted_cost_price: Decimal
    currency: str
    tax_included: bool | None = None
    package_quantity: int | None = None
    price_type: SupplierPriceType = SupplierPriceType.UNIT
    price_was_inferred: bool = False
    observed_at: datetime
    is_best: bool = False
    unit_savings_vs_system: Decimal | None = None


class SupplierPriceComparisonRow(BaseModel):
    product_id: str
    product_name: str
    current_cost: Decimal | None
    sale_price: Decimal | None
    quantity: Decimal | None = None
    supplier_prices: list[SupplierPriceComparisonCell]
    missing_supplier_ids: list[str] = Field(default_factory=list)
    best_supplier_id: str | None = None
    best_supplier_name: str | None = None
    best_cost_price: Decimal | None = None
    estimated_best_total: Decimal | None = None
    estimated_savings_vs_system: Decimal | None = None


class SupplierPriceComparisonResponse(BaseModel):
    business_id: str
    supplier_ids: list[str]
    rows: list[SupplierPriceComparisonRow]

    @computed_field
    @property
    def compared_product_count(self) -> int:
        return len(self.rows)


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


class CatalogSyncStatus(BaseModel):
    total_products: int = 0
    last_import: CatalogImportResult | None = None


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


class SupplierOfferDocumentSummary(BaseModel):
    document: SupplierOfferDocument
    supplier_name: str
    item_count: int = 0
    candidate_count: int = 0
    matched_count: int = 0
    pending_count: int = 0
    accepted_count: int = 0
    rejected_count: int = 0
    buy_count: int = 0
    review_count: int = 0
    do_not_buy_count: int = 0


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
    price_type: SupplierPriceType = SupplierPriceType.UNIT
    currency: str = "ARS"
    tax_included: bool | None = None
    page_number: int | None = None
    confidence_score: Decimal | None = None
    metadata: dict[str, object] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)

    @computed_field
    @property
    def comparable_unit_price(self) -> Decimal | None:
        return normalize_offer_price(
            offer_price=self.offer_price,
            package_quantity=self.package_quantity,
            price_type=self.price_type,
        )


class SupplierOfferImportResult(BaseModel):
    document: SupplierOfferDocument
    items: list[SupplierOfferItem]


ReaderDecimal = Annotated[Decimal, WithJsonSchema({"type": "number"}, mode="validation")]


class SupplierOfferObservation(BaseModel):
    """Reader output, without backend reconciliation or arbitrary JSON metadata."""
    raw_name: str
    brand: str | None = None
    unit_size: ReaderDecimal | None = None
    unit: str | None = None
    package_quantity: int | None = None
    offer_price: ReaderDecimal
    currency: str = "ARS"
    tax_included: bool | None = None
    price_type: SupplierPriceType = SupplierPriceType.UNIT
    page_number: int | None = None
    confidence_score: ReaderDecimal = Decimal("0.50")
    notes: str | None = None


class ExtractedSupplierOfferItem(SupplierOfferObservation):
    pack_review: dict[str, object] = Field(default_factory=dict)
    agreement_score: Decimal | None = Field(default=None, ge=0, le=1)
    agreement_status: str = "single_source"
    selected_source: str | None = None
    resolved_by: str = "deterministic"
    provider_observations: dict[str, object] = Field(default_factory=dict)


class SupplierOfferPageCoverage(BaseModel):
    estimated_offer_blocks: int | None = None
    extracted_item_count: int = 0
    needs_review: bool = False
    notes: str | None = None


class SupplierOfferPageExtraction(BaseModel):
    page_number: int
    items: list[ExtractedSupplierOfferItem] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    coverage: SupplierOfferPageCoverage = Field(default_factory=SupplierOfferPageCoverage)


class SupplierOfferExtraction(BaseModel):
    supplier_name: str | None = None
    source_filename: str | None = None
    raw_text: str | None = None
    items: list[ExtractedSupplierOfferItem]
    warnings: list[str] = Field(default_factory=list)
    page_extractions: list[SupplierOfferPageExtraction] = Field(default_factory=list)
    document_metadata: dict[str, object] = Field(default_factory=dict)


class SupplierOfferReaderPage(BaseModel):
    page_number: int | None = None
    items: list[SupplierOfferObservation] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    coverage: SupplierOfferPageCoverage = Field(default_factory=SupplierOfferPageCoverage)


class SupplierOfferReaderExtraction(BaseModel):
    """Closed structured output shared by the two readers (local page numbers)."""

    supplier_name: str | None = None
    page_extractions: list[SupplierOfferReaderPage]
    warnings: list[str] = Field(default_factory=list)


class ProductMatchCandidate(BaseModel):
    auto_accepted: bool = False
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


class SupplierOfferPurchasePlanItem(BaseModel):
    candidate: ProductMatchCandidateRecord
    supplier_offer_item: SupplierOfferItem
    product: Product | None = None
    sales_summary: ProductSalesSummary | None = None
    target_coverage_days: int | None = None
    suggested_quantity: Decimal | None = None
    estimated_savings: Decimal | None = None


class SupplierOfferPurchasePlan(BaseModel):
    business_id: str
    supplier_offer_document_id: str
    date_from: date
    date_to: date
    target_coverage_days: int | None = None
    use_stock: bool = False
    ignore_negative_stock: bool = True
    sort_by: str = "sales_then_savings"
    only_positive_savings: bool = False
    items: list[SupplierOfferPurchasePlanItem]


class SupplierOfferComparisonReport(BaseModel):
    business_id: str
    supplier_offer_document_id: str
    candidates: list[ProductMatchCandidate]

    @computed_field
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


class SupplierOfferDocumentAnalysisResponse(BaseModel):
    import_result: SupplierOfferImportResult
    comparison: SupplierOfferCompareResponse
    extraction_warnings: list[str] = Field(default_factory=list)
    analysis: AnalysisSummary | None = None
    pending_blocks: list[PendingBlock] = Field(default_factory=list)
