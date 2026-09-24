import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from lib.account_identity import current_generation, publish_generation  # noqa: E402
from lib.login_recovery import AUTH_REQUIRED, it_login_expired, request_refresh  # noqa: E402
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

    def publish(self, n):
        publish_generation(self.store, market="it", account="acc6", role="communications", reason="relogin",
                           identity={"browserRef": f"browser:{n}", "httpRef": f"http:{n}", "imRef": f"im:{n}",
                                     "institutionFingerprint": "i" * 64},
                           capabilities={"http": {"state": "verified"}, "im": {"state": "verified"}}, now=NOW + n)
        return current_generation(self.store, "it", "acc6")["generationId"]

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
        self.assertEqual(self.refresh(), {"state": "not_requested", "reason": "account_maintenance_active"})
        (self.root / "config/market-accounts.json").write_text("{}")
        self.assertEqual(self.refresh()["state"], "not_requested")
        self.assertEqual(self.spawned, [])

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
