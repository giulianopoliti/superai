import json
from decimal import Decimal

from app.modules.procurement.ocr_price_candidates import (
    OcrBoundingBox,
    OcrPageResult,
    OcrWord,
    extract_price_candidates,
)
from app.providers.ocr.google_vision import GoogleVisionOcrProvider


def _word_payload(text: str, left: int, top: int, right: int, bottom: int) -> dict:
    return {
        "symbols": [{"text": character} for character in text],
        "boundingBox": {
            "vertices": [
                {"x": left, "y": top},
                {"x": right, "y": top},
                {"x": right, "y": bottom},
                {"x": left, "y": bottom},
            ]
        },
        "confidence": 0.91,
    }


def test_google_vision_ocr_provider_extracts_words(tmp_path) -> None:
    image_path = tmp_path / "page.jpg"
    image_path.write_bytes(b"fake-image")
    seen_payloads: list[dict] = []

    def fake_transport(url: str, payload: bytes, timeout_seconds: float) -> dict:
        assert url.startswith("https://vision.googleapis.com/v1/images:annotate?key=test-key")
        assert timeout_seconds == 12
        seen_payloads.append(json.loads(payload.decode("utf-8")))
        return {
            "responses": [
                {
                    "fullTextAnnotation": {
                        "pages": [
                            {
                                "blocks": [
                                    {
                                        "paragraphs": [
                                            {
                                                "words": [
                                                    _word_payload("CAÑUELAS", 10, 20, 80, 35),
                                                    _word_payload("$", 100, 20, 110, 35),
                                                    _word_payload("2.849", 112, 20, 160, 35),
                                                ]
                                            }
                                        ]
                                    }
                                ]
                            }
                        ]
                    }
                }
            ]
        }

    provider = GoogleVisionOcrProvider(
        api_key="test-key",
        timeout_seconds=12,
        transport=fake_transport,
    )

    page = provider.extract_page_words(image_path=image_path, page_number=3)

    assert seen_payloads[0]["requests"][0]["features"][0]["type"] == "DOCUMENT_TEXT_DETECTION"
    assert seen_payloads[0]["requests"][0]["image"]["content"]
    assert page.page_number == 3
    assert [word.text for word in page.words] == ["CAÑUELAS", "$", "2.849"]
    assert page.words[2].bounding_box.left == 112
    assert page.words[2].confidence == 0.91


def test_extract_price_candidates_groups_split_currency_symbol() -> None:
    page = OcrPageResult(
        page_number=1,
        words=[
            OcrWord(
                text="ACEITE",
                bounding_box=OcrBoundingBox(left=10, top=10, right=70, bottom=25),
                confidence=0.92,
            ),
            OcrWord(
                text="GIRASOL",
                bounding_box=OcrBoundingBox(left=75, top=10, right=140, bottom=25),
                confidence=0.91,
            ),
            OcrWord(
                text="$",
                bounding_box=OcrBoundingBox(left=160, top=10, right=170, bottom=25),
                confidence=0.9,
            ),
            OcrWord(
                text="2.849",
                bounding_box=OcrBoundingBox(left=172, top=10, right=220, bottom=25),
                confidence=0.89,
            ),
        ],
    )

    candidates = extract_price_candidates(page)

    assert len(candidates) == 1
    assert candidates[0].price_text == "$2.849"
    assert candidates[0].price_value == Decimal("2849")
    assert candidates[0].nearby_text == "ACEITE GIRASOL $ 2.849"
    assert candidates[0].bounding_box.left == 160
    assert candidates[0].bounding_box.right == 220
