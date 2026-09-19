#!/usr/bin/env python3
"""Coordinate authorized catalog, supply, identity and product-card preparation. Sends use their own ledger."""
import argparse,fcntl,json,os,signal,subprocess,sys,time
from pathlib import Path
sys.dont_write_bytecode=True
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from lib.second_cycle import CycleStore
from lib.cycle_scheduler import Scheduler,schedule_status
PYTHON=str(ROOT/'.venv/bin/python');STOP=False;CHILD=None

def stop(*_):
 global STOP
 STOP=True
 if CHILD and CHILD.poll() is None:CHILD.terminate()

def command(stage,run,target):
 report=ROOT/'var/cycle-scheduler'/f'{run}.json'
 if stage.startswith('catalog_'):
  source=stage.split('_',1)[1]
  # Same UTC-day run file resumes interrupted read pages, instead of restarting full fetches.
  report=ROOT/'var/cycle-scheduler'/f'{stage}-{time.strftime("%Y%m%d",time.gmtime())}.json'
  return [PYTHON,str(ROOT/'scripts/sync-cycle-catalog.py'),'--source',source,'--run',str(report),'--max-requests','5'],report
 if stage=='kalodata':return [PYTHON,str(ROOT/'scripts/second-cycle-worker.py'),'--prepare-target',str(target),'--max-pids','5','--max-steps','10','--report',str(report)],report
 if stage=='identity_reconcile':return [PYTHON,str(ROOT/'scripts/second-cycle-identities.py'),'reconcile'],None
 if stage=='materials_check':return [PYTHON,str(ROOT/'scripts/advance-cycle-materials.py'),'--max-cards','3','--report',str(report)],report
 raise ValueError('unknown_stage')

def main():
 global CHILD
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--once',action='store_true');p.add_argument('--target',type=int,default=30);a=p.parse_args()
 if not 1<=a.target<=100:p.error('target 1..100, prepared stock not platform quota')
 signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
 out=ROOT/'var/cycle-scheduler';out.mkdir(exist_ok=True)
 with (out/'worker.lock').open('a') as lock,CycleStore(ROOT/'var/second-cycle.sqlite') as store:
  fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);plan=store.db.execute("SELECT id FROM plan WHERE market='it' AND institution='bjn-local-research'").fetchone()[0];s=Scheduler(store);s.initialize(plan)
  # After an unclean shutdown require process inspection instead of silently duplicating a reader.
  if s.has_running(plan):raise SystemExit('interrupted_stage_needs_process_check')
  while not STOP:
   active=store.db.execute("SELECT 1 FROM sqlite_master WHERE name='cycle_delivery'").fetchone() and store.db.execute("SELECT 1 FROM cycle_delivery WHERE plan_id=? AND state IN ('ready','running','unknown') AND expires>?",(plan,time.time())).fetchone()
   job=None if (out/'pause').exists() else s.claim(plan,('catalog_selected','catalog_campaign') if active else ())
   if job:
    args,report=command(job['stage'],job['run_id'],a.target);success=False;result={}
    with (out/f"{job['run_id']}.log").open('w') as log:
     try:
      CHILD=subprocess.Popen(args,cwd=ROOT,stdin=subprocess.DEVNULL,stdout=log,stderr=log);code=CHILD.wait(timeout=150);success=code==0
      if report and report.exists():
       data=json.loads(report.read_text());success=success and not data.get('error')
       if isinstance(data.get('state'),str) and data['state']=='running':success=False
       checkpointed=job['stage'].startswith('catalog_') and data.get('state')=='paused' and not data.get('error')
       if job['stage']=='kalodata' and any(v.get('status')=='blocked' for v in data.get('steps',[])):success=False
      result={'exitCode':code,'report':str(report) if report else None,'realSends':0,'checkpointed':checkpointed if report and report.exists() else False,'reason':data.get('error') if report and report.exists() else None}
     except subprocess.TimeoutExpired:
      CHILD.kill();CHILD.wait();result={'reason':'reader_timeout','realSends':0}
     finally:CHILD=None
    s.complete(plan,job['stage'],job['run_id'],success,result)
   tmp=out/'status.tmp';tmp.write_text(json.dumps({'pid':os.getpid(),'checkedAt':time.time(),'deferredForDelivery':bool(active),'stages':schedule_status(store,plan),'realSends':0},indent=2));tmp.replace(out/'status.json')
   if a.once:break
   for _ in range(15):
    if STOP:break
    time.sleep(1)
if __name__=='__main__':main()
