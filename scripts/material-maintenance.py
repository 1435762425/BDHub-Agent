#!/usr/bin/env python3
"""Run source-specific material maintenance.  Link checks are always creates=0 and never delete."""
import argparse,json,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.dont_write_bytecode=True;sys.path.insert(0,str(ROOT/'scripts'))
from lib.material_maintenance import scheduler_state,tick,stop_path  # noqa:E402

def main():
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--worker',action='store_true');parser.add_argument('--once',action='store_true');args=parser.parse_args()
 while True:
  if stop_path(ROOT).exists():break
  try:result=tick(ROOT)
  except Exception as error:result={'error':type(error).__name__,'checkedAt':time.time()}
  print(json.dumps(result,ensure_ascii=False),flush=True)
  if args.once or not args.worker:break
  for _ in range(30):
   if stop_path(ROOT).exists():break
   time.sleep(1)
 print(json.dumps(scheduler_state(ROOT),ensure_ascii=False),flush=True);return 0
if __name__=='__main__':raise SystemExit(main())
