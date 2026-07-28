import argparse
import csv
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from app.db.repositories.sql_procurement import SqlProcurementRepository
from app.db.session import SessionLocal
from app.main import build_engine
from app.modules.procurement.catalog_import import PosCatalogImportService
from app.modules.procurement.matching import ProductMatchService
from app.modules.procurement.schemas import ProductMatchFeedback, SupplierProductRelationship
from app.modules.procurement.supplier_offers import SupplierOfferService
from app.providers.documents.local_text import LocalTextSupplierOfferProvider
from app.schemas.assistant import AssistantRequest, Channel, MessageType


def handle_message(args: argparse.Namespace) -> None:
    text = args.text
    if isinstance(text, list):
        text = " ".join(text)

    if not text:
        raise SystemExit("Message text is required.")

    business_id = args.business_id
    external_user_id = args.external_user_id

    request = AssistantRequest(
        channel=Channel.CLI,
        external_user_id=external_user_id,
        business_id=business_id,
        message_type=MessageType.TEXT,
        text=text,
        timestamp=datetime.now(UTC),
        raw_payload={"source": "cli"},
    )
    response = build_engine().handle_message(request)
    print(response.reply)


def handle_import_catalog(args: argparse.Namespace) -> None:
    if SessionLocal is None:
        raise SystemExit("DATABASE_URL is not configured.")

    repository = SqlProcurementRepository(SessionLocal)
    service = PosCatalogImportService(repository)
    result = service.import_csv(
        business_id=args.business_id,
        csv_path=Path(args.csv_path),
    )
    print(json.dumps(result.model_dump(mode="json"), indent=2, ensure_ascii=False))


def handle_import_supplier_offer(args: argparse.Namespace) -> None:
    if SessionLocal is None:
        raise SystemExit("DATABASE_URL is not configured.")

    with Path(args.json_path).open("r", encoding="utf-8") as file:
        payload = json.load(file)

    service = SupplierOfferService(SqlProcurementRepository(SessionLocal))
    result = service.create_manual_offer(
        business_id=args.business_id,
        supplier_name=payload["supplier_name"],
        source_filename=payload.get("source_filename") or Path(args.json_path).name,
        raw_text=payload.get("raw_text"),
        items=payload["items"],
    )
    print(json.dumps(result.model_dump(mode="json"), indent=2, ensure_ascii=False))


def handle_extract_supplier_offer_text(args: argparse.Namespace) -> None:
    provider = LocalTextSupplierOfferProvider()
    extraction = provider.extract_supplier_offer(
        document_path=Path(args.text_path),
        supplier_hint=args.supplier_name,
    )

    if args.persist:
        if SessionLocal is None:
            raise SystemExit("DATABASE_URL is not configured.")
        if not args.supplier_name:
            raise SystemExit("--supplier-name is required when --persist is used.")

        service = SupplierOfferService(SqlProcurementRepository(SessionLocal))
        result = service.create_offer_from_extraction(
            business_id=args.business_id,
            supplier_name=args.supplier_name,
            extraction=extraction,
        )
        print(json.dumps(result.model_dump(mode="json"), indent=2, ensure_ascii=False))
        return

    print(json.dumps(extraction.model_dump(mode="json"), indent=2, ensure_ascii=False))


