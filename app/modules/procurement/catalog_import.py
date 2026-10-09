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
from app.modules.procurement.schemas import (
    CatalogImportResult,
    CatalogImportStatus,
    Product,
    ProductFamily,
)

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

    def import_csv(
        self, *, business_id: str, csv_path: Path, families_csv_path: Path | None = None
    ) -> CatalogImportResult:
        result = CatalogImportResult(
            business_id=business_id,
            source_filename=csv_path.name,
        )
        result = self._repository.add_catalog_import(result)

        try:
            if families_csv_path is not None:
                families = self._read_families_csv(
                    business_id=business_id, csv_path=families_csv_path
                )
                self._repository.upsert_product_families(families)
            with self._open_csv(csv_path) as file:
                csv_encoding = file.encoding
                reader = csv.DictReader(file, delimiter=";")
                headers = set(reader.fieldnames or [])
                if "snombre" not in headers or not ({"id", "sean"} & headers):
                    raise ValueError("CSV must include snombre and either id or sean columns")
                rows = list(reader)
                family_ids = {
                    (row.get("idfamilia") or "").strip()
                    for row in rows
                    if (row.get("idfamilia") or "").strip()
                }
                families_by_external_id = self._repository.get_product_families_by_external_ids(
                    business_id=business_id, external_ids=family_ids
                )
                products: list[Product] = []
                for line_number, row in enumerate(rows, start=2):
                    result.row_count += 1
                    product = self._product_from_row(
                        business_id=business_id,
                        row=row,
                        families_by_external_id=families_by_external_id,
                    )
                    if product is None:
                        result.skipped_count += 1
                        result.errors.append(
                            f"line {line_number}: missing product name or stable POS identifier"
                        )
                        continue

                    products.append(product)
                    result.imported_count += 1

            counts = self._repository.upsert_products(products)
            result.status = CatalogImportStatus.COMPLETED
            result.summary = self._repository.summarize_products(business_id=business_id)
            result.summary.update(counts)
            result.summary["unresolved_family_count"] = sum(
                1 for product in products if product.external_family_id and not product.family_id
            )
            result.summary["category_source"] = "idfamilia_when_available"
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
    def _read_families_csv(*, business_id: str, csv_path: Path) -> list[ProductFamily]:
        with PosCatalogImportService._open_csv(csv_path) as file:
            reader = csv.DictReader(file, delimiter=";")
            if not {"id", "sfamilia"}.issubset(reader.fieldnames or []):
                raise ValueError("Families CSV must include id and sfamilia columns")
            families: list[ProductFamily] = []
            for row in reader:
                external_id = (row.get("id") or "").strip()
                name = (row.get("sfamilia") or "").strip().upper()
                if external_id and name:
                    families.append(
                        ProductFamily(
                            business_id=business_id,
                            external_family_id=external_id,
                            name=name,
                            normalized_name=normalize_text(name),
                            active=(row.get("bactivo") or "T").strip().upper() == "T",
                        )
                    )
            return families

    @staticmethod
    def _product_from_row(
        *,
        business_id: str,
        row: dict[str, str],
        families_by_external_id: dict[str, ProductFamily] | None = None,
    ) -> Product | None:
        name = (row.get("snombre") or "").strip()
        external_product_id = (row.get("id") or "").strip() or None
        if not name:
            return None

        unit_size, unit = parse_unit(name)
        barcode = (row.get("sean") or "").strip() or None
        if barcode == "0":
            barcode = None
        if not external_product_id and not barcode:
            return None
        external_family_id = (row.get("idfamilia") or "").strip() or None
        family = (families_by_external_id or {}).get(external_family_id or "")

        return Product(
            business_id=business_id,
            external_product_id=external_product_id,
            sku=(row.get("scodproducto") or "").strip() or None,
            barcode=barcode,
            name=name,
            normalized_name=normalize_text(name),
            category=family.name if family else PosCatalogImportService._category_from_row(row),
            family_id=family.id if family else None,
            external_family_id=external_family_id,
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
        if (row.get("idfamilia") or "").strip():
            return None

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
