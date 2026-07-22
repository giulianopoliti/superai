import re
from datetime import datetime, timedelta
from unicodedata import combining, normalize
from zoneinfo import ZoneInfo

DEFAULT_TIMEZONE = "America/Buenos_Aires"


def parse_due_at(
    text: str,
    *,
    reference: datetime,
    timezone: str = DEFAULT_TIMEZONE,
) -> datetime | None:
    local_tz = ZoneInfo(timezone)
    local_reference = reference.astimezone(local_tz)
    normalized = _normalize_text(text)

    relative_minutes = re.search(r"\ben\s+(\d{1,4})\s+minutos?\b", normalized)
    if relative_minutes:
        return local_reference + timedelta(minutes=int(relative_minutes.group(1)))

    relative_hours = re.search(r"\ben\s+(\d{1,3})\s+horas?\b", normalized)
    if relative_hours:
        return local_reference + timedelta(hours=int(relative_hours.group(1)))

    time_match = re.search(r"\ba\s+las\s+(\d{1,2})(?::(\d{2}))?\s*(?:hs?|horas)?\b", normalized)
    if not time_match:
        return None

    hour = int(time_match.group(1))
    minute = int(time_match.group(2) or 0)
    if hour > 23 or minute > 59:
        return None

    days_delta = 0
    if "pasado manana" in normalized:
        days_delta = 2
    elif "manana" in normalized:
        days_delta = 1

    due_date = local_reference.date() + timedelta(days=days_delta)
    due_at = datetime.combine(due_date, datetime.min.time(), tzinfo=local_tz).replace(
        hour=hour,
        minute=minute,
    )

    if days_delta == 0 and "hoy" not in normalized and due_at <= local_reference:
        due_at += timedelta(days=1)

    return due_at


def remove_due_at_expression(text: str) -> str:
    patterns = [
        r"(?i)\s*\ben\s+\d{1,4}\s+minutos?\b",
        r"(?i)\s*\ben\s+\d{1,3}\s+horas?\b",
        r"(?i)\s*\ba\s+las\s+\d{1,2}(?::\d{2})?\s*(?:hs?|horas)?\s*(?:de\s+)?(?:hoy|ma[nñ]ana|pasado\s+ma[nñ]ana)?\b",
    ]
    cleaned = text
    for pattern in patterns:
        cleaned = re.sub(pattern, "", cleaned).strip()
    return re.sub(r"\s+", " ", cleaned).strip(" .,")


def _normalize_text(text: str) -> str:
    decomposed = normalize("NFD", text.lower())
    return "".join(char for char in decomposed if not combining(char))
