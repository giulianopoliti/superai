from datetime import UTC, datetime
from decimal import Decimal

from app.db.repositories.sql_procurement import SqlProcurementRepository
from app.modules.procurement.analysis_contracts import AnalysisLedger, AnalysisSummary
from app.modules.procurement.document_analysis import validate_metadata_size
from app.modules.procurement.normalization import (
    normalize_text,
    parse_package_quantity,
    parse_unit,
)
from app.modules.procurement.pricing import SupplierPriceType
from app.modules.procurement.schemas import (
    ExtractedSupplierOfferItem,
    Supplier,
    SupplierOfferDocument,
    SupplierOfferDocumentStatus,
    SupplierOfferExtraction,
    SupplierOfferImportResult,
    SupplierOfferItem,
)


class SupplierOfferService:
    def __init__(
        self, repository: SqlProcurementRepository, *, max_metadata_bytes=4_000_000,
    ) -> None:
        self._repository = repository
        self._max_metadata_bytes = max_metadata_bytes

    def upsert_supplier(
        self, *, business_id: str, supplier_name: str, notes: str | None = None
    ) -> Supplier:
        return self._repository.upsert_supplier(
            Supplier(
                business_id=business_id,
                name=supplier_name.strip(),
                normalized_name=normalize_text(supplier_name),
                notes=notes,
            )
        )

    def create_manual_offer(
        self,
        *,
        business_id: str,
        supplier_name: str,
        source_filename: str,
        items: list[dict[str, object]],
        raw_text: str | None = None,
    ) -> SupplierOfferImportResult:
        return self._create_offer(
            business_id=business_id,
            supplier_name=supplier_name,
            source_filename=source_filename,
            raw_text=raw_text,
            items=items,
            document_type="manual",
            extraction_provider="manual",
        )

    def create_offer_from_extraction(
        self,
        *,
        business_id: str,
        supplier_name: str,
        extraction: SupplierOfferExtraction,
    ) -> SupplierOfferImportResult:
        document_id = None
        if extraction.document_metadata.get("analysis_v2"):
            summary = AnalysisSummary.model_validate(extraction.document_metadata["analysis"])
            ledger = AnalysisLedger.model_validate(extraction.document_metadata["analysis_ledger"])
            if (ledger.business_id != business_id or ledger.run_id != summary.run_id
                    or ledger.method != summary.method or len(summary.run_id) > 64):
                raise ValueError("Analysis ledger belongs to another tenant/execution")
            document_id = summary.run_id
            previous = self._repository.get_supplier_offer_document(
                business_id=business_id, supplier_offer_document_id=document_id,
            )
            if previous is not None:
                if previous.metadata.get("analysis_ledger") != ledger.model_dump(mode="json"):
                    raise ValueError("This execution has already persisted different evidence")
                if previous.completed_at is None:
                    raise ValueError(
                        "This execution has incomplete persistence; review is required"
                    )
                return SupplierOfferImportResult(
                    document=previous,
                    items=self._repository.list_supplier_offer_items(
                        business_id=business_id, supplier_offer_document_id=document_id,
                    ),
                )
        return self._create_offer(
            business_id=business_id,
            supplier_name=supplier_name,
            source_filename=extraction.source_filename or "supplier-offer",
            raw_text=extraction.raw_text,
            items=[
                self._item_dict_from_extracted_item(item, extraction.warnings)
                for item in extraction.items
            ],
            document_type="extracted",
            extraction_provider="document_extraction",
            document_metadata=extraction.document_metadata,
            document_id=document_id,
        )

    def _create_offer(
        self,
        *,
        business_id: str,
        supplier_name: str,
        source_filename: str,
        items: list[dict[str, object]],
        raw_text: str | None = None,
        document_type: str,
        extraction_provider: str,
        document_metadata: dict[str, object] | None = None,
        document_id: str | None = None,
    ) -> SupplierOfferImportResult:
        if document_metadata is not None:
            document_metadata = {**document_metadata, "item_count": len(items)}
            validate_metadata_size(document_metadata, max_bytes=self._max_metadata_bytes)
        supplier = self.upsert_supplier(business_id=business_id, supplier_name=supplier_name)
        document = self._repository.add_supplier_offer_document(
            SupplierOfferDocument(
                **({"id": document_id} if document_id else {}),
                business_id=business_id,
                supplier_id=supplier.id,
                source_filename=source_filename,
                document_type=document_type,
                extraction_status=SupplierOfferDocumentStatus.PENDING,
                extraction_provider=extraction_provider,
                raw_text=raw_text,
                metadata=dict(document_metadata or {}),
            )
        )
        offer_items = [
            self._build_item(
                business_id=business_id,
                supplier_id=supplier.id,
                document_id=document.id,
                item=item,
            )
            for item in items
        ]
        saved_items = self._repository.add_supplier_offer_items(offer_items)
        document.extraction_status = (
            SupplierOfferDocumentStatus.FAILED
            if not saved_items and document_metadata and document_metadata.get("needs_review_pages")
            else SupplierOfferDocumentStatus.EXTRACTED
        )
        document.completed_at = datetime.now(UTC)
        metadata = dict(document_metadata or {})
        metadata["item_count"] = len(saved_items)
        document.metadata = metadata
        document = self._repository.update_supplier_offer_document(document)
        return SupplierOfferImportResult(document=document, items=saved_items)

    @staticmethod
    def _item_dict_from_extracted_item(
        item: ExtractedSupplierOfferItem,
        warnings: list[str],
    ) -> dict[str, object]:
        metadata: dict[str, object] = {
            "price_type": item.price_type.value,
        }
        analysis = item.provider_observations.get("analysis_v2")
        if analysis:
            metadata["analysis_v2"] = analysis
        if item.pack_review:
            metadata["pack_review"] = item.pack_review
        if item.selected_source or item.provider_observations:
            metadata.update(
                {
                    "agreement_score": float(item.agreement_score)
                    if item.agreement_score is not None
                    else None,
                    "agreement_status": item.agreement_status,
                    "selected_source": item.selected_source,
                    "resolved_by": item.resolved_by,
                    "provider_observations": item.model_dump(mode="json")["provider_observations"],
                }
            )
        if item.notes:
            metadata["notes"] = item.notes
        if warnings:
            metadata["document_warnings"] = warnings

        return {
            "raw_name": item.raw_name,
            "brand": item.brand,
            "unit_size": item.unit_size,
            "unit": item.unit,
            "package_quantity": item.package_quantity,
            "offer_price": item.offer_price,
            "price_type": item.price_type.value,
            "currency": item.currency,
            "tax_included": item.tax_included,
            "page_number": item.page_number,
            "confidence_score": item.confidence_score,
            "metadata": metadata,
        }

    @staticmethod
    def _build_item(
        *,
        business_id: str,
        supplier_id: str,
        document_id: str,
        item: dict[str, object],
    ) -> SupplierOfferItem:
        raw_name = str(item["raw_name"]).strip()
        parsed_unit_size, parsed_unit = parse_unit(raw_name)
        parsed_package_quantity = parse_package_quantity(raw_name)
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        is_v2 = bool(metadata.get("analysis_v2"))
        unit_size = item.get("unit_size") if is_v2 else item.get("unit_size") or parsed_unit_size
        unit = item.get("unit") if is_v2 else item.get("unit") or parsed_unit
        raw_price_type = item.get("price_type") or metadata.get("price_type") or "unit"
        return SupplierOfferItem(
            **({"id": metadata["analysis_v2"]["offer_id"]}
               if metadata.get("analysis_v2") else {}),
            business_id=business_id,
            supplier_id=supplier_id,
            supplier_offer_document_id=document_id,
            raw_name=raw_name,
            normalized_name=normalize_text(raw_name),
            brand=str(item["brand"]).strip() if item.get("brand") else None,
            unit_size=Decimal(str(unit_size)) if unit_size is not None else None,
            unit=str(unit) if unit else None,
            package_quantity=(
                int(item["package_quantity"])
                if item.get("package_quantity")
                else None if metadata.get("pack_review") or metadata.get("analysis_v2")
                else parsed_package_quantity
            ),
            offer_price=Decimal(str(item["offer_price"])),
            price_type=SupplierPriceType(str(raw_price_type).lower()),
            currency=str(item.get("currency") or "ARS"),
            tax_included=(
                item.get("tax_included") if isinstance(item.get("tax_included"), bool) else None
            ),
            page_number=int(item["page_number"]) if item.get("page_number") else None,
            confidence_score=(
                Decimal(str(item["confidence_score"])) if item.get("confidence_score") else None
            ),
            metadata=metadata,
        )
