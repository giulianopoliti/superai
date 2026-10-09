"""Readonly snapshot + fake repository; proposals remain unconfirmed."""
import json
import sys
from pathlib import Path
from decimal import Decimal

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from app.modules.procurement.analysis_contracts import OfferTextBlock, OfferInterpretation, OfferDecisionRequest, EvidenceRef
from app.modules.procurement.schemas import Product
from app.modules.procurement.matching import ProductMatchService
from app.modules.procurement.decision_policy import DocumentDecisionPolicy
from app.modules.procurement.block_interpretation import detect_value_candidates

def read(name):
    return json.loads((ROOT/"output/diagnostics"/name).read_text(encoding="utf-8-sig"))
ledger = read("jev-run-4a3058e1-full.json")["metadata"]["analysis_ledger"]
catalog = read("catalog-demo-business-2026-10-07.json")
class SnapshotRepository:
    def list_products(self, *, business_id):
        assert business_id == "demo-business"
        return [Product.model_validate(p) for p in catalog["products"]]
    def list_product_match_feedback_rules(self, *, business_id, supplier_id, normalized_names):
        assert business_id == "demo-business"
        assert not catalog["feedback"]
        return []
service = ProductMatchService(SnapshotRepository())
policy = DocumentDecisionPolicy()
blocks = {b["block_id"]:OfferTextBlock.model_validate(b) for b in ledger["blocks"]}
offers = [OfferInterpretation.model_validate(o) for o in ledger["interpretations"]]
found = service.retrieve_analysis_candidates(business_id="demo-business", supplier_id=catalog["supplier_id"], offers=offers)
rows=[]
for offer in offers:
    request = OfferDecisionRequest(business_id="demo-business", block=blocks[offer.block_id], offer=offer, candidates=found[offer.offer_id], questions=[])
    prepared=policy.prepare_request(request=request)
    result=policy.validate_decision(request=request,decision=None)
    rows.append({"offer_id":offer.offer_id,"raw_name":offer.raw_name,"offer_price":str(offer.offer_price),
                 "retrieved_candidates":[c.name for c in found[offer.offer_id]],"route":result.route,
                 "questions":[q.question_id.rsplit(":",1)[-1] for q in prepared.questions],
                 "identity_status":result.offer.identity_status,"price_status":result.offer.price_status,
                 "has_price_type_refs":bool(offer.field_evidence.get("price_type"))})

fixtures=[]
for label,needle,name,amount,size,unit in [
    ("NATURA","DESDE 3 UNIDADES\n$3.649","NATURA\nAceite girasol\nx900ml.","3649","900","ml"),
    ("KEVIN","corporal dubal","KEVIN\nDesodorante\ncorporal dubal\nx150ml","3889","150","ml"),
    ("KIMBIES","Toalla humeda\nx48u","KIMBIES\nToalla humeda\nx48u","1869",None,None),
]:
    block=next(b for b in blocks.values() if needle in b.full_text)
    fragment=next(f for f in block.fragments if name in f.text)
    start=fragment.text.index(name)
    offer=OfferInterpretation(offer_id=f"offline-{label}",block_id=block.block_id,business_id="demo-business",raw_name=name,
        unit_size=Decimal(size) if size else None,unit=unit,offer_price=Decimal(amount),currency="$",
        field_evidence={"raw_name":[EvidenceRef(fragment_id=fragment.fragment_id,quote=name,start=start,end=start+len(name))]})
    candidates=service.retrieve_analysis_candidates(business_id="demo-business",supplier_id=catalog["supplier_id"],offers=[offer])
    fixtures.append({"label":label,"literal_name":name,"catalog_candidates":[{"name":c.name,"reason_codes":c.reason_codes} for c in candidates[offer.offer_id]],
                     "note":"Counterfactual literal span, not provider output or identity confirmation."})

heineken=next(b for b in blocks.values() if "HEINEKEN" in b.full_text)
result={"catalog_note":catalog["snapshot_note"],"product_count":len(catalog["products"]),"feedback_count":len(catalog["feedback"]),
        "rows":rows,"counterfactuals":fixtures,"heineken_size_candidates":[c.literal for c in detect_value_candidates(block=heineken) if c.kind=="size"]}
Path(__file__).with_name("audit_policy_matching_results.json").write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
print(json.dumps(result,ensure_ascii=False,indent=2))
