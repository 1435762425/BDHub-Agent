"""Offline parsing, durable discovery, identity reuse and pause contracts."""
import concurrent.futures
import contextlib
from copy import deepcopy
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from lib.creator_discovery import CreatorDiscoveryError, CreatorDiscoveryStore, CreatorDiscoveryWorker, preview
from lib.creator_identity import CreatorIdentityStore
from lib.profile_completion import summarize_profile, merge_profile_summaries

SPEC = importlib.util.spec_from_file_location("discovery_cli_subject", ROOT / "scripts/creator-discovery.py")
CLI = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CLI)
REFRESH_SPEC = importlib.util.spec_from_file_location("discovery_shared_worker_cli", ROOT / "scripts/creator-profile-refresh.py")
SHARED = importlib.util.module_from_spec(REFRESH_SPEC)
REFRESH_SPEC.loader.exec_module(SHARED)
T0 = 1789174800.0


def report_for(target, *, oec="123456789", missing=False, profile_failure=False):
    ref, handle = target["ref"], target["handle"]
    found = summarize_profile({"handle": {"value": handle}, "creator_oecuid": {"value": oec},
                               "selection_region": {"value": "IT"}, "follower_cnt": {"value": 20}})
    combined = summarize_profile({"handle": {"value": handle}, "creator_oecuid": {"value": oec},
                                  "selection_region": {"value": "IT"}, "units_sold": {"value": 2}})
    item = {"targetRef": ref, "externalId": target["externalId"], "inputKind": "handle_discovery",
            "requestedHandle": handle, "requestedOecId": None, "oecId": oec, "status": "completed",
            "currentHandleResolved": True, "currentPlatformIdentityVerified": True,
            "historicalCrossSourceIdentityProven": False, "find": found,
            "profiles": [{"profileTypes": [1, 2, 6], "summary": combined}, {"profileTypes": [2], "summary": combined}],
            "merged": merge_profile_summaries([found, combined, combined])}
    requests = [{"targetRef": ref, "stage": stage, "status": "returned", "httpStatus": 200, "code": "0", "verificationRequired": False}
                for stage in ("find", "profile", "profile")]
    value = {"schema": "bdhub.italy-profile-probe.v3", "market": "it", "account": "acc6", "status": "completed",
             "startedAt": "2026-09-12T01:00:00Z", "finishedAt": "2026-09-12T01:00:03Z", "oldDatabaseWrites": 0,
             "realSends": 0, "identityFileUnchanged": True, "requests": requests, "targets": [item],
             "counters": {"request_count": 3, "challenge_count": 0, "captcha_success_count": 0,
                          "captcha_replay_response_count": 0, "captcha_replay_code0_count": 0, "code10000_missing_header_count": 0}}
    if missing:
        value["requests"] = requests[:1]
        value["counters"]["request_count"] = 1
        value["targets"] = [{"targetRef": ref, "externalId": target["externalId"], "inputKind": "handle_discovery",
                             "requestedHandle": handle, "status": "unresolved", "reason": "no_exact_handle", "currentHandleResolved": False}]
    if profile_failure:
        value.update(status="blocked", reason="request_or_signer_error")
        item.pop("status")
        item.pop("merged")
        item.pop("currentPlatformIdentityVerified")
        item["profiles"] = []
        value["requests"] = requests[:1] + [{"targetRef": ref, "stage": "profile", "status": "error", "errorType": "RuntimeError"}]
    return value


