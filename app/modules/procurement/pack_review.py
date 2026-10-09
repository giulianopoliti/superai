"""Validate a reader's interpretation of the price basis before normalization."""

from typing import Literal

from pydantic import BaseModel, Field

from app.modules.procurement.normalization import parse_package_quantity
from app.modules.procurement.pricing import SupplierPriceType
from app.modules.procurement.schemas import ExtractedSupplierOfferItem


class PackReviewDecision(BaseModel):
    price_type: Literal["unit", "pack", "unknown"]
    package_quantity: int | None
    retail_contents_count: int | None
    confidence: float = Field(ge=0, le=1)
    evidence: str
    reason: str


class IndexedPackReviewDecision(PackReviewDecision):
    item_index: int = Field(ge=0)


class PackReviewBatch(BaseModel):
    decisions: list[IndexedPackReviewDecision]


def needs_pack_review(item: ExtractedSupplierOfferItem) -> bool:
    return (
        item.price_type in {SupplierPriceType.PACK, SupplierPriceType.UNKNOWN}
        or bool(item.package_quantity and item.package_quantity > 1)
        or parse_package_quantity(item.raw_name) is not None
    )


def apply_pack_review(item, decision: PackReviewDecision | None, *, error=None):
    original = {
        "price_type": item.price_type.value,
        "package_quantity": item.package_quantity,
    }
    confirmed = bool(
        decision and decision.confidence >= 0.90 and decision.evidence.strip()
        and (
            decision.price_type == "unit" and decision.package_quantity is None
            or decision.price_type == "pack" and decision.package_quantity is not None
            and 2 <= decision.package_quantity <= 1000
        )
    )
    item.pack_review = {
        "status": "confirmed" if confirmed else "needs_review",
        "original": original,
        "decision": decision.model_dump(mode="json") if decision else None,
        "error": error,
    }
    # Never let x48 contents become 48 sellable envelopes by a regex fallback.
    item.price_type = (
        SupplierPriceType(decision.price_type) if confirmed else SupplierPriceType.UNKNOWN
    )
    item.package_quantity = decision.package_quantity if confirmed else None
