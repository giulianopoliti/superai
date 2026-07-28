import re
import unicodedata
from decimal import Decimal, InvalidOperation

UNIT_PATTERN = re.compile(
    r"(?P<size>\d+(?:[,.]\d+)?)\s*(?P<unit>kgs?|kg|grs?|g|lts?|lt|l|ml|cc)\b",
    re.IGNORECASE,
)


def normalize_text(value: str | None) -> str:
    if not value:
        return ""
    value = value.replace("�", "n")
    without_accents = "".join(
        character
        for character in unicodedata.normalize("NFKD", value)
        if not unicodedata.combining(character)
    )
    lowered = without_accents.lower()
    cleaned = re.sub(r"[^a-z0-9]+", " ", lowered)
    return re.sub(r"\s+", " ", cleaned).strip()


def normalize_match_text(value: str | None) -> str:
    normalized = normalize_text(value)
    replacements = {
        " t b ": " tetrabrick ",
        " tb ": " tetrabrick ",
        " tetrabrik ": " tetrabrick ",
        " tetrabrick ": " tetrabrick ",
        " lt ": " l ",
        " lts ": " l ",
        " grs ": " g ",
        " gr ": " g ",
        " jabon ": " jabon ",
    }
    padded = f" {normalized} "
    for source, target in replacements.items():
        padded = padded.replace(source, target)
    return re.sub(r"\s+", " ", padded).strip()


def parse_argentine_decimal(value: str | None) -> Decimal | None:
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        return None

    normalized = stripped.replace(".", "").replace(",", ".")
    try:
        return Decimal(normalized)
    except InvalidOperation:
        return None


def parse_unit(name: str) -> tuple[Decimal | None, str | None]:
    match = UNIT_PATTERN.search(name)
    if match is None:
        return None, None

    size = parse_argentine_decimal(match.group("size"))
    unit = match.group("unit").lower()
    normalized_unit = {
        "kgs": "kg",
        "grs": "g",
        "gr": "g",
        "lts": "l",
        "lt": "l",
        "cc": "ml",
    }.get(unit, unit)
    return size, normalized_unit
