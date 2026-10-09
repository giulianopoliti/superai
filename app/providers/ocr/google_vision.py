import base64
import json
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

from app.modules.procurement.ocr_price_candidates import (
    OcrBoundingBox,
    OcrPageResult,
    OcrWord,
)

Transport = Callable[[str, bytes, float], dict[str, Any]]


class GoogleVisionOcrProvider:
    def __init__(
        self,
        *,
        api_key: str,
        timeout_seconds: float,
        transport: Transport | None = None,
    ) -> None:
        self._api_key = api_key
        self._timeout_seconds = timeout_seconds
        self._transport = transport or self._default_transport

    def extract_page_words(self, *, image_path: Path, page_number: int) -> OcrPageResult:
        payload = {
            "requests": [
                {
                    "image": {
                        "content": base64.b64encode(image_path.read_bytes()).decode("ascii"),
                    },
                    "features": [{"type": "DOCUMENT_TEXT_DETECTION"}],
                }
            ]
        }
        url = f"https://vision.googleapis.com/v1/images:annotate?key={self._api_key}"
        response = self._transport(url, json.dumps(payload).encode("utf-8"), self._timeout_seconds)
        api_response = response.get("responses", [{}])[0]
        if "error" in api_response:
            message = api_response["error"].get("message", "Google Vision OCR failed.")
            raise ValueError(message)
        return OcrPageResult(
            page_number=page_number,
            words=self._parse_words(api_response),
        )

    @staticmethod
    def _default_transport(url: str, payload: bytes, timeout_seconds: float) -> dict[str, Any]:
        request = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            try:
                payload = json.loads(body)
                message = payload.get("error", {}).get("message") or body
            except json.JSONDecodeError:
                message = body
            raise ValueError(f"Google Vision OCR request failed ({exc.code}): {message}") from exc

    @staticmethod
    def _parse_words(api_response: dict[str, Any]) -> list[OcrWord]:
        words: list[OcrWord] = []
        pages = api_response.get("fullTextAnnotation", {}).get("pages", [])
        for page in pages:
            for block in page.get("blocks", []):
                for paragraph in block.get("paragraphs", []):
                    for word in paragraph.get("words", []):
                        text = "".join(
                            symbol.get("text", "") for symbol in word.get("symbols", [])
                        ).strip()
                        if not text:
                            continue
                        words.append(
                            OcrWord(
                                text=text,
                                bounding_box=_parse_bounding_box(word.get("boundingBox", {})),
                                confidence=word.get("confidence"),
                            )
                        )
        return words


def _parse_bounding_box(payload: dict[str, Any]) -> OcrBoundingBox:
    vertices = payload.get("vertices") or payload.get("normalizedVertices") or []
    xs = [int(vertex.get("x", 0)) for vertex in vertices]
    ys = [int(vertex.get("y", 0)) for vertex in vertices]
    if not xs or not ys:
        return OcrBoundingBox()
    return OcrBoundingBox(
        left=min(xs),
        top=min(ys),
        right=max(xs),
        bottom=max(ys),
    )
