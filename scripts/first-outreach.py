#!/usr/bin/env python3
"""BR first-outreach CLI. Status never starts a worker or accesses the platform."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / 'scripts'))
from lib.first_outreach_runtime import ACTIONS, _code, mutate, status, worker  # noqa:E402


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('status', 'worker', *sorted(ACTIONS)))
    parser.add_argument('--market', required=True, choices=('br',))
    parser.add_argument('--json')
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args(argv)
    try:
        if args.action == 'status':
            if args.json is not None or args.once:
                raise ValueError('invalid_first_outreach_request')
            result = status(ROOT, args.market)
        elif args.action == 'worker':
            if args.json is not None:
                raise ValueError('invalid_first_outreach_request')
            result = worker(ROOT, once=args.once)
        else:
            if args.json is None or args.once or len(args.json.encode()) > 8192:
                raise ValueError('invalid_first_outreach_request')
            result = mutate(ROOT, args.action, args.market, json.loads(args.json))
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as error:
        print(json.dumps({'error': _code(error)}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
