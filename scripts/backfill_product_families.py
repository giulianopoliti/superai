import argparse
import csv
import sys
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.db.models import ProductFamilyModel, ProductModel
from app.db.session import SessionLocal
from app.modules.procurement.normalization import normalize_text
from app.modules.procurement.schemas import ProductFamily

CSV_ENCODINGS = ("utf-8-sig", "cp1252", "latin-1")


@dataclass(frozen=True)
class BackfillStats:
    families_read: int
    families_upserted: int
    products_read: int
    products_matched: int
    products_updated: int
    products_without_family: int
    products_missing_in_db: int


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    last_error: UnicodeDecodeError | None = None
    for encoding in CSV_ENCODINGS:
        try:
            with path.open("r", encoding=encoding, newline="") as file:
                return list(csv.DictReader(file, delimiter=";"))
        except UnicodeDecodeError as exc:
            last_error = exc
    raise ValueError(f"Could not decode {path} with supported encodings: {CSV_ENCODINGS}") from last_error


def family_from_row(*, business_id: str, row: dict[str, str]) -> ProductFamily | None:
    external_family_id = (row.get("id") or "").strip()
    name = (row.get("sfamilia") or "").strip().upper()
    if not external_family_id or not name:
        return None

    return ProductFamily(
        business_id=business_id,
        external_family_id=external_family_id,
        name=name,
        normalized_name=normalize_text(name),
        active=(row.get("bactivo") or "T").strip().upper() == "T",
    )


def backfill_product_families(
    *,
    business_id: str,
    families_csv: Path,
    products_csv: Path,
) -> BackfillStats:
    if SessionLocal is None:
        raise RuntimeError("DATABASE_URL is not configured.")

    family_rows = read_csv_rows(families_csv)
    product_rows = read_csv_rows(products_csv)
    families = [
        family
        for row in family_rows
        if (family := family_from_row(business_id=business_id, row=row)) is not None
    ]

    with SessionLocal() as session:
        existing_families = session.scalars(
            select(ProductFamilyModel).where(ProductFamilyModel.business_id == business_id)
        ).all()
        families_by_external_id = {
            family.external_family_id: family for family in existing_families
        }

        for family in families:
            model = families_by_external_id.get(family.external_family_id)
            if model is None:
                model = ProductFamilyModel(
                    id=family.id,
                    business_id=family.business_id,
                    external_family_id=family.external_family_id,
                    created_at=family.created_at,
                )
                session.add(model)
                families_by_external_id[family.external_family_id] = model

            model.name = family.name
            model.normalized_name = family.normalized_name
            model.active = family.active
            model.updated_at = family.updated_at

        session.flush()

        product_family_by_external_product_id: dict[str, ProductFamilyModel] = {}
        products_without_family = 0
        for row in product_rows:
            external_product_id = (row.get("id") or "").strip()
            external_family_id = (row.get("idfamilia") or "").strip()
            if not external_product_id or not external_family_id:
                products_without_family += 1
                continue

            family = families_by_external_id.get(external_family_id)
            if family is None:
                products_without_family += 1
                continue

            product_family_by_external_product_id[external_product_id] = family

        external_product_ids = set(product_family_by_external_product_id)
        products = session.scalars(
            select(ProductModel).where(
                ProductModel.business_id == business_id,
                ProductModel.external_product_id.in_(external_product_ids),
            )
        ).all()

        products_updated = 0
        for product in products:
            if not product.external_product_id:
                continue
            family = product_family_by_external_product_id[product.external_product_id]
            if (
                product.family_id == family.id
                and product.external_family_id == family.external_family_id
                and product.category == family.name
            ):
                continue

            product.family_id = family.id
            product.external_family_id = family.external_family_id
            product.category = family.name
            products_updated += 1

        session.commit()

    products_matched = len(products)
    return BackfillStats(
        families_read=len(family_rows),
        families_upserted=len(families),
        products_read=len(product_rows),
        products_matched=products_matched,
        products_updated=products_updated,
        products_without_family=products_without_family,
        products_missing_in_db=len(external_product_ids) - products_matched,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill product families from POS familias/productos CSV exports."
    )
    parser.add_argument("--business-id", required=True)
    parser.add_argument("--families-csv", required=True, type=Path)
    parser.add_argument("--products-csv", required=True, type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    stats = backfill_product_families(
        business_id=args.business_id,
        families_csv=args.families_csv,
        products_csv=args.products_csv,
    )
    print(stats)


if __name__ == "__main__":
    main()
