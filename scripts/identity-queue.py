#!/usr/bin/env python3
"""Show the creator-identity (OECID) stage of the funnel.

    python scripts/identity-queue.py status
    python scripts/identity-queue.py save --json '{"batchSize":2000,"cohortSize":20}'

``status`` reads only: how many leads already carry an OECID (and are therefore pool positions),
how many are still waiting for one, and how many handles the platform could not find -- the last
group is kept and reported, never given a fabricated identity and never admitted to the pool.

The actual resolving is done by ``scripts/identity-batch.py``, which walks the backlog through the
already-validated ``creator-profile-refresh.py worker``. Errors come back as JSON codes.
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / 'scripts'))
from lib.identity_queue import status  # noqa: E402
from lib.job_run import load_config, save_config  # noqa: E402


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
        saved = save_config(ROOT, 'identity', {**load_config(ROOT, 'identity'), **raw})
        print(json.dumps(status(ROOT) | {'config': saved, 'saved': True}, ensure_ascii=False))
        return 0
    except ValueError as error:
        print(json.dumps({'error': str(error)}, ensure_ascii=False))
        return 2
    except json.JSONDecodeError:
        print(json.dumps({'error': 'identity_queue_json_invalid'}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
