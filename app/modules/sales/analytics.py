from collections import Counter
from collections.abc import Iterable
from decimal import Decimal
from itertools import combinations


def basket_associations(
    tickets: Iterable[dict[str, tuple[str, str | None]]],
    *,
    family_a: str | None,
    family_b: str | None,
    min_tickets: int,
    limit: int,
    include_families: set[str] | None = None,
    exclude_families: set[str] | None = None,
    group_families: list[str] | None = None,
) -> tuple[int, list[dict], dict[str, list[dict]]]:
    total = 0
    frequencies: Counter[str] = Counter()
    pairs: Counter[tuple[str, str]] = Counter()
    items: dict[str, tuple[str, str | None]] = {}
    for ticket in tickets:
        total += 1
        if include_families or exclude_families:
            ticket = {
                key: item for key, item in ticket.items()
                if (not include_families or item[1] in include_families)
                and (not exclude_families or item[1] not in exclude_families)
            }
        items.update(ticket)
        frequencies.update(ticket.keys())
        if family_a is None:
            pairs.update(combinations(sorted(ticket), 2))
            continue
        left = [key for key, (_, family) in ticket.items() if family == family_a]
        if family_b is None or family_a == family_b:
            pairs.update(combinations(sorted(left), 2))
            continue
        right = [key for key, (_, family) in ticket.items() if family == family_b]
        pairs.update((a, b) for a in left for b in right)

    ranked = []
    for (a, b), count in pairs.items():
        a_count, b_count = frequencies[a], frequencies[b]
        if count < min_tickets or count * total <= a_count * b_count:
            continue
        lift = Decimal(count * total) / (a_count * b_count)
        ranked.append((count, lift, a, b))
    ranked.sort(key=lambda pair: (-pair[0], -pair[1], pair[2], pair[3]))
    groups = {family: [] for family in group_families or []}
    if group_families is not None:
        selected = []
        for row in ranked:
            _, _, a, b = row
            eligible = [
                family for family in {items[a][1], items[b][1]}
                if family in groups and len(groups[family]) < limit
            ]
            if eligible:
                selected.append(row)
                for family in eligible:
                    groups[family].append((a, b))
            if all(len(group) == limit for group in groups.values()):
                break
        ranked = selected
    else:
        ranked = ranked[:limit]
    results = {
        (a, b): {
            "left": {"id": a, "name": items[a][0], "family": items[a][1]},
            "right": {"id": b, "name": items[b][0], "family": items[b][1]},
            "tickets_together": count,
            "left_tickets": frequencies[a],
            "right_tickets": frequencies[b],
            "support_pct": round(Decimal(count * 100) / total, 2),
            "confidence_left_to_right_pct": round(Decimal(count * 100) / frequencies[a], 2),
            "confidence_right_to_left_pct": round(Decimal(count * 100) / frequencies[b], 2),
            "lift": round(lift, 2),
        }
        for count, lift, a, b in ranked
    }
    return total, list(results.values()), {
        family: [results[key] for key in keys] for family, keys in groups.items()
    }


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
