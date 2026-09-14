#!/usr/bin/env python3
"""Bounded real Find lane comparison at an unchanged aggregate 3 QPS."""
import json,sys,time,statistics,subprocess,os,fcntl
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.dont_write_bytecode=True;sys.path.insert(0,str(ROOT/'scripts'))
from lib.creator_discovery import CreatorDiscoveryStore,CreatorDiscoveryWorker
from lib.discovery_cohort import run_cohort
from lib.batch_task_service import TaskService
import importlib.util
spec=importlib.util.spec_from_file_location('lane_benchmark_reconcile',ROOT/'scripts/creator-profile-refresh.py');shared=importlib.util.module_from_spec(spec);spec.loader.exec_module(shared)

def main():
 path=ROOT/'var'/f'identity-lane-benchmark-{int(time.time())}.json'
 result={'startedAt':time.time(),'qps':3,'rounds':[],'realSends':0,'stageStop':None}
 def save():path.write_text(json.dumps(result,ensure_ascii=False,indent=2))
 save()
 with CreatorDiscoveryStore(ROOT/'var') as store,CreatorDiscoveryWorker(store) as worker:
  for lanes in (3,6,9):
   repeat=0
   while repeat<3:
    before=time.monotonic();g=run_cohort(worker,20,lanes=lanes)
    if not g:result['stageStop']='no_claimable_work';save();return
    if g.get('recovered'):
     result.setdefault('recoveries',[]).append(g);save();continue
    raw=json.loads(Path(g['report']).read_text()) if Path(g['report']).exists() else {}
    reconcile=shared.reconcile_cycle(ROOT/'var',store)
    round={'lanes':lanes,'repeat':repeat+1,'id':g['id'],'seconds':round_value(time.monotonic()-before),'targets':g['targets'],'requests':len(raw.get('requests',[])),
           'verified':sum(t.get('status')=='identity_verified' for t in raw.get('targets',[])),'status':raw.get('status'),'reason':raw.get('reason'),'challenges':raw.get('counters',{}).get('challenge_count',0),'cohortSeconds':g['seconds'],'report':g['report']}
    times=sorted(raw.get('requestStartTimes',[]));round['minRequestSpacing']=min((b-a for a,b in zip(times,times[1:])),default=None)
    repeat+=1
    result['rounds'].append(round);save();print(json.dumps(round,ensure_ascii=False),flush=True)
    if raw.get('status')!='completed' or round['challenges']:
     result['stageStop']='validation_or_remote_anomaly';save();return
    time.sleep(1)
 result['finishedAt']=time.time();save();print(json.dumps({'report':str(path),'finished':True}),flush=True)
def round_value(v):return round(v,3)
if __name__=='__main__':
 pidfile=ROOT/'var/identity-worker.pid'
 if pidfile.exists():
  try:os.kill(int(pidfile.read_text()),0)
  except ProcessLookupError:pass
  else:raise SystemExit('Stop the identity worker before this bounded benchmark; it is restored automatically.')
 lock_path=ROOT/'var/identity-stress/benchmark.lock';lock_path.parent.mkdir(exist_ok=True)
 lock=lock_path.open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 try:main()
 finally:
  with (ROOT/'var/identity-worker.log').open('a') as log:
   p=subprocess.Popen([sys.executable,str(ROOT/'scripts/creator-profile-refresh.py'),'worker','--interval','1','--cohort-size','20','--cohort-lanes','3'],cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,start_new_session=True,env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'})
  (ROOT/'var/identity-worker.pid').write_text(str(p.pid));print(json.dumps({'restoredProductionLanes':3,'pid':p.pid}),flush=True)
