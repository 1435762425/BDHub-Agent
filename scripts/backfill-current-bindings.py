#!/usr/bin/env python3
"""Check or promote provably exact standard TapLinks from the local verified ledger."""
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.dont_write_bytecode=True;sys.path.insert(0,str(ROOT/'scripts'))
from lib.catalog_binding import audit_existing  # noqa: E402
def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=('check','apply'));a=p.parse_args()
 try:print(json.dumps(audit_existing(ROOT,apply=a.action=='apply'),ensure_ascii=False,sort_keys=True));return 0
 except (OSError,ValueError) as error:print(json.dumps({'error':str(error)},ensure_ascii=False));return 2
if __name__=='__main__':raise SystemExit(main())
