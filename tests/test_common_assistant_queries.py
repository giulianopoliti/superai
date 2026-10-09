from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import main
from app.assistant.agent.executor import AgentExecutionService
from app.assistant.agent.schemas import AgentPlan, AgentStep
from app.assistant.tools.schemas import AssistantToolName, ToolExecutionContext
from app.db.models import Base, ProductModel, SalesItemLineModel
from app.db.repositories.sql_procurement import SqlProcurementRepository
from app.modules.procurement.schemas import Product, ProductFamily
from app.schemas.assistant import AssistantRequest
from app.settings import settings


@pytest.fixture
def client(monkeypatch):
    engine = create_engine(
        "sqlite+pysqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(main, "SessionLocal", session)
    monkeypatch.setattr(settings, "llm_enabled", False)
    monkeypatch.setattr(settings, "agent_enabled", False)
    monkeypatch.setattr(settings, "scheduler_enabled", False)
    monkeypatch.setattr(settings, "kapso_api_key", None)
    repository = SqlProcurementRepository(session)
    rows = [
        ("MEAT", "MILA POLLO", "CARNICERIA", 100, -1),
        ("VEG", "TOMATE", "VERDULERIA", 90, 3),
        ("BREAD", "BOLSA PAN 1/2 KILO", "ALMACEN", 80, 4),
        ("EGG1", "MAPLE HUEVOS BLANCOS", "FIAMBRERIA", 70, 1),
        ("EGG2", "1/2 DOCENA HUEVOS COLORADOS", "ALMACEN", 60, 2),
        ("CHOC", "HUEVO KINDER 150GRS", "GOLOSINAS", 19, 2),
        ("COCA", "COCA COLA 1.5L", "BEBIDAS", 16, 0),
        ("MILK", "LECHE ENTERA", "LACTEOS", 15, 6),
    ]
    for sku, name, family, _quantity, stock in rows:
        repository.add_product(
            Product(
                business_id="business-1",
                external_product_id=sku,
                sku=sku,
                name=name,
                normalized_name=name.lower(),
                category=family,
                stock_quantity=Decimal(stock),
                sale_price=Decimal(10),
                current_cost=Decimal(2),
            )
        )
    repository.add_product(
        Product(
            business_id="business-2",
            external_product_id="OTHER",
            sku="OTHER",
            name="COCA PRIVADA",
            normalized_name="coca privada",
            category="BEBIDAS",
        )
    )
    csv = "id;idventa;idproducto;rcantidad;rtotal;rcostou;fcreacion;scodproducto\n"
    for sku, _, _, quantity, _ in rows:
        csv += (
            f"line-{sku};same-ticket;{sku};{quantity};{quantity * 10};2;"
            f"2026-09-28T10:00:00Z;{sku}\n"
        )
    csv += "previous;old-ticket;COCA;8;80;2;2026-09-21T10:00:00Z;COCA\n"
    with TestClient(main.create_app()) as client:
        response = client.post(
            "/sales/imports/from-file",
            data={"business_id": "business-1"},
            files={"file": ("ventasitems_2026-09-21_2026-09-28.csv", csv.encode(), "text/csv")},
        )
        assert response.status_code == 200
        yield client
    engine.dispose()


def message(client, text):
    response = client.post(
        "/assistant/message",
        json={
            "channel": "web",
            "external_user_id": "test-user",
            "business_id": "business-1",
            "message_type": "text",
            "text": text,
            "timestamp": "2026-09-28T18:00:00Z",
        },
    )
    assert response.status_code == 200
    return response.json()


def test_original_query_excludes_families_and_all_egg_presentations(client):
    body = message(
        client,
        "Cuales son los productos mas vendidos sin contar bolsa de pan, "
        "huevos y excluyendo cualquiera que venga de la familia carniceria o verduleria.",
    )
    products = body["metadata"]["tool_data"]["products"]
    assert [product["sku"] for product in products] == ["CHOC", "COCA", "MILK"]
    assert body["metadata"]["tool_data"]["excluded_families"] == ["CARNICERIA", "VERDULERIA"]
    assert "19 cantidad registrada" in body["reply"]
    assert "19.000" not in body["reply"]


def test_summary_counts_distinct_tickets_and_has_no_ranking_limit(client):
    body = message(client, "Resumen de ventas de los ultimos 7 dias")
    totals = body["metadata"]["tool_data"]["totals"]
    assert totals["ticket_count"] == 1
    assert Decimal(totals["quantity_sold"]) == 450
    assert Decimal(totals["average_ticket"]) == 4500


def test_product_history_and_previous_period_comparison(client):
    history = message(client, "Cuanto vendi de COCA en los ultimos 7 dias")
    assert history["metadata"]["tool"] == "sales.product_history"
    assert history["metadata"]["tool_data"]["history"][0]["date"] == "2026-09-28"
    body = message(client, "Compara ventas de los ultimos 7 dias")
    data = body["metadata"]["tool_data"]
    assert data["comparison_date_from"] == "2026-09-15"
    assert data["comparison_date_to"] == "2026-09-21"
    assert Decimal(data["comparison_totals"]["revenue"]) == 80
    assert Decimal(data["changes"]["revenue"]["absolute"]) == 4420


def test_catalog_and_stock_queries_are_tenant_scoped(client):
    search = message(client, "Buscame COCA")
    assert [p["sku"] for p in search["metadata"]["tool_data"]["products"]] == ["COCA"]
    stock = message(client, "Productos sin stock")
    assert {p["sku"] for p in stock["metadata"]["tool_data"]["products"]} == {"MEAT", "COCA"}


def test_today_uses_calendar_date_and_does_not_return_last_loaded_day(client):
    body = client.post(
        "/assistant/message",
        json={
            "channel": "web",
            "external_user_id": "user",
            "business_id": "business-1",
            "message_type": "text",
            "text": "Cuanto vendi hoy",
            "timestamp": "2026-10-03T18:00:00Z",
        },
    ).json()
    data = body["metadata"]["tool_data"]
    assert data["date_from"] == data["date_to"] == "2026-10-03"
    assert data["totals"]["line_count"] == 0


def test_unresolved_filter_stops_plan_and_does_not_claim_exclusion(client):
    registry = main.build_tool_registry()
    request = AssistantRequest(
        channel="web",
        external_user_id="user",
        business_id="business-1",
        message_type="text",
        text="test",
        timestamp=datetime(2026, 9, 28, tzinfo=UTC),
    )
    result = AgentExecutionService(tool_registry=registry).execute(
        request=request,
        plan=AgentPlan(
            confidence=0.9,
            steps=[
                AgentStep(
                    tool=AssistantToolName.SALES_TOP_PRODUCTS,
                    arguments={"exclude_families": ["familia inexistente xyz"]},
                ),
                AgentStep(tool=AssistantToolName.SALES_SUMMARY, arguments={}),
            ],
        ),
    )
    assert len(result.steps) == 1
    assert result.metadata["requires_clarification"]
    assert "Exclui" not in result.reply


def test_registry_rejects_foreign_business_and_unknown_arguments(client):
    registry = main.build_tool_registry()
    with pytest.raises(ValueError):
        registry.execute(
            name=AssistantToolName.SALES_SUMMARY,
            arguments={"business_id": "business-2"},
            context=ToolExecutionContext(business_id="business-1"),
        )

    with pytest.raises(ValueError):
        registry.execute(
            name=AssistantToolName.SALES_TOP_PRODUCTS,
            arguments={"exclude_family": "carniceria"},
            context=ToolExecutionContext(business_id="business-1"),
        )


def test_explicit_dates_and_comparison_ranges(client):
    result = message(client, "Compara ventas del 22/09 al 28/09 con el 15/09 al 21/09")
    data = result["metadata"]["tool_data"]
    assert data["date_from"] == "2026-09-22"
    assert data["comparison_date_from"] == "2026-09-15"
    assert data["comparison_date_to"] == "2026-09-21"


def test_invalid_dates_and_stock_without_threshold_request_clarification(client):
    result = message(client, "Resumen de ventas del 31/09 al 32/09")
    assert result["metadata"]["requires_clarification"]
    result = message(client, "Productos con stock bajo")
    assert result["metadata"]["requires_clarification"]


def test_zero_base_comparison(client):
    tool = main.build_tool_registry()
    context = ToolExecutionContext(business_id="business-1")
    result = tool.execute(
        name=AssistantToolName.SALES_COMPARE_PERIODS,
        arguments={
            "date_from": "2026-09-28",
            "date_to": "2026-09-28",
            "comparison_date_from": "2026-09-01",
            "comparison_date_to": "2026-09-01",
        },
        context=context,
    )
    assert result.data["changes"]["revenue"]["percent"] is None
    assert "sin porcentaje" in result.reply


def test_agent_can_execute_every_new_tool(client):
    registry = main.build_tool_registry()
    context = ToolExecutionContext(business_id="business-1")
    cases = {
        AssistantToolName.SALES_SUMMARY: {},
        AssistantToolName.SALES_PRODUCT_HISTORY: {"product": "COCA"},
        AssistantToolName.SALES_COMPARE_PERIODS: {},
        AssistantToolName.CATALOG_SEARCH_PRODUCTS: {"query": "COCA"},
        AssistantToolName.INVENTORY_STOCK_STATUS: {"family": "bebidas", "max_stock": 0},
    }
    assert len(registry.definitions()) == 8
    for name, arguments in cases.items():
        result = registry.execute(name=name, arguments=arguments, context=context)
        assert result.name == name
        assert result.data


def test_filters_use_canonical_family_and_exclude_before_limit(client):
    repository = SqlProcurementRepository(main.SessionLocal)
    family = repository.upsert_product_families(
        [
            ProductFamily(
                business_id="business-1",
                external_family_id="canonical",
                name="CARNICERIA",
                normalized_name="carniceria",
            )
        ]
    )[0]
    with main.SessionLocal() as session:
        product = session.query(ProductModel).filter_by(business_id="business-1", sku="MEAT").one()
        product.family_id = family.id
        product.category = "ALMACEN"
        session.commit()
    result = main.build_tool_registry().execute(
        name=AssistantToolName.SALES_TOP_PRODUCTS,
        arguments={"exclude_families": ["carniceria", "verduleria"], "limit": 1},
        context=ToolExecutionContext(business_id="business-1"),
    )
    assert result.data["products"][0]["sku"] == "BREAD"
    summary = main.build_tool_registry().execute(
        name=AssistantToolName.SALES_SUMMARY,
        arguments={"exclude_families": ["carniceria", "verduleria"]},
        context=ToolExecutionContext(business_id="business-1"),
    )
    assert summary.data["totals"]["quantity_sold"] == 260


def test_cost_zero_is_reported_and_margin_changes_are_unavailable(client):
    with main.SessionLocal() as session:
        line = (
            session.query(SalesItemLineModel)
            .filter_by(business_id="business-1", sku="COCA")
            .first()
        )
        line.unit_cost = Decimal(0)
        session.commit()
    result = main.build_tool_registry().execute(
        name=AssistantToolName.SALES_COMPARE_PERIODS,
        arguments={},
        context=ToolExecutionContext(business_id="business-1"),
    )
    assert "margen no es confiable" in result.reply
    assert result.data["changes"]["gross_margin"]["absolute"] is None


def test_ambiguous_product_does_not_silently_choose_a_presentation(client):
    result = message(client, "Cuanto vendi de huevos")
    assert result["metadata"]["tool_metadata"]["requires_clarification"]


def test_top_products_limit_above_ten_is_rendered(client):
    repository = SqlProcurementRepository(main.SessionLocal)
    # More than ten sellable products verify the reply respects the requested limit.
    from app.db.models import ProductSalesDailyModel

    for index in range(12):
        product = repository.add_product(
            Product(
                business_id="business-1",
                sku=f"EXTRA{index}",
                name=f"EXTRA {index}",
                normalized_name=f"extra {index}",
            )
        )
        with main.SessionLocal() as session:
            session.add(
                ProductSalesDailyModel(
                    id=f"daily-extra-{index}",
                    business_id="business-1",
                    source_import_id=session.query(ProductSalesDailyModel).first().source_import_id,
                    external_product_id=f"extra-{index}",
                    product_id=product.id,
                    sku=product.sku,
                    sale_date=datetime(2026, 9, 28, tzinfo=UTC),
                    quantity_sold=Decimal(1),
                )
            )
            session.commit()
    result = main.build_tool_registry().execute(
        name=AssistantToolName.SALES_TOP_PRODUCTS,
        arguments={"limit": 15},
        context=ToolExecutionContext(business_id="business-1"),
    )
    assert len(result.data["products"]) == 15
    assert "\n15. " in result.reply
