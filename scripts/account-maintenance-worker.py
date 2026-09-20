#!/usr/bin/env python3
"""Advance one project-owned account maintenance intent; legacy credentials remain read-only."""
import json
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.dont_write_bytecode=True;sys.path.insert(0,str(ROOT/'scripts'))
from lib.account_identity import claim_next,execute_claimed,status  # noqa:E402
from lib.second_cycle import CycleStore  # noqa:E402
def main():
 with CycleStore(ROOT/'var/second-cycle.sqlite') as store:
  claimed=claim_next(store)
  result=execute_claimed(store,ROOT,claimed['intentId']) if claimed else None
  print(json.dumps(status(store,ROOT)|{'result':result},ensure_ascii=False))
 return 0
if __name__=='__main__':raise SystemExit(main())
