import base64
import json
from types import SimpleNamespace

import pytest

from app.modules.procurement.analysis_contracts import (
    WIRE_VERSION,
    AnalysisMethod,
    DocumentReadRequest,
    ReaderBlocksExtractionV2,
    ReaderStructuredExtractionV2,
)
from app.providers.documents.base import DocumentPageContext
from app.providers.documents.openai import OpenAISupplierOfferDocumentProvider


def proposal(**overrides):
    result = {
        "raw_name": None,
        "brand": None,
        "unit_size": None,
        "unit": None,
        "offer_price": None,
        "currency": None,
        "tax_included": None,
        "price_type": "unknown",
        "package_quantity": None,
        "retail_contents_count": None,
        "minimum_purchase": None,
        "confidence_score": None,
        "field_evidence": [],
        "warnings": [],
    }
    return result | overrides


def payload(*, structured=False):
    lines = [
        "  KIMBIES toallitas x48u $1.200 por sobre  ",
        "6 botellas de 1,5 L — antes $15.000, ahora $12.000 por bulto, mínimo 2 bultos",
        "10% por display",
        "Arroz 1kg $900; Arroz 500g $500",
        "",
    ]
    blocks = [
        {
            "fragments": [
                {
                    "text": line,
                    "legibility": "readable" if line else "unreadable",
                }
            ],
            "page_condition_indices": [0],
            "warnings": ["Texto ilegible"] if not line else [],
        }
        for line in lines
    ]
    if structured:
        blocks[0]["proposals"] = [
            proposal(
                raw_name="KIMBIES toallitas x48u",
                retail_contents_count=48,
                offer_price=1200,
                price_type="unit",
                field_evidence=[
                    {
                        "field": "retail_contents_count",
                        "refs": [{"scope": "block", "fragment_index": 0, "quote": "x48u"}],
                    }
                ],
            )
        ]
        blocks[1]["proposals"] = [
            proposal(
                raw_name="botellas",
                offer_price=12000,
                package_quantity=6,
                unit_size=1.5,
                unit="l",
                price_type="pack",
                minimum_purchase={
                    "value": 2,
                    "unit": "bultos",
                    "scope": "offer",
                    "refs": [{"scope": "block", "fragment_index": 0, "quote": "mínimo 2 bultos"}],
                },
            )
        ]
        blocks[2]["proposals"] = [proposal(raw_name="Display", offer_price=None)]
        blocks[3]["proposals"] = [
            proposal(raw_name="Arroz 1kg", offer_price=900),
            proposal(raw_name="Arroz 500g", offer_price=500),
        ]
        blocks[4]["proposals"] = []
    return {
        "supplier_name": None,
        "warnings": [],
        "pages": [
            {
                "page_number": 2,
                "page_conditions": [{"text": "IVA incluido", "legibility": "readable"}],
                "blocks": blocks,
                "coverage": {
                    "estimated_offer_blocks": 5,
                    "needs_visual_review": True,
                    "warnings": [],
                },
            }
        ],
    }


def response(data=None, **overrides):
    return SimpleNamespace(
        **(
            {
                "id": "resp-test",
                "model": "current-model",
                "status": "completed",
                "output": [],
                "output_text": json.dumps(data or payload()),
                "usage": SimpleNamespace(
                    input_tokens=100,
                    input_tokens_details=SimpleNamespace(cached_tokens=40),
                    output_tokens=20,
                    output_tokens_details=SimpleNamespace(reasoning_tokens=5),
                    total_tokens=120,
                ),
            }
            | overrides
        )
    )


@pytest.fixture
def reader_harness(monkeypatch, tmp_path):
    calls, configurations = [], []
    current = {"response": response()}

    class Client:
        def __init__(self, **kwargs):
            configurations.append(kwargs)
            self.responses = self

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def create(self, **kwargs):
            calls.append(kwargs)
            if isinstance(current["response"], Exception):
                raise current["response"]
            return current["response"]

    monkeypatch.setattr("openai.OpenAI", Client)
    document = tmp_path / "chunk.pdf"
    document.write_bytes(b"synthetic PDF transport fixture")
    request = DocumentReadRequest(
        document_path=document,
        run_id="run-1",
        reading_id="initial-chunk-1",
        analysis_method=AnalysisMethod.OPENAI_BLOCKS_JEV,
        phase="initial",
        source_filename="original.pdf",
        start_page=7,
        end_page=8,
        supplier_hint="Proveedor",
        timeout_seconds=11,
    )
    provider = OpenAISupplierOfferDocumentProvider(
        api_key="fake-key",
        model="current-model",
        timeout_seconds=60,
    )
    return provider, request, current, calls, configurations


