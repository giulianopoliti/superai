"""Read-only replay of stored A evidence against a frozen catalogue, no providers."""
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.stdout.reconfigure(encoding="utf-8")
from app.modules.procurement.analysis_contracts import OfferDecisionRequest, OfferTextBlock
from app.modules.procurement.decision_policy import DocumentDecisionPolicy, observed_offer_price
from app.modules.procurement.document_analysis import StructuredProposalAssembler
from app.modules.procurement.matching import ProductMatchService
from app.modules.procurement.schemas import Product

root = Path(__file__).parent
source = json.loads((root / "openai-run-dbea1644-full.json").read_text(encoding="utf-8-sig"))
catalog = json.loads((root / "catalog-demo-business-2026-10-07.json").read_text(encoding="utf-8-sig"))
products = [Product.model_validate(p) for p in catalog["products"]]

class SnapshotRepository:
    def list_products(self, *, business_id):
        return [p for p in products if p.business_id == business_id]

    def list_product_match_feedback_rules(self, **kwargs):
        return []

assembler, policy = StructuredProposalAssembler(), DocumentDecisionPolicy()
blocks = [OfferTextBlock.model_validate(b) for b in source["metadata"]["analysis_ledger"]["blocks"]]
offers = [offer for b in blocks for offer in assembler.assemble_interpretation(
    business_id=catalog["business_id"], block=b, classification=None, candidates=[],
).offers]
found = ProductMatchService(SnapshotRepository()).retrieve_analysis_candidates(
    business_id=catalog["business_id"], supplier_id=catalog["supplier_id"], offers=offers,
)
by_id = {b.block_id: b for b in blocks}
rows = []
for offer in offers:
    block = by_id[offer.block_id]
    result = policy.validate_decision(request=OfferDecisionRequest(
        business_id=catalog["business_id"], block=block, offer=offer,
        candidates=found[offer.offer_id], questions=[],
    ), decision=None)
    rows.append({"page": block.page_number, "order": block.order_on_page,
                 "name": result.offer.raw_name, "amount_valid": observed_offer_price(
                     offer=result.offer, block=block) is not None,
                 "identity": result.offer.identity_status, "base": result.offer.price_status,
                 "product_id": result.offer.product_id, "reasons": result.reason_codes})
output = {"note": "Stored transcription only; current snapshot, no visual or new model verification.",
          "offers": len(rows), "valid_amounts": sum(r["amount_valid"] for r in rows),
          "identities": dict(Counter(r["identity"] for r in rows)),
          "bases": dict(Counter(r["base"] for r in rows)), "rows": rows}
(root / "integrated_vital_replay.json").write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
print({key: value for key, value in output.items() if key != "rows"})
for row in rows:
    if row["identity"] == "confirmed":
        print({key: row[key] for key in ("page", "order", "name", "amount_valid", "identity", "base")})
