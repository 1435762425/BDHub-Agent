#!/usr/bin/env python3
"""Prepare intents or execute/verify one explicitly scoped IT card canary. No IM sends."""
import argparse,fcntl,hashlib,importlib.util,json,os,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];LEGACY=ROOT.parent/'01-BDSystem-V2'
sys.dont_write_bytecode=True;sys.path.insert(0,str(LEGACY));sys.path.insert(0,str(ROOT/'scripts'))
from lib.second_cycle import CycleStore,CycleError,digest,assess_offer
from lib.cycle_materials import Materials
from lib.cycle_card_creation import CardCreation
from lib.cycle_catalog import normalize,read_current_offer,CAMPAIGNS,PRODUCTS
SPEC=importlib.util.spec_from_file_location('cycle_card_inspection',ROOT/'scripts/prepare-cycle-materials.py');inspection=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(inspection)
CREATE='/api/v1/affiliate/partner/campaign/product_list/create';SELECTED='/api/v1/affiliate/partner/product/pick_up/list'

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['prepare','execute-one','verify-one']);p.add_argument('--id');p.add_argument('--report',type=Path,required=True);a=p.parse_args()
 if not a.report.resolve().is_relative_to(ROOT/'var') or a.report.exists():p.error('new var report required')
 a.report.parent.mkdir(parents=True,exist_ok=True);report={'action':a.action,'createRequests':0,'readRequests':0,'realSends':0}
 def save():a.report.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
 save()
 with (ROOT/'var/cycle-card-create.lock').open('a') as lock,CycleStore(ROOT/'var/second-cycle.sqlite') as store:
  fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);materials=Materials(store);ledger=CardCreation(store)
  plan=store.db.execute("SELECT id FROM plan WHERE institution='bjn-local-research' AND market='it'").fetchone()[0]
  if a.action=='prepare':
   intents=[]
   for offer in materials.candidates(plan):
    r=store.db.execute('SELECT payload FROM cycle_card_check WHERE plan_id=? AND offer_key=? AND offer_fingerprint=?',(plan,offer['offerKey'],digest(offer))).fetchone()
    if not r or json.loads(r[0])['state']!='needs_card_preparation':continue
    name=materials.name(offer)
    if not name:continue
    row=ledger.prepare(plan,offer,name['shortNameIt']);intents.append({k:row[k] for k in ('id','pid','list_name','state')})
   report['intents']=intents;save();print(json.dumps(report,ensure_ascii=False));return
  intent=ledger.get(a.id);offer=json.loads(intent['offer_json']);report.update(intentId=intent['id'],pid=offer['pid'],targetCreatorPercent=offer['creatorPercent']);save()
  if intent['plan_id']!=plan:raise CycleError('plan_mismatch')
  if intent['state'] in ('verified','invalidated'):report['status']='already_verified' if intent['state']=='verified' else 'invalidated';save();print(json.dumps(report));return
  from bdhub import scheduled_relogin
  from bdhub.hub.markets import MARKETS,identity_for
  from bdhub.send.taplink.transport import account_for
  from bdhub.send.taplink.protocol import create_payload,creation_receipt
  from bdhub.research.catalog_rules import link_rules_for,engine_for
  from bdhub.enrich.identity_store import load_identity
  from lib.italy_cards import legacy_params
  import requests
  capability=MARKETS['it'].capabilities.tap_link
  if capability not in ('canary','enabled'):raise CycleError('tap_link_not_available')
  report['capability']=capability;report['scope']='one IT ACC6 product-list creation or read-only verification'
  cfg,account=account_for('it','acc6',check_maintenance=False);identity=identity_for('it',account=account,cfg=cfg).require_product_search()
  original_scope=json.loads((ROOT/'var/cycle-catalog-it-20260913/selected.json').read_text())['scope']
  if not identity.partner_id_is_own or original_scope['institutionFingerprint']!=digest(str(identity.im_market_partner_id)):raise CycleError('institution_changed')
  before=hashlib.sha256(Path(account.headers_json).read_bytes()).hexdigest()
  spec=importlib.util.spec_from_file_location('create_guard',ROOT/'scripts/probe-italy-profile.py');guard=importlib.util.module_from_spec(spec);spec.loader.exec_module(guard)
  try:
   with guard.readonly_guard(account,wait_seconds=30),requests.Session() as session:
    session.trust_env=False;headers={k:v for k,v in load_identity(account.headers_json).headers.items() if not k.startswith(':') and k.lower() not in ('host','content-length','origin','referer')};headers.update(origin='https://partner.eu.tiktokshop.com',referer='https://partner.eu.tiktokshop.com/');params=legacy_params(identity,account);last=[0.]
    def request(method,path,extra,body=None,write=False):
     allowed={("GET",inspection.CARD),("GET",inspection.MEMBERS),("POST",SELECTED),("GET",CAMPAIGNS),("GET",PRODUCTS)}
     if write:
      if (method,path)!=('POST',CREATE) or report['createRequests'] or a.action!='execute-one':raise CycleError('write_scope_invalid')
     elif (method,path) not in allowed:raise CycleError('read_scope_invalid')
     if scheduled_relogin.maintenance_due(account,initialize=False,ignore_retry_throttle=True):raise CycleError('maintenance_due')
     time.sleep(max(0,last[0]+1-time.monotonic()));last[0]=time.monotonic()
     if write:
      if not assess_offer(fresh,time.time())['eligible'] or time.time()-fresh['observedAt']>60:raise CycleError('preflight_expired')
      ledger.begin(intent['id']);report['createRequests']+=1
     else:report['readRequests']+=1
     save()
     response=session.request(method,identity.host+path,params=params|extra,json=body,headers=headers,timeout=(5,25),allow_redirects=False)
     if response.status_code!=200 or response.headers.get('bdturing-verify') or response.headers.get('x-tt-system-error')=='3':raise CycleError('remote_result_unconfirmed')
     data=response.json()
     if type(data.get('code')) is not int or data['code']!=0:raise CycleError('remote_business_rejected')
     if path==inspection.CARD:
      rows=(data.get('data') or {}).get('list') or []
      report['lastImCardRows']=[{'listId':r.get('product_list_id'),'name':r.get('product_list_name'),'campaignId':r.get('campaign_id'),'matchingProducts':[{'pid':v.get('product_id'),'campaignId':v.get('campaign_id'),'creatorPercentRaw':v.get('creator_commission_percent')} for v in r.get('campaign_products',[]) if str(v.get('product_id'))==offer['pid']]} for r in rows]
     if path==inspection.MEMBERS:
      body=data.get('data') or {};report['lastMembersSummary']={'total':body.get('total_num'),'products':[{k:v.get(k) for k in ('product_id','campaign_id','creator_commission_percent','stock','product_status','is_under_governed','unavailable_type')} for v in body.get('campaign_products',[]) if str(v.get('product_id'))==offer['pid']]}
     save()
     return data,hashlib.sha256(response.content).hexdigest()
    read=lambda path,extra:request('GET',path,extra)
    if a.action=='execute-one':
     if intent['state']!='prepared':raise CycleError('verify_existing_attempt_first')
     current={o['offerKey']:o for _,o in store._offers(plan)}
     if offer['offerKey'] not in current or digest(current[offer['offerKey']])!=digest(offer):raise CycleError('offer_snapshot_changed')
     rule=link_rules_for('it',offer['catalogSource'])[0]
     if digest(rule)!=offer['commissionRuleFingerprint']:raise CycleError('commission_rule_changed')
     fresh=read_current_offer(offer,rule,engine_for(rule).calculate,request,time.time)
     if not assess_offer(fresh,time.time())['eligible'] or fresh['creatorPercent']!=offer['creatorPercent']:
      ledger.invalidate_preflight(intent['id'],fresh)
      report.update(status='invalidated',reason='current_offer_changed',currentAssessment=assess_offer(fresh,time.time()),currentCreatorPercent=fresh['creatorPercent']);save();return
     existing=inspection.inspect_card(offer,read)
     if existing['state']=='verified_read_only':
      ledger.confirm(intent['id'],existing,reused=True);report.update(status='reused',card=existing);save();print(json.dumps(report));return
     payload=create_payload(pid=offer['pid'],campaign_id=offer['campaignId'],creator_pct=offer['creatorPercent'],name=intent['list_name'],route=offer['catalogSource'])
     body,sha=request('POST',CREATE,{},payload,True)
     receipt=creation_receipt(body);receipt['responseSha256']=sha;ledger.save_receipt(intent['id'],receipt)
    else:receipt=json.loads(intent['receipt']) if intent['receipt'] else {}
    card=inspection.inspect_card(offer,read,expected_list_id=receipt.get('list_id'),expected_name=intent['list_name'])
    if card['state']!='verified_read_only':raise CycleError('created_card_not_verified')
    ledger.confirm(intent['id'],card);report.update(status='verified',card=card)
  except Exception as e:
   ledger.unknown(intent['id']);report.update(status='unresolved',error=str(e) if isinstance(e,CycleError) else type(e).__name__)
  finally:report['identityFileUnchanged']=hashlib.sha256(Path(account.headers_json).read_bytes()).hexdigest()==before;report['intentState']=ledger.get(intent['id'])['state'];save()
  print(json.dumps(report,ensure_ascii=False))
if __name__=='__main__':main()
