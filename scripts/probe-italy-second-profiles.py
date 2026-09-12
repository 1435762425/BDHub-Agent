#!/usr/bin/env python3
"""Run the fixed local 48-creator Italy cohort in sequential read-only batches.

Each batch uses the existing supervised probe and stops the whole run on an
account/transport error. Existing successful and unresolved observations are
retained without blind retries. No database access or messaging operations.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
VAR = ROOT / "var"


def read(path):
    return json.loads(path.read_text())


def write_new(path, data):
    with path.open("x", encoding="utf-8") as stream:
        path.chmod(0o600)
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def plan(source, initial):
    if source.get("market") != "it" or len(source.get("records", [])) != 48:
        raise ValueError("Expected the fixed 48-creator Italy readiness export")
    if initial.get("market") != "it" or initial.get("status") != "completed":
        raise ValueError("Initial live probe must have completed without account errors")
    records = source["records"]
    ids = [r["externalId"] for r in records]
    handles = [r["handle"].strip().lstrip("@").lower() for r in records]
    if len(set(ids)) != 48 or len(set(handles)) != 48:
        raise ValueError("Cohort contains duplicate identities or handles")
    observations = initial.get("targets")
    if not isinstance(observations, list):
        raise ValueError("Initial targets must be a list")
    existing = {t["externalId"]: t for t in observations}
    if len(existing) != len(observations) or len({t.get("requestedHandle") for t in observations}) != len(observations):
        raise ValueError("Initial probe contains duplicate identities or handles")
    if not existing or not set(existing).issubset(set(ids)):
        raise ValueError("Initial targets are outside the fixed cohort")
    targets = []
    for index, record in enumerate(records, 1):
        handle = record["handle"].strip().lstrip("@").lower()
        previous = existing.get(record["externalId"])
        if previous:
            if previous.get("requestedHandle") != handle or previous.get("status") not in {"completed", "unresolved"}:
                raise ValueError("Initial target is not a finished observation for this handle")
            continue
        targets.append({"ref": f"it-second-{index:02d}", "handle": handle,
                        "externalId": record["externalId"], "historicalOecHint": record.get("localOecHint")})
    return [targets[i:i+3] for i in range(0, len(targets), 3)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=VAR / "italy-second-readiness-20260912.json")
    parser.add_argument("--initial", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--account", default="acc6")
    args = parser.parse_args()
    paths = [p.resolve() for p in (args.source, args.initial, args.output)]
    if any(not p.is_relative_to(VAR.resolve()) for p in paths):
        raise ValueError("All files must stay in new-project var")
    source_path, initial_path, output = paths
    source, initial = read(source_path), read(initial_path)
    if initial.get("account") != args.account:
        raise ValueError("Continuation must keep the same account")
    batches = plan(source, initial)
    output.mkdir(parents=True, exist_ok=False)
    write_new(output / "manifest.private.json", {
        "schema": "bdhub.italy-second-profile-probe.v1", "market": "it", "account": args.account,
        "source": str(source_path), "sourceSha256": hashlib.sha256(source_path.read_bytes()).hexdigest(),
        "initial": str(initial_path), "initialSha256": hashlib.sha256(initial_path.read_bytes()).hexdigest(),
        "targetCount": 48, "previouslyObserved": len(initial["targets"]), "batches": batches,
        "oldDatabaseWrites": 0, "realSends": 0})
    for index, targets in enumerate(batches, 1):
        target_file = output / f"batch-{index:02d}-targets.private.json"
        batch_output = output / f"batch-{index:02d}"
        write_new(target_file, {"market": "it", "targets": targets})
        process = subprocess.run([sys.executable, str(ROOT / "scripts/probe-italy-profile.py"),
            "--account", args.account, "--targets", str(target_file), "--output", str(batch_output)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        report_path = batch_output / "report.private.json"
        report = read(report_path) if report_path.exists() else {"status": "blocked", "reason": "no_batch_report"}
        print(json.dumps({"batch": index, "totalBatches": len(batches), "status": report.get("status"),
            "reason": report.get("reason"), "completed": sum(t.get("status") == "completed" for t in report.get("targets", [])),
            "unresolved": sum(t.get("status") == "unresolved" for t in report.get("targets", [])),
            "counters": report.get("counters")}), flush=True)
        if process.returncode or report.get("status") != "completed":
            return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
