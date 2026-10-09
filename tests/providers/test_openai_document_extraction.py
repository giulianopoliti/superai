import base64
from types import SimpleNamespace

import pytest

from app.modules.procurement.schemas import SupplierOfferReaderExtraction
from app.providers.documents.base import DocumentPageContext
from app.providers.documents.openai import OpenAISupplierOfferDocumentProvider


def test_openai_chunk_is_one_inline_request_with_local_pages(monkeypatch, tmp_path):
    calls = []
    document = tmp_path / "chunk.pdf"
    document.write_bytes(b"six page pdf")
    parsed = SupplierOfferReaderExtraction(
        page_extractions=[
            {
                "page_number": 2,
                "items": [{"raw_name": "Arroz", "offer_price": 100, "page_number": 2}],
                "coverage": {"estimated_offer_blocks": 1},
                "warnings": [],
            }
        ]
    )

    class Client:
        def __init__(self, **kwargs):
            assert kwargs["timeout"] == 60
            assert kwargs["max_retries"] == 0
            self.responses = self

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def parse(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(output_parsed=parsed)

    monkeypatch.setattr("openai.OpenAI", Client)
    result = OpenAISupplierOfferDocumentProvider(
        api_key="test", model="unchanged-model", timeout_seconds=60
    ).extract_supplier_offer(
        document_path=document,
        page_context=DocumentPageContext(source_filename="original.pdf", start_page=7, end_page=12),
    )
    assert len(calls) == 1
    assert calls[0]["model"] == "unchanged-model"
    assert calls[0]["text_format"] is SupplierOfferReaderExtraction
    content = calls[0]["input"][0]["content"][1]
    assert base64.b64decode(content["file_data"].split(",")[1]) == document.read_bytes()
    assert "todas las páginas" in calls[0]["instructions"]
    assert result.source_filename == "original.pdf"
    assert result.items[0].page_number == 2  # only the orchestrator remaps
    assert result.document_metadata["reader_provider"] == "openai"


def test_openai_reader_schema_contains_no_arbitrary_metadata():
    from openai.lib._pydantic import to_strict_json_schema

    schema = to_strict_json_schema(SupplierOfferReaderExtraction)

    def visit(node):
        if isinstance(node, dict):
            # Decimal's default Pydantic schema uses regex lookaround, rejected
            # by the real API with invalid_json_schema before reading the PDF.
            assert "(?" not in node.get("pattern", "")
            if node.get("type") == "object":
                assert node["additionalProperties"] is False
                assert set(node["required"]) == set(node["properties"])
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)

    visit(schema)
    properties = schema["$defs"]["SupplierOfferObservation"]["properties"]
    assert properties["offer_price"]["type"] == "number"
    assert {s["type"] for s in properties["unit_size"]["anyOf"]} == {"number", "null"}


def test_openai_rejects_refusal_or_unparsed_output(monkeypatch, tmp_path):
    class Client:
        def __init__(self, **kwargs):
            self.responses = self

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def parse(self, **kwargs):
            return SimpleNamespace(output_parsed=None)

    monkeypatch.setattr("openai.OpenAI", Client)
    path = tmp_path / "x.pdf"
    path.write_bytes(b"pdf")
    with pytest.raises(ValueError):
        OpenAISupplierOfferDocumentProvider(
            api_key="test", model="same", timeout_seconds=60
        ).extract_supplier_offer(document_path=path)
