#!/usr/bin/env python3
"""Read or refresh the safe catalogue onboarding status for one enabled market."""
import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.dont_write_bytecode=True
sys.path.insert(0,str(ROOT/'scripts'))

from lib.account_identity import current_generation  # noqa:E402
from lib.campaign_join import preview as campaign_preview,status as campaign_status  # noqa:E402
from lib.campaign_screen import status as screen_status  # noqa:E402
from lib.global_source import GlobalSources  # noqa:E402
from lib.market_accounts import load_config  # noqa:E402
from lib.market_registry import market as market_record  # noqa:E402
from lib.second_cycle import CycleStore  # noqa:E402

def source_path(market):
 path=ROOT/('var/global-source.sqlite' if market=='it' else f'var/global-source-{market}.sqlite')
 if not path.exists():
  audit=ROOT/f'var/{market}-global-onboarding-canary.sqlite'
  path=audit if audit.exists() else path
 return path

def full_products(market,offset=0,query=''):
 meta=market_record(ROOT,market)
 if meta['capabilities']['fullManagedCatalog'] is not True:
  return {'market':market,'availability':'unsupported' if meta['capabilities']['fullManagedCatalog'] is False else 'unavailable','items':[],'total':0,'offset':offset,'limit':30,'observedAt':None,'readOnly':True,'platformWrites':0}
 path=source_path(market)
 if not path.exists():return {'market':market,'availability':'unavailable','items':[],'total':0,'offset':offset,'limit':30,'observedAt':None,'readOnly':True,'platformWrites':0}
 store=GlobalSources(path,readonly=True)
 try:
  result=store.products(offset=offset,limit=30,query=query)
  if result.get('market')!=market:raise ValueError('market_catalog_scope_mismatch')
  items=[]
  for row in result.get('items',[]):
   items.append({key:row.get(key) for key in ('pid','title','listedSelected','publicCommissionRaw','totalCommissionRaw','detailsChecked','stockChecked','selectionObservation')}
    |{'selectedOffers':[{'creatorPercent':offer.get('creatorPercent'),'eligible':(offer.get('assessment') or {}).get('eligible')} for offer in row.get('selectedOffers',[])[:4]]})
  return {'market':market,'availability':'ready' if result.get('available') else 'unavailable','items':items,'total':int(result.get('totalMatches') or 0),'offset':offset,'limit':30,
          'displayRunId':result.get('displayRunId'),'latestRunId':result.get('id'),
          'observedAt':(result.get('activePublished') or {}).get('updated') or result.get('updatedAt'),
          'readOnly':True,'platformWrites':0}
 finally:store.close()


def full_status(market):
 if not market_record(ROOT,market)['capabilities']['fullManagedCatalog']:
  return {'applicable':False,'available':False,'reason':'not_supported'}
 path=source_path(market)
 if not path.exists():return {'applicable':True,'available':False,'reason':'not_collected'}
 store=GlobalSources(path,readonly=True)
 try:
  value=store.status()
  result={'applicable':True,**{key:value.get(key) for key in (
   'available','state','products','pages','reportedTotal','reason','published','coverage',
   'categoryCount','categoriesCompleted','categoryMemberships','categoryOverlap','stableDuplicateRows',
   'listedSelectedProducts','listedUnselectedProducts','detailProducts','operatorAcceptance','updatedAt','elapsedSeconds') if key in value}}
  with sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True) as db:
   screen=db.execute('SELECT counts FROM global_source_screen_run ORDER BY updated DESC LIMIT 1').fetchone() if db.execute("SELECT 1 FROM sqlite_master WHERE name='global_source_screen_run'").fetchone() else None
  if screen:
   counts=(json.loads(screen[0]) or {}).get('states') or {};result['screen']={'eligible':int(counts.get('eligible') or 0),'rejected':int(counts.get('rejected') or 0)}
  selection=ROOT/('var/global-selection.sqlite' if market=='it' else f'var/global-selection-{market}.sqlite')
  if selection.exists():
   with sqlite3.connect(selection.resolve().as_uri()+'?mode=ro',uri=True) as db:
    run=db.execute('SELECT id FROM intake_run ORDER BY created DESC LIMIT 1').fetchone()
    states=dict(db.execute('SELECT state,count(*) FROM intake_item WHERE run_id=? GROUP BY state',(run[0],)).fetchall()) if run else {}
   result['selection']={'states':states,'selected':int(states.get('confirmed') or 0)+int(states.get('already_selected') or 0),'pending':int(states.get('pending') or 0)}
  return result
 finally:store.close()


