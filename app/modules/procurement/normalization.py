import re
import unicodedata
from decimal import Decimal, InvalidOperation

UNIT_PATTERN = re.compile(
    r"(?P<size>\d+(?:[,.]\d+)?)\s*(?P<unit>kgs?|kg|grs?|g|lts?|lt|l|ml|cc)\b",
    re.IGNORECASE,
)

PACK_PATTERNS = (
    re.compile(
        r"\b(?P<quantity>\d{1,3})\s*[x×]\s*\d+(?:[,.]\d+)?\s*"
        r"(?:kgs?|kg|grs?|gr|g|lts?|lt|l|ml|cc)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b\d+(?:[,.]\d+)?\s*(?:kgs?|kg|grs?|gr|g|lts?|lt|l|ml|cc)\s*"
        r"[x×]\s*(?P<quantity>\d{1,3})\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bpack\s*(?:de\s*)?(?P<quantity>\d{1,3})\b", re.IGNORECASE),
    re.compile(
        r"\b(?P<quantity>\d{1,3})\s*(?:u|un|uni|unid|unidades)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bx\s*(?P<quantity>\d{1,2})\b"
        r"(?!\s*(?:kgs?|kg|grs?|gr|g|lts?|lt|l|ml|cc)\b)",
        re.IGNORECASE,
    ),
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

    # Presentations use either decimal separator; the money parser treats dots
    # as thousands separators and would silently turn 1.5 kg into 15 kg.
    size = Decimal(match.group("size").replace(",", "."))
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


def parse_package_quantity(name: str) -> int | None:
    for pattern in PACK_PATTERNS:
        match = pattern.search(name)
        if match is None:
            continue
        quantity = int(match.group("quantity"))
        if quantity > 1:
            return quantity
    return None
