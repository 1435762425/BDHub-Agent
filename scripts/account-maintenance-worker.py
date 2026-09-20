#!/usr/bin/env python3
"""Drain the project-owned account maintenance queue one account at a time."""
import json
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.dont_write_bytecode=True;sys.path.insert(0,str(ROOT/'scripts'))
from lib.account_identity import claim_next,execute_claimed,status  # noqa:E402
from lib.project_account_identity import ProjectAccountIdentityAdapter  # noqa:E402
from lib.second_cycle import CycleStore  # noqa:E402
def main():
 adapter=ProjectAccountIdentityAdapter(ROOT);results=[]
 with CycleStore(ROOT/'var/second-cycle.sqlite') as store:
  while True:
   claimed=claim_next(store)
   if not claimed:break
   results.append(execute_claimed(store,ROOT,claimed['intentId'],adapter))
  print(json.dumps(status(store,ROOT)|{'results':results},ensure_ascii=False))
 return 0
if __name__=='__main__':raise SystemExit(main())
