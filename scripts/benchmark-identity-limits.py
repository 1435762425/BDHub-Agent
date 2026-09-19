#!/usr/bin/env python3
"""User-authorized bounded single-rate and multi-account Find stress test."""
import json,os,signal,sqlite3,subprocess,sys,time,uuid,hashlib,argparse,fcntl
from contextlib import closing
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];LEGACY=ROOT.parent/'01-BDSystem-V2'
sys.dont_write_bytecode=True;sys.path.insert(0,str(ROOT/'scripts'))
from lib.legacy_runtime import configure_vendored_bdhub
configure_vendored_bdhub(root=ROOT,legacy_root=LEGACY)
ACCOUNTS=['acc6','acc1','acc8','acc9','acc11'];ACTIVE=[]

def launch(script,args,logfile,pidfile):
 with (ROOT/'var'/logfile).open('a') as log:
  p=subprocess.Popen([sys.executable,str(ROOT/'scripts'/script),*args],cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,start_new_session=True,env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'})
 (ROOT/'var'/pidfile).write_text(str(p.pid))

def main():
 parser=argparse.ArgumentParser();parser.add_argument('--mode',choices=['both','single','multi'],default='both');args=parser.parse_args()
 from bdhub import config
 from bdhub.enrich.pure_http_worker import prepare_collection_accounts
 from bdhub.hub.markets import identity_for
 from lib.second_cycle import digest
 cfg=config.load();prepared,unavailable=prepare_collection_accounts(cfg,config.load_accounts(cfg),market='it',requested_names=ACCOUNTS)
 by_name={a.account.name:a for a in prepared}
 if set(by_name)!=set(ACCOUNTS):raise ValueError('account_not_prepared')
 partner={digest(str(identity_for('it',a.account,cfg).im_market_partner_id)) for a in prepared}
 if len(partner)!=1 or not all(identity_for('it',a.account,cfg).partner_id_is_own for a in prepared):raise ValueError('institution_mismatch')
 token=uuid.uuid4().hex;folder=ROOT/'var/identity-stress'/token;folder.mkdir(parents=True,mode=0o700)
 with closing(sqlite3.connect((ROOT/'var/creator-identities.sqlite').as_uri()+'?mode=ro',uri=True)) as c:
  sample=c.execute("SELECT current_handle,oec_id FROM creator_identity WHERE market='it' AND handle_conflict=0 AND current_handle IS NOT NULL GROUP BY current_handle HAVING count(*)=1 ORDER BY max(handle_observed_us) DESC LIMIT 180").fetchall()
 if len(sample)!=180:raise ValueError('reference_sample_missing')
 targets={a:[{'ref':f'{a}-{i}','externalId':f'{a}-{i}','handle':sample[n*36+i][0]} for i in range(36)] for n,a in enumerate(ACCOUNTS)}
 expected={f'{a}-{i}':sample[n*36+i][1] for n,a in enumerate(ACCOUNTS) for i in range(36)}
 cases={'single-3':{'accounts':['acc6'],'qps':3,'lanes':3},'multi-2':{'accounts':ACCOUNTS[:2],'qps':3,'lanes':3},'multi-5':{'accounts':ACCOUNTS,'qps':3,'lanes':3},'single-5':{'accounts':['acc6'],'qps':5,'lanes':6},'single-8':{'accounts':['acc6'],'qps':8,'lanes':9},'single-12':{'accounts':['acc6'],'qps':12,'lanes':9}}
 if args.mode!='both':cases={k:v for k,v in cases.items() if k=='single-3' or k.startswith(args.mode+'-')}
 manifest={'id':token,'market':'it','cases':cases,'targets':targets,'expectedOecs':expected,'identityFingerprints':{a:hashlib.sha256(Path(by_name[a].account.headers_json).read_bytes()).hexdigest() for a in ACCOUNTS}}
 p=folder/'manifest.json';p.write_text(json.dumps(manifest));p.chmod(0o600)
 result={'id':token,'startedAt':time.time(),'mode':args.mode,'sample':'frozen previously verified handles; not new leads','productionOverrides':False,'realSends':0,'cases':[]}
 out=folder/'summary.json'
 def save():out.write_text(json.dumps(result,ensure_ascii=False,indent=2))
 save();print(json.dumps({'run':token,'report':str(out)}),flush=True)
 for key,case in cases.items():
  started=time.monotonic();jobs=[];native_stop=False
  for account in case['accounts']:
   base=folder/key/account;base.mkdir(parents=True,mode=0o700);file=base/'targets.private.json'
   file.write_text(json.dumps({'market':'it','identityOnly':True,'stressRun':token,'stressCase':key,'targets':targets[account]}));file.chmod(0o600)
   p=subprocess.Popen([sys.executable,str(ROOT/'scripts/probe-italy-profile.py'),'--account',account,'--targets',str(file),'--output',str(base/'output')],cwd=ROOT,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True,env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'})
   jobs.append((account,p,base/'output/report.private.json'));ACTIVE.append(p)
  while any(p.poll() is None for _,p,_ in jobs):
   for _,_,path in jobs:
    if not path.exists():continue
    try:v=json.loads(path.read_text())
    except (OSError,ValueError):continue
    if v.get('status') in ('blocked','bounded_timeout'):
     native_stop=True;(folder/'STOP').touch()
   if time.monotonic()-started>200:
    native_stop=True;(folder/'STOP').touch()
    for _,p,_ in jobs:
     if p.poll() is None:p.terminate()
   time.sleep(.3)
  accounts=[]
  for account,p,path in jobs:
   raw=json.loads(path.read_text()) if path.exists() else {};tt=raw.get('targets',[])
   good=sum(t.get('status')=='identity_verified' and t.get('oecId')==expected.get(t['targetRef']) for t in tt)
   starts=sorted(raw.get('requestStartTimes',[]));spacings=[b-a for a,b in zip(starts,starts[1:])]
   accounts.append({'account':account,'errorTypes':sorted({r.get('errorType') for r in raw.get('requests',[]) if r.get('errorType')}),'status':raw.get('status'),'reason':raw.get('reason'),'verified':good,'targets':36,'requests':raw.get('counters',{}).get('request_count',0),'challenges':raw.get('counters',{}).get('challenge_count',0),'identityFileUnchanged':raw.get('identityFileUnchanged'), 'actualPacedQps':(len(starts)-1)/(starts[-1]-starts[0]) if len(starts)>1 else None,'minRequestSpacing':min(spacings) if spacings else None,'report':str(path)})
  elapsed=time.monotonic()-started;successful=sum(a['verified'] for a in accounts)
  row={'case':key,'configuredQpsPerAccount':case['qps'],'seconds':round(elapsed,3),'verifiedReferences':successful,'referencesPerMinute':round(successful/elapsed*60,2),'accounts':accounts}
  result['cases'].append(row);save();print(json.dumps(row,ensure_ascii=False),flush=True)
  if native_stop or any(a['status']!='completed' or a['identityFileUnchanged'] is not True or a['verified']!=36 for a in accounts):
   result['stopReason']='remote_or_evidence_anomaly';break
  if any(a['challenges']>7 for a in accounts):result['stopReason']='verification_cost_exceeded_20_percent';break
 result['finishedAt']=time.time();save()

def interrupted(*_):raise KeyboardInterrupt()
if __name__=='__main__':
 for file in ('identity-worker.pid','batch-preparation/worker.pid'):
  path=ROOT/'var'/file
  if path.exists():
   try:os.kill(int(path.read_text()),0)
   except ProcessLookupError:pass
   else:raise SystemExit('Stop the two preparation workers before this bounded benchmark; they are restored automatically.')
 lock_path=ROOT/'var/identity-stress/benchmark.lock';lock_path.parent.mkdir(exist_ok=True)
 lock=lock_path.open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 signal.signal(signal.SIGTERM,interrupted)
 try:main()
 finally:
  for p in ACTIVE:
   if p.poll() is None:p.terminate()
  for p in ACTIVE:
   if p.poll() is None:
    try:p.wait(timeout=8)
    except subprocess.TimeoutExpired:os.killpg(p.pid,signal.SIGKILL);p.wait()
  launch('batch-preparation-worker.py',[],'batch-preparation/worker.log','batch-preparation/worker.pid')
  launch('creator-profile-refresh.py',['worker','--interval','1','--cohort-size','20','--cohort-lanes','3'],'identity-worker.log','identity-worker.pid')
  print(json.dumps({'productionRestored':True,'qpsPerAccount':3,'productionAccount':'acc6'}),flush=True)
