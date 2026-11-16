"""Run local deterministic matching against Cloud PostgreSQL in read-only mode."""

import argparse
import json
import sys
from pathlib import Path

from sqlalchemy import create_engine, make_url, text
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.repositories.sql_procurement import SqlProcurementRepository  # noqa: E402
from app.modules.procurement.matching import ProductMatchService  # noqa: E402
from app.modules.procurement.schemas import SupplierOfferItem  # noqa: E402
from app.settings import settings  # noqa: E402

DEFAULT_CASES = (
    ("Nescafe Dolca 170 g", "Nescafe"),
    ("Nescafe Dolca Origen 170 g", "Nescafe"),
    ("Nesquik cacao 360 g", "Nesquik"),
    ("Cacao Nesquik 180 g", "Nesquik"),
)


def check_catalog(factory, business_id, cases):
    products = SqlProcurementRepository(factory).list_products(business_id=business_id)
    fresh = sum(product.auxiliary is not None for product in products)
    report = {
        "mode": "local_matching_cloud_read_only", "business_id": business_id,
        "active_products": len(products), "current_interpretations": fresh,
        "original_only_products": len(products) - fresh, "paid_calls": 0,
        "cases": [],
    }
    service = ProductMatchService(None)
    for name, brand in cases:
        item = SupplierOfferItem(
            business_id=business_id, supplier_id="local-check",
            supplier_offer_document_id="local-check",
            raw_name=name, normalized_name=name.lower(), brand=brand, offer_price=1,
        )
        candidates = service.match_item(item=item, products=products, limit=3)
        report["cases"].append({
            "offer": name,
            "candidates": [{
                "product_id": c.product.id if c.product else None,
                "original_name": c.product.name if c.product else None,
                "uses_interpretation": bool(c.product and c.product.auxiliary),
                "identity_accepted": c.auto_accepted,
                "retrieval_score": str(c.confidence_score), "reasons": c.reasons,
            } for c in candidates],
        })
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--business-id", default=settings.default_business_id)
    parser.add_argument("--query", help="Optional offer name; default runs four fixed cases")
    parser.add_argument("--brand")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if (not settings.database_url
            or make_url(settings.database_url).get_backend_name() != "postgresql"):
        parser.error("Configure DATABASE_URL for the existing Cloud PostgreSQL")
    engine = create_engine(
        settings.database_url, connect_args={"connect_timeout": 10},
        execution_options={"postgresql_readonly": True},
    )
    try:
        with engine.connect() as connection:
            if connection.scalar(text("SHOW transaction_read_only")) != "on":
                raise RuntimeError("Read-only transaction was not enforced")
        cases = [(args.query, args.brand)] if args.query else DEFAULT_CASES
        report = check_catalog(sessionmaker(bind=engine), args.business_id, cases)
        serialized = json.dumps(report, ensure_ascii=False, indent=2)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(serialized + "\n", encoding="utf-8")
        print(serialized)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
