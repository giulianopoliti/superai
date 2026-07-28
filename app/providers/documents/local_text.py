import re
from decimal import Decimal
from pathlib import Path

from app.modules.procurement.normalization import parse_unit
from app.modules.procurement.schemas import (
    ExtractedSupplierOfferItem,
    SupplierOfferExtraction,
)
from app.providers.documents.base import DocumentExtractionProvider

PRICE_PATTERN = re.compile(r"\$?\s*(?P<price>\d{2,}(?:[.,]\d{1,2})?)")


class LocalTextSupplierOfferProvider(DocumentExtractionProvider):
    """Cheap fallback for already-extracted text fixtures, not OCR."""

    def extract_supplier_offer(
        self,
        *,
        document_path: Path,
        supplier_hint: str | None = None,
    ) -> SupplierOfferExtraction:
        raw_text = document_path.read_text(encoding="utf-8-sig")
        items: list[ExtractedSupplierOfferItem] = []
        warnings: list[str] = []

        for line_number, line in enumerate(raw_text.splitlines(), start=1):
            stripped = line.strip().removeprefix("\ufeff")
            if not stripped:
                continue

            matches = list(PRICE_PATTERN.finditer(stripped))
            if not matches:
                warnings.append(f"line {line_number}: no price found")
                continue

            match = matches[-1]
            raw_name = stripped[: match.start()].strip(" -:\t")
            if not raw_name:
                warnings.append(f"line {line_number}: no product name found")
                continue

            unit_size, unit = parse_unit(raw_name)
            items.append(
                ExtractedSupplierOfferItem(
                    raw_name=raw_name,
                    unit_size=unit_size,
                    unit=unit,
                    offer_price=Decimal(match.group("price").replace(",", ".")),
                    confidence_score=Decimal("0.70"),
                    notes=f"Parsed from line {line_number}",
                )
            )

        return SupplierOfferExtraction(
            supplier_name=supplier_hint,
            source_filename=document_path.name,
            raw_text=raw_text,
            items=items,
            warnings=warnings,
        )
