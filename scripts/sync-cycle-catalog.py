#!/usr/bin/env python3
"""Read-only IT catalog extraction into new local state; no creation or sending."""
import argparse,json,os,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];LEGACY=ROOT.parent/'01-BDSystem-V2'
sys.dont_write_bytecode=True;sys.path.insert(0,str(LEGACY));sys.path.insert(0,str(ROOT/'scripts'))
from lib.second_cycle import CycleStore,CycleError,digest
from lib.cycle_catalog import new_state,step,CAMPAIGNS,PRODUCTS,SELECTED
from lib.global_source_transport import opportunity_reader,is_account_busy
from lib.market_accounts import catalog_read_account,catalog_scope

def save(path,data):
 tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n');os.chmod(tmp,0o600);tmp.replace(path)

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source',choices=['campaign','selected'],required=True);p.add_argument('--run',type=Path,required=True);p.add_argument('--max-requests',type=int,default=100);p.add_argument('--audit-only',action='store_true');args=p.parse_args()
 path=args.run.resolve()
 if not path.is_relative_to(ROOT/'var') or not 1<=args.max_requests<=150:p.error('invalid local scope')
 path.parent.mkdir(parents=True,exist_ok=True)
 import fcntl
 from bdhub.research.catalog_rules import link_rules_for,engine_for
 lock=path.with_suffix('.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 prior=json.loads(path.read_text()) if path.exists() else None
 account_name=catalog_read_account(ROOT,prior['scope']['account'] if prior else None)
 rule=link_rules_for('it',args.source)[0]
 scope=catalog_scope(ROOT,account_name)
 state=prior or new_state(args.source,rule,scope,time.time())
 if state['source']!=args.source or state['scope']!=scope or digest(state['rule'])!=digest(rule):raise CycleError('catalog_run_scope_changed')
 engine=engine_for(rule)
 def publish():
  if args.audit_only:return
  if state.get('identityFileUnchanged') is not True:raise CycleError('identity_unverified')
  with CycleStore(ROOT/'var/second-cycle.sqlite') as store:
   plan=store.plan('bjn-local-research','it');sid=store.publish(plan,'live-it-'+args.source,state['startedAt'],state['offers'])
   store.project_current_offers(plan)
   state['publishedSnapshot']=sid;save(path,state)
 if state['state']=='completed':
  publish();print(json.dumps({'status':'already_completed','offers':len(state['offers'])}));return
 state['state']='running';state.pop('error',None);save(path,state)
 report={}
 try:
  with opportunity_reader(report,account_name=account_name,extra_read_endpoints={(CAMPAIGNS,'GET'),(PRODUCTS,'GET')}) as transport:
   if report['scope']!=scope:raise CycleError('catalog_actor_scope_mismatch')
   def read(method,endpoint,extra,body):
    if (method,endpoint) not in {('GET',CAMPAIGNS),('GET',PRODUCTS),('POST',SELECTED)}:raise CycleError('catalog_endpoint_forbidden')
    r=transport._xhr(method=method,path=endpoint,params=transport._params()|extra,payload=body,write=False)
    state.setdefault('attempts',[]).append({'account':account_name,'path':endpoint,'http':r.http_status,'code':r.code if type(r.code) is int else None,'at':time.time()})
    data=transport.require_read(r)
    return data,digest(data)
   for _ in range(args.max_requests):
    next_state=json.loads(json.dumps(state));step(next_state,read,engine.calculate,time.time());next_state['attempts']=state.get('attempts',[]);state=next_state;save(path,state)
    if state['state']=='completed':break
   if state['state']!='completed':state['state']='paused';save(path,state)
 except Exception as e:
  busy=is_account_busy(e)
  state['state']='paused' if busy else 'blocked';state['error']='account_busy' if busy else str(e) if isinstance(e,(CycleError,ValueError)) else type(e).__name__;save(path,state)
 finally:
  state['identityFileUnchanged']=report.get('identityFileUnchanged') is True;state['transportReport']=report;save(path,state)
 if state['state']=='completed' and state['identityFileUnchanged']:
  publish()
 print(json.dumps({'status':state['state'],'source':args.source,'account':account_name,'requests':state['requests'],'offers':len(state['offers']),'error':state.get('error'),'platformWrites':0},ensure_ascii=False))
if __name__=='__main__':main()