@pytest.mark.parametrize("structured", [True, False])
def test_v2_single_inline_request_uses_frozen_schema_model_and_local_pages(
    reader_harness,
    structured,
):
    reader, request, current, calls, configurations = reader_harness
    request.analysis_method = (
        AnalysisMethod.OPENAI_STRUCTURED if structured else AnalysisMethod.OPENAI_BLOCKS_JEV
    )
    current["response"] = response(payload(structured=structured))
    result = reader.read(request=request)
    assert result.call.status == "success"
    assert len(calls) == 1
    assert configurations == [{"api_key": "fake-key", "timeout": 11, "max_retries": 0}]
    call = calls[0]
    assert call["model"] == "current-model"
    expected = ReaderStructuredExtractionV2 if structured else ReaderBlocksExtractionV2
    assert isinstance(result.extraction, expected)
    assert call["text"]["format"]["name"] == expected.__name__
    assert call["text"]["format"]["strict"] is True
    assert "Attached page count: 2" in call["input"][0]["content"][0]["text"]
    assert "original.pdf" in call["input"][0]["content"][0]["text"]
    content = call["input"][0]["content"][1]
    assert content["type"] == "input_file"
    assert base64.b64decode(content["file_data"].split(",", 1)[1]) == (
        request.document_path.read_bytes()
    )
    assert result.extraction.pages[0].page_number == 2
    # Only the integrating service maps this 2 to original page 8.
    context = DocumentPageContext(source_filename="original.pdf", start_page=7, end_page=8)
    assert context.original_page(result.extraction.pages[0].page_number) == 8
    assert "LOCAL" in call["instructions"]
    assert result.call.start_page == 7 and result.call.end_page == 8
    assert result.call.schema_version == WIRE_VERSION
    assert result.call.prompt_version == (
        "supplier_offer_structured_v3" if structured else "supplier_offer_blocks_v1"
    )


def test_v2_image_transport_is_inline(reader_harness):
    reader, request, _, calls, _ = reader_harness
    request.document_path = request.document_path.with_suffix(".png")
    request.document_path.write_bytes(b"synthetic image")
    assert reader.read(request=request).call.status == "success"
    content = calls[0]["input"][0]["content"][1]
    assert content == {
        "type": "input_image",
        "image_url": "data:image/png;base64," + base64.b64encode(b"synthetic image").decode(),
    }


def test_a_preserves_prices_contents_minimum_variants_and_unpriced_blocks(reader_harness):
    reader, request, current, _, _ = reader_harness
    request.analysis_method = AnalysisMethod.OPENAI_STRUCTURED
    current["response"] = response(payload(structured=True))
    extraction = reader.read(request=request).extraction
    blocks = extraction.pages[0].blocks
    assert len(blocks) == 5
    contents = blocks[0].proposals[0]
    assert contents.retail_contents_count == 48
    assert contents.package_quantity is None
    pack = blocks[1].proposals[0]
    assert pack.package_quantity == 6 and pack.offer_price == 12000
    assert pack.minimum_purchase.value == 2
    assert pack.minimum_purchase.unit == "bultos"
    assert pack.minimum_purchase.scope == "offer"
    assert pack.minimum_purchase.refs[0].quote == "mínimo 2 bultos"
    assert "antes $15.000" in blocks[1].fragments[0].text
    assert blocks[2].proposals[0].offer_price is None
    assert blocks[2].proposals[0].price_type == "unknown"
    assert blocks[2].proposals[0].confidence_score is None
    assert len(blocks[3].proposals) == 2
    assert blocks[4].fragments[0].legibility == "unreadable"


