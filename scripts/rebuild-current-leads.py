#!/usr/bin/env python3
"""Preview or confirm a current lead reset while preserving evidence and sent history."""
import argparse
import json
from pathlib import Path
import sqlite3
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.dont_write_bytecode=True
sys.path.insert(0,str(ROOT/'scripts'))

from lib.lead_rebuild import reset_current  # noqa:E402
from lib.second_cycle import CycleError  # noqa:E402


def main():
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('action',choices=('preview','apply'))
 parser.add_argument('--confirmed',action='store_true');args=parser.parse_args()
 try:
  if args.action=='apply' and not args.confirmed:raise CycleError('lead_rebuild_confirmation_required')
  if args.action=='preview' and args.confirmed:raise CycleError('lead_rebuild_arguments_invalid')
  print(json.dumps(reset_current(ROOT,confirmed=args.action=='apply'),ensure_ascii=False));return 0
 except (CycleError,OSError,sqlite3.Error) as error:
  print(json.dumps({'error':str(error)},ensure_ascii=False));return 2


if __name__=='__main__':
 raise SystemExit(main())
