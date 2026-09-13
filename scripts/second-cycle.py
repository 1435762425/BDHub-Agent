#!/usr/bin/env python3
"""Local preparation CLI. No model, TikTok or Kalodata network requests."""
import argparse,json,hashlib,sqlite3
from pathlib import Path
from lib.second_cycle import CycleStore,digest
ROOT=Path(__file__).resolve().parents[1];DB=ROOT/'var/second-cycle.sqlite'

def import_italy(store):
 from lib.second_outreach_source import load_second_source
 source=load_second_source(ROOT);batch=json.loads((ROOT/'var/italy-offline-batch.json').read_text())['batch']
 plan=store.plan('bjn-local-research','it')
 card_path=ROOT/'var/italy-card-facts-20260912-freegrin-first/card-facts.json'
 card=json.loads(card_path.read_text()) if card_path.exists() else {}
 member=card.get('membershipProduct',{});products=[];offer_keys={}
 for item in batch['products']:
  pid=item['pid'];same=member.get('pid')==pid and card.get('bindingVerified') is True
  key=pid+':'+(str(member['campaignId']) if same else 'unknown-offer');offer_keys[pid]=key
  products.append({'pid':pid,'offerKey':key,'title':item['title'],'stock':member.get('stock',{}).get('value') if same else None,'creatorPercent':member.get('creatorCommission',{}).get('percent') if same else None,'publicPercent':member.get('publicCommission',{}).get('percent') if same else None,'endAt':None,'available':None,'rating':None,'evidenceRef':'local-card-observation:'+hashlib.sha256(card_path.read_bytes()).hexdigest() if same else item['source']['ref'],'observationNote':'历史观测；尚缺完整当前方案条件，不能发送'})
 observed=max(p['source']['observedAt'] for p in batch['products'])/1000
 store.publish(plan,'italy-historical-source',observed,products)
 edges=[]
 from datetime import datetime,timezone
 day=lambda ms:datetime.fromtimestamp(ms/1000,timezone.utc).date().isoformat()
 for opportunity in source['opportunities']:
  person=opportunity['currentRecipient']
  for p in opportunity['products']:
   edges.append({'sourceId':digest([source['sourceFingerprint'],opportunity['id'],p['pid']]),'pid':p['pid'],'offerKey':offer_keys[p['pid']],'creatorId':person['creatorId'] if person else None,'oec':person['oecId'] if person else None,'units':p['units'],'evidenceRef':p['sourceRef'],'observedAt':p['observedAt']/1000,'windowStart':day(p['windowStart']),'windowEnd':day(p['windowEnd']),'historicalOwnership':'unverified'})
 store.import_edges(plan,edges)
 # Carry forward an explicit pause from the existing new-project opportunity store.
 legacy_opportunities=ROOT/'var/second-outreach.sqlite'
 if legacy_opportunities.exists():
  with sqlite3.connect(legacy_opportunities.resolve().as_uri()+'?mode=ro',uri=True) as c:
   paused={r[0]:r[1] for r in c.execute("SELECT id,revision FROM second_opportunity WHERE control='paused'")}
  for opportunity in source['opportunities']:
   person=opportunity['currentRecipient']
   if person and opportunity['id'] in paused:
    event='source-pause:'+digest([opportunity['id'],paused[opportunity['id']]])
    if not store.db.execute('SELECT 1 FROM control_event WHERE plan_id=? AND event_id=?',(plan,event)).fetchone():
     row=store.db.execute('SELECT revision FROM relationship WHERE plan_id=? AND creator_id=?',(plan,person['creatorId'])).fetchone()
     store.control(plan,event,row[0],'paused',person['creatorId'])
 return store.status(plan)

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['import-italy','status']);p.add_argument('--db',type=Path,default=DB);args=p.parse_args()
 if args.action=='import-italy' and not args.db.resolve().is_relative_to(ROOT/'var'):p.error('write target must stay in new var')
 if args.action=='status' and not args.db.exists():print(json.dumps({'available':False,'executionAllowed':False}));return
 with CycleStore(args.db,readonly=args.action=='status') as store:
  if args.action=='import-italy':r=import_italy(store)
  else:
   row=store.db.execute("SELECT id FROM plan WHERE institution='bjn-local-research' AND market='it'").fetchone()
   r=store.status(row[0]) if row else {'available':False,'executionAllowed':False}
 print(json.dumps(r,ensure_ascii=False))
if __name__=='__main__':main()
