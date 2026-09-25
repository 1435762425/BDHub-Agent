#!/usr/bin/env python3
"""Run bounded A/B query slices or inspect the persistent market queue. No platform writes."""
import argparse,json,re,signal,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.dont_write_bytecode=True;sys.path.insert(0,str(ROOT/'scripts'))
from lib.rolling_leads import run,status,sync
STOP=False
def stop(*_):
 global STOP;STOP=True

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['run','status','sync']);p.add_argument('--market',required=True)
 p.add_argument('--fragments',type=int,default=8);p.add_argument('--kind',choices=['A','B']);p.add_argument('--scheduled',action='store_true');a=p.parse_args()
 signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
 if a.action=='run':result=run(ROOT,a.market,fragments=a.fragments,kind=a.kind,scheduled=a.scheduled,stopped=lambda:STOP)
 elif a.action=='sync':result=sync(ROOT,a.market)
 else:result=status(ROOT,a.market)
 print(json.dumps(result,ensure_ascii=False))
if __name__=='__main__':
 try:main()
 except Exception as error:
  code=str(error) if re.fullmatch(r'[a-z][a-z0-9_]{2,100}',str(error)) else 'rolling_leads_internal:'+type(error).__name__
  print(json.dumps({'error':code,'platformWrites':0,'realSends':0}));raise SystemExit(2)
