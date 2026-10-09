"""Explicit, read-only pilot runner. API calls are made only when invoked by an operator."""

import argparse
import json
from collections import defaultdict
from hashlib import sha256
from pathlib import Path

from app.db.repositories.sql_procurement import SqlProcurementRepository
from app.db.session import create_session_factory
from app.modules.procurement.jev_pilot import (
    DualExtraction,
    Observation,
    PilotDataset,
    evaluate_shadow,
    extract_independent_pages,
    measure,
    reconcile,
)
from app.modules.procurement.matching import ProductMatchService
from app.modules.procurement.schemas import ExtractedSupplierOfferItem
from app.providers.documents.gemini import GeminiSupplierOfferDocumentProvider
from app.providers.documents.openai import OpenAISupplierOfferDocumentProvider
from app.providers.jev import JevHttpChooser
from app.settings import settings


def run() -> None:
    parser = argparse.ArgumentParser(description="Piloto de extracción y Jev sin escrituras en DB")
    subparsers = parser.add_subparsers(dest="mode", required=True)
    dual = subparsers.add_parser("dual", help="Gemini y Luna sobre las mismas páginas")
    dual.add_argument("--pdf", type=Path, required=True)
    dual.add_argument("--business-id", required=True)
    dual.add_argument("--supplier-id", required=True)
    dual.add_argument("--labels", type=Path)
    dual.add_argument("--output", type=Path, required=True)
    baseline = subparsers.add_parser("baseline", help="Medir ofertas legacy ya guardadas")
    baseline.add_argument("--business-id", required=True)
    baseline.add_argument("--document-id", required=True)
    baseline.add_argument("--pdf", type=Path, required=True)
    baseline.add_argument("--labels", type=Path, required=True)
    baseline.add_argument("--output", type=Path, required=True)
    shadow = subparsers.add_parser("shadow", help="Jev sobre ofertas ya guardadas")
    shadow.add_argument("--business-id", required=True)
    shadow.add_argument("--document-id", required=True)
    shadow.add_argument("--model", required=True, help="Versión fija, no alias latest")
    shadow.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    if args.mode == "dual":
        parser.error("La extracción dual con Gemini está suspendida. Usá baseline o shadow.")
        dataset = None
        if args.labels:
            dataset = PilotDataset.model_validate_json(args.labels.read_text(encoding="utf-8"))
            if dataset.business_id != args.business_id or dataset.supplier_id != args.supplier_id:
                parser.error("Las etiquetas corresponden a otro negocio o proveedor")
        if not settings.gemini_api_key or not settings.openai_api_key:
            parser.error("GEMINI_API_KEY y OPENAI_API_KEY son obligatorias")
        providers = {
            "gemini": GeminiSupplierOfferDocumentProvider(
                api_key=settings.gemini_api_key,
                model=settings.gemini_document_model or settings.gemini_model,
                timeout_seconds=settings.llm_timeout_seconds,
                allow_direct_fallback=False,
            ),
            "luna": OpenAISupplierOfferDocumentProvider(
                api_key=settings.openai_api_key,
                model=settings.openai_model,
                timeout_seconds=settings.llm_timeout_seconds,
            ),
        }
        result = extract_independent_pages(
            business_id=args.business_id,
            document_path=args.pdf,
            providers=providers,
        )
        report = {"extraction": result.model_dump(mode="json")}
        if dataset:
            report["metrics"] = {
                name: measure(dataset, result, provider=name).model_dump(mode="json")
                for name in providers
                if any(row.provider == name for row in result.observations)
            }
            report["metrics"]["agreement"] = measure(dataset, result).model_dump(mode="json")
    elif args.mode == "baseline":
        if not settings.database_url:
            parser.error("DATABASE_URL es obligatoria")
        dataset = PilotDataset.model_validate_json(args.labels.read_text(encoding="utf-8"))
        digest = sha256(args.pdf.read_bytes()).hexdigest()
        if dataset.business_id != args.business_id or dataset.document_sha256 != digest:
            parser.error("El PDF o negocio no coincide con las etiquetas")
        repository = SqlProcurementRepository(create_session_factory(settings.database_url))
        items = repository.list_supplier_offer_items(
            business_id=args.business_id,
            supplier_offer_document_id=args.document_id,
        )
        observations = [
            Observation(
                provider="legacy",
                page_number=item.page_number or 0,
                ordinal=index,
                item=ExtractedSupplierOfferItem(
                    raw_name=item.raw_name,
                    brand=item.brand,
                    unit_size=item.unit_size,
                    unit=item.unit,
                    package_quantity=item.package_quantity,
                    offer_price=item.offer_price,
                    currency=item.currency,
                    tax_included=item.tax_included,
                    price_type=item.price_type,
                    page_number=item.page_number,
                ),
            )
            for index, item in enumerate(items)
        ]
        extraction = DualExtraction(
            business_id=args.business_id,
            document_sha256=digest,
            observations=observations,
            offers=reconcile(observations),
        )
        report = {
            "baseline": extraction.model_dump(mode="json"),
            "metrics": measure(dataset, extraction, provider="legacy").model_dump(mode="json"),
        }
    else:
        if not settings.database_url or not (api_key := settings.typesafe_api_key):
            parser.error("DATABASE_URL y TYPESAFE_AI_API_KEY (o JEV_API_KEY) son obligatorias")
        repository = SqlProcurementRepository(create_session_factory(settings.database_url))
        report = ProductMatchService(repository).retrieve_pilot_candidates(
            business_id=args.business_id,
            supplier_offer_document_id=args.document_id,
        )
        grouped = defaultdict(list)
        for candidate in report.candidates:
            grouped[candidate.supplier_offer_item.id].append(candidate)
        chooser = JevHttpChooser(api_key=api_key, model=args.model)
        decisions = [
            evaluate_shadow(item=items[0].supplier_offer_item, candidates=items, chooser=chooser)
            for items in grouped.values()
        ]
        report = {
            "business_id": args.business_id,
            "document_id": args.document_id,
            "decisions": [decision.model_dump(mode="json") for decision in decisions],
        }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as output:
        json.dump(report, output, ensure_ascii=False, indent=2)
    print(f"Reporte guardado: {args.output}")


if __name__ == "__main__":
    run()
