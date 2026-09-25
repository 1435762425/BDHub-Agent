"""One persistent market/PID/A-B read queue over existing Kalodata evidence ledgers.

Fragments release the existing workflow resource claim between finite stages. Only completed
PID scopes publish; the queue is never an all-products barrier for identity or sending pools.
"""
import fcntl,json,time
from collections import Counter
from contextlib import closing
from datetime import datetime,timedelta
from pathlib import Path
import sqlite3

from lib.second_cycle import CycleStore,CycleError,digest,encoded
from lib.video_window import BEIJING

POLICIES={'A':'rolling-a50-v1','B':'rolling-video30-v1'}
REFRESH_SECONDS=7*86400
FRAGMENT_REQUESTS=3
STAGE_FRAGMENTS=8
MAX_FAILURES=3
RETRY_SECONDS=(300,1800,7200)
QUOTA_CHECK_SECONDS=3600
MARKET_WAITS={'kalodata_daily_quota_exhausted':3600,'kalodata_auth_required':3600,
              'browser_lock_busy':60,'account_in_use':60,'kalodata_initialization_failed':3600,
              'kalodata_service_unavailable':300}


def windows(at,kind):
    end=datetime.fromtimestamp(at,BEIJING).date()-timedelta(days=2)
    return str(end-timedelta(days=13 if kind=='A' else 29)),str(end)


def _event(db,row,state,at,**detail):
    db.execute('INSERT INTO lead_query_task_event(market,pid,kind,query_id,state,detail_json,at) VALUES(?,?,?,?,?,?,?)',
               (row['market'],row['pid'],row['kind'],row['query_id'],state,encoded(detail),at))


def material_scope(root,market,at):
    from lib.leads_queue import eligible_products
    from lib.second_cycle import assess_offer
    from lib.catalog_binding import offer_fingerprint
    from lib.link_naming import load as naming_policy
    products=eligible_products(root,market)
    path=Path(root)/'var/catalog-links.sqlite'
    if not path.exists():return {}
    with CycleStore(Path(root)/'var/second-cycle.sqlite',readonly=True) as store:
        plan=store.db.execute("SELECT id FROM plan WHERE market=? AND institution='bjn-local-research'",(market,)).fetchone()
        if not plan:return {}
        qualified=set()
        for _,offer in store._offers(plan[0]):
            if assess_offer(offer,at)['eligible']:
                qualified.add((str(offer.get('catalogSource')),str(offer['pid']),str(offer.get('campaignId')),offer_fingerprint(offer)))
    naming_version=naming_policy(root,market)['version']
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as db:
        db.row_factory=sqlite3.Row
        rows=db.execute("SELECT catalog_source,pid,campaign_id,list_id,offer_fingerprint,verified_at FROM catalog_current_binding WHERE market=? AND state='active' AND commission_rule_version='commission-1-to-2-v1' AND naming_rule_version=?",(market,naming_version)).fetchall()
        events=dict(db.execute("SELECT pid,min(observed_at) FROM catalog_current_binding_event WHERE market=? AND state='active' GROUP BY pid",(market,)))
    grouped={}
    for row in rows:
        pid=row['pid']
        if pid not in products or (row['catalog_source'],pid,row['campaign_id'],row['offer_fingerprint']) not in qualified:continue
        item=grouped.setdefault(pid,{'units':int(products[pid].get('units') or 0),'readyAt':events.get(pid,row['verified_at']),'bindings':[]})
        item['bindings'].append((row['list_id'],row['offer_fingerprint']))
    for item in grouped.values():item['key']=digest(sorted(item.pop('bindings')))
    return grouped