def handle_compare_supplier_offer(args: argparse.Namespace) -> None:
    if SessionLocal is None:
        raise SystemExit("DATABASE_URL is not configured.")

    service = ProductMatchService(SqlProcurementRepository(SessionLocal))
    report = service.compare_supplier_offer(
        business_id=args.business_id,
        supplier_offer_document_id=args.supplier_offer_document_id,
        max_candidates_per_item=args.max_candidates,
    )
    persisted_count = 0
    if args.persist_candidates:
        records = service.save_supplier_offer_candidates(report=report)
        persisted_count = len(records)
    if args.format == "json":
        print(json.dumps(report.model_dump(mode="json"), indent=2, ensure_ascii=False))
        return
    if args.format == "csv":
        writer = csv.writer(sys.stdout)
        writer.writerow(
            [
                "supplier_item",
                "supplier_price",
                "matched_product",
                "current_cost",
                "sale_price",
                "saving",
                "saving_percentage",
                "estimated_margin_percentage",
                "relationship_type",
                "confidence_score",
                "recommendation",
            ]
        )
        for candidate in report.candidates:
            product = candidate.product
            writer.writerow(
                [
                    candidate.supplier_offer_item.raw_name,
                    candidate.supplier_offer_item.offer_price,
                    product.name if product else "",
                    product.current_cost if product else "",
                    product.sale_price if product else "",
                    candidate.cost_difference or "",
                    candidate.cost_difference_percentage or "",
                    candidate.estimated_margin_percentage or "",
                    candidate.relationship_type,
                    candidate.confidence_score,
                    candidate.recommendation,
                ]
            )
        return

    print(
        f"items={len(report.candidates)} matched={report.matched_count} "
        f"buy={sum(1 for c in report.candidates if c.recommendation == 'buy')} "
        f"review={sum(1 for c in report.candidates if c.recommendation == 'review')} "
        f"do_not_buy={sum(1 for c in report.candidates if c.recommendation == 'do_not_buy')} "
        f"persisted={persisted_count}"
    )
    for recommendation in ("buy", "do_not_buy", "review"):
        print(f"\n{recommendation.upper()}")
        for candidate in [c for c in report.candidates if c.recommendation == recommendation]:
            product = candidate.product
            match_name = product.name if product else "NO MATCH"
            print(
                f"- {candidate.supplier_offer_item.raw_name} @ "
                f"{candidate.supplier_offer_item.offer_price} -> {match_name} | "
                f"cost={product.current_cost if product else ''} | "
                f"sale={product.sale_price if product else ''} | "
                f"saving={candidate.cost_difference or ''} | "
                f"margin={candidate.estimated_margin_percentage or ''}% | "
                f"conf={candidate.confidence_score} | rel={candidate.relationship_type}"
            )


def handle_review_product_match(args: argparse.Namespace) -> None:
    if SessionLocal is None:
        raise SystemExit("DATABASE_URL is not configured.")

    repository = SqlProcurementRepository(SessionLocal)
    candidates = repository.list_product_match_candidates(
        business_id=args.business_id,
        supplier_offer_document_id=args.supplier_offer_document_id,
    )
    candidate = next(
        (
            record
            for record in candidates
            if record.id == args.product_match_candidate_id
        ),
        None,
    )
    if candidate is None:
        raise SystemExit("Product match candidate not found for this business/document.")

    relationship = (
        SupplierProductRelationship(args.relationship_type)
        if args.relationship_type
        else candidate.relationship_type
    )
    feedback = repository.add_product_match_feedback(
        ProductMatchFeedback(
            business_id=args.business_id,
            product_match_candidate_id=candidate.id,
            supplier_offer_item_id=candidate.supplier_offer_item_id,
            candidate_product_id=candidate.product_id,
            relationship_type=relationship,
            accepted=args.accepted,
            reviewed_by_user_id=args.reviewed_by_user_id,
            notes=args.notes,
        )
    )
    print(json.dumps(feedback.model_dump(mode="json"), indent=2, ensure_ascii=False))


