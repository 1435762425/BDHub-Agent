#!/usr/bin/env python3
"""Read, preview and save the full-managed intake thresholds used when products are collected.

    python scripts/global-screen.py show
    python scripts/global-screen.py preview --json '{"minSales":200}'
    python scripts/global-screen.py save    --json '{"minSales":200}'
    python scripts/global-screen.py run     [--run-id it-global-20260914]

``show`` always answers with the funnel of the latest collection under the thresholds in force,
screening it first if that record does not exist yet -- screening is local arithmetic over
stored listings and never contacts the platform. ``preview`` answers the operator's real
question: how many products would the new numbers admit, and how many of those are not in the
selected pool yet. Errors come back as JSON codes, never as a traceback.
"""
import argparse
import json
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / 'scripts'))
from lib.global_screen import (DEFAULTS, Screen, config_path, fingerprint, funnel, load,  # noqa: E402
                               preview, save, screen_run_id, screen_source, validate)
from lib.global_selection import settled_pids  # noqa: E402

DB = ROOT / 'var/global-source.sqlite'
BOUNDS = {'minSales': [0, 1_000_000], 'minRating': [0, 5], 'minCommissionGapPoints': [0, 100]}


def latest_collection(db=DB):
    """The newest collection run that actually holds products."""
    if not db.exists():
        return None
    with closing(sqlite3.connect(db.resolve().as_uri() + '?mode=ro', uri=True)) as conn:
        row = conn.execute('SELECT r.id,r.state,r.updated,r.reported_total,'
                           '(SELECT count(*) FROM global_source_product p WHERE p.run_id=r.id) '
                           'FROM global_source_run r ORDER BY r.created DESC').fetchall()
    for run_id, state, updated, reported, products in row:
        if products:
            return {'runId': run_id, 'state': state, 'products': products,
                    'reportedTotal': reported, 'observedAt': updated}
    return None


def active_screen(config, collection):
    """Screen the latest collection under ``config`` if that exact record is missing."""
    run_id = screen_run_id(collection['runId'], config)
    ledger = Screen(DB)
    try:
        known = ledger.detail(run_id)
    finally:
        ledger.close()
    return known or screen_source(DB, collection['runId'], root=ROOT, config=config)


def state(collection=None, config=None, extra=None):
    config = config or load(ROOT)
    collection = collection or latest_collection()
    payload = {'config': config, 'fingerprint': fingerprint(config), 'defaults': DEFAULTS,
               'bounds': BOUNDS, 'configPath': str(config_path(ROOT).relative_to(ROOT)),
               'collection': collection, 'screen': None, 'funnel': None}
    if collection:
        record = active_screen(config, collection)
        payload['screen'] = record
        payload['funnel'] = funnel(DB, record['runId'], settled_pids(ROOT))
    if extra:
        payload.update(extra)
    return payload


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['show', 'preview', 'save', 'run'])
    parser.add_argument('--json', help='config JSON, inline or @path')
    parser.add_argument('--run-id', help='collection run to screen')
    args = parser.parse_args()

    raw = {}
    if getattr(args, 'json', None):
        raw = json.loads(Path(args.json[1:]).read_text(encoding='utf-8')) if args.json.startswith('@') else json.loads(args.json)

    try:
        if args.action == 'show':
            print(json.dumps(state(), ensure_ascii=False))
            return 0

        if args.action == 'run':
            collection = latest_collection()
            if not collection:
                print(json.dumps({'error': 'catalog_screen_no_collection'}, ensure_ascii=False))
                return 2
            if args.run_id:
                collection = collection | {'runId': args.run_id}
            config = load(ROOT)
            record = screen_source(DB, collection['runId'], root=ROOT, config=config)
            print(json.dumps(state(collection, config, {'screen': record,
                                                        'funnel': funnel(DB, record['runId'],
                                                                         settled_pids(ROOT))}), ensure_ascii=False))
            return 0

        proposed = validate({**load(ROOT), **raw})
        collection = latest_collection()
        if args.action == 'preview':
            payload = state(collection, None, {'preview': preview(DB, collection['runId'], proposed) if collection else None})
            print(json.dumps(payload | {'previewConfig': proposed}, ensure_ascii=False))
            return 0
        # Saving re-screens the stored listing under the new thresholds so the funnel the page
        # shows is the one actually in force. Screening never writes to the platform.
        config = save(ROOT, proposed)
        print(json.dumps(state(collection, config, {'saved': True}), ensure_ascii=False))
        return 0
    except ValueError as error:
        print(json.dumps({'saved': False, 'error': str(error), 'config': load(ROOT)}, ensure_ascii=False))
        return 2
    except json.JSONDecodeError:
        print(json.dumps({'saved': False, 'error': 'catalog_screen_json_invalid', 'config': load(ROOT)}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
