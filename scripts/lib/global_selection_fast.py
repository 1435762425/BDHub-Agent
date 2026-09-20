"""Bounded same-account preparation/selection lanes, shared pacing and serialized verification."""
from concurrent.futures import ThreadPoolExecutor,wait,FIRST_COMPLETED
from threading import Lock
import time
from lib.global_selection import READBACK_DELAYS,assess,choose_campaign,selected_rows
from lib.global_source import clean_product
from lib.global_source_transport import DETAIL

class Gate:
    def __init__(self,qps,clock=time.monotonic,sleep=time.sleep):
        if not 1<=qps<=8:raise ValueError('selection_qps_outside_test_scope')
        self.interval=1/qps;self.clock=clock;self.sleep=sleep;self.lock=Lock();self.next_at=0
    def acquire(self):
        with self.lock:
            delay=self.next_at-self.clock()
            if delay>0:self.sleep(delay)
            self.next_at=self.clock()+self.interval

def receipt(r):return {'http':r.http_status,'code':r.code if type(r.code) is int else None,'verification':r.has_turing,'ambiguous':r.ambiguous,'systemError':r.system_error_3}

def dispatch(pool,lanes,ready,send,stopped):
    """Refill only a completed lane; on any rejection drain before returning."""
    active={};outcomes=[];cursor=0;paused=False
    def submit(lane):
        nonlocal cursor
        item,cid,_,campaign=ready[cursor];cursor+=1
        active[pool.submit(send,lane,item,cid)]=(item,cid,lane,campaign)
    for lane in lanes:
        if cursor<len(ready) and not stopped():submit(lane)
    while active:
        done,_=wait(active,return_when=FIRST_COMPLETED);free=[]
        for future in done:
            entry=active.pop(future);r=future.result();outcomes.append((entry,r));free.append(entry[2])
            if r is None or r.http_status!=200 or r.code!=0 or r.has_turing or r.ambiguous or r.system_error_3:paused=True
        if not paused and not stopped():
            for lane in free:
                if cursor<len(ready):submit(lane)
    return outcomes,ready[cursor:]