def handle_list_product_matches(args: argparse.Namespace) -> None:
    if SessionLocal is None:
        raise SystemExit("DATABASE_URL is not configured.")

    repository = SqlProcurementRepository(SessionLocal)
    records = repository.list_product_match_candidates(
        business_id=args.business_id,
        supplier_offer_document_id=args.supplier_offer_document_id,
    )
    products_by_id = {
        product.id: product
        for product in repository.list_products(business_id=args.business_id)
    }
    items_by_id = {
        item.id: item
        for item in repository.list_supplier_offer_items(
            business_id=args.business_id,
            supplier_offer_document_id=args.supplier_offer_document_id,
        )
    }

    if args.format == "json":
        print(json.dumps([record.model_dump(mode="json") for record in records], indent=2))
        return

    writer = csv.writer(sys.stdout)
    writer.writerow(
        [
            "candidate_id",
            "status",
            "recommendation",
            "supplier_item",
            "supplier_price",
            "matched_product",
            "current_cost",
            "sale_price",
            "saving",
            "confidence_score",
            "relationship_type",
        ]
    )
    for record in records:
        item = items_by_id.get(record.supplier_offer_item_id)
        product = products_by_id.get(record.product_id or "")
        writer.writerow(
            [
                record.id,
                record.status,
                record.recommendation,
                item.raw_name if item else "",
                item.offer_price if item else "",
                product.name if product else "",
                product.current_cost if product else "",
                product.sale_price if product else "",
                record.cost_difference or "",
                record.confidence_score,
                record.relationship_type,
            ]
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Stock AI local tools.")
    subparsers = parser.add_subparsers(dest="command")

    message_parser = subparsers.add_parser("message", help="Send a message to the assistant core.")
    message_parser.add_argument("text", nargs="+", help="Message text to process.")
    message_parser.add_argument("--business-id", default="demo-business")
    message_parser.add_argument("--external-user-id", default="local-user")
    message_parser.set_defaults(handler=handle_message)

    import_parser = subparsers.add_parser("import-catalog", help="Import a POS catalog CSV.")
    import_parser.add_argument("csv_path", help="Path to the POS CSV file.")
    import_parser.add_argument("--business-id", default="demo-business")
    import_parser.set_defaults(handler=handle_import_catalog)

    offer_parser = subparsers.add_parser(
        "import-supplier-offer",
        help="Import a structured supplier offer JSON.",
    )
    offer_parser.add_argument("json_path", help="Path to a structured supplier offer JSON file.")
    offer_parser.add_argument("--business-id", default="demo-business")
    offer_parser.set_defaults(handler=handle_import_supplier_offer)

    extract_parser = subparsers.add_parser(
        "extract-supplier-offer-text",
        help="Extract a simple supplier offer from an already-text document.",
    )
    extract_parser.add_argument("text_path", help="Path to a text file.")
    extract_parser.add_argument("--supplier-name")
    extract_parser.add_argument("--business-id", default="demo-business")
    extract_parser.add_argument("--persist", action="store_true")
    extract_parser.set_defaults(handler=handle_extract_supplier_offer_text)

    compare_parser = subparsers.add_parser(
        "compare-supplier-offer",
        help="Compare a supplier offer document against local products.",
    )
    compare_parser.add_argument("supplier_offer_document_id")
    compare_parser.add_argument("--business-id", default="demo-business")
    compare_parser.add_argument("--max-candidates", type=int, default=1)
    compare_parser.add_argument("--persist-candidates", action="store_true")
    compare_parser.add_argument(
        "--format",
        choices=["summary", "csv", "json"],
        default="summary",
    )
    compare_parser.set_defaults(handler=handle_compare_supplier_offer)

    review_parser = subparsers.add_parser(
        "review-product-match",
        help="Accept or reject a persisted product match candidate.",
    )
    review_parser.add_argument("supplier_offer_document_id")
    review_parser.add_argument("product_match_candidate_id")
    review_parser.add_argument("--business-id", default="demo-business")
    review_group = review_parser.add_mutually_exclusive_group(required=True)
    review_group.add_argument("--accepted", dest="accepted", action="store_true")
    review_group.add_argument("--rejected", dest="accepted", action="store_false")
    review_parser.add_argument(
        "--relationship-type",
        choices=[relationship.value for relationship in SupplierProductRelationship],
    )
    review_parser.add_argument("--reviewed-by-user-id")
    review_parser.add_argument("--notes")
    review_parser.set_defaults(handler=handle_review_product_match)

    list_matches_parser = subparsers.add_parser(
        "list-product-matches",
        help="List persisted product match candidates for a supplier offer document.",
    )
    list_matches_parser.add_argument("supplier_offer_document_id")
    list_matches_parser.add_argument("--business-id", default="demo-business")
    list_matches_parser.add_argument("--format", choices=["csv", "json"], default="csv")
    list_matches_parser.set_defaults(handler=handle_list_product_matches)

    args = parser.parse_args()
    if not hasattr(args, "handler"):
        parser.print_help()
        raise SystemExit(2)

    args.handler(args)


if __name__ == "__main__":
    main()
