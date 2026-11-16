"""Catalog pilot: Decisions first, structured proposals only for taxonomy gaps."""
# ruff: noqa: E402 -- direct script execution needs the repository on sys.path.

import argparse
import json
import sys
from pathlib import Path
from time import monotonic
from uuid import uuid4

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import select

from app.db.models import CatalogInterpretationModel, CatalogVocabularyModel, ProductModel
from app.db.repositories.catalog_interpretations import (
    ensure_vocabulary,
    find_interpretation,
    save_interpretation,
)
from app.db.repositories.catalog_taxonomy_changes import apply_taxonomy_changes
from app.db.session import create_session_factory
from app.modules.procurement.catalog_interpretation import (
    VERSION,
    build_options,
    build_questions,
    digest,
    interpretation_columns,
    snapshot,
    validate_answers,
)
from app.modules.procurement.catalog_taxonomy import (
    entry_dict,
    resolve_taxonomy,
    shortlist,
    taxonomy_options,
    with_reviewed_matches,
)
from app.providers.catalog_decisions import OpenAICatalogDecisions
from app.providers.catalog_taxonomy_proposal import OpenAITaxonomyProposer
from app.settings import settings


def export_row(row, *, cached: bool) -> dict:
    fields = (
        "id",
        "run_id",
        "business_id",
        "product_id",
        "source",
        "standard_name",
        "status",
        "product_type",
        "category",
        "subcategory",
        "brand",
        "line",
        "variant",
        "packaging",
        "content_unit",
        "package_quantity",
        "minimum_order_quantity",
        "attributes",
        "response",
    )
    return {
        **{field: getattr(row, field) for field in fields},
        "content_value": str(row.content_value) if row.content_value is not None else None,
        "cached": cached,
    }


