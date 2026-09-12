"""Offline contracts for aggregate profile diagnostics and current identity export."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import stat
import tempfile
import unittest

PATH = Path(__file__).resolve().parents[1] / "scripts/summarize-italy-profile-probes.py"
SPEC = importlib.util.spec_from_file_location("probe_summary", PATH)
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)
from lib.profile_completion import summarize_profile, merge_profile_summaries


def summary(**fields):
    return summarize_profile({"creator_oecuid": {"value": "123456789"}, "handle": {"value": "private_handle"},
                              "selection_region": {"value": "IT"}, **{key: {"value": value} for key, value in fields.items()}})


def report(at="2026-09-12T01:00:00+00:00"):
    found = summary(follower_cnt=12)
    combined = summary(follower_cnt=12, units_sold=0)
    split = summary(video_publish_cnt_30d=0, ec_video_gpm={"value": "1.20", "symbol": "EUR"})
    return {"schema": "bdhub.italy-profile-probe.v2", "market": "it", "status": "completed",
            "finishedAt": at, "oldDatabaseWrites": 0, "realSends": 0,
            "counters": {"request_count": 4, "challenge_count": 1, "captcha_success_count": 1,
                         "captcha_replay_response_count": 1, "captcha_replay_code0_count": 1,
                         "code10000_missing_header_count": 0},
            "targets": [{"targetRef": "test-ref", "externalId": "external-not-oec", "status": "completed",
                         "currentPlatformIdentityVerified": True, "currentHandleResolved": True,
                         "historicalCrossSourceIdentityProven": False, "oecId": "123456789",
                         "requestedHandle": "private_handle", "find": found,
                         "profiles": [{"profileTypes": [1, 2, 6], "summary": combined},
                                      {"profileTypes": [2], "summary": split}],
                         "merged": merge_profile_summaries([found, combined, split])}]}


def known_report(current_handle="renamed_handle"):
    value = report()
    value["schema"] = "bdhub.italy-profile-probe.v3"
    combined = summary(handle=current_handle, follower_cnt=12, units_sold=0)
    split = summary(handle=current_handle, video_publish_cnt_30d=0)
    target = value["targets"][0]
    target.pop("find")
    target.update(inputKind="known_oec", requestedHandle=None, requestedOecId="123456789",
                  auditHandle="old_handle", currentHandleResolved=bool(current_handle),
                  profiles=[{"profileTypes": [1, 2, 6], "summary": combined},
                            {"profileTypes": [2], "summary": split}],
                  merged=merge_profile_summaries([combined, split]))
    value["requests"] = [{"targetRef": "test-ref", "stage": "profile", "attempts": [{}]}]
    return value


class ProbeSummaryTests(unittest.TestCase):
    def test_known_oec_without_handle_is_verified_without_inventing_old_name(self):
        public, private = M.build_documents([known_report(None)])
        item = private["records"][0]
        self.assertTrue(item["currentPlatformIdentityVerified"])
        self.assertIsNone(item["currentHandle"])
        self.assertFalse(item["currentHandleResolved"])
        self.assertEqual(item["auditHandle"], "old_handle")
        self.assertIsNone(item["summary"]["identity"]["handle"])
        self.assertEqual(public["inputKindTargetAttempts"]["known_oec"], 1)
        self.assertTrue(public["knownOecRouteDiagnostics"]["noFindInRecordedRequests"])
        self.assertEqual(public["reducedPlanComparison"]["comparedTargets"], 0)

    def test_known_oec_adopts_returned_name_without_requiring_audit_match(self):
        public, private = M.build_documents([known_report()])
        item = private["records"][0]
        self.assertEqual(item["currentHandle"], "renamed_handle")
        self.assertTrue(item["currentHandleResolved"])
        self.assertEqual(item["requestedOecId"], item["currentOecId"])
        self.assertEqual(item["auditHandle"], "old_handle")
        self.assertNotIn("renamed_handle", json.dumps(public))

    def test_known_oec_rejects_wrong_requested_response_or_handle_resolution(self):
        for mutate in (
            lambda t: t.update(requestedOecId="999999"),
            lambda t: t.update(oecId="999999"),
            lambda t: t.update(requestedHandle="old_handle"),
            lambda t: t.update(currentHandleResolved=False),
            lambda t: t["profiles"][0].update(summary=summary(creator_oecuid="999999")),
        ):
            item = known_report()
            mutate(item["targets"][0])
            with self.assertRaises(ValueError):
                M.build_documents([item])
        item = known_report(None)
        item["targets"][0]["currentHandleResolved"] = True
        with self.assertRaises(ValueError):
            M.build_documents([item])

    def test_known_route_diagnostic_uses_only_same_report_validated_target_refs(self):
        item = known_report()
        item["requests"] += [{"targetRef": "other-private-ref", "stage": "find", "attempts": [{}]},
                             {"targetRef": "not-a-target", "stage": "find", "attempts": [{}]}]
        public, _ = M.build_documents([item])
        self.assertEqual(public["knownOecRouteDiagnostics"]["findRequestEntries"], 0)
        item["requests"].append({"targetRef": "test-ref", "stage": "find", "attempts": [{}, {}]})
        public, _ = M.build_documents([item])
        diagnostics = public["knownOecRouteDiagnostics"]
        self.assertEqual(diagnostics["findRequestEntries"], 1)
        self.assertEqual(diagnostics["findRecordedHttpResponses"], 2)
        self.assertFalse(diagnostics["noFindInRecordedRequests"])

    def test_missing_known_route_request_log_does_not_claim_zero_find_validation(self):
        item = known_report()
        item.pop("requests")
        public, _ = M.build_documents([item])
        self.assertIsNone(public["knownOecRouteDiagnostics"]["noFindInRecordedRequests"])

    def test_v3_handle_discovery_keeps_exact_handle_rule(self):
        item = report()
        item["schema"] = "bdhub.italy-profile-probe.v3"
        item["targets"][0].update(inputKind="handle_discovery", requestedOecId=None)
        public, _ = M.build_documents([item])
        self.assertEqual(public["inputKindTargetAttempts"]["handle_discovery"], 1)
        item["targets"][0]["requestedHandle"] = "different_handle"
        with self.assertRaises(ValueError):
            M.build_documents([item])

    def test_counts_baseline_zero_and_profile2_gain(self):
        baseline = {"schemaVersion": 1, "market": "it", "records": [
            {"oecId": "123456789", "summary": summary(follower_cnt=10, units_sold=9)}]}
        public, private = M.build_documents([report()], baseline)
        self.assertEqual(public["coverage"]["fieldStates"]["units_sold"]["zero"], 1)
        self.assertEqual(public["baselineComparison"]["newlyAvailable"]["video_publish_cnt_30d"], 1)
        self.assertEqual(public["profileTypeComparison"]["incremental2AvailableFields"]["video_publish_cnt_30d"], 1)
        self.assertEqual(public["profileTypeComparison"]["incremental2AvailableFields"]["follower_cnt"], 0)
        self.assertEqual(public["verification"]["totals"]["captcha_replay_code0_count"], 1)
        self.assertEqual(public["baselineComparison"]["targetsWithNewlyAvailableFields"], 1)
        self.assertEqual(public["profileTypeComparison"]["targetsWithIncremental2Fields"], 1)
        self.assertFalse(private["records"][0]["historicalCrossSourceIdentityProven"])
        self.assertEqual(private["records"][0]["sourceExternalId"], "external-not-oec")
        self.assertEqual(private["records"][0]["baselineMatchedBy"], "exact_current_oec")
        for token in ("private_handle", "123456789", "external-not-oec"):
            self.assertNotIn(token, json.dumps(public))

    def test_completed_identity_does_not_imply_complete_profile(self):
        public, _ = M.build_documents([report()])
        result = public["profileCompleteness"]
        self.assertEqual(result["identityVerifiedTargets"], 1)
        self.assertEqual(result["all22FieldsAvailableTargets"], 0)
        self.assertEqual(result["allCoreFieldsAvailableTargets"], 0)
        self.assertEqual(result["unavailableStateTargetCounts"]["absent"], 1)
        self.assertEqual(public["uniqueCurrentPlatformOec"], 1)

    def test_reduced_plan_reports_lost_fields_instead_of_claiming_equivalence(self):
        public, _ = M.build_documents([report()])
        reduced = public["reducedPlanComparison"]
        self.assertEqual(reduced["comparedTargets"], 1)
        self.assertEqual(reduced["coverageEquivalentTargets"], 0)
        self.assertEqual(reduced["identicalSummaryTargets"], 0)
        self.assertEqual(reduced["missingFieldCounts"]["units_sold"], 1)

    def test_reduced_plan_equal_values_are_only_offline_evidence(self):
        item = report()
        target = item["targets"][0]
        target["profiles"][1]["summary"] = summary(units_sold=0, video_publish_cnt_30d=0,
                                                      ec_video_gpm={"value": "1.20", "symbol": "EUR"})
        target["merged"] = merge_profile_summaries([target["find"], *[p["summary"] for p in target["profiles"]]])
        public, _ = M.build_documents([item])
        reduced = public["reducedPlanComparison"]
        self.assertEqual(reduced["coverageEquivalentTargets"], 1)
        self.assertEqual(reduced["identicalSummaryTargets"], 1)
        self.assertFalse(reduced["independentReducedPlanRuntimeValidated"])

    def test_latest_failure_is_not_hidden_by_previous_success(self):
        newer = report("2026-09-12T02:00:00+00:00")
        newer["targets"][0] = {"targetRef": "test-ref", "externalId": "external-not-oec", "status": "unresolved"}
        public, private = M.build_documents([newer, report()])
        self.assertEqual(public["targetAttempts"], 2)
        self.assertEqual(public["uniqueSourceTargets"], 1)
        self.assertEqual(public["targetsWithSuccessfulObservation"], 1)
        self.assertEqual(public["latestTargetStatuses"], {"unresolved": 1})
        self.assertEqual(private["records"][0]["latestAttempt"]["status"], "unresolved")
        self.assertEqual(private["records"][0]["capturedAt"], "2026-09-12T01:00:00+00:00")
        self.assertEqual(len(private["attempts"]), 2)
        self.assertEqual(private["unresolvedTargets"][0]["sourceExternalId"], "external-not-oec")
        self.assertEqual(public["latestUnresolvedReasons"], {"not_reported": 1})

    def test_public_reason_is_allowlisted_and_private_unresolved_is_retained(self):
        item = report()
        item["targets"][0].update(status="unresolved", reason="private_handle")
        public, private = M.build_documents([item])
        self.assertEqual(public["latestUnresolvedReasons"], {"other": 1})
        self.assertNotIn("private_handle", json.dumps(public))
        self.assertEqual(private["unresolvedTargets"][0]["requestedHandle"], "private_handle")

    def test_unmatched_baseline_is_not_fabricated_as_empty(self):
        baseline = {"schemaVersion": 1, "market": "it", "records": []}
        public, private = M.build_documents([report()], baseline)
        self.assertEqual(public["baselineComparison"]["matchedCurrentOec"], 0)
        self.assertEqual(sum(public["baselineComparison"]["newlyAvailable"].values()), 0)
        self.assertIsNone(private["records"][0]["fieldsNewSinceBaseline"])

    def test_old_blocked_probe_does_not_invent_verification_measurements(self):
        old = {"schema": "bdhub.italy-profile-probe.v1", "market": "it", "status": "blocked",
               "finishedAt": "2026-09-12T00:00:00Z", "oldDatabaseWrites": 0, "realSends": 0,
               "businessRequests": 1, "targets": [{"targetRef": "test-ref", "externalId": "external-not-oec"}]}
        public, _ = M.build_documents([old, report()])
        self.assertEqual(public["verification"]["reportsWithoutChallengeCounters"], 1)
        self.assertEqual(public["verification"]["totals"]["request_count"], 5)
        self.assertEqual(public["verification"]["totals"]["challenge_count"], 1)

    def test_rejects_identity_or_merged_evidence_disagreement(self):
        for mutation in (
            lambda d: d["targets"][0].update(oecId="987"),
            lambda d: d["targets"][0].update(currentPlatformIdentityVerified=False),
            lambda d: d["targets"][0].update(historicalCrossSourceIdentityProven=True),
            lambda d: d["targets"][0].update(merged=summary()),
            lambda d: d.update(market="mx"),
            lambda d: d.update(realSends=1),
        ):
            item = report()
            mutation(item)
            with self.assertRaises(ValueError):
                M.build_documents([item])

    def test_duplicates_do_not_inflate_metrics(self):
        item = report()
        with self.assertRaises(ValueError):
            M.build_documents([item, deepcopy(item)])
        item["targets"].append(deepcopy(item["targets"][0]))
        with self.assertRaises(ValueError):
            M.build_documents([item])

    def test_export_is_private_exclusive_and_contained(self):
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            output = base / "new-report"
            public, private = M.build_documents([report()])
            M.write_documents(output, public, private, root=base)
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o700)
            for name in ("summary.json", "current-identities.private.json"):
                self.assertEqual(stat.S_IMODE((output / name).stat().st_mode), 0o600)
            with self.assertRaises(FileExistsError):
                M.write_documents(output, public, private, root=base)
            with self.assertRaises(ValueError):
                M.in_var(base / ".." / "outside", root=base)
            link = base / "escape"
            link.symlink_to(base.parent, target_is_directory=True)
            with self.assertRaises(ValueError):
                M.in_var(link / "outside", root=base)


if __name__ == "__main__":
    unittest.main()