def test_b_preserves_order_spaces_mixed_lines_conditions_and_empty_text(reader_harness):
    reader, request, _, _, _ = reader_harness
    result = reader.read(request=request)
    assert result.extraction.model_dump(mode="json") == payload()
    assert len(result.extraction.pages[0].blocks) == 5
    assert not hasattr(result.extraction.pages[0].blocks[0], "proposals")
    assert result.extraction.pages[0].blocks[0].fragments[0].text.startswith("  ")


def test_usage_captures_subsets_without_adding_them_and_attempt_identity(reader_harness):
    reader, request, _, calls, _ = reader_harness
    first = reader.read(request=request)
    request.reading_id, request.phase = "reread-1", "reread"
    second = reader.read(request=request)
    assert first.call.call_id != second.call.call_id
    assert first.call.reading_id == "initial-chunk-1" and second.call.reading_id == "reread-1"
    assert second.call.phase == "reread"
    assert len(calls) == 2
    assert first.call.usage.model_dump() == {
        "input_tokens": 100,
        "cached_input_tokens": 40,
        "output_tokens": 20,
        "reasoning_output_tokens": 5,
        "total_tokens": 120,
    }
    assert first.call.response_id == "resp-test"
    assert first.call.duration_seconds is not None and first.call.duration_seconds >= 0
    assert first.call.cost is None


@pytest.mark.parametrize(
    "failure,expected",
    [
        ("refusal", "refusal"),
        ("incomplete", "incomplete"),
        ("no_text", "error"),
        ("bad_json", "error"),
        ("schema_invalid", "error"),
        ("failed", "error"),
    ],
)
def test_failures_keep_response_metrics_without_successful_extraction(
    reader_harness,
    failure,
    expected,
):
    reader, request, current, calls, _ = reader_harness
    overrides = {}
    if failure == "refusal":
        overrides["output"] = [
            SimpleNamespace(
                content=[
                    SimpleNamespace(
                        type="refusal",
                        refusal="untrusted refusal text",
                    )
                ]
            )
        ]
    elif failure in {"incomplete", "failed"}:
        overrides["status"] = failure
    elif failure == "no_text":
        overrides["output_text"] = ""
    elif failure == "bad_json":
        overrides["output_text"] = '{"supplier_name":'
    else:
        overrides["output_text"] = '{"supplier_name": null, "pages": [], "extra": 42}'
    current["response"] = response(**overrides)
    result = reader.read(request=request)
    assert result.extraction is None
    assert result.call.status == expected
    assert result.call.response_id == "resp-test"
    assert result.call.usage.total_tokens == 120
    assert result.call.duration_seconds is not None
    assert result.call.safe_error.startswith("openai_")
    assert "untrusted" not in result.call.safe_error
    assert len(calls) == 1


def test_transport_error_returns_nullable_usage_and_safe_envelope(reader_harness, caplog):
    reader, request, current, calls, _ = reader_harness
    current["response"] = TimeoutError("credential=secret; document=private")
    current["response"].__cause__ = ConnectionError("secret transport details")
    current["response"].request_id = "req_safe_123"
    with caplog.at_level("INFO", logger="uvicorn.error"):
        result = reader.read(request=request)
    assert result.extraction is None and result.call.status == "error"
    assert result.call.usage is None and result.call.response_id is None
    assert result.call.safe_error == "openai_read_failed:TimeoutError"
    assert result.call.duration_seconds is not None
    assert len(calls) == 1
    assert "error_type=TimeoutError cause_type=ConnectionError" in caplog.text
    assert "request_id=req_safe_123" in caplog.text
    assert "timeout_seconds=11.000" in caplog.text
    assert "secret" not in caplog.text and "private" not in caplog.text
    assert "fake-key" not in caplog.text


def test_reader_logs_reject_untrusted_request_id(reader_harness, caplog):
    reader, request, current, _, _ = reader_harness
    current["response"] = TimeoutError("sensitive")
    current["response"].request_id = "req_bad\ncredential=private"
    with caplog.at_level("INFO", logger="uvicorn.error"):
        reader.read(request=request)
    assert "request_id=None" in caplog.text
    assert "credential" not in caplog.text and "private" not in caplog.text


