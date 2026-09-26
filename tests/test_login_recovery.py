import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from lib.account_identity import current_generation, publish_generation  # noqa: E402
from lib.login_recovery import (AUTH_REQUIRED, _legacy_request_id, auth_family, it_login_expired,  # noqa: E402
                                request_recovery, request_refresh)
from lib.operations_scheduler import SubprocessStageExecutor  # noqa: E402
from lib.market_im_runtime import _read  # noqa: E402
from lib.schema_migrations import apply_database  # noqa: E402
from lib.second_cycle import CycleStore  # noqa: E402
from test_account_identity import NOW, config  # noqa: E402


class LoginRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "var").mkdir()
        (self.root / "config").mkdir()
        (self.root / "config/market-accounts.json").write_text(json.dumps(config("project_owned")))
        with CycleStore(self.root / "var/second-cycle.sqlite", lambda: NOW) as store:
            store.plan("bjn-local-research", "it")
        apply_database(self.root, "second-cycle", clock=lambda: NOW)
        self.store = CycleStore(self.root / "var/second-cycle.sqlite", lambda: NOW)
        self.spawned = []

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def publish(self, n, account="acc6", role="communications"):
        publish_generation(self.store, market="it", account=account, role=role, reason="relogin",
                           identity={"browserRef": f"browser:{n}", "httpRef": f"http:{n}", "imRef": f"im:{n}",
                                     "institutionFingerprint": "i" * 64},
                           capabilities={"http": {"state": "verified"}, "im": {"state": "verified"}}, now=NOW + n)
        return current_generation(self.store, "it", account)["generationId"]

    def refresh(self):
        return request_refresh(self.store, self.root, "it", spawn=self.spawned.append)

    def settle(self, intent_id, state, error=None):
        self.store.db.execute("UPDATE account_maintenance_intent SET state=?,error_code=? WHERE intent_id=?",
                              (state, error, intent_id))

    def test_lapsed_login_queues_one_refresh_per_generation(self):
        first = self.publish(1)
        queued = self.refresh()
        self.assertEqual((queued["state"], queued["duplicate"], queued["generationId"], queued["account"]),
                         ("queued", False, first, "acc6"))
        again = self.refresh()
        self.assertEqual((again["intentId"], again["duplicate"]), (queued["intentId"], True))
        self.assertEqual(len(self.spawned), 1)
        self.settle(queued["intentId"], "completed")
        self.publish(2)
        later = self.refresh()
        self.assertEqual((later["state"], later["duplicate"]), ("queued", False))
        self.assertNotEqual(later["intentId"], queued["intentId"])
        self.assertEqual(len(self.spawned), 2)

    def test_failed_refresh_stays_for_a_person_until_a_new_generation(self):
        self.publish(1)
        queued = self.refresh()
        self.settle(queued["intentId"], "needs_human", "account_login_timeout")
        held = self.refresh()
        self.assertEqual((held["state"], held["duplicate"], held["errorCode"]),
                         ("needs_human", True, "account_login_timeout"))
        self.assertEqual(len(self.spawned), 1)

    def test_running_maintenance_or_missing_facts_do_not_raise(self):
        self.assertEqual(self.refresh()["reason"], "identity_generation_missing")
        self.publish(1)
        self.store.db.execute("INSERT INTO account_maintenance_intent VALUES(?,?,?,?,?,?,'running',?,NULL,?,NULL,NULL,'{}',NULL,?)",
                              ("maintenance-other", "maintenance-other-request", "it", "acc6", "communications", "refresh",
                               None, NOW, NOW))
        # Another maintenance of the same account is the chain: join it instead of queueing a second login.
        waiting = self.refresh()
        self.assertEqual((waiting["state"], waiting["intentId"]), ("waiting_active", "maintenance-other"))
        (self.root / "config/market-accounts.json").write_text("{}")
        self.assertEqual(self.refresh()["state"], "not_requested")
        self.assertEqual(self.spawned, [])

    def intents(self):
        return list(self.store.db.execute("SELECT request_id,account,operation FROM account_maintenance_intent"))

    def test_every_reporter_of_one_lapse_joins_one_refresh_chain(self):
        self.publish(1)
        first = request_recovery(self.store, self.root, "it", "communications", source="sdk", spawn=self.spawned.append)
        joined = [self.refresh(),
                  request_recovery(self.store, self.root, "it", "communications", source="scheduler:oecid-0",
                                   spawn=self.spawned.append)]
        self.assertEqual({row["intentId"] for row in joined}, {first["intentId"]})
        self.assertTrue(all(row["duplicate"] for row in joined))
        self.assertEqual([(row["account"], row["operation"]) for row in self.intents()], [("acc6", "refresh")])
        self.assertEqual(len(self.spawned), 1)

    def test_a_lapse_handled_under_the_old_inbox_id_stays_handled(self):
        generation = self.publish(1)
        self.store.db.execute("INSERT INTO account_maintenance_intent VALUES(?,?,?,?,?,?,'needs_human',?,NULL,?,NULL,NULL,'{}',?,?)",
                              ("maintenance-legacy", _legacy_request_id("it", "acc6", generation), "it", "acc6",
                               "communications", "refresh", generation, NOW, "account_login_timeout", NOW))
        held = self.refresh()
        self.assertEqual((held["intentId"], held["state"], held["duplicate"]), ("maintenance-legacy", "needs_human", True))
        self.assertEqual(len(self.intents()), 1)

    def test_the_failed_account_recovers_without_touching_the_other_role(self):
        self.publish(1)
        self.publish(1, account="acc9", role="supply")
        supply = request_recovery(self.store, self.root, "it", "supply", source="scheduler:selection",
                                  spawn=self.spawned.append)
        self.assertEqual((supply["account"], supply["state"]), ("acc9", "queued"))
        self.assertEqual([row["account"] for row in self.intents()], ["acc9"])

    def test_only_a_lapsed_login_is_an_auth_failure(self):
        self.assertEqual(auth_family(platform_code=AUTH_REQUIRED), "auth_expired")
        self.assertEqual(auth_family("sdk_login_required"), "auth_expired")
        self.assertEqual(auth_family("market_identity_auth_required"), "auth_expired")
        for code in ("provider_timeout", "ReadTimeout", "business_rejected", "quota_exhausted",
                     "market_identity_blocked", "ProfileBusyError", None):
            self.assertIsNone(auth_family(code, platform_code=98000001), code)

    def test_scheduler_waits_for_the_shared_chain_and_needs_a_newer_generation(self):
        self.publish(1)
        shared = self.refresh()
        executor = SubprocessStageExecutor(self.root)
        workers = []

        def worker(args, label, timeout=14400):
            workers.append(args[0])
            self.settle(shared["intentId"], "completed")
            self.publish(2)
            return {"state": "completed"}

        executor._call = worker
        executor.sleep = lambda seconds: None
        result = executor._relogin_market_account(self.store, "it", "communications", "run-1", "oecid-0")
        self.assertEqual(result["state"], "completed")
        self.assertEqual(workers, ["scripts/account-maintenance-worker.py"])
        self.assertEqual(len(self.intents()), 1)
        # The recovered generation fails in turn: a new chain, still a refresh first.
        self.settle(shared["intentId"], "completed")
        executor._call = lambda args, label, timeout=14400: {"state": "completed"}
        pending = executor._relogin_market_account
        requested = request_recovery(self.store, self.root, "it", "communications", source="sdk", spawn=self.spawned.append)
        self.assertNotEqual(requested["intentId"], shared["intentId"])
        self.settle(requested["intentId"], "needs_human", "account_manual_verification_required")
        failed = pending(self.store, "it", "communications", "run-2", "oecid-0")
        self.assertEqual(failed, {"state": "failed", "errorCode": "account_manual_verification_required"})
        self.assertEqual(len(self.intents()), 2)

    def test_the_platform_slot_is_lent_to_other_markets_while_waiting_for_the_login(self):
        from lib.operations_workflow import create_run
        from lib.workflow_resources import claim
        self.publish(1)
        run = create_run(self.store, market="it", trigger_source="manual", scheduled_at=NOW,
                         request_id="auth-wait-slot-run", only_stage="oecid", sources=["campaign"])
        stage = next(row for row in run["stages"] if row["state"] == "queued")
        ticket = claim(self.store, stage["stageRunId"], "scheduler-auth-owner",
                       [("workflow:it", 1), ("communications:acc6", 1), ("platform:global", 1)], worker_pid=1)
        executor = SubprocessStageExecutor(self.root)
        executor._claims.ticket = ticket | {"ownerId": "scheduler-auth-owner"}
        executor.sleep = lambda seconds: None
        seen = []

        def worker(args, label, timeout=14400):
            holders = [row[0] for row in self.store.db.execute(
                "SELECT owner_stage_run_id FROM workflow_resource_slot WHERE resource_key='platform:global'")]
            seen.append(holders)
            intent = self.store.db.execute("SELECT intent_id FROM account_maintenance_intent").fetchone()[0]
            self.settle(intent, "completed")
            self.publish(2)
            return {"state": "completed"}

        executor._call = worker
        result = executor._relogin_market_account(self.store, "it", "communications", "run-1", "oecid-0")
        self.assertEqual(result["state"], "completed")
        self.assertEqual(seen, [[]])  # free for other markets during the wait
        self.assertEqual([row[0] for row in self.store.db.execute(
            "SELECT owner_stage_run_id FROM workflow_resource_slot WHERE resource_key='platform:global'")],
            [stage["stageRunId"]])
        # The account and market slots were never let go.
        self.assertEqual(self.store.db.execute(
            "SELECT count(*) FROM workflow_resource_slot WHERE owner_stage_run_id=?", (stage["stageRunId"],)).fetchone()[0], 3)

    def test_a_stage_that_cannot_take_the_slot_back_stops_instead_of_reading_without_it(self):
        from lib.workflow_resources import lend_slot, reclaim_slot, claim
        from lib.operations_workflow import create_run
        runs = [create_run(self.store, market=m, trigger_source="manual", scheduled_at=NOW,
                           request_id=f"slot-reclaim-{m}", only_stage="oecid", sources=["campaign"]) for m in ("it",)]
        stage = next(row for row in runs[0]["stages"] if row["state"] == "queued")
        ticket = claim(self.store, stage["stageRunId"], "scheduler-slot-owner", [("platform:global", 1)], worker_pid=1)
        self.assertTrue(lend_slot(self.store, stage["stageRunId"], "scheduler-slot-owner", ticket["fence"], "platform:global"))
        self.store.db.execute("INSERT INTO workflow_resource_slot VALUES('platform:global',0,'other-stage',999,?,?)", (NOW + 300, NOW))
        self.assertFalse(reclaim_slot(self.store, stage["stageRunId"], "scheduler-slot-owner", ticket["fence"], "platform:global", 1))
        executor = SubprocessStageExecutor(self.root)
        executor._claims.ticket = ticket | {"ownerId": "scheduler-slot-owner"}
        self.assertFalse(executor._reclaim_platform_slot(self.store, lambda _s: None, wait_seconds=0))

    def test_it_auth_report_marks_only_the_lapsed_login(self):
        self.assertTrue(it_login_expired({"authReads": [{"code": 0}, {"code": AUTH_REQUIRED,
                                                                        "errorCode": "business_rejected"}]}))
        self.assertFalse(it_login_expired({"authReads": [{"code": 98000001, "errorCode": "business_rejected"}]}))
        self.assertFalse(it_login_expired({"authReads": [{"code": AUTH_REQUIRED, "errorCode": "http_rejected"}]}))
        self.assertFalse(it_login_expired({}))

    def test_failed_auth_read_carries_the_platform_code(self):
        class Result:
            def __init__(self, code):
                self.code = code
                self.payload = {"code": code, "data": {"ok": True}}

        class Reader:
            def __init__(self, code):
                self.result = Result(code)

            def _xhr(self, **request):
                return self.result

            @staticmethod
            def require_read(result):
                if result.code != 0:
                    raise ValueError("taplink_remote_read_failed")
                return result.payload

        with self.assertRaisesRegex(ValueError, "taplink_remote_read_failed") as caught:
            _read(Reader(AUTH_REQUIRED), method="GET", path="/info", params={}, payload=None, write=False)
        self.assertEqual(caught.exception.platform_code, AUTH_REQUIRED)
        self.assertEqual(_read(Reader(0), method="GET", path="/info", params={}, payload=None, write=False)["data"],
                         {"ok": True})


if __name__ == "__main__":
    unittest.main()
