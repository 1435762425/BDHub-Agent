#!/usr/bin/env python3
"""User-authorized IT intake only; no link creation, Kalodata, IM, or old DB writes."""
import argparse,fcntl,json,signal,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.dont_write_bytecode=True;sys.path.insert(0,str(ROOT/'scripts'))
from lib.global_selection import Selection,assess,choose_campaign,selected_rows
from lib.global_source import clean_product
from lib.global_source_transport import opportunity_selector
STOP=False
def stop(*_):
    global STOP
    STOP=True
def main():
    p=argparse.ArgumentParser();p.add_argument('action',choices=['prepare','execute','verify','status']);p.add_argument('--limit',type=int,default=600);a=p.parse_args()
    if not 1<=a.limit<=600:p.error('limit 1..600')
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    with (ROOT/'var/global-selection.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);ledger=Selection(ROOT);id=ledger.prepare();report={'id':id,'action':a.action,'scope':'IT ACC6 select only','realSends':0,'linkCreates':0,'started':time.time()}
        path=ROOT/'var/global-selection-status.json'
        def save():
            report.update(states=ledger.status(id),elapsedSeconds=round(time.time()-report['started'],2));tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(report,ensure_ascii=False,indent=2));tmp.replace(path)
            print(json.dumps({'id':id,'states':report['states'],'elapsedSeconds':report['elapsedSeconds'],'error':report.get('error')}),flush=True)
        save()
        if a.action in ('prepare','status'):return
        scope={};pending=[i for i in ledger.items(id) if i['state'] in (('pending',) if a.action=='execute' else ('submitting','result_unknown'))][:a.limit]
        current=None
        try:
            with opportunity_selector(report,scope,stopped=lambda:STOP) as t:
                def verify(items):
                    if not items:return
                    rows=selected_rows(t,[i['pid'] for i in items]);matched={}
                    for r in rows:
                        if str((r.get('campaign_info') or {}).get('crs_campaign_type')) in ('8','9'):
                            pid=str(r['campaign_product']['product_id']);matched.setdefault(pid,[]).append({'pid':pid,'campaignId':str(r['campaign_info']['campaign_id']),'type':r['campaign_info']['crs_campaign_type']})
                    for i in items:
                        if i['pid'] in matched:ledger.update(i,'already_selected' if i['state']=='pending' else 'confirmed',selectionEvidence=matched[i['pid']],verifiedAt=time.time())
                    return matched
                for offset in range(0,len(pending),15):
                    if STOP:break
                    chunk=pending[offset:offset+15];verify(chunk)
                    if a.action=='verify':save();continue
                    remaining=[i for i in chunk if i['state']=='pending']
                    if not remaining:save();continue
                    page=t.opportunity_page(1,global_only=True,pids=[i['pid'] for i in remaining]);products=page['products']
                    allowed={i['pid'] for i in remaining}
                    if page['has_more'] or len({p['product_id'] for p in products})!=len(products) or any(p['product_id'] not in allowed for p in products):raise ValueError('fresh_listing_scope_incomplete')
                    fresh={p['product_id']:clean_product(p) for p in products}
                    for i in remaining:
                        if STOP:break
                        current=i;product=fresh.get(i['pid'])
                        if not product or not assess(product)['eligible']:
                            ledger.update(i,'filtered',reason=assess(product or {})['reasons'],freshProduct=product);continue
                        if product.get('fs_is_selected') is True:
                            ledger.update(i,'needs_review',reason='listed_selected_but_not_in_selected_readback');continue
                        campaign=choose_campaign(t.offers(i['pid']),time.time())
                        if not campaign:ledger.update(i,'filtered',reason='no_active_full_managed_offer_with_gap_2');continue
                        cid=str(campaign['campaign']['campaign_id']);scope[i['pid']]=cid;ledger.begin(i,campaign)
                        r=t.select_product(i['pid'],cid)
                        receipt={'http':r.http_status,'code':r.code if type(r.code) is int else None,'verification':r.has_turing,'ambiguous':r.ambiguous,'systemError':r.system_error_3}
                        ledger.update(i,'result_unknown',receipt=receipt)
                        if r.http_status!=200 or r.code!=0 or r.has_turing or r.ambiguous or r.system_error_3:
                            if r.http_status==200 and r.code==10000 and r.has_turing and not r.ambiguous and getattr(t,'_pending_selection_verification',None):
                                # Existing same-account verification protocol, never replay the selection POST.
                                t.resolve_selection_verification(i['pid'],cid)
                                ledger.update(i,'result_unknown',platformVerification='passed')
                                verify([j for j in chunk if j['state'] in ('submitting','result_unknown')]);save()
                                continue
                            verify([i]);save();raise ValueError('selection_receipt_requires_review')
                    attempted=[i for i in chunk if i['state'] in ('submitting','result_unknown')]
                    for delay in (0,1,3):
                        if not attempted:break
                        if delay:time.sleep(delay)
                        verify(attempted);attempted=[i for i in attempted if i['state']!='confirmed']
                    save()
                    if any(i['payload'].get('platformVerification')!='passed' for i in attempted):raise ValueError('selection_readback_unconfirmed')
        except Exception as e:
            if current and current['state']=='submitting':ledger.update(current,'result_unknown',reason='interrupted_after_intent')
            report['error']=str(e) if isinstance(e,ValueError) else type(e).__name__
        finally:save();ledger.db.close()
if __name__=='__main__':main()
