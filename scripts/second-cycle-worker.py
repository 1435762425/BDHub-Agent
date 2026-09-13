#!/usr/bin/env python3
"""Bounded pure HTTP Kalodata worker for new local IT supply jobs. Never sends IM."""
import argparse,fcntl,hashlib,importlib.util,json,os,sys,time
from pathlib import Path
from datetime import date,timedelta
ROOT=Path(__file__).resolve().parents[1];LEGACY=ROOT.parent/'01-BDSystem-V2'
sys.dont_write_bytecode=True;sys.path.insert(0,str(LEGACY));sys.path.insert(0,str(ROOT/'scripts'))
from lib.second_cycle import CycleStore,CycleError
from lib.cycle_kalodata import KalodataWorker,PATH

class HttpProvider:
 def __enter__(self):
  from bdhub import config
  from curl_cffi import requests
  module_path=config.load().kalodata.project_dir/'product_top50/collect.py'
  spec=importlib.util.spec_from_file_location('cycle_kalodata_config',module_path);core=importlib.util.module_from_spec(spec);spec.loader.exec_module(core)
  self.cookie_path=core.COOKIE_PATH;self.before=hashlib.sha256(self.cookie_path.read_bytes()).hexdigest()
  self.headers=core.build_headers(core.read_cookie(),'','IT','EUR');self.headers['referer']='https://www.kalodata.com/product?region=IT&language=zh-CN&currency=EUR';self.proxy=core.load_proxy_url()
  self.session=requests.Session(impersonate='chrome');self.last=0.;self.requests=0;self.diagnostics=[]
  return self
 def __exit__(self,*args):
  self.session.close();self.unchanged=hashlib.sha256(self.cookie_path.read_bytes()).hexdigest()==self.before
 def request(self,path,payload):
  import re
  if path!=PATH or not re.fullmatch(r'[0-9]{19}',str(payload.get('id',''))):raise CycleError('kalodata_endpoint_forbidden')
  if (date.fromisoformat(payload['endDate'])-date.fromisoformat(payload['startDate'])).days!=13:raise CycleError('kalodata_window_invalid')
  time.sleep(max(0,1-(time.monotonic()-self.last)));self.last=time.monotonic();self.requests+=1
  r=self.session.post('https://www.kalodata.com'+PATH,headers=self.headers,json=payload,timeout=25,allow_redirects=False,proxies={'http':self.proxy,'https':self.proxy} if self.proxy else None)
  try:data=r.json()
  except ValueError:data={}
  from bdhub.research.kalodata_worker import business_message
  item={'http':r.status_code,'success':data.get('success') if isinstance(data,dict) else None,'responseFields':sorted(data)[:25] if isinstance(data,dict) else [],'dataType':type(data.get('data')).__name__ if isinstance(data,dict) else None}
  if isinstance(data,dict):
   import re
   for key in ['code','errorCategory']:
    value=data.get(key);item[key]=value if type(value) is int or isinstance(value,str) and re.fullmatch(r'[A-Za-z0-9_.:-]{1,64}',value) else None
   if data.get('success') is not True:item['message']=business_message(data.get('message'))
  self.diagnostics.append(item)
  if r.status_code in (401,403):raise CycleError('kalodata_auth_required')
  if r.status_code!=200:raise CycleError('kalodata_business_rejected')
  if not isinstance(data,dict) or data.get('success') is not True:raise CycleError('kalodata_business_rejected')
  return data

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--prepare-target',type=int,default=0);p.add_argument('--max-pids',type=int,default=2);p.add_argument('--max-steps',type=int,default=3);p.add_argument('--retry-blocked',action='store_true');p.add_argument('--report',type=Path,required=True);a=p.parse_args()
 if not 0<=a.prepare_target<=500 or not 1<=a.max_pids<=5 or not 1<=a.max_steps<=10 or not a.report.resolve().is_relative_to(ROOT/'var') or a.report.exists():p.error('invalid bounded local scope')
 report={'mode':'IT source preparation only','platformWrites':0,'modelCalls':0,'realSends':0,'steps':[],'networkRequests':0,'datePolicy':'legacy Kalodata two-day lag; 14-day window'}
 a.report.parent.mkdir(parents=True,exist_ok=True)
 def save():a.report.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
 save()
 try:
  # Same advisory lock as the old Kalodata reader, opened without creating/writing it.
  with (LEGACY/'data/research/kalodata/.browser.lock').open('rb') as lock:
   fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
   with CycleStore(ROOT/'var/second-cycle.sqlite') as store:
    plan=store.db.execute("SELECT id FROM plan WHERE institution='bjn-local-research' AND market='it'").fetchone()
    if not plan:raise CycleError('plan_missing')
    plan=plan[0]
    blocked=store.db.execute("SELECT count(*) FROM source_job WHERE plan_id=? AND state='blocked'",(plan,)).fetchone()[0]
    if blocked and not a.retry_blocked:raise CycleError('source_blocked_review_required')
    if blocked:
     with store.tx():
      if store._plan(plan)['state']!='active':raise CycleError('plan_paused')
      store.db.execute("UPDATE source_job SET state='queued' WHERE plan_id=? AND state='blocked'",(plan,))
    if a.prepare_target:
     report['preparationPlan']=store.replenish(plan,new_remaining=a.prepare_target,established_capacity=0,max_pids=a.max_pids,window_end=(date.today()-timedelta(days=2)).isoformat())
     report['preparationPlan']['capacityMeaning']='local source stock target; not verified remaining TikTok quota';save()
    with HttpProvider() as provider:
     worker=KalodataWorker(store,provider,owner='kalodata-worker-'+str(os.getpid()))
     for _ in range(a.max_steps):
      result=worker.once(plan)
      if result['status'] in ('completed','checkpointed'):
       from lib.creator_discovery import CreatorDiscoveryStore
       from lib.cycle_identity import IdentityBridge
       with CreatorDiscoveryStore(ROOT/'var') as discovery:
        bridge=IdentityBridge(store,discovery,ROOT/'var/creator-identities.sqlite');bridge.freeze(plan);bridge.dispatch(plan)
      report['steps'].append(result);report['networkRequests']=provider.requests;report['diagnostics']=provider.diagnostics;save()
      if result['status'] in ('idle','blocked'):break
    report['cookieFileUnchanged']=provider.unchanged
    report['state']=store.status(plan)
 except Exception as e:report['error']=str(e) if isinstance(e,CycleError) else type(e).__name__
 save();print(json.dumps({k:report[k] for k in ['steps','networkRequests','realSends']}|{'error':report.get('error'),'cookieFileUnchanged':report.get('cookieFileUnchanged')},ensure_ascii=False))
if __name__=='__main__':main()
