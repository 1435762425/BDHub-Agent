#!/usr/bin/env python3
"""Project or read bounded market page snapshots without external platform calls."""
import argparse,importlib.util,json,sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1];sys.dont_write_bytecode=True;sys.path.insert(0,str(ROOT/'scripts'))
from lib.market_read_model import VIEWS,publish,read  # noqa:E402
from lib.market_registry import market as market_record  # noqa:E402
from lib.second_cycle import CycleError,CycleStore  # noqa:E402

def module(name,path):
 spec=importlib.util.spec_from_file_location(name,path);value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value

def project(store,market,view):
 if view=='operations':payload=module('operations_home_projector',ROOT/'scripts/operations-home.py').home(store,market)
 elif view=='catalog':payload=module('market_catalog_projector',ROOT/'scripts/market-catalog-status.py').status(market)
 else:raise CycleError('market_read_model_invalid')
 return publish(store,market,view,payload)

def main():
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('action',choices=('read','project'));parser.add_argument('--market',required=True);parser.add_argument('--view',required=True,choices=sorted(VIEWS));parser.add_argument('--max-age',type=float);args=parser.parse_args()
 try:
  market_record(ROOT,args.market)
  with CycleStore(ROOT/'var/second-cycle.sqlite',readonly=args.action=='read') as store:
   if args.action=='read':
    value=read(store,args.market,args.view,max_age=args.max_age)
    if value is None:raise CycleError('market_read_model_unavailable')
    print(json.dumps(value,ensure_ascii=False));return 0
   result=project(store,args.market,args.view)
   print(json.dumps(result,ensure_ascii=False));return 0
 except (CycleError,ValueError) as error:
  print(json.dumps({'error':str(error)},ensure_ascii=False));return 2

if __name__=='__main__':raise SystemExit(main())
