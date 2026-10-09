from decimal import Decimal

import pytest

from app.modules.procurement.schemas import ExtractedSupplierOfferItem, SupplierOfferExtraction
from app.providers.documents.fallback import (
    DocumentExtractionUnavailableError,
    FallbackDocumentExtractionProvider,
)


class FakeProvider:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = 0

    def extract_supplier_offer(self, *, document_path, supplier_hint=None):
        self.calls += 1
        if self.error:
            raise self.error
        return self.result


def extraction() -> SupplierOfferExtraction:
    return SupplierOfferExtraction(
        supplier_name="Vital",
        items=[
            ExtractedSupplierOfferItem(
                raw_name="Aceite 900 ml",
                offer_price=Decimal("1000"),
            )
        ],
    )


def test_falls_back_and_records_selected_reader(tmp_path) -> None:
    gemini = FakeProvider(error=RuntimeError("503 UNAVAILABLE secret details"))
    openai = FakeProvider(result=extraction())
    provider = FallbackDocumentExtractionProvider([("gemini", gemini), ("openai", openai)])

    result = provider.extract_supplier_offer(document_path=tmp_path / "offer.pdf")

    assert gemini.calls == openai.calls == 1
    assert result.document_metadata["selected_extraction_provider"] == "openai"
    assert result.document_metadata["failed_extraction_providers"] == ["gemini"]
    assert result.warnings[0] == ("gemini no estaba disponible; la lectura se completó con openai.")


def test_does_not_call_fallback_when_primary_succeeds(tmp_path) -> None:
    gemini = FakeProvider(result=extraction())
    openai = FakeProvider(result=extraction())
    provider = FallbackDocumentExtractionProvider([("gemini", gemini), ("openai", openai)])

    result = provider.extract_supplier_offer(document_path=tmp_path / "offer.pdf")

    assert gemini.calls == 1
    assert openai.calls == 0
    assert result.document_metadata["selected_extraction_provider"] == "gemini"


def test_reports_compact_failure_when_all_readers_fail(tmp_path) -> None:
    provider = FallbackDocumentExtractionProvider(
        [
            ("gemini", FakeProvider(error=RuntimeError("503 UNAVAILABLE secret"))),
            ("openai", FakeProvider(error=TimeoutError("private request details"))),
        ]
    )

    with pytest.raises(DocumentExtractionUnavailableError) as error:
        provider.extract_supplier_offer(document_path=tmp_path / "offer.pdf")

    message = str(error.value)
    assert "gemini: temporarily unavailable" in message
    assert "openai: TimeoutError" in message
    assert "secret" not in message
