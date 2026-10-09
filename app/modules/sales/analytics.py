from decimal import Decimal


def compare_sales_totals(current: dict, previous: dict) -> dict[str, dict]:
    changes = {}
    for key in ("quantity_sold", "revenue", "ticket_count", "gross_margin"):
        valid = key != "gross_margin" or (
            current["margin_reliable"] and previous["margin_reliable"]
        )
        delta = current[key] - previous[key] if valid else None
        changes[key] = {
            "absolute": delta,
            "percent": (
                (Decimal(delta) / Decimal(previous[key]) * 100).quantize(Decimal("0.01"))
                if valid and previous[key] != 0
                else None
            ),
        }
    return changes
