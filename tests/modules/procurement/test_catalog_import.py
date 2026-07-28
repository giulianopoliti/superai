from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import Base
from app.db.repositories.sql_procurement import SqlProcurementRepository
from app.modules.procurement.catalog_import import PosCatalogImportService
from app.modules.procurement.normalization import (
    normalize_text,
    parse_argentine_decimal,
    parse_unit,
)
from app.modules.procurement.schemas import CatalogImportStatus


def build_repository() -> SqlProcurementRepository:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)
    return SqlProcurementRepository(session)


def test_normalizes_text_decimals_and_units() -> None:
    assert normalize_text("ACEITE CAÑUELAS GIRASOL 900ML") == "aceite canuelas girasol 900ml"
    assert parse_argentine_decimal("1.234,56") == Decimal("1234.56")
    assert parse_unit("CREMA DE LECHE X3KG") == (Decimal("3"), "kg")


def test_pos_catalog_imports_and_upserts_products(tmp_path) -> None:
    csv_path = tmp_path / "productos.csv"
    csv_path.write_text(
        "\n".join(
            [
                "id;scodproducto;sean;snombre;rpreciou;rcostou;rmargenganancia;"
                "rstock;sfamilia;bactivo",
                "product-1;4;7792180001641;ACEITE CAÑUELAS GIRASOL 900ML;4200;3120;"
                "34,62;-9;ALMACEN;T",
                "product-1;4;7792180001641;ACEITE CAÑUELAS GIRASOL 900ML;4300;3000;"
                "43,33;10;ALMACEN;T",
            ]
        ),
        encoding="utf-8",
    )
    repository = build_repository()
    service = PosCatalogImportService(repository)

    result = service.import_csv(business_id="business-1", csv_path=csv_path)

    assert result.row_count == 2
    assert result.imported_count == 2
    assert result.skipped_count == 0
    assert result.status == CatalogImportStatus.COMPLETED
    assert result.completed_at is not None
    assert result.summary["total_count"] == 1
    assert result.summary["duplicate_barcode_count"] == 0
    assert repository.count_products(business_id="business-1") == 1


def test_pos_catalog_keeps_distinct_products_with_same_barcode(tmp_path) -> None:
    csv_path = tmp_path / "productos.csv"
    csv_path.write_text(
        "\n".join(
            [
                "id;scodproducto;sean;snombre;rpreciou;rcostou;rmargenganancia;"
                "rstock;sfamilia;bactivo",
                "product-1;1;7798117660196;ZAMBONI HORNO;1000;700;42,85;5;ALMACEN;T",
                "product-2;2;7798117660196;ZAMBONI TAPA HORNO GRANDE;1100;800;"
                "37,50;6;ALMACEN;T",
            ]
        ),
        encoding="utf-8",
    )
    repository = build_repository()
    service = PosCatalogImportService(repository)

    result = service.import_csv(business_id="business-1", csv_path=csv_path)

    assert result.imported_count == 2
    assert result.summary["duplicate_barcode_count"] == 1
    assert repository.count_products(business_id="business-1") == 2
