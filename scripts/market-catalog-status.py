#!/usr/bin/env python3
"""Read or refresh the safe catalogue onboarding status for one enabled market."""
import argparse
from collections import Counter
from contextlib import closing
import json
import sqlite3
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.dont_write_bytecode=True
sys.path.insert(0,str(ROOT/'scripts'))

from lib.account_identity import current_generation  # noqa:E402
from lib.campaign_join import preview as campaign_preview,status as campaign_status  # noqa:E402
from lib.campaign_screen import recorded as recorded_screen  # noqa:E402
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

def category_snapshot(store,market):
 if not store.db.execute("SELECT 1 FROM sqlite_master WHERE name='global_source_partition'").fetchone():return None
 head=store.db.execute('''SELECT h.scope_hash FROM global_source_head h JOIN global_source_run current
  ON current.id=h.run_id WHERE json_extract(current.scope,'$.market')=?
  ORDER BY current.created DESC LIMIT 1''',(market,)).fetchone()
 if not head:return None
 row=store.db.execute('''SELECT r.id,r.state,r.updated,
  (SELECT count(*) FROM global_source_product p WHERE p.run_id=r.id),
  (SELECT coalesce(sum(page_count),0) FROM global_source_partition q WHERE q.run_id=r.id),
  (SELECT count(*) FROM global_source_partition q WHERE q.run_id=r.id),
  (SELECT count(*) FROM global_source_partition q WHERE q.run_id=r.id AND q.state='completed'),
  EXISTS(SELECT 1 FROM global_source_head h WHERE h.scope_hash=r.scope_hash AND h.run_id=r.id)
  FROM global_source_run r WHERE r.scope_hash=? AND json_extract(r.scope,'$.market')=?
  AND json_extract(r.scope,'$.partitionMode')='category_l1_v1'
  AND json_extract(r.scope,'$.coverageOverlay') IS NULL
  AND r.state IN ('completed','accepted_partial') AND r.identity_unchanged=1
  ORDER BY r.updated DESC LIMIT 1''',(head[0],market)).fetchone()
 if not row:return None
 return {'runId':row[0],'state':row[1],'updatedAt':row[2],'products':row[3],'pages':row[4],
         'categoryCount':row[5],'categoriesCompleted':row[6],'isCurrentHead':bool(row[7])}


def full_products(market,offset=0,query='',snapshot='current'):
 meta=market_record(ROOT,market)
 if meta['capabilities']['fullManagedCatalog'] is not True:
  return {'market':market,'scope':snapshot,'availability':'unsupported' if meta['capabilities']['fullManagedCatalog'] is False else 'unavailable','items':[],'total':0,'offset':offset,'limit':30,'observedAt':None,'readOnly':True,'platformWrites':0}
 path=source_path(market)
 if not path.exists():return {'market':market,'scope':snapshot,'availability':'unavailable','items':[],'total':0,'offset':offset,'limit':30,'observedAt':None,'readOnly':True,'platformWrites':0}
 store=GlobalSources(path,readonly=True)
 try:
  category=category_snapshot(store,market) if snapshot=='category' else None
  overlay={}
  if snapshot=='weekly':
   head=store.db.execute('''SELECT r.scope FROM global_source_head h JOIN global_source_run r
    ON r.id=h.run_id WHERE json_extract(r.scope,'$.market')=?''',(market,)).fetchone()
   overlay=(json.loads(head[0]).get('coverageOverlay') or {}) if head else {}
  selected_run=category['runId'] if category else overlay.get('refreshRunId') if snapshot=='weekly' else None
  if snapshot!='current' and not selected_run:
   return {'market':market,'scope':snapshot,'availability':'unavailable','items':[],'total':0,'offset':offset,'limit':30,'observedAt':None,'readOnly':True,'platformWrites':0}
  result=store.products(id=selected_run,offset=offset,limit=30,query=query)
  if result.get('market')!=market:raise ValueError('market_catalog_scope_mismatch')
  items=[]
  for row in result.get('items',[]):
   items.append({key:row.get(key) for key in ('pid','title','listedSelected','publicCommissionRaw','totalCommissionRaw','detailsChecked','stockChecked','selectionObservation')}
    |{'selectedOffers':[{'creatorPercent':offer.get('creatorPercent'),'eligible':(offer.get('assessment') or {}).get('eligible')} for offer in row.get('selectedOffers',[])[:4]]})
  return {'market':market,'scope':snapshot,'availability':'ready' if result.get('available') else 'unavailable','items':items,'total':int(result.get('totalMatches') or 0),'offset':offset,'limit':30,
          'displayRunId':result.get('displayRunId'),'latestRunId':result.get('id'),
          'observedAt':result.get('updatedAt') if selected_run else (result.get('activePublished') or {}).get('updated') or result.get('updatedAt'),
          'readOnly':True,'platformWrites':0}
 finally:store.close()


