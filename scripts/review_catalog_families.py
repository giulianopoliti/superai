"""Operator CLI for family proposals. No AI calls and no original catalog writes."""
# ruff: noqa: E402

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.db.repositories.family_suggestions import list_suggestions
from app.db.session import SessionLocal
from app.modules.procurement.catalog_family_backfill import materialize_report
from app.modules.procurement.catalog_family_suggestions import FamilySuggestionService


def serialize(row):
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


def main():
    parser = argparse.ArgumentParser(
        description="Revisar sugerencias de familia sin modificar el stock"
    )
    parser.add_argument(
        "command", choices=["list", "backfill", "approve", "reject", "check-stale", "reconcile"]
    )
    parser.add_argument("--business-id", required=True)
    parser.add_argument("--kind", choices=["change", "creation"], default="change")
    parser.add_argument("--status", choices=["pending", "approved", "rejected", "applied", "stale"])
    parser.add_argument("--id")
    parser.add_argument("--actor-id")
    parser.add_argument("--notes")
    parser.add_argument("--reference")
    parser.add_argument("--observed-family-id")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if SessionLocal is None:
        parser.error("DATABASE_URL no está configurada")
    if args.command == "backfill" and not args.report:
        parser.error("backfill requiere --report con una selección explícita")
    if args.command in {"approve", "reject", "reconcile"} and not (args.id and args.actor_id):
        parser.error("La revisión requiere --id y --actor-id")
    if args.command in {"approve", "reject"} and not args.notes:
        parser.error("Aprobar o rechazar requiere --notes")
    if args.command == "reconcile" and not args.reference:
        parser.error("La reconciliación requiere --reference")
    with SessionLocal() as session:
        with session.begin() as transaction:
            service = FamilySuggestionService(session, args.business_id)
            if args.command == "list":
                result = {
                    "business_id": args.business_id,
                    "kind": args.kind,
                    "results": [
                        serialize(r)
                        for r in list_suggestions(session, args.business_id, args.kind, args.status)
                    ],
                }
            elif args.command == "backfill":
                result = materialize_report(
                    session, args.business_id, json.loads(args.report.read_text(encoding="utf-8"))
                )
            elif args.command == "check-stale":
                result = {"stale": service.check_stale()}
            elif args.command == "reconcile":
                result = serialize(
                    service.reconcile(
                        args.kind, args.id, args.actor_id, args.reference, args.observed_family_id
                    )
                )
            else:
                result = serialize(
                    service.review(
                        args.kind,
                        args.id,
                        "approved" if args.command == "approve" else "rejected",
                        args.actor_id,
                        args.notes,
                    )
                )
            if args.dry_run:
                session.flush()
                transaction.rollback()
                result["dry_run"] = True
    content = json.dumps(result, ensure_ascii=False, indent=2, default=str)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(content, encoding="utf-8")
    print(content)


if __name__ == "__main__":
    main()
