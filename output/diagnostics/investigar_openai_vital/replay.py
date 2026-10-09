import json,sys
from pathlib import Path
from collections import Counter
sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
from app.modules.procurement.analysis_contracts import OfferTextBlock,OfferDecisionRequest
from app.modules.procurement.document_analysis import StructuredProposalAssembler
from app.modules.procurement.decision_policy import DocumentDecisionPolicy, observed_offer_price
from app.modules.procurement.matching import ProductMatchService
from app.modules.procurement.schemas import Product
from app.modules.procurement.identity import identity_conflicts,identity_tokens
sys.stdout.reconfigure(encoding='utf-8')
def read(n):return json.loads(Path('output/diagnostics/'+n).read_text(encoding='utf-8-sig'))
f=read('openai-run-dbea1644-full.json');l=f['metadata']['analysis_ledger']; c=read('catalog-demo-business-2026-10-07.json')
class SnapshotRepository:
 def list_products(self,*,business_id):return [Product.model_validate(x) for x in c['products'] if x['business_id']==business_id]
 def list_product_match_feedback_rules(self,*,business_id,supplier_id,normalized_names):return []
repo=SnapshotRepository();matcher=ProductMatchService(repo);assembler=StructuredProposalAssembler();policy=DocumentDecisionPolicy();rows=[]
blocks=[OfferTextBlock.model_validate(x) for x in l['blocks']];offers=[];bm={b.block_id:b for b in blocks};am={}
for b in blocks:
 a=assembler.assemble_interpretation(business_id=c['business_id'],block=b,classification=None,candidates=[]);am[b.block_id]=a
 offers+=a.offers
found=matcher.retrieve_analysis_candidates(business_id=c['business_id'],supplier_id=c['supplier_id'],offers=offers)
products=repo.list_products(business_id=c['business_id'])
for o in offers:
 b=bm[o.block_id]; ranked=[]
 for p in products:
  score,reasons=matcher._score_identity(o,p)
  if score>=.45:ranked.append((score,p,reasons,identity_conflicts(o,p)))
 ranked.sort(key=lambda x:(-x[0],x[1].id));req=OfferDecisionRequest(business_id=c['business_id'],block=b,offer=o,candidates=found[o.offer_id],questions=[]);prepared=policy.prepare_request(request=req);r=policy.validate_decision(request=prepared,decision=None)
 saved=next((x for x in l['interpretations'] if x['offer_id']==o.offer_id),None)
 row={'page':b.page_number,'order':b.order_on_page,'text':b.full_text,'proposal':b.proposals[0].model_dump(mode='json') if b.proposals else None,'assembled':o.model_dump(mode='json'),'valid_observed_amount':str(observed_offer_price(offer=o,block=b)),'retrieved_candidates':[x.model_dump(mode='json') for x in found[o.offer_id]],'ranked_any':[{'score':str(s),'product_id':p.id,'name':p.name,'brand':p.brand,'unit_size':str(p.unit_size),'unit':p.unit,'reasons':rs,'conflicts':co,'tokens_equal':identity_tokens(o.raw_name)==identity_tokens(p.name)} for s,p,rs,co in ranked[:10]],'offline_policy':r.model_dump(mode='json'),'questions':[x.model_dump(mode='json') for x in prepared.questions],'saved':saved};rows.append(row)
Path('output/diagnostics/investigar_openai_vital/offline-stage-replay.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
print('OFFERS',len(offers),'ASSEMBLER_REASONS',[(b.page_number,b.order_on_page,am[b.block_id].reason_codes) for b in blocks if am[b.block_id].reason_codes]);print('ROUTES',Counter(x['offline_policy']['route'] for x in rows));print('CANDIDATES_LENGTH',Counter(len(x['retrieved_candidates']) for x in rows));print('REASONS',Counter(y for x in l['interpretations'] for y in x['reason_codes']))
for x in rows:
 if x['retrieved_candidates'] or not x['saved']['offer_price']:
  print('\n',x['page'],x['order'],x['assembled']['raw_name'],x['valid_observed_amount'],x['offline_policy']['route'],x['offline_policy']['reason_codes'])
  print([(y['score'],y['name'],y['conflicts'],y['tokens_equal']) for y in x['ranked_any'][:3]])
  print('RETRIEVED',[(y['name'],y['reason_codes']) for y in x['retrieved_candidates']])

