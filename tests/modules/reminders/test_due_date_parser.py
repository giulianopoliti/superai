from datetime import datetime
from zoneinfo import ZoneInfo

from app.modules.reminders.due_date_parser import parse_due_at, remove_due_at_expression


def test_parse_due_at_today_explicit_time() -> None:
    reference = datetime(2026, 7, 22, 9, 0, tzinfo=ZoneInfo("America/Buenos_Aires"))

    due_at = parse_due_at(
        "recordame cortar fiambre a las 10:05hs de hoy",
        reference=reference,
    )

    assert due_at == datetime(2026, 7, 22, 10, 5, tzinfo=ZoneInfo("America/Buenos_Aires"))


def test_parse_due_at_tomorrow_explicit_time_with_accent() -> None:
    reference = datetime(2026, 7, 22, 9, 0, tzinfo=ZoneInfo("America/Buenos_Aires"))

    due_at = parse_due_at("recordame abrir caja mañana a las 9", reference=reference)

    assert due_at == datetime(2026, 7, 23, 9, 0, tzinfo=ZoneInfo("America/Buenos_Aires"))


def test_parse_due_at_tomorrow_explicit_time_without_accent() -> None:
    reference = datetime(2026, 7, 22, 9, 0, tzinfo=ZoneInfo("America/Buenos_Aires"))

    due_at = parse_due_at("recordame abrir caja manana a las 9", reference=reference)

    assert due_at == datetime(2026, 7, 23, 9, 0, tzinfo=ZoneInfo("America/Buenos_Aires"))


def test_parse_due_at_relative_minutes() -> None:
    reference = datetime(2026, 7, 22, 9, 0, tzinfo=ZoneInfo("America/Buenos_Aires"))

    due_at = parse_due_at("recordame revisar heladera en 10 minutos", reference=reference)

    assert due_at == datetime(2026, 7, 22, 9, 10, tzinfo=ZoneInfo("America/Buenos_Aires"))


def test_remove_due_at_expression() -> None:
    assert remove_due_at_expression("cortar fiambre a las 10:05hs de hoy") == "cortar fiambre"
