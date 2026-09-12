#!/usr/bin/env python3
"""Import verified local probe evidence into the independent identity registry.

No network calls or legacy writes. Handle leads remain pending until exact Find
evidence resolves their current OEC. Existing creators refresh by OEC alone.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
VAR = ROOT / "var"
sys.path.insert(0, str(ROOT / "scripts"))
from lib.creator_identity import CreatorIdentityStore

SPEC = importlib.util.spec_from_file_location("identity_probe_evidence", ROOT / "scripts/summarize-italy-profile-probes.py")
evidence = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(evidence)


def import_reports(store, reports):
    # Validate all successful observations before the first database write.
    evidence.build_documents(reports)
    for report in reports:
        for target in report["targets"]:
            if target.get("inputKind", "handle_discovery") == "handle_discovery" and target.get("status") == "completed":
                find_receipt(report, target)
    for report in sorted(reports, key=lambda r: evidence.timestamp(r.get("finishedAt", r.get("startedAt")))):
        observed_at = report.get("finishedAt", report.get("startedAt"))
        report_hash = hashlib.sha256(json.dumps(report, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
        for target in report["targets"]:
            reference = f"profile-probe:{report_hash}:{target['targetRef']}"
            route = target.get("inputKind", "handle_discovery")
            if route == "handle_discovery":
                lead = store.record_handle_lead("it", target["requestedHandle"], observed_at, reference + ":lead",
                    external_source="probe_source_reference", external_id=target["externalId"],
                    payload={"targetRef": target["targetRef"], "historicalCrossSourceIdentityProven": False})
                if target.get("status") != "completed":
                    continue
                find = find_receipt(report, target)
                store.resolve_handle_lead(lead["leadId"], target["oecId"], observed_at, reference + ":find", exact_find={
                    "market": "it", "queriedHandle": target["requestedHandle"],
                    "returnedHandle": target["find"]["identity"]["handle"], "oecId": target["find"]["identity"]["oecId"],
                    "httpStatus": find["httpStatus"], "code": 0, "verificationRequired": find["verificationRequired"]})
            elif route == "known_oec":
                if target.get("status") != "completed":
                    store.record_profile_failure("it", target["requestedOecId"], observed_at, reference + ":failure",
                        "timeout" if report.get("status") == "bounded_timeout" else "unknown")
                    continue
            else:
                raise ValueError("unknown identity input kind")
            summary = evidence.normalized(target["merged"])
            store.observe_profile("it", summary["identity"]["oecId"], summary["identity"]["handle"],
                observed_at, reference + ":profile", payload=summary)
    return store.stats()


def find_receipt(report, target):
    find = next((r for r in report.get("requests", []) if r.get("targetRef") == target["targetRef"] and r.get("stage") == "find"), None)
    if (not find or find.get("status") != "returned" or find.get("httpStatus") != 200
            or find.get("code") != "0" or find.get("verificationRequired") is not False):
        raise ValueError("successful handle discovery lacks final Find receipt")
    return find


def in_var(path):
    resolved = path.resolve()
    if not resolved.is_relative_to(VAR.resolve()) or resolved == VAR.resolve():
        raise ValueError("all inputs and the identity database must stay in new-project var")
    return resolved


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reports", nargs="+", type=Path, required=True)
    parser.add_argument("--database", type=Path, default=VAR / "creator-identities.sqlite")
    args = parser.parse_args()
    reports = [json.loads(in_var(path).read_text()) for path in args.reports]
    with CreatorIdentityStore(in_var(args.database)) as store:
        before = store.stats()
        after = import_reports(store, reports)
    print(json.dumps({"before": before, "after": after, "platformRequests": 0, "oldDatabaseWrites": 0, "realSends": 0}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
