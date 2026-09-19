#!/usr/bin/env python3
"""Read, configure and start the catalogue work jobs.

    python scripts/job-run.py status
    python scripts/job-run.py save   --name links --json '{"creates":5}'
    python scripts/job-run.py start  --name selection
    python scripts/job-run.py start  --name links --json '{"readLimit":15,"creates":0}'
    python scripts/job-run.py stop   --name identity

Starting a job launches the underlying script detached and returns immediately; the page follows
``run.running``. ``creates: 0`` keeps link preparation read-only. ``stop`` writes a stop request the
driver reads at its next safe point, so it ends the batch without abandoning a round of platform
work. Errors come back as JSON codes.
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / 'scripts'))
from lib.job_run import JOBS, request_stop, save_config, start, status  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['status', 'save', 'start', 'stop'])
    parser.add_argument('--name', choices=sorted(JOBS))
    parser.add_argument('--json', help='config JSON, inline or @path')
    args = parser.parse_args()

    def payload(raw):
        return (json.loads(Path(raw[1:]).read_text(encoding='utf-8')) if raw.startswith('@')
                else json.loads(raw)) if raw else {}

    try:
        if args.action == 'status':
            print(json.dumps(status(ROOT, args.name), ensure_ascii=False))
            return 0
        if not args.name:
            raise ValueError('job_unknown')
        if args.action == 'save':
            save_config(ROOT, args.name, {**status(ROOT, args.name)[args.name]['config'], **payload(args.json)})
            # Always answer with both jobs: a partial payload is not a valid state.
            print(json.dumps(status(ROOT) | {'saved': True}, ensure_ascii=False))
            return 0
        if args.action == 'stop':
            request_stop(ROOT, args.name)
            print(json.dumps(status(ROOT), ensure_ascii=False))
            return 0
        start(ROOT, args.name, payload(args.json))
        print(json.dumps(status(ROOT), ensure_ascii=False))
        return 0
    except ValueError as error:
        print(json.dumps({'error': str(error)}, ensure_ascii=False))
        return 2
    except json.JSONDecodeError:
        print(json.dumps({'error': 'job_json_invalid'}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
