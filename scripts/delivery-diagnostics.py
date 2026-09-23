#!/usr/bin/env python3
"""Print aggregate read-only delivery throughput diagnostics, without recipient data."""
import argparse
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "scripts"))
from lib.delivery_diagnostics import diagnose


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", type=float, default=0, help="Unix timestamp, defaults to all recorded history")
    args = parser.parse_args(argv)
    try:
        print(json.dumps(diagnose(ROOT, since=args.since), ensure_ascii=False, sort_keys=True))
        return 0
    except (OSError, ValueError, sqlite3.Error) as error:
        print(json.dumps({"error": type(error).__name__}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
