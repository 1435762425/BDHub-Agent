#!/usr/bin/env python3
"""Diagnose legacy inbox gaps (read-only) or re-enter one into the resumable backfill (--confirm)."""
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.dont_write_bytecode=True;sys.path.insert(0,str(ROOT/'scripts'))
from lib.cycle_inbox import Inbox
from lib.inbox_gap import diagnose,recover
from lib.second_cycle import CycleError,CycleStore

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=('diagnose','recover'))
    p.add_argument('--market',required=True);p.add_argument('--cid');p.add_argument('--confirm',action='store_true');a=p.parse_args()
    try:
        with CycleStore(ROOT/'var/second-cycle.sqlite',readonly=a.action=='diagnose' or not a.confirm) as store:
            if a.action=='recover' and a.confirm:Inbox(store)
            result=diagnose(store,a.market) if a.action=='diagnose' else recover(store,a.market,a.cid,confirm=a.confirm)
        print(json.dumps(result,ensure_ascii=False));return 0
    except CycleError as error:
        print(json.dumps({'error':str(error)},ensure_ascii=False));return 2

if __name__=='__main__':raise SystemExit(main())
