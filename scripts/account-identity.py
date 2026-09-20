#!/usr/bin/env python3
"""Local controls for account identity generations and maintenance intents."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "scripts"))

from lib.account_identity import request_maintenance, set_enabled, status  # noqa:E402
from lib.second_cycle import CycleError, CycleStore  # noqa:E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("status", "set-enabled", "request"))
    parser.add_argument("--json")
    args = parser.parse_args()
    try:
        body = json.loads(args.json or "{}")
        with CycleStore(ROOT / "var/second-cycle.sqlite") as store:
            if args.action == "set-enabled":
                set_enabled(store, ROOT, "it", body.get("account"), body.get("enabled"),
                            body.get("requestId"), body.get("expectedRevision"))
            elif args.action == "request":
                request_maintenance(store, ROOT, market="it", account=body.get("account"),
                                    operation=body.get("operation"), request_id=body.get("requestId"))
            result = status(store, ROOT)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (CycleError, ValueError, TypeError, json.JSONDecodeError) as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
