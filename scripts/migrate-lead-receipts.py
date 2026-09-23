#!/usr/bin/env python3
"""Check or apply the additive market-scoped Kalodata page receipt schema."""
import argparse
import json
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.dont_write_bytecode=True
sys.path.insert(0,str(ROOT/'scripts'))

from lib.leads_queue import SCHEMA  # noqa:E402
from lib.market_registry import operational_market_keys  # noqa:E402

NEW_TABLES={'leads_page_scope','leads_page_legacy_history'}
OLD_TABLES={'leads_query','leads_page','leads_attempt'}


def path_for(root,market):
    return Path(root)/('var/kalodata-leads.sqlite' if market=='it' else f'var/kalodata-leads-{market}.sqlite')


def check(root,market):
    path=path_for(root,market)
    if not path.exists():raise ValueError('leads_receipt_database_missing')
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as db:
        tables={row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not OLD_TABLES<=tables:raise ValueError('leads_receipt_legacy_schema_missing')
    pending=sorted(NEW_TABLES-tables)
    return {'market':market,'database':str(path),'ready':not pending,'pending':pending}


def apply(root,market):
    current=check(root,market)
    if current['pending']:
        with closing(sqlite3.connect(path_for(root,market),timeout=30)) as db:
            db.executescript(SCHEMA)
    return check(root,market)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('check','apply'))
    parser.add_argument('--market',default='all')
    args=parser.parse_args(argv)
    try:
        markets=operational_market_keys(ROOT)
        if args.market!='all' and args.market not in markets:
            raise ValueError('leads_receipt_market_invalid')
        chosen=markets if args.market=='all' else (args.market,)
        rows=[check(ROOT,market) if args.action=='check' else apply(ROOT,market) for market in chosen]
        result={'ready':all(row['ready'] for row in rows),'databases':rows,'platformWrites':0}
        print(json.dumps(result,ensure_ascii=False,sort_keys=True))
        return 0 if result['ready'] else 2
    except (OSError,ValueError,sqlite3.Error) as error:
        print(json.dumps({'error':str(error)},ensure_ascii=False))
        return 2


if __name__=='__main__':raise SystemExit(main())
