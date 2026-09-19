#!/usr/bin/env python3
"""Read and save the operator's schedule intent for the workbench jobs.

    python scripts/jobs.py status
    python scripts/jobs.py save --json '{"jobs":{"catalog_collect":{"enabled":true,"at":"03:00"}}}'

Schedules remain off by default. ``start-scheduler`` launches only the local coordinator; individual
maintenance cycles run only after their own explicit enabled switch is saved.
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / 'scripts'))
from lib.jobs import load, save, status  # noqa: E402
from lib.material_maintenance import start_scheduler, stop_scheduler  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['status', 'save', 'start-scheduler', 'stop-scheduler'])
    parser.add_argument('--json', help='config JSON, inline or @path')
    args = parser.parse_args()

    try:
        if args.action == 'status':
            print(json.dumps(status(ROOT), ensure_ascii=False))
            return 0
        if args.action == 'start-scheduler':
            start_scheduler(ROOT);print(json.dumps(status(ROOT), ensure_ascii=False));return 0
        if args.action == 'stop-scheduler':
            stop_scheduler(ROOT);print(json.dumps(status(ROOT), ensure_ascii=False));return 0
        raw = {}
        if args.json:
            raw = (json.loads(Path(args.json[1:]).read_text(encoding='utf-8'))
                   if args.json.startswith('@') else json.loads(args.json))
        current = load(ROOT)
        updates=raw.get('jobs') or {}
        merged = {'version': current['version'],
                  'jobs': {key:{**value,**(updates.get(key) or {})} for key,value in current['jobs'].items()}}
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
