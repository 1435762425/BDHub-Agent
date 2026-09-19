#!/usr/bin/env python3
"""Read and save the operator's schedule intent for the workbench jobs.

    python scripts/jobs.py status
    python scripts/jobs.py save --json '{"jobs":{"catalog_collect":{"enabled":true,"at":"03:00"}}}'

Nothing here starts a process. The scheduler is not built, so ``schedulerReady`` stays false and
the page must present the switches as recorded intent rather than a running schedule.
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / 'scripts'))
from lib.jobs import load, save, status  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['status', 'save'])
    parser.add_argument('--json', help='config JSON, inline or @path')
    args = parser.parse_args()

    try:
        if args.action == 'status':
            print(json.dumps(status(ROOT), ensure_ascii=False))
            return 0
        raw = {}
        if args.json:
            raw = (json.loads(Path(args.json[1:]).read_text(encoding='utf-8'))
                   if args.json.startswith('@') else json.loads(args.json))
        current = load(ROOT)
        merged = {'version': current['version'],
                  'jobs': {**current['jobs'], **(raw.get('jobs') or {})}}
        saved = save(ROOT, merged)
        print(json.dumps(status(ROOT) | {'saved': True, 'config': saved}, ensure_ascii=False))
        return 0
    except ValueError as error:
        print(json.dumps({'saved': False, 'error': str(error)}, ensure_ascii=False))
        return 2
    except json.JSONDecodeError:
        print(json.dumps({'saved': False, 'error': 'jobs_json_invalid'}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