def run_decisions(store,market,run_id):
 result={};db=store.db
 screen=db.execute('SELECT run_id,counts FROM global_source_screen_run WHERE source_run=? ORDER BY updated DESC LIMIT 1',(run_id,)).fetchone() if run_id and db.execute("SELECT 1 FROM sqlite_master WHERE name='global_source_screen_run'").fetchone() else None
 if screen:
  counts=(json.loads(screen[1]) or {}).get('states') or {}
  selected=dict(db.execute("""SELECT json_extract(p.payload,'$.fs_is_selected'),count(*)
   FROM global_source_screen s JOIN global_source_product p ON p.run_id=? AND p.pid=s.pid
   WHERE s.run_id=? AND s.state='eligible' GROUP BY 1""",(run_id,screen[0])))
  result['screen']={'eligible':int(counts.get('eligible') or 0),'rejected':int(counts.get('rejected') or 0),
                    'eligibleListedSelected':int(selected.get(1) or 0),'eligibleListedUnselected':int(selected.get(0) or 0)}
 selection=ROOT/('var/global-selection.sqlite' if market=='it' else f'var/global-selection-{market}.sqlite')
 if selection.exists() and run_id:
  with closing(sqlite3.connect(selection.resolve().as_uri()+'?mode=ro',uri=True)) as ledger:
   run=ledger.execute('SELECT id FROM intake_run WHERE source_run=? ORDER BY created DESC LIMIT 1',(run_id,)).fetchone()
   if run:
    from lib.fullmanaged_candidates import selection_rows
    ledger.row_factory=sqlite3.Row
    items=[(r['pid'],r['state']) for r in selection_rows(ledger,run[0])]
    unselected={row[0] for row in db.execute("""SELECT s.pid FROM global_source_screen s
     JOIN global_source_product p ON p.run_id=? AND p.pid=s.pid
     WHERE s.run_id=? AND s.state='eligible' AND json_extract(p.payload,'$.fs_is_selected')=0""",(run_id,screen[0]))} if screen else set()
    items=[r for r in items if r[0] in unselected]
    states=dict(Counter(row[1] for row in items));current_pids={row[0] for row in items}
    missing=unselected-current_pids;previous_unknown=set()
    if missing:
     placeholders=','.join('?'*len(missing))
     previous_unknown={row[0] for row in ledger.execute(f"SELECT DISTINCT pid FROM intake_item WHERE pid IN ({placeholders}) AND state IN ('skipped_unknown','result_unknown')",tuple(missing))}
    result['selection']={'states':states,'selected':int(states.get('confirmed') or 0)+int(states.get('already_selected') or 0),
                         'pending':int(states.get('pending') or 0),'intakeTotal':len(items),
                         'skippedUnknown':sum(int(states.get(k) or 0) for k in ('skipped_unknown','result_unknown','submitting','awaiting_verification','needs_review','isolated_unverified')),
                         'endedWithoutSelection':sum(n for k,n in states.items() if k not in ('confirmed','already_selected','pending','skipped_unknown','result_unknown','submitting','awaiting_verification','needs_review','isolated_unverified')),
                         'carriedUnknown':len(missing & previous_unknown),'untrackedEligible':len(missing-previous_unknown)}
 return result


def full_status(market):
 if not market_record(ROOT,market)['capabilities']['fullManagedCatalog']:
  return {'applicable':False,'available':False,'reason':'not_supported'}
 path=source_path(market)
 if not path.exists():return {'applicable':True,'available':False,'reason':'not_collected'}
 store=GlobalSources(path,readonly=True)
 try:
  value=store.status()
  result={'applicable':True,**{key:value.get(key) for key in (
   'available','id','state','products','pages','reportedTotal','reason','published','coverage',
   'partitionMode','categoryCount','categoriesCompleted','categoryMemberships','categoryOverlap','stableDuplicateRows',
   'listedSelectedProducts','listedUnselectedProducts','detailProducts','operatorAcceptance','coverageOverlay','updatedAt','elapsedSeconds') if key in value}}
  active=value.get('activePublished') or {}
  result['activePublished']={key:active.get(key) for key in ('id','products','updated','state')} if active else None
  result['categorySnapshot']=category_snapshot(store,market)
  from lib.fullmanaged_candidates import candidate_summary
  result['candidates']=candidate_summary(ROOT,market,value.get('id'))
  result.update(run_decisions(store,market,value.get('id')))
  overlay=value.get('coverageOverlay') or {}
  if overlay:
   refresh=store.status(overlay['refreshRunId'])
   result['weeklyRefresh']={'runId':refresh.get('id'),'products':refresh.get('products'),
                            'pages':refresh.get('pages'),'updatedAt':refresh.get('updatedAt'),
                            **run_decisions(store,market,refresh.get('id'))}
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


