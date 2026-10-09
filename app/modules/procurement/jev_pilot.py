"""Offline, read-only evaluation of dual extraction and closed-set matching.

No result from this module changes a product match or supplier price.
"""

from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal, Protocol

from pydantic import BaseModel, Field

from app.modules.procurement.normalization import normalize_text
from app.modules.procurement.schemas import (
    ExtractedSupplierOfferItem,
    ProductMatchCandidate,
    SupplierOfferExtraction,
    SupplierOfferItem,
)
from app.providers.documents.base import DocumentExtractionProvider


class LabeledOffer(BaseModel):
    """One manually verified offer block, not a model-generated label."""

    page_number: int = Field(ge=1)
    raw_name: str
    offer_price: Decimal = Field(gt=0)
    product_id: str | None = None
    currency: str = "ARS"


class PilotDataset(BaseModel):
    business_id: str = Field(min_length=1)
    supplier_id: str = Field(min_length=1)
    document_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    split: Literal["development", "blind"]
    labels: list[LabeledOffer] = Field(min_length=1)


class Observation(BaseModel):
    provider: str
    page_number: int
    ordinal: int
    item: ExtractedSupplierOfferItem


class ReconciledOffer(BaseModel):
    page_number: int
    observations: list[Observation]
    status: str  # agreed, single_source, conflict, ambiguous_duplicate


class DualExtraction(BaseModel):
    business_id: str
    document_sha256: str
    observations: list[Observation]
    offers: list[ReconciledOffer]
    errors: dict[str, str] = Field(default_factory=dict)


def reconcile(observations: list[Observation]) -> list[ReconciledOffer]:
    """Pair only unambiguous same-name/page rows; never vote on a price conflict."""
    groups: dict[tuple[int, str], list[Observation]] = defaultdict(list)
    for observation in observations:
        groups[(observation.page_number, normalize_text(observation.item.raw_name))].append(
            observation
        )
    offers = []
    for (page, _), group in groups.items():
        counts = Counter(row.provider for row in group)
        if any(count > 1 for count in counts.values()):
            # Without source coordinates, repeated blocks cannot be safely paired.
            offers.extend(
                ReconciledOffer(page_number=page, observations=[row], status="ambiguous_duplicate")
                for row in group
            )
        elif len(group) == 1:
            offers.append(
                ReconciledOffer(page_number=page, observations=group, status="single_source")
            )
        else:
            first = group[0].item
            same = all(
                (
                    row.item.offer_price,
                    row.item.currency,
                    row.item.unit_size,
                    row.item.unit,
                    row.item.package_quantity,
                    row.item.price_type,
                )
                == (
                    first.offer_price,
                    first.currency,
                    first.unit_size,
                    first.unit,
                    first.package_quantity,
                    first.price_type,
                )
                for row in group[1:]
            )
            offers.append(
                ReconciledOffer(
                    page_number=page,
                    observations=group,
                    status="agreed" if same else "conflict",
                )
            )
    return offers


def extract_independent_pages(
    *,
    business_id: str,
    document_path: Path,
    providers: dict[str, DocumentExtractionProvider],
    supplier_hint: str | None = None,
) -> DualExtraction:
    """Send identical single-page PDFs to each provider, without sharing model outputs."""
    from hashlib import sha256

    from pypdf import PdfReader, PdfWriter

    if len(providers) < 2 or len(providers) != len(set(providers)):
        raise ValueError("At least two distinct named providers are required.")
    digest = sha256(document_path.read_bytes()).hexdigest()
    reader = PdfReader(str(document_path))
    observations: list[Observation] = []
    errors: dict[str, str] = {}
    with TemporaryDirectory(prefix="stock-ai-dual-") as directory:
        for page_number, page in enumerate(reader.pages, start=1):
            page_path = Path(directory) / f"page-{page_number:04d}.pdf"
            writer = PdfWriter()
            writer.add_page(page)
            with page_path.open("wb") as output:
                writer.write(output)
            for name, provider in providers.items():
                try:
                    result: SupplierOfferExtraction = provider.extract_supplier_offer(
                        document_path=page_path, supplier_hint=supplier_hint
                    )
                    observations.extend(
                        Observation(
                            provider=name,
                            page_number=page_number,
                            ordinal=index,
                            item=item.model_copy(update={"page_number": page_number}),
                        )
                        for index, item in enumerate(result.items)
                    )
                except Exception as exc:
                    errors[f"{name}:page:{page_number}"] = str(exc)
    return DualExtraction(
        business_id=business_id,
        document_sha256=digest,
        observations=observations,
        offers=reconcile(observations),
        errors=errors,
    )


class JevChoice(BaseModel):
    product_id: str | None = None
    confidence: Decimal | None = None
    model_version: str
    probabilities: dict[str, Decimal] = Field(default_factory=dict)
    input_tokens: int | None = None
    output_tokens: int | None = None


class JevChooser(Protocol):
    def choose(
        self, *, item: SupplierOfferItem, candidates: list[ProductMatchCandidate]
    ) -> JevChoice: ...


