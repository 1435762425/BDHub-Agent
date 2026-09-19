#!/usr/bin/env python3
"""Show the creator-lead sending pool: the funnel, the layers, and the next slots to send.

    python scripts/lead-pool.py status [--limit 20]

Read-only. The pool is recomputed from existing tables on every read because its order depends on
the current time; nothing here is stored and nothing is sent.
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / 'scripts'))
from lib.lead_pool import pool  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['status'])
    parser.add_argument('--limit', type=int, default=20)
    args = parser.parse_args()
    if not 1 <= args.limit <= 200:
        print(json.dumps({'error': 'lead_pool_limit_invalid'}, ensure_ascii=False))
        return 2
    print(json.dumps(pool(ROOT, limit=args.limit), ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
