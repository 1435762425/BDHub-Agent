"""Offline durable queue contracts; the only executor used here is a fake."""
import concurrent.futures
import contextlib
from copy import deepcopy
from datetime import datetime
import importlib.util
import io
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from lib.creator_identity import CreatorIdentityStore
from lib.profile_completion import summarize_profile
from lib.profile_refresh import ProfileRefreshError, ProfileRefreshStore, ProfileRefreshWorker
from lib import profile_refresh

CLI_SPEC = importlib.util.spec_from_file_location("refresh_cli_subject", ROOT / "scripts/creator-profile-refresh.py")
CLI = importlib.util.module_from_spec(CLI_SPEC)
CLI_SPEC.loader.exec_module(CLI)
T0 = datetime.fromisoformat("2026-09-12T01:00:00+00:00").timestamp()


def report_for(target, *, handle="new.handle"):
    summary = summarize_profile({"creator_oecuid": {"value": target["oecId"]}, "selection_region": {"value": "IT"},
                                 "handle": {"value": handle}, "follower_cnt": {"value": 100}})
    return {"schema": "bdhub.italy-profile-probe.v3", "market": "it", "account": "acc6", "status": "completed",
            "startedAt": "2026-09-12T01:00:00Z", "finishedAt": "2026-09-12T01:00:03Z",
            "oldDatabaseWrites": 0, "realSends": 0, "identityFileUnchanged": True,
            "counters": {"request_count": 2, "challenge_count": 0, "captcha_success_count": 0,
                         "captcha_replay_response_count": 0, "captcha_replay_code0_count": 0, "code10000_missing_header_count": 0},
            "targets": [{"targetRef": target["ref"], "externalId": target["externalId"], "inputKind": "known_oec",
                         "requestedOecId": target["oecId"], "requestedHandle": None, "auditHandle": target.get("handle"),
                         "oecId": target["oecId"], "status": "completed", "currentPlatformIdentityVerified": True,
                         "currentHandleResolved": bool(handle), "historicalCrossSourceIdentityProven": False,
                         "profiles": [{"profileTypes": kind, "summary": deepcopy(summary)} for kind in ([1, 2, 6], [2])],
                         "merged": summary}],
            "requests": [{"targetRef": target["ref"], "stage": "profile", "profileTypes": kind, "status": "returned",
                          "httpStatus": 200, "code": "0", "verificationRequired": False} for kind in ([1, 2, 6], [2])]}


class ProfileRefreshJobsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.var = Path(self.temporary.name)
        self.clock = T0
        self.identities = CreatorIdentityStore(self.var / "creator-identities.sqlite")
        self.addCleanup(self.identities.close)
        self.creator = self.identities.observe_profile("it", "123456789", "old.handle", "2026-09-10T00:00:00Z", "baseline")
        self.other = self.identities.observe_profile("it", "987654321", "other.creator", "2026-09-10T00:00:00Z", "baseline:other")
        self.store = ProfileRefreshStore(self.var, now=lambda: self.clock)
        self.addCleanup(self.store.close)
        self.calls = []

    def enqueue(self, request="request:1", creator=None):
        return self.store.enqueue((creator or self.creator)["creatorId"], request)

    def execute(self, target_file, output):
        target = json.loads(target_file.read_text())["targets"][0]
        self.calls.append(target)
        output.mkdir()
        (output / "report.private.json").write_text(json.dumps(report_for(target)), encoding="utf-8")
        return 0

    def worker(self, executor=None, importer=None):
        return ProfileRefreshWorker(self.store, executor=executor or self.execute, importer=importer)

    def raw_job(self, job):
        return self.store._row(job["id"])

    def save_final(self, job, report=None):
        target = {"ref": job["id"], "oecId": job["oecId"], "externalId": job["creatorId"]}
        output = self.var / "creator-profile-refresh" / job["id"] / "attempt-1"
        output.mkdir(parents=True)
        value = report or report_for(target)
        (output / "report.private.json").write_text(json.dumps(value), encoding="utf-8")
        return value

    def test_enqueue_uses_canonical_and_active_creator_jobs_coalesce(self):
        first = self.enqueue()
        self.assertEqual(first, self.enqueue())
        self.assertEqual(first["id"], self.enqueue("different-request")["id"])
        self.assertEqual(first["oecId"], "123456789")
        self.assertEqual(first["market"], "it")
        self.assertEqual(first["status"], "queued")
        self.assertEqual(len(self.store.list_jobs(self.creator["creatorId"])["jobs"]), 1)
        with self.assertRaises(ProfileRefreshError) as conflict:
            self.enqueue(creator=self.other)
        self.assertEqual((conflict.exception.code, conflict.exception.status), ("idempotency_conflict", 409))

    def test_unknown_pending_and_unverified_market_are_not_queued(self):
        pending = self.identities.record_handle_lead("it", "pending.handle", "2026-09-10T00:00:00Z", "lead")
        mexico = self.identities.observe_profile("mx", "42", "mexican.creator", "2026-09-10T00:00:00Z", "mx")
        for creator_id, code in (("creator_unknown", "identity_not_found"), (pending["leadId"], "identity_not_found"),
                                 (mexico["creatorId"], "unsupported_market")):
            with self.subTest(code=code), self.assertRaises(ProfileRefreshError) as error:
                self.store.enqueue(creator_id, "unknown:" + creator_id)
            self.assertEqual(error.exception.code, code)

    def test_success_runs_one_oec_target_imports_real_contract_and_keeps_internal_id(self):
        job = self.enqueue()
        done = self.worker().run_once()
        self.assertEqual(done["id"], job["id"])
        self.assertEqual(done["status"], "completed")
        self.assertTrue(done["identityUpdated"])
        self.assertEqual(done["requestCount"], 2)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0]["oecId"], "123456789")
        self.assertEqual(self.calls[0]["externalId"], self.creator["creatorId"])
        self.assertEqual(self.calls[0]["handle"], "old.handle")
        current = self.identities.get_by_oec("it", "123456789")
        self.assertEqual(current["creatorId"], self.creator["creatorId"])
        self.assertEqual(current["currentHandle"], "new.handle")
        self.assertIsNone(self.worker().run_once())
        self.assertEqual(self.enqueue()["status"], "completed")

    def test_partial_success_keeps_old_name_and_its_verification_time(self):
        def partial(target_file, output):
            target = json.loads(target_file.read_text())["targets"][0]
            output.mkdir()
            (output / "report.private.json").write_text(json.dumps(report_for(target, handle=None)))
            return 0
        self.enqueue()
        result = self.worker(partial).run_once()
        self.assertEqual(result["status"], "completed")
        current = self.identities.get_by_oec("it", "123456789")
        self.assertEqual(current["currentHandle"], "old.handle")
        self.assertEqual(current["currentHandleVerifiedAt"], self.creator["currentHandleVerifiedAt"])

    def test_failed_probe_keeps_identity_records_failure_and_filters_error_text(self):
        self.enqueue()
        def fails(*_):
            raise RuntimeError("token=private-token https://private.invalid/cookie")
        result = self.worker(fails).run_once()
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["errorCode"], "probe_failed")
        self.assertFalse(result["identityUpdated"])
        self.assertNotIn("private", json.dumps(result))
        self.assertEqual(self.identities.get_by_oec("it", "123456789"), self.creator)
        self.assertEqual(self.identities.history("it", "123456789")[-1]["kind"], "failure")

    def test_blocked_final_report_does_not_call_importer(self):
        job = self.enqueue()
        def blocked(_target, output):
            output.mkdir()
            (output / "report.private.json").write_text(json.dumps({"status": "blocked", "finishedAt": "2026-09-12T01:00:01Z",
                "reason": "account_not_startable", "counters": {"request_count": 0}}))
            return 2
        importer = Mock(side_effect=AssertionError("must not import failed evidence"))
        result = self.worker(blocked, importer).run_once()
        self.assertEqual(result["errorCode"], "account_not_startable")
        self.assertEqual(result["requestCount"], 0)
        self.assertEqual(result["id"], job["id"])
        importer.assert_not_called()

    def test_queued_job_survives_reopen_and_claim_is_atomic_across_connections(self):
        queued = self.enqueue()
        def claim(owner):
            with ProfileRefreshStore(self.var, now=lambda: self.clock) as connection:
                return connection.claim(owner)
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(claim, ("worker:a", "worker:b")))
        self.assertEqual(sum(result is not None for result in results), 1)
        self.assertEqual(next(result for result in results if result)["id"], queued["id"])
        self.assertEqual(self.store.status(queued["id"])["status"], "running")

    def test_concurrent_enqueue_collapses_same_creator(self):
        def enqueue(index):
            with ProfileRefreshStore(self.var, now=lambda: self.clock) as connection:
                return connection.enqueue(self.creator["creatorId"], f"parallel:{index}")["id"]
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            ids = list(executor.map(enqueue, range(16)))
        self.assertEqual(len(set(ids)), 1)

    def test_expired_running_without_final_is_blocked_and_never_rerun(self):
        job = self.enqueue()
        self.store.claim("old-worker")
        self.clock += 241
        never = Mock(side_effect=AssertionError("do not replay an interrupted attempt"))
        result = self.worker(never).run_once()
        self.assertEqual(result["errorCode"], "lease_expired_no_report")
        self.assertEqual(self.store.status(job["id"])["status"], "blocked")
        self.assertIsNone(self.worker(never).run_once())
        self.assertEqual(self.enqueue()["id"], job["id"])
        never.assert_not_called()

    def test_final_report_after_crash_is_imported_without_network_and_replay_is_idempotent(self):
        job = self.enqueue()
        self.store.claim("crashed-worker")
        report = self.save_final(job)
        # Simulate crash after identity import but before queue settlement.
        profile_refresh._import_reports(self.identities, [report])
        before = self.identities.stats()
        self.clock += 241
        never = Mock(side_effect=AssertionError("existing final report must be used"))
        result = self.worker(never).run_once()
        self.assertEqual(result["status"], "completed")
        self.assertTrue(result["identityUpdated"])
        self.assertEqual(self.identities.stats(), before)
        never.assert_not_called()

    def test_active_unexpired_lease_cannot_be_recovered(self):
        self.enqueue()
        self.store.claim("still-running")
        never = Mock(side_effect=AssertionError("active job cannot be replayed"))
        self.assertIsNone(self.worker(never).run_once())
        never.assert_not_called()

    def test_stale_worker_cannot_import_or_settle_after_lease_transfer(self):
        job = self.enqueue()
        old = self.worker(importer=Mock(side_effect=AssertionError("stale worker must not import")))
        claimed = self.store.claim(old.owner)
        value = self.save_final(job)
        self.clock += 241
        self.store.claim_recovery("replacement-owner")
        result = old._settle(claimed, value)
        self.assertEqual(result["status"], "running")
        self.assertFalse(result["identityUpdated"])
        self.assertEqual(self.identities.get_by_oec("it", "123456789"), self.creator)
        old.importer.assert_not_called()

    def test_frozen_oec_mismatch_blocks_before_executor(self):
        self.enqueue()
        with contextlib.closing(sqlite3.connect(self.store.identity_path)) as database:
            with database:
                database.execute("UPDATE creator_identity SET oec_id='777' WHERE creator_id=?", (self.creator["creatorId"],))
        never = Mock(side_effect=AssertionError("mismatched frozen identity must not execute"))
        result = self.worker(never).run_once()
        self.assertEqual(result["errorCode"], "identity_mismatch")
        never.assert_not_called()

    def test_existing_attempt_without_final_is_preserved_and_blocked(self):
        job = self.enqueue()
        output = self.var / "creator-profile-refresh" / job["id"] / "attempt-1"
        output.mkdir(parents=True)
        evidence = output / "report.private.json"
        evidence.write_text('{"status":"starting"}')
        never = Mock(side_effect=AssertionError("attempt evidence must not be overwritten"))
        result = self.worker(never).run_once()
        self.assertEqual(result["errorCode"], "attempt_exists_without_final")
        self.assertEqual(evidence.read_text(), '{"status":"starting"}')
        never.assert_not_called()

    def test_unbound_report_or_find_route_cannot_update_identity(self):
        for mutation in ("wrong_oec", "wrong_creator", "find", "verification"):
            with self.subTest(mutation=mutation):
                self.enqueue("invalid:" + mutation)
                def invalid(target_file, output):
                    target = json.loads(target_file.read_text())["targets"][0]
                    report = report_for(target)
                    if mutation == "wrong_oec":
                        report["targets"][0]["requestedOecId"] = "999"
                    elif mutation == "wrong_creator":
                        report["targets"][0]["externalId"] = self.other["creatorId"]
                    elif mutation == "find":
                        report["requests"][0]["stage"] = "find"
                    else:
                        report["requests"][0]["verificationRequired"] = True
                    output.mkdir()
                    (output / "report.private.json").write_text(json.dumps(report))
                    return 0
                result = self.worker(invalid).run_once()
                self.assertEqual(result["errorCode"], "probe_report_invalid")
                self.assertEqual(self.identities.get_by_oec("it", "123456789"), self.creator)

    def test_list_is_creator_scoped_limited_and_reports_heartbeat_freshness(self):
        for index in range(12):
            self.clock += 1
            self.enqueue("list:" + str(index))
            row = self.store.claim("list-worker")
            self.store.finish(row["id"], "list-worker")
        self.enqueue("other:list", self.other)
        result = self.store.list_jobs(self.creator["creatorId"])
        self.assertEqual(len(result["jobs"]), 10)
        self.assertTrue(all(row["creatorId"] == self.creator["creatorId"] for row in result["jobs"]))
        self.assertFalse(result["workerOnline"])
        self.store.heartbeat("online-worker")
        self.assertTrue(self.store.list_jobs(self.creator["creatorId"])["workerOnline"])
        self.clock += 31
        self.assertFalse(self.store.list_jobs(self.creator["creatorId"])["workerOnline"])

    def test_worker_context_maintains_heartbeat_and_removes_it_on_shutdown(self):
        with self.worker():
            self.assertTrue(self.store.worker_online())
        self.assertFalse(self.store.worker_online())

    def test_malformed_final_on_recovery_is_blocked_without_killing_worker(self):
        job = self.enqueue()
        self.store.claim("crashed-worker")
        self.save_final(job, {"status": {"private": "bad-status"}, "finishedAt": "2026-09-12T01:00:03Z"})
        self.clock += 241
        never = Mock(side_effect=AssertionError("malformed evidence must not be replayed"))
        result = self.worker(never).run_once()
        self.assertEqual(result["errorCode"], "probe_report_invalid")
        self.assertEqual(result["status"], "blocked")
        never.assert_not_called()

    def test_owned_process_is_terminated_when_outer_probe_deadline_expires(self):
        process = Mock(pid=999991)
        process.wait.side_effect = [profile_refresh.subprocess.TimeoutExpired("probe", 190), 0]
        process.poll.return_value = None
        with patch.object(profile_refresh.subprocess, "Popen", return_value=process) as launch, \
                patch.object(profile_refresh.os, "killpg") as kill:
            with self.assertRaises(ProfileRefreshError) as error:
                profile_refresh._execute_probe(self.var / "targets.json", self.var / "attempt")
        self.assertEqual(error.exception.code, "probe_timeout")
        self.assertEqual(process.wait.call_args_list[0].kwargs, {"timeout": 190})
        self.assertEqual(process.wait.call_args_list[1].kwargs, {"timeout": 5})
        kill.assert_called_once_with(process.pid, profile_refresh.signal.SIGTERM)
        command = launch.call_args.args[0]
        self.assertEqual(command[1], str(ROOT / "scripts/probe-italy-profile.py"))
        from lib.im_session_owner import identity_account
        self.assertEqual(command[command.index("--account") + 1], identity_account(ROOT,"it"))

    def test_cli_rejects_extra_path_or_route_fields_and_sanitizes_errors(self):
        cases = [{"creatorId": self.creator["creatorId"], "requestId": "cli", "oecId": "forged"},
                 {"creatorId": self.creator["creatorId"], "requestId": "cli", "database": "/private/path"}]
        for value in cases:
            with self.subTest(value=value), patch.object(CLI.sys, "stdin", io.StringIO(json.dumps(value))):
                with self.assertRaises(ProfileRefreshError) as error:
                    CLI.read_request(("creatorId", "requestId"))
                self.assertEqual(error.exception.code, "invalid_request")
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(CLI, "CRASH_LOG", Path(folder) / "identity-worker-crash.log"), \
                patch.object(CLI.sys, "argv", ["refresh", "enqueue"]), patch.object(CLI, "ProfileRefreshStore", side_effect=RuntimeError("private-token")), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(CLI.main(), 1)
        value = json.loads(output.getvalue())
        self.assertEqual(value["error"]["status"], 500)
        self.assertIn("message", value["error"])
        self.assertNotIn("private-token", output.getvalue())

    def test_a_crash_is_announced_by_type_and_kept_in_a_local_log(self):
        """`internal_error` 不能是全部：异常类型要能到操作者眼前（它足以区分"库被锁"和"字段缺失"），
        而原始消息可能带凭证或远端原文，只许落在本机日志里。"""
        with tempfile.TemporaryDirectory() as folder:
            crash_log = Path(folder) / "identity-worker-crash.log"
            output, errors = io.StringIO(), io.StringIO()
            with patch.object(CLI.sys, "argv", ["refresh", "enqueue"]), \
                    patch.object(CLI, "ProfileRefreshStore", side_effect=RuntimeError("private-token")), \
                    patch.object(CLI, "CRASH_LOG", crash_log), \
                    contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                self.assertEqual(CLI.main(), 1)
            self.assertNotIn("private-token", output.getvalue())
            self.assertNotIn("private-token", errors.getvalue())
            self.assertIn("RuntimeError", errors.getvalue())
            self.assertIn("identity-worker-crash.log", errors.getvalue())
            # 完整原因留在本机日志里，那才是能定位的那一份。
            self.assertIn("private-token", crash_log.read_text(encoding="utf-8"))
            self.assertEqual(crash_log.stat().st_mode & 0o777, 0o600)

    def test_a_cohort_refusal_is_not_flattened_into_internal_error(self):
        """cohort 层的拒绝自带安全码；把它压成 internal_error 等于说"我们不知道"，而我们知道。"""
        output, errors = io.StringIO(), io.StringIO()
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(CLI, "CRASH_LOG", Path(folder) / "identity-worker-crash.log"), \
                patch.object(CLI.sys, "argv", ["refresh", "enqueue"]), \
                patch.object(CLI, "ProfileRefreshStore",
                             side_effect=CLI.CreatorDiscoveryError("probe_report_invalid")), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            self.assertEqual(CLI.main(), 1)
        self.assertEqual(json.loads(output.getvalue())["error"]["code"], "probe_report_invalid")


if __name__ == "__main__":
    unittest.main()
