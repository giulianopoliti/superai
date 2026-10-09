from decimal import Decimal

from app.providers.documents.gemini import GeminiSupplierOfferDocumentProvider


class FakeGeminiFiles:
    def __init__(self) -> None:
        self.uploaded_path: str | None = None
        self.uploaded_paths: list[str] = []

    def upload(self, *, file: str) -> object:
        self.uploaded_path = file
        self.uploaded_paths.append(file)
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


class FakeGeminiPageModels(FakeGeminiModels):
    def generate_content(self, **kwargs) -> object:
        self.seen_models.append(kwargs["model"])
        instruction = kwargs["contents"][1]
        if "page_number:" not in instruction:
            return type(
                "FakeResponse",
                (),
                {
                    "text": (
                        '{"supplier_name":"Diarco","source_filename":null,"raw_text":null,'
                        '"items":[{"raw_name":"CAÃ‘UELAS Aceite Girasol x 900 Ml.",'
                        '"offer_price":"2849","confidence_score":"0.95"},'
                        '{"raw_name":"NATURA Aceite Girasol x 1,5 Lt.",'
                        '"offer_price":"5399","confidence_score":"0.95"}],'
                        '"warnings":[]}'
                    )
                },
            )()
        page_number = 1 if "page_number: 1" in instruction else 2
        if page_number == 1:
            text = (
                '{"page_number":1,'
                '"items":[{"raw_name":"CAÑUELAS Aceite Girasol x 900 Ml.",'
                '"offer_price":"2849","page_number":1,"confidence_score":"0.95"}],'
                '"warnings":[],'
                '"coverage":{"estimated_offer_blocks":1,"extracted_item_count":1,'
                '"needs_review":false}}'
            )
        else:
            text = (
                '{"page_number":2,'
                '"items":[{"raw_name":"NATURA Aceite Girasol x 1,5 Lt.",'
                '"offer_price":"5399","confidence_score":"0.95"}],'
                '"warnings":["small text in lower grid"],'
                '"coverage":{"estimated_offer_blocks":2,"extracted_item_count":1,'
                '"needs_review":false}}'
            )
        return type("FakeResponse", (), {"text": text})()


class FakeGeminiLowCoveragePageModels(FakeGeminiPageModels):
    def generate_content(self, **kwargs) -> object:
        instruction = kwargs["contents"][1]
        if "page_number: 2" not in instruction:
            return super().generate_content(**kwargs)
        self.seen_models.append(kwargs["model"])
        return type(
            "FakeResponse",
            (),
            {
                "text": (
                    '{"page_number":2,'
                    '"items":[{"raw_name":"NATURA Aceite Girasol x 1,5 Lt.",'
                    '"offer_price":"5399","confidence_score":"0.95"}],'
                    '"warnings":["small text in lower grid"],'
                    '"coverage":{"estimated_offer_blocks":12,"extracted_item_count":1,'
                    '"needs_review":false}}'
                )
            },
        )()


class FakeGeminiPageFailureModels(FakeGeminiPageModels):
    def generate_content(self, **kwargs) -> object:
        instruction = kwargs["contents"][1]
        if "page_number:" in instruction:
            raise RuntimeError("page image unreadable")
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
        retry_delay_seconds=0,
    )

    extraction = provider.extract_supplier_offer(
        document_path=document_path,
        supplier_hint="Vital",
    )

    assert client.models.seen_models[:2] == ["gemini-primary", "gemini-primary"]
    assert extraction.items[0].offer_price == Decimal("1469")


def test_gemini_supplier_offer_provider_extracts_image_only_pdf_by_page(
    tmp_path, monkeypatch
) -> None:
    document_path = tmp_path / "diarco.pdf"
    document_path.write_bytes(b"%PDF-1.7\n")
    page_1 = tmp_path / "page-1.jpg"
    page_2 = tmp_path / "page-2.jpg"
    page_1.write_bytes(b"page 1")
    page_2.write_bytes(b"page 2")
    client = FakeGeminiClient()
    client.models = FakeGeminiPageModels()
    provider = GeminiSupplierOfferDocumentProvider(
        api_key="test-key",
        model="gemini-test",
        timeout_seconds=6,
        client=client,
    )
    monkeypatch.setattr(
        provider,
        "_extract_image_only_pdf_pages",
        lambda *, document_path, output_dir: [(1, page_1), (2, page_2)],
    )

    extraction = provider.extract_supplier_offer(
        document_path=document_path,
        supplier_hint="Diarco",
    )

    assert client.files.uploaded_paths == [str(page_1), str(page_2)]
    assert extraction.supplier_name == "Diarco"
    assert extraction.source_filename == "diarco.pdf"
    assert [item.raw_name for item in extraction.items] == [
        "CAÑUELAS Aceite Girasol x 900 Ml.",
        "NATURA Aceite Girasol x 1,5 Lt.",
    ]
    assert extraction.items[1].page_number == 2
    assert extraction.document_metadata["extraction_mode"] == "image_only_pdf_pages"
    assert extraction.document_metadata["items_by_page"] == {"1": 1, "2": 1}
    assert extraction.document_metadata["needs_review_pages"] == []
    assert "page 2: small text in lower grid" in extraction.warnings


