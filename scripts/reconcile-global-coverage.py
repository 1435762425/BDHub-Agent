#!/usr/bin/env python3
"""Preserve an accepted category coverage head and overlay only matching weekly PIDs."""
import argparse,json,sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1];sys.dont_write_bytecode=True;sys.path.insert(0,str(ROOT/'scripts'))
from lib.cycle_management import sync_full_managed  # noqa:E402
from lib.global_screen import screen_source  # noqa:E402
from lib.global_source import GlobalSourceError,GlobalSources  # noqa:E402
from lib.market_registry import market as market_record  # noqa:E402


def main():
 parser=argparse.ArgumentParser(description=__doc__)
 parser.add_argument('--market',required=True);parser.add_argument('--apply',action='store_true')
 args=parser.parse_args()
 try:
  market=market_record(ROOT,args.market)
  if market['capabilities']['fullManagedCatalog'] is not True:raise ValueError('full_managed_not_supported')
  path=ROOT/('var/global-source.sqlite' if args.market=='it' else f'var/global-source-{args.market}.sqlite')
  if not path.exists():raise GlobalSourceError('coverage_source_missing')
  store=GlobalSources(path,readonly=not args.apply)
  try:
   result=store.reconcile_category_coverage(args.market,apply=args.apply)
   if args.apply:scope=store.get(result['runId'])['scope']
  finally:store.close()
  if args.apply:
   screening=screen_source(path,result['runId'],root=ROOT)
   management=sync_full_managed(ROOT/'var/second-cycle.sqlite',path,
                                {key:scope[key] for key in ('market','account','institutionFingerprint')})
   result.update(screenRunId=screening['runId'],screenCounts=screening['counts'],
                 managementPids=management['fullManagedPids'])
  print(json.dumps({'market':args.market,'applied':args.apply,**result,'platformWrites':0,'realSends':0},ensure_ascii=False));return 0
 except (OSError,ValueError,GlobalSourceError) as error:
  print(json.dumps({'error':str(error)},ensure_ascii=False));return 2


if __name__=='__main__':raise SystemExit(main())
