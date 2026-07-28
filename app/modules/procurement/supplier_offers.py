from datetime import UTC, datetime
from decimal import Decimal

from app.db.repositories.sql_procurement import SqlProcurementRepository
from app.modules.procurement.normalization import normalize_text, parse_unit
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
    def __init__(self, repository: SqlProcurementRepository) -> None:
        self._repository = repository

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
    ) -> SupplierOfferImportResult:
        supplier = self.upsert_supplier(business_id=business_id, supplier_name=supplier_name)
        document = self._repository.add_supplier_offer_document(
            SupplierOfferDocument(
                business_id=business_id,
                supplier_id=supplier.id,
                source_filename=source_filename,
                document_type=document_type,
                extraction_status=SupplierOfferDocumentStatus.PENDING,
                extraction_provider=extraction_provider,
                raw_text=raw_text,
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
        document.extraction_status = SupplierOfferDocumentStatus.EXTRACTED
        document.completed_at = datetime.now(UTC)
        document.metadata = {"item_count": len(saved_items)}
        document = self._repository.update_supplier_offer_document(document)
        return SupplierOfferImportResult(document=document, items=saved_items)

    @staticmethod
    def _item_dict_from_extracted_item(
        item: ExtractedSupplierOfferItem,
        warnings: list[str],
    ) -> dict[str, object]:
        metadata: dict[str, object] = {
            "price_type": item.price_type,
        }
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
        unit_size = item.get("unit_size") or parsed_unit_size
        unit = item.get("unit") or parsed_unit
        return SupplierOfferItem(
            business_id=business_id,
            supplier_id=supplier_id,
            supplier_offer_document_id=document_id,
            raw_name=raw_name,
            normalized_name=normalize_text(raw_name),
            brand=str(item["brand"]).strip() if item.get("brand") else None,
            unit_size=Decimal(str(unit_size)) if unit_size is not None else None,
            unit=str(unit) if unit else None,
            package_quantity=(
                int(item["package_quantity"]) if item.get("package_quantity") else None
            ),
            offer_price=Decimal(str(item["offer_price"])),
            currency=str(item.get("currency") or "ARS"),
            tax_included=(
                item.get("tax_included") if isinstance(item.get("tax_included"), bool) else None
            ),
            page_number=int(item["page_number"]) if item.get("page_number") else None,
            confidence_score=(
                Decimal(str(item["confidence_score"])) if item.get("confidence_score") else None
            ),
            metadata=item.get("metadata") if isinstance(item.get("metadata"), dict) else {},
        )