def run(ledger,id,t,scope,items,report,save,stopped,*,width=8,qps=8,native_listing=False):
    if not 1<=width<=8:raise ValueError('selection_lanes_outside_test_scope')
    gate=Gate(qps);t._pace=gate.acquire;lanes=[t.fork_lane(gate.acquire) for _ in range(width)]
    initial=report.setdefault('baselineConfirmed',ledger.status(id).get('confirmed',0));started=time.monotonic()
    report.update(mode='same_account_parallel',selectionSource='global_listing' if native_listing else 'campaign_detail',configuredQps=qps,lanes=width)
    report.setdefault('stageMetrics',{})
    def stage(name,seconds,count):
        m=report['stageMetrics'].setdefault(name,{'seconds':0,'items':0});m['seconds']=round(m['seconds']+seconds,3);m['items']+=count
    def refresh_speed():
        report['confirmedThisRun']=ledger.status(id).get('confirmed',0)-initial
        elapsed=time.time()-report['started'] if 'started' in report else time.monotonic()-started
        report['confirmedPerMinute']=round(report['confirmedThisRun']*60/max(.001,elapsed),2)
    def adopt(lane):
        if lane is not t:t.copy_session_from(lane)
        for other in lanes:
            if other is not lane:other.copy_session_from(t)
    def verify(batch):
        pending=[i for i in batch if i['state'] in ('pending','submitting','awaiting_verification','result_unknown')]
        if not pending:return
        start=time.monotonic();rows=selected_rows(t,[i['pid'] for i in pending]);matches={}
        for r in rows:
            if str((r.get('campaign_info') or {}).get('crs_campaign_type')) in ('8','9'):
                pid=str(r['campaign_product']['product_id']);matches.setdefault(pid,[]).append({'pid':pid,'campaignId':str(r['campaign_info']['campaign_id']),'type':r['campaign_info']['crs_campaign_type']})
        for i in pending:
            if i['pid'] in matches:ledger.update(i,'already_selected' if i['state']=='pending' else 'confirmed',selectionEvidence=matches[i['pid']],verifiedAt=time.time())
        stage('readback',time.monotonic()-start,len(pending));refresh_speed();save()
    def read_offer(lane,item):
        try:return lane._xhr(method='GET',path=DETAIL,params=lane._params()|{'product_id':item['pid']},payload=None,write=False)
        except Exception:return None
    def send(lane,item,cid):
        try:return lane.select_product(item['pid'],cid)
        except Exception:return None
    try:
        with ThreadPoolExecutor(max_workers=width) as pool:
            for start_index in range(0,len(items),40):
                if stopped():break
                batch=items[start_index:start_index+40];verify(batch);batch=[i for i in batch if i['state']=='pending'];fresh={}
                began=time.monotonic()
                for j in range(0,len(batch),15):
                    if stopped():break
                    targets={i['pid'] for i in batch[j:j+15]};page=t.opportunity_page(1,global_only=True,pids=list(targets));rows=page['products']
                    if page['has_more'] or len({p['product_id'] for p in rows})!=len(rows) or any(p['product_id'] not in targets for p in rows):raise ValueError('fresh_listing_scope_incomplete')
                    fresh.update({p['product_id']:clean_product(p) for p in rows})
                if stopped():break
                stage('listing',time.monotonic()-began,len(batch));plans=[]
                for item in batch:
                    p=fresh.get(item['pid'])
                    if not p or not assess(p)['eligible']:ledger.update(item,'filtered',reason=assess(p or {})['reasons']);continue
                    if p.get('fs_is_selected') is True:ledger.update(item,'needs_review',reason='listed_selected_but_no_readback');continue
                    plans.append(item)
                # Refresh same-account session state after sequential source reads.
                adopt(t)
                group_size=40 if native_listing else width
                for j in range(0,len(plans),group_size):
                    if stopped():break
                    group=plans[j:j+group_size];began=time.monotonic()
                    futures=[] if native_listing else [pool.submit(read_offer,lanes[k],i) for k,i in enumerate(group)]
                    results=[f.result() for f in futures]
                    if not native_listing:stage('campaign_details',time.monotonic()-began,len(group))
                    challenges=[k for k,r in enumerate(results) if r and r.has_turing]
                    if challenges:
                        lane=lanes[challenges[0]];header=lane._verification_header
                        if not header:raise ValueError('read_verification_header_missing')
                        began=time.monotonic();lane._solve_verification(header);lane.verification_successes+=1;adopt(lane);stage('verification',time.monotonic()-began,1)
                        report.setdefault('verificationDetails',[]).append(dict(getattr(lane,'last_verification',{})))
                        for k in challenges:results[k]=read_offer(lanes[k],group[k])
                    ready=[]
                    for k,item in enumerate(group):
                        if native_listing:
                            cid=str(fresh[item['pid']].get('campaign_id') or '')
                            if not cid.isdigit() or not 10<=len(cid)<=32:raise ValueError('native_listing_campaign_missing')
                            # Native selection reference is not the resulting TapLink campaign binding.
                            campaign={'campaign':{'campaign_id':cid},'selectionSource':'global_listing','freshProduct':fresh[item['pid']]}
                            scope[item['pid']]=cid;ready.append((item,cid,lanes[k%width],campaign));continue
                        r=results[k]
                        if r is None:raise ValueError('parallel_detail_failed')
                        body=t.require_read(r).get('data') or {};entries=body.get('product_campaign_detail')
                        if not isinstance(entries,list):raise ValueError('detail_shape_invalid')
                        campaign=choose_campaign(entries,time.time())
                        if not campaign:ledger.update(item,'filtered',reason='no_active_offer_with_gap_2');continue
                        cid=str(campaign['campaign']['campaign_id']);scope[item['pid']]=cid;ready.append((item,cid,lanes[k],campaign))
                    if stopped():break
                    while ready and not stopped():
                        # Every intent commits before its request is scheduled.
                        for item,cid,lane,campaign in ready:ledger.begin(item,campaign)
                        began=time.monotonic();outcomes,unsent=dispatch(pool,lanes,ready,send,stopped)
                        for item,_,_,_ in unsent:ledger.update(item,'pending',notDispatched=True)
                        stage('selection',time.monotonic()-began,len(outcomes));challenges=[];fatal=False
                        for (item,cid,lane,_),r in outcomes:
                            if r is None:ledger.update(item,'result_unknown',reason='parallel_write_interrupted');fatal=True;continue
                            rec=receipt(r);ok=r.http_status==200 and r.code==0 and not r.has_turing and not r.ambiguous and not r.system_error_3
                            ledger.update(item,'awaiting_verification' if ok else 'result_unknown',receipt=rec)
                            if not ok:
                                if r.http_status==200 and r.code==10000 and r.has_turing and not r.ambiguous and getattr(lane,'_pending_selection_verification',None):challenges.append((item,cid,lane))
                                else:fatal=True
                        # All in-flight requests have returned before verification/session adoption.
                        if challenges and not fatal:
                            item,cid,lane=challenges[0];began=time.monotonic();lane.resolve_selection_verification(item['pid'],cid);adopt(lane);stage('verification',time.monotonic()-began,1)
                            report.setdefault('verificationDetails',[]).append(dict(getattr(lane,'last_verification',{})))
                            for item,_,_ in challenges:ledger.update(item,'result_unknown',platformVerification='passed')
                            verify([i for i in group if i['state']!='pending'])
                        refresh_speed();save()
                        if fatal:verify(batch);raise ValueError('parallel_selection_requires_review')
                        ready=unsent
                for delay in READBACK_DELAYS:
                    remaining=[i for i in batch if i['state'] in ('submitting','awaiting_verification') or (i['state']=='result_unknown' and i['payload'].get('platformVerification')!='passed')]
                    if not remaining:break
                    if delay:time.sleep(delay)
                    verify(remaining)
                unresolved=[i for i in batch if i['state'] in ('submitting','awaiting_verification','result_unknown')]
                for item in unresolved:ledger.update(item,'result_unknown')
                if any(i['payload'].get('platformVerification')!='passed' for i in unresolved):raise ValueError('parallel_readback_unconfirmed')
                refresh_speed();save()
    finally:
        report['laneVerificationAttempts']=report.get('laneVerificationAttempts',0)+sum(getattr(lane,'verification_attempts',0) for lane in lanes)
        report['laneVerificationSuccesses']=report.get('laneVerificationSuccesses',0)+sum(getattr(lane,'verification_successes',0) for lane in lanes)
        for lane in lanes:lane.session.close()
        refresh_speed();save()
