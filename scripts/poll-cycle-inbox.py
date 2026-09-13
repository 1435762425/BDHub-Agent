#!/usr/bin/env python3
"""Read-only IT/ACC6 polling of indexed cycle relationships. No sender/model tools."""
import argparse,fcntl,json,os,signal,sqlite3,sys,time
from contextlib import closing
from pathlib import Path
sys.dont_write_bytecode=True
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from lib.second_cycle import CycleStore
from lib.cycle_inbox import Inbox,inbox_status
from lib.cycle_service import Service
from lib.second_live_runtime import _authenticated
from lib.italy_im_session import ItalyImReadSession
STOP=False

def stop(*_):
 global STOP
 STOP=True

def tick(limit):
 report={'realSends':0,'automaticReplies':0,'processed':0};reader=None
 with CycleStore(ROOT/'var/second-cycle.sqlite') as store:
  inbox=Inbox(store)
  from lib.cycle_agent import AgentEvaluation
  from lib.draft_provider import call_model
  service=Service(store,classifier=lambda contents,background:AgentEvaluation(store).evaluate(contents,call_model,mode='live_classification',background=background))
  plan=store.db.execute("SELECT id FROM plan WHERE institution='bjn-local-research' AND market='it'").fetchone()[0]
  if store._plan(plan)['state']!='active':return {'state':'plan_paused','realSends':0}
  if (ROOT/'var/cycle-inbox.pause').exists():return {'state':'paused','realSends':0}
  with closing(sqlite3.connect((ROOT/'var/it-conversations.sqlite').as_uri()+'?mode=ro',uri=True)) as idx, idx:
   rows=idx.execute("SELECT cid,oec,kind FROM conversation WHERE scope='it:acc6' AND kind=2 ORDER BY cid").fetchall()
  targets=[]
  for cid,oec,kind in rows:
   if not store.db.execute('SELECT 1 FROM relationship WHERE plan_id=? AND oec=?',(plan,oec)).fetchone():continue
   cp=store.db.execute('SELECT checked_at FROM inbox_checkpoint WHERE plan_id=? AND cid=?',(plan,cid)).fetchone()
   targets.append((cp[0] if cp else 0,cid,oec,kind))
  targets.sort();report['indexedTargets']=len(targets)
  def deadline(*_):raise TimeoutError('inbox_deadline')
  signal.signal(signal.SIGALRM,deadline);signal.setitimer(signal.ITIMER_REAL,55)
  try:
   if targets:
    with _authenticated(report,stopped=lambda:STOP) as (_,_,_,auth,maintenance,available):
     reader=ItalyImReadSession(auth,report,maintenance_due=maintenance,stopped=lambda:STOP)
     for _,cid,oec,kind in targets[:limit]:
      if STOP or (ROOT/'var/cycle-inbox.pause').exists() or store._plan(plan)['state']!='active':break
      conv=reader.conversation(cid,oec,conversation_type=kind)
      history=reader.history_summary(conv,include_events=True,include_contents=True)
      result=inbox.ingest(plan,cid,oec,history);service.capture(plan,cid,oec,history.get('contents',[]));report['processed']+=1
      for key in ('added','historical','liveReplies'):report[key]=report.get(key,0)+result[key]
  except Exception as e:report['errorCode']=getattr(e,'code',type(e).__name__)
  finally:
   signal.setitimer(signal.ITIMER_REAL,0)
   if reader:reader.close()
   report['serviceDecisions']=len(service.process_due(plan));report['status']=inbox_status(store,plan);report['checkedAt']=time.time()
 return report

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--worker',action='store_true');p.add_argument('--limit',type=int,default=6);p.add_argument('--interval',type=int,default=60);a=p.parse_args()
 if not 1<=a.limit<=12 or a.interval<30:p.error('limit 1..12; interval >=30')
 signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
 with (ROOT/'var/cycle-inbox.lock').open('a') as lock:
  try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  except BlockingIOError:raise SystemExit('inbox_worker_busy')
  while not STOP:
   r=tick(a.limit);r['workerPid']=os.getpid();r['workerMode']=a.worker
   path=ROOT/'var/cycle-inbox-status.json';tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(r,indent=2)+'\n');tmp.replace(path)
   print(json.dumps({k:r[k] for k in ('processed','added','historical','liveReplies','errorCode','state') if k in r}),flush=True)
   if not a.worker:break
   # Backoff failure; never spin or send to discover quota.
   delay=15 if r.get('errorCode')=='live_guard_busy' else max(a.interval,300) if r.get('errorCode') else a.interval
   until=time.monotonic()+delay
   while not STOP and time.monotonic()<until:time.sleep(max(0,min(1,until-time.monotonic())))
if __name__=='__main__':main()
