#!/usr/bin/env python3
"""Summarize existing local Italy probes; no network, legacy DB or identity writes.

The public document contains counts only. The private document retains current
platform observations under (targetRef, sourceExternalId); resolving today's
handle does not prove the historical external-source identity association.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
VAR = ROOT / "var"
sys.path.insert(0, str(ROOT / "scripts"))
from lib.profile_completion import PROFILE_FIELDS, merge_profile_summaries

STATES = ("absent", "no_value", "unauthorized", "error", "zero", "value")
AVAILABLE = {"value", "zero"}
COUNTERS = ("request_count", "challenge_count", "captcha_success_count",
            "captcha_replay_response_count", "captcha_replay_code0_count",
            "code10000_missing_header_count")
OUTCOMES = {"starting", "completed", "unresolved", "blocked", "bounded_timeout"}
INPUT_KINDS = {"handle_discovery", "known_oec"}
REASONS = {"account_not_startable", "account_not_prepared", "identity_changed_before_guard", "maintenance_due",
           "shared_backoff", "account_maintenance_or_shared_backoff", "verification_required", "remote_error",
           "whole_probe_deadline", "request_or_signer_error", "no_exact_handle", "find_market_mismatch",
           "find_identity_invalid", "profile_identity_mismatch", "profile_market_mismatch",
           "supplement_identity_mismatch", "supplement_market_mismatch", "merged_identity_not_confirmed",
           "probe_initialization_or_validation_error", "child_no_report"}
# A named diagnostic group, not a new matching gate or platform entitlement.
CORE_FIELDS = ("follower_cnt", "industry_groups", "med_gmv_revenue", "video_gmv",
               "live_gmv", "units_sold", "video_avg_view_cnt")


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode()).hexdigest()


def timestamp(value):
    if not isinstance(value, str):
        raise ValueError("missing observation timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("observation timestamp must include timezone")
    return parsed


def normalized(summary):
    # Existing allowlisted parser validates shape, field states and identity.
    return merge_profile_summaries([summary])


def available(summary):
    return {key for key, field in summary["fields"].items() if field["status"] in AVAILABLE}


def coverage(summaries):
    items = list(summaries)
    states = {key: {state: 0 for state in STATES} for key in PROFILE_FIELDS}
    for item in items:
        for key in PROFILE_FIELDS:
            states[key][item["fields"][key]["status"]] += 1
    return {"recordCount": len(items), "fieldStates": states,
            "available": {key: row["value"] + row["zero"] for key, row in states.items()}}


def completeness(summaries):
    items = list(summaries)
    incomplete_reasons = Counter({state: 0 for state in STATES if state not in AVAILABLE})
    for item in items:
        # One target can have multiple kinds of unavailable fields.
        incomplete_reasons.update({row["status"] for row in item["fields"].values()
                                   if row["status"] not in AVAILABLE})
    return {"identityVerifiedTargets": len(items),
            "all22FieldsAvailableTargets": sum(len(available(item)) == len(PROFILE_FIELDS) for item in items),
            "coreDiagnosticFields": list(CORE_FIELDS),
            "allCoreFieldsAvailableTargets": sum(set(CORE_FIELDS) <= available(item) for item in items),
            "unavailableStateTargetCounts": dict(incomplete_reasons),
            "meaning": "completed means exact current identity and request workflow completed; field completeness is measured separately"}


def nonnegative(value):
    if type(value) is not int or value < 0:
        raise ValueError("invalid probe count")
    return value


def status(value):
    return value if value in OUTCOMES else "not_completed"


def reason(value):
    return value if value in REASONS else ("not_reported" if value is None else "other")


def input_kind(report, target):
    if report["schema"] in {"bdhub.italy-profile-probe.v1", "bdhub.italy-profile-probe.v2"}:
        return "handle_discovery"
    kind = target.get("inputKind")
    if kind not in INPUT_KINDS:
        raise ValueError("invalid target input kind")
    if kind == "known_oec":
        oec = target.get("requestedOecId")
        if (not isinstance(oec, str) or not oec.isascii() or not oec.isdigit()
                or target.get("requestedHandle") is not None):
            raise ValueError("invalid known OEC input")
    return kind


def build_documents(reports, baseline=None):
    """Pure aggregation, with attempts and latest successful observations separate."""
    reports = list(reports)
    if not reports:
        raise ValueError("at least one probe is required")
    fingerprints = [digest(report) for report in reports]
    if len(set(fingerprints)) != len(fingerprints):
        raise ValueError("duplicate probe report")
    baseline_by_oec = {}
    if baseline is not None:
        if baseline.get("market") != "it" or baseline.get("schemaVersion") != 1:
            raise ValueError("invalid baseline schema or market")
        for record in baseline["records"]:
            summary = normalized(record["summary"])
            oec = summary["identity"]["oecId"]
            if not oec or record.get("oecId") != oec or summary["identity"]["market"] != "it":
                raise ValueError("baseline identity mismatch")
            if oec in baseline_by_oec:
                raise ValueError("duplicate baseline identity")
            baseline_by_oec[oec] = summary
    latest_attempt = {}
    latest_success = {}
    counters = Counter({key: 0 for key in COUNTERS})
    counter_reports = 0
    attempts = Counter()
    input_kinds = Counter({kind: 0 for kind in sorted(INPUT_KINDS)})
    known_oec_find_entries = 0
    known_oec_find_responses = 0
    known_oec_reports_with_requests = 0
    known_oec_reports_without_requests = 0
    private_attempts = []
    report_outcomes = Counter()
    stage_counts = {stage: Counter() for stage in ("find", "profile")}
    for report in sorted(reports, key=lambda item: timestamp(item.get("finishedAt", item.get("startedAt")))):
        if report.get("schema") not in {"bdhub.italy-profile-probe.v1", "bdhub.italy-profile-probe.v2", "bdhub.italy-profile-probe.v3"} or report.get("market") != "it":
            raise ValueError("invalid probe schema or market")
        if report.get("oldDatabaseWrites") != 0 or report.get("realSends") != 0:
            raise ValueError("report is outside read-only scope")
        captured = report.get("finishedAt", report.get("startedAt"))
        timestamp(captured)
        report_outcomes[status(report.get("status"))] += 1
        raw_counts = report.get("counters")
        if raw_counts is not None:
            counter_reports += 1
            for key in COUNTERS:
                counters[key] += nonnegative(raw_counts[key])
            for stage in stage_counts:
                values = raw_counts.get("byStage", {}).get(stage, {})
                for key in ("challenges", "verify_successes", "replay_responses", "replay_code0"):
                    stage_counts[stage][key] += nonnegative(values.get(key, 0))
        else:
            # Old single-step probes measured business requests but no challenge counters.
            counters["request_count"] += nonnegative(report.get("businessRequests", 0))
        seen = set()
        seen_refs = set()
        known_oec_refs = set()
        for target in report["targets"]:
            ref, external = target.get("targetRef"), target.get("externalId")
            if not isinstance(ref, str) or not ref or not isinstance(external, str) or not external:
                raise ValueError("target source references required")
            key = (ref, external)
            if key in seen or ref in seen_refs:
                raise ValueError("duplicate target within report")
            seen.add(key)
            seen_refs.add(ref)
            kind = input_kind(report, target)
            input_kinds[kind] += 1
            if kind == "known_oec":
                known_oec_refs.add(ref)
            target_status = status(target.get("status"))
            attempts[target_status] += 1
            safe_reason = None if target_status == "completed" else reason(target.get("reason", report.get("reason")))
            attempt = {"targetRef": ref, "sourceExternalId": external,
                       "inputKind": kind, "requestedOecId": target.get("requestedOecId"),
                       "auditHandle": target.get("auditHandle"),
                       "requestedHandle": target.get("requestedHandle"), "status": target_status,
                       "reason": safe_reason, "capturedAt": captured, "reportSha256": digest(report),
                       "historicalCrossSourceIdentityProven": False}
            private_attempts.append(attempt)
            latest_attempt[key] = attempt
            if target_status != "completed":
                continue
            merged = normalized(target["merged"])
            identity = merged["identity"]
            if (target.get("currentPlatformIdentityVerified") is not True
                    or not identity["oecId"] or identity["oecId"] != target.get("oecId")
                    or identity["market"] != "it"
                    or target.get("historicalCrossSourceIdentityProven") is not False):
                raise ValueError("completed target lacks exact current identity evidence")
            if kind == "handle_discovery":
                if (target.get("currentHandleResolved") is not True or not identity["handle"]
                        or identity["handle"] != target.get("requestedHandle")):
                    raise ValueError("discovered handle does not match exact current identity")
            elif (target.get("requestedHandle") is not None
                    or target.get("requestedOecId") != identity["oecId"]
                    or target.get("currentHandleResolved") is not bool(identity["handle"])
                    or target.get("find") is not None):
                raise ValueError("known OEC target lacks exact requested OEC evidence")
            observations = [normalized(target["find"])] if target.get("find") else []
            two_gains = None
            combined_126 = None
            split_2 = None
            for profile in target.get("profiles", []):
                item = normalized(profile["summary"])
                if kind == "known_oec" and item["identity"]["oecId"] != target["requestedOecId"]:
                    raise ValueError("known OEC profile response identity mismatch")
                if profile.get("profileTypes") == [1, 2, 6]:
                    combined_126 = item
                if profile.get("profileTypes") == [2]:
                    split_2 = item
                    if observations:
                        prior = merge_profile_summaries(observations)
                        after = merge_profile_summaries([prior, item])
                        two_gains = sorted(available(after) - available(prior))
                observations.append(item)
            checked = merge_profile_summaries(observations)
            if checked != merged:
                raise ValueError("merged summary differs from observed responses")
            latest_success[key] = {"targetRef": ref, "sourceExternalId": external,
                                  "inputKind": kind, "requestedOecId": target.get("requestedOecId"),
                                  "auditHandle": target.get("auditHandle"),
                                  "capturedAt": captured, "summary": merged,
                                  "profile2IncrementalFields": two_gains,
                                  "profile126": combined_126, "profile2": split_2,
                                  "find": normalized(target["find"]) if target.get("find") else None}
        if known_oec_refs:
            request_log = report.get("requests")
            if isinstance(request_log, list):
                known_oec_reports_with_requests += 1
                # Only refs validated as known_oec in THIS report are eligible.
                for entry in request_log:
                    if (isinstance(entry, dict) and entry.get("targetRef") in known_oec_refs
                            and entry.get("stage") == "find"):
                        known_oec_find_entries += 1
                        responses = entry.get("attempts")
                        if isinstance(responses, list):
                            known_oec_find_responses += len(responses)
            else:
                known_oec_reports_without_requests += 1
    records = []
    new_fields = Counter({key: 0 for key in PROFILE_FIELDS})
    missing_now = Counter({key: 0 for key in PROFILE_FIELDS})
    profile2_fields = Counter({key: 0 for key in PROFILE_FIELDS})
    matched_baselines = []
    current_comparables = []
    profile126 = []
    profile2 = []
    two_comparables = 0
    baseline_targets_gained = 0
    profile2_targets_gained = 0
    reduced_compared = 0
    reduced_coverage_equal = 0
    reduced_identical = 0
    reduced_missing = Counter({key: 0 for key in PROFILE_FIELDS})
    reduced_value_differences = Counter({key: 0 for key in PROFILE_FIELDS})
    for key, result in sorted(latest_success.items()):
        summary = result["summary"]
        old = baseline_by_oec.get(summary["identity"]["oecId"])
        if old is not None:
            matched_baselines.append(old)
            current_comparables.append(summary)
            new_fields.update(available(summary) - available(old))
            baseline_targets_gained += bool(available(summary) - available(old))
            missing_now.update(available(old) - available(summary))
        increment = result["profile2IncrementalFields"]
        if increment is not None:
            two_comparables += 1
            profile2_fields.update(increment)
            profile2_targets_gained += bool(increment)
        if result["profile126"] is not None:
            profile126.append(result["profile126"])
        if result["profile2"] is not None:
            profile2.append(result["profile2"])
        if all(result[name] is not None for name in ("find", "profile126", "profile2")):
            reduced = merge_profile_summaries([result["find"], result["profile2"]])
            reduced_compared += 1
            reduced_coverage_equal += available(reduced) == available(summary)
            reduced_identical += reduced == summary
            reduced_missing.update(available(summary) - available(reduced))
            reduced_value_differences.update(key for key in PROFILE_FIELDS
                                             if reduced["fields"][key] != summary["fields"][key])
        records.append({key: value for key, value in result.items() if key not in {"profile126", "profile2", "find"}} | {
            "currentOecId": summary["identity"]["oecId"], "currentHandle": summary["identity"]["handle"],
            "currentHandleResolved": bool(summary["identity"]["handle"]),
            "currentPlatformIdentityVerified": True, "historicalCrossSourceIdentityProven": False,
            "latestAttempt": latest_attempt[key], "baselineMatchedBy": "exact_current_oec" if old else None,
            "fieldsNewSinceBaseline": sorted(available(summary) - available(old)) if old else None})
    summary = {
        "schema": "bdhub.italy-profile-probe-summary.v1", "market": "it", "mode": "offline_probe_aggregation",
        "probeReports": len(reports), "probeReportStatuses": dict(report_outcomes),
        "targetAttempts": sum(attempts.values()), "targetAttemptStatuses": dict(attempts),
        "inputKindTargetAttempts": dict(input_kinds),
        "latestTargetInputKinds": dict(Counter(row["inputKind"] for row in latest_attempt.values())),
        "successfulObservationInputKinds": dict(Counter(row["inputKind"] for row in records)),
        "knownOecRouteDiagnostics": {"reportsWithRequestLog": known_oec_reports_with_requests,
            "reportsWithoutRequestLog": known_oec_reports_without_requests,
            "findRequestEntries": known_oec_find_entries, "findRecordedHttpResponses": known_oec_find_responses,
            "noFindInRecordedRequests": (known_oec_find_entries == 0)
                if known_oec_reports_with_requests and not known_oec_reports_without_requests else None,
            "scope": "request entries whose targetRef belongs to a validated known_oec target in the same report"},
        "uniqueSourceTargets": len(latest_attempt), "targetsWithSuccessfulObservation": len(records),
        "latestTargetStatuses": dict(Counter(row["status"] for row in latest_attempt.values())),
        "latestUnresolvedReasons": dict(Counter(row["reason"] for row in latest_attempt.values()
                                                if row["status"] != "completed")),
        "uniqueCurrentPlatformOec": len({row["currentOecId"] for row in records}),
        "coverageBasis": "latest_successful_observation_per_targetRef_and_sourceExternalId",
        "coverage": coverage(result["summary"] for result in latest_success.values()),
        "profileCompleteness": completeness(result["summary"] for result in latest_success.values()),
        "baselineComparison": {"provided": baseline is not None, "baselineRecords": len(baseline_by_oec),
            "matchedCurrentOec": len(matched_baselines), "unmatchedCurrentOec": len(records) - len(matched_baselines),
            "join": "exact_current_platform_oec_only", "historicalExternalIdentityAssociationProven": False,
            "baselineCoverageForComparedTargets": coverage(matched_baselines),
            "currentCoverageForComparedTargets": coverage(current_comparables),
            "targetsWithNewlyAvailableFields": baseline_targets_gained,
            "newlyAvailable": dict(new_fields), "noLongerAvailable": dict(missing_now)},
        "profileTypeComparison": {"combined126": coverage(profile126), "split2": coverage(profile2),
            "incremental2ComparableTargets": two_comparables, "incremental2AvailableFields": dict(profile2_fields),
            "targetsWithIncremental2Fields": profile2_targets_gained,
            "incrementBasis": "same_target_Find_and_all_profiles_observed_before_Profile_2"},
        "reducedPlanComparison": {"basis": "offline_recomposition_of_already_observed_Find_and_Profile_2_responses",
            "comparedTargets": reduced_compared, "coverageEquivalentTargets": reduced_coverage_equal,
            "identicalSummaryTargets": reduced_identical, "missingFieldCounts": dict(reduced_missing),
            "differentFieldStateOrValueCounts": dict(reduced_value_differences),
            "candidateOmittedProfileTypes": [1, 2, 6], "independentReducedPlanRuntimeValidated": False},
        "verification": {"reportsWithMeasuredCounters": counter_reports,
            "reportsWithoutChallengeCounters": len(reports) - counter_reports,
            "totals": dict(counters), "byStage": {key: dict(value) for key, value in stage_counts.items()},
            "acceptance": "verification_pass_and_business_replay_code0_are_separate_from_exact_profile_success"},
        "aggregationPlatformRequests": 0, "legacyDatabaseWrites": 0, "realSends": 0,
        "provenance": {"inputDocumentSha256": sorted(fingerprints),
            "baselineDocumentSha256": digest(baseline) if baseline is not None else None},
    }
    private = {"schema": "bdhub.italy-current-profile-observations.v1", "market": "it", "records": records,
               "attempts": private_attempts,
               "unresolvedTargets": [row for row in latest_attempt.values() if row["status"] != "completed"],
               "historicalExternalIdentityAssociationProven": False,
               "identityMeaning": "Exact current platform OEC observation with optional returned handle; auditHandle remains historical. sourceExternalId is preserved without proving historical Kalodata identity.",
               "provenance": summary["provenance"]}
    return summary, private


def in_var(path, *, root=VAR):
    root = root.resolve()
    resolved = Path(path).expanduser().resolve()
    if not resolved.is_relative_to(root) or resolved == root:
        raise ValueError("input and output paths must stay in new project var")
    return resolved


def write_documents(output, summary, private, *, root=VAR):
    output = in_var(output, root=root)
    # Create exclusively: never overwrite a previous report, even an empty folder.
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    for name, value in (("summary.json", summary), ("current-identities.private.json", private)):
        descriptor = os.open(output / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            json.dump(value, file, ensure_ascii=False, indent=2)
            file.write("\n")
    return output


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reports", type=Path, nargs="+", required=True)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    documents = [json.loads(in_var(path).read_text()) for path in args.reports]
    baseline = json.loads(in_var(args.baseline).read_text()) if args.baseline else None
    summary, private = build_documents(documents, baseline)
    output = write_documents(args.output, summary, private)
    print(json.dumps({"summary": str(output / "summary.json"), "probeReports": summary["probeReports"],
                      "targetsWithSuccessfulObservation": summary["targetsWithSuccessfulObservation"],
                      "aggregationPlatformRequests": 0, "realSends": 0}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
