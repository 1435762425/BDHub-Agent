import contextlib
import json
from pathlib import Path
import sys
import tempfile
import sqlite3
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from lib.creator_identity import CreatorIdentityStore
from lib.profile_completion import summarize_profile
from lib.profile_refresh import ProfileRefreshError, ProfileRefreshStore, ProfileRefreshWorker
from lib import profile_refresh


def make_report(frozen, *, oec=None, unauthorized=False):
    target = frozen["targets"][0]
    found = "handle" in target and "oecId" not in target
    wrap = lambda value: {"status": 0, "is_authorized": not unauthorized, "value": value}
    summary = summarize_profile({"handle": {"value": target["handle"] if found else "new.name"},
        "creator_oecuid": {"value": oec or target.get("oecId", "123")},
        "selection_region": {"value": "GB" if frozen["market"] == "uk" else frozen["market"].upper()},
        "med_gmv_revenue": wrap({"value": "100.01", "symbol": "$"}), "video_gmv": wrap({"value": "100.01"}),
        "live_gmv": wrap({"value": "0"}), "industry_groups": wrap([{"id": "10", "value": "1"}])})
    row = {"targetRef": target["ref"], "externalId": target["externalId"], "inputKind": "handle_discovery" if found else "known_oec",
           "requestedHandle": target.get("handle") if found else None, "requestedOecId": target.get("oecId"),
           "oecId": summary["identity"]["oecId"], "currentHandleResolved": True,
           "status": "identity_verified" if found else "completed"}
    if found:
        row["find"] = summary
    else:
        row.update({"merged": summary, "profiles": [{"profileTypes": [2], "summary": summary}], "currentPlatformIdentityVerified": True})
    return {"schema": "bdhub.italy-profile-probe.v3" if frozen["market"] == "it" else "bdhub.market-profile-probe.v1",
        "market": frozen["market"], "account": frozen["account"], "identityFileUnchanged": True,
        "oldDatabaseWrites": 0, "realSends": 0, "status": "completed", "finishedAt": "2026-10-11T08:30:00Z",
        "counters": {"request_count": 1}, "targets": [row], "requests": [{"targetRef": target["ref"],
            "stage": "find" if found else "profile", "status": "returned", "httpStatus": 200, "code": "0",
            "verificationRequired": False, "systemError3": False, "profileTypes": None if found else [2]}]}


class CoreRefreshTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.var = Path(self.tmp.name)
        self.now = 1791707400.0
        self.identities = CreatorIdentityStore(self.var / "creator-identities.sqlite")
        self.addCleanup(self.identities.close)
        self.creators = {m: self.identities.observe_profile(m, "123", "old.name", "2026-10-09T00:00:00Z", "baseline:" + m)
                         for m in ("br", "my", "th", "de", "fr", "uk")}
        self.store = ProfileRefreshStore(self.var, now=lambda: self.now)
        self.addCleanup(self.store.close)
        self.calls = []

    def enqueue(self, market):
        return self.store.enqueue(self.creators[market]["creatorId"], "request:" + market, market)

    def execute(self, file, output, *, mutate=None, unauthorized=False):
        frozen = json.loads(file.read_text())
        self.calls.append(frozen)
        report = make_report(frozen, unauthorized=unauthorized)
        if mutate:
            mutate(report)
        output.mkdir()
        (output / "report.private.json").write_text(json.dumps(report))
        return 0

    def test_find_refreshes_three_markets_without_profile_call_or_new_identity(self):
        before = self.identities.stats()["identities"]
        for market in ("br", "my", "th"):
            job = self.enqueue(market)
            result = ProfileRefreshWorker(self.store, market=market, executor=self.execute).run_once()
            self.assertEqual(result["id"], job["id"])
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["requestCount"], 1)
            frozen = self.calls[-1]
            self.assertTrue(frozen["identityOnly"])
            self.assertNotIn("oecId", frozen["targets"][0])
            self.assertEqual(frozen["targets"][0]["handle"], "old.name")
            current = self.identities.history(market, "123")[-1]
            self.assertEqual(current["payload"]["profileSource"]["provider"], "tiktok")
            self.assertEqual(current["payload"]["fields"]["live_gmv"]["status"], "zero")
        self.assertEqual(self.identities.stats()["identities"], before)

    def test_profile_markets_read_by_oec_and_keep_unauthorized_states(self):
        for market in ("de", "fr", "uk"):
            self.enqueue(market)
            result = ProfileRefreshWorker(self.store, market=market,
                executor=lambda f, o: self.execute(f, o, unauthorized=market == "uk")).run_once()
            self.assertEqual(result["status"], "completed")
            self.assertEqual(self.calls[-1]["targets"][0]["oecId"], "123")
            self.assertEqual(self.calls[-1]["profileTypeSets"], [[2]])
            if market == "uk":
                profile = self.identities.history(market, "123")[-1]["payload"]
                self.assertEqual(profile["fields"]["med_gmv_revenue"], {"status": "unauthorized"})

    def test_recycled_handle_and_wrong_original_account_do_not_update_profile(self):
        for market, mutate in [("br", lambda r: r["targets"][0]["find"]["identity"].update(oecId="456")),
                               ("my", lambda r: r.update(account="acc1"))]:
            self.enqueue(market)
            result = ProfileRefreshWorker(self.store, market=market,
                executor=lambda f, o: self.execute(f, o, mutate=mutate)).run_once()
            self.assertEqual(result["status"], "blocked")
            self.assertIsNone(self.identities.get_by_oec(market, "456"))
            self.assertEqual(self.identities.get_by_oec(market, "123"), self.creators[market])

    def test_route_change_cannot_replace_a_frozen_account(self):
        job = self.enqueue("br")
        account = self.store._row(job["id"])["account"]
        with patch.object(profile_refresh, "_identity_account", return_value="acc1"):
            done = ProfileRefreshWorker(self.store, market="br", executor=self.execute).run_once()
        self.assertEqual(done["status"], "completed")
        self.assertEqual(self.calls[0]["account"], account)

    def test_name_change_while_profile_is_queued_does_not_change_its_oec_or_frozen_audit(self):
        self.enqueue("de")
        self.identities.observe_profile("de", "123", "changed.name", "2026-10-10T00:00:00Z", "renamed")
        result = ProfileRefreshWorker(self.store, market="de", executor=self.execute).run_once()
        self.assertEqual(result["status"], "completed")
        self.assertEqual(self.calls[0]["targets"][0]["oecId"], "123")
        self.assertEqual(self.calls[0]["targets"][0]["handle"], "old.name")
        self.assertEqual(self.identities.get_by_oec("de", "123")["currentHandle"], "new.name")

    def test_api_queue_status_and_worker_are_market_scoped(self):
        br, my = self.enqueue("br"), self.enqueue("my")
        for call in (lambda: self.store.status(br["id"], "my"),
                     lambda: self.store.list_jobs(self.creators["br"]["creatorId"], "my"),
                     lambda: self.store.enqueue(self.creators["br"]["creatorId"], "request:br", "my")):
            with self.assertRaises(ProfileRefreshError) as error:
                call()
            self.assertEqual(error.exception.code, "market_mismatch")
        worker = ProfileRefreshWorker(self.store, market="my", executor=self.execute)
        self.assertEqual(worker.run_once()["id"], my["id"])
        self.assertEqual(self.store.status(br["id"])["status"], "queued")
        self.assertTrue(self.store.worker_online("my"))
        self.assertFalse(self.store.worker_online("br"))

    def test_busy_shared_resources_defer_before_creating_an_attempt(self):
        job = self.enqueue("br")
        @contextlib.contextmanager
        def busy(*args):
            raise ProfileRefreshError("resources_busy")
            yield
        never = Mock(side_effect=AssertionError("busy resource must not read"))
        result = ProfileRefreshWorker(self.store, market="br", executor=never, resource_guard=busy).run_once()
        self.assertEqual(result["status"], "queued")
        self.assertEqual(result["errorCode"], "resources_busy")
        self.assertFalse((self.var / "creator-profile-refresh" / job["id"]).exists())
        never.assert_not_called()
        self.now += 6
        self.assertEqual(ProfileRefreshWorker(self.store, market="br", executor=self.execute).run_once()["status"], "completed")

    def test_zero_request_mutex_wait_keeps_immutable_attempt_then_reads_same_input(self):
        job = self.enqueue("br")
        def busy(file, output):
            frozen = json.loads(file.read_text())
            output.mkdir()
            (output / "report.private.json").write_text(json.dumps({"status": "blocked", "finishedAt": "2026-10-11T08:30:00Z",
                "market": "br", "account": frozen["account"], "oldDatabaseWrites": 0, "realSends": 0,
                "reason": "probe_initialization_or_validation_error", "errorMessage": "account_in_use", "requests": []}))
            return 2
        result = ProfileRefreshWorker(self.store, market="br", executor=busy).run_once()
        self.assertEqual(result["status"], "queued")
        file = self.var / "creator-profile-refresh" / job["id"] / "attempt-1/report.private.json"
        first = file.read_bytes()
        self.now += 31
        self.assertEqual(ProfileRefreshWorker(self.store, market="br", executor=self.execute).run_once()["status"], "completed")
        self.assertEqual(first, file.read_bytes())
        self.assertTrue(file.parent.parent.joinpath("attempt-2/report.private.json").exists())

    def test_real_request_failure_and_lost_fence_are_never_requeued(self):
        job = self.enqueue("br")
        def bad(file, output):
            report = make_report(json.loads(file.read_text()))
            report.update(status="blocked", reason="request_or_signer_error")
            output.mkdir()
            (output / "report.private.json").write_text(json.dumps(report))
            return 2
        worker = ProfileRefreshWorker(self.store, market="br", executor=bad)
        self.assertEqual(worker.run_once()["status"], "blocked")
        self.assertIsNone(worker.run_once())
        self.assertEqual(self.store._row(job["id"])["attempt_no"], 1)
        self.enqueue("my")
        @contextlib.contextmanager
        def lost(*args):
            def check():
                raise ProfileRefreshError("resource_lease_lost")
            yield check
        result = ProfileRefreshWorker(self.store, market="my", executor=self.execute, resource_guard=lost).run_once()
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["errorCode"], "resource_lease_lost")
        self.assertEqual(self.identities.get_by_oec("my", "123"), self.creators["my"])

    def test_due_refresh_is_bounded_idempotent_and_skips_latest_unauthorized(self):
        self.assertEqual(self.store.due_candidates("br", limit=1)["dueCreators"], 1)
        first = self.store.enqueue_due("br", limit=1)
        self.assertEqual(len(first["jobs"]), 1)
        self.assertEqual(self.store.enqueue_due("br", limit=1)["jobs"], [])
        ProfileRefreshWorker(self.store, market="br", executor=self.execute).run_once()
        self.assertEqual(self.store.due_candidates("br")["dueCreators"], 0)
        self.now += 169 * 3600
        self.assertEqual(self.store.due_candidates("br")["dueCreators"], 1)
        self.enqueue("uk")
        ProfileRefreshWorker(self.store, market="uk", executor=lambda f,o:self.execute(f,o,unauthorized=True)).run_once()
        self.assertEqual(self.store.due_candidates("uk")["skipped"]["permission_required"], 1)

    def test_drain_handshake_does_not_leave_later_enqueues_with_an_exiting_worker(self):
        worker = ProfileRefreshWorker(self.store, market="br", executor=self.execute)
        self.store.heartbeat(worker.owner, "br")
        self.assertTrue(self.store.worker_online("br"))
        self.assertTrue(self.store.begin_drain(worker.owner, "br"))
        self.assertFalse(self.store.worker_online("br"))
        self.enqueue("br")
        self.assertFalse(self.store.begin_drain(worker.owner, "br"))
        self.assertTrue(self.store.has_pending("br"))

    def test_old_queue_upgrade_preserves_legacy_job_and_request(self):
        with tempfile.TemporaryDirectory() as folder:
            var = Path(folder)
            db = sqlite3.connect(var / "creator-profile-refresh.sqlite")
            db.executescript(profile_refresh._SCHEMA)
            job = "refresh_" + "c" * 32
            db.execute("INSERT INTO profile_refresh_job(id,creator_id,market,oec_id,status,created_at) VALUES(?,?,?,?,?,?)",
                       (job, "creator_legacy", "it", "123", "completed", "2026-09-12T00:00:00Z"))
            db.execute("INSERT INTO profile_refresh_request VALUES(?,?,?)", ("legacy-request", "creator_legacy", job))
            db.commit()
            db.close()
            for _ in range(2):
                with ProfileRefreshStore(var) as store:
                    self.assertEqual(store.status(job)["status"], "completed")
                    self.assertEqual(store._row(job)["attempt_no"], 1)
                    self.assertIsNone(store._row(job)["account"])
                    self.assertEqual(store._db.execute("SELECT job_id FROM profile_refresh_request WHERE request_id='legacy-request'").fetchone()[0], job)


if __name__ == "__main__":
    unittest.main()
