from decimal import Decimal


def format_number(value: object) -> str:
    number = Decimal(str(value))
    formatted = f"{number:,.3f}".rstrip("0").rstrip(".")
    return formatted.replace(",", "_").replace(".", ",").replace("_", ".")


def cost_warning(zero_cost_lines: int) -> str:
    if zero_cost_lines:
        return "Hay ventas con costo cero: el margen no es confiable hasta revisar esos costos."
    return ""
