#!/usr/bin/env python3
"""Create the isolated local plan rows required by enabled product markets."""
import argparse
import json
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.dont_write_bytecode=True
sys.path.insert(0,str(ROOT/'scripts'))

from lib.market_accounts import load_config  # noqa:E402
from lib.market_registry import enabled_market_keys,market  # noqa:E402
from lib.second_cycle import CycleError,CycleStore,digest  # noqa:E402

INSTITUTION='bjn-local-research'


def expected_rows():
 accounts=load_config(ROOT)['markets']
 return [{'market':key,'planId':'cycle-'+digest([INSTITUTION,key])[:24],
          'label':market(ROOT,key)['label'],'accounts':accounts[key]['roles'],
          'fullManagedCatalog':market(ROOT,key)['capabilities']['fullManagedCatalog']}
         for key in enabled_market_keys(ROOT)]


def main():
 parser=argparse.ArgumentParser(description=__doc__)
 parser.add_argument('action',choices=('preview','apply'))
 parser.add_argument('--confirmed',action='store_true')
 args=parser.parse_args()
 try:
  if args.action=='apply' and not args.confirmed:raise CycleError('onboarding_confirmation_required')
  rows=expected_rows();created=[]
  with CycleStore(ROOT/'var/second-cycle.sqlite',readonly=args.action=='preview') as store:
   existing={row['market']:row['id'] for row in store.db.execute(
    'SELECT id,market FROM plan WHERE institution=?',(INSTITUTION,))}
   if args.action=='apply':
    for row in rows:
     if row['market'] not in existing:
      store.plan(INSTITUTION,row['market']);created.append(row['market'])
  print(json.dumps({'action':args.action,'markets':rows,'created':created,
                    'platformWrites':0,'credentialWrites':0,'realSends':0},ensure_ascii=False))
  return 0
 except (CycleError,ValueError,OSError) as error:
  print(json.dumps({'error':str(error)},ensure_ascii=False));return 2


if __name__=='__main__':raise SystemExit(main())
