#!/usr/bin/env python3
"""Resolve current market Kalodata handles through the assigned communications account."""
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.dont_write_bytecode=True;sys.path.insert(0,str(ROOT/'scripts'))
from lib.market_identity import pending,run
from lib.market_registry import enabled_market_keys

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=('status','run'));p.add_argument('--market',required=True);p.add_argument('--limit',type=int,default=3);p.add_argument('--profile-canary',action='store_true');a=p.parse_args()
 if a.market not in enabled_market_keys(ROOT) or not 1<=a.limit<=500:p.error('invalid market identity scope')
 try:
  value=pending(ROOT,a.market,a.limit) if a.action=='status' else run(ROOT,a.market,a.limit,profile_canary=a.profile_canary)
  if a.action=='run' and value.get('stopped'):
   print(json.dumps(value|{'error':value['stopped']},ensure_ascii=False));return 2
  print(json.dumps(value,ensure_ascii=False));return 0
 except Exception as error:
  from lib.second_cycle import CycleError
  print(json.dumps({'error':str(error) if isinstance(error,(CycleError,ValueError)) else type(error).__name__},ensure_ascii=False));return 2
if __name__=='__main__':raise SystemExit(main())