def published_screen(market):
 """Read the published screening ledger without re-evaluating every Offer on a page GET."""
 saved=recorded_screen(ROOT,'campaign',market)
 with closing(sqlite3.connect((ROOT/'var/second-cycle.sqlite').resolve().as_uri()+'?mode=ro',uri=True)) as db:
  head=db.execute('''SELECT h.snapshot_id FROM catalog_head h JOIN plan p ON p.id=h.plan_id
   WHERE p.institution='bjn-local-research' AND p.market=? AND h.source=?''',(market,f'live-{market}-campaign')).fetchone()
 if not saved or not head or saved['snapshot']!=head[0]:
  return {'available':False,'market':market,'source':'campaign',
          'reason':'screen_stale' if saved and head else 'snapshot_missing'}
 counts=saved['counts'];states=counts.get('states') or {};pool_counts=saved.get('poolCounts') or {}
 return {'available':True,'market':market,'source':'campaign','snapshot':saved['snapshot'],
         'runId':saved['runId'],'offers':sum(states.values()),'distinctPids':sum(pool_counts.values()),
         'eligiblePids':int(counts.get('eligiblePids') or 0),
         'multiCampaignPids':int(counts.get('multiCampaignPids') or 0),
         'counts':states,'reasons':counts.get('reasons') or {},'pool':{'counts':pool_counts}}


def status(market):
 meta=market_record(ROOT,market);pair=load_config(ROOT)['markets'][market]
 with CycleStore(ROOT/'var/second-cycle.sqlite',readonly=True) as store:
  supply=pair['roles']['supply'];generation=current_generation(store,market,supply)
  plan=store.db.execute("SELECT id FROM plan WHERE institution='bjn-local-research' AND market=?",(market,)).fetchone()
  downstream={'leadEdges':0,'identityResolved':0,'deliveriesConfirmed':0,'activeTapLinks':0,
              'activeSelectedTapLinks':0,'activeCampaignTapLinks':0,
              'currentSelectedCatalogOffers':None,'currentLeadEdges':None,
              'currentResolvedLeadEdges':None,'currentResolvedCreators':None}
  if plan:
   plan=plan[0]
   downstream['leadEdges']=store.db.execute('SELECT count(*) FROM source_edge WHERE plan_id=?',(plan,)).fetchone()[0]
   downstream['identityResolved']=store.db.execute('SELECT count(*) FROM cycle_identity_resolution WHERE plan_id=?',(plan,)).fetchone()[0]
   selected=store.db.execute("""SELECT json_array_length(c.payload) FROM catalog_head h
    JOIN catalog c ON c.id=h.snapshot_id WHERE h.plan_id=? AND h.source=?""",(plan,f'live-{market}-selected')).fetchone()
   if selected:downstream['currentSelectedCatalogOffers']=selected[0]
   lead_head=store.db.execute('SELECT 1 FROM lead_query_head WHERE plan_id=? LIMIT 1',(plan,)).fetchone()
   if lead_head:
    current=store.db.execute('''SELECT count(*),sum(CASE WHEN r.source_id IS NOT NULL THEN 1 ELSE 0 END),
      count(DISTINCT r.creator_id) FROM lead_query_head h
      JOIN lead_query_selection s ON s.query_id=h.query_id
      LEFT JOIN cycle_identity_resolution r ON r.plan_id=h.plan_id AND r.source_id=s.source_id
      WHERE h.plan_id=?''',(plan,)).fetchone()
    downstream.update(currentLeadEdges=current[0],currentResolvedLeadEdges=current[1] or 0,currentResolvedCreators=current[2])
   if store.db.execute("SELECT 1 FROM sqlite_master WHERE name='cycle_delivery'").fetchone():
    downstream['deliveriesConfirmed']=store.db.execute("SELECT count(*) FROM cycle_delivery WHERE plan_id=? AND state='confirmed'",(plan,)).fetchone()[0]
  links=ROOT/'var/catalog-links.sqlite'
  if links.exists():
   with closing(sqlite3.connect(links.resolve().as_uri()+'?mode=ro',uri=True)) as db:
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='catalog_current_binding'").fetchone():
     for source,count in db.execute("SELECT catalog_source,count(*) FROM catalog_current_binding WHERE market=? AND state='active' GROUP BY catalog_source",(market,)):
      downstream['activeTapLinks']+=count
      if source=='selected':downstream['activeSelectedTapLinks']=count
      elif source=='campaign':downstream['activeCampaignTapLinks']=count
 capabilities={key:value['state'] for key,value in (generation or {}).get('capabilities',{}).items()}
 try:campaign=campaign_status(ROOT,market=market)
 except (OSError,ValueError,sqlite3.Error):campaign={'available':False,'reason':'not_previewed'}
 try:screen=published_screen(market)
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
 parser.add_argument('--snapshot',choices=('current','category','weekly'),default='current')
 args=parser.parse_args()
 try:
  if args.action=='products':
   if not 0<=args.offset<=1000000 or len(args.query)>100:raise ValueError('market_catalog_products_query_invalid')
   print(json.dumps(full_products(args.market,args.offset,args.query,args.snapshot),ensure_ascii=False));return 0
  if args.action=='preview-campaign':campaign_preview(ROOT,market=args.market)
  print(json.dumps(status(args.market),ensure_ascii=False));return 0
 except (OSError,ValueError,sqlite3.Error) as error:
  print(json.dumps({'error':str(error)},ensure_ascii=False));return 2


if __name__=='__main__':raise SystemExit(main())
