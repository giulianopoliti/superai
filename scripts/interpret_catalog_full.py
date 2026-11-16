"""Full catalog pilot: propose, verify, persist auxiliary data and family notices."""

import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import select

from app.db.models import (
    CatalogInterpretationModel,
    CatalogVocabularyModel,
    ProductFamilyModel,
    ProductModel,
)
from app.db.repositories.catalog_interpretations import (
    ensure_vocabulary,
    find_interpretation,
    save_interpretation,
)
from app.db.repositories.catalog_taxonomy_changes import apply_taxonomy_changes
from app.db.repositories.catalog_vocabulary_activation import activate_vocabulary_node
from app.db.repositories.family_suggestions import lock_business
from app.db.session import create_session_factory
from app.modules.procurement.catalog_family_backfill import materialize_notice
from app.modules.procurement.catalog_full_interpretation import (
    FIELDS,
    VERSION,
    family_notice,
    full_options,
    full_questions,
    normalize_known_hierarchy,
    proposal_vocabulary,
    resolve_auxiliary_attributes,
    supported,
    validate_structure,
)
from app.modules.procurement.catalog_interpretation import (
    digest,
    interpretation_columns,
    snapshot,
    validate_answers,
)
from app.modules.procurement.catalog_taxonomy import entry_dict
from app.modules.procurement.normalization import normalize_text
from app.providers.catalog_decisions import OpenAICatalogDecisions
from app.providers.catalog_taxonomy_proposal import FullCatalogProposal, OpenAITaxonomyProposer
from app.settings import settings

ROOT = Path(__file__).resolve().parents[1]


def configuration_hash(model, threshold, families):
    prompt = (ROOT / "app/assistant/prompts/catalog_full_interpretation.md").read_text(
        encoding="utf-8"
    )
    verification = (ROOT / "app/assistant/prompts/catalog_full_verification.md").read_text(
        encoding="utf-8"
    )
    return digest(
        {
            "version": VERSION,
            "prompt": prompt,
            "verify_prompt": verification,
            "model": model,
            "threshold": threshold,
            "families": families,
        }
    )


