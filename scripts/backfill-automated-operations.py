#!/usr/bin/env python3
"""Preview/apply local collaboration and account-generation projections after v11-v13."""
import argparse
import json
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.dont_write_bytecode=True;sys.path.insert(0,str(ROOT/'scripts'))
from lib.account_identity import apply_bootstrap,bootstrap_preview  # noqa:E402
from lib.collaboration_status import apply_backfill,backfill_preview  # noqa:E402
from lib.market_accounts import evidence_summary,load_config  # noqa:E402
from lib.second_cycle import CycleError,CycleStore  # noqa:E402
def main():
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('action',choices=('preview','apply'));parser.add_argument('--confirmed',action='store_true');args=parser.parse_args()
 try:
  if args.action=='apply' and not args.confirmed:raise CycleError('backfill_confirmation_required')
  config=load_config(ROOT);pair=config['markets']['it'];evidence=evidence_summary(ROOT,pair,'it')
  with CycleStore(ROOT/'var/second-cycle.sqlite',readonly=args.action=='preview') as store:
   collaboration=apply_backfill(store) if args.action=='apply' else backfill_preview(store)
   accounts=apply_bootstrap(store,ROOT,evidence) if args.action=='apply' else bootstrap_preview(store,ROOT,evidence)
  print(json.dumps({'action':args.action,'collaboration':collaboration,'accounts':accounts,
                    'platformWrites':0,'credentialWrites':0,'realSends':0},ensure_ascii=False));return 0
 except (CycleError,ValueError,OSError) as error:
  print(json.dumps({'error':str(error)},ensure_ascii=False));return 2
if __name__=='__main__':raise SystemExit(main())
