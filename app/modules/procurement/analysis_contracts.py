"""Shared V2 analysis contracts; independent of required-price legacy offers."""

from decimal import Decimal
from enum import StrEnum
from math import isfinite
from pathlib import Path
from typing import Annotated, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, WithJsonSchema, model_validator

CONTRACT_VERSION = "supplier_offer_analysis_v1"
WIRE_VERSION = "supplier_offer_reader_v2"
ReaderDecimal = Annotated[Decimal, WithJsonSchema({"type": "number"}, mode="validation")]


class AnalysisMethod(StrEnum):
    OPENAI_STRUCTURED = "openai_structured"
    OPENAI_BLOCKS_JEV = "openai_blocks_jev"


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class ReaderFragmentV2(ClosedModel):
    text: str
    legibility: Literal["readable", "partial", "unreadable"]


class ReaderEvidenceRefV2(ClosedModel):
    scope: Literal["block", "page_condition"]
    fragment_index: int = Field(ge=0)
    quote: str


class ReaderFieldEvidenceV2(ClosedModel):
    field: Literal[
        "raw_name",
        "brand",
        "unit_size",
        "unit",
        "offer_price",
        "currency",
        "tax_included",
        "price_type",
        "package_quantity",
        "retail_contents_count",
        "minimum_purchase",
    ]
    refs: list[ReaderEvidenceRefV2]


class ReaderMinimumPurchaseV2(ClosedModel):
    value: ReaderDecimal | None
    unit: str | None
    scope: Literal["offer", "page", "unknown"]
    refs: list[ReaderEvidenceRefV2]


class ReaderOfferProposalV2(ClosedModel):
    raw_name: str | None
    brand: str | None
    unit_size: ReaderDecimal | None
    unit: Literal["g", "kg", "ml", "l", "unit"] | None
    offer_price: ReaderDecimal | None
    currency: str | None
    tax_included: bool | None
    price_type: Literal["unit", "pack", "unknown"]
    package_quantity: int | None
    retail_contents_count: int | None
    minimum_purchase: ReaderMinimumPurchaseV2 | None
    confidence_score: ReaderDecimal | None
    field_evidence: list[ReaderFieldEvidenceV2]
    warnings: list[str]


class ReaderBlockV2(ClosedModel):
    fragments: list[ReaderFragmentV2]
    page_condition_indices: list[int]
    warnings: list[str]


class ReaderStructuredBlockV2(ReaderBlockV2):
    proposals: list[ReaderOfferProposalV2]


class ReaderCoverageV2(ClosedModel):
    estimated_offer_blocks: int | None
    needs_visual_review: bool
    warnings: list[str]


class ReaderPageV2(ClosedModel):
    page_number: int | None
    page_conditions: list[ReaderFragmentV2]
    blocks: list[ReaderBlockV2]
    coverage: ReaderCoverageV2


class ReaderStructuredPageV2(ReaderPageV2):
    blocks: list[ReaderStructuredBlockV2]


class ReaderBlocksExtractionV2(ClosedModel):
    supplier_name: str | None
    pages: list[ReaderPageV2]
    warnings: list[str]


class ReaderStructuredExtractionV2(ClosedModel):
    supplier_name: str | None
    pages: list[ReaderStructuredPageV2]
    warnings: list[str]


class TokenUsage(ClosedModel):
    input_tokens: int | None = Field(default=None, ge=0)
    cached_input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    reasoning_output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)


class AnalysisCost(ClosedModel):
    amount: Decimal = Field(ge=0)
    currency: str
    tariff_id: str
    estimated: bool


class ProviderCallRecord(ClosedModel):
    call_id: str
    run_id: str
    reading_id: str
    provider: str
    model: str
    prompt_version: str
    schema_version: str
    analysis_method: AnalysisMethod
    phase: Literal["initial", "reread", "classification", "decision"]
    start_page: int | None = None
    end_page: int | None = None
    response_id: str | None = None
    duration_seconds: float | None = Field(default=None, ge=0)
    status: Literal["success", "error", "refusal", "incomplete"]
    safe_error: str | None = None
    usage: TokenUsage | None = None
    cost: AnalysisCost | None = None


class DocumentReadRequest(ClosedModel):
    document_path: Path
    run_id: str
    reading_id: str
    analysis_method: AnalysisMethod
    phase: Literal["initial", "reread"]
    source_filename: str
    start_page: int
    end_page: int
    supplier_hint: str | None = None
    timeout_seconds: float = Field(gt=0)


class DocumentReadResult(ClosedModel):
    extraction: ReaderStructuredExtractionV2 | ReaderBlocksExtractionV2 | None
    call: ProviderCallRecord


class AnalysisDocumentReader(Protocol):
    def read(self, *, request: DocumentReadRequest) -> DocumentReadResult: ...


class EvidenceFragment(ClosedModel):
    fragment_id: str
    text: str
    order: int = Field(ge=0)
    legibility: Literal["readable", "partial", "unreadable"]


