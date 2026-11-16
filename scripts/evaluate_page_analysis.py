"""Bounded local evaluation. Writes local evidence only; never changes business data."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.modules.procurement.matching import ProductMatchService
from app.modules.procurement.page_analysis import PageOfferAnalysisService
from app.modules.procurement.page_analysis_schemas import PageAnalysisCheckpoint
from app.modules.procurement.schemas import Product
from app.providers.documents.openai import OpenAISupplierOfferDocumentProvider
from app.settings import settings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--catalog", type=Path)
    parser.add_argument("--business-id", default="demo-business")
    parser.add_argument("--supplier", default="Vital")
    parser.add_argument("--max-calls", type=int, default=80)
    parser.add_argument("--effort", choices=["low", "none"], default="low")
    parser.add_argument(
        "--resolve-only",
        action="store_true",
        help="Resolve a saved checkpoint locally without sending requests.",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Make paid calls, capped at 12000 output tokens each.",
    )
    args = parser.parse_args()
    if not args.execute and not args.resolve_only:
        parser.error("Evaluation requires --execute; no requests were sent.")
    if not 1 <= args.max_calls <= 100:
        parser.error("max-calls must be between 1 and 100")
    products = (
        [Product.model_validate(p) for p in json.loads(args.catalog.read_text(encoding="utf-8"))]
        if args.catalog
        else []
    )
    # Match the production pipeline, which uses original catalog fields.
    products = [product.model_copy(update={"auxiliary": None}) for product in products]

    class Catalog:
        def list_products(self, **_):
            return [p for p in products if p.business_id == args.business_id]

        def list_product_match_feedback_rules(self, **_):
            return []

    def retrieve(**kwargs):
        kwargs.pop("supplier_name")
        return ProductMatchService(Catalog()).retrieve_analysis_candidates(
            supplier_id="evaluation",
            **kwargs,
        )

    args.output.mkdir(parents=True, exist_ok=True)
    checkpoint_path = args.output / "checkpoint.json"

    def save(state):
        temporary = checkpoint_path.with_suffix(".tmp")
        temporary.write_text(state.model_dump_json(), encoding="utf-8")
        temporary.replace(checkpoint_path)
        print(
            json.dumps(
                {
                    "done": sum(
                        w.status in {"succeeded", "failed", "uncertain"} for w in state.work
                    ),
                    "units": len(state.work),
                    "pages": state.page_count,
                }
            ),
            flush=True,
        )

    service = PageOfferAnalysisService(
        reader=OpenAISupplierOfferDocumentProvider(
            api_key=settings.openai_api_key,
            model=settings.openai_document_model,
            timeout_seconds=60,
            single_page=True,
            reasoning_effort=args.effort,
            max_output_tokens=12000,
        ),
        candidate_retriever=retrieve,
        max_calls=args.max_calls,
    )
    if args.resolve_only:
        state = PageAnalysisCheckpoint.model_validate_json(
            checkpoint_path.read_text(encoding="utf-8")
        )
        if state.business_id != args.business_id:
            parser.error("Checkpoint business does not match")
        result = service._resolve(state, args.supplier, None)
        (args.output / "resolved.json").write_text(
            result.model_dump_json(indent=2), encoding="utf-8"
        )
        print(json.dumps(result.document_metadata["analysis"], ensure_ascii=False), flush=True)
        return
    result = service.analyze(
        business_id=args.business_id,
        document_path=args.pdf,
        method="openai_structured",
        supplier_hint=args.supplier,
        checkpoint=json.loads(checkpoint_path.read_text(encoding="utf-8"))
        if checkpoint_path.exists()
        else None,
        save_checkpoint=save,
    )
    (args.output / "result.json").write_text(result.model_dump_json(indent=2), encoding="utf-8")
    print(json.dumps(result.document_metadata["analysis"], ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
