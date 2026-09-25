#!/usr/bin/env python3
"""Read-only market overview; never initializes state or starts workers."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / 'scripts'))
from lib.market_overview import overview


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--market', required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(overview(ROOT, args.market), ensure_ascii=False))
        return 0
    except ValueError:
        print(json.dumps({'error': 'market_overview_unavailable'}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
