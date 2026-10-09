from pathlib import Path
from typing import Protocol

from app.modules.procurement.ocr_price_candidates import OcrPageResult


class OcrProvider(Protocol):
    def extract_page_words(self, *, image_path: Path, page_number: int) -> OcrPageResult: ...
