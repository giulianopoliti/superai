from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

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


def message(client, text, timestamp="2026-09-28T18:00:00Z", **scope):
    response = client.post(
        "/assistant/message",
        json={
            "channel": "web",
            "external_user_id": "test-user",
            "business_id": "business-1",
            "message_type": "text",
            "text": text,
            "timestamp": timestamp,
            **scope,
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


def test_rank_comparison_uses_same_products_and_keeps_filters_on_follow_up(client):
    scope = {"conversation_id": str(uuid4())}
    body = message(client, "Más vendidos de bebidas esta semana", **scope)
    assert [p["sku"] for p in body["metadata"]["tool_data"]["products"]] == ["COCA"]
    body = message(client, "Comparalo con la semana anterior", **scope)
    result = body["metadata"]["tool_data"]
    assert result["date_from"] == "2026-09-28"
    comparison = result["comparison"]
    assert comparison["date_from"] == "2026-09-21"
    assert comparison["date_to"] == "2026-09-27"
    assert len(comparison["rows"]) == 1
    row = comparison["rows"][0]
    assert row["product_name"] == "COCA COLA 1.5L"
    assert Decimal(row["previous"]) == 8
    assert Decimal(row["current"]) == 16
    assert Decimal(row["percent_change"]) == 100
    assert "distinta duración" in body["reply"]
    body = message(client, "Incluí también los productos poco relevantes", **scope)
    result = body["metadata"]["tool_data"]
    assert result["include_irrelevant_products"]
    assert result["comparison"]["date_from"] == "2026-09-21"
    body = message(client, "Y sin contar los poco relevantes", **scope)
    assert not body["metadata"]["tool_data"]["include_irrelevant_products"]
    for alternate in (
        {"conversation_id": str(uuid4())},
        {**scope, "external_user_id": "other-user"},
        {**scope, "channel": "cli"},
        {**scope, "business_id": "business-2"},
    ):
        result = message(client, "Lo mismo", **alternate)
        assert "tool" not in result["metadata"]


def test_sales_follow_ups_from_chat_keep_comparison_and_support_more_records(client):
    conversation = {"conversation_id": str(uuid4())}
    message(
        client, "Mostrame los 5 productos más vendidos esta semana y comparalos "
        "con la semana anterior", **conversation,
    )
    body = message(
        client, "Hace lo mismo pero solamente de la familia almacen, bebidas, fiambreria. "
        "Lo demas sacalo.", **conversation,
    )
    data = body["metadata"]["tool_data"]
    assert data["limit"] == 5
    assert data["date_from"] == data["date_to"] == "2026-09-28"
    assert data["resolved_categories"] == ["ALMACEN", "BEBIDAS", "FIAMBRERIA"]
    assert data["comparison"]["date_from"] == "2026-09-21"
    assert data["comparison"]["date_to"] == "2026-09-27"
    body = message(client, "Mostrame 2 más y comparalo con el 21/09", **conversation)
    data = body["metadata"]["tool_data"]
    assert data["limit"] == 2
    assert data["rank_from"] == 6
    assert data["date_from"] == "2026-09-28"
    assert data["comparison"]["date_to"] == "2026-09-21"
    assert data["products"] == []
    body = message(client, "Dame 80 registros", **conversation)
    assert body["metadata"]["tool_data"]["limit"] == 80
    assert body["metadata"]["tool_data"]["rank_from"] is None
    body = message(client, "Dame 200 registros", **conversation)
    assert "100 productos por mensaje" in body["reply"]


def test_sales_growth_is_ranked_by_increase_and_never_uses_stock(client):
    conversation = {"conversation_id": str(uuid4())}
    message(client, "Mostrame el top 1 de productos más vendidos esta semana", **conversation)
    body = message(
        client, "Hace lo mismo pero solamente de la familia bebidas", **conversation,
    )
    assert body["metadata"]["tool_data"]["resolved_categories"] == ["BEBIDAS"]
    body = message(
        client, "Que productos se vendieron mucho mas comparado a semanas anteriores? "
        "Alguno que tenga que tener ne cuenta para pedir urgente? No te guies por el stock.",
        **conversation,
    )
    assert body["metadata"]["tool"] == "sales.top_products"
    data = body["metadata"]["tool_data"]
    assert data["growth"] and data["limit"] == 1
    assert data["resolved_categories"] == ["BEBIDAS"]
    assert data["comparison"]["baseline_periods"] == [["2026-09-21", "2026-09-21"]]
    row = data["comparison"]["rows"][0]
    assert Decimal(row["absolute_change"]) == 8
    assert Decimal(row["percent_change"]) == 100
    assert row["current_stock"] is None
    assert data["products"][0]["current_stock"] is None
    assert "stock de catálogo" not in body["reply"]
    assert "para confirmar urgencia" in body["reply"].lower()
    missing = message(
        client, "Y qué productos se vendieron mucho más comparado con el 15/09", **conversation,
    )
    assert not missing["metadata"]["tool_data"]["comparison"]["data_available"]
    assert "No puedo calcular subas" in missing["reply"]


def test_ranking_explicit_comparison_and_missing_sales(client):
    body = message(
        client,
        "Productos más vendidos esta semana vs. del 21/09 al 23/09, "
        "incluí los productos de baja importancia",
    )
    result = body["metadata"]["tool_data"]
    assert result["include_irrelevant_products"] is True
    assert result["comparison"]["date_to"] == "2026-09-23"
    assert result["comparison"]["data_available"]
    body = message(client, "Más vendidos esta semana, comparalo con el 15/09")
    result = body["metadata"]["tool_data"]["comparison"]
    assert not result["data_available"]
    assert all(row["previous"] is None for row in result["rows"])


def test_basket_stats_use_distinct_tickets_and_scope_survives_app_restart(client):
    repository = SqlProcurementRepository(main.SessionLocal)
    for family in ("LIMPIEZA", "PERFUMERIA"):
        repository.add_product(Product(
            business_id="business-1", name=family, normalized_name=family.lower(),
            category=family,
        ))
    csv = ["id;idventa;idproducto;rcantidad;rtotal;rcostou;fcreacion;scodproducto"]
    for index in range(6):
        skus = ["BREAD", "EGG2", "MILK", "EGG1", "VEG", "BREAD"] if index < 3 else ["COCA"]
        for line, sku in enumerate(skus):
            csv.append(f"basket-{index}-{line};basket-{index};{sku};1;10;2;2026-09-29T10:00:00Z;{sku}")
        if index < 3:
            csv.append(f"return-{index};basket-{index};EGG2;-1;-10;2;2026-09-29T10:00:00Z;EGG2")
    response = client.post(
        "/sales/imports/from-file",
        data={"business_id": "business-1"},
        files={
            "file": ("ventasitems_2026-09-29_2026-09-29.csv", "\n".join(csv).encode(), "text/csv")
        },
    )
    assert response.status_code == 200
    scope = {"conversation_id": str(uuid4())}
    body = message(
        client, "Dame posibles promociones entre la familia almacén y la familia lácteos "
        "del 29/09", **scope,
    )
    data = body["metadata"]["tool_data"]
    assert data["ticket_count"] == 6
    assert len(data["pairs"]) == 1
    pair = data["pairs"][0]
    assert pair["left"]["id"] == "BREAD"
    assert pair["right"]["id"] == "MILK"
    assert pair["tickets_together"] == 3
    assert Decimal(str(pair["support_pct"])) == 50
    assert Decimal(str(pair["lift"])) == 2
    assert Decimal(str(pair["confidence_left_to_right_pct"])) == 100
    with TestClient(main.create_app()) as restarted:
        body = message(restarted, "Y ahora por familia", **scope)
        data = body["metadata"]["tool_data"]
        assert data["level"] == "family"
        assert data["date_from"] == "2026-09-29"
        assert any(
            {pair["left"]["name"], pair["right"]["name"]} == {"ALMACEN", "LACTEOS"}
            for pair in data["pairs"]
        )
        body = message(restarted, "Ahora dentro de la familia almacén", **scope)
        assert body["metadata"]["tool_data"]["pairs"] == []
        body = message(
            restarted,
            "Que no sean de la familia verduleria por favor. "
            "Almacen o fiambreria o limpieza o perfumeria",
            **scope,
        )
        data = body["metadata"]["tool_data"]
        assert data["date_from"] == data["date_to"] == "2026-09-29"
        assert data["ticket_count"] == 6
        assert data["include_families"] == ["ALMACEN", "FIAMBRERIA", "LIMPIEZA", "PERFUMERIA"]
        assert data["exclude_families"] == ["VERDULERIA"]
        assert [(pair["left"]["id"], pair["right"]["id"]) for pair in data["pairs"]] == [
            ("BREAD", "EGG1")
        ]
        assert Decimal(str(data["pairs"][0]["support_pct"])) == 50
        repeated = message(restarted, "Lo mismo", **scope)
        assert repeated["metadata"]["tool_data"] == data
        grouped = message(restarted, "Y ahora por familia", **scope)
        assert grouped["metadata"]["tool_data"]["include_families"] == data["include_families"]
        basket_scope = {"conversation_id": str(uuid4())}
        message(restarted, "Dame posibles promociones del 29/09", **basket_scope)
        remaining = message(
            restarted, "Ok, pero exclui la familia verduleria, de lo demas por favor.",
            **basket_scope,
        )
        data = remaining["metadata"]["tool_data"]
        assert data["date_from"] == data["date_to"] == "2026-09-29"
        assert data["ticket_count"] == 6
        assert data["exclude_families"] == ["VERDULERIA"]
        assert data["include_families"] == []
        assert data["family_a"] is None and data["family_b"] is None
        assert len(data["pairs"]) == 3
        assert "Cada compra con BOLSA PAN 1/2 KILO también llevó" in remaining["reply"]
        assert "(3 de 3 tickets)" in remaining["reply"]
        assert "50% de los tickets, 2 veces lo esperado por azar." in remaining["reply"]
        excluded_only = message(
            restarted, "Combos sin la familia verdulería del 29/09", **scope,
        )
        data = excluded_only["metadata"]["tool_data"]
        assert data["exclude_families"] == ["VERDULERIA"]
        assert data["include_families"] == []
        assert data["level"] == "product"
        assert all(
            pair["left"]["family"] != "VERDULERIA" and pair["right"]["family"] != "VERDULERIA"
            for pair in data["pairs"]
        )
        body = message(restarted, "Dame ideas de combos, 2 de cada familia, del 29/09")
        data = body["metadata"]["tool_data"]
        groups = {group["family"]: group["pairs"] for group in data["groups"]}
        assert data["per_family"] and data["limit"] == 2 and data["ticket_count"] == 6
        assert len(groups["ALMACEN"]) == len(groups["VERDULERIA"]) == 2
        assert groups["PERFUMERIA"] == []
        assert all(
            family in (pair["left"]["family"], pair["right"]["family"])
            for family, pairs in groups.items() for pair in pairs
        )
        body = message(restarted, "Y con verdulería")
        data = body["metadata"]["tool_data"]
        assert data["date_from"] == data["date_to"] == "2026-09-29"
        assert [(group["family"], len(group["pairs"])) for group in data["groups"]] == [
            ("VERDULERIA", 2)
        ]
        body = message(restarted, "Ahora entre almacén con fiambrería")
        data = body["metadata"]["tool_data"]
        assert not data["per_family"] and data["groups"] == []
        assert [(pair["left"]["id"], pair["right"]["id"]) for pair in data["pairs"]] == [
            ("BREAD", "EGG1")
        ]
        body = message(restarted, "Dame un combo de cada familia: almacén, fiambrería.")
        data = body["metadata"]["tool_data"]
        assert data["per_family"] and data["limit"] == 1
        assert [group["family"] for group in data["groups"]] == ["ALMACEN", "FIAMBRERIA"]


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
        AssistantToolName.SALES_BASKET_ASSOCIATIONS: {},
        AssistantToolName.CATALOG_SEARCH_PRODUCTS: {"query": "COCA"},
        AssistantToolName.INVENTORY_STOCK_STATUS: {"family": "bebidas", "max_stock": 0},
    }
    assert len(registry.definitions()) == 9
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


@pytest.mark.parametrize("text,tool,start,end", [
    ("Cuánto vendimos hoy", "sales.summary", "2026-09-28", "2026-09-28"),
    ("Ventas de ayer", "sales.summary", "2026-09-27", "2026-09-27"),
    ("Ventas del 28/09", "sales.summary", "2026-09-28", "2026-09-28"),
    ("Ventas del 2026-09-21 al 2026-09-28", "sales.summary", "2026-09-21", "2026-09-28"),
    ("Cuánto vendí de COCA este mes", "sales.product_history", None, None),
    (
        "Dame los productos más vendidos de esta semana", "sales.top_products",
        "2026-09-28", "2026-09-28",
    ),
    ("Qué es lo que más vendí esta semana", "sales.top_products", "2026-09-28", "2026-09-28"),
])
def test_common_question_matrix(client, text, tool, start, end):
    body = message(client, text)
    assert body["metadata"]["tool"] == tool
    data = body["metadata"]["tool_data"]
    if start:
        assert data["date_from"] == start
        assert data["date_to"] == end
    else:
        assert data["summary"]["date_from"] == "2026-09-01"
        assert data["summary"]["date_to"] == "2026-09-28"


def test_revenue_and_ticket_questions_do_not_dump_unrequested_metrics(client):
    revenue = message(client, "Cuánto vendí hoy")
    assert "$4.500" in revenue["reply"]
    assert "tickets" not in revenue["reply"]
    assert "Margen" not in revenue["reply"]
    assert "cantidad registrada" not in revenue["reply"]
    assert revenue["metadata"]["tool_data"]["totals"]["ticket_count"] == 1
    ticket = message(client, "Ticket promedio de hoy")
    assert "$4.500" in ticket["reply"]
    assert "Ventas " not in ticket["reply"]
    assert "Margen" not in ticket["reply"]


def test_missing_current_day_data_is_not_presented_as_zero_sales(client):
    result = message(client, "Cuánto vendí hoy", "2026-10-03T18:00:00Z")
    assert "No hay ventas cargadas" in result["reply"]
    assert "$0" not in result["reply"]
    assert "28/09/2026" in result["reply"]
    comparison = message(client, "Compará hoy con ayer", "2026-10-03T18:00:00Z")
    data = comparison["metadata"]["tool_data"]
    assert data["date_from"] == "2026-10-03"
    assert data["comparison_date_from"] == "2026-10-02"
    assert data["comparison_data_available"] is False
    assert "No puedo confirmar la variación" in comparison["reply"]


def test_product_quantity_query_only_displays_daily_rows_when_requested(client):
    total = message(client, "Cuánto vendí de COCA en los últimos 7 días")
    assert "Días con ventas cargadas" not in total["reply"]
    assert total["metadata"]["tool_data"]["history"]
    detail = message(client, "Historial de ventas de COCA en los últimos 7 días")
    assert "Días con ventas cargadas" in detail["reply"]


def test_price_and_stock_questions_keep_the_requested_information(client):
    price = message(client, "Precio de COCA")
    assert "precio de venta $10" in price["reply"]
    assert "stock" not in price["reply"]
    assert "SKU" not in price["reply"]
    stock = message(client, "Stock de COCA")
    assert "stock 0" in stock["reply"]
    assert "$" not in stock["reply"]


def test_missing_price_is_explicit_and_ambiguous_stock_requests_a_presentation(client):
    repository = SqlProcurementRepository(main.SessionLocal)
    repository.add_product(Product(
        business_id="business-1", sku="MISSING", name="SIN PRECIO",
        normalized_name="sin precio", sale_price=None,
    ))
    result = message(client, "Precio de SIN PRECIO")
    assert "sin precio de venta cargado" in result["reply"]
    assert "$0" not in result["reply"]
    repository.add_product(Product(
        business_id="business-1", sku="COCA2", name="COCA COLA 2.25L",
        normalized_name="coca cola 2.25l", sale_price=Decimal(20), stock_quantity=Decimal(5),
    ))
    ambiguous = message(client, "Stock de COCA")
    assert ambiguous["requires_confirmation"]
    assert "1.5L" in ambiguous["reply"] and "2.25L" in ambiguous["reply"]
    assert "¿Cuál querés consultar?" in ambiguous["reply"]
    assert "PRIVADA" not in ambiguous["reply"]


def test_daily_summary_uses_only_requested_calendar_day(client):
    result = message(client, "Resumen del día", "2026-10-03T18:00:00Z")
    assert result["metadata"]["date_from"] == "2026-10-03"
    assert result["metadata"]["date_to"] == "2026-10-03"
    assert "No hay ventas cargadas" in result["reply"]
    assert "unidades" not in result["reply"]
    assert "margen" not in result["reply"]


def test_reminder_about_stock_is_created_and_list_does_not_expose_uuids(client):
    created = message(client, "Recordame revisar el stock de COCA mañana a las 10")
    assert created["actions"][0]["type"] == "reminder.created"
    reminder_id = created["actions"][0]["payload"]["reminder_id"]
    listed = message(client, "Mostrar recordatorios pendientes")
    assert "revisar el stock" in listed["reply"]
    assert reminder_id not in listed["reply"]
    assert listed["metadata"]["reminders"][0]["id"] == reminder_id


@pytest.mark.parametrize("text", ["hola", "qué podés hacer"])
def test_greeting_and_help_offer_real_brief_capabilities(client, text):
    result = message(client, text)
    assert "ventas" in result["reply"]
    assert "recordatorios" in result["reply"]
    assert not result["actions"]
    assert len(result["reply"]) < 300


def test_ranking_preferences_apply_before_limit_and_do_not_change_sales_totals(client, monkeypatch):
    from app.db.repositories.sql_sales import SqlSalesRepository

    products = SqlProcurementRepository(main.SessionLocal).list_products(business_id="business-1")
    excluded = {p.id for p in products if p.sku in {"BREAD", "EGG2"}}
    monkeypatch.setattr(
        SqlSalesRepository, "list_excluded_sales_ranking_product_ids",
        lambda self, *, business_id: excluded if business_id == "business-1" else set(),
    )
    result = message(client, "Dame los 3 productos más vendidos de esta semana")
    data = result["metadata"]["tool_data"]
    assert [p["sku"] for p in data["products"]] == ["MEAT", "VEG", "EGG1"]
    assert data["preference_excluded_product_count"] == 2
    assert set(data["preference_excluded_product_ids"]) == excluded
    assert "2 productos marcados como poco relevantes" in result["reply"]
    summary = message(client, "Cuánto vendí hoy")
    assert Decimal(summary["metadata"]["tool_data"]["totals"]["revenue"]) == 4500
    history = message(client, "Cuánto vendí de BREAD hoy")
    assert Decimal(history["metadata"]["tool_data"]["summary"]["quantity_sold"]) == 80


@pytest.mark.parametrize("suffix", [
    "incluí también los poco relevantes", "mostrá también los poco relevantes", "sin exclusiones"
])
def test_ranking_can_include_preferences_without_losing_family_or_explicit_exclusions(
    client, monkeypatch, suffix
):
    from app.db.repositories.sql_sales import SqlSalesRepository

    products = SqlProcurementRepository(main.SessionLocal).list_products(business_id="business-1")
    excluded = {p.id for p in products if p.sku in {"BREAD", "EGG2"}}
    monkeypatch.setattr(
        SqlSalesRepository, "list_excluded_sales_ranking_product_ids",
        lambda self, *, business_id: excluded,
    )
    result = message(
        client, f"Dame los productos más vendidos familia ALMACEN esta semana, {suffix}"
    )
    data = result["metadata"]["tool_data"]
    assert data["include_irrelevant_products"] is True
    assert data["preference_excluded_product_count"] == 0
    assert [p["sku"] for p in data["products"]] == ["BREAD", "EGG2"]
    explicit = message(
        client, "Dame los productos más vendidos de esta semana sin contar huevos, " + suffix
    )
    skus = {p["sku"] for p in explicit["metadata"]["tool_data"]["products"]}
    assert "BREAD" in skus
    assert not {"EGG1", "EGG2"} & skus
