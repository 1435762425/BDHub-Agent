#!/usr/bin/env python3
"""Collect the exact global-only opportunity source into a new, resumable local pool."""
import argparse,fcntl,json,os,signal,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.dont_write_bytecode=True;sys.path.insert(0,str(ROOT/'scripts'))
from lib.global_source import GlobalSources,GlobalSourceError,list_request
from lib.global_source_transport import opportunity_reader,LIST,is_account_busy
from lib.market_accounts import catalog_read_account,catalog_scope
STOP=False
def stop(*_):
 global STOP;STOP=True

def main():
 p=argparse.ArgumentParser();p.add_argument('--run-id',default='it-global-20260914');p.add_argument('--pages',type=int,default=15);p.add_argument('--worker',action='store_true');p.add_argument('--by-category',action='store_true');p.add_argument('--retry-boundary-tail',action='store_true');p.add_argument('--status',action='store_true');p.add_argument('--offset',type=int,default=0);p.add_argument('--query',default='');p.add_argument('--audit-store',type=Path);a=p.parse_args()
 db=ROOT/'var/global-source.sqlite'
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
 with (db.with_suffix('.lock') if a.audit_store else ROOT/'var/global-source-worker.lock').open('a') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  s=GlobalSources(db)
  try:
   previous=s.db.execute('SELECT scope FROM global_source_run WHERE id=?',(a.run_id,)).fetchone()
   previous_scope=json.loads(previous[0]) if previous else None
   scope={k:previous_scope[k] for k in ('market','account','institutionFingerprint')} if previous else catalog_scope(ROOT,catalog_read_account(ROOT))
   previous_partitioned=bool(previous_scope and previous_scope.get('partitionMode')=='category_l1_v1')
   if previous and previous_partitioned!=a.by_category:raise GlobalSourceError('run_mode_changed')
   if a.by_category:
    if previous_scope:
     categories=[{'category_id':row['categoryId'],'name':row['name'],'is_leaf':row['isLeaf']} for row in previous_scope['categories']]
    else:
     category_report={}
     with opportunity_reader(category_report,stopped=lambda:STOP,account_name=scope['account'],wait_seconds=60) as transport:
      if category_report['scope']!=scope:raise GlobalSourceError('collector_actor_scope_mismatch')
      categories=transport.category_children('0')
     if category_report.get('identityFileUnchanged') is not True:raise GlobalSourceError('source_identity_changed')
    s.start_partitioned(a.run_id,scope,categories)
   else:s.start(a.run_id,scope)
   if a.retry_boundary_tail:
    if a.by_category:raise GlobalSourceError('partition_tail_is_automatic')
    previous=json.loads((ROOT/'var/global-source-runtime.json').read_text())
    if previous.get('status',{}).get('id')!=a.run_id:raise GlobalSourceError('boundary_run_mismatch')
    s.retry_boundary_tail(a.run_id,previous.get('lastResponse',{}))
   while not STOP:
    state=s.get(a.run_id)
    if state['state']!='collecting':break
    report={};busy=False
    try:
     with opportunity_reader(report,stopped=lambda:STOP,account_name=scope['account']) as t:
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
     s.finish_session(a.run_id,report.get('identityFileUnchanged'))
    except Exception as e:
     busy=is_account_busy(e)
     code='account_busy' if busy else str(e) if isinstance(e,ValueError) else type(e).__name__
     if not busy and code!='source_stopped':s.blocked(a.run_id,code)
     report['error']='account_busy' if busy else code
    current=s.status(a.run_id);report.update(status=current,at=time.time());(db.with_suffix('.report.json') if a.audit_store else ROOT/'var/global-source-runtime.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps({'state':current['state'],'account':scope['account'],'products':current['products'],'pages':current['pages'],'categoriesCompleted':current.get('categoriesCompleted'),'categoryCount':current.get('categoryCount'),'nextCategory':current.get('nextCategory'),'error':report.get('error')}),flush=True)
    if not a.worker or current['state']!='collecting':break
    for _ in range(5 if busy else 2):
     if STOP:break
     time.sleep(1)
   if s.status(a.run_id)['published'] and not a.audit_store:
    from lib.cycle_management import sync_full_managed
    sync_full_managed(ROOT/'var/second-cycle.sqlite',db,scope)
   final=s.status(a.run_id)
   if not a.audit_store and final['state']!='collecting':
    # Screen at collection. This is the fixed step once a collection turn stops reading: it
    # applies the operator's thresholds to the listings already stored and records one decision
    # per product. It only ever admits, never contacts the platform, and a screening error must
    # not cast doubt on a collection that already succeeded -- so it is reported, not raised.
    try:
     from lib.global_screen import screen_source
     detail=screen_source(db,a.run_id,root=ROOT)
     report['screen']={'runId':detail['runId'],'counts':detail['counts'],'reasons':detail['reasons']}
    except Exception as e:
     report['screen']={'error':str(e) if isinstance(e,ValueError) else type(e).__name__}
    (ROOT/'var/global-source-runtime.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps({'screen':report['screen']},ensure_ascii=False),flush=True)
   print(json.dumps(final,ensure_ascii=False),flush=True)
  finally:s.close()
if __name__=='__main__':main()
