from datetime import UTC, datetime, timedelta
from decimal import Decimal
from io import BytesIO

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import main
from app.db.models import Base
from app.db.repositories.sql_procurement import SqlProcurementRepository
from app.db.repositories.sql_reminders import SqlReminderRepository
from app.modules.procurement.schemas import Product
from app.modules.reminders.service import ReminderService
from app.settings import settings


def build_session():
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def test_operations_dashboard_aggregates_existing_modules(monkeypatch) -> None:
    session = build_session()
    procurement_repository = SqlProcurementRepository(session)
    product = procurement_repository.add_product(
        Product(
            business_id="business-1",
            external_product_id="pos-product-1",
            sku="316",
            name="1/2 DOCENA DE HUEVOS BLANCOS",
            normalized_name="media docena huevos blancos",
            current_cost=Decimal("1300"),
            sale_price=Decimal("1800"),
            stock_quantity=Decimal("1"),
        )
    )
    ReminderService(SqlReminderRepository(session)).create_reminder(
        business_id="business-1",
        created_by_external_user_id="user-1",
        title="Revisar heladera",
        due_at=datetime.now(UTC) - timedelta(hours=1),
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
        dashboard_response = client.get(
            "/operations/dashboard",
            params={
                "business_id": "business-1",
                "date_from": "2026-06-30",
                "date_to": "2026-07-01",
            },
        )
        other_business_response = client.get(
            "/operations/dashboard",
            params={
                "business_id": "business-2",
                "date_from": "2026-06-30",
                "date_to": "2026-07-01",
            },
        )

    assert import_response.status_code == 200
    assert dashboard_response.status_code == 200
    dashboard = dashboard_response.json()
    assert dashboard["sales_totals"]["quantity_sold"] == "3.000"
    assert dashboard["sales_totals"]["revenue"] == "5400.00"
    assert dashboard["top_products_by_quantity"][0]["product_id"] == product.id
    assert dashboard["purchase_suggestions"][0]["sales_summary"]["product_id"] == product.id
    assert dashboard["pending_reminders"][0]["title"] == "Revisar heladera"
    assert any(alert["type"] == "reminders.overdue" for alert in dashboard["alerts"])
    assert any(alert["type"] == "sales.low_stock_coverage" for alert in dashboard["alerts"])

    assert other_business_response.status_code == 200
    other_dashboard = other_business_response.json()
    assert other_dashboard["sales_totals"]["quantity_sold"] == "0.000"
    assert other_dashboard["pending_reminders"] == []


def test_operations_daily_summary_returns_whatsapp_ready_text(monkeypatch) -> None:
    session = build_session()
    monkeypatch.setattr(settings, "kapso_api_key", None)
    monkeypatch.setattr(settings, "kapso_webhook_secret", None)
    monkeypatch.setattr(settings, "scheduler_enabled", False)
    monkeypatch.setattr(main, "SessionLocal", session)

    with TestClient(main.create_app()) as client:
        response = client.get(
            "/operations/daily-summary",
            params={
                "business_id": "business-1",
                "date_from": "2026-06-30",
                "date_to": "2026-07-30",
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert "Resumen operativo" in body["summary"]
    assert body["dashboard"]["business_id"] == "business-1"


def test_assistant_message_can_request_operations_dashboard(monkeypatch) -> None:
    session = build_session()
    monkeypatch.setattr(settings, "kapso_api_key", None)
    monkeypatch.setattr(settings, "kapso_webhook_secret", None)
    monkeypatch.setattr(settings, "scheduler_enabled", False)
    monkeypatch.setattr(settings, "llm_enabled", False)
    monkeypatch.setattr(main, "SessionLocal", session)

    with TestClient(main.create_app()) as client:
        response = client.post(
            "/assistant/message",
            json={
                "channel": "cli",
                "external_user_id": "user-1",
                "business_id": "business-1",
                "message_type": "text",
                "text": "pasame el panel general",
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert "Panel general" in body["reply"]
    assert body["metadata"]["intent"] == "get_operations_dashboard"
