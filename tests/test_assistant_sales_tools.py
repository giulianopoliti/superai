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


def seed_sales(client: TestClient, session) -> tuple[Product, Product]:
    repository = SqlProcurementRepository(session)
    coca = repository.add_product(
        Product(
            business_id="business-1",
            external_product_id="pos-coca",
            sku="COCA",
            name="COCA COLA 2.25L",
            normalized_name="coca cola 225l",
            category="BEBIDAS",
            current_cost=Decimal("1500"),
            sale_price=Decimal("2400"),
            stock_quantity=Decimal("8"),
        )
    )
    bag = repository.add_product(
        Product(
            business_id="business-1",
            external_product_id="pos-bag",
            sku="BAG",
            name="BOLSA CAMISETA",
            normalized_name="bolsa camiseta",
            category="VARIOS",
            current_cost=Decimal("10"),
            sale_price=Decimal("50"),
            stock_quantity=Decimal("200"),
        )
    )
    csv_content = (
        b"id;idventa;idproducto;rcantidad;rtotal;rcostou;fcreacion;scodproducto\n"
        b"line-1;ticket-1;pos-bag;10;500;10;2026-08-01T10:00:00.000Z;BAG\n"
        b"line-2;ticket-2;pos-coca;3;7200;1500;2026-08-02T10:00:00.000Z;COCA\n"
        b"line-3;ticket-3;pos-coca;2;4800;1500;2026-08-03T10:00:00.000Z;COCA\n"
    )
    response = client.post(
        "/sales/imports/from-file",
        data={"business_id": "business-1"},
        files={
            "file": (
                "ventasitems_2026-08-01_2026-08-03.csv",
                BytesIO(csv_content),
                "text/csv",
            )
        },
    )
    assert response.status_code == 200
    return coca, bag


def configure_app(monkeypatch, session) -> None:
    monkeypatch.setattr(settings, "kapso_api_key", None)
    monkeypatch.setattr(settings, "kapso_webhook_secret", None)
    monkeypatch.setattr(settings, "scheduler_enabled", False)
    monkeypatch.setattr(settings, "llm_enabled", False)
    monkeypatch.setattr(main, "SessionLocal", session)


