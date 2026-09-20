#!/usr/bin/env python3
"""Read and mutate the local automated-operations control ledger."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "scripts"))

from lib.operations_workflow import create_run, request_stop, save_setting, status  # noqa:E402
from lib.second_cycle import CycleError, CycleStore  # noqa:E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("status", "save", "run", "stop"))
    parser.add_argument("--json")
    args = parser.parse_args()
    try:
        body = json.loads(args.json or "{}")
        with CycleStore(ROOT / "var/second-cycle.sqlite") as store:
            if args.action == "status":
                result = status(store)
            elif args.action == "save":
                result = save_setting(store, "it", body.get("requestId"), body.get("expectedRevision"),
                                      body.get("changes"))
                result = status(store) | {"saved": result}
            elif args.action == "run":
                result = create_run(store, market="it", trigger_source="manual",
                                    request_id=body.get("requestId"))
                result = status(store) | {"created": result}
            else:
                result = request_stop(store, body.get("runId"), body.get("expectedState", "running"))
                result = status(store) | {"stopped": result}
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (CycleError, ValueError, TypeError, json.JSONDecodeError) as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
