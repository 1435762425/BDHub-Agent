#!/usr/bin/env python3
"""Execute one explicitly authorized market card+text delivery canary."""
import argparse,json,re,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.dont_write_bytecode=True;sys.path.insert(0,str(ROOT/'scripts'))
from lib.market_send_canary import run

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--market',required=True,choices=('br','uk'));p.add_argument('--request-id',required=True);p.add_argument('--report',type=Path);a=p.parse_args()
 if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]{7,119}',a.request_id):p.error('invalid request id')
 try:
  value=run(ROOT,a.market,a.request_id)
  if a.report:
   path=a.report.resolve()
   if not path.is_relative_to((ROOT/'var').resolve()) or path.exists():raise ValueError('new report under var required')
   path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
  print(json.dumps(value,ensure_ascii=False));return 0
 except Exception as error:
  from lib.second_cycle import CycleError
  print(json.dumps({'error':str(error) if isinstance(error,(CycleError,ValueError)) else type(error).__name__},ensure_ascii=False));return 2
if __name__=='__main__':raise SystemExit(main())
