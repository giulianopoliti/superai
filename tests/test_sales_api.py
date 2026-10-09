from decimal import Decimal
from io import BytesIO

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import main
from app.db.models import Base
from app.db.repositories.sql_procurement import SqlProcurementRepository
from app.modules.procurement.schemas import Product
from app.settings import settings


def build_session():
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def test_sales_api_imports_sales_csv_and_lists_top_products(monkeypatch) -> None:
    session = build_session()
    repository = SqlProcurementRepository(session)
    product = repository.add_product(
        Product(
            business_id="business-1",
            external_product_id="pos-product-1",
            sku="316",
            name="1/2 DOCENA DE HUEVOS BLANCOS",
            normalized_name="media docena huevos blancos",
            current_cost=Decimal("1300"),
            sale_price=Decimal("1800"),
            stock_quantity=Decimal("2"),
        )
    )
    monkeypatch.setattr(settings, "kapso_api_key", None)
    monkeypatch.setattr(settings, "kapso_webhook_secret", None)
    monkeypatch.setattr(settings, "scheduler_enabled", False)
    monkeypatch.setattr(main, "SessionLocal", session)
    csv_content = (
        b"id;idventa;idproducto;rcantidad;rtotal;rcostou;fcreacion;scodproducto\n"
        b"line-1;ticket-1;pos-product-1;1;1800;1300;2026-06-30T10:00:00.000Z;316\n"
        b"line-2;ticket-2;pos-product-1;2;3600;1300;2026-07-01T10:00:00.000Z;316\n"
    )

    with TestClient(main.create_app()) as client:
        import_response = client.post(
            "/sales/imports/from-file",
            data={"business_id": "business-1"},
            files={
                "file": (
                    "ventasitems_2026-06-30_2026-07-30.csv",
                    BytesIO(csv_content),
                    "text/csv",
                )
            },
        )
        top_response = client.get(
            "/sales/top-products",
            params={
                "business_id": "business-1",
                "date_from": "2026-06-30",
                "date_to": "2026-07-30",
                "sort": "quantity",
            },
        )
        summary_response = client.get(
            f"/sales/products/{product.id}/summary",
            params={
                "business_id": "business-1",
                "date_from": "2026-06-30",
                "date_to": "2026-07-30",
            },
        )
        status_response = client.get(
            "/sales/status",
            params={"business_id": "business-1"},
        )
        suggestions_without_stock_response = client.get(
            "/sales/purchase-suggestions",
            params={
                "business_id": "business-1",
                "date_from": "2026-06-30",
                "date_to": "2026-07-30",
                "target_coverage_days": 7,
                "use_stock": False,
            },
        )
        suggestions_with_stock_response = client.get(
            "/sales/purchase-suggestions",
            params={
                "business_id": "business-1",
                "date_from": "2026-06-30",
                "date_to": "2026-07-30",
                "target_coverage_days": 7,
                "use_stock": True,
            },
        )

    assert import_response.status_code == 200
    assert import_response.json()["imported_count"] == 2
    assert import_response.json()["summary"]["daily_record_count"] == 2
    assert top_response.status_code == 200
    assert top_response.json()["products"][0]["product_id"] == product.id
    assert top_response.json()["products"][0]["quantity_sold"] == "3.000"
    assert summary_response.status_code == 200
    assert summary_response.json()["quantity_sold"] == "3.000"
    assert status_response.status_code == 200
    assert status_response.json()["first_sale_date"] == "2026-06-30"
    assert status_response.json()["last_sale_date"] == "2026-07-01"
    assert status_response.json()["total_sale_lines"] == 2
    assert status_response.json()["total_daily_records"] == 2
    assert suggestions_without_stock_response.status_code == 200
    suggestion_without_stock = suggestions_without_stock_response.json()["suggestions"][0]
    assert suggestion_without_stock["sales_summary"]["product_id"] == product.id
    assert suggestion_without_stock["use_stock"] is False
    assert suggestion_without_stock["demand_quantity"] == "0.700"
    assert suggestion_without_stock["stock_deduction"] == "0.000"
    assert suggestion_without_stock["suggested_quantity"] == "0.700"
    assert suggestions_with_stock_response.status_code == 200
    suggestion_with_stock = suggestions_with_stock_response.json()["suggestions"][0]
    assert suggestion_with_stock["use_stock"] is True
    assert suggestion_with_stock["stock_deduction"] == "2.000"
    assert suggestion_with_stock["suggested_quantity"] == "0.000"
