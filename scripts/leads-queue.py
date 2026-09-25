#!/usr/bin/env python3
"""Show and maintain the PID -> creator-lead query queue.

    python scripts/leads-queue.py status
    python scripts/leads-queue.py sync              # reconcile the clock with the job stores
    python scripts/leads-queue.py save --json '{"refreshDays":7}'

``status`` reads only. ``sync`` records a query day for products whose Kalodata job actually
completed, and removes rows this module wrote for products that were never really queried --
``queued`` and ``awaiting_identity`` jobs never reached the platform. It never re-queries. Errors come back as JSON
codes, never as a traceback.
"""
import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / 'scripts'))
from lib.leads_queue import (DEFAULTS, Ledger, config_path, load, plan, run_state,  # noqa: E402
                             save_config, status, sync)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['status', 'plan', 'sync', 'save', 'run'])
    parser.add_argument('--market',required=True)
    parser.add_argument('--json', help='config JSON, inline or @path')
    parser.add_argument('--at', type=float, help='unix time to record; defaults to now')
    parser.add_argument('--batch-size', type=int, help='override the configured batch ceiling')
    args = parser.parse_args()

    try:
        if args.action == 'status':
            print(json.dumps(status(ROOT,market=args.market), ensure_ascii=False))
            return 0
        if args.action == 'plan':
            planned = plan(ROOT,market=args.market, batch_size=args.batch_size)
            print(json.dumps({'batchSize': planned['batchSize'], 'dueQueue': planned['dueQueue'],
                              'taken': planned['taken'], 'first': planned['first'],
                              'refresh': planned['refresh'], 'shortfall': planned['shortfall'],
                              'padded': planned['padded'], 'notYetDue': planned['notYetDue'],
                              'items': planned['items'][:20]}, ensure_ascii=False))
            return 0
        if args.action == 'run':
            from lib.second_cycle import CycleStore
            from lib.operations_workflow import create_run
            from lib.operations_scheduler import scheduler_state,start_scheduler
            with CycleStore(ROOT/'var/second-cycle.sqlite') as store:
                create_run(store,market=args.market,trigger_source='manual',sources=['campaign'],from_stage='kalodata')
            if not scheduler_state(ROOT)['running']:start_scheduler(ROOT)
            print(json.dumps(status(ROOT,market=args.market),ensure_ascii=False))
            return 0
        if args.action == 'save':
            raw = {}
            if args.json:
                raw = (json.loads(Path(args.json[1:]).read_text(encoding='utf-8'))
                       if args.json.startswith('@') else json.loads(args.json))
            saved = save_config(ROOT, {**load(ROOT), **raw})
            print(json.dumps(status(ROOT,market=args.market, config=saved) | {'saved': True}, ensure_ascii=False))
            return 0
        stamp = args.at if args.at else time.time()
        result = sync(ROOT,market=args.market, at=stamp)
        print(json.dumps(status(ROOT,market=args.market) | {'sync': result}, ensure_ascii=False))
        return 0
    except ValueError as error:
        print(json.dumps({'error': str(error)}, ensure_ascii=False))
        return 2
    except json.JSONDecodeError:
        print(json.dumps({'error': 'leads_queue_json_invalid'}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
