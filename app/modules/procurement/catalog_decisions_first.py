"""Evidence candidates and closed decisions; no database or provider calls."""

import re
from dataclasses import asdict

from app.modules.procurement.catalog_interpretation import (
    Option,
    build_options,
    build_questions,
    digest,
    interpretation_columns,
    validate_answers,
)
from app.modules.procurement.normalization import normalize_text

VERSION = "catalog-decisions-first-v1"
PROMPT_HEADER = "# catalog_decisions_first v1"
TEXT_FIELDS = ("brand", "line", "variant", "packaging")


def fragments(name):
    """Literal spans, including alphanumeric names; no assumed semantic roles."""
    words = list(re.finditer(r"[^\W_]+(?:[.'’&-][^\W_]+)*", name, re.UNICODE))
    # Exclude size spans from *combinations*, never erase the original input.
    from app.modules.procurement.catalog_interpretation import SIZE_RE

    sizes = [m.span() for m in SIZE_RE.finditer(name)]
    spans = {}
    for width in range(1, len(words) + 1):
        for start in range(len(words) - width + 1):
            first, last = words[start], words[start + width - 1]
            a, b = first.start(), last.end()
            value = name[a:b]
            if len(value) > 120 or not any(c.isalpha() for c in value):
                continue
            if any(a < y and b > x for x, y in sizes):
                continue
            spans.setdefault(normalize_text(value), (value, a, b))
    # Short fragments and complete phrases get deterministic, interleaved priority.
    return sorted(spans.values(), key=lambda x: (len(x[0].split()), x[1], x[2]))


def prepare_options(source, entries, families, *, span_budget=900):
    """Budget is conservative UTF-8 bytes; every omitted span is audited."""
    active = [e for e in entries if e["status"] == "active"]
    # Only quantities reuse legacy extraction. Legacy role filters are not used.
    quantities = build_options(source, [])
    options = {
        k: quantities[k]
        for k in (
            "content",
            "package_quantity",
            "minimum_order_quantity",
        )
    }
    name = source["original_name"]
    normalized = " " + normalize_text(name) + " "
    audit = {"spans": [], "omitted_spans": [], "category_scope": "all_active"}
    chosen = []
    used = 0
    all_spans = fragments(name)
    # Reserve the full non-size phrase before shorter alternatives.
    ordered = sorted(all_spans, key=lambda x: (-len(x[0]), x[1]))[:1]
    ordered += [s for s in all_spans if s not in ordered]
    for value, a, b in ordered:
        cost = len(value.encode("utf-8")) + 28
        item = {"text": value, "start": a, "end": b, "origin": "literal_span"}
        if used + cost <= span_budget:
            chosen.append(value)
            used += cost
            audit["spans"].append(item)
        else:
            audit["omitted_spans"].append({**item, "reason": "span_byte_budget"})
    for field in TEXT_FIELDS:
        candidates = []
        for e in active:
            if e["attribute"] != field:
                continue
            hits = [
                a
                for a in [e["label"], *e["aliases"]]
                if " " + normalize_text(a) + " " in normalized
            ]
            if hits:
                candidates.append(Option(e["id"], e["label"], hits[0], "vocabulary", e["id"]))
        if field == "brand" and source.get("brand"):
            candidates.append(
                Option("source_brand", source["brand"], "brand=" + source["brand"], "source")
            )
        known = {normalize_text(c.label) for c in candidates}
        for value in chosen:
            if normalize_text(value) not in known:
                candidates.append(
                    Option("span_" + digest(value)[:16], value, value, "literal_span")
                )
        options[field] = candidates
    categories = taxonomy_options(source, active, "category")
    if len(categories) <= 252:
        options["category"] = categories
    else:
        for start in range(0, len(categories), 252):
            options[f"category_group_{start // 252}"] = categories[start : start + 252]
    options["original_family"] = [
        Option("family_" + digest(f)[:16], f, name, "source") for f in sorted(set(families))
    ]
    # More than one lexical size is deliberately offered; Decisions chooses per unit.
    return options, audit


def taxonomy_options(source, entries, attribute, parent_id=None):
    return [
        Option(e["id"], e["label"], source["original_name"], "vocabulary", e["id"])
        for e in entries
        if e["status"] == "active"
        and e["attribute"] == attribute
        and (attribute != "subcategory" or e["parent_id"] == parent_id)
    ]


def questions_for(options):
    normal = {
        k: v
        for k, v in options.items()
        if k != "original_family" and not k.startswith("category_group_")
    }
    questions = build_questions(normal)
    for field, candidates in options.items():
        if field.startswith("category_group_"):
            question = build_questions({"category": candidates})[0]
            question.update(
                name=field,
                instructions=(
                    "Elegí la categoría adecuada dentro de ESTE grupo. Si la categoría "
                    "corresponde a otro grupo o ninguna de estas opciones, none. "
                    "Si el nombre es insuficiente, unknown."
                ),
            )
            questions.append(question)
    for q in questions:
        if q["name"] == "subcategory":
            q["instructions"] = (
                "Elegí el subtipo dentro de selected_category. Si el subtipo no está "
                "expreso, unknown. Si está expreso pero falta en opciones, none."
            )
    if "original_family" in options:
        questions += [
            {
                "name": "original_family",
                "type": "choice",
                "instructions": (
                    "Elegí la familia existente adecuada; current_family puede estar mal."
                ),
                "choices": [
                    {"value": o.value, "description": o.label} for o in options["original_family"]
                ]
                + [
                    {"value": k, "description": v}
                    for k, v in {
                        "unknown": "Datos insuficientes",
                        "none": "Ninguna corresponde",
                        "not_applicable": "No aplica",
                    }.items()
                ],
            }
        ]
    return questions


def accept_answers(raw_response, options, source, threshold):
    states = validate_answers(raw_response, options, threshold=threshold, source=source)
    # Literal unknown brands remain per-product evidence, never global aliases.
    # Each was offered as a span; validate_answers applies probability and role conflicts.
    return states


def finish_attributes(states):
    category = states["category"]
    sub = states.setdefault("subcategory", {"status": "unknown", "value": None})
    if sub.get("value") and not category.get("value"):
        sub.update(status="conflict", value=None, reason="category_unresolved")
    for field in ("line", "variant"):
        value = states[field].get("value")
        if value and any(
            normalize_text(value) == normalize_text(states[k].get("value"))
            for k in ("brand", "category", "subcategory", "line")
            if k != field
        ):
            states[field].update(status="conflict", value=None, reason="duplicate_role")
    states["product_type"] = dict(sub if sub.get("value") else category)
    return interpretation_columns(states, "")


def options_audit(options):
    return {k: [asdict(o) for o in values] for k, values in options.items()}


def family_result(source, state):
    target = state.get("value") if state["status"] == "interpreted" else None
    changed = bool(target and target != source.get("category"))
    return {
        "status": "change_suggested" if changed else "compatible" if target else "needs_review",
        "original_family": source.get("category"),
        "suggested_family": target,
        "probability": state.get("probability"),
        "evidence": source["original_name"],
        "reason": "Familia evaluada entre opciones existentes por Decisions.",
        "requires_user_review": not target or changed,
        "original_family_modified": False,
    }