def run(args) -> dict:
    if not getattr(args, "legacy_decisions_first", False):
        from scripts.interpret_catalog_full import run_full

        return run_full(args)
    if not settings.database_url or not settings.openai_api_key:
        raise RuntimeError("DATABASE_URL y OPENAI_API_KEY son obligatorias")
    factory = create_session_factory(settings.database_url)
    chooser = OpenAICatalogDecisions(settings.openai_api_key, model=args.model)
    proposer = OpenAITaxonomyProposer(settings.openai_api_key)
    seed_path = args.vocabulary or (
        PROJECT_ROOT / "app/assistant/prompts/catalog_vocabulary_seed.json"
    )
    seed = json.loads(seed_path.read_text(encoding="utf-8"))
    taxonomy_seed = json.loads(
        (PROJECT_ROOT / "app/assistant/prompts/catalog_taxonomy_seed.json").read_text(
            encoding="utf-8"
        )
    )
    maintenance = None
    with factory() as session:
        statement = (
            select(ProductModel)
            .where(
                ProductModel.business_id == args.business_id,
                ProductModel.active.is_(True),
            )
            .order_by(ProductModel.name, ProductModel.id)
        )
        if args.product_id:
            statement = statement.where(ProductModel.id.in_(args.product_id))
        products = session.scalars(statement.limit(args.limit)).all()
        if not products:
            raise ValueError("No hay productos para ese negocio y selección")
        if args.product_id and len(products) != len(set(args.product_id)):
            raise ValueError("Algún ID no pertenece al negocio, está inactivo o supera el límite")
        sources = [snapshot(product) for product in products]
        for attribute, entries in seed.items():
            if attribute not in {"product_type", "brand", "line", "variant", "packaging"}:
                raise ValueError("Unsupported seed attribute")
            for entry in entries:
                ensure_vocabulary(
                    session,
                    args.business_id,
                    attribute,
                    entry["label"],
                    entry["aliases"],
                    status="active",
                    update_aliases=False,
                )
        for entry in taxonomy_seed:
            parent = ensure_vocabulary(
                session,
                args.business_id,
                "category",
                entry["label"],
                entry["aliases"],
                status="active",
                update_aliases=False,
            )
            for retired in entry.get("retired_children", []):
                old = session.scalar(
                    select(CatalogVocabularyModel).where(
                        CatalogVocabularyModel.business_id == args.business_id,
                        CatalogVocabularyModel.attribute == "subcategory",
                        CatalogVocabularyModel.parent_id == parent.id,
                        CatalogVocabularyModel.label == retired,
                    )
                )
                if old is not None:
                    old.status = "retired"
            for child in entry["children"]:
                ensure_vocabulary(
                    session,
                    args.business_id,
                    "subcategory",
                    child["label"],
                    child["aliases"],
                    status="active",
                    parent_id=parent.id,
                    update_aliases=False,
                )
        session.commit()
        vocabulary = session.scalars(
            select(CatalogVocabularyModel)
            .where(
                CatalogVocabularyModel.business_id == args.business_id,
                (CatalogVocabularyModel.status == "active")
                | (
                    CatalogVocabularyModel.attribute.in_(["category", "subcategory"])
                    & (CatalogVocabularyModel.status == "proposed")
                ),
            )
            .order_by(CatalogVocabularyModel.attribute, CatalogVocabularyModel.id)
        ).all()

    if getattr(args, "reorganize", False):
        entries = [entry_dict(v) for v in vocabulary if v.attribute in {"category", "subcategory"}]
        raw_plan, plan = proposer.reorganize(args.taxonomy_goal, entries, sources)
        maintenance = {"response": raw_plan, "change_id": None}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.with_suffix(".maintenance.json").write_text(
            json.dumps(
                {"goal": args.taxonomy_goal, "plan": plan.model_dump(), "response": raw_plan},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
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
                maintenance["change_id"] = change.id
        with factory() as session:
            vocabulary = session.scalars(
                select(CatalogVocabularyModel)
                .where(
                    CatalogVocabularyModel.business_id == args.business_id,
                    (CatalogVocabularyModel.status == "active")
                    | (
                        CatalogVocabularyModel.attribute.in_(["category", "subcategory"])
                        & (CatalogVocabularyModel.status == "proposed")
                    ),
                )
                .order_by(CatalogVocabularyModel.attribute, CatalogVocabularyModel.id)
            ).all()

    config_hash = digest(
        {
            "version": VERSION,
            "model": args.model,
            "prompt": chooser.prompt,
            "proposal_model": proposer.model,
            "proposal_prompt": proposer.prompt,
            "taxonomy_seed": taxonomy_seed,
            "threshold": args.threshold,
            "vocabulary": [entry_dict(v) for v in vocabulary],
        }
    )
    report = {
        "run_id": str(uuid4()),
        "business_id": args.business_id,
        "version": VERSION,
        "model": args.model,
        "config_hash": config_hash,
        "threshold": args.threshold,
        "selected_products": len(sources),
        "api_calls": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "decisions_calls": 0,
        "generative_calls": 0,
        "estimated_cost_usd": 0,
        "estimated_cost_incomplete": False,
        "results": [],
        "errors": [],
        "maintenance": maintenance,
    }
    if maintenance:
        usage = maintenance["response"].get("usage", {}) or {}
        report["api_calls"] = report["generative_calls"] = 1
        report["input_tokens"] = usage.get("input_tokens", 0)
        report["output_tokens"] = usage.get("output_tokens", 0)
        report["estimated_cost_usd"] = (
            report["input_tokens"] * 0.10 + report["output_tokens"] * 0.50
        ) / 1_000_000
    args.output.parent.mkdir(parents=True, exist_ok=True)
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
            options = build_options(source, vocabulary)
            options.pop("product_type")
            categories = [entry_dict(v) for v in vocabulary if v.attribute == "category"]
            options["category"] = taxonomy_options(source, shortlist(source, categories))
            questions = build_questions(options)
            started = monotonic()
            report["api_calls"] += 1
            report["decisions_calls"] += 1
            raw, response = chooser.decide(source, questions)
            attempts = raw.get("transport_attempts", 1)
            report["api_calls"] += attempts - 1
            report["decisions_calls"] += attempts - 1
            if attempts > 1:
                report["estimated_cost_incomplete"] = True
            report["input_tokens"] += response.usage.input_tokens
            attributes = validate_answers(
                response, options, threshold=args.threshold, source=source
            )
            with factory() as session:
                taxonomy = with_reviewed_matches(
                    [
                        entry_dict(v)
                        for v in session.scalars(
                            select(CatalogVocabularyModel).where(
                                CatalogVocabularyModel.business_id == args.business_id,
                                CatalogVocabularyModel.attribute.in_(["category", "subcategory"]),
                                CatalogVocabularyModel.status.in_(["active", "proposed"]),
                            )
                        ).all()
                    ],
                    taxonomy_seed,
                )
            stages = []
            attributes["category"], extra = resolve_taxonomy(
                source,
                "category",
                taxonomy,
                parent_id=None,
                chooser=chooser,
                proposer=proposer,
                threshold=args.threshold,
                initial_state=attributes["category"],
            )
            stages.extend(extra)
            parent_id = attributes["category"].get("vocabulary_id")
            if attributes["category"].get("value") and parent_id:
                attributes["subcategory"], extra = resolve_taxonomy(
                    source,
                    "subcategory",
                    taxonomy,
                    parent_id=parent_id,
                    chooser=chooser,
                    proposer=proposer,
                    threshold=args.threshold,
                )
                stages.extend(extra)
            else:
                attributes["subcategory"] = {
                    "status": "unknown",
                    "value": None,
                    "reason": "category_unresolved",
                }
            attributes["product_type"] = dict(
                attributes["subcategory"]
                if attributes["subcategory"].get("value")
                else attributes["category"]
            )
            for stage in stages:
                attempts = stage.get("response", {}).get("transport_attempts", 1)
                report["api_calls"] += attempts
                counter = (
                    "decisions_calls" if stage["provider"] == "decisions" else "generative_calls"
                )
                report[counter] += attempts
                if attempts > 1:
                    report["estimated_cost_incomplete"] = True
                usage = stage.get("response", {}).get("usage", {}) or {}
                if not usage:
                    report["estimated_cost_incomplete"] = True
                report["input_tokens"] += usage.get("input_tokens", 0)
                report["output_tokens"] += usage.get("output_tokens", 0)
            report["estimated_cost_usd"] = (
                report["input_tokens"] * 0.10 + report["output_tokens"] * 0.50
            ) / 1_000_000
            with factory() as session:
                for attribute, value in attributes.items():
                    if value["status"] == "proposed_new" and attribute != "product_type":
                        proposed = ensure_vocabulary(
                            session,
                            args.business_id,
                            attribute,
                            value["proposed_value"],
                            value.get("aliases", [value["evidence"]]),
                            status="proposed",
                            parent_id=value.get("parent_id"),
                        )
                        value["vocabulary_id"] = proposed.id
                        if attribute in {"category", "subcategory"}:
                            change = apply_taxonomy_changes(
                                session,
                                args.business_id,
                                [
                                    {
                                        "action": "activate",
                                        "target_id": proposed.id,
                                        "reason": "Interpretación auxiliar validada",
                                        "evidence": value["evidence"],
                                    }
                                ],
                                {"product_names": [source["original_name"]], "goal": ""},
                            )
                            value.update(
                                status="interpreted",
                                value=proposed.label,
                                taxonomy_change_id=change.id,
                            )
                attributes["product_type"] = dict(
                    attributes["subcategory"]
                    if attributes["subcategory"].get("value")
                    else attributes["category"]
                )
                row = CatalogInterpretationModel(
                    id=str(uuid4()),
                    run_id=report["run_id"],
                    business_id=args.business_id,
                    product_id=source["product_id"],
                    source_hash=digest(source),
                    config_hash=config_hash,
                    source=source,
                    attributes=attributes,
                    request={"input": source, "questions": questions, "prompt": chooser.prompt},
                    response={**raw, "taxonomy_stages": stages},
                    **interpretation_columns(attributes, source["original_name"]),
                )
                row = save_interpretation(session, row)
                session.commit()
                exported = export_row(row, cached=False)
            exported["duration_seconds"] = round(monotonic() - started, 3)
            report["results"].append(exported)
            print(f"{source['original_name']} -> {row.standard_name} [{row.status}]", flush=True)
        except Exception as exc:
            report["estimated_cost_incomplete"] = True
            # Error types are sufficient here; avoid leaking DB URLs or credentials.
            message = (
                str(exc) if isinstance(exc, (ValueError, RuntimeError)) else type(exc).__name__
            )
            report["errors"].append({"product_id": source["product_id"], "error": message})
            print(f"Error en {source['original_name']}: {message}", flush=True)
            break
        finally:
            args.output.write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
    report["persisted_or_reused"] = len(report["results"])
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(
        description="Interpretar una muestra del catálogo con Decisions"
    )
    parser.add_argument("--business-id", required=True)
    parser.add_argument("--limit", type=int, default=12)
    parser.add_argument("--product-id", action="append")
    parser.add_argument("--model", default="gpt-6-luna", choices=["gpt-6-luna"])
    parser.add_argument("--threshold", type=float, default=0.90)
    parser.add_argument("--legacy-decisions-first", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument(
        "--reuse-proposals", type=Path, help="Revalidar fichas existentes sin extraerlas otra vez"
    )
    parser.add_argument("--vocabulary", type=Path)
    parser.add_argument(
        "--reorganize",
        action="store_true",
        help="Luna reorganiza el vocabulario auxiliar antes de clasificar",
    )
    parser.add_argument(
        "--taxonomy-goal", default="Reutilizar conceptos, evitar duplicados y mantener dos niveles."
    )
    parser.add_argument("--output", type=Path, default=Path("output/catalog/pilot.json"))
    args = parser.parse_args()
    if not 1 <= args.limit <= 100 or not 0 <= args.threshold <= 1:
        parser.error("limit debe estar entre 1 y 100; threshold entre 0 y 1")
    report = run(args)
    print(json.dumps({k: v for k, v in report.items() if k not in {"results", "config_hash"}}))
    if report["errors"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
