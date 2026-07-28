from pathlib import Path
from typing import Protocol

from app.modules.procurement.schemas import SupplierOfferExtraction


class DocumentExtractionProvider(Protocol):
    def extract_supplier_offer(
        self,
        *,
        document_path: Path,
        supplier_hint: str | None = None,
    ) -> SupplierOfferExtraction:
        """Extract structured supplier offer data from a document."""