class DiscoveryPreviewTests(unittest.TestCase):
    def test_formats_duplicates_invalid_rows_and_id_like_inputs_are_explicit(self):
        value = preview("it", " Test ", " alice,@ALICE\nhttps://www.tiktok.com/@bob?lang=it, bad handle!!\n123456789012345\n@123456789012345")
        self.assertEqual(value["counts"], {"total": 6, "valid": 3, "duplicate": 1, "invalid": 2})
        self.assertEqual([row["index"] for row in value["items"]], [1, 2, 3, 4, 5, 6])
        self.assertEqual(value["items"][1]["duplicateOf"], 1)
        self.assertEqual(value["items"][2]["handle"], "bob")
        self.assertEqual(value["items"][3]["reason"], "invalid_handle")
        self.assertEqual(value["items"][4]["reason"], "looks_like_id")
        self.assertEqual(value["sourceLabel"], "Test")
        self.assertEqual(len(value["previewHash"]), 64)

    def test_url_hosts_and_url_shape_are_not_guessed(self):
        values = ["http://tiktok.com/@alice", "https://evil.invalid/@alice", "https://tiktok.com.evil.invalid/@alice",
                  "https://tiktok.com:443/@alice", "https://user@tiktok.com/@alice", "https://tiktok.com/video/123"]
        parsed = preview("it", "urls", "\n".join(values))
        self.assertEqual(parsed["counts"]["invalid"], len(values))
        self.assertTrue(all(row["reason"] == "unsupported_url" for row in parsed["items"]))

    def test_preview_is_pure_bounded_and_hash_freezes_original_text(self):
        with patch.object(CLI, "CreatorDiscoveryStore", side_effect=AssertionError("preview must not open storage")), \
                patch.object(CLI.sys, "argv", ["discovery", "preview"]), \
                patch.object(CLI.sys, "stdin", io.StringIO(json.dumps({"market": "it", "sourceLabel": "test", "text": "alice"}))), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(CLI.main(), 0)
        self.assertNotEqual(preview("it", "test", "alice")["previewHash"], preview("it", "test", "alice\n")["previewHash"])
        for text in ("\n".join(f"creator{i}" for i in range(501)), "中" * 22000):
            with self.subTest(length=len(text)), self.assertRaises(CreatorDiscoveryError) as error:
                preview("it", "test", text)
            self.assertEqual(error.exception.code, "input_limit_exceeded")
        self.assertFalse(preview("it", "empty", " , \n")["canSubmit"])


class CreatorDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.var = Path(self.temporary.name)
        self.clock = T0
        self.identities = CreatorIdentityStore(self.var / "creator-identities.sqlite")
        self.addCleanup(self.identities.close)
        self.store = CreatorDiscoveryStore(self.var, now=lambda: self.clock)
        self.addCleanup(self.store.close)
        self.calls = []

    def submit(self, text="alice", request="submit:1"):
        value = preview("it", "test source", text)
        return self.store.submit("it", "test source", text, value["previewHash"], request)

    def test_zero_request_guard_busy_defers_without_blocking_batch(self):
        batch=self.submit();worker=CreatorDiscoveryWorker(self.store,executor=self.execute);item=self.store.claim(worker.owner)
        report={'schema':'bdhub.italy-profile-probe.v3','market':'it','account':'acc6','oldDatabaseWrites':0,'realSends':0,'targets':[],'requests':[],'errorType':'BlockingIOError','reason':'probe_initialization_or_validation_error'}
        worker._settle(item,report)
        self.assertIsNone(self.store.claim(worker.owner));self.clock+=31;retry=self.store.claim(worker.owner)
        self.assertEqual(retry['attempt_no'],2);self.assertEqual(worker._paths(retry)[1].name,'attempt-2')
        self.assertEqual(self.store.detail(batch['id'])['batch']['counts']['blocked'],0)

    def execute(self, target_file, output):
        target = json.loads(target_file.read_text())["targets"][0]
        self.calls.append(target)
        output.mkdir()
        (output / "report.private.json").write_text(json.dumps(report_for(target, missing=target["handle"] == "missing")))
        return 0

    def worker(self, execute=None):
        return CreatorDiscoveryWorker(self.store, executor=execute or self.execute)

    def lead(self, handle="alice", source="old-pending"):
        return self.identities.record_handle_lead("it", handle, "2026-09-10T00:00:00Z", source)

    def test_submit_is_idempotent_and_creates_no_pending_lead(self):
        first = self.submit("alice,@alice,bad handle!!")
        self.assertEqual(first["id"], self.submit("alice,@alice,bad handle!!")["id"])
        self.assertEqual(first["counts"]["queued"], 1)
        self.assertEqual(first["counts"]["duplicate"], 1)
        self.assertEqual(first["counts"]["invalid"], 1)
        self.assertEqual(self.identities.stats()["leads"], 0)
        with self.assertRaises(CreatorDiscoveryError) as conflict:
            self.submit("bob")
        self.assertEqual(conflict.exception.status, 409)
        with self.assertRaises(CreatorDiscoveryError) as changed:
            self.store.submit("it", "test source", "alice\n", preview("it", "test source", "alice")["previewHash"], "changed")
        self.assertEqual(changed.exception.code, "preview_mismatch")

    def test_duplicate_is_found_once_and_new_and_existing_identities_are_distinct_outcomes(self):
        self.submit("alice,@alice")
        result = self.worker().run_once()
        self.assertEqual(result["batch"]["counts"]["created"], 1)
        self.assertEqual(result["items"][0]["outcome"], "created")
        first_id = result["items"][0]["creatorId"]
        self.assertEqual(len(self.calls), 1)
        self.assertNotIn("oecId", self.calls[0])
        self.assertIsNone(self.worker().run_once())
        self.submit("alice", "new-explicit-submit")
        again = self.worker().run_once()
        self.assertEqual(again["items"][0]["outcome"], "existing")
        self.assertEqual(again["items"][0]["creatorId"], first_id)
        self.assertEqual(len(self.calls), 2)  # no alias cache bypasses Find

    def test_success_resolves_all_matching_pending_but_does_not_prove_external_history(self):
        first, second = self.lead(source="old:1"), self.lead(source="old:2")
        self.lead(handle="other", source="other")
        self.submit()
        result = self.worker().run_once()
        self.assertEqual(result["items"][0]["status"], "completed")
        self.assertEqual(self.identities.stats()["leads"], 3)
        self.assertEqual(self.identities.stats()["pendingLeads"], 1)
        for lead in (first, second):
            resolved = self.identities.get_handle_lead(lead["leadId"])
            self.assertEqual(resolved["creatorId"], result["items"][0]["creatorId"])
            self.assertFalse(resolved["historicalCrossSourceIdentityProven"])

    def test_missing_handle_reuses_pending_and_continues_next_row(self):
        self.lead(handle="missing")
        self.submit("missing,alice")
        first = self.worker().run_once()
        self.assertEqual(first["items"][0]["status"], "unresolved")
        self.assertEqual(first["items"][1]["status"], "queued")
        self.assertEqual(self.identities.stats()["pendingLeads"], 1)
        second = self.worker().run_once()
        self.assertEqual(second["batch"]["status"], "completed")
        self.submit("missing", "explicit-missing-again")
        self.worker().run_once()
        self.assertEqual(self.identities.stats()["pendingLeads"], 1)

    def test_old_resolved_lead_is_not_reassigned_when_handle_is_reused(self):
        old_lead = self.lead()
        old = self.identities.resolve_handle_lead(old_lead["leadId"], "999", "2026-09-10T01:00:00Z", "old-find", exact_find={
            "market": "it", "queriedHandle": "alice", "returnedHandle": "alice", "oecId": "999",
            "httpStatus": 200, "code": 0, "verificationRequired": False})
        self.submit()
        result = self.worker().run_once()
        self.assertNotEqual(result["items"][0]["creatorId"], old["identity"]["creatorId"])
        self.assertEqual(self.identities.get_handle_lead(old_lead["leadId"])["creatorId"], old["identity"]["creatorId"])
        self.assertEqual(len(self.identities.find_handle_candidates("it", "alice")), 2)

    def test_find_success_profile_failure_preserves_identity_and_stops_only_current_item(self):
        self.lead()
        batch = self.submit("alice,bob")
        def profile_error(target_file, output):
            target = json.loads(target_file.read_text())["targets"][0]
            self.calls.append(target)
            output.mkdir()
            (output / "report.private.json").write_text(json.dumps(report_for(target, profile_failure=True)))
            return 2
        result = self.worker(profile_error).run_once()
        first, second = result["items"]
        self.assertEqual(first["status"], "blocked")
        self.assertEqual(first["outcome"], "identity_only")
        self.assertIsNotNone(first["creatorId"])
        self.assertEqual(first["oecId"], "123456789")
        self.assertEqual(second["status"], "queued")
        self.assertEqual(result["batch"]["counts"]["identityOnly"], 1)
        self.assertEqual(self.identities.stats()["pendingLeads"], 0)
        self.assertIsNone(self.worker(profile_error).run_once())
        self.store.control(batch["id"], "resume", "resume:1")
        self.worker().run_once()
        self.assertEqual([call["handle"] for call in self.calls], ["alice", "bob"])

    def test_pause_allows_inflight_settlement_but_stops_next_claim(self):
        batch = self.submit("alice,bob")
        def pausing(target_file, output):
            self.store.control(batch["id"], "pause", "pause:1")
            return self.execute(target_file, output)
        result = self.worker(pausing).run_once()
        self.assertEqual(result["batch"]["status"], "paused")
        self.assertEqual(result["items"][0]["status"], "completed")
        self.assertEqual(result["items"][1]["status"], "queued")
        self.assertIsNone(self.worker().run_once())
        self.store.control(batch["id"], "resume", "resume:1")
        self.assertEqual(self.worker().run_once()["batch"]["status"], "completed")
        self.assertEqual(len(self.calls), 2)

    def test_partial_find_requires_explicit_it_and_unchanged_identity_and_no_conflict(self):
        for change in ("unknown_market", "identity_changed", "profile_mismatch"):
            with self.subTest(change=change):
                self.submit("alice", "partial-reject:" + change)
                def partial(target_file, output):
                    target = json.loads(target_file.read_text())["targets"][0]
                    value = report_for(target, profile_failure=True)
                    if change == "unknown_market":
                        value["targets"][0]["find"] = summarize_profile({"handle": {"value": "alice"}, "creator_oecuid": {"value": "123456789"}})
                    elif change == "identity_changed":
                        value["identityFileUnchanged"] = False
                    else:
                        value["reason"] = "profile_market_mismatch"
                    output.mkdir()
                    (output / "report.private.json").write_text(json.dumps(value))
                    return 2
                result = self.worker(partial).run_once()
                self.assertEqual(result["items"][0]["status"], "blocked")
                self.assertEqual(result["items"][0]["reason"], "probe_report_invalid")
                self.assertIsNone(result["items"][0]["creatorId"])
                self.assertEqual(self.identities.stats()["identities"], 0)

    def test_paused_batch_remains_paused_when_inflight_request_fails(self):
        batch = self.submit("alice,bob")
        def pausing_failure(*_):
            self.store.control(batch["id"], "pause", "pause-before-failure")
            raise RuntimeError("private-error")
        result = self.worker(pausing_failure).run_once()
        self.assertEqual(result["batch"]["status"], "paused")
        self.assertEqual(result["batch"]["errorCode"], "probe_failed")
        self.assertEqual([row["status"] for row in result["items"]], ["blocked", "queued"])
        self.assertIsNone(self.worker().run_once())

    def test_account_error_blocks_batch_without_failing_remaining_rows(self):
        self.submit("alice,bob,charlie")
        def blocked(_target, output):
            output.mkdir()
            (output / "report.private.json").write_text(json.dumps({"schema": "bdhub.italy-profile-probe.v3", "market": "it", "account": "acc6",
                "status": "blocked", "finishedAt": "2026-09-12T01:00:03Z", "oldDatabaseWrites": 0, "realSends": 0,
                "reason": "account_not_startable", "targets": [], "requests": []}))
            return 2
        result = self.worker(blocked).run_once()
        self.assertEqual([row["status"] for row in result["items"]], ["blocked", "queued", "queued"])
        self.assertEqual(result["batch"]["errorCode"], "account_not_startable")
        self.assertIsNone(self.worker(blocked).run_once())
        self.assertEqual(self.identities.stats()["identities"], 0)

    def test_find_only_report_cannot_claim_completed_profile_collection(self):
        self.submit()
        def find_only(target_file, output):
            target = json.loads(target_file.read_text())["targets"][0]
            value = report_for(target)
            item = value["targets"][0]
            item["profiles"] = []
            item["merged"] = item["find"]
            value["requests"] = value["requests"][:1]
            output.mkdir()
            (output / "report.private.json").write_text(json.dumps(value))
            return 0
        result = self.worker(find_only).run_once()
        self.assertEqual(result["items"][0]["status"], "blocked")
        self.assertEqual(result["items"][0]["outcome"], "identity_only")
        self.assertEqual(result["items"][0]["reason"], "probe_report_invalid")
        self.assertEqual(self.identities.stats()["identities"], 1)

    def test_control_replay_does_not_reapply_old_pause_or_accept_different_action(self):
        batch = self.submit("alice,bob")
        self.store.control(batch["id"], "pause", "control:pause")
        self.store.control(batch["id"], "resume", "control:resume")
        replay = self.store.control(batch["id"], "pause", "control:pause")
        self.assertEqual(replay["status"], "queued")
        with self.assertRaises(CreatorDiscoveryError) as conflict:
            self.store.control(batch["id"], "resume", "control:pause")
        self.assertEqual(conflict.exception.code, "idempotency_conflict")

    def test_recovery_replays_final_and_preserves_created_classification(self):
        batch = self.submit()
        old = self.worker()
        item = self.store.claim(old.owner)
        target = {"ref": item["id"], "handle": item["handle"], "externalId": item["id"]}
        report = report_for(target)
        _, output, _ = old._paths(item)
        output.mkdir(parents=True)
        (output / "report.private.json").write_text(json.dumps(report))
        with patch.object(self.store, "finish", side_effect=SystemExit("simulated crash after identity commit")):
            with self.assertRaises(SystemExit):
                old._settle(item, report)
        before = self.identities.stats()
        self.clock += 241
        never = Mock(side_effect=AssertionError("do not repeat network"))
        result = self.worker(never).run_once()
        self.assertEqual(result["batch"]["id"], batch["id"])
        self.assertEqual(result["items"][0]["outcome"], "created")
        self.assertEqual(self.identities.stats(), before)
        never.assert_not_called()

    def test_expired_attempt_without_final_is_not_retried_and_stale_owner_cannot_import(self):
        self.submit("alice,bob")
        old = self.worker()
        item = self.store.claim(old.owner)
        self.clock += 241
        never = Mock(side_effect=AssertionError("do not retry unknown attempt"))
        result = self.worker(never).run_once()
        self.assertEqual(result["items"][0]["reason"], "lease_expired_no_report")
        self.assertEqual(result["items"][1]["status"], "queued")
        old._settle(item, report_for({"ref": item["id"], "handle": "alice", "externalId": item["id"]}))
        self.assertEqual(self.identities.stats()["identities"], 0)
        never.assert_not_called()

    def test_claims_are_globally_serial_and_parallel_submit_is_idempotent(self):
        def submit(_index):
            with CreatorDiscoveryStore(self.var, now=lambda: self.clock) as store:
                p = preview("it", "test source", "alice,bob")
                return store.submit("it", "test source", "alice,bob", p["previewHash"], "parallel")["id"]
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            ids = list(executor.map(submit, range(8)))
        self.assertEqual(len(set(ids)), 1)
        self.assertIsNotNone(self.store.claim("worker:1"))
        self.assertIsNone(self.store.claim("worker:2"))

    def test_pending_history_is_not_automatically_queued_and_cli_rejects_extra_paths(self):
        self.lead()
        self.assertEqual(self.store.list_batches()["batches"], [])
        self.assertIsNone(self.worker().run_once())
        with patch.object(CLI.sys, "stdin", io.StringIO(json.dumps({"batchId": "anything", "database": "/private/path"}))):
            with self.assertRaises(CreatorDiscoveryError):
                CLI.read_request("detail")

    def test_shared_worker_once_runs_refresh_before_discovery_sequentially(self):
        events = []
        refresh = Mock()
        refresh.run_once.side_effect = lambda: events.append("refresh") or {"id": "job"}
        discovery = Mock()
        discovery.run_once.side_effect = lambda: events.append("discovery") or {"batch": {"id": "batch"}}
        store_context, refresh_context, discovery_context, discovery_store_context = [Mock() for _ in range(4)]
        for context, value in ((store_context, self.store), (refresh_context, refresh), (discovery_context, discovery), (discovery_store_context, self.store)):
            context.__enter__ = Mock(return_value=value)
            context.__exit__ = Mock(return_value=False)
        output = io.StringIO()
        with patch.object(SHARED.sys, "argv", ["refresh", "worker", "--once"]), patch.object(SHARED.signal, "signal"), \
                patch.object(SHARED, "ProfileRefreshStore", return_value=store_context), \
                patch.object(SHARED, "ProfileRefreshWorker", return_value=refresh_context), \
                patch.object(SHARED, "CreatorDiscoveryStore", return_value=discovery_store_context), \
                patch.object(SHARED, "CreatorDiscoveryWorker", return_value=discovery_context), contextlib.redirect_stdout(output):
            self.assertEqual(SHARED.main(), 0)
        self.assertEqual(events, ["refresh", "discovery"])
        self.assertEqual(json.loads(output.getvalue()), {"refresh": {"id": "job"}, "discovery": {"id": "batch"}})


if __name__ == "__main__":
    unittest.main()
