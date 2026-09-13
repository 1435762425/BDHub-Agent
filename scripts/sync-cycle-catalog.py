#!/usr/bin/env python3
"""Read-only IT catalog extraction into new local state; no creation or sending."""
import argparse,hashlib,importlib.util,json,os,sys,time
from pathlib import Path
from contextlib import contextmanager
ROOT=Path(__file__).resolve().parents[1];LEGACY=ROOT.parent/'01-BDSystem-V2'
sys.dont_write_bytecode=True;sys.path.insert(0,str(LEGACY));sys.path.insert(0,str(ROOT/'scripts'))
from lib.second_cycle import CycleStore,CycleError,digest
from lib.cycle_catalog import new_state,step,CAMPAIGNS,PRODUCTS,SELECTED

def save(path,data):
 tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n');os.chmod(tmp,0o600);tmp.replace(path)

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source',choices=['campaign','selected'],required=True);p.add_argument('--run',type=Path,required=True);p.add_argument('--max-requests',type=int,default=100);args=p.parse_args()
 path=args.run.resolve()
 if not path.is_relative_to(ROOT/'var') or not 1<=args.max_requests<=150:p.error('invalid local scope')
 path.parent.mkdir(parents=True,exist_ok=True)
 import fcntl,requests
 from bdhub import scheduled_relogin
 from bdhub.hub.markets import identity_for
 from bdhub.enrich.identity_store import load_identity
 from bdhub.send.taplink.transport import account_for
 from bdhub.research.catalog_rules import link_rules_for,engine_for
 from lib.italy_cards import legacy_params
 lock=path.with_suffix('.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 cfg,account=account_for('it','acc6',check_maintenance=False);identity=identity_for('it',account=account,cfg=cfg).require_product_search()
 if not identity.partner_id_is_own:raise CycleError('identity_not_own')
 rule=link_rules_for('it','campaign' if args.source=='campaign' else 'selected')[0]
 scope={'market':'it','account':'acc6','institutionFingerprint':digest(str(identity.im_market_partner_id))}
 state=json.loads(path.read_text()) if path.exists() else new_state(args.source,rule,scope,time.time())
 if state['source']!=args.source or state['scope']!=scope or digest(state['rule'])!=digest(rule):raise CycleError('catalog_run_scope_changed')
 engine=engine_for(rule)
 def publish():
  if state.get('identityFileUnchanged') is not True:raise CycleError('identity_unverified')
  with CycleStore(ROOT/'var/second-cycle.sqlite') as store:
   plan=store.plan('bjn-local-research','it');sid=store.publish(plan,'live-it-'+args.source,state['startedAt'],state['offers'])
   store.project_current_offers(plan)
   state['publishedSnapshot']=sid;save(path,state)
 if state['state']=='completed':
  publish();print(json.dumps({'status':'already_completed','offers':len(state['offers'])}));return
 state['state']='running';state.pop('error',None);save(path,state)
 spec=importlib.util.spec_from_file_location('catalog_guard',ROOT/'scripts/probe-italy-profile.py');guard=importlib.util.module_from_spec(spec);spec.loader.exec_module(guard)
 before=hashlib.sha256(Path(account.headers_json).read_bytes()).hexdigest()
 try:
  with guard.readonly_guard(account):
   headers={k:v for k,v in load_identity(account.headers_json).headers.items() if not k.startswith(':') and k.lower() not in ('host','content-length','origin','referer')};headers.update(origin='https://partner.eu.tiktokshop.com',referer='https://partner.eu.tiktokshop.com/')
   params=legacy_params(identity,account)
   with requests.Session() as session:
    session.trust_env=False;last=[0.0]
    def read(method,endpoint,extra,body):
     if (method,endpoint) not in {('GET',CAMPAIGNS),('GET',PRODUCTS),('POST',SELECTED)}:raise CycleError('catalog_endpoint_forbidden')
     if scheduled_relogin.maintenance_due(account,initialize=False,ignore_retry_throttle=True):raise CycleError('maintenance_due')
     time.sleep(max(0,last[0]+1-time.monotonic()));last[0]=time.monotonic()
     r=session.request(method,identity.host+endpoint,params=params|extra,json=body,headers=headers,timeout=(5,20),allow_redirects=False)
     state.setdefault('attempts',[]).append({'path':endpoint,'http':r.status_code,'at':time.time()})
     if r.status_code!=200 or r.headers.get('bdturing-verify') or r.headers.get('x-tt-system-error')=='3':raise CycleError('catalog_read_blocked')
     data=r.json()
     if type(data.get('code')) is not int or data['code']!=0:raise CycleError('catalog_business_rejected')
     return data,hashlib.sha256(r.content).hexdigest()
    for _ in range(args.max_requests):
     # Apply a page to a copy so malformed pages cannot advance checkpoints.
     next_state=json.loads(json.dumps(state));step(next_state,read,engine.calculate,time.time());next_state['attempts']=state.get('attempts',[]);state=next_state;save(path,state)
     if state['state']=='completed':break
    if state['state']!='completed':state['state']='paused';save(path,state)
 except Exception as e:
  state['state']='blocked';state['error']=str(e) if isinstance(e,CycleError) else type(e).__name__;save(path,state)
 finally:
  state['identityFileUnchanged']=hashlib.sha256(Path(account.headers_json).read_bytes()).hexdigest()==before;save(path,state)
 if state['state']=='completed' and state['identityFileUnchanged']:
  publish()
 print(json.dumps({'status':state['state'],'source':args.source,'requests':state['requests'],'offers':len(state['offers']),'error':state.get('error'),'platformWrites':0},ensure_ascii=False))
if __name__=='__main__':main()
