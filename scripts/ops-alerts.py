#!/usr/bin/env python3
"""Print the read-only health alerts shown in the page alert bar; never starts or retries work."""
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "scripts"))

from lib.ops_alerts import evaluate, evidence, gather  # noqa: E402
from lib.second_cycle import CycleError, CycleStore  # noqa: E402


def main():
    try:
        with CycleStore(ROOT / "var/second-cycle.sqlite", readonly=True) as store:
            facts = gather(ROOT, store)
        print(json.dumps({"schemaVersion": "bdhub.ops-alerts.v1", "checkedAt": facts["now"], "alerts": evaluate(facts),
                          "evidence": evidence(facts), "readOnly": True, "platformWrites": 0}, ensure_ascii=False))
        return 0
    except (CycleError, OSError, ValueError, sqlite3.Error) as error:
        print(json.dumps({"error": str(error) or type(error).__name__}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