class EvidenceRef(ClosedModel):
    fragment_id: str
    quote: str
    start: int | None = Field(default=None, ge=0)
    end: int | None = Field(default=None, ge=0)


class MinimumPurchase(ClosedModel):
    value: Decimal | None = None
    unit: str | None = None
    scope: Literal["offer", "page", "unknown"] = "unknown"
    refs: list[EvidenceRef] = Field(default_factory=list)


class OfferTextBlock(ClosedModel):
    block_id: str
    run_id: str
    reading_id: str
    page_number: int | None
    order_on_page: int = Field(ge=0)
    full_text: str
    fragments: list[EvidenceFragment]
    page_conditions: list[EvidenceFragment] = Field(default_factory=list)
    condition_refs: list[str] = Field(default_factory=list)
    proposals: list[ReaderOfferProposalV2] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)
    derived_offer_ids: list[str] = Field(default_factory=list)
    supersedes_block_id: str | None = None

    @model_validator(mode="after")
    def canonical_text(self):
        if self.full_text != "\n".join(fragment.text for fragment in self.fragments):
            raise ValueError("full_text must be the ordered canonical fragment composition")
        ids = [f.fragment_id for f in self.fragments + self.page_conditions]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate evidence fragment IDs")
        if not set(self.condition_refs) <= {f.fragment_id for f in self.page_conditions}:
            raise ValueError("Condition reference outside this page")
        return self


class ValueCandidate(ClosedModel):
    candidate_id: str
    block_id: str
    fragment_id: str
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    literal: str
    kind: Literal["number", "money", "quantity", "size", "text"]
    value: Decimal | None = None
    unit: str | None = None


FragmentCategory = Literal[
    "product_name",
    "brand",
    "variant",
    "retail_size",
    "retail_contents",
    "commercial_quantity",
    "current_price",
    "previous_price",
    "reference_price",
    "minimum_purchase",
    "promotion",
    "tax_condition",
    "other",
    "uncertain",
    "mixed",
]


class ChoiceQuestion(ClosedModel):
    question_id: str
    options: list[str]
    prompt: str

    @model_validator(mode="after")
    def unique_options(self):
        if not self.options or len(set(self.options)) != len(self.options):
            raise ValueError("Question options must be nonempty and unique")
        return self


class ChoiceDecision(ClosedModel):
    question_id: str
    choice: str
    confidence: float = Field(ge=0, le=1)
    probabilities: dict[str, float]
    model: str
    prompt_version: str

    @model_validator(mode="after")
    def valid_distribution(self):
        values = list(self.probabilities.values())
        if (
            self.choice not in self.probabilities
            or not values
            or any(not isfinite(v) or not 0 <= v <= 1 for v in values)
            or abs(sum(values) - 1) > 0.001
        ):
            raise ValueError("Invalid probability distribution")
        return self


class FragmentClassification(ClosedModel):
    fragment_id: str
    category: FragmentCategory
    decision: ChoiceDecision


class BlockClassificationRequest(ClosedModel):
    business_id: str
    block: OfferTextBlock
    candidates: list[ValueCandidate]
    questions: list[ChoiceQuestion]


class BlockClassificationResult(ClosedModel):
    run_id: str
    block_id: str
    decisions: list[ChoiceDecision] = Field(default_factory=list)
    classifications: list[FragmentClassification] = Field(default_factory=list)
    calls: list[ProviderCallRecord] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)


class BlockClassifier(Protocol):
    def classify_block(
        self, *, request: BlockClassificationRequest
    ) -> BlockClassificationResult: ...


class OfferInterpretation(ClosedModel):
    offer_id: str
    block_id: str
    business_id: str
    raw_name: str | None = None
    brand: str | None = None
    unit_size: Decimal | None = None
    unit: str | None = None
    offer_price: Decimal | None = None
    currency: str | None = None
    tax_included: bool | None = None
    price_type: Literal["unit", "pack", "unknown"] = "unknown"
    package_quantity: int | None = None
    retail_contents_count: int | None = None
    minimum_purchase: MinimumPurchase | None = None
    field_evidence: dict[str, list[EvidenceRef]] = Field(default_factory=dict)
    identity_status: Literal["confirmed", "pending", "no_match"] = "pending"
    price_status: Literal["confirmed", "pending"] = "pending"
    product_id: str | None = None
    reason_codes: list[str] = Field(default_factory=list)
    needs_visual_review: bool = False


class BlockInterpretationResult(ClosedModel):
    block_id: str
    offers: list[OfferInterpretation] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)
    needs_visual_review: bool = False


class BlockAssembler(Protocol):
    def assemble_interpretation(
        self,
        *,
        business_id: str,
        block: OfferTextBlock,
        classification: BlockClassificationResult | None,
        candidates: list[ValueCandidate],
    ) -> BlockInterpretationResult: ...