def public_campaign(value):
 return {key:value.get(key) for key in (
  'available','reason','state','error','account','joinedCount','counts','platformWrites') if key in value} | {
   'unresolvedCount':len(value.get('unresolved') or [])}


def public_screen(value):
 if not value.get('available'):return {key:value.get(key) for key in ('available','market','source','reason') if key in value}
 pool=value.get('pool') or {}
 return {key:value.get(key) for key in (
  'available','market','source','snapshot','runId','offers','distinctPids','eligiblePids',
  'multiCampaignPids','counts','reasons') if key in value} | {'pool':{'counts':pool.get('counts') or {}}}


def status(market):
 meta=market_record(ROOT,market);pair=load_config(ROOT)['markets'][market]
 with CycleStore(ROOT/'var/second-cycle.sqlite',readonly=True) as store:
  supply=pair['roles']['supply'];generation=current_generation(store,market,supply)
  plan=store.db.execute("SELECT id FROM plan WHERE institution='bjn-local-research' AND market=?",(market,)).fetchone()
  downstream={'leadEdges':0,'identityResolved':0,'deliveriesConfirmed':0,'activeTapLinks':0}
  if plan:
   plan=plan[0]
   downstream['leadEdges']=store.db.execute('SELECT count(*) FROM source_edge WHERE plan_id=?',(plan,)).fetchone()[0]
   downstream['identityResolved']=store.db.execute('SELECT count(*) FROM cycle_identity_resolution WHERE plan_id=?',(plan,)).fetchone()[0]
   if store.db.execute("SELECT 1 FROM sqlite_master WHERE name='cycle_delivery'").fetchone():
    downstream['deliveriesConfirmed']=store.db.execute("SELECT count(*) FROM cycle_delivery WHERE plan_id=? AND state='confirmed'",(plan,)).fetchone()[0]
  links=ROOT/'var/catalog-links.sqlite'
  if links.exists():
   with sqlite3.connect(links.resolve().as_uri()+'?mode=ro',uri=True) as db:
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='catalog_current_binding'").fetchone():
     downstream['activeTapLinks']=db.execute("SELECT count(*) FROM catalog_current_binding WHERE market=? AND state='active'",(market,)).fetchone()[0]
 capabilities={key:value['state'] for key,value in (generation or {}).get('capabilities',{}).items()}
 try:campaign=campaign_status(ROOT,market=market)
 except (OSError,ValueError,sqlite3.Error):campaign={'available':False,'reason':'not_previewed'}
 try:screen=screen_status(ROOT,market=market)
 except (OSError,ValueError,sqlite3.Error):screen={'available':False,'reason':'snapshot_missing'}
 return {'schemaVersion':'bdhub.market-catalog.v1','market':market,'label':meta['label'],
  'capabilities':meta['capabilities'],'account':supply,'accountCapabilities':capabilities,
  'campaign':public_campaign(campaign),'screen':public_screen(screen),'fullManaged':full_status(market),'downstream':downstream,
  'readOnly':True,'platformWrites':0,'realSends':0}


def main():
 parser=argparse.ArgumentParser(description=__doc__)
 parser.add_argument('action',choices=('status','preview-campaign','products'))
 parser.add_argument('--market',required=True)
 parser.add_argument('--offset',type=int,default=0)
 parser.add_argument('--query',default='')
 args=parser.parse_args()
 try:
  if args.action=='products':
   if not 0<=args.offset<=1000000 or len(args.query)>100:raise ValueError('market_catalog_products_query_invalid')
   print(json.dumps(full_products(args.market,args.offset,args.query),ensure_ascii=False));return 0
  if args.action=='preview-campaign':campaign_preview(ROOT,market=args.market)
  print(json.dumps(status(args.market),ensure_ascii=False));return 0
 except (OSError,ValueError,sqlite3.Error) as error:
  print(json.dumps({'error':str(error)},ensure_ascii=False));return 2


if __name__=='__main__':raise SystemExit(main())
