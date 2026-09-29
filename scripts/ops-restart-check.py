#!/usr/bin/env python3
"""Read-only restart checks: preflight baseline, drained gate, postflight comparison.

Exit 0 when every check passes, 1 when a check fails, 2 when a check could not be read. It writes
only its own evidence file and never stops, starts or changes any process or ledger.
"""
import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "scripts"))

from lib.restart_check import drained, evidence_path, postflight, preflight  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("preflight", "drained", "postflight"))
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--workers-stopped", action="store_true", help="drained: also require no resident process alive")
    args = parser.parse_args()
    try:
        target = evidence_path(ROOT, args.release_id, args.phase)
        if args.phase == "preflight":
            result = preflight(ROOT)
            result["ok"] = True
        elif args.phase == "drained":
            result = drained(ROOT, workers_stopped=args.workers_stopped)
        else:
            baseline = json.loads(evidence_path(ROOT, args.release_id, "preflight").read_text(encoding="utf-8"))
            result = postflight(ROOT, baseline, wait_seconds=120)
        result |= {"releaseId": args.release_id, "readOnly": True, "platformWrites": 0}
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["ok"] else 1
    except (OSError, ValueError, KeyError, sqlite3.Error) as error:
        print(json.dumps({"phase": args.phase, "ok": False, "error": str(error) or type(error).__name__}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    sys.exit(main())