def run_full(args):
    from scripts.interpret_catalog import export_row

    if not settings.database_url or not settings.openai_api_key:
        raise RuntimeError("DATABASE_URL y OPENAI_API_KEY son obligatorias")
    if not 0.5 < args.threshold <= 1:
        raise ValueError("El umbral debe superar 0,5")
    factory = getattr(args, "session_factory", None) or create_session_factory(
        settings.database_url
    )
    chooser = OpenAICatalogDecisions(
        settings.openai_api_key,
        model=args.model,
        prompt_path=ROOT / "app/assistant/prompts/catalog_full_verification.md",
    )
    proposer = OpenAITaxonomyProposer(settings.openai_api_key, model=args.model)
    reuse_path = getattr(args, "reuse_proposals", None)
    reusable = {}
    if reuse_path:
        previous = json.loads(Path(reuse_path).read_text(encoding="utf-8"))
        if previous["business_id"] != args.business_id:
            raise ValueError("Las propuestas pertenecen a otro negocio")
        reusable = {r["product_id"]: r for r in previous["results"]}
    prompt = (ROOT / "app/assistant/prompts/catalog_full_interpretation.md").read_text(
        encoding="utf-8"
    )
    with factory() as session:
        statement = (
            select(ProductModel)
            .where(ProductModel.business_id == args.business_id, ProductModel.active.is_(True))
            .order_by(ProductModel.name, ProductModel.id)
        )
        if args.product_id:
            statement = statement.where(ProductModel.id.in_(args.product_id))
        products = session.scalars(statement.limit(args.limit)).all()
        if not products or (args.product_id and len(products) != len(set(args.product_id))):
            raise ValueError("La selección debe pertenecer al negocio y estar activa")
        sources = [snapshot(p) for p in products]
        families = sorted(
            set(
                session.scalars(
                    select(ProductFamilyModel.name).where(
                        ProductFamilyModel.business_id == args.business_id,
                        ProductFamilyModel.active.is_(True),
                    )
                ).all()
            )
            | {s["category"] for s in sources if s.get("category")}
        )

    config_hash = configuration_hash(args.model, args.threshold, families)
    review_feedback = getattr(args, "review_feedback", {}) or {}
    if review_feedback:
        config_hash = digest({"base": config_hash, "review_feedback": review_feedback})
    report = {
        "run_id": str(uuid4()),
        "business_id": args.business_id,
        "version": VERSION,
        "model": args.model,
        "config_hash": config_hash,
        "threshold": args.threshold,
        "selected_products": len(sources),
        "api_calls": 0,
        "decisions_calls": 0,
        "generative_calls": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "estimated_cost_usd": 0,
        "estimated_cost_incomplete": False,
        "results": [],
        "errors": [],
        "audit_changes": [],
        "review_flags": {},
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)

    def count_call(raw, kind):
        attempts = raw.get("transport_attempts", 1)
        report["api_calls"] += attempts
        report[kind] += attempts
        usage = raw.get("usage") or {}
        report["input_tokens"] += usage.get("input_tokens", 0)
        report["output_tokens"] += usage.get("output_tokens", 0)
        details = usage.get("input_tokens_details") or {}
        cached = details.get("cached_tokens", 0)
        written = details.get("cache_write_tokens", 0)
        report["estimated_cost_usd"] += (
            max(0, usage.get("input_tokens", 0) - cached - written) * 0.10
            + cached * 0.01
            + written * 0.125
            + usage.get("output_tokens", 0) * 0.50
        ) / 1_000_000
        if attempts > 1 or not usage:
            report["estimated_cost_incomplete"] = True

    if getattr(args, "reorganize", False):
        with factory() as session:
            nodes = session.scalars(
                select(CatalogVocabularyModel).where(
                    CatalogVocabularyModel.business_id == args.business_id,
                    CatalogVocabularyModel.attribute.in_(["category", "subcategory"]),
                    CatalogVocabularyModel.status.in_(["active", "proposed"]),
                )
            ).all()
        raw_plan, plan = proposer.reorganize(
            args.taxonomy_goal, [entry_dict(n) for n in nodes], sources
        )
        count_call(raw_plan, "generative_calls")
        if plan.operations:
            with factory() as session:
                change = apply_taxonomy_changes(
                    session,
                    args.business_id,
                    [o.model_dump() for o in plan.operations],
                    {
                        "goal": args.taxonomy_goal,
                        "product_names": [s["original_name"] for s in sources],
                        "provider_response": raw_plan,
                    },
                )
                session.commit()
            report["maintenance"] = {"change_id": change.id, "response": raw_plan}

    for source in sources:
        try:
            with factory() as session:
                existing = find_interpretation(
                    session,
                    business_id=args.business_id,
                    product_id=source["product_id"],
                    source_hash=digest(source),
                    config_hash=config_hash,
                )
                if existing:
                    report["results"].append(export_row(existing, cached=True))
                    print(f"Reutilizado: {source['original_name']}", flush=True)
                    continue
                vocabulary = session.scalars(
                    select(CatalogVocabularyModel).where(
                        CatalogVocabularyModel.business_id == args.business_id,
                        CatalogVocabularyModel.status.in_(["active", "proposed"]),
                    )
                ).all()
            entries = [entry_dict(v) for v in vocabulary]
            shortlist = proposal_vocabulary(source, entries)
            previous = reusable.get(source["product_id"])
            if previous and previous["source"] == source:
                raw_proposal = previous["response"]["full_proposal"]
                parsed = next(
                    c["parsed"]
                    for o in raw_proposal["output"]
                    for c in o.get("content", [])
                    if c.get("parsed")
                )
                proposal = FullCatalogProposal.model_validate(parsed)
            else:
                proposal_source = (
                    {**source, "validation_feedback": review_feedback[source["product_id"]]}
                    if (source["product_id"] in review_feedback)
                    else source
                )
                raw_proposal, proposal = proposer.interpret(proposal_source, shortlist, families)
                count_call(raw_proposal, "generative_calls")
            invalid = [
                k
                for k in FIELDS
                if getattr(proposal, k).value and not supported(source, getattr(proposal, k))
            ]
            if invalid:
                # One repair attempt; never silently substitute the family as evidence.
                repair_source = {
                    **source,
                    "validation_feedback": {
                        "invalid_evidence_fields": invalid,
                        "instruction": "La evidencia debe provenir del nombre, no de la familia.",
                    },
                }
                repaired_raw, proposal = proposer.interpret(repair_source, shortlist, families)
                count_call(repaired_raw, "generative_calls")
                repaired_raw["previous_invalid_proposal"] = raw_proposal
                raw_proposal = repaired_raw
            normalized_proposal = normalize_known_hierarchy(proposal, entries)
            if normalized_proposal != proposal:
                raw_proposal["backend_normalization"] = normalized_proposal.model_dump()
            proposal = normalized_proposal
            if proposal.suggested_family and proposal.suggested_family not in families:
                raise ValueError("La familia propuesta no pertenece al negocio")
            options = full_options(source, proposal, entries, vocabulary, families)
            disputed = source["product_id"] in review_feedback
            if not disputed:
                options = {k: v for k, v in options.items() if k not in {"category", "subcategory"}}
            questions = full_questions(options)
            verification_input = {
                **source,
                "current_family": source.get("category"),
                "proposed_category": proposal.category.value,
                "proposed_attributes": proposal.model_dump(),
            }
            raw, response = chooser.decide(verification_input, questions)
            count_call(raw, "decisions_calls")
            attributes = validate_answers(
                response, options, threshold=args.threshold, source=source
            )
            notice = family_notice(source, proposal, attributes.pop("original_family"))
            notice["checked_at"] = datetime.now(UTC).isoformat()
            reviewed = (
                {k: dict(attributes[k]) for k in ("category", "subcategory")} if disputed else {}
            )
            resolve_auxiliary_attributes(source, proposal, attributes)
            for key, state in reviewed.items():
                if getattr(proposal, key).value:
                    attributes[key] = {
                        **state,
                        "proposed_value": getattr(proposal, key).value,
                        "reason": "ambiguity_review"
                        if state.get("value")
                        else "ambiguity_not_confirmed",
                    }
                    if not state.get("value"):
                        attributes[key]["status"] = "needs_review"
            validate_structure(attributes, proposal)
            staged_changes = []
            with factory() as session:
                if getattr(args, "compact_audit", False):
                    lock_business(session, args.business_id)
                known_nodes = (
                    session.scalars(
                        select(CatalogVocabularyModel).where(
                            CatalogVocabularyModel.business_id == args.business_id
                        )
                    ).all()
                    if getattr(args, "compact_audit", False)
                    else []
                )
                parent_id = None
                for attribute in FIELDS:
                    state = attributes[attribute]
                    if not state.get("value"):
                        continue
                    if attribute == "subcategory" and not parent_id:
                        state.update(status="conflict", value=None, reason="category_unresolved")
                        continue
                    matches = [
                        n
                        for n in known_nodes
                        if n.attribute == attribute
                        and normalize_text(state["value"])
                        in {n.normalized_label, *[normalize_text(a) for a in n.aliases]}
                    ]
                    if len(matches) > 1:
                        raise ValueError("Ambiguous auxiliary vocabulary")
                    node = (
                        matches[0]
                        if matches
                        else ensure_vocabulary(
                            session,
                            args.business_id,
                            attribute,
                            state["value"],
                            [] if attribute in {"category", "subcategory"} else [state["evidence"]],
                            status="proposed",
                            parent_id=parent_id if attribute == "subcategory" else None,
                            update_aliases=False,
                        )
                    )
                    if node.parent_id != (parent_id if attribute == "subcategory" else None):
                        raise ValueError(
                            "Existing label belongs to another parent; review required"
                        )
                    if node not in known_nodes:
                        known_nodes.append(node)
                    if node.status == "retired":
                        state.update(status="conflict", value=None, reason="retired_concept")
                        continue
                    if node.status == "proposed":
                        if attribute in {"category", "subcategory"}:
                            change = (
                                activate_vocabulary_node(
                                    session,
                                    args.business_id,
                                    node.id,
                                    {**source, "evidence": state["evidence"]},
                                )
                                if getattr(args, "compact_audit", False)
                                else apply_taxonomy_changes(
                                    session,
                                    args.business_id,
                                    [
                                        {
                                            "action": "activate",
                                            "target_id": node.id,
                                            "reason": "Propuesta validada con evidencia",
                                            "evidence": state["evidence"],
                                        }
                                    ],
                                    {
                                        "product_names": [source["original_name"]],
                                        "product_id": source["product_id"],
                                        "probability": state["probability"],
                                    },
                                )
                            )
                            state["taxonomy_change_id"] = change.id
                            staged_changes.append(
                                {
                                    "action": "activate",
                                    "label": node.label,
                                    "before_type": attribute,
                                    "after_type": attribute,
                                    "before_parent": parent_id,
                                    "after_parent": parent_id,
                                    "reason": "Interpretación completa validada",
                                    "change_id": change.id,
                                }
                            )
                        else:
                            node.status = "active"
                    state.update(vocabulary_id=node.id, value=node.label, parent_id=node.parent_id)
                    if attribute == "category":
                        parent_id = node.id
                validate_structure(attributes, proposal)
                row = CatalogInterpretationModel(
                    id=str(uuid4()),
                    run_id=report["run_id"],
                    business_id=args.business_id,
                    product_id=source["product_id"],
                    source_hash=digest(source),
                    config_hash=config_hash,
                    source=source,
                    attributes=attributes,
                    request={
                        "input": source,
                        "prompt": prompt,
                        "verification_prompt": chooser.prompt,
                        "questions": questions,
                        "vocabulary": shortlist,
                        "threshold": args.threshold,
                        "review_feedback": review_feedback.get(source["product_id"]),
                    },
                    response={
                        **raw,
                        "full_proposal": raw_proposal,
                        "original_family_validation": notice,
                    },
                    **interpretation_columns(attributes, source["original_name"]),
                )
                row = save_interpretation(session, row)
                try:
                    with session.begin_nested():
                        review = materialize_notice(session, args.business_id, row)
                    notice.update(review)
                except ValueError as exc:
                    notice["persistence_conflict"] = str(exc)
                row.response = {**row.response, "original_family_validation": notice}
                session.commit()
                report["audit_changes"].extend(staged_changes)
                report["results"].append(export_row(row, cached=False))
            print(
                f"{source['original_name']} -> {row.standard_name} [{notice['status']}]", flush=True
            )
        except Exception as exc:
            report["estimated_cost_incomplete"] = True
            report["errors"].append(
                {
                    "product_id": source["product_id"],
                    "error": type(exc).__name__,
                    "validation_message": str(exc)
                    if isinstance(exc, ValueError) and not isinstance(exc, ValidationError)
                    else None,
                    "validation_errors": exc.errors(include_input=False, include_context=False)
                    if isinstance(exc, ValidationError)
                    else [],
                }
            )
            print(f"Error: {source['original_name']} ({type(exc).__name__})", flush=True)
            break
        finally:
            args.output.write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
    for row in report["results"]:
        notice = row["response"].get("original_family_validation", {})
        if notice.get("requires_user_review"):
            report["review_flags"][row["product_id"]] = (
                f"Familia original: {notice.get('original_family')}. "
                f"Sugerida: {notice.get('suggested_family') or 'sin confirmar'}. "
                f"{notice.get('reason', '')} Requiere revisión del usuario."
            )
    report["persisted_or_reused"] = len(report["results"])
    report["summary"] = {
        "with_category": sum(bool(r["category"]) for r in report["results"]),
        "with_subcategory": sum(bool(r["subcategory"]) for r in report["results"]),
        "resolved_brand": sum(bool(r["brand"]) for r in report["results"]),
        "family_checks": dict(
            Counter(
                r["response"]["original_family_validation"]["status"] for r in report["results"]
            )
        ),
    }
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report