def test_raw_installed_sdk_response_keeps_text_and_usage(reader_harness):
    from openai.types.responses import Response

    reader, request, current, _, _ = reader_harness
    current["response"] = Response.model_construct(
        id="resp-sdk",
        model="current-model",
        status="completed",
        output=[
            {
                "type": "message",
                "id": "msg-1",
                "status": "completed",
                "role": "assistant",
                "content": [
                    {"type": "output_text", "text": json.dumps(payload()), "annotations": []}
                ],
            }
        ],
        usage={
            "input_tokens": 100,
            "output_tokens": 20,
            "total_tokens": 120,
            "input_tokens_details": {"cached_tokens": 40},
            "output_tokens_details": {"reasoning_tokens": 5},
        },
    )
    result = reader.read(request=request)
    assert result.call.status == "success"
    assert result.call.response_id == "resp-sdk"
    assert result.extraction.model_dump(mode="json") == payload()
    assert result.call.usage.total_tokens == 120


def test_response_without_usage_attribute_keeps_unknown(reader_harness):
    reader, request, current, _, _ = reader_harness
    del current["response"].usage
    result = reader.read(request=request)
    assert result.call.status == "success"
    assert result.call.usage is None


@pytest.mark.parametrize(
    "usage",
    [
        None,
        {},
        {"input_tokens": 12},
        {
            "input_tokens": True,
            "output_tokens": -1,
            "total_tokens": "100",
        },
    ],
)
def test_missing_or_partial_usage_stays_unknown(reader_harness, usage):
    reader, request, current, _, _ = reader_harness
    current["response"] = response(usage=usage)
    result = reader.read(request=request)
    if usage is None:
        assert result.call.usage is None
    else:
        assert result.call.usage.total_tokens is None
        assert result.call.usage.cached_input_tokens is None
        assert result.call.usage.output_tokens is None
        assert result.call.usage.input_tokens == (12 if usage == {"input_tokens": 12} else None)


def test_invalid_request_range_fails_before_provider(reader_harness):
    reader, request, _, calls, _ = reader_harness
    request.end_page = 6
    result = reader.read(request=request)
    assert result.call.status == "error" and result.extraction is None
    assert not calls


@pytest.mark.parametrize("schema_type", [ReaderStructuredExtractionV2, ReaderBlocksExtractionV2])
def test_wire_schemas_are_closed_required_and_do_not_duplicate_text(schema_type):
    from openai.lib._pydantic import to_strict_json_schema

    schema = to_strict_json_schema(schema_type)

    def visit(node):
        if isinstance(node, dict):
            assert "(?" not in node.get("pattern", "")
            if node.get("type") == "object":
                assert node["additionalProperties"] is False
                assert set(node["required"]) == set(node["properties"])
                assert "full_text" not in node["properties"]
                assert "block_id" not in node["properties"]
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)

    visit(schema)
    if schema_type is ReaderStructuredExtractionV2:
        minimum = schema["$defs"]["ReaderMinimumPurchaseV2"]["properties"]
        assert set(minimum) == {"value", "unit", "scope", "refs"}
        price = schema["$defs"]["ReaderOfferProposalV2"]["properties"]["offer_price"]
        assert {branch["type"] for branch in price["anyOf"]} == {"number", "null"}


def test_wrong_page_and_refs_are_retained_for_backend_validation(reader_harness):
    reader, request, current, _, _ = reader_harness
    request.analysis_method = AnalysisMethod.OPENAI_STRUCTURED
    data = payload(structured=True)
    data["pages"][0]["page_number"] = 90
    data["pages"][0]["blocks"][0]["page_condition_indices"] = [99]
    current["response"] = response(data)
    result = reader.read(request=request)
    assert result.call.status == "success"  # Valid wire is not a commercial confirmation.
    assert result.extraction.pages[0].page_number == 90
    assert result.extraction.pages[0].blocks[0].page_condition_indices == [99]


@pytest.mark.parametrize("request_timeout,expected", [(180, 60), (5, 5)])
def test_v2_caps_transport_with_configured_reader_timeout(
    reader_harness, request_timeout, expected
):
    reader, request, _, _, configurations = reader_harness
    request.timeout_seconds = request_timeout
    assert reader.read(request=request).call.status == "success"
    assert configurations[0]["timeout"] == expected
