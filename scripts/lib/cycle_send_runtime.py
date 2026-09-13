"""Current card binding for the cycle sender; only known verified conversations."""
import importlib.util,hashlib,time,sqlite3
from contextlib import closing
from datetime import datetime,timezone
from pathlib import Path
from decimal import Decimal
from dataclasses import asdict
from lib.second_cycle import CycleError,digest
from lib.product_stock_policy import full_managed,mark_full_managed
from lib.italy_im_delivery import ItalyVerifiedProductCard,card_binding_sha256,CARD_ORIGIN,CARD_TITLE_KEY
ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('cycle_card_reader',ROOT/'scripts/prepare-cycle-materials.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)

def descriptor(c):
 value={'product_id':c['pid'],'list_id':c['listId'],'campaign_id':c['wireCampaignId'],'list_name':c['listName'],'campaign_name':c['campaignName'],'market':'it','account_name':'acc6','verified_at':datetime.fromtimestamp(c['checkedAt'],timezone.utc).isoformat(),'origin':CARD_ORIGIN,'evidence_sha256':digest(c['evidenceRefs']),'verified':True,'title_key':CARD_TITLE_KEY}
 value['binding_sha256']=card_binding_sha256(value);return ItalyVerifiedProductCard(**value)

def fresh_card(candidate,account,identity,headers,maintenance,stopped,*,request_budget=None):
 import requests
 from lib.italy_cards import legacy_params
 offer=candidate['offer'];last=0
 # Old delivered snapshots retain their original bytes; apply the current product policy only to this fresh read.
 if not full_managed(offer):
  with closing(sqlite3.connect((ROOT/'var/second-cycle.sqlite').as_uri()+'?mode=ro',uri=True)) as policy:
   if policy.execute("SELECT 1 FROM sqlite_master WHERE name='cycle_product_management'").fetchone():
    row=policy.execute("SELECT m.evidence_ref FROM cycle_product_management m JOIN plan p ON p.id=m.plan_id WHERE p.market='it' AND p.institution='bjn-local-research' AND m.pid=? AND m.kind='full_managed'",(offer['pid'],)).fetchone()
    if row:offer=mark_full_managed(offer,row[0])
 with requests.Session() as session:
  session.trust_env=False
  safe={k:v for k,v in headers.items() if not k.startswith(':') and k.lower() not in ('host','content-length','origin','referer')};safe.update(origin='https://partner.eu.tiktokshop.com',referer='https://partner.eu.tiktokshop.com/')
  def read(path,params):
   nonlocal last
   if stopped() or maintenance():raise CycleError('account_unavailable')
   if path not in (module.CARD,module.MEMBERS):raise CycleError('read_path_forbidden')
   if request_budget is not None:request_budget.acquire()
   else:time.sleep(max(0,last+1-time.monotonic()))
   if stopped() or maintenance():raise CycleError('account_unavailable')
   last=time.monotonic()
   r=session.get(identity.host+path,params=legacy_params(identity,account)|params,headers=safe,timeout=(5,20),allow_redirects=False)
   if r.status_code!=200 or r.headers.get('bdturing-verify'):raise CycleError('card_read_unavailable')
   b=r.json()
   if type(b.get('code')) is not int or b['code']!=0:raise CycleError('card_read_rejected')
   return b,hashlib.sha256(r.content).hexdigest()
  c=module.inspect_card(offer,read,expected_list_id=candidate['card']['listId'])
  if c['state']!='verified_read_only' or c.get('publicPercent') is None or Decimal(c['creatorPercent'])<=Decimal(c['publicPercent']):raise CycleError('commission_advantage_unverified')
  return descriptor(c),c