def _legacy_a(root,market,pid,at):
    path=Path(root)/('var/kalodata-leads.sqlite' if market=='it' else f'var/kalodata-leads-{market}.sqlite')
    if not path.exists():return None
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='leads_page_scope'").fetchone():return None
        pages=db.execute('SELECT query_id,payload FROM leads_page_scope WHERE pid=?',(pid,)).fetchall()
    grouped={}
    for qid,raw in pages:grouped.setdefault(qid,[]).append(json.loads(raw))
    with CycleStore(Path(root)/'var/second-cycle.sqlite',readonly=True) as store:
        published={r[0] for r in store.db.execute('SELECT query_id FROM lead_query_run WHERE pid=?',(pid,))}
    active=[]
    for qid,receipts in grouped.items():
        if qid in published or any(r.get('done') for r in receipts):continue
        edges=[e for receipt in receipts for e in receipt.get('edges',[])]
        pairs={(e.get('windowStart'),e.get('windowEnd')) for e in edges}
        if not pairs:
            # Recover an empty-edge legacy page only by verifying its exact original query hash.
            for days in range(90):
                start,end=windows(at-days*86400,'A')
                if qid=='leads-'+digest(['leads-queue',market,pid,start,end])[:28]:
                    pairs={(start,end)};break
        if len(pairs)!=1:continue  # Never splice unverifiable or conflicting dates.
        start,end=next(iter(pairs));created=min((e.get('observedAt',at) for e in edges),default=at)
        if start and end:active.append((end,created,qid,start))
    if not active:return None
    end,created,qid,start=max(active)
    return {'query':qid,'start':start,'end':end,'created':created,'policy':'legacy-a20'}


