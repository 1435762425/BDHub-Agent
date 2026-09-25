#!/usr/bin/env python3
"""Authorized market/supply-account full-managed intake; no links, Kalodata or IM writes."""
import argparse,fcntl,json,signal,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.dont_write_bytecode=True;sys.path.insert(0,str(ROOT/'scripts'))
from lib.global_selection import READBACK_DELAYS,Selection,assess,matching_selection_evidence,prioritized_selection_batch,selection_campaign,selected_rows
from lib.global_source import clean_product
from lib.global_source_transport import opportunity_selector
STOP=False
def stop(*_):
    global STOP
    STOP=True
def main():
    p=argparse.ArgumentParser();p.add_argument('action',choices=['prepare','execute','execute-fast','execute-serial','verify','skip-unknown','status']);p.add_argument('--market',default='it');p.add_argument('--limit',type=int,default=600);p.add_argument('--native-listing',action='store_true');p.add_argument('--reconcile-rejections',action='store_true');p.add_argument('--confirm-skip-unknown',action='store_true');p.add_argument('--skip-after-readback',action='store_true');p.add_argument('--canary',action='store_true');p.add_argument('--lanes',type=int,default=8);p.add_argument('--qps',type=int,default=8);p.add_argument('--group-size',type=int,default=40);a=p.parse_args()
    if not 1<=a.limit<=600:p.error('limit 1..600')
    if not 1<=a.lanes<=8 or not 1<=a.qps<=8:p.error('lanes/qps 1..8')
    if not 1<=a.group_size<=100:p.error('group-size 1..100')
    from lib.market_registry import supports
    if not supports(ROOT,a.market,'fullManagedCatalog'):p.error('market has no full-managed catalog')
    if a.canary and (a.market=='it' or a.limit!=1 or a.action not in {'execute','execute-serial','verify'}):p.error('canary requires a non-IT full-managed market, limit 1, execute or verify')
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    suffix='' if a.market=='it' else '-'+a.market
    from lib.market_accounts import load_config
    account=load_config(ROOT)['markets'][a.market]['roles']['supply']
    with (ROOT/f'var/global-selection{suffix}.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);ledger=Selection(ROOT,a.market);id=ledger.prepare();report={'id':id,'action':a.action,'market':a.market,'account':account,'scope':f'{a.market.upper()} {account.upper()} select only','realSends':0,'linkCreates':0,'platformWrites':0,'started':time.time()}
        path=ROOT/f'var/global-selection-status{suffix}.json'
        def save():
            report.update(states=ledger.status(id),elapsedSeconds=round(time.time()-report['started'],2));tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(report,ensure_ascii=False,indent=2));tmp.replace(path)
            print(json.dumps({'id':id,'states':report['states'],'elapsedSeconds':report['elapsedSeconds'],'platformWrites':report.get('platformWrites',0),'error':report.get('error')}),flush=True)
        if a.action=='status':
            print(json.dumps({'id':id,'states':ledger.status(id)}));ledger.db.close();return
        if a.action=='skip-unknown':
            if not a.confirm_skip_unknown:p.error('skip-unknown requires --confirm-skip-unknown')
            report['skippedUnknown']=ledger.skip_unknown(id);save();ledger.db.close();return
        counts=ledger.status(id)
        if a.action.startswith('execute') and counts and set(counts)<={'confirmed','already_selected','filtered'}:
            print(json.dumps({'id':id,'states':counts,'alreadyComplete':True}));ledger.db.close();return
        save()
        if a.action=='prepare':ledger.db.close();return
        scope={};pending=[i for i in ledger.items(id) if i['state'] in (('pending',) if a.action in ('execute','execute-fast','execute-serial') else ('submitting','awaiting_verification','result_unknown'))][:a.limit]
        current=None
        try:
            with opportunity_selector(report,scope,market=a.market,canary=a.canary,stopped=lambda:STOP) as t:
                if a.action in ('execute','execute-fast'):
                    from lib.global_selection_fast import run
                    retried=[]
                    if a.reconcile_rejections or a.action=='execute':
                        from lib.global_selection import reconcile_verification_rejections
                        retried=reconcile_verification_rejections(ledger,id,t,limit=a.limit)
                    pending=prioritized_selection_batch(ledger.items(id),retried,a.limit)
                    operation_pids={i['pid'] for i in pending}
                    run(ledger,id,t,scope,pending,report,save,lambda:STOP,width=a.lanes,qps=a.qps,
                        native_listing=a.native_listing or a.action=='execute',batch_size=a.group_size,
                        skip_unknown_after_readback=a.skip_after_readback)
                    if a.reconcile_rejections or a.action=='execute':
                        for _ in range(2):
                            pending=reconcile_verification_rejections(ledger,id,t,pids=operation_pids)
                            if not pending:break
                            run(ledger,id,t,scope,pending,report,save,lambda:STOP,width=a.lanes,qps=a.qps,
                                native_listing=a.native_listing or a.action=='execute',batch_size=a.group_size,
                                skip_unknown_after_readback=a.skip_after_readback)
                    return
                if a.action=='execute-serial' and a.reconcile_rejections:
                    from lib.global_selection import reconcile_verification_rejections
                    retried=reconcile_verification_rejections(ledger,id,t,limit=a.limit)
                    pending=prioritized_selection_batch(ledger.items(id),retried,a.limit)
                def verify(items):
                    if not items:return
                    rows=selected_rows(t,[i['pid'] for i in items]);matched={}
                    for r in rows:
                        if str((r.get('campaign_info') or {}).get('crs_campaign_type')) in ('8','9'):
                            pid=str(r['campaign_product']['product_id']);matched.setdefault(pid,[]).append({'pid':pid,'campaignId':str(r['campaign_info']['campaign_id']),'type':r['campaign_info']['crs_campaign_type']})
                    for i in items:
                        observed=matched.get(i['pid'],[]);exact=matching_selection_evidence(i,observed)
                        if exact:ledger.update(i,'already_selected' if i['state']=='pending' else 'confirmed',selectionEvidence=exact,verifiedAt=time.time())
                        elif i['state'] in ('submitting','awaiting_verification','result_unknown'):
                            if observed:ledger.update(i,i['state'],otherCampaignObserved=observed)
                            else:ledger.record_readback_absence(i)
                    return matched
                if a.action=='verify':
                    for offset in range(0,len(pending),15):verify(pending[offset:offset+15]);save()
                    return
                operation_pids={i['pid'] for i in pending};serial_queue=pending
                for _serial_round in range(3 if a.reconcile_rejections else 1):
                    for offset in range(0,len(serial_queue),15):
                        if STOP:break
                        chunk=serial_queue[offset:offset+15];verify(chunk)
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
                            campaign=selection_campaign(t,product,time.time(),native_listing=a.native_listing)
                            if not campaign:ledger.update(i,'filtered',reason='no_active_full_managed_offer_with_gap_2');continue
                            cid=str(campaign['campaign']['campaign_id']);scope[i['pid']]=cid;ledger.begin(i,campaign)
                            r=t.select_product(i['pid'],cid)
                            receipt={'http':r.http_status,'code':r.code if type(r.code) is int else None,'verification':r.has_turing,'ambiguous':r.ambiguous,'systemError':r.system_error_3}
                            ledger.update(i,'result_unknown',receipt=receipt)
                            if r.http_status!=200 or r.code!=0 or r.has_turing or r.ambiguous or r.system_error_3:
                                if r.http_status==200 and r.code==10000 and r.has_turing and not r.ambiguous and getattr(t,'_pending_selection_verification',None):
                                    # Solve once on the shared batch session.  The rejected PID is
                                    # reconciled after all untouched PIDs have been submitted.
                                    t.resolve_selection_verification(i['pid'],cid)
                                    ledger.update(i,'result_unknown',platformVerification='passed')
                                    verify([i]);save();continue
                                verify([i]);save();raise ValueError('selection_receipt_requires_review')
                        verify([i for i in chunk if i['state'] in ('submitting','result_unknown')]);save()
                    if STOP:break
                    if not a.reconcile_rejections:break
                    from lib.global_selection import reconcile_verification_rejections
                    serial_queue=reconcile_verification_rejections(ledger,id,t,pids=operation_pids)
                    if not serial_queue:break
                unsettled=[i for i in ledger.items(id) if i['pid'] in operation_pids and i['state'] in ('submitting','awaiting_verification','result_unknown')]
                for delay in READBACK_DELAYS:
                    if not unsettled:break
                    if delay:time.sleep(delay)
                    verify(unsettled);unsettled=[i for i in unsettled if i['state'] not in ('confirmed','already_selected')]
                save()
                if any(i['payload'].get('platformVerification')!='passed' for i in unsettled):raise ValueError('selection_readback_unconfirmed')
        except Exception as e:
            if current and current['state']=='submitting':ledger.update(current,'result_unknown',reason='interrupted_after_intent')
            report['error']=str(e) if isinstance(e,ValueError) else type(e).__name__
        finally:save();ledger.db.close()
if __name__=='__main__':main()
