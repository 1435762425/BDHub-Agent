#!/usr/bin/env python3
"""Check or apply additive BDHub-Agent schema migrations."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "scripts"))

from lib.schema_migrations import DATABASES, apply_all, check_all  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("check", "apply"))
    parser.add_argument("--database", choices=("all", *DATABASES), default="all")
    args = parser.parse_args()
    keys = None if args.database == "all" else (args.database,)
    try:
        result = check_all(ROOT, keys) if args.action == "check" else apply_all(ROOT, keys)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0 if result["ready"] else 2
    except (OSError, ValueError) as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
