#!/usr/bin/env python3
"""Print the read-only cross-market console; never starts, retries or claims work."""
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "scripts"))

from lib.operations_console import console  # noqa: E402
from lib.second_cycle import CycleError, CycleStore  # noqa: E402


def main():
    try:
        with CycleStore(ROOT / "var/second-cycle.sqlite", readonly=True) as store:
            print(json.dumps(console(ROOT, store), ensure_ascii=False))
        return 0
    except (CycleError, OSError, ValueError, sqlite3.Error) as error:
        print(json.dumps({"error": str(error) or type(error).__name__}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
