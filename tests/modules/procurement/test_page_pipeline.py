"""Regression fixtures derived from the Vital incident; no database or paid calls."""

from copy import deepcopy
from decimal import Decimal

import pytest
from pypdf import PdfWriter

from app.modules.procurement.analysis_contracts import (
    AnalysisMethod,
    DocumentReadResult,
    ProviderCallRecord,
    ReaderStructuredExtractionV3,
)
from app.modules.procurement.matching import ProductMatchService
from app.modules.procurement.page_analysis import PageOfferAnalysisService, work_attachment
from app.modules.procurement.page_analysis_schemas import PageWork
from app.modules.procurement.schemas import Product


def source(tmp_path, count=1):
    path = tmp_path / "offers.pdf"
    writer = PdfWriter()
    for _ in range(count):
        writer.add_blank_page(width=600, height=800)
    writer.write(path)
    return path


def raw_block(
    name="Aceite girasol x900ml.",
    brand="LEGITIMO",
    amount="2949",
    reference="3276.67",
    reference_unit="l",
):
    label = "PRECIO XLT" if reference_unit == "l" else "PRECIO XKG"
    text = f"{brand}\n{name}\nDESDE 3 UNIDADES\n${amount}\n{label}: ${reference}"
    fields = {
        "raw_name": name,
        "brand": brand,
        "unit_size": "900ml",
        "unit": "900ml",
        "offer_price": "$" + amount,
        "reference_price": "$" + reference,
        "reference_unit": label,
    }
    return {
        "fragments": [{"text": text, "legibility": "readable"}],
        "page_condition_indices": [],
        "warnings": [],
        "proposals": [
            {
                "raw_name": name,
                "brand": brand,
                "unit_size": 900,
                "unit": "ml",
                "offer_price": amount,
                "currency": None,
                "tax_included": None,
                "price_type": "unknown",
                "package_quantity": None,
                "retail_contents_count": None,
                "confidence_score": None,
                "reference_price": reference,
                "reference_unit": reference_unit,
                "minimum_purchase": {
                    "value": 3,
                    "unit": "unidades",
                    "scope": "offer",
                    "refs": [{"scope": "block", "fragment_index": 0, "quote": "DESDE 3 UNIDADES"}],
                },
                "field_evidence": [
                    {
                        "field": field,
                        "refs": [{"scope": "block", "fragment_index": 0, "quote": quote}],
                    }
                    for field, quote in fields.items()
                ],
                "warnings": [],
            }
        ],
    }


def extraction(blocks, *, estimate=None, repeated=False):
    page = {
        "page_number": 1,
        "page_conditions": [],
        "blocks": blocks,
        "coverage": {
            "estimated_offer_blocks": estimate if estimate is not None else len(blocks),
            "needs_visual_review": False,
            "warnings": [],
        },
    }
    pages = [{**page, "blocks": [b]} for b in blocks] if repeated else [page]
    return ReaderStructuredExtractionV3(supplier_name="Vital", warnings=[], pages=pages)


class Reader:
    def __init__(self, operation):
        self.operation = operation
        self.requests = []

    def read(self, *, request):
        self.requests.append(request)
        result = self.operation(request, len(self.requests))
        return DocumentReadResult(
            extraction=result,
            call=ProviderCallRecord(
                call_id=request.reading_id,
                run_id=request.run_id,
                reading_id=request.reading_id,
                provider="openai",
                model="fake",
                prompt_version="fixture",
                schema_version="v3",
                analysis_method=request.analysis_method,
                phase=request.phase,
                start_page=request.start_page,
                end_page=request.end_page,
                status="success" if result else "error",
            ),
        )


class Catalog:
    def list_products(self, **_):
        return [
            Product(
                id="legitimo",
                business_id="shop",
                name="LEGITIMO ACEITE DE GIRASOL 900ML",
                normalized_name="legitimo aceite de girasol 900ml",
                unit_size=Decimal(900),
                unit="ml",
                current_cost=Decimal(2400),
            )
        ]

    def list_product_match_feedback_rules(self, **_):
        return []


