from decimal import Decimal

from app.providers.documents.gemini import GeminiSupplierOfferDocumentProvider


class FakeGeminiFiles:
    def __init__(self) -> None:
        self.uploaded_path: str | None = None

    def upload(self, *, file: str) -> object:
        self.uploaded_path = file
        return object()


class FakeGeminiModels:
    def __init__(self) -> None:
        self.seen_models: list[str] = []

    def generate_content(self, **kwargs) -> object:
        self.seen_models.append(kwargs["model"])
        self.kwargs = kwargs
        return type(
            "FakeResponse",
            (),
            {
                "text": (
                    '{"supplier_name":"Vital","source_filename":null,"raw_text":null,'
                    '"items":[{"raw_name":"UVITA Vino t/b 1lt","offer_price":"1469",'
                    '"unit_size":"1","unit":"l","confidence_score":"0.86"}],'
                    '"warnings":[]}'
                )
            },
        )()


class FakeGeminiClient:
    def __init__(self) -> None:
        self.files = FakeGeminiFiles()
        self.models = FakeGeminiModels()


class FakeGeminiRetryableError(Exception):
    status_code = 503


class FakeGeminiFallbackModels(FakeGeminiModels):
    def generate_content(self, **kwargs) -> object:
        self.seen_models.append(kwargs["model"])
        if len(self.seen_models) == 1:
            raise FakeGeminiRetryableError("busy")
        return super().generate_content(**kwargs)


def test_gemini_supplier_offer_provider_extracts_structured_json(tmp_path) -> None:
    document_path = tmp_path / "vital.pdf"
    document_path.write_bytes(b"%PDF-1.7\n")
    client = FakeGeminiClient()
    provider = GeminiSupplierOfferDocumentProvider(
        api_key="test-key",
        model="gemini-test",
        timeout_seconds=6,
        client=client,
    )

    extraction = provider.extract_supplier_offer(
        document_path=document_path,
        supplier_hint="Vital",
    )

    assert client.files.uploaded_path == str(document_path)
    assert extraction.supplier_name == "Vital"
    assert extraction.source_filename == "vital.pdf"
    assert extraction.items[0].raw_name == "UVITA Vino t/b 1lt"
    assert extraction.items[0].offer_price == Decimal("1469")


def test_gemini_supplier_offer_provider_falls_back_when_model_is_busy(tmp_path) -> None:
    document_path = tmp_path / "vital.pdf"
    document_path.write_bytes(b"%PDF-1.7\n")
    client = FakeGeminiClient()
    client.models = FakeGeminiFallbackModels()
    provider = GeminiSupplierOfferDocumentProvider(
        api_key="test-key",
        model="gemini-primary",
        timeout_seconds=6,
        client=client,
    )

    extraction = provider.extract_supplier_offer(
        document_path=document_path,
        supplier_hint="Vital",
    )

    assert client.models.seen_models[:2] == ["gemini-primary", "gemini-flash-latest"]
    assert extraction.items[0].offer_price == Decimal("1469")
