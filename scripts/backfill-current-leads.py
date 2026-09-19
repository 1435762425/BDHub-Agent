#!/usr/bin/env python3
"""Check or publish current top-20 lead generations from existing local receipts."""
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.dont_write_bytecode=True;sys.path.insert(0,str(ROOT/'scripts'))
from lib.lead_selection import backfill_receipts  # noqa: E402
def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=('check','apply'));a=p.parse_args()
 try:print(json.dumps(backfill_receipts(ROOT,apply=a.action=='apply'),ensure_ascii=False));return 0
 except Exception as error:
  from lib.second_cycle import CycleError
  print(json.dumps({'error':str(error) if isinstance(error,(CycleError,ValueError)) else 'lead_backfill_unavailable'}));return 2
if __name__=='__main__':raise SystemExit(main())
