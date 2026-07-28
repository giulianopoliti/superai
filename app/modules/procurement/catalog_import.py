import csv
from datetime import UTC, datetime
from pathlib import Path

from app.db.repositories.sql_procurement import SqlProcurementRepository
from app.modules.procurement.normalization import (
    normalize_text,
    parse_argentine_decimal,
    parse_unit,
)
from app.modules.procurement.schemas import CatalogImportResult, CatalogImportStatus, Product


class PosCatalogImportService:
    def __init__(self, repository: SqlProcurementRepository) -> None:
        self._repository = repository

    def import_csv(self, *, business_id: str, csv_path: Path) -> CatalogImportResult:
        result = CatalogImportResult(
            business_id=business_id,
            source_filename=csv_path.name,
        )
        result = self._repository.add_catalog_import(result)

        try:
            with csv_path.open("r", encoding="utf-8-sig", newline="") as file:
                reader = csv.DictReader(file, delimiter=";")
                products: list[Product] = []
                for line_number, row in enumerate(reader, start=2):
                    result.row_count += 1
                    product = self._product_from_row(business_id=business_id, row=row)
                    if product is None:
                        result.skipped_count += 1
                        result.errors.append(f"line {line_number}: missing product name")
                        continue

                    products.append(product)
                    result.imported_count += 1

            self._repository.upsert_products(products)
            result.status = CatalogImportStatus.COMPLETED
            result.summary = self._repository.summarize_products(business_id=business_id)
            result.completed_at = datetime.now(UTC)
            return self._repository.update_catalog_import(result)
        except Exception as exc:
            result.status = CatalogImportStatus.FAILED
            result.errors.append(str(exc))
            result.completed_at = datetime.now(UTC)
            self._repository.update_catalog_import(result)
            raise

    @staticmethod
    def _product_from_row(*, business_id: str, row: dict[str, str]) -> Product | None:
        name = (row.get("snombre") or "").strip()
        if not name:
            return None

        unit_size, unit = parse_unit(name)
        barcode = (row.get("sean") or "").strip() or None
        if barcode == "0":
            barcode = None

        return Product(
            business_id=business_id,
            external_product_id=(row.get("id") or "").strip() or None,
            sku=(row.get("scodproducto") or "").strip() or None,
            barcode=barcode,
            name=name,
            normalized_name=normalize_text(name),
            category=(row.get("sfamilia") or "").strip() or None,
            unit_size=unit_size,
            unit=unit,
            sale_price=parse_argentine_decimal(row.get("rpreciou")),
            current_cost=parse_argentine_decimal(row.get("rcostou")),
            margin_percentage=parse_argentine_decimal(row.get("rmargenganancia")),
            stock_quantity=parse_argentine_decimal(row.get("rstock")),
            active=(row.get("bactivo") or "T").strip().upper() == "T",
            source="pos_csv",
        )
