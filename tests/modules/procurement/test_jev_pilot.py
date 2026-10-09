from decimal import Decimal
from pathlib import Path

from pypdf import PdfWriter

from app.modules.procurement.jev_pilot import (
    JevChoice,
    LabeledOffer,
    Observation,
    PilotDataset,
    evaluate_shadow,
    extract_independent_pages,
    measure,
    reconcile,
)
from app.modules.procurement.schemas import (
    ExtractedSupplierOfferItem,
    Product,
    ProductMatchCandidate,
    SupplierOfferExtraction,
    SupplierOfferItem,
    SupplierProductRelationship,
)


def item(price: str = "100") -> ExtractedSupplierOfferItem:
    return ExtractedSupplierOfferItem(raw_name="Aceite 900 ml", offer_price=Decimal(price))


def test_reconcile_conflict_and_repeated_rows():
    rows = [
        Observation(provider="gemini", page_number=1, ordinal=0, item=item()),
        Observation(provider="luna", page_number=1, ordinal=0, item=item("120")),
    ]
    assert reconcile(rows)[0].status == "conflict"
    rows.append(Observation(provider="gemini", page_number=1, ordinal=1, item=item()))
    assert [offer.status for offer in reconcile(rows)] == ["ambiguous_duplicate"] * 3


def test_independent_pages_and_metrics(tmp_path: Path):
    document = tmp_path / "offers.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=300, height=300)
    writer.add_blank_page(width=300, height=300)
    with document.open("wb") as file:
        writer.write(file)

    class Reader:
        def __init__(self, price):
            self.price = price
            self.calls = []

        def extract_supplier_offer(self, *, document_path, supplier_hint=None):
            self.calls.append(document_path.read_bytes())
            return SupplierOfferExtraction(items=[item(self.price)])

    gemini, luna = Reader("100"), Reader("100")
    result = extract_independent_pages(
        business_id="b1",
        document_path=document,
        providers={"gemini": gemini, "luna": luna},
    )
    assert len(gemini.calls) == len(luna.calls) == 2
    assert gemini.calls == luna.calls
    assert [offer.status for offer in result.offers] == ["agreed", "agreed"]
    labels = PilotDataset(
        business_id="b1",
        supplier_id="s1",
        document_sha256=result.document_sha256,
        split="development",
        labels=[
            LabeledOffer(page_number=i, raw_name="Aceite 900 ml", offer_price=100) for i in (1, 2)
        ],
    )
    metrics = measure(labels, result)
    assert metrics.extraction_recall == metrics.correct_price_recall == 1
    assert metrics.false_price_count == 0
    assert measure(labels, result, provider="gemini").correct_price_recall == 1


def test_shadow_rejects_unlisted_product_and_does_not_mutate():
    offer = SupplierOfferItem(
        business_id="b1",
        supplier_id="s1",
        supplier_offer_document_id="d1",
        raw_name="Aceite",
        normalized_name="aceite",
        offer_price=100,
    )
    product = Product(business_id="b1", name="Aceite", normalized_name="aceite")
    candidate = ProductMatchCandidate(
        supplier_offer_item=offer,
        product=product,
        relationship_type=SupplierProductRelationship.EXACT_MATCH,
        confidence_score=Decimal("0.8"),
    )

    class InvalidChooser:
        def choose(self, *, item, candidates):
            return JevChoice(product_id="foreign", model_version="test")

    decision = evaluate_shadow(item=offer, candidates=[candidate], chooser=InvalidChooser())
    assert decision.error and decision.jev_product_id is None
    assert candidate.product.id == product.id
