import json
import tempfile
import unittest
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from lib.account_identity import (apply_bootstrap, claim_next, current_generation, execute_claimed,
                                  next_due, publish_generation, request_maintenance, set_enabled,
                                  project_runtime_readiness, status)  # noqa:E402
from lib.schema_migrations import apply_database  # noqa:E402
from lib.second_cycle import CycleStore  # noqa:E402


NOW = datetime(2026, 9, 20, 14, 45, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp()


def config(authority="legacy_readonly"):
    return {"schemaVersion": 1, "markets": {"it": {
        "accounts": ["acc6", "acc9"], "roles": {"communications": "acc6", "supply": "acc9"},
        "assignmentState": "catalog_reads_enabled", "validationEvidence": "var/evidence.json",
        "credentialAuthority": authority,
        "maintenanceExecutor": "agent_identity_generation" if authority == "project_owned" else "legacy_lifecycle",
        "automaticRoleSwitchEnabled": False,
    }}, "lifecycle": {"healthPollMinutes": None, "deepCheckHours": None,
        "identityRefreshHours": 72, "loginMaintenanceHours": 72,
        "standbyEarlyMaintenanceHours": 0, "globalLoginConcurrency": 1,
        "enableNewMaintenanceWorker": authority == "project_owned"}}


class Adapter:
    def __init__(self, refresh=True, relogin=True):
        self.refresh_ok = refresh
        self.relogin_ok = relogin
        self.calls = []

    def drain(self, account):
        self.calls.append(("drain", account))

    def _result(self, ok, account):
        return {"ok": ok, "identity": {"browserRef": f"browser:{account}:2",
            "httpRef": f"http:{account}:2", "imRef": f"im:{account}:2",
            "institutionFingerprint": "i" * 64}}

    def refresh(self, account):
        self.calls.append(("refresh", account))
        return self._result(self.refresh_ok, account)

    def relogin(self, account):
        self.calls.append(("relogin", account))
        return self._result(self.relogin_ok, account)

    def validate(self, account, result):
        self.calls.append(("validate", account))
        return {"http": {"state": "verified", "evidenceRef": "http-proof"},
                "im": {"state": "verified", "evidenceRef": "im-proof"}}

    def reconnect_inbox(self, account, previous, generated):
        self.calls.append(("reconnect", account))


class AccountIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "var").mkdir()
        (self.root / "config").mkdir()
        (self.root / "config/market-accounts.json").write_text(json.dumps(config()))
        with CycleStore(self.root / "var/second-cycle.sqlite", lambda: NOW) as store:
            store.plan("bjn-local-research", "it")
        apply_database(self.root, "second-cycle", clock=lambda: NOW)
        self.store = CycleStore(self.root / "var/second-cycle.sqlite", lambda: NOW)
        self.evidence = {"accounts": {
            "acc6": {"capabilities": {"catalog_read": "verified", "im_token": "verified"}},
            "acc9": {"capabilities": {"catalog_read": "verified", "im_token": "verified"}},
        }}

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_bootstrap_is_secret_free_and_idempotent(self):
        result = apply_bootstrap(self.store, self.root, self.evidence)
        self.assertEqual(result["applied"], 2)
        self.assertEqual(apply_bootstrap(self.store, self.root, self.evidence)["applied"], 0)
        generation = current_generation(self.store, "it", "acc6")
        self.assertTrue(generation["browserRef"].startswith("legacy-readonly-profile:"))
        self.assertNotIn("/", generation["browserRef"])

    def test_communications_is_claimed_before_supply(self):
        request_maintenance(self.store, self.root, market="it", account="acc9", operation="refresh",
                            request_id="maintenance-request-acc9", scheduled_at=NOW - 10)
        request_maintenance(self.store, self.root, market="it", account="acc6", operation="refresh",
                            request_id="maintenance-request-acc6", scheduled_at=NOW - 5)
        claimed = claim_next(self.store, now=NOW)
        self.assertEqual((claimed["account"], claimed["role"]), ("acc6", "communications"))
        self.assertIsNone(claim_next(self.store, now=NOW))

    def test_dead_maintenance_worker_is_recovered_before_the_next_claim(self):
        intent = request_maintenance(self.store, self.root, market="it", account="acc6",
                                     operation="relogin", request_id="maintenance-dead-worker",
                                     scheduled_at=NOW)
        claim_next(self.store, now=NOW)
        with self.store.tx():
            self.store.db.execute("UPDATE account_maintenance_intent SET checkpoint_json=? WHERE intent_id=?",
                                  (json.dumps({"stage": "browser_login", "workerPid": 2147483647}),
                                   intent["intentId"]))
        self.assertIsNone(claim_next(self.store, now=NOW))
        latest = status(self.store, self.root)["accounts"][0]["maintenance"]
        self.assertEqual(latest["state"], "failed_known")
        self.assertEqual(latest["errorCode"], "account_maintenance_worker_exited")

    def test_readonly_authority_never_executes_refresh_or_relogin(self):
        request_maintenance(self.store, self.root, market="it", account="acc6", operation="relogin",
                            request_id="maintenance-request-readonly", scheduled_at=NOW)
        claimed = claim_next(self.store, now=NOW)
        adapter = Adapter()
        result = execute_claimed(self.store, self.root, claimed["intentId"], adapter)
        self.assertEqual(result["state"], "needs_human")
        self.assertEqual(result["errorCode"], "project_identity_authority_required")
        self.assertEqual(adapter.calls, [])

    def test_project_owned_refresh_publishes_browser_http_im_atomically(self):
        (self.root / "config/market-accounts.json").write_text(json.dumps(config("project_owned")))
        publish_generation(self.store, market="it", account="acc6", role="communications",
                           reason="baseline", identity={"browserRef": "browser:1", "httpRef": "http:1",
                           "imRef": "im:1", "institutionFingerprint": "i" * 64},
                           capabilities={"http": {"state": "verified"}, "im": {"state": "verified"}})
        request_maintenance(self.store, self.root, market="it", account="acc6", operation="refresh",
                            request_id="maintenance-request-project", scheduled_at=NOW)
        claimed = claim_next(self.store, now=NOW)
        adapter = Adapter(refresh=True)
        result = execute_claimed(self.store, self.root, claimed["intentId"], adapter)
        self.assertEqual(result["state"], "completed")
        current = current_generation(self.store, "it", "acc6")
        self.assertEqual((current["browserRef"], current["httpRef"], current["imRef"]),
                         ("browser:acc6:2", "http:acc6:2", "im:acc6:2"))
        self.assertIn(("reconnect", "acc6"), adapter.calls)

    def test_project_readiness_is_authoritative_after_legacy_account_retirement(self):
        (self.root / "config/market-accounts.json").write_text(json.dumps(config("project_owned")))
        capabilities = {name: {"state": "verified", "evidenceRef": "project-proof"}
                        for name in ("browser_session", "partner_http", "institution_market", "im_identity")}
        publish_generation(self.store, market="it", account="acc6", role="communications",
                           reason="relogin", identity={"browserRef": "browser:acc6", "httpRef": "http:acc6",
                           "imRef": "im:acc6", "institutionFingerprint": "i" * 64},
                           capabilities=capabilities)
        readiness = project_runtime_readiness(self.root)
        acc6 = next(row for row in readiness["accounts"] if row["name"] == "acc6")
        acc9 = next(row for row in readiness["accounts"] if row["name"] == "acc9")
        self.assertTrue(acc6["startable"])
        self.assertEqual(acc6["authority"], "project_owned")
        self.assertFalse(acc9["startable"])
        self.assertEqual(acc9["blockers"], [{"code": "project_identity_missing"},
                                             {"code": "project_identity_capability_unverified",
                                              "capabilities": sorted(("browser_session", "partner_http",
                                                                      "institution_market", "im_identity"))}])

        set_enabled(self.store, self.root, "it", "acc6", False,
                    "disable-project-acc6", 0)
        disabled = next(row for row in project_runtime_readiness(self.root)["accounts"]
                        if row["name"] == "acc6")
        self.assertFalse(disabled["startable"])
        self.assertIn({"code": "project_account_disabled"}, disabled["blockers"])

    def test_explicit_relogin_opens_relogin_without_silent_refresh_first(self):
        (self.root / "config/market-accounts.json").write_text(json.dumps(config("project_owned")))
        request_maintenance(self.store, self.root, market="it", account="acc6", operation="relogin",
                            request_id="maintenance-explicit-relogin", scheduled_at=NOW)
        claimed = claim_next(self.store, now=NOW)
        adapter = Adapter(refresh=True, relogin=True)
        result = execute_claimed(self.store, self.root, claimed["intentId"], adapter)
        self.assertEqual(result["state"], "completed")
        self.assertIn(("relogin", "acc6"), adapter.calls)
        self.assertNotIn(("refresh", "acc6"), adapter.calls)

    def test_failed_new_generation_does_not_replace_last_published(self):
        first = publish_generation(self.store, market="it", account="acc6", role="communications",
                                   reason="baseline", identity={"browserRef": "browser:1", "httpRef": "http:1",
                                   "imRef": "im:1", "institutionFingerprint": "i" * 64},
                                   capabilities={"http": {"state": "verified"}})
        with self.assertRaisesRegex(Exception, "capability_validation_failed"):
            publish_generation(self.store, market="it", account="acc6", role="communications",
                               reason="refresh", identity={"browserRef": "browser:bad", "httpRef": "http:bad",
                               "imRef": "im:bad", "institutionFingerprint": "i" * 64},
                               capabilities={"http": {"state": "failed"}})
        self.assertEqual(current_generation(self.store, "it", "acc6")["generationId"], first["generationId"])

    def test_enable_setting_revision_and_72_hour_slots(self):
        changed = set_enabled(self.store, self.root, "it", "acc6", False,
                              "account-setting-request", 0)
        self.assertFalse(changed["enabled"])
        self.assertEqual(status(self.store, self.root)["intervalHours"], 72)
        base = datetime(2026, 9, 17, 14, 20, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp()
        zone = ZoneInfo("Asia/Shanghai")
        communication = datetime.fromtimestamp(next_due(base, "communications", NOW), zone)
        supply = datetime.fromtimestamp(next_due(base, "supply", NOW), zone)
        self.assertEqual((communication.hour, communication.minute), (14, 30))
        self.assertEqual((supply.hour, supply.minute), (14, 40))
        rows = {row["account"]: row for row in status(self.store, self.root)["accounts"]}
        self.assertEqual(rows["acc6"]["responsibilities"],
                         ["inbox_read", "message_send", "agent_reply", "oecid_find", "creator_profile"])
        self.assertEqual(rows["acc9"]["responsibilities"],
                         ["catalog_read", "campaign", "product_select", "taplink"])


if __name__ == "__main__":
    unittest.main()