def retriever(**kwargs):
    kwargs.pop("supplier_name")
    return ProductMatchService(Catalog()).retrieve_analysis_candidates(
        supplier_id="vital", **kwargs
    )


def analyze(service, path, **kwargs):
    return service.analyze(
        business_id="shop",
        document_path=path,
        method=AnalysisMethod.OPENAI_STRUCTURED,
        supplier_hint="Vital",
        **kwargs,
    )


def test_failed_first_read_recovers_new_legitimo_and_confirms_reference_basis(tmp_path):
    reader = Reader(lambda req, _: None if req.phase == "initial" else extraction([raw_block()]))
    result = analyze(
        PageOfferAnalysisService(reader=reader, candidate_retriever=retriever), source(tmp_path)
    )
    assert len(reader.requests) == 5  # first page plus four bounded regions
    assert len(result.items) == 1  # overlapping regions do not multiply the same observation
    offer = result.items[0].provider_observations["analysis_v2"]["interpretation"]
    assert offer["product_id"] == "legitimo"
    assert offer["identity_status"] == offer["price_status"] == "confirmed"
    assert offer["price_type"] == "unit"
    assert offer["minimum_purchase"]["value"] == "3"
    assert result.items[0].package_quantity is None
    assert result.document_metadata["analysis"]["incomplete_page_numbers"] == []


def test_repeated_local_pages_keep_all_thirteen_new_blocks_without_foreign_conditions(tmp_path):
    blocks = [raw_block(name=f"Aceite variante {n} x900ml.") for n in range(14)]
    for b in blocks[1:]:
        b["page_condition_indices"] = [0]  # absent optional condition cannot discard the name/price
    reader = Reader(lambda req, _: extraction(blocks, repeated=req.phase == "initial"))
    result = analyze(PageOfferAnalysisService(reader=reader), source(tmp_path))
    assert len(result.items) == 14
    assert all(i.page_number == 1 for i in result.items)
    assert all(
        not b["condition_refs"] for b in result.document_metadata["analysis_ledger"]["blocks"]
    )


def test_sparse_portada_recovers_twenty_observations(tmp_path):
    blocks = [raw_block(name=f"Producto visible {n} 900ml") for n in range(20)]
    reader = Reader(
        lambda req, _: (
            extraction(blocks[:1], estimate=20) if req.phase == "initial" else extraction(blocks)
        )
    )
    result = analyze(PageOfferAnalysisService(reader=reader), source(tmp_path))
    assert len(result.items) == 20
    assert result.document_metadata["analysis"]["incomplete_page_numbers"] == []


@pytest.mark.parametrize("change", ["amount", "label", "size", "pack", "discount", "association"])
def test_reference_corroboration_refuses_wrong_or_ambiguous_evidence(tmp_path, change):
    block = raw_block()
    proposal = block["proposals"][0]
    if change == "amount":
        proposal["reference_price"] = "4000"
    elif change == "label":
        proposal["reference_unit"] = "kg"
    elif change == "size":
        proposal["unit_size"] = 1000
    elif change == "pack":
        proposal["package_quantity"] = 6
        block["fragments"][0]["text"] += "\npack de 6"
        proposal["field_evidence"].append(
            {
                "field": "package_quantity",
                "refs": [{"scope": "block", "fragment_index": 0, "quote": "pack de 6"}],
            }
        )
    elif change == "association":
        block["fragments"][0]["text"] = block["fragments"][0]["text"].replace(
            "PRECIO XLT: $3276.67", "$3276.67 PRECIO XLT: $9000"
        )
    else:
        block["fragments"][0]["text"] += " 10% descuento"
    reader = Reader(lambda *_: extraction([block, raw_block(name="Otro 900ml")]))
    result = analyze(PageOfferAnalysisService(reader=reader), source(tmp_path))
    first = result.items[0].provider_observations["analysis_v2"]["interpretation"]
    assert first["price_status"] == "pending"


