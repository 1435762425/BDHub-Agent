#!/usr/bin/env python3
"""B-read compatibility entrypoint backed by the persistent market queue; legacy generations remain inspectable."""
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.dont_write_bytecode=True;sys.path.insert(0,str(ROOT/'scripts'))
from lib.rolling_leads import run,status,sync
from lib.kalodata_video_scan import status as generation_status
from lib.second_cycle import CycleError

def main(argv=None):
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['init','run','status'])
 p.add_argument('--market',default='it');p.add_argument('--generation');args=p.parse_args(argv)
 if args.generation:
  if args.action!='status':raise CycleError('legacy_generation_runs_use_rolling_queue')
  result=generation_status(ROOT,args.generation)
 elif args.action=='init':result=sync(ROOT,args.market)
 elif args.action=='run':result=run(ROOT,args.market,kind='B')
 else:result=status(ROOT,args.market)
 print(json.dumps(result,ensure_ascii=False));return 0
if __name__=='__main__':raise SystemExit(main())
