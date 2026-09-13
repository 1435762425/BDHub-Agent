#!/usr/bin/env python3
"""Collect the exact global-only opportunity source into a new, resumable local pool."""
import argparse,fcntl,json,os,signal,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.dont_write_bytecode=True;sys.path.insert(0,str(ROOT/'scripts'))
from lib.global_source import GlobalSources,GlobalSourceError,list_request
from lib.global_source_transport import opportunity_reader,LIST
STOP=False
def stop(*_):
 global STOP;STOP=True

def main():
 p=argparse.ArgumentParser();p.add_argument('--run-id',default='it-global-20260914');p.add_argument('--pages',type=int,default=15);p.add_argument('--worker',action='store_true');p.add_argument('--retry-boundary-tail',action='store_true');p.add_argument('--status',action='store_true');p.add_argument('--offset',type=int,default=0);p.add_argument('--query',default='');a=p.parse_args()
 db=ROOT/'var/global-source.sqlite'
 if a.status:
  if not db.exists():print(json.dumps({'available':False,'executionAllowed':False}));return
  s=GlobalSources(db,readonly=True)
  try:print(json.dumps(s.products(offset=a.offset,query=a.query),ensure_ascii=False))
  finally:s.close()
  return
 if not 1<=a.pages<=40:p.error('pages must be 1..40 per account turn')
 signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
 with (ROOT/'var/global-source-worker.lock').open('a') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  s=GlobalSources(db)
  try:
   scope=json.loads((ROOT/'var/cycle-catalog-it-20260913/selected.json').read_text())['scope'];s.start(a.run_id,scope)
   if a.retry_boundary_tail:
    previous=json.loads((ROOT/'var/global-source-runtime.json').read_text())
    if previous.get('status',{}).get('id')!=a.run_id:raise GlobalSourceError('boundary_run_mismatch')
    s.retry_boundary_tail(a.run_id,previous.get('lastResponse',{}))
   while not STOP:
    state=s.get(a.run_id)
    if state['state']!='collecting':break
    report={};busy=False
    try:
     with opportunity_reader(report,stopped=lambda:STOP) as t:
      for _ in range(a.pages):
       if STOP:break
       state=s.get(a.run_id)
       if state['state']!='collecting':break
       page=state['next_page']
       for attempt in range(3):
        t.check_stop();r=t._xhr(method='POST',path=LIST,params=t._params(),payload=list_request(page,state['reported_total']),write=False)
        report['lastResponse']={'page':page,'attempt':attempt+1,'http':r.http_status,'code':r.code if type(r.code) is int else None,'verification':r.has_turing,'systemError':r.system_error_3}
        transient=r.http_status in (0,500,502,503,504) or (r.code==100000 and r.system_error_3)
        if not transient or r.has_turing or attempt==2:break
        time.sleep(2*(attempt+1))
       if r.http_status!=200 or r.code!=0 or r.has_turing or r.system_error_3:raise GlobalSourceError('source_remote_rejected')
       s.page(a.run_id,page,t.require_read(r)['data'],request_payload=list_request(page,state['reported_total']))
     s.finish_session(a.run_id,report.get('identityFileUnchanged'))
    except Exception as e:
     code=str(e) if isinstance(e,ValueError) else type(e).__name__;busy=code in ('BlockingIOError','account_in_use')
     if not busy and code!='source_stopped':s.blocked(a.run_id,code)
     report['error']='account_busy' if busy else code
    current=s.status(a.run_id);report.update(status=current,at=time.time());(ROOT/'var/global-source-runtime.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps({'state':current['state'],'products':current['products'],'pages':current['pages'],'error':report.get('error')}),flush=True)
    if not a.worker or current['state']!='collecting':break
    for _ in range(5 if busy else 2):
     if STOP:break
     time.sleep(1)
   if s.status(a.run_id)['published']:
    from lib.cycle_management import sync_full_managed
    sync_full_managed(ROOT/'var/second-cycle.sqlite',db,scope)
   print(json.dumps(s.status(a.run_id),ensure_ascii=False),flush=True)
  finally:s.close()
if __name__=='__main__':main()
