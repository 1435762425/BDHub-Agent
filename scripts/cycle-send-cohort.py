#!/usr/bin/env python3
"""Short shared-auth cohort under the existing user-authorized bulk scope."""
import argparse,fcntl,json,signal,sys,threading
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.dont_write_bytecode=True;sys.path.insert(0,str(ROOT/'scripts'))
from lib.cycle_burst import run_cohort
from lib.second_cycle import CycleError

def main():
 p=argparse.ArgumentParser();p.add_argument('--batch-id',required=True);p.add_argument('--lanes',type=int,choices=(1,2,4),default=2);p.add_argument('--limit',type=int,default=8);a=p.parse_args()
 stopped=threading.Event();signal.signal(signal.SIGTERM,lambda *_:stopped.set());signal.signal(signal.SIGINT,lambda *_:stopped.set())
 with (ROOT/'var/cycle-send.lock').open('a') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  result=run_cohort(a.batch_id,limit=a.limit,lanes=a.lanes,stopped=stopped.is_set)
 print(json.dumps(result),flush=True)
if __name__=='__main__':
 try:main()
 except Exception as e:
  print(json.dumps({'error':getattr(e,'code',None) or (str(e) if isinstance(e,CycleError) else type(e).__name__)}));sys.exit(1)