def test_sales_top_products_tool_endpoint_excludes_product_names(monkeypatch) -> None:
    session = build_session()
    configure_app(monkeypatch, session)

    with TestClient(main.create_app()) as client:
        coca, _ = seed_sales(client, session)
        response = client.post(
            "/assistant/tools/sales/top-products",
            json={
                "business_id": "business-1",
                "date_from": "2026-08-01",
                "date_to": "2026-08-03",
                "sort": "quantity",
                "limit": 10,
                "exclude_product_names": ["bolsa"],
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["products"][0]["product_id"] == coca.id
    assert body["products"][0]["quantity_sold"] == "5.000"
    assert len(body["products"]) == 1
    assert "Exclui: bolsa." in body["reply"]


def test_assistant_message_executes_sales_top_products_tool(monkeypatch) -> None:
    session = build_session()
    configure_app(monkeypatch, session)

    with TestClient(main.create_app()) as client:
        coca, _ = seed_sales(client, session)
        response = client.post(
            "/assistant/message",
            json={
                "channel": "cli",
                "external_user_id": "user-1",
                "business_id": "business-1",
                "message_type": "text",
                "text": "Decime los productos mas vendidos de los ultimos 7 dias excluyendo bolsa",
                "timestamp": "2026-08-06T12:00:00Z",
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert "COCA COLA 2.25L" in body["reply"]
    assert "BOLSA CAMISETA" not in body["reply"]
    assert body["actions"][0]["type"] == "tool.executed"
    assert body["actions"][0]["payload"]["tool_name"] == "sales.top_products"
    assert body["metadata"]["intent"] == "query_sales_top_products"
    assert body["metadata"]["tool_metadata"]["product_count"] == 1
    assert body["metadata"]["tool_metadata"]["date_to"] == "2026-08-03"
    assert coca.name in body["reply"]


def test_assistant_message_resolves_family_and_fuzzy_exclusions(monkeypatch) -> None:
    session = build_session()
    configure_app(monkeypatch, session)

    with TestClient(main.create_app()) as client:
        repository = SqlProcurementRepository(session)
        yerba = repository.add_product(
            Product(
                business_id="business-1",
                external_product_id="pos-yerba",
                sku="YERBA",
                name="YERBA MATE PLAYADITO 1KG",
                normalized_name="yerba mate playadito 1kg",
                category="ALMACEN",
                current_cost=Decimal("1800"),
                sale_price=Decimal("2600"),
            )
        )
        repository.add_product(
            Product(
                business_id="business-1",
                external_product_id="pos-bolsa-pan",
                sku="BOLPAN",
                name="BOLSA PANADERIA",
                normalized_name="bolsa panaderia",
                category="ALMACEN",
                current_cost=Decimal("20"),
                sale_price=Decimal("80"),
            )
        )
        repository.add_product(
            Product(
                business_id="business-1",
                external_product_id="pos-coca",
                sku="COCA",
                name="COCA COLA 2.25L",
                normalized_name="coca cola 225l",
                category="BEBIDAS",
                current_cost=Decimal("1500"),
                sale_price=Decimal("2400"),
            )
        )
        csv_content = (
            b"id;idventa;idproducto;rcantidad;rtotal;rcostou;fcreacion;scodproducto\n"
            b"line-1;ticket-1;pos-bolsa-pan;30;2400;20;2026-08-01T10:00:00.000Z;BOLPAN\n"
            b"line-2;ticket-2;pos-yerba;7;18200;1800;2026-08-02T10:00:00.000Z;YERBA\n"
            b"line-3;ticket-3;pos-coca;50;120000;1500;2026-08-03T10:00:00.000Z;COCA\n"
        )
        import_response = client.post(
            "/sales/imports/from-file",
            data={"business_id": "business-1"},
            files={
                "file": (
                    "ventasitems_2026-08-01_2026-08-03.csv",
                    BytesIO(csv_content),
                    "text/csv",
                )
            },
        )
        assert import_response.status_code == 200

        response = client.post(
            "/assistant/message",
            json={
                "channel": "web",
                "external_user_id": "user-1",
                "business_id": "business-1",
                "message_type": "text",
                "text": (
                    "Dame los 2 productos mas vendidos de almacen "
                    "excluyendo bolsa pan"
                ),
                "timestamp": "2026-08-06T12:00:00Z",
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert yerba.name in body["reply"]
    assert "BOLSA PANADERIA" not in body["reply"]
    assert "COCA COLA" not in body["reply"]
    assert body["metadata"]["tool_metadata"]["resolved_category"] == "ALMACEN"
    assert body["metadata"]["tool_metadata"]["product_count"] == 1
    assert body["metadata"]["tool_data"]["products"][0]["product_id"] == yerba.id


def test_assistant_message_returns_requested_rank_range(monkeypatch) -> None:
    session = build_session()
    configure_app(monkeypatch, session)

    with TestClient(main.create_app()) as client:
        repository = SqlProcurementRepository(session)
        for rank in range(1, 13):
            product_code = f"pos-p{rank:02d}"
            repository.add_product(
                Product(
                    business_id="business-1",
                    external_product_id=product_code,
                    sku=f"P{rank:02d}",
                    name=f"PRODUCTO {rank:02d}",
                    normalized_name=f"producto {rank:02d}",
                    current_cost=Decimal("10"),
                    sale_price=Decimal("20"),
                )
            )
        lines = ["id;idventa;idproducto;rcantidad;rtotal;rcostou;fcreacion;scodproducto"]
        for rank in range(1, 13):
            quantity = 100 - rank
            lines.append(
                f"line-{rank};ticket-{rank};pos-p{rank:02d};{quantity};"
                f"{quantity * 20};10;2026-08-03T10:00:00.000Z;P{rank:02d}"
            )
        import_response = client.post(
            "/sales/imports/from-file",
            data={"business_id": "business-1"},
            files={
                "file": (
                    "ventasitems_2026-08-03_2026-08-03.csv",
                    BytesIO(("\n".join(lines) + "\n").encode()),
                    "text/csv",
                )
            },
        )
        assert import_response.status_code == 200

        response = client.post(
            "/assistant/message",
            json={
                "channel": "web",
                "external_user_id": "user-1",
                "business_id": "business-1",
                "message_type": "text",
                "text": "Dame los productos mas vendidos del top 7 al top 12",
                "timestamp": "2026-08-06T12:00:00Z",
            },
        )

    assert response.status_code == 200
    body = response.json()
    products = body["metadata"]["tool_data"]["products"]
    assert [product["product_name"] for product in products] == [
        "PRODUCTO 07",
        "PRODUCTO 08",
        "PRODUCTO 09",
        "PRODUCTO 10",
        "PRODUCTO 11",
        "PRODUCTO 12",
    ]
    assert body["metadata"]["tool_metadata"]["rank_from"] == 7
    assert body["metadata"]["tool_metadata"]["rank_to"] == 12
    assert "7. PRODUCTO 07" in body["reply"]


def test_assistant_message_uses_all_time_period(monkeypatch) -> None:
    session = build_session()
    configure_app(monkeypatch, session)

    with TestClient(main.create_app()) as client:
        repository = SqlProcurementRepository(session)
        product = repository.add_product(
            Product(
                business_id="business-1",
                external_product_id="pos-azucar",
                sku="AZU",
                name="AZUCAR LEDESMA 1KG",
                normalized_name="azucar ledesma 1kg",
                category="ALMACEN",
                current_cost=Decimal("1000"),
                sale_price=Decimal("1800"),
            )
        )
        csv_content = (
            b"id;idventa;idproducto;rcantidad;rtotal;rcostou;fcreacion;scodproducto\n"
            b"line-old;ticket-old;pos-azucar;4;7200;1000;2026-07-01T10:00:00.000Z;AZU\n"
            b"line-new;ticket-new;pos-azucar;2;3600;1000;2026-08-03T10:00:00.000Z;AZU\n"
        )
        import_response = client.post(
            "/sales/imports/from-file",
            data={"business_id": "business-1"},
            files={
                "file": (
                    "ventasitems_2026-07-01_2026-08-03.csv",
                    BytesIO(csv_content),
                    "text/csv",
                )
            },
        )
        assert import_response.status_code == 200

        response = client.post(
            "/assistant/message",
            json={
                "channel": "web",
                "external_user_id": "user-1",
                "business_id": "business-1",
                "message_type": "text",
                "text": (
                    "Dame los productos mas vendidos historicos, "
                    "de la categoria almacen por favor."
                ),
                "timestamp": "2026-08-06T12:00:00Z",
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["metadata"]["tool_metadata"]["period"] == "all_time"
    assert body["metadata"]["tool_metadata"]["date_from"] == "2026-07-01"
    assert body["metadata"]["tool_data"]["products"][0]["product_id"] == product.id
    assert body["metadata"]["tool_data"]["products"][0]["quantity_sold"] == "6.000"


def test_assistant_message_resolves_multiple_families_since_month(monkeypatch) -> None:
    session = build_session()
    configure_app(monkeypatch, session)

    with TestClient(main.create_app()) as client:
        repository = SqlProcurementRepository(session)
        azucar = repository.add_product(
            Product(
                business_id="business-1",
                external_product_id="pos-azucar",
                sku="AZU",
                name="AZUCAR 1KG",
                normalized_name="azucar 1kg",
                category="ALMACEN",
                current_cost=Decimal("1000"),
                sale_price=Decimal("1800"),
            )
        )
        banana = repository.add_product(
            Product(
                business_id="business-1",
                external_product_id="pos-banana",
                sku="BAN",
                name="BANANA ECUADOR",
                normalized_name="banana ecuador",
                category="VERDULERIA",
                current_cost=Decimal("900"),
                sale_price=Decimal("1600"),
            )
        )
        repository.add_product(
            Product(
                business_id="business-1",
                external_product_id="pos-mila",
                sku="MIL",
                name="OFERTA MILA",
                normalized_name="oferta mila",
                category="CARNICERIA",
                current_cost=Decimal("3000"),
                sale_price=Decimal("5000"),
            )
        )
        csv_content = (
            b"id;idventa;idproducto;rcantidad;rtotal;rcostou;fcreacion;scodproducto\n"
            b"line-1;ticket-1;pos-azucar;8;14400;1000;2026-03-10T10:00:00.000Z;AZU\n"
            b"line-2;ticket-2;pos-banana;6;9600;900;2026-08-02T10:00:00.000Z;BAN\n"
            b"line-3;ticket-3;pos-mila;30;150000;3000;2026-08-03T10:00:00.000Z;MIL\n"
        )
        import_response = client.post(
            "/sales/imports/from-file",
            data={"business_id": "business-1"},
            files={
                "file": (
                    "ventasitems_2026-03-10_2026-08-03.csv",
                    BytesIO(csv_content),
                    "text/csv",
                )
            },
        )
        assert import_response.status_code == 200

        response = client.post(
            "/assistant/message",
            json={
                "channel": "web",
                "external_user_id": "user-1",
                "business_id": "business-1",
                "message_type": "text",
                "text": (
                    "Dame los 5 productos mas vendidos de la categoria almacen, "
                    "y de la familia verduleria, desde marzo hasta ahora."
                ),
                "timestamp": "2026-08-13T12:00:00Z",
            },
        )

    assert response.status_code == 200
    body = response.json()
    product_ids = {
        product["product_id"] for product in body["metadata"]["tool_data"]["products"]
    }
    assert product_ids == {azucar.id, banana.id}
    assert "OFERTA MILA" not in body["reply"]
    assert body["metadata"]["tool_metadata"]["date_from"] == "2026-03-01"
    assert body["metadata"]["tool_metadata"]["resolved_categories"] == [
        "ALMACEN",
        "VERDULERIA",
    ]
