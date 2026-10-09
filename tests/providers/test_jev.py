import json
from decimal import Decimal

from app.modules.procurement.schemas import (
    Product,
    ProductMatchCandidate,
    SupplierOfferItem,
    SupplierProductRelationship,
)
from app.providers.jev import JevHttpChooser


def test_jev_choice_uses_closed_ids_and_records_usage(monkeypatch):
    offer = SupplierOfferItem(
        business_id="b",
        supplier_id="s",
        supplier_offer_document_id="d",
        raw_name="Aceite 900 ml",
        normalized_name="aceite 900 ml",
        offer_price=100,
    )
    product = Product(
        id="p1", business_id="b", name="Aceite 900 ml", normalized_name="aceite 900 ml"
    )
    candidate = ProductMatchCandidate(
        supplier_offer_item=offer,
        product=product,
        relationship_type=SupplierProductRelationship.EXACT_MATCH,
        confidence_score=Decimal("0.8"),
    )

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self):
            return json.dumps(
                {
                    "model": "jev-1.13.0",
                    "answers": {
                        "product": {
                            "type": "choice",
                            "choice": "p1",
                            "confidence": 0.8,
                            "probabilities": {"p1": 0.9, "none": 0.05, "insufficient": 0.05},
                        }
                    },
                    "usage": {"input_tokens": 100, "output_tokens": 20},
                }
            ).encode()

    def fake_open(request, timeout):
        body = json.loads(request.data)
        assert body["model"] == "jev-1.13.0"
        assert set(body["questions"]["product"]["criteria"]) == {"p1", "none", "insufficient"}
        assert "offer_price" not in body["state"]["offer"]
        return Response()

    monkeypatch.setattr("app.providers.jev.urlopen", fake_open)
    choice = JevHttpChooser(api_key="test", model="jev-1.13.0").choose(
        item=offer, candidates=[candidate]
    )
    assert choice.product_id == "p1"
    assert choice.input_tokens == 100


def test_jev_reconciler_uses_closed_questions_and_validates_response(monkeypatch):
    from app.providers.jev import JevHttpOfferReconciler

    seen = []

    class Response:
        def __init__(self, data):
            self.data = data

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self):
            return json.dumps(self.data).encode()

    def fake_open(request, timeout):
        body = json.loads(request.data)
        seen.append(body)
        assert timeout == 60
        assert set(body["state"]["observations"]) == {"gemini", "openai"}
        assert "shadow" in body["questions"]["decision"]["instructions"]
        answers = {}
        choices = {"decision": "same_offer", "selected_source": "openai", "reason": "same"}
        for key, question in body["questions"].items():
            choice = choices.get(key, "compatible")
            answers[key] = {
                "type": "choice",
                "choice": choice,
                "confidence": 0.87,
                "probabilities": {option: int(option == choice) for option in question["criteria"]},
            }
        return Response(
            {
                "model": body["model"],
                "answers": answers,
                "usage": {"input_tokens": 100, "output_tokens": 10},
            }
        )

    monkeypatch.setattr("app.providers.jev.urlopen", fake_open)
    decision = JevHttpOfferReconciler(
        api_key="test", model="jev-1.13.0", timeout_seconds=60
    ).reconcile(
        observations={"gemini": {"raw_name": "Mayonesa"}, "openai": {"raw_name": "MAYONESA"}}
    )
    assert len(seen) == 1
    assert decision.decision == "same_offer"
    assert decision.selected_source == "openai"
    assert decision.confidence == Decimal(".87")
    assert decision.conflicting_fields == []
    assert "brand" in decision.compatible_fields
