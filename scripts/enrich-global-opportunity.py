#!/usr/bin/env python3
"""Read bounded current campaign/selected facts for discovered products. Full-managed stock is not a gate. No selection or creation."""
import argparse,json,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.dont_write_bytecode=True;sys.path.insert(0,str(ROOT/'scripts'))
from lib.global_source import GlobalSources,GlobalSourceError
from lib.global_source_transport import opportunity_reader,DETAIL,SELECTED
from lib.second_cycle import digest,assess_offer
from lib.cycle_catalog import normalize

def main():
 p=argparse.ArgumentParser();p.add_argument('--run-id',default='it-global-20260914');p.add_argument('--limit',type=int,default=3);a=p.parse_args()
 if not 1<=a.limit<=10:p.error('limit 1..10')
 s=GlobalSources(ROOT/'var/global-source.sqlite');report={'items':[],'platformWrites':0};started=time.time()
 try:
  rows=s.db.execute('SELECT p.* FROM global_source_product p LEFT JOIN global_source_stock st ON st.run_id=p.run_id AND st.pid=p.pid AND st.listing_fingerprint=p.fingerprint WHERE p.run_id=? AND st.pid IS NULL ORDER BY p.first_page,p.pid LIMIT ?',(a.run_id,a.limit)).fetchall()
  with opportunity_reader(report) as t:
   from bdhub.research.catalog_rules import link_rules_for,engine_for
   rule=link_rules_for('it','selected')[0];calculator=engine_for(rule).calculate
   for row in rows:
    pid=row['pid'];r=t._xhr(method='GET',path=DETAIL,params=t._params()|{'product_id':pid},payload=None,write=False);data=t.require_read(r)['data'];details=data.get('product_campaign_detail');s.detail(a.run_id,pid,row['fingerprint'],details)
    cids={str(x['campaign']['campaign_id']) for x in details if str(x.get('campaign',{}).get('crs_campaign_type')) in ('8','9')}
    r=t._xhr(method='POST',path=SELECTED,params=t._params(),payload={'cur_page':1,'page_size':100,'product_ids':[pid],'filter':{'product_source':[],'campaign_type':[],'label_type':[],'product_status':1}},write=False)
    selected=t.require_read(r);products=selected.get('data') or [];total=selected.get('total_num')
    if not isinstance(products,list) or str(total)!=str(len(products)) or any(str((x.get('campaign_product') or {}).get('product_id'))!=pid for x in products):raise GlobalSourceError('selected_read_incomplete')
    evidence='global-selected:'+digest(selected);offers=[]
    for item in products:
     if str((item.get('campaign_info') or {}).get('campaign_id')) in cids:
      o=normalize(item['campaign_product'],item['campaign_info'],'selected',rule,calculator,evidence,time.time());o['assessment']=assess_offer(o,time.time());offers.append(o)
    s.stock(a.run_id,pid,row['fingerprint'],offers,evidence)
    listed=json.loads(row['payload']);report['items'].append({'pid':pid,'listedCampaignId':listed.get('campaign_id'),'currentCampaignIds':sorted(cids),'selectedOffers':len(offers),'eligibleOffers':sum(o['assessment']['eligible'] for o in offers),'state':'selected_verified' if offers else 'selection_or_binding_required'})
 except Exception as e:report['error']=str(e) if isinstance(e,ValueError) else type(e).__name__
 finally:s.close()
 report['seconds']=round(time.time()-started,2);path=ROOT/'var'/f'global-source-enrichment-{int(started)}.json';path.write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps(report,ensure_ascii=False))
if __name__=='__main__':main()