class IdentityCandidate(ClosedModel):
    candidate_id: str
    product_id: str
    business_id: str
    name: str
    brand: str | None = None
    unit_size: Decimal | None = None
    unit: str | None = None
    reason_codes: list[str] = Field(default_factory=list)


class OfferDecisionRequest(ClosedModel):
    business_id: str
    block: OfferTextBlock
    offer: OfferInterpretation
    candidates: list[IdentityCandidate]
    questions: list[ChoiceQuestion]


class OfferDecisionResult(ClosedModel):
    offer_id: str
    decisions: list[ChoiceDecision] = Field(default_factory=list)
    calls: list[ProviderCallRecord] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)


class OfferDecisionProvider(Protocol):
    def decide_offer(self, *, request: OfferDecisionRequest) -> OfferDecisionResult: ...


class PolicyResult(ClosedModel):
    offer: OfferInterpretation
    route: Literal["resolved", "needs_jev", "needs_visual_review", "needs_human_review"]
    reason_codes: list[str] = Field(default_factory=list)


class AnalysisPolicy(Protocol):
    def prepare_request(self, *, request: OfferDecisionRequest) -> OfferDecisionRequest: ...

    def validate_decision(
        self, *, request: OfferDecisionRequest, decision: OfferDecisionResult | None
    ) -> PolicyResult: ...


class PendingBlock(ClosedModel):
    block_id: str
    page_number: int | None
    order_on_page: int
    text: str
    reason_codes: list[str]
    reason: str


class AnalysisSummary(ClosedModel):
    contract_version: str = CONTRACT_VERSION
    run_id: str
    method: AnalysisMethod
    status: Literal["completed", "partial"]
    page_count: int = Field(ge=0)
    observed_block_count: int = Field(ge=0)
    interpreted_offer_count: int = Field(ge=0)
    pending_block_count: int = Field(ge=0)
    unlinked_offer_count: int = Field(default=0, ge=0)
    identity_review_offer_count: int = Field(default=0, ge=0)
    price_review_offer_count: int = Field(default=0, ge=0)
    # None preserves unknown coverage on historical analyses without a ledger.
    read_page_count: int | None = Field(default=None, ge=0)
    attempted_page_count: int | None = Field(default=None, ge=0)
    unattempted_page_numbers: list[int] | None = None
    failed_page_numbers: list[int] | None = None
    provider_call_count: int | None = Field(default=None, ge=0)
    local_rejection_count: int | None = Field(default=None, ge=0)
    review_page_numbers: list[int] = Field(default_factory=list)
    duration_seconds: float | None = Field(default=None, ge=0)
    usage: TokenUsage | None = None
    cost: AnalysisCost | None = None


class AnalysisMethodCapability(ClosedModel):
    id: AnalysisMethod
    available: bool
    unavailable_reason_code: str | None = None
    unavailable_reason: str | None = None


class AnalysisMethodsResponse(ClosedModel):
    contract_version: str = CONTRACT_VERSION
    default_method: AnalysisMethod = AnalysisMethod.OPENAI_STRUCTURED
    methods: list[AnalysisMethodCapability]


class DocumentAnalysisDetail(ClosedModel):
    document_id: str
    analysis: AnalysisSummary | None = None
    pending_blocks: list[PendingBlock] = Field(default_factory=list)


class PageRecoveryRecord(ClosedModel):
    page_number: int
    initial_done: bool = False
    reread_count: int = Field(default=0, ge=0, le=1)
    reason_codes: list[str] = Field(default_factory=list)
    reading_ids: list[str] = Field(default_factory=list)
    status: Literal["pending", "read", "recovered", "exhausted"] = "pending"
    attempt_reasons: dict[str, list[str]] = Field(default_factory=dict)


class DecisionAuditRecord(ClosedModel):
    run_id: str
    reading_id: str
    block_id: str
    offer_id: str | None = None
    phase: Literal["classification", "decision"]
    questions: list[ChoiceQuestion] = Field(default_factory=list)
    decisions: list[ChoiceDecision] = Field(default_factory=list)
    classifications: list[FragmentClassification] = Field(default_factory=list)
    call_ids: list[str] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)
    status: Literal["success", "partial", "error"] = "error"


class AnalysisLedger(ClosedModel):
    contract_version: str = CONTRACT_VERSION
    run_id: str
    business_id: str
    method: AnalysisMethod
    blocks: list[OfferTextBlock] = Field(default_factory=list)
    interpretations: list[OfferInterpretation] = Field(default_factory=list)
    pending_blocks: list[PendingBlock] = Field(default_factory=list)
    pages: list[PageRecoveryRecord] = Field(default_factory=list)
    calls: list[ProviderCallRecord] = Field(default_factory=list)
    decision_audit: list[DecisionAuditRecord] = Field(default_factory=list)
    policy: dict[str, object] = Field(default_factory=dict)
    page_conditions: dict[str, list[EvidenceFragment]] = Field(default_factory=dict)
    invalid_evidence: list[dict[str, object]] = Field(default_factory=list)