class ShadowDecision(BaseModel):
    offer_item_id: str
    fuzzy_product_id: str | None
    jev_product_id: str | None
    candidate_product_ids: list[str]
    model_version: str | None = None
    confidence: Decimal | None = None
    probabilities: dict[str, Decimal] = Field(default_factory=dict)
    input_tokens: int | None = None
    output_tokens: int | None = None
    error: str | None = None


def evaluate_shadow(
    *,
    item: SupplierOfferItem,
    candidates: list[ProductMatchCandidate],
    chooser: JevChooser,
) -> ShadowDecision:
    ids = [candidate.product.id for candidate in candidates if candidate.product]
    fuzzy = ids[0] if ids else None
    try:
        choice = chooser.choose(item=item, candidates=candidates)
        if choice.product_id is not None and choice.product_id not in ids:
            raise ValueError("Jev selected a product outside the closed candidate set")
        return ShadowDecision(
            offer_item_id=item.id,
            fuzzy_product_id=fuzzy,
            jev_product_id=choice.product_id,
            candidate_product_ids=ids,
            model_version=choice.model_version,
            confidence=choice.confidence,
            probabilities=choice.probabilities,
            input_tokens=choice.input_tokens,
            output_tokens=choice.output_tokens,
        )
    except Exception as exc:
        return ShadowDecision(
            offer_item_id=item.id,
            fuzzy_product_id=fuzzy,
            jev_product_id=None,
            candidate_product_ids=ids,
            error=str(exc),
        )


class PilotMetrics(BaseModel):
    labeled_count: int
    extraction_recall: Decimal
    correct_price_recall: Decimal
    false_price_count: int
    duplicate_count: int
    conflict_count: int
    candidate_recall: Decimal | None = None
    fuzzy_accuracy: Decimal | None = None
    jev_accuracy: Decimal | None = None


def measure(
    dataset: PilotDataset,
    extraction: DualExtraction,
    decisions: list[ShadowDecision] | None = None,
    item_labels: dict[str, str | None] | None = None,
    provider: str | None = None,
) -> PilotMetrics:
    if (
        dataset.business_id != extraction.business_id
        or dataset.document_sha256 != extraction.document_sha256
    ):
        raise ValueError("Dataset does not belong to this business/document")
    if provider is not None:
        selected = [row for row in extraction.observations if row.provider == provider]
        if not selected:
            raise ValueError(f"No observations for provider: {provider}")
        offers = reconcile(selected)
        usable = {"single_source"}
    else:
        offers = extraction.offers
        usable = {"agreed"}
    # Multiset matching keeps repeated, identically named offer blocks distinct.
    observed = Counter(
        (offer.page_number, normalize_text(offer.observations[0].item.raw_name))
        for offer in offers
        if offer.status != "ambiguous_duplicate"
    )
    priced = Counter(
        (
            offer.page_number,
            normalize_text(offer.observations[0].item.raw_name),
            offer.observations[0].item.offer_price,
            offer.observations[0].item.currency,
        )
        for offer in offers
        if offer.status in usable
    )
    all_prices = Counter()
    for offer in offers:
        for price, currency in {
            (row.item.offer_price, row.item.currency) for row in offer.observations
        }:
            all_prices[
                (
                    offer.page_number,
                    normalize_text(offer.observations[0].item.raw_name),
                    price,
                    currency,
                )
            ] += 1
    found = correct = 0
    for label in dataset.labels:
        key = (label.page_number, normalize_text(label.raw_name))
        if observed[key]:
            found += 1
            observed[key] -= 1
        price_key = (*key, label.offer_price, label.currency)
        if priced[price_key]:
            correct += 1
            priced[price_key] -= 1
        if all_prices[price_key]:
            all_prices[price_key] -= 1
    count = len(dataset.labels)
    labels = item_labels or {}
    evaluated = [decision for decision in (decisions or []) if decision.offer_item_id in labels]
    eligible = [decision for decision in evaluated if labels[decision.offer_item_id] is not None]

    def ratio(n: int, d: int) -> Decimal:
        return Decimal(n) / Decimal(d) if d else Decimal(0)

    return PilotMetrics(
        labeled_count=count,
        extraction_recall=ratio(found, count),
        correct_price_recall=ratio(correct, count),
        false_price_count=sum(all_prices.values()),
        duplicate_count=sum(o.status == "ambiguous_duplicate" for o in offers),
        conflict_count=sum(o.status == "conflict" for o in offers),
        candidate_recall=(
            ratio(
                sum(labels[d.offer_item_id] in d.candidate_product_ids for d in eligible),
                len(eligible),
            )
            if decisions is not None
            else None
        ),
        fuzzy_accuracy=(
            ratio(
                sum(d.fuzzy_product_id == labels[d.offer_item_id] for d in evaluated),
                len(evaluated),
            )
            if decisions is not None
            else None
        ),
        jev_accuracy=(
            ratio(
                sum(
                    d.error is None and d.jev_product_id == labels[d.offer_item_id]
                    for d in evaluated
                ),
                len(evaluated),
            )
            if decisions is not None
            else None
        ),
    )