def sync(root,market,*,at=None,scope=None):
    if market not in ('it','br','my','uk'):raise CycleError('lead_queue_scope_invalid')
    at=time.time() if at is None else at
    scope=material_scope(root,market,at) if scope is None else scope
    with CycleStore(Path(root)/'var/second-cycle.sqlite') as store:
        db=store.db
        old={ (r['pid'],r['kind']):r for r in db.execute('SELECT * FROM lead_query_task WHERE market=?',(market,))}
        # Read legacy A checkpoints outside the publication transaction.
        legacy={pid:_legacy_a(root,market,pid,at) for pid in scope if (pid,'A') not in old}
        with store.tx():
            db.execute('INSERT OR IGNORE INTO lead_query_market(market,updated_at) VALUES(?,?)',(market,at))
            db.execute('UPDATE lead_query_market SET scope_checked_at=? WHERE market=?',(at,market))
            service_order=db.execute('SELECT service_counter FROM lead_query_market WHERE market=?',(market,)).fetchone()[0]
            for pid,material in scope.items():
                for kind in ('A','B'):
                    row=old.get((pid,kind))
                    if row is None:
                        prior=legacy.get(pid) if kind=='A' else None
                        if kind=='B':
                            pending=db.execute("""SELECT g.generation_id,g.window_start,g.window_end,g.created_at,j.state
                              FROM kalodata_video_generation g JOIN kalodata_video_generation_scope m ON m.generation_id=g.generation_id
                              JOIN kalodata_video_scan_job j ON j.generation_id=g.generation_id
                              WHERE m.market=? AND j.pid=? AND j.state IN ('listing','detailing')
                              ORDER BY g.created_at DESC LIMIT 1""",(market,pid)).fetchone()
                            fresh_head=db.execute("SELECT r.observed_at FROM kalodata_video_head h JOIN kalodata_video_run r ON r.run_id=h.run_id WHERE h.market=? AND h.pid=? AND r.coverage='complete'",(market,pid)).fetchone()
                            if pending and (pending[4]!='queued' or not fresh_head or fresh_head[0]+REFRESH_SECONDS<=at):prior={'query':pending[0],'start':pending[1],'end':pending[2],'created':pending[3],'policy':POLICIES[kind]}
                        complete=None
                        if kind=='B' and not prior:
                            done=db.execute('''SELECT r.observed_at FROM kalodata_video_head h JOIN kalodata_video_run r ON r.run_id=h.run_id
                              WHERE h.market=? AND h.pid=? AND r.coverage='complete' ''',(market,pid)).fetchone()
                            complete=done[0] if done else None
                        if kind=='A' and not prior:
                            done=db.execute('SELECT r.published_at,r.policy_version FROM lead_query_head h JOIN lead_query_run r ON r.query_id=h.query_id JOIN plan p ON p.id=h.plan_id WHERE p.market=? AND h.pid=?',(market,pid)).fetchone()
                            if done:complete=done[0]
                        is_current=(kind=='B' or done and done[1]==POLICIES['A']) if not prior else False
                        state='waiting' if is_current and complete is not None and complete+REFRESH_SECONDS>at else 'queued'
                        ready=(complete+REFRESH_SECONDS if is_current else min(complete+REFRESH_SECONDS,at)) if complete is not None else min(float(material['readyAt']),at)
                        db.execute('''INSERT INTO lead_query_task(market,pid,kind,state,query_id,window_start,window_end,
                          policy_version,ready_at,service_order,units,material_key,last_completed,next_due,created_at,updated_at)
                          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(market,pid,kind,state,(prior or {}).get('query'),
                          (prior or {}).get('start'),(prior or {}).get('end'),(prior or {}).get('policy',POLICIES[kind]),
                          ready,service_order,material['units'],material['key'],complete,complete+REFRESH_SECONDS if complete else None,
                          (prior or {}).get('created',at),at))
                    else:
                        state=row['state'];reset=False
                        if state=='material_paused':
                            state='waiting' if not row['query_id'] and row['next_due'] is not None and row['next_due']>at else 'queued';reset=bool(row['query_id'] and at-row['created_at']>REFRESH_SECONDS)
                        elif state in ('waiting','isolated') and (row['next_due'] or at+1)<=at:
                            state='queued';reset=True
                        elif state=='isolated' and row['material_key']!=material['key']:
                            state='queued';reset=True
                        if reset:
                            _event(db,row,'superseded_read_scope',at,reason='new_window_or_material',windowEnd=row['window_end'])
                            db.execute('''UPDATE lead_query_task SET query_id=NULL,window_start=NULL,window_end=NULL,
                              attempts=0,retry_at=0,last_error=NULL,request_scope_json='{}',policy_version=?,created_at=?,ready_at=?,service_order=?
                              WHERE market=? AND pid=? AND kind=?''',(POLICIES[kind],at,row['next_due'] or row['ready_at'],service_order,market,pid,kind))
                        if reset or (state,material['units'],material['key'])!=(row['state'],row['units'],row['material_key']):
                            db.execute('UPDATE lead_query_task SET state=?,units=?,material_key=?,updated_at=? WHERE market=? AND pid=? AND kind=?',
                                       (state,material['units'],material['key'],at,market,pid,kind))
            for row in old.values():
                if row['pid'] not in scope and row['state']!='material_paused':
                    _event(db,row,'material_paused',at)
                    db.execute("UPDATE lead_query_task SET state='material_paused',updated_at=? WHERE market=? AND pid=? AND kind=?",(at,market,row['pid'],row['kind']))
    return status(root,market,at=at)


def choose(root,market,*,at=None,kind=None):
    at=time.time() if at is None else at
    with CycleStore(Path(root)/'var/second-cycle.sqlite') as store,store.tx():
        m=store.db.execute('SELECT * FROM lead_query_market WHERE market=?',(market,)).fetchone()
        if not m or m['retry_at']>at:return None
        kinds=[kind] if kind else [m['next_kind'],'B' if m['next_kind']=='A' else 'A']
        for selected in kinds:
            phase_key='next_a_phase' if selected=='A' else 'next_b_phase'
            preferred=m[phase_key]
            for phase in (preferred,'new' if preferred=='resume' else 'resume'):
                comparator='IS NOT NULL' if phase=='resume' else 'IS NULL'
                order='service_order,ready_at,units DESC,pid' if phase=='resume' else 'ready_at,units DESC,pid'
                row=store.db.execute("SELECT * FROM lead_query_task WHERE market=? AND kind=? "
                    "AND state IN ('queued','running','backoff') AND retry_at<=? AND query_id "+comparator+
                    " ORDER BY "+order+" LIMIT 1",(market,selected,at)).fetchone()
                if row:
                    store.db.execute("UPDATE lead_query_task SET served_at=?,service_order=?,state='running',updated_at=? WHERE market=? AND pid=? AND kind=?",
                                     (at,m['service_counter']+1,at,market,row['pid'],selected))
                    store.db.execute('UPDATE lead_query_market SET next_kind=?,service_counter=service_counter+1,'+phase_key+'=?,updated_at=? WHERE market=?',
                                     ('B' if selected=='A' else 'A','new' if phase=='resume' else 'resume',at,market))
                    return dict(row)
    return None


def freeze(root,task,*,at):
    from lib.market_registry import market as market_record
    currency=market_record(root,task['market'])['currency']
    scope={'market':task['market'],'region':'GB' if task['market']=='uk' else task['market'].upper(),
           'currency':currency,'limit':20 if task['policy_version']=='legacy-a20' else 50 if task['kind']=='A' else 0,
           'windowDays':14 if task['kind']=='A' else 30}
    frozen=json.loads(task.get('request_scope_json') or '{}')
    if frozen and frozen!=scope:raise CycleError('kalodata_query_scope_changed')
    if not frozen:
        with CycleStore(Path(root)/'var/second-cycle.sqlite') as store,store.tx():
            store.db.execute('UPDATE lead_query_task SET request_scope_json=? WHERE market=? AND pid=? AND kind=?',
                             (encoded(scope),task['market'],task['pid'],task['kind']))
        task=task|{'request_scope_json':encoded(scope)}
    if task['query_id']:return task
    start,end=windows(at,task['kind'])
    if task['kind']=='B':
        from lib.kalodata_video_scan import initialize
        query=initialize(root,[{'pid':task['pid'],'units':task['units']}],start,end,market=task['market'],clock=lambda:at)['generationId']
    else:query='leads-'+digest(['rolling',task['market'],task['pid'],task['policy_version'],start,end])[:28]
    with CycleStore(Path(root)/'var/second-cycle.sqlite') as store,store.tx():
        store.db.execute('UPDATE lead_query_task SET query_id=?,window_start=?,window_end=?,created_at=? WHERE market=? AND pid=? AND kind=?',
                         (query,start,end,at,task['market'],task['pid'],task['kind']))
    return task|{'query_id':query,'window_start':start,'window_end':end,'created_at':at}


def settle(root,task,result,*,at):
    with CycleStore(Path(root)/'var/second-cycle.sqlite') as store,store.tx():
        db=store.db;done=result.get('status')=='completed';due=task['created_at']+REFRESH_SECONDS
        if task['policy_version']=='legacy-a20':due=at
        if done:
            _event(db,task,'completed',at,windowStart=task['window_start'],windowEnd=task['window_end'],result=result)
            db.execute("""UPDATE lead_query_task SET state=CASE WHEN state='material_paused' THEN state ELSE 'waiting' END,query_id=NULL,last_completed=?,next_due=?,ready_at=?,
              attempts=0,retry_at=0,last_error=NULL,updated_at=? WHERE market=? AND pid=? AND kind=? AND query_id=?""",
              (at,due,due,at,task['market'],task['pid'],task['kind'],task['query_id']))
        else:
            db.execute("UPDATE lead_query_task SET state=CASE WHEN state='material_paused' THEN state ELSE 'queued' END,updated_at=? WHERE market=? AND pid=? AND kind=? AND query_id=?",(at,task['market'],task['pid'],task['kind'],task['query_id']))
        if result.get('networkRequests',0)>0:
            db.execute("UPDATE lead_query_market SET state='ready',retry_at=0,available_at=?,last_error=NULL,quota_checks=0,updated_at=? WHERE market=?",(at,at,task['market']))


def failed(root,task,code,*,at):
    with CycleStore(Path(root)/'var/second-cycle.sqlite') as store,store.tx():
        db=store.db
        if code in MARKET_WAITS:
            quota=code=='kalodata_daily_quota_exhausted'
            delay=MARKET_WAITS[code]
            db.execute('UPDATE lead_query_market SET state=?,retry_at=?,quota_at=CASE WHEN ? THEN ? ELSE quota_at END,quota_checks=quota_checks+?,last_error=?,updated_at=? WHERE market=?',
              ('waiting_quota' if quota else 'waiting_account',at+delay,int(quota),at,int(quota),code,at,task['market']))
            db.execute("UPDATE lead_query_task SET state='queued',updated_at=? WHERE market=? AND pid=? AND kind=?",(at,task['market'],task['pid'],task['kind']))
            if quota:
                phase_key='next_a_phase' if task['kind']=='A' else 'next_b_phase'
                db.execute('UPDATE lead_query_market SET next_kind=?,'+phase_key+"='resume' WHERE market=?",(task['kind'],task['market']))
        else:
            attempts=task['attempts']+1;isolated=attempts>=MAX_FAILURES
            db.execute('''UPDATE lead_query_task SET state=?,attempts=?,retry_at=?,next_due=?,last_error=?,updated_at=?
              WHERE market=? AND pid=? AND kind=?''',('isolated' if isolated else 'backoff',attempts,
              at+RETRY_SECONDS[min(attempts-1,len(RETRY_SECONDS)-1)],at+REFRESH_SECONDS if isolated else task['next_due'],
              code,at,task['market'],task['pid'],task['kind']))
        _event(db,task,'read_failed',at,code=code)


def status(root,market,*,at=None):
    if market not in ('it','br','my','uk'):raise CycleError('lead_queue_scope_invalid')
    at=time.time() if at is None else at
    with CycleStore(Path(root)/'var/second-cycle.sqlite',readonly=True) as store:
        if not store.db.execute("SELECT 1 FROM sqlite_master WHERE name='lead_query_task'").fetchone():return {'available':False,'market':market}
        rows=list(store.db.execute('SELECT kind,state,query_id,ready_at,retry_at,last_completed,created_at,window_end FROM lead_query_task WHERE market=?',(market,)))
        summary={}
        for kind in ('A','B'):
            selected=[r for r in rows if r['kind']==kind]
            runnable=[r for r in selected if r['state'] in ('queued','running','backoff') and r['retry_at']<=at]
            summary[kind]={'total':len(selected),'states':dict(Counter(r['state'] for r in selected)),
              'runnable':len(runnable),'first':sum(r['last_completed'] is None and r['query_id'] is None for r in runnable),
              'refresh':sum(r['last_completed'] is not None and r['query_id'] is None for r in runnable),
              'checkpoints':sum(r['query_id'] is not None for r in runnable),
              'oldestReadyAt':min((r['ready_at'] for r in runnable),default=None),
              'staleCheckpoints':sum(r['query_id'] is not None and at-r['created_at']>=REFRESH_SECONDS for r in selected),
              'oldestWindowEnd':min((r['window_end'] for r in selected if r['query_id'] and r['window_end']),default=None)}
        m=store.db.execute('SELECT * FROM lead_query_market WHERE market=?',(market,)).fetchone()
        active=store.db.execute('''SELECT r.run_id,r.started_at,s.state FROM workflow_run r
          JOIN workflow_stage_run s ON s.run_id=r.run_id WHERE r.market=? AND r.state IN ('queued','running')
          AND s.stage='kalodata' AND s.state IN ('queued','running') ORDER BY r.started_at DESC LIMIT 1''',(market,)).fetchone()
        if m and m['retry_at']>at:
            for row in summary.values():row['runnable']=0
        enabled=automatic_enabled(root,market) if m else False
        hold=identity_hold(root,market) if m and (Path(root)/'config/market-accounts.json').exists() else None
        return {'automaticEnabled':enabled,'identityHold':hold,'activeRun':dict(active) if active else None,'available':True,'market':market,'types':summary,'control':dict(m) if m else None,'platformWrites':0}


def read_a(root,task,requester,*,max_requests=FRAGMENT_REQUESTS,clock=time.time):
    from lib.leads_queue import Ledger
    from lib.cycle_kalodata import PATH,parse_page
    from lib.lead_selection import publish_query,select_top_leads
    from lib.market_registry import market as market_record
    market=task['market'];pid=task['pid'];query=task['query_id'];network=0
    ledger=Ledger(root,market)
    try:
        with CycleStore(Path(root)/'var/second-cycle.sqlite',readonly=True) as store:
            plan=store.db.execute("SELECT id FROM plan WHERE market=? AND institution='bjn-local-research'",(market,)).fetchone()
        if not plan:raise CycleError('plan_missing')
        currency=json.loads(task['request_scope_json'])['currency'];cursor='';edges=[];fingerprints=[];done=False
        limit=20 if task['policy_version']=='legacy-a20' else 50
        while not done:
            saved=ledger.db.execute('SELECT payload FROM leads_page_scope WHERE query_id=? AND pid=? AND cursor=?',(query,pid,cursor)).fetchone()
            if saved:receipt=json.loads(saved[0])
            else:
                if network>=max_requests:return {'status':'yielded','networkRequests':network}
                page=int(cursor or '1')
                claim={'id':query,'pid':pid,'cursor':cursor,'offer_key':'current-material:'+task['material_key'],
                       'window_start':task['window_start'],'window_end':task['window_end']}
                body=requester(PATH,{'id':pid,'startDate':task['window_start'],'endDate':task['window_end'],
                    'authority':True,'pageSize':50,'pageNo':page,'sort':[{'field':'revenue','type':'DESC'}]})
                network+=1
                receipt=parse_page(body,claim,clock(),max_pages=10000,market=market,currency=currency)
                receipt.update(windowStart=task['window_start'],windowEnd=task['window_end'],market=market)
                with ledger.db:ledger.db.execute('INSERT INTO leads_page_scope VALUES(?,?,?,?)',(query,pid,cursor,encoded(receipt)))
            if any((e.get('pid'),e.get('windowStart'),e.get('windowEnd'),e.get('currency'))!=(pid,task['window_start'],task['window_end'],currency) for e in receipt['edges']):raise CycleError('kalodata_receipt_scope_mismatch')
            if receipt.get('market',market)!=market or receipt.get('windowStart',task['window_start'])!=task['window_start'] or receipt.get('windowEnd',task['window_end'])!=task['window_end']:raise CycleError('kalodata_receipt_scope_mismatch')
            if receipt['rowsFingerprint'] in fingerprints:raise CycleError('kalodata_repeated_page')
            edges.extend(receipt['edges']);fingerprints.append(receipt['rowsFingerprint'])
            enough=len(select_top_leads(edges,limit))>=limit
            done=enough or receipt.get('coverage')=='short_page'
            if receipt.get('done') and not done:raise CycleError('kalodata_page_scope_incomplete')
            cursor=receipt['nextCursor']
        result=publish_query(root,plan_id=plan[0],query_id=query,pid=pid,edges=edges,
             receipt_fingerprints=fingerprints,policy_version=task['policy_version'],limit=limit,
             window_start=task['window_start'],window_end=task['window_end'],at=clock())
        ledger.succeeded_with_pages(pid,query,window_end=task['window_end'],leads=result['selected'],at=clock())
        return {'status':'completed','selected':result['selected'],'networkRequests':network,'coverage':'top50' if limit==50 else 'legacy_top20'}
    finally:ledger.close()


def read_b(root,task,requester,*,max_requests=FRAGMENT_REQUESTS,clock=time.time):
    from lib.kalodata_video_scan import scan_one,_generation
    with CycleStore(Path(root)/'var/second-cycle.sqlite',readonly=True) as store:
        generation=_generation(store,task['query_id'])
        if (generation['market'],generation['window_start'],generation['window_end'])!=(task['market'],task['window_start'],task['window_end']):raise CycleError('video_market_mismatch')
        job=store.db.execute('SELECT state FROM kalodata_video_scan_job WHERE generation_id=? AND pid=?',(task['query_id'],task['pid'])).fetchone()
    if job and job[0]=='completed':return {'status':'completed','networkRequests':0,'cached':True}
    return scan_one(root,task['query_id'],requester,clock=clock,pid=task['pid'],market=task['market'],max_requests=max_requests)


def automatic_enabled(root,market):
    with CycleStore(Path(root)/'var/second-cycle.sqlite',readonly=True) as store:
        row=store.db.execute("SELECT a.automatic_operations_enabled FROM market_automation_setting a JOIN plan p ON p.market=a.market WHERE a.market=? AND p.institution='bjn-local-research' AND p.state='active'",(market,)).fetchone()
        return bool(row and row[0])


def run(root,market,*,fragments=STAGE_FRAGMENTS,kind=None,provider_factory=None,scope=None,
        clock=time.time,stopped=lambda:False,scheduled=False):
    root=Path(root)
    if market not in ('it','br','my','uk') or kind not in (None,'A','B'):raise CycleError('lead_queue_scope_invalid')
    if type(fragments) is not int or not 1<=fragments<=100:raise CycleError('lead_queue_fragment_limit_invalid')
    report={'market':market,'completed':0,'A':0,'B':0,'fragments':0,'networkRequests':0,'errors':[],
            'platformWrites':0,'realSends':0,'sliceComplete':True}
    root.joinpath('var').mkdir(exist_ok=True)
    with (root/'var'/f'leads-rolling-{market}.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return report|{'stopped':'reader_busy'}
        if scheduled and not automatic_enabled(root,market):return report|{'stopped':'automation_paused'}
        sync(root,market,at=clock(),scope=scope)
        for _ in range(fragments):
            if stopped() or scheduled and not automatic_enabled(root,market):
                report['stopped']='automation_paused';break
            task=choose(root,market,at=clock(),kind=kind)
            if not task:break
            task=freeze(root,task,at=clock());report['fragments']+=1
            requests=[0];initialized=False;provider=None
            try:
                if provider_factory:context=provider_factory(root,task)
                elif task['kind']=='A':
                    from lib.batch_source_runtime import kalodata_provider
                    context=kalodata_provider(root,market)
                else:
                    from lib.kalodata_video_transport import live_provider
                    context=live_provider(root,root.parent/'01-BDSystem-V2',task['pid'],task['window_start'],task['window_end'],market)
                with context as provider:
                    initialized=True
                    def request(path,payload):
                        if stopped() or scheduled and not automatic_enabled(root,market):raise CycleError('lead_query_paused')
                        requests[0]+=1
                        return provider.request(path,payload)
                    result=(read_a if task['kind']=='A' else read_b)(root,task,request,clock=clock)
                settle(root,task,result,at=clock())
                if result['status']=='completed':
                    report['completed']+=1;report[task['kind']]+=1
            except Exception as error:
                code=str(error) if isinstance(error,CycleError) else 'browser_lock_busy' if isinstance(error,BlockingIOError) else type(error).__name__
                if not initialized and code not in MARKET_WAITS:code='kalodata_initialization_failed'
                response=getattr(provider,'last_result',None) or (getattr(provider,'diagnostics',[]) or [{}])[-1]
                if int(response.get('http') or 0)>=500:code='kalodata_service_unavailable'
                if code=='lead_query_paused':report['stopped']='automation_paused';break
                failed(root,task,code,at=clock());report['errors'].append({'kind':task['kind'],'pid':task['pid'],'code':code})
                if code in MARKET_WAITS:
                    report['stopped']=code;break
            finally:report['networkRequests']+=requests[0]
        report['queue']=status(root,market,at=clock())
        return report


def due_at(root,market,*,at=None,refresh_scope=True):
    """Local scheduling only; quota readiness is established by the next actual pending read."""
    at=time.time() if at is None else at
    path=Path(root)/'var/second-cycle.sqlite'
    if not path.exists():return None
    with CycleStore(path,readonly=True) as store:
        if not store.db.execute("SELECT 1 FROM sqlite_master WHERE name='lead_query_task'").fetchone():return None
        control=store.db.execute('SELECT * FROM lead_query_market WHERE market=?',(market,)).fetchone()
    if refresh_scope and (not control or control['scope_checked_at']+300<=at):
        with (Path(root)/'var'/f'leads-rolling-{market}.lock').open('a') as lock:
            try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:return at+60
            sync(root,market,at=at)
    with CycleStore(path,readonly=True) as store:
        control=store.db.execute('SELECT * FROM lead_query_market WHERE market=?',(market,)).fetchone()
        row=store.db.execute("""SELECT min(CASE WHEN state IN ('waiting','isolated') THEN next_due ELSE retry_at END)
          FROM lead_query_task WHERE market=? AND state<>'material_paused'""",(market,)).fetchone()
    if not row or row[0] is None:return None
    return max(float(row[0]),float(control['retry_at']) if control else 0)


def identity_hold(root,market):
    """An existing identity failure pauses only that lane until a real recovery is recorded."""
    from lib.market_accounts import load_config
    account=load_config(root)['markets'][market]['roles']['communications']
    with CycleStore(Path(root)/'var/second-cycle.sqlite',readonly=True) as store:
        failure=store.db.execute("""SELECT r.run_id,s.finished_at,s.error_code FROM workflow_stage_run s
          JOIN workflow_run r ON r.run_id=s.run_id WHERE r.market=? AND s.stage='oecid'
          AND s.state IN ('failed','needs_human') ORDER BY s.finished_at DESC LIMIT 1""",(market,)).fetchone()
        if not failure:return None
        success=store.db.execute("SELECT max(s.finished_at) FROM workflow_stage_run s JOIN workflow_run r ON r.run_id=s.run_id WHERE r.market=? AND s.stage='oecid' AND s.state='completed'",(market,)).fetchone()[0]
        refreshed=store.db.execute("SELECT max(published_at) FROM account_identity_generation WHERE market=? AND account=? AND state='published'",(market,account)).fetchone()[0]
        if max(success or 0,refreshed or 0)>=(failure['finished_at'] or 0):return None
        return {'runId':failure['run_id'],'error':failure['error_code'],'failedAt':failure['finished_at']}
