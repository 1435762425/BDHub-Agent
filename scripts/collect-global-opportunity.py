#!/usr/bin/env python3
"""Collect the exact global-only opportunity source into a new, resumable local pool."""
import argparse,fcntl,json,os,signal,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.dont_write_bytecode=True;sys.path.insert(0,str(ROOT/'scripts'))
from lib.global_source import GlobalSources,GlobalSourceError,list_request
from lib.global_source_transport import opportunity_reader,LIST,is_account_busy
from lib.market_accounts import catalog_read_account,catalog_scope
STOP=False
def identity_safe(report):return report.get('identityFileUnchanged') is True or report.get('identityFileVerifiedUpdate') is True
def stop(*_):
 global STOP;STOP=True

def main():
 p=argparse.ArgumentParser();p.add_argument('--market',default='it');p.add_argument('--run-id');p.add_argument('--pages',type=int,default=15);p.add_argument('--worker',action='store_true');p.add_argument('--by-category',action='store_true');p.add_argument('--retry-boundary-tail',action='store_true');p.add_argument('--retry-partial-category',action='store_true');p.add_argument('--repair-partial-category',action='store_true');p.add_argument('--repair-partial-query',action='store_true');p.add_argument('--accept-stable-duplicates',action='store_true');p.add_argument('--accept-stable-query-duplicates',action='store_true');p.add_argument('--accept-partial-snapshot',action='store_true');p.add_argument('--resume-after-relogin',action='store_true');p.add_argument('--status',action='store_true');p.add_argument('--offset',type=int,default=0);p.add_argument('--query',default='');p.add_argument('--audit-store',type=Path);a=p.parse_args()
 from lib.market_registry import supports
 if not supports(ROOT,a.market,'fullManagedCatalog'):p.error('market has no full-managed catalog')
 if a.market=='it' and a.by_category and not a.status:
  p.error('it_category_collection_disabled')
 a.run_id=a.run_id or f'{a.market}-global-{time.strftime("%Y%m%d")}'
 db=ROOT/('var/global-source.sqlite' if a.market=='it' else f'var/global-source-{a.market}.sqlite')
 if a.audit_store:
  db=a.audit_store.resolve()
  if not db.is_relative_to(ROOT/'var') or db.exists() or a.worker or a.status or a.pages>3 or a.retry_boundary_tail or a.by_category:p.error('audit requires a new var file and at most 3 pages')
  db.parent.mkdir(parents=True,exist_ok=True)
 if a.status:
  if not db.exists():print(json.dumps({'available':False,'executionAllowed':False}));return
  s=GlobalSources(db,readonly=True)
  try:print(json.dumps(s.products(offset=a.offset,query=a.query),ensure_ascii=False))
  finally:s.close()
  return
 if not 1<=a.pages<=40:p.error('pages must be 1..40 per account turn')
 signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
 runtime_path=ROOT/('var/global-source-runtime.json' if a.market=='it' else f'var/global-source-runtime-{a.market}.json')
 with (db.with_suffix('.lock') if a.audit_store else ROOT/f'var/global-source-worker-{a.market}.lock').open('a') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  s=GlobalSources(db)
  try:
   previous=s.db.execute('SELECT scope FROM global_source_run WHERE id=?',(a.run_id,)).fetchone()
   previous_scope=json.loads(previous[0]) if previous else None
   scope={k:previous_scope[k] for k in ('market','account','institutionFingerprint')} if previous else catalog_scope(ROOT,catalog_read_account(ROOT,market=a.market),a.market)
   previous_partitioned=bool(previous_scope and previous_scope.get('partitionMode')=='category_l1_v1')
   if previous and previous_partitioned!=a.by_category:raise GlobalSourceError('run_mode_changed')
   if a.by_category:
    if previous_scope:
     categories=[{'category_id':row['categoryId'],'name':row['name'],'is_leaf':row['isLeaf']} for row in previous_scope['categories']]
    else:
     category_report={}
     with opportunity_reader(category_report,market=a.market,stopped=lambda:STOP,account_name=scope['account'],wait_seconds=60) as transport:
      if category_report['scope']!=scope:raise GlobalSourceError('collector_actor_scope_mismatch')
      categories=transport.category_children('0')
     if not identity_safe(category_report):raise GlobalSourceError('source_identity_changed')
    s.start_partitioned(a.run_id,scope,categories)
    if s.get(a.run_id)['state']=='blocked' and runtime_path.exists():
     prior_report=json.loads(runtime_path.read_text(encoding='utf-8'));proof=prior_report.get('lastResponse') or {}
     if proof.get('code')==98001004 and proof.get('verification') is False:
      s.retry_blocked_partition(a.run_id,proof)
   else:s.start(a.run_id,scope)
   if a.retry_boundary_tail:
    if a.by_category:raise GlobalSourceError('partition_tail_is_automatic')
    previous=json.loads(runtime_path.read_text())
    if previous.get('status',{}).get('id')!=a.run_id:raise GlobalSourceError('boundary_run_mismatch')
    s.retry_boundary_tail(a.run_id,previous.get('lastResponse',{}))
   if a.retry_partial_category:
    if not a.by_category:raise GlobalSourceError('partial_partition_retry_invalid')
    s.retry_partial_partition(a.run_id)
   if a.repair_partial_category:
    if not a.by_category:raise GlobalSourceError('partial_partition_repair_invalid')
    repair=s.partial_repair_scope(a.run_id);repair_report={}
    with opportunity_reader(repair_report,market=a.market,stopped=lambda:STOP,account_name=scope['account'],wait_seconds=60) as transport:
     for page in repair['pages']:
      request=list_request(page,repair['reportedTotal'],repair['categoryId'])
      for request_attempt in range(3):
       response=transport._xhr(method='POST',path=LIST,params=transport._params(),payload=request,write=False)
       if response.http_status==200 and response.code==0 and not response.has_turing and not response.system_error_3:break
       if response.code!=98001004 or request_attempt==2:break
       time.sleep(2*(request_attempt+1))
      if response.http_status!=200 or response.code!=0 or response.has_turing or response.system_error_3:
       runtime_path.write_text(json.dumps({'error':'partial_repair_remote_rejected','lastResponse':{'page':page,'categoryId':repair['categoryId'],'http':response.http_status,'code':response.code if type(response.code) is int else None,'verification':response.has_turing,'systemError':response.system_error_3},'status':s.status(a.run_id)},ensure_ascii=False,indent=2))
       raise GlobalSourceError('source_remote_rejected')
      result=s.repair_partition_page(a.run_id,repair['categoryId'],page,transport.require_read(response)['data'],request,repair['attempt'])
      if result['complete']:break
    if s.get(a.run_id)['state']=='partial':raise GlobalSourceError('partial_partition_repair_incomplete')
    if s.get(a.run_id)['state']=='completed':s.finish_session(a.run_id,identity_safe(repair_report))
   if a.repair_partial_query:
    if a.by_category or a.worker:raise GlobalSourceError('partial_query_repair_invalid')
    repair=s.partial_query_repair_scope(a.run_id);repair_report={}
    with opportunity_reader(repair_report,market=a.market,stopped=lambda:STOP,account_name=scope['account'],wait_seconds=60) as transport:
     for page in repair['pages']:
      request=list_request(page,repair['reportedTotal'])
      for request_attempt in range(3):
       response=transport._xhr(method='POST',path=LIST,params=transport._params(),payload=request,write=False)
       if response.http_status==200 and response.code==0 and not response.has_turing and not response.system_error_3:break
       if response.code!=98001004 or request_attempt==2:break
       time.sleep(2*(request_attempt+1))
      if response.http_status!=200 or response.code!=0 or response.has_turing or response.system_error_3:
       raise GlobalSourceError('partial_query_repair_remote_rejected')
      result=s.repair_query_page(a.run_id,page,transport.require_read(response)['data'],request,repair['attempt'])
      if result['complete']:break
    if s.get(a.run_id)['state']=='partial':raise GlobalSourceError('partial_query_repair_incomplete')
    s.finish_session(a.run_id,identity_safe(repair_report))
   if a.accept_stable_duplicates:
    if not a.by_category:raise GlobalSourceError('stable_duplicate_evidence_missing')
    s.accept_stable_duplicate_rows(a.run_id)
    if s.get(a.run_id)['state']=='completed':s.finish_session(a.run_id,bool(s.get(a.run_id)['identity_unchanged']))
   if a.accept_stable_query_duplicates:
    if a.by_category or a.worker:raise GlobalSourceError('stable_duplicate_evidence_missing')
    s.accept_stable_query_duplicates(a.run_id)
    s.finish_session(a.run_id,bool(s.get(a.run_id)['identity_unchanged']))
   operator_acceptance=None
   if a.accept_partial_snapshot:
    if not a.by_category or a.worker:raise GlobalSourceError('partial_snapshot_acceptance_invalid')
    operator_acceptance=s.accept_partial_snapshot(a.run_id)
   if a.resume_after_relogin:
    prior_report=json.loads(runtime_path.read_text(encoding='utf-8'));proof=prior_report.get('lastResponse') or {}
    from lib.account_identity import current_generation
    from lib.second_cycle import CycleStore
    with CycleStore(ROOT/'var/second-cycle.sqlite',readonly=True) as identity_store:generation=current_generation(identity_store,a.market,scope['account'])
    if not generation or generation.get('reason')!='relogin':raise GlobalSourceError('relogin_resume_evidence_missing')
    s.resume_partition_after_relogin(a.run_id,proof,generation['publishedAt'])
   report={'operatorAcceptance':operator_acceptance} if operator_acceptance else {}
   while not STOP:
    state=s.get(a.run_id)
    if state['state']!='collecting':break
    report={};busy=False
    try:
     with opportunity_reader(report,market=a.market,stopped=lambda:STOP,account_name=scope['account']) as t:
      if report['scope']!=scope:raise GlobalSourceError('collector_actor_scope_mismatch')
      for _ in range(a.pages):
       if STOP:break
       state=s.get(a.run_id)
       if state['state']!='collecting':break
       partition=s.next_partition(a.run_id) if a.by_category else None
       if a.by_category and partition is None:break
       page=partition['next_page'] if partition else state['next_page']
       request=list_request(page,partition['reported_total'] if partition else state['reported_total'],partition['category_id'] if partition else None)
       for attempt in range(3):
        t.check_stop();r=t._xhr(method='POST',path=LIST,params=t._params(),payload=request,write=False)
        report['lastResponse']={'page':page,'categoryId':partition['category_id'] if partition else None,'attempt':attempt+1,'http':r.http_status,'code':r.code if type(r.code) is int else None,'verification':r.has_turing,'systemError':r.system_error_3}
        transient=r.http_status in (0,500,502,503,504) or (r.code==100000 and r.system_error_3)
        if not transient or r.has_turing or attempt==2:break
        time.sleep(2*(attempt+1))
       if r.http_status!=200 or r.code!=0 or r.has_turing or r.system_error_3:raise GlobalSourceError('source_remote_rejected')
       data=t.require_read(r)['data']
       if a.by_category:s.partition_page(a.run_id,partition['category_id'],page,data,request_payload=request)
       else:s.page(a.run_id,page,data,request_payload=request)
     s.finish_session(a.run_id,identity_safe(report))
    except Exception as e:
     busy=is_account_busy(e)
     code='account_busy' if busy else str(e) if isinstance(e,ValueError) else type(e).__name__
     if not busy and code!='source_stopped':
      s.blocked(a.run_id,code)
      proof=report.get('lastResponse') or {}
      if a.by_category and proof.get('code')==98001004 and proof.get('verification') is False:
       try:s.retry_blocked_partition(a.run_id,proof);code='partition_signature_retry'
       except GlobalSourceError:pass
     report['error']='account_busy' if busy else code
    current=s.status(a.run_id);report.update(status=current,at=time.time());(db.with_suffix('.report.json') if a.audit_store else runtime_path).write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps({'state':current['state'],'account':scope['account'],'products':current['products'],'pages':current['pages'],'categoriesCompleted':current.get('categoriesCompleted'),'categoryCount':current.get('categoryCount'),'nextCategory':current.get('nextCategory'),'error':report.get('error')}),flush=True)
    if not a.worker or current['state']!='collecting':break
    for _ in range(5 if busy else 2):
     if STOP:break
     time.sleep(1)
   observed=s.status(a.run_id);active=(observed.get('activePublished') or {}).get('id')
   effective_run=a.run_id if observed['published'] else None
   if active and active!=a.run_id:
    overlay=s.get(active)['scope'].get('coverageOverlay') or {}
    if overlay.get('refreshRunId')==a.run_id:effective_run=active
   if effective_run and not a.audit_store:
    from lib.cycle_management import sync_full_managed
    sync_full_managed(ROOT/'var/second-cycle.sqlite',db,scope)
   final=s.status(effective_run or a.run_id)
   if not a.audit_store and final['state']!='collecting':
    # Screen at collection. This is the fixed step once a collection turn stops reading: it
    # applies the operator's thresholds to the listings already stored and records one decision
    # per product. It only ever admits, never contacts the platform, and a screening error must
    # not cast doubt on a collection that already succeeded -- so it is reported, not raised.
    try:
     from lib.global_screen import screen_source
     detail=screen_source(db,effective_run or a.run_id,root=ROOT)
     report['screen']={'runId':detail['runId'],'counts':detail['counts'],'reasons':detail['reasons']}
    except Exception as e:
     report['screen']={'error':str(e) if isinstance(e,ValueError) else type(e).__name__}
    report['status']=final
    runtime_path.write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps({'screen':report['screen']},ensure_ascii=False),flush=True)
   print(json.dumps(final,ensure_ascii=False),flush=True)
  finally:s.close()
if __name__=='__main__':main()
