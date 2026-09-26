#!/usr/bin/env python3
"""Prepare five product-level names and read existing cards. No create/send."""
import argparse,fcntl,hashlib,importlib.util,json,os,sys,time
from pathlib import Path
from decimal import Decimal
ROOT=Path(__file__).resolve().parents[1];LEGACY=ROOT.parent/'01-BDSystem-V2'
sys.dont_write_bytecode=True;sys.path.insert(0,str(ROOT/'scripts'))
from lib.legacy_runtime import configure_vendored_bdhub
configure_vendored_bdhub(root=ROOT,legacy_root=LEGACY)
from lib.second_cycle import CycleStore,CycleError,digest,encoded
from lib.cycle_materials import Materials,render
from lib.product_stock_policy import require_stock,unavailable_allowed
CARD='/api/v1/affiliate/partner/im/product_list/list';MEMBERS='/api/v1/affiliate/partner/campaign/product_list/products'

def inspect_card(offer,read,*,expected_list_id=None,expected_name=None):
 wanted=str(offer['campaignId']);wire='0' if offer['catalogSource']=='selected' else wanted
 candidates=[];rates=[];seen=set();total=None
 for page in range(1,6):
  body,sha=read(CARD,{'cur_page':page,'page_size':20,'version':1,'search_type':2,'key_word':offer['pid']});data=body.get('data',{});n=data.get('total');rows=data.get('list',[]) if n==0 else data.get('list')
  if type(n) is not int or n<0 or not isinstance(rows,list) or total is not None and total!=n:raise CycleError('card_search_incomplete')
  total=n
  for row in rows:
   lid=str(row.get('product_list_id') or '')
   if not lid.isdigit() or lid in seen:raise CycleError('card_search_duplicate')
   seen.add(lid)
   if expected_list_id and lid!=expected_list_id:continue
   if expected_name and row.get('product_list_name')!=expected_name:continue
   if str(row.get('campaign_id') or '0')!=wire:continue
   products=[p for p in row.get('campaign_products',[]) if str(p.get('product_id'))==offer['pid']]
   if len(products)!=1:continue
   raw=products[0].get('creator_commission_percent');rate=Decimal(str(raw))/100 if raw is not None else None
   if rate is not None:rates.append(format(rate,'f'))
   if rate is None or rate==Decimal(offer['creatorPercent']):candidates.append((lid,sha,row))
  if len(seen)>=total:break
  if not rows:raise CycleError('card_search_incomplete')
 else:raise CycleError('card_search_incomplete')
 if expected_name and len(candidates)>1:raise CycleError('card_name_ambiguous')
 for lid,imsha,card_row in candidates[:3]:
  body,sha=read(MEMBERS,{'list_id':lid,'source':2 if wire=='0' else 1});data=body.get('data',{});rows=data.get('campaign_products')
  if not isinstance(rows,list) or data.get('total_num')!=len(rows):raise CycleError('card_members_incomplete')
  members=[p for p in rows if str(p.get('product_id'))==offer['pid'] and (str(p.get('campaign_id'))==wanted or wire!='0' and p.get('campaign_id') is None)]
  if len(members)!=1:continue
  p=members[0];rate=Decimal(str(p.get('creator_commission_percent')))/100
  rates.append(format(rate,'f'))
  if rate!=Decimal(offer['creatorPercent']):continue
  if str(p.get('product_status'))!='2' or p.get('is_under_governed') is True or not unavailable_allowed(p.get('unavailable_type'),offer):continue
  if require_stock(offer) and (p.get('stock') is None or Decimal(str(p.get('stock')))<=100):continue
  return {'state':'verified_read_only','pid':offer['pid'],'verifiedListName':expected_name,'listName':str(card_row.get('product_list_name') or ''),'campaignName':str(card_row.get('campaign_name') or ''),'stock':str(p.get('stock')) if require_stock(offer) else None,'stockRequired':require_stock(offer),'publicPercent':format(Decimal(str(p['plan_commission_percent']))/100,'f') if p.get('plan_commission_percent') is not None else None,'listId':lid,'wireCampaignId':wire,'sourceCampaignId':wanted,'creatorPercent':format(rate,'f'),'checkedAt':time.time(),'evidenceRefs':[imsha,sha],'executionAllowed':False}
 if len(candidates)>3:raise CycleError('card_candidates_incomplete')
 return {'state':'needs_card_preparation','checkedAt':time.time(),'observedCreatorRates':sorted(set(rates)),'candidateListsChecked':min(len(candidates),3),'executionAllowed':False}

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--generate-names',action='store_true');p.add_argument('--check-cards',action='store_true');p.add_argument('--report',type=Path,required=True);a=p.parse_args()
 if not a.report.resolve().is_relative_to(ROOT/'var') or a.report.exists():p.error('new var report required')
 a.report.parent.mkdir(parents=True,exist_ok=True)
 report={'modelCalls':0,'cardReadRequests':0,'platformWrites':0,'realSends':0,'products':[]}
 def save():a.report.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
 with (ROOT/'var/cycle-materials.lock').open('a') as lock,CycleStore(ROOT/'var/second-cycle.sqlite') as store:
  fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  plan=store.db.execute("SELECT id FROM plan WHERE institution='bjn-local-research' AND market='it'").fetchone()[0];materials=Materials(store);offers=materials.candidates(plan)
  if store._plan(plan)['state']!='active':raise CycleError('plan_paused')
  if a.generate_names:
   from lib.draft_provider import call_model
   from lib.model_service import guard
   call_model=guard(ROOT,call_model)
   report['namePreparation']=materials.prepare_names(offers,call_model);report['modelCalls']=report['namePreparation']['modelCalls'];save()
  if a.check_cards and offers:
   from bdhub import scheduled_relogin
   from bdhub.send.taplink.transport import account_for
   from bdhub.hub.markets import identity_for
   from bdhub.enrich.identity_store import load_identity
   from lib.italy_cards import legacy_params
   import requests
   cfg,account=account_for('it','acc6',check_maintenance=False);identity=identity_for('it',account=account,cfg=cfg).require_product_search()
   if not identity.partner_id_is_own:raise CycleError('identity_not_own')
   before=hashlib.sha256(Path(account.headers_json).read_bytes()).hexdigest()
   spec=importlib.util.spec_from_file_location('material_guard',ROOT/'scripts/probe-italy-profile.py');guard=importlib.util.module_from_spec(spec);spec.loader.exec_module(guard)
   with guard.readonly_guard(account,wait_seconds=30),requests.Session() as session:
    session.trust_env=False;headers={k:v for k,v in load_identity(account.headers_json).headers.items() if not k.startswith(':') and k.lower() not in ('host','content-length','origin','referer')};headers.update(origin='https://partner.eu.tiktokshop.com',referer='https://partner.eu.tiktokshop.com/');params=legacy_params(identity,account);last=[0.]
    def read(path,extra):
     if path not in (CARD,MEMBERS):raise CycleError('read_path_forbidden')
     if scheduled_relogin.maintenance_due(account,initialize=False,ignore_retry_throttle=True):raise CycleError('maintenance_due')
     time.sleep(max(0,last[0]+1-time.monotonic()));last[0]=time.monotonic();report['cardReadRequests']+=1
     r=session.get(identity.host+path,params=params|extra,headers=headers,timeout=(5,20),allow_redirects=False)
     if r.status_code!=200 or r.headers.get('bdturing-verify') or r.headers.get('x-tt-system-error')=='3':raise CycleError('card_read_blocked')
     body=r.json()
     if type(body.get('code')) is not int or body['code']!=0:raise CycleError('card_business_rejected')
     return body,hashlib.sha256(r.content).hexdigest()
    for o in offers:
     try:card=inspect_card(o,read)
     except Exception as e:card={'state':'check_unresolved','reason':str(e) if isinstance(e,CycleError) else 'card_fields_invalid','executionAllowed':False}
     with store.tx():store.db.execute('INSERT OR REPLACE INTO cycle_card_check VALUES(?,?,?,?)',(plan,o['offerKey'],digest(o),encoded(card)))
     report['products'].append({'pid':o['pid'],'name':materials.name(o),'creatorPercent':o['creatorPercent'],'card':card,'templates':[render(materials.name(o),o,k) for k in ('standard','brief','video_live')] if materials.name(o) else []});save()
     if card['state']=='check_unresolved':break
   report['identityFileUnchanged']=hashlib.sha256(Path(account.headers_json).read_bytes()).hexdigest()==before
  else:
   report['products']=[{'pid':o['pid'],'name':materials.name(o),'creatorPercent':o['creatorPercent'],'templates':[render(materials.name(o),o)] if materials.name(o) else []} for o in offers]
 save();print(json.dumps({'modelCalls':report['modelCalls'],'cardReadRequests':report['cardReadRequests'],'products':len(report['products']),'platformWrites':0}))
if __name__=='__main__':main()
