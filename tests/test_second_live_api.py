"""All approvals and launch effects use temporary databases and fake processes."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
import importlib.util
import io
import json
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from lib.second_live_api import SecondLiveAPI, _hash, _ReadOnlyStore
from lib.second_live_trial import LiveTrialError, LiveTrialStore, EXPIRY_SECONDS
from test_second_live_trial import Clock, source_item
from test_italy_im_delivery import card


class FakeProcess:
    pid = 12345


class SecondLiveAPITest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.var = self.root / "var"
        self.directory = self.var / "second-live"
        self.directory.mkdir(parents=True)
        self.clock = Clock()
        self.store = LiveTrialStore(self.var, now=self.clock)
        facts = {key: str(index) * 64 for index, key in enumerate(("senderBindingHash",
            "templateContextFingerprint", "cardBindingSha256", "opportunityFingerprint"), start=1)}
        self.trial = self.store.create_trial("fixture", [source_item(cards=[asdict(card())])], _hash(facts))
        self.id = self.trial["trialId"]
        self.scope = {"schema": "bdhub.second-live-scope.v1", "trialId": self.id,
            "snapshotHash": self.trial["snapshotHash"], "approvalFacts": facts,
            "cardFactsPath": "card-facts.json", "authReport": {"private": "secret_auth_only"}, "commercialFacts": {}}
        self.scope_path = self.directory / (self.id + ".json")
        self.write_scope()
        self.calls = []
        self.spawns = []
        self.running = True
        self.api = SecondLiveAPI(self.root, runner=self.fake_cli, popen=self.fake_popen,
            alive=lambda pid: self.running, now=self.clock)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def write_scope(self):
        self.scope_path.write_text(json.dumps(self.scope), encoding="utf-8")
        self.scope_path.chmod(0o600)

    @property
    def record_path(self):
        return self.directory / (self.id + ".launch.json")

    @property
    def request(self):
        return {"trialId": self.id, "snapshotHash": self.trial["snapshotHash"], "confirmed": True, "requestId": "click-1"}

    def fake_cli(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        self.assertEqual(argv[2], "approve")
        trial = self.store.approve(argv[4], argv[6])
        return subprocess.CompletedProcess(argv, 0, json.dumps(trial).encode(), b"")

    def fake_popen(self, argv, **kwargs):
        self.spawns.append((argv, kwargs))
        self.assertEqual(json.loads(self.record_path.read_text())["phase"], "launch_intent")
        self.assertTrue(self.store.get_trial(self.id)["approved"])
        return FakeProcess()

    def show(self):
        return self.api.dispatch("show", {"trialId": self.id})

    def start(self, **patches):
        return self.api.dispatch("start", {**self.request, **patches})

    def assert_error(self, code, callback, status=None):
        with self.assertRaises(LiveTrialError) as raised:
            callback()
        self.assertEqual(raised.exception.code, code)
        if status is not None:
            self.assertEqual(raised.exception.status, status)

    def test_show_is_read_only_allowlisted_and_has_no_launch_side_effects(self):
        files = set(self.directory.iterdir())
        before = self.store.db.execute("SELECT * FROM live_trial").fetchall()
        result = self.show()
        self.assertEqual(before, self.store.db.execute("SELECT * FROM live_trial").fetchall())
        self.assertEqual(set(self.directory.iterdir()), files)
        self.assertEqual(result["launch"], {"state": "not_started", "pid": None, "requestId": None})
        self.assertFalse(result["trial"]["approved"])
        item = result["trial"]["items"][0]
        self.assertEqual(item["oecId"], self.trial["items"][0]["oecId"])
        self.assertEqual(item["components"][0]["listId"], card().list_id)
        self.assertNotIn("secret_auth_only", json.dumps(result))
        for forbidden in ("snapshot", "events", "conversationId", "sendReceipt", "confirmation", "contextFingerprint", "authReport"):
            self.assertNotIn('"' + forbidden + '":', json.dumps(result))
        self.assertFalse(self.calls or self.spawns)

    def test_read_adapter_enforces_sqlite_readonly_even_for_accidental_write(self):
        with _ReadOnlyStore(self.var / "second-live-trials.sqlite", self.clock) as store:
            with self.assertRaises(sqlite3.OperationalError):
                store.db.execute("UPDATE live_trial SET approved_at=1")

    def test_show_missing_scope_or_database_never_creates_defaults(self):
        self.scope_path.unlink()
        self.assert_error("trial_scope_not_found", self.show, 404)
        self.assertFalse(self.calls or self.spawns)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            api = SecondLiveAPI(root, runner=self.fake_cli, popen=self.fake_popen)
            self.assert_error("trial_scope_not_found", lambda: api.dispatch("show", {"trialId": self.id}), 404)
            self.assertEqual(list(root.iterdir()), [])
            directory = root / "var" / "second-live"
            directory.mkdir(parents=True)
            (directory / (self.id + ".json")).write_text(json.dumps(self.scope))
            self.assert_error("trial_not_found", lambda: api.dispatch("show", {"trialId": self.id}), 404)
            self.assertFalse((root / "var" / "second-live-trials.sqlite").exists())
            self.assertFalse(list(directory.glob("*.launch.*")))

    def test_start_calls_only_fixed_cli_then_detached_process(self):
        result = self.start()
        self.assertEqual(result["launch"], {"state": "started", "pid": 12345, "requestId": "click-1"})
        self.assertTrue(result["trial"]["approved"])
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(len(self.spawns), 1)
        argv, opts = self.spawns[0]
        self.assertEqual(argv, [str(Path(__file__).resolve().parents[1] / ".venv/bin/python"),
            str(self.root / "scripts" / "italy-second-live.py"), "run", "--trial-id", self.id])
        self.assertFalse(opts["shell"])
        self.assertTrue(opts["start_new_session"])
        self.assertTrue(opts["close_fds"])
        self.assertEqual(opts["stdin"], subprocess.DEVNULL)
        self.assertEqual(opts["env"]["PYTHONDONTWRITEBYTECODE"], "1")
        self.assertEqual(opts["cwd"], str(self.root))
        self.assertIs(opts["stdout"], opts["stderr"])
        for suffix in (".launch.json", ".launch.lock", ".launch.log"):
            self.assertEqual(stat.S_IMODE((self.directory / (self.id + suffix)).stat().st_mode), 0o600)

    def test_existing_approval_is_not_repeated(self):
        self.store.approve(self.id, self.trial["snapshotHash"])
        self.start()
        self.assertFalse(self.calls)
        self.assertEqual(len(self.spawns), 1)

    def test_same_request_returns_launch_even_after_expiry_pause_or_unknown(self):
        self.start()
        self.clock.advance(EXPIRY_SECONDS + 1)
        self.store.db.execute("UPDATE live_trial SET paused_at=1 WHERE id=?", (self.id,))
        self.store.db.execute("UPDATE live_trial_item SET state='result_unknown',requires_reconciliation=1 WHERE trial_id=?", (self.id,))
        repeated = self.start()
        self.assertEqual(repeated["launch"]["state"], "running")
        self.assertTrue(repeated["trial"]["expired"])
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(len(self.spawns), 1)
        self.assert_error("request_conflict", lambda: self.start(requestId="new-click"))

    def test_rejects_payload_overrides_and_unconfirmed_input_before_approval(self):
        for patch_value in ({"confirmed": False}, {"confirmed": 1}, {"requestId": "../bad"},
                {"trialId": "../bad"}, {"snapshotHash": "wrong"}):
            self.assert_error("invalid_request", lambda: self.start(**patch_value), 400)
        for extra in ("account", "oecId", "textIt", "model", "root", "command"):
            self.assert_error("invalid_request", lambda: self.start(**{extra: "injected"}), 400)
        self.assertFalse(self.calls or self.spawns)
        self.assertFalse(self.record_path.exists())

    def test_snapshot_and_scope_conflicts_never_approve(self):
        self.assert_error("snapshot_mismatch", lambda: self.start(snapshotHash="0" * 64))
        for key, value in (("schema", "wrong"), ("trialId", "second_live_trial_" + "0" * 32),
                ("snapshotHash", "0" * 64), ("cardFactsPath", "../escape.json")):
            original = self.scope[key]
            self.scope[key] = value
            self.write_scope()
            self.assert_error("scope_conflict", self.start)
            self.scope[key] = original
        self.scope["approvalFacts"]["senderBindingHash"] = "f" * 64
        self.write_scope()
        self.assert_error("scope_conflict", self.start)
        self.assertFalse(self.calls or self.spawns)

    def test_expiry_pause_complete_and_global_unknown_block_new_launch(self):
        self.clock.advance(EXPIRY_SECONDS)
        self.assert_error("trial_expired", self.start)
        self.clock.value -= EXPIRY_SECONDS
        self.store.db.execute("UPDATE live_trial SET paused_at=1 WHERE id=?", (self.id,))
        self.assert_error("trial_paused", self.start)
        self.store.db.execute("UPDATE live_trial SET paused_at=NULL WHERE id=?", (self.id,))
        self.store.db.execute("UPDATE live_trial_item SET state='confirmed' WHERE trial_id=?", (self.id,))
        self.assert_error("trial_complete", self.start)
        self.store.db.execute("UPDATE live_trial_item SET state='pending' WHERE trial_id=?", (self.id,))
        other = self.store.create_trial("other", [source_item(2)], "b" * 64)
        self.store.approve(other["trialId"], other["snapshotHash"])
        self.store.db.execute("UPDATE live_trial_item SET state='result_unknown' WHERE trial_id=?", (other["trialId"],))
        self.assert_error("campaign_result_unknown", self.start)
        self.assertFalse(self.calls or self.spawns)
        self.assertFalse(self.record_path.exists())

    def test_untracked_external_cli_attempts_or_lease_do_not_get_second_launcher(self):
        self.store.approve(self.id, self.trial["snapshotHash"])
        lease = self.store.claim(self.id, "external-cli")
        self.assert_error("trial_already_started", self.start)
        self.store.begin_attempt(lease["itemId"], "external-cli", lease["fence"], "create_conversation", "external-create")
        self.assert_error("trial_already_started", self.start)
        self.clock.advance(91)
        self.assert_error("campaign_result_unknown", self.start)
        self.assertFalse(self.spawns)

    def test_reconciliation_cannot_be_started_via_send_confirmation(self):
        self.store.db.execute("UPDATE live_trial_item SET requires_reconciliation=1 WHERE trial_id=?", (self.id,))
        self.assert_error("readback_required", self.start)
        self.assertFalse(self.calls or self.spawns)

    def test_failed_process_creation_is_durable_and_never_auto_retried(self):
        def fail(*args, **kwargs):
            raise FileNotFoundError("private executable")
        self.api.popen = fail
        result = self.start()
        self.assertEqual(result["launch"]["state"], "not_started")
        self.api.popen = self.fake_popen
        self.assertEqual(self.start()["launch"]["state"], "not_started")
        self.assertFalse(self.spawns)
        self.assert_error("request_conflict", lambda: self.start(requestId="new-click"))

    def test_unknown_spawn_outcome_cannot_retry(self):
        def uncertain(*args, **kwargs):
            self.spawns.append((args, kwargs))
            raise RuntimeError("may have started; secret")
        self.api.popen = uncertain
        result = self.start()
        self.assertEqual(result["launch"]["state"], "start_unknown")
        self.assertNotIn("secret", json.dumps(result))
        self.api.popen = self.fake_popen
        self.assertEqual(self.start()["launch"]["state"], "start_unknown")
        self.assertEqual(len(self.spawns), 1)

    def test_crash_after_spawn_before_pid_record_leaves_non_retryable_intent(self):
        from lib import second_live_api
        original = second_live_api._save
        def save(path, value):
            if value["phase"] == "launched":
                raise OSError("disk full")
            original(path, value)
        with patch.object(second_live_api, "_save", side_effect=save):
            result = self.start()
        self.assertEqual(result["launch"]["state"], "start_unknown")
        self.assertEqual(json.loads(self.record_path.read_text())["phase"], "launch_intent")
        self.assertEqual(self.start()["launch"]["state"], "start_unknown")
        self.assertEqual(len(self.spawns), 1)

    def test_intent_write_failure_cannot_spawn_or_approve(self):
        with patch("lib.second_live_api._save", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.start()
        self.assertFalse(self.calls or self.spawns)

    def test_approval_timeout_stays_unknown_and_no_process_is_launched(self):
        def timeout(argv, **kwargs):
            self.calls.append((argv, kwargs))
            raise subprocess.TimeoutExpired(argv, 15, output=b"secret")
        self.api.runner = timeout
        self.assert_error("approval_result_unknown", self.start, 503)
        self.assertEqual(self.start()["launch"]["state"], "start_unknown")
        self.assertEqual(len(self.calls), 1)
        self.assertFalse(self.spawns)

    def test_approval_error_is_sanitized_and_saved_not_started(self):
        self.api.runner = lambda argv, **kw: subprocess.CompletedProcess(argv, 1, b'{"error":"Cookie=SECRET"}', b"SECRET")
        self.assert_error("approval_failed", self.start)
        self.assertEqual(self.start()["launch"]["state"], "not_started")
        self.assertNotIn("SECRET", self.record_path.read_text())
        self.assertFalse(self.spawns)

    def test_approval_must_be_visible_in_actual_temp_database_before_spawn(self):
        self.api.runner = lambda argv, **kw: subprocess.CompletedProcess(argv, 0, json.dumps({
            "trialId": self.id, "snapshotHash": self.trial["snapshotHash"], "approved": True}), b"")
        self.assert_error("approval_not_applied", self.start, 503)
        self.assertFalse(self.spawns)

    def test_recheck_detects_expiry_between_approval_and_spawn(self):
        original = self.fake_cli
        def approve_then_expire(*args, **kwargs):
            result = original(*args, **kwargs)
            self.clock.advance(EXPIRY_SECONDS)
            return result
        self.api.runner = approve_then_expire
        self.assert_error("trial_expired", self.start)
        self.assertFalse(self.spawns)

    def test_exited_process_is_unknown_until_database_has_terminal_evidence(self):
        self.start()
        self.running = False
        self.assertEqual(self.show()["launch"]["state"], "start_unknown")
        self.assertEqual(self.start()["launch"]["state"], "start_unknown")
        self.store.db.execute("UPDATE live_trial_item SET state='failed_not_sent' WHERE trial_id=?", (self.id,))
        self.assertEqual(self.show()["launch"]["state"], "finished")
        self.assertEqual(self.start()["launch"]["state"], "finished")
        self.assertEqual(len(self.spawns), 1)

    def test_two_concurrent_starts_have_one_approval_and_one_spawn(self):
        entered, release = threading.Event(), threading.Event()
        def slow_approve(*args, **kwargs):
            entered.set()
            self.assertTrue(release.wait(3))
            return self.fake_cli(*args, **kwargs)
        self.api.runner = slow_approve
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self.start)
            self.assertTrue(entered.wait(3))
            try:
                self.assert_error("launch_busy", self.start, 409)
            finally:
                release.set()
            self.assertEqual(first.result(timeout=3)["launch"]["state"], "started")
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(len(self.spawns), 1)

    def test_rejects_symlinks_untrusted_permissions_and_oversized_scope(self):
        target = self.root / "scope-target.json"
        self.scope_path.rename(target)
        self.scope_path.symlink_to(target)
        self.assert_error("unsafe_local_file", self.show, 503)
        self.scope_path.unlink()
        target.rename(self.scope_path)
        self.scope_path.chmod(0o666)
        self.assert_error("unsafe_local_file", self.show, 503)
        self.scope_path.chmod(0o600)
        self.scope_path.write_text("x" * 65537)
        self.assert_error("local_file_too_large", self.show, 503)
        self.assertFalse(self.calls or self.spawns)

    def test_symlink_launch_lock_or_record_never_followed(self):
        victim = self.root / "victim"
        victim.write_text("unchanged")
        lock = self.directory / (self.id + ".launch.lock")
        lock.symlink_to(victim)
        self.assert_error("unsafe_local_file", self.start, 503)
        lock.unlink()
        self.record_path.symlink_to(victim)
        self.assert_error("unsafe_local_file", self.start, 503)
        self.assertEqual(victim.read_text(), "unchanged")
        self.assertFalse(self.calls or self.spawns)

    def test_corrupt_launch_state_is_never_treated_as_absent(self):
        self.record_path.write_text("{}"); self.record_path.chmod(0o600)
        self.assert_error("invalid_launch_state", self.start, 503)
        self.assertFalse(self.calls or self.spawns)

    def test_cli_bounds_input_and_redacts_internal_exception(self):
        script = Path(__file__).resolve().parents[1] / "scripts" / "second-live-api.py"
        spec = importlib.util.spec_from_file_location("second_live_api_cli", script)
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        for raw in ("x" * 4097, "null", "{}", "not json", json.dumps({"trialId": self.id, "account": "acc6"})):
            output = io.StringIO()
            code = module.main(["show"], api=self.api, stdin=io.StringIO(raw), stdout=output)
            self.assertEqual(code, 1)
            self.assertEqual(json.loads(output.getvalue())["error"]["code"], "invalid_request")
        output = io.StringIO()
        self.assertEqual(module.main(["show"], api=self.api, stdin=io.StringIO(json.dumps({"trialId": self.id})), stdout=output), 0)
        self.assertEqual(json.loads(output.getvalue())["launch"]["state"], "not_started")
        with patch.object(self.api, "dispatch", side_effect=RuntimeError("SECRET")):
            output = io.StringIO()
            module.main(["show"], api=self.api, stdin=io.StringIO("{}"), stdout=output)
            self.assertNotIn("SECRET", output.getvalue())
            self.assertEqual(json.loads(output.getvalue())["error"]["code"], "second_live_api_failed")


if __name__ == "__main__":
    unittest.main()
