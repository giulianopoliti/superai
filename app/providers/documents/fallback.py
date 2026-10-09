from pathlib import Path

from app.modules.procurement.schemas import SupplierOfferExtraction
from app.providers.documents.base import DocumentExtractionProvider


class DocumentExtractionUnavailableError(RuntimeError):
    pass


class FallbackDocumentExtractionProvider:
    """Try document readers in order and return the first valid extraction."""

    def __init__(
        self,
        providers: list[tuple[str, DocumentExtractionProvider]],
    ) -> None:
        if not providers:
            raise ValueError("At least one document extraction provider is required.")
        self._providers = providers

    def extract_supplier_offer(
        self,
        *,
        document_path: Path,
        supplier_hint: str | None = None,
    ) -> SupplierOfferExtraction:
        failures: list[str] = []
        for name, provider in self._providers:
            try:
                extraction = provider.extract_supplier_offer(
                    document_path=document_path,
                    supplier_hint=supplier_hint,
                )
            except Exception as exc:
                failures.append(f"{name}: {self._safe_error(exc)}")
                continue

            extraction.document_metadata = {
                **extraction.document_metadata,
                "selected_extraction_provider": name,
                "failed_extraction_providers": [failure.partition(":")[0] for failure in failures],
            }
            if failures:
                extraction.warnings.insert(
                    0,
                    f"{failures[0].partition(':')[0]} no estaba disponible; "
                    f"la lectura se completó con {name}.",
                )
            return extraction

        summary = " | ".join(failures)
        raise DocumentExtractionUnavailableError(f"No document reader was available. {summary}")

    @staticmethod
    def _safe_error(exc: Exception) -> str:
        status_code = getattr(exc, "status_code", None)
        if status_code:
            return f"HTTP {status_code}"
        message = " ".join(str(exc).split())
        if "503" in message or "UNAVAILABLE" in message.upper():
            return "temporarily unavailable"
        if "429" in message:
            return "rate limited"
        return type(exc).__name__