def test_checkpoint_resume_skips_completed_and_uncertain_calls(tmp_path):
    path = source(tmp_path, 3)
    saved = []
    reader = Reader(lambda *_: extraction([raw_block(), raw_block(name="Otro 900ml")]))
    service = PageOfferAnalysisService(reader=reader, max_calls=1)
    result = analyze(service, path, save_checkpoint=saved.append)
    assert len(reader.requests) == 1
    assert result.document_metadata["recovery_pending"]["continuation_available"]
    resumed = analyze(service, path, checkpoint=saved[-1])
    assert len(reader.requests) == 1
    assert len(resumed.items) == 2
    in_flight = deepcopy(saved[-1])
    in_flight.work[1].status = "inflight"
    in_flight.work[1].result = None
    uncertain = analyze(service, path, checkpoint=in_flight)
    assert len(reader.requests) == 1
    from app.modules.procurement.analysis_reporting import summary_from_metadata

    summary = summary_from_metadata(uncertain.document_metadata)
    assert summary.attempted_page_count == 2
    assert summary.provider_call_count == 2


def test_missing_name_is_preserved_as_pending_instead_of_crashing(tmp_path):
    block = raw_block()
    block["proposals"][0]["raw_name"] = None
    reader = Reader(lambda *_: extraction([block, raw_block(name="Otro 900ml")]))
    result = analyze(PageOfferAnalysisService(reader=reader), source(tmp_path))
    assert len(result.items) == 1
    assert result.document_metadata["analysis"]["pending_block_count"] == 1


def test_checkpoint_rejects_other_source_or_business_before_transport(tmp_path):
    saved = []
    reader = Reader(lambda *_: extraction([raw_block(), raw_block(name="Otro 900ml")]))
    service = PageOfferAnalysisService(reader=reader)
    path = source(tmp_path)
    analyze(service, path, save_checkpoint=saved.append)
    checkpoint = saved[-1]
    checkpoint.business_id = "another-shop"
    before = len(reader.requests)
    with pytest.raises(ValueError, match="mismatch"):
        analyze(service, path, checkpoint=checkpoint)
    assert len(reader.requests) == before


def test_regions_are_single_page_clipped_attachments_and_temporaries_are_removed(tmp_path):
    from pypdf import PdfReader

    with work_attachment(source(tmp_path), PageWork(page=1, region=0)) as path:
        page = PdfReader(path).pages[0]
        assert page.mediabox.width == 330
        assert page.mediabox.height == 440
    assert not path.exists()


def test_equal_current_and_reference_unit_prices_keep_the_current_amount(tmp_path):
    block = raw_block(amount="939", reference="939")
    block["fragments"][0]["text"] = block["fragments"][0]["text"].replace("XLT", "XU")
    for field in block["proposals"][0]["field_evidence"]:
        if field["field"] == "reference_unit":
            field["refs"][0]["quote"] = "PRECIO XU"
    reader = Reader(lambda *_: extraction([block, raw_block(name="Otro 900ml")]))
    result = analyze(PageOfferAnalysisService(reader=reader), source(tmp_path))
    assert result.items[0].offer_price == Decimal(939)


def test_weak_none_decision_does_not_claim_absence_from_catalog():
    from app.modules.procurement.analysis_contracts import ChoiceDecision, OfferDecisionResult
    from app.modules.procurement.decision_policy import (
        DocumentDecisionPolicy,
        build_offer_decision_request,
    )
    from app.modules.procurement.offer_evidence import StructuredProposalAssembler
    from app.modules.procurement.page_evidence import page_blocks

    [block], _ = page_blocks(
        extraction=extraction([raw_block()]),
        work=PageWork(page=1),
        run_id="fixture",
        reading_id="read",
    )
    [offer] = (
        StructuredProposalAssembler()
        .assemble_interpretation(
            business_id="shop",
            block=block,
            classification=None,
            candidates=[],
        )
        .offers
    )
    candidates = retriever(business_id="shop", offers=[offer], supplier_name="Vital")[
        offer.offer_id
    ]
    # Remove deterministic acceptance to exercise the optional decision boundary.
    for candidate in candidates:
        candidate.reason_codes = []
    request = build_offer_decision_request(
        business_id="shop", block=block, offer=offer, candidates=candidates
    )
    answer = ChoiceDecision(
        question_id=f"{offer.offer_id}:identity",
        choice="none",
        confidence=0.4,
        probabilities={"none": 0.5, "insufficient": 0.1, candidates[0].candidate_id: 0.4},
        model="fixture",
        prompt_version="fixture",
    )
    result = DocumentDecisionPolicy().validate_decision(
        request=request,
        decision=OfferDecisionResult(offer_id=offer.offer_id, decisions=[answer]),
    )
    assert result.offer.identity_status == "pending"
    assert result.offer.price_status == "confirmed"


