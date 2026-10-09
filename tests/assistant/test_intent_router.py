from datetime import datetime
from zoneinfo import ZoneInfo

from app.assistant.intent_router import IntentRouter
from app.schemas.assistant import AssistantRequest, Channel, MessageType
from app.schemas.intents import IntentName


def make_request(text: str) -> AssistantRequest:
    return AssistantRequest(
        channel=Channel.CLI,
        external_user_id="user-1",
        business_id="business-1",
        message_type=MessageType.TEXT,
        text=text,
        timestamp=datetime(2026, 7, 22, 9, 0, tzinfo=ZoneInfo("America/Buenos_Aires")),
    )


def test_routes_create_reminder() -> None:
    result = IntentRouter().route(make_request("recordame revisar la camara"))

    assert result.intent == IntentName.CREATE_REMINDER
    assert result.entities["title"] == "revisar la camara"


def test_routes_create_reminder_with_due_at() -> None:
    result = IntentRouter().route(make_request("recordame cortar fiambre a las 10:05hs de hoy"))

    assert result.intent == IntentName.CREATE_REMINDER
    assert result.entities["title"] == "cortar fiambre"
    assert result.entities["due_at"] == "2026-07-22T10:05:00-03:00"


def test_routes_haceme_acordar_with_relative_due_at() -> None:
    result = IntentRouter().route(
        make_request("Haceme acordar en 2 minutos que me prepare la ropa por favor")
    )

    assert result.intent == IntentName.CREATE_REMINDER
    assert result.entities["title"] == "que me prepare la ropa por favor"
    assert result.entities["due_at"] == "2026-07-22T09:02:00-03:00"


def test_routes_list_reminders() -> None:
    result = IntentRouter().route(make_request("mostrar recordatorios pendientes"))

    assert result.intent == IntentName.LIST_REMINDERS


def test_routes_unknown() -> None:
    result = IntentRouter().route(make_request("buen dia"))

    assert result.intent == IntentName.UNKNOWN


def test_routes_sales_top_products_rank_range() -> None:
    result = IntentRouter().route(
        make_request("Dame los productos mas vendidos del top 7 al top 12")
    )

    assert result.intent == IntentName.QUERY_SALES_TOP_PRODUCTS
    assert result.entities["rank_from"] == 7
    assert result.entities["rank_to"] == 12
    assert result.entities["limit"] == 6


def test_routes_sales_top_products_skips_first_ranks() -> None:
    result = IntentRouter().route(
        make_request("Dame los productos mas vendidos excluyendo los primeros 7")
    )

    assert result.intent == IntentName.QUERY_SALES_TOP_PRODUCTS
    assert result.entities["rank_from"] == 8
    assert result.entities["exclude_product_names"] == []


def test_routes_sales_top_products_all_time_with_clean_family() -> None:
    result = IntentRouter().route(
        make_request(
            "Dame los productos mas vendidos historicos, "
            "de la categoria almacen por favor."
        )
    )

    assert result.intent == IntentName.QUERY_SALES_TOP_PRODUCTS
    assert result.entities["period"] == "all_time"
    assert "days" not in result.entities
    assert result.entities["family"] == "almacen"


def test_routes_sales_top_products_multiple_families_since_month() -> None:
    result = IntentRouter().route(
        make_request(
            "Dame los 5 productos mas vendidos de la categoria almacen, "
            "y de la familia verduleria, desde marzo hasta ahora."
        )
    )

    assert result.intent == IntentName.QUERY_SALES_TOP_PRODUCTS
    assert result.entities["limit"] == 5
    assert result.entities["families"] == ["almacen", "verduleria"]
    assert result.entities["date_from"] == "2026-03-01"
    assert "days" not in result.entities


def test_routes_sales_top_products_union_when_multiple_families_without_hierarchy() -> None:
    result = IntentRouter().route(
        make_request("Dame los 5 productos mas vendidos de almacen y verduleria desde marzo")
    )

    assert result.intent == IntentName.QUERY_SALES_TOP_PRODUCTS
    assert result.entities["families"] == ["almacen", "verduleria"]
