import csv
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO

from app.db.repositories.sql_procurement import SqlProcurementRepository
from app.modules.procurement.normalization import (
    normalize_text,
    parse_argentine_decimal,
    parse_unit,
)
from app.modules.procurement.schemas import CatalogImportResult, CatalogImportStatus, Product

CSV_ENCODINGS = ("utf-8-sig", "cp1252", "latin-1")

SUSPICIOUS_CATEGORY_KEYS = {
    "",
    "general",
    "alimento para animales",
}

CATEGORY_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "PERFUMERIA",
        (
            "shampoo",
            "acondicionador",
            "algodon",
            "alcohol",
            "jabon",
            "desodorante",
            "toallita",
            "protector diario",
            "panal",
            "colgate",
            "crema dental",
            "gel",
            "sedal",
            "dove",
            "rexona",
            "plusbelle",
            "lysoform",
        ),
    ),
    (
        "LIMPIEZA",
        (
            "detergente",
            "lavandina",
            "suavizante",
            "limpia",
            "procenex",
            "poett",
            "harpic",
            "ayudin",
            "cif",
            "trapo",
            "rejilla",
            "esponja",
            "bolsa consorcio",
        ),
    ),
    (
        "BEBIDAS",
        (
            "vino",
            "vodka",
            "whisky",
            "fernet",
            "gin",
            "cerveza",
            "lata 473",
            "coca",
            "pepsi",
            "sprite",
            "fanta",
            "manaos",
            "agua",
            "speed",
            "aperitivo",
            "campari",
            "cynar",
            "uvita",
        ),
    ),
    (
        "FIAMBRES Y LACTEOS",
        (
            "queso",
            "jamon",
            "salame",
            "mortadela",
            "yogur",
            "leche",
            "manteca",
            "ricota",
            "muzzarella",
            "salchicha",
            "bondiola",
            "lomito",
        ),
    ),
    (
        "ALMACEN",
        (
            "aceite",
            "arroz",
            "azucar",
            "harina",
            "yerba",
            "fideo",
            "fideos",
            "spaguetti",
            "spaghetti",
            "tallarines",
            "mostachol",
            "tirabuzon",
            "penne",
            "rebozador",
            "salsa",
            "mate cocido",
            "arveja",
            "poroto",
            "cafe",
            "capuccino",
            "maiz",
            "tomate",
            "pure",
            "atun",
            "lenteja",
            "durazno",
            "mermelada",
            "mayonesa",
            "ketchup",
            "mostaza",
            "sal",
            "pan rallado",
            "polenta",
        ),
    ),
    (
        "GALLETITAS Y GOLOSINAS",
        (
            "galletita",
            "oreo",
            "chocolate",
            "alfajor",
            "turron",
            "gomita",
            "caramelo",
            "oblea",
            "pepitos",
            "chocolinas",
            "bombon",
            "bon o bon",
        ),
    ),
    (
        "ALIMENTO PARA ANIMALES",
        (
            "dog",
            "cat",
            "gati",
            "dogui",
            "kongo",
            "sabrositos",
            "razas pequenas",
            "cachorro",
        ),
    ),
)


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
            with self._open_csv(csv_path) as file:
                csv_encoding = file.encoding
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
            result.summary["category_source"] = "sfamilia_with_name_inference"
            result.summary["csv_encoding"] = csv_encoding
            result.completed_at = datetime.now(UTC)
            return self._repository.update_catalog_import(result)
        except Exception as exc:
            result.status = CatalogImportStatus.FAILED
            result.errors.append(str(exc))
            result.completed_at = datetime.now(UTC)
            self._repository.update_catalog_import(result)
            raise

    @staticmethod
    def _open_csv(csv_path: Path) -> TextIO:
        last_error: UnicodeDecodeError | None = None
        for encoding in CSV_ENCODINGS:
            try:
                file = csv_path.open("r", encoding=encoding, newline="")
                file.read(4096)
                file.seek(0)
                return file
            except UnicodeDecodeError as exc:
                last_error = exc
        raise ValueError(
            f"Could not decode CSV with supported encodings: {CSV_ENCODINGS}"
        ) from last_error

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
            category=PosCatalogImportService._category_from_row(row),
            unit_size=unit_size,
            unit=unit,
            sale_price=parse_argentine_decimal(row.get("rpreciou")),
            current_cost=parse_argentine_decimal(row.get("rcostou")),
            margin_percentage=parse_argentine_decimal(row.get("rmargenganancia")),
            stock_quantity=parse_argentine_decimal(row.get("rstock")),
            active=(row.get("bactivo") or "T").strip().upper() == "T",
            source="pos_csv",
        )

    @staticmethod
    def _category_from_row(row: dict[str, str]) -> str | None:
        raw_category = (row.get("sfamilia") or "").strip().upper()
        raw_category_key = normalize_text(raw_category)
        name = normalize_text((row.get("snombre") or "").strip())
        inferred = PosCatalogImportService._infer_category_from_name(name)

        if raw_category_key in SUSPICIOUS_CATEGORY_KEYS:
            return inferred
        return raw_category or inferred

    @staticmethod
    def _infer_category_from_name(name: str) -> str | None:
        if not name:
            return None
        searchable_name = f" {name} "
        tokens = set(name.split())
        for category, keywords in CATEGORY_RULES:
            if any(
                keyword in tokens if len(keyword) <= 3 else keyword in searchable_name
                for keyword in keywords
            ):
                return category
        return None
