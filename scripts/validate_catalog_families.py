"""Persist original-family warnings for DB products in an existing pilot sample."""
# ruff: noqa: E402

import argparse
import json
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import select

from app.db.models import CatalogInterpretationModel, ProductModel
from app.db.session import SessionLocal
from app.modules.procurement.catalog_family_validation import (
    ANIMAL_FOOD_DEFINITION,
    STATUS_LABELS,
    family_question,
    validate_family_answer,
)
from app.modules.procurement.catalog_interpretation import digest, snapshot
from app.providers.catalog_decisions import OpenAICatalogDecisions
from app.settings import settings


def run(args):
    if SessionLocal is None or not settings.openai_api_key:
        raise RuntimeError("DATABASE_URL y OPENAI_API_KEY son obligatorias")
    report = json.loads(args.sample.read_text(encoding="utf-8"))
    selected = [r for r in report["results"] if r["source"].get("category") == args.family]
    if not selected or any(r["business_id"] != args.business_id for r in selected):
        raise ValueError("La selección debe pertenecer al negocio solicitado")
    prompt = ROOT / "app/assistant/prompts/catalog_original_family_decisions.md"
    chooser = OpenAICatalogDecisions(settings.openai_api_key, model=args.model, prompt_path=prompt)
    config_hash = digest(
        {
            "prompt": chooser.prompt,
            "model": args.model,
            "definition": args.definition,
            "threshold": args.threshold,
        }
    )
    results = []
    with SessionLocal() as session:

        def catalog_hash():
            rows = (
                session.execute(
                    select(ProductModel.__table__)
                    .where(ProductModel.business_id == args.business_id)
                    .order_by(ProductModel.id)
                )
                .mappings()
                .all()
            )
            return digest([{k: str(v) for k, v in row.items()} for row in rows])

        before = catalog_hash()
        for exported in selected:
            product = session.scalar(
                select(ProductModel).where(
                    ProductModel.business_id == args.business_id,
                    ProductModel.id == exported["product_id"],
                    ProductModel.active.is_(True),
                )
            )
            interpretation = session.scalar(
                select(CatalogInterpretationModel).where(
                    CatalogInterpretationModel.business_id == args.business_id,
                    CatalogInterpretationModel.id == exported["id"],
                    CatalogInterpretationModel.product_id == exported["product_id"],
                )
            )
            if product is None or interpretation is None:
                raise ValueError("Producto o interpretación no pertenece al negocio/selección")
            source = snapshot(product)
            if source != exported["source"] or source != interpretation.source:
                raise ValueError("El producto cambió: actualizar primero su interpretación")
            cached = (interpretation.response or {}).get("original_family_validation", {})
            source_hash = digest(source)
            if (
                cached.get("config_hash") == config_hash
                and cached.get("source_hash") == source_hash
            ):
                validation = cached
            else:
                independent_source = {k: v for k, v in source.items() if k != "category"}
                independent_source.update(
                    family_to_validate=product.category, family_definition=args.definition
                )
                raw, response = chooser.decide(independent_source, [family_question()])
                validation = {
                    **validate_family_answer(response, threshold=args.threshold),
                    "family": product.category,
                    "source_hash": source_hash,
                    "config_hash": config_hash,
                    "source": independent_source,
                    "model": response.model,
                    "checked_at": datetime.now(UTC).isoformat(),
                    "raw_response": raw,
                }
                interpretation.response = {
                    **(interpretation.response or {}),
                    "original_family_validation": validation,
                }
                session.commit()
            exported["response"]["original_family_validation"] = validation
            result = {
                "product_id": product.id,
                "original_name": product.name,
                "original_family": product.category,
                **validation,
            }
            results.append(result)
            print(f"{product.name}: {STATUS_LABELS[validation['status']]}", flush=True)
        if before != catalog_hash():
            raise RuntimeError("Los datos originales del catálogo cambiaron durante la validación")
    summary = {
        "business_id": args.business_id,
        "results": results,
        "counts": dict(Counter(r["status"] for r in results)),
        "original_catalog_unchanged": True,
    }
    args.output.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    report["original_family_validation"] = summary
    args.sample.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# Validación de familia original",
        "",
        "Avisos para revisar el sistema de stock. No se modificó ninguna familia original.",
        "La categoría auxiliar no se utilizó para tomar esta decisión.",
        "",
        "| Producto | Familia original | Validación |",
        "|---|---|---|",
    ]
    for row in results:
        lines.append(
            f"| {row['original_name']} | {row['original_family']} | "
            f"{STATUS_LABELS[row['status']]} |"
        )
    args.output.with_suffix(".md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(summary["counts"], ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Validar familia original en la muestra de DB")
    parser.add_argument("--business-id", required=True)
    parser.add_argument("--sample", type=Path, required=True)
    parser.add_argument("--family", default="ALIMENTO PARA ANIMALES")
    parser.add_argument("--definition")
    parser.add_argument("--threshold", type=float, default=0.90)
    parser.add_argument("--model", default="gpt-6-luna")
    parser.add_argument(
        "--output", type=Path, default=Path("output/catalog/original-family-validation.json")
    )
    args = parser.parse_args()
    if not args.definition:
        if args.family != "ALIMENTO PARA ANIMALES":
            parser.error("Otra familia requiere --definition para definir su alcance")
        args.definition = ANIMAL_FOOD_DEFINITION
    run(args)
