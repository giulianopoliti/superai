from datetime import date
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import Base
from app.db.repositories.sql_procurement import SqlProcurementRepository
from app.db.repositories.sql_sales import SqlSalesRepository
from app.modules.procurement.schemas import Product
from app.modules.sales.sales_import import PosSalesImportService
from app.modules.sales.schemas import SalesImportStatus


def build_repositories():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)
    return SqlProcurementRepository(session), SqlSalesRepository(session)


def test_pos_sales_import_aggregates_daily_product_sales(tmp_path) -> None:
    procurement_repository, sales_repository = build_repositories()
    product = procurement_repository.add_product(
        Product(
            business_id="business-1",
            external_product_id="pos-product-1",
            sku="316",
            name="1/2 DOCENA DE HUEVOS BLANCOS",
            normalized_name="media docena huevos blancos",
            current_cost=Decimal("1300"),
            sale_price=Decimal("1800"),
            stock_quantity=Decimal("12"),
        )
    )
    csv_path = tmp_path / "ventasitems_2026-06-30_2026-07-30.csv"
    csv_path.write_text(
        "\n".join(
            [
                "id;idventa;idproducto;rcantidad;rtotal;rcostou;fcreacion;scodproducto",
                "line-1;ticket-1;pos-product-1;1;1800;1300;2026-06-30T10:00:00.000Z;316",
                "line-2;ticket-2;pos-product-1;2;3600;1300;2026-06-30T11:00:00.000Z;316",
                "line-3;ticket-3;pos-product-1;1;1800;1300;2026-07-01T09:00:00.000Z;316",
            ]
        ),
        encoding="utf-8",
    )
    service = PosSalesImportService(sales_repository)

    result = service.import_csv(business_id="business-1", csv_path=csv_path)
    summary = sales_repository.summarize_product_sales(
        business_id="business-1",
        product_id=product.id,
        date_from=result.period_start,
        date_to=result.period_end,
    )
    top = sales_repository.top_products(
        business_id="business-1",
        date_from=result.period_start,
        date_to=result.period_end,
        sort="quantity",
        limit=5,
    )

    assert result.status == SalesImportStatus.COMPLETED
    assert result.row_count == 3
    assert result.imported_count == 3
    assert result.unique_ticket_count == 3
    assert result.unique_product_count == 1
    assert result.summary["daily_record_count"] == 2
    assert summary is not None
    assert summary.quantity_sold == Decimal("4.000")
    assert summary.revenue == Decimal("7200.00")
    assert summary.ticket_count == 3
    assert summary.average_daily_quantity == Decimal("0.13")
    assert top[0].product_id == product.id
    assert top[0].product_name == "1/2 DOCENA DE HUEVOS BLANCOS"


def test_pos_sales_import_skips_duplicate_sale_item_lines(tmp_path) -> None:
    procurement_repository, sales_repository = build_repositories()
    product = procurement_repository.add_product(
        Product(
            business_id="business-1",
            external_product_id="pos-product-1",
            sku="316",
            name="HUEVOS",
            normalized_name="huevos",
        )
    )
    csv_path = tmp_path / "ventasitems_2026-06-30_2026-07-30.csv"
    csv_path.write_text(
        "\n".join(
            [
                "id;idventa;idproducto;rcantidad;rtotal;rcostou;fcreacion;scodproducto",
                "line-1;ticket-1;pos-product-1;1;1800;1300;2026-06-30T10:00:00.000Z;316",
            ]
        ),
        encoding="utf-8",
    )
    service = PosSalesImportService(sales_repository)
    first_import = service.import_csv(business_id="business-1", csv_path=csv_path)
    csv_path.write_text(
        "\n".join(
            [
                "id;idventa;idproducto;rcantidad;rtotal;rcostou;fcreacion;scodproducto",
                "line-1;ticket-1;pos-product-1;5;9000;1300;2026-06-30T10:00:00.000Z;316",
            ]
        ),
        encoding="utf-8",
    )

    second_import = service.import_csv(business_id="business-1", csv_path=csv_path)
    summary = sales_repository.summarize_product_sales(
        business_id="business-1",
        product_id=product.id,
        date_from=date(2026, 6, 30),
        date_to=date(2026, 7, 30),
    )

    assert first_import.imported_count == 1
    assert first_import.summary["duplicate_line_count"] == 0
    assert second_import.imported_count == 0
    assert second_import.summary["duplicate_line_count"] == 1
    assert summary is not None
    assert summary.quantity_sold == Decimal("1.000")
    assert summary.revenue == Decimal("1800.00")