def test_gemini_supplier_offer_provider_falls_back_to_whole_pdf_when_page_coverage_is_low(
    tmp_path, monkeypatch
) -> None:
    document_path = tmp_path / "diarco.pdf"
    document_path.write_bytes(b"%PDF-1.7\n")
    page_1 = tmp_path / "page-1.jpg"
    page_2 = tmp_path / "page-2.jpg"
    page_1.write_bytes(b"page 1")
    page_2.write_bytes(b"page 2")
    client = FakeGeminiClient()
    client.models = FakeGeminiLowCoveragePageModels()
    provider = GeminiSupplierOfferDocumentProvider(
        api_key="test-key",
        model="gemini-test",
        timeout_seconds=6,
        client=client,
    )
    monkeypatch.setattr(
        provider,
        "_extract_image_only_pdf_pages",
        lambda *, document_path, output_dir: [(1, page_1), (2, page_2)],
    )

    extraction = provider.extract_supplier_offer(
        document_path=document_path,
        supplier_hint="Diarco",
    )

    assert client.files.uploaded_paths == [str(page_1), str(page_2), str(document_path)]
    assert extraction.document_metadata["extraction_mode"] == "whole_document_direct_fallback"
    assert "Resultado generado con Gemini directo" in extraction.warnings[0]
    assert len(extraction.items) == 2


def test_gemini_pilot_preserves_low_coverage_page_instead_of_selecting_by_count(
    tmp_path, monkeypatch
) -> None:
    document_path = tmp_path / "diarco.pdf"
    document_path.write_bytes(b"%PDF-1.7\n")
    page = tmp_path / "page-1.jpg"
    page.write_bytes(b"page 1")
    page_2 = tmp_path / "page-2.jpg"
    page_2.write_bytes(b"page 2")
    client = FakeGeminiClient()
    client.models = FakeGeminiLowCoveragePageModels()
    provider = GeminiSupplierOfferDocumentProvider(
        api_key="test-key",
        model="gemini-test",
        timeout_seconds=6,
        client=client,
        allow_direct_fallback=False,
    )
    monkeypatch.setattr(
        provider,
        "_extract_image_only_pdf_pages",
        lambda *, document_path, output_dir: [(1, page), (2, page_2)],
    )

    extraction = provider.extract_supplier_offer(document_path=document_path)

    assert client.files.uploaded_paths == [str(page), str(page_2)]
    assert extraction.document_metadata["extraction_mode"] == "image_only_pdf_pages"
    assert extraction.document_metadata["needs_review_pages"]


def test_gemini_supplier_offer_provider_falls_back_to_whole_pdf_when_page_reading_fails(
    tmp_path, monkeypatch
) -> None:
    document_path = tmp_path / "diarco.pdf"
    document_path.write_bytes(b"%PDF-1.7\n")
    page_1 = tmp_path / "page-1.jpg"
    page_1.write_bytes(b"page 1")
    client = FakeGeminiClient()
    client.models = FakeGeminiPageFailureModels()
    provider = GeminiSupplierOfferDocumentProvider(
        api_key="test-key",
        model="gemini-test",
        timeout_seconds=6,
        client=client,
        retry_delay_seconds=0,
    )
    monkeypatch.setattr(
        provider,
        "_extract_image_only_pdf_pages",
        lambda *, document_path, output_dir: [(1, page_1)],
    )

    extraction = provider.extract_supplier_offer(
        document_path=document_path,
        supplier_hint="Diarco",
    )

    assert client.files.uploaded_paths == [str(page_1), str(document_path)]
    assert extraction.document_metadata["extraction_mode"] == "whole_document_direct_fallback"
    assert "No pudimos leer bien el documento" in extraction.warnings[0]


def test_gemini_chunk_bypasses_internal_image_page_loop(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from app.providers.documents.base import DocumentPageContext

    calls = []

    def generate(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            text='{"page_extractions":[{"page_number":1,"items":[{"raw_name":"Arroz","offer_price":100,"page_number":1}],"coverage":{"estimated_offer_blocks":1},"warnings":[]}]}'
        )

    client = SimpleNamespace(models=SimpleNamespace(generate_content=generate))
    provider = GeminiSupplierOfferDocumentProvider(
        api_key="test", model="gemini", timeout_seconds=60, client=client
    )

    def forbidden(**kwargs):
        raise AssertionError("must not split chunks or upload individual pages")

    monkeypatch.setattr(provider, "_extract_image_only_pdf_pages", forbidden)
    document = tmp_path / "chunk.pdf"
    document.write_bytes(b"six-page-pdf")
    result = provider.extract_supplier_offer(
        document_path=document,
        page_context=DocumentPageContext(source_filename="original.pdf", start_page=7, end_page=12),
    )
    assert len(calls) == 1
    assert calls[0]["contents"][0].inline_data.data == document.read_bytes()
    assert "Attached page count: 6" in calls[0]["contents"][1]
    assert result.items[0].page_number == 1
    assert result.document_metadata["reader_provider"] == "gemini"


def test_gemini_transport_timeout_is_in_milliseconds(monkeypatch):
    from google import genai

    calls = []
    monkeypatch.setattr(genai, "Client", lambda **kwargs: calls.append(kwargs))
    GeminiSupplierOfferDocumentProvider(
        api_key="test", model="gemini", timeout_seconds=60
    )._build_client()
    assert calls == [{"api_key": "test", "http_options": {"timeout": 60000}}]
