import re
from decimal import Decimal, InvalidOperation

from pydantic import BaseModel, Field


class OcrBoundingBox(BaseModel):
    left: int = 0
    top: int = 0
    right: int = 0
    bottom: int = 0

    @property
    def center_y(self) -> float:
        return (self.top + self.bottom) / 2

    def union(self, other: "OcrBoundingBox") -> "OcrBoundingBox":
        return OcrBoundingBox(
            left=min(self.left, other.left),
            top=min(self.top, other.top),
            right=max(self.right, other.right),
            bottom=max(self.bottom, other.bottom),
        )


class OcrWord(BaseModel):
    text: str
    bounding_box: OcrBoundingBox
    confidence: float | None = None


class OcrPriceCandidate(BaseModel):
    page_number: int
    price_text: str
    price_value: Decimal | None = None
    bounding_box: OcrBoundingBox
    nearby_text: str
    confidence: float | None = None


class OcrPageResult(BaseModel):
    page_number: int
    words: list[OcrWord] = Field(default_factory=list)


class OcrPageInspection(BaseModel):
    page_number: int
    word_count: int
    price_candidate_count: int
    price_candidates: list[OcrPriceCandidate]


class SupplierOfferOcrInspection(BaseModel):
    source_filename: str
    page_count: int
    total_word_count: int
    total_price_candidate_count: int
    pages: list[OcrPageInspection]


PRICE_NUMBER_RE = re.compile(r"^\d{2,6}(?:[.,]\d{2})?$|^\d{1,3}(?:[.]\d{3})+(?:,\d{2})?$")
PRICE_WITH_SYMBOL_RE = re.compile(
    r"^\$?\s*\d{2,6}(?:[.,]\d{2})?$|^\$?\s*\d{1,3}(?:[.]\d{3})+(?:,\d{2})?$"
)


def build_supplier_offer_ocr_inspection(
    *,
    source_filename: str,
    pages: list[OcrPageResult],
) -> SupplierOfferOcrInspection:
    page_inspections = [
        OcrPageInspection(
            page_number=page.page_number,
            word_count=len(page.words),
            price_candidates=extract_price_candidates(page),
            price_candidate_count=0,
        )
        for page in pages
    ]
    for page in page_inspections:
        page.price_candidate_count = len(page.price_candidates)

    return SupplierOfferOcrInspection(
        source_filename=source_filename,
        page_count=len(page_inspections),
        total_word_count=sum(page.word_count for page in page_inspections),
        total_price_candidate_count=sum(
            page.price_candidate_count for page in page_inspections
        ),
        pages=page_inspections,
    )


def extract_price_candidates(page: OcrPageResult) -> list[OcrPriceCandidate]:
    words = sorted(page.words, key=lambda word: (word.bounding_box.top, word.bounding_box.left))
    candidates: list[OcrPriceCandidate] = []
    seen_boxes: set[tuple[int, int, int, int]] = set()
    consumed_indexes: set[int] = set()

    for index, _word in enumerate(words):
        if index in consumed_indexes:
            continue
        price_text = _candidate_price_text(words, index)
        if price_text is None:
            continue

        price_box = _candidate_price_box(words, index, price_text)
        if words[index].text.strip() == "$" and price_text.startswith("$"):
            consumed_indexes.add(index + 1)
        box_key = (price_box.left, price_box.top, price_box.right, price_box.bottom)
        if box_key in seen_boxes:
            continue
        seen_boxes.add(box_key)

        nearby_words = _nearby_words(words, price_box)
        confidences = [
            nearby_word.confidence
            for nearby_word in nearby_words
            if nearby_word.confidence is not None
        ]
        candidates.append(
            OcrPriceCandidate(
                page_number=page.page_number,
                price_text=price_text,
                price_value=_parse_price(price_text),
                bounding_box=price_box,
                nearby_text=" ".join(nearby_word.text for nearby_word in nearby_words),
                confidence=sum(confidences) / len(confidences) if confidences else None,
            )
        )

    return candidates


def _candidate_price_text(words: list[OcrWord], index: int) -> str | None:
    text = words[index].text.strip()
    compact_text = text.replace(" ", "")
    if PRICE_WITH_SYMBOL_RE.match(compact_text):
        return compact_text

    if compact_text == "$" and index + 1 < len(words):
        next_text = words[index + 1].text.strip()
        if PRICE_NUMBER_RE.match(next_text):
            return f"${next_text}"

    if compact_text.endswith("$"):
        number_text = compact_text[:-1]
        if PRICE_NUMBER_RE.match(number_text):
            return f"${number_text}"

    return None


def _candidate_price_box(words: list[OcrWord], index: int, price_text: str) -> OcrBoundingBox:
    box = words[index].bounding_box
    if words[index].text.strip() == "$" and price_text.startswith("$") and index + 1 < len(words):
        box = box.union(words[index + 1].bounding_box)
    return box


def _nearby_words(words: list[OcrWord], price_box: OcrBoundingBox) -> list[OcrWord]:
    left = price_box.left - 280
    right = price_box.right + 120
    top = price_box.top - 60
    bottom = price_box.bottom + 170
    nearby = [
        word
        for word in words
        if left <= word.bounding_box.left
        and word.bounding_box.right <= right
        and top <= word.bounding_box.center_y <= bottom
    ]
    return sorted(nearby, key=lambda word: (word.bounding_box.top, word.bounding_box.left))


def _parse_price(text: str) -> Decimal | None:
    normalized = text.replace("$", "").replace(" ", "").strip()
    if "," in normalized:
        normalized = normalized.replace(".", "").replace(",", ".")
    elif normalized.count(".") > 1:
        normalized = normalized.replace(".", "")
    else:
        parts = normalized.split(".")
        if len(parts) == 2 and len(parts[1]) == 3:
            normalized = "".join(parts)
    try:
        return Decimal(normalized)
    except (InvalidOperation, ValueError):
        return None