def test_persisted_identity_keeps_separate_brand_evidence_and_blocks_unknown_tax(tmp_path):
    from app.modules.procurement.schemas import SupplierOfferItem

    reader = Reader(lambda *_: extraction([raw_block(), raw_block(name="Otro 900ml")]))
    result = analyze(
        PageOfferAnalysisService(reader=reader, candidate_retriever=retriever), source(tmp_path)
    )
    extracted = result.items[0]
    item = SupplierOfferItem(
        business_id="shop",
        supplier_offer_document_id="doc",
        supplier_id="vital",
        normalized_name="aceite girasol x900ml",
        **extracted.model_dump(exclude={"provider_observations", "selected_source"}),
        metadata=extracted.provider_observations,
    )
    from app.modules.procurement.identity import canonical_identity_equal

    [product] = Catalog().list_products()
    assert canonical_identity_equal(item, product)
    [candidate] = ProductMatchService(Catalog()).match_item(item=item, products=[product])
    assert candidate.product.id == "legitimo"
    assert candidate.auto_accepted
    assert candidate.cost_difference is None
    assert candidate.recommendation == "review"
    assert "tax_basis_unverified" in candidate.reasons


def test_saved_resolution_cannot_silently_switch_to_another_catalog_product():
    from app.modules.procurement.schemas import SupplierOfferItem

    [product] = Catalog().list_products()
    item = SupplierOfferItem(
        business_id="shop",
        supplier_offer_document_id="doc",
        supplier_id="vital",
        raw_name=product.name,
        normalized_name=product.normalized_name,
        offer_price=2949,
        metadata={
            "analysis_v2": {
                "interpretation": {
                    "identity_status": "confirmed",
                    "product_id": "missing-product",
                }
            }
        },
    )
    [candidate] = ProductMatchService(Catalog()).match_item(item=item, products=[product])
    assert candidate.product is None
    assert "resolved_product_unavailable" in candidate.reasons


@pytest.mark.parametrize("conflict", [False, True])
def test_region_consolidation_preserves_evidence_and_different_minimums(tmp_path, conflict):
    first = raw_block()
    second = raw_block(name="LEGITIMO Aceite girasol x900ml.")
    # Different spelling and separately transcribed brand describe the same label.
    second["proposals"][0]["minimum_purchase"]["unit"] = "unit"
    if conflict:
        second["proposals"][0]["minimum_purchase"]["value"] = 6
        second["fragments"][0]["text"] = second["fragments"][0]["text"].replace(
            "DESDE 3 UNIDADES", "DESDE 6 UNIDADES"
        )
        second["proposals"][0]["minimum_purchase"]["refs"][0]["quote"] = "DESDE 6 UNIDADES"
    reader = Reader(
        lambda req, n: None if req.phase == "initial" else extraction([first if n % 2 else second])
    )
    result = analyze(PageOfferAnalysisService(reader=reader), source(tmp_path))
    assert len(result.items) == (2 if conflict else 1)
    if not conflict:
        from app.modules.procurement.analysis_contracts import OfferInterpretation, OfferTextBlock
        from app.modules.procurement.offer_evidence import usable_evidence

        item = result.items[0]
        offer = OfferInterpretation.model_validate(
            item.provider_observations["analysis_v2"]["interpretation"]
        )
        block = next(
            OfferTextBlock.model_validate(b)
            for b in result.document_metadata["analysis_ledger"]["blocks"]
            if b["block_id"] == offer.block_id
        )
        assert usable_evidence(offer, block)
        assert "corroborating_readings_merged" in block.warnings
