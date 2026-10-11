import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from lib.creator_core_profile import CORE_FIELDS, core_refresh_plan, find_core_observations
from lib.creator_identity import CreatorIdentityStore
from lib.profile_completion import summarize_profile


def envelope(value):
    return {"status": 0, "is_authorized": True, "value": value}


def fixture(market="br", amount="100.01"):
    found = summarize_profile({"handle": envelope("alice"), "creator_oecuid": envelope("123"),
        "selection_region": envelope("GB" if market == "uk" else market.upper()),
        "med_gmv_revenue": envelope({"value": amount, "symbol": "R$"}),
        "video_gmv": envelope({"value": "0", "symbol": "R$"}),
        "live_gmv": envelope({"value": "0", "symbol": "R$"}),
        "industry_groups": envelope([{"id": "-1", "value": "0.2"}, {"id": "10", "name": "Beauty", "value": "0.8"}]),
        "sales_performance_end_time": envelope(1791504000)})
    target = {"targetRef": "a", "externalId": "source-a", "status": "identity_verified",
              "currentHandleResolved": True, "find": found, "oecId": "123"}
    schema = "bdhub.italy-profile-probe.v3" if market == "it" else "bdhub.market-profile-probe.v1"
    report = {"schema": schema, "market": market, "account": "acc2",
        "status": "completed", "identityFileUnchanged": True, "oldDatabaseWrites": 0, "realSends": 0,
        "finishedAt": "2026-10-11T08:00:00+00:00", "targets": [target], "requests": [
            {"targetRef": "a", "stage": "find", "status": "returned", "httpStatus": 200,
             "code": "0", "verificationRequired": False, "systemError3": False}]}
    requested = [{"ref": "a", "handle": "alice", "sourceId": "source-a", "pid": "unused"}]
    return report, requested


def observations(report, requested, **kwargs):
    return find_core_observations(report, requested, market=report["market"], account="acc2", **kwargs)


class CoreProfileTests(unittest.TestCase):
    def test_exact_amount_zero_unmapped_category_and_unknown_window(self):
        report, requested = fixture(amount="9007199254740993.01")
        row = observations(report, requested)[0]
        self.assertTrue(row["coreComplete"])
        fields = row["payload"]["fields"]
        self.assertEqual(fields["med_gmv_revenue"]["value"]["decimal"], "9007199254740993.01")
        self.assertEqual(fields["video_gmv"]["status"], "zero")
        self.assertEqual(fields["industry_groups"]["value"][0], {"id": "-1", "weight": "0.2"})
        self.assertIsNone(row["payload"]["profileSource"]["windowStart"])
        self.assertNotIn("primaryChannel", json.dumps(row))
        self.assertNotIn("currency", json.dumps(row))
        self.assertEqual(row["observedAt"], "2026-10-11T08:00:00.000000Z")

    def test_all_markets_accept_tiktok_find_facts_and_gb_maps_to_uk(self):
        for market in ("br", "my", "th", "uk", "it", "fr", "de"):
            with self.subTest(market=market):
                report, requested = fixture(market)
                self.assertEqual(observations(report, requested)[0]["market"], market)

    def test_unauthorized_never_carries_old_or_hidden_values(self):
        report, requested = fixture()
        report["targets"][0]["find"]["fields"]["med_gmv_revenue"] = {"status": "unauthorized", "value": "hidden"}
        row = observations(report, requested)[0]
        self.assertFalse(row["coreComplete"])
        self.assertEqual(row["payload"]["fields"]["med_gmv_revenue"], {"status": "unauthorized"})
        self.assertNotIn("hidden", json.dumps(row))

    def test_nested_unrelated_values_do_not_enter_the_payload(self):
        report, requested = fixture()
        fields = report["targets"][0]["find"]["fields"]
        fields["med_gmv_revenue"]["value"]["token"] = "secret"
        fields["industry_groups"]["value"][0]["email"] = "secret"
        row = observations(report, requested)[0]
        self.assertNotIn("secret", json.dumps(row))

    def test_expected_oec_rejects_a_recycled_handle(self):
        report, requested = fixture()
        with self.assertRaisesRegex(ValueError, "identity_mismatch"):
            observations(report, requested, expected_oecs={"a": "456"})

    def test_wrong_account_market_external_id_receipt_and_time_are_rejected(self):
        for change in ("account", "market", "externalId", "receipt", "time"):
            report, requested = fixture()
            with self.subTest(change=change):
                if change == "account": report["account"] = "acc5"
                if change == "market": report["targets"][0]["find"]["identity"]["market"] = "my"
                if change == "externalId": report["targets"][0]["externalId"] = "other"
                if change == "receipt": report["requests"][0]["code"] = "100000"
                if change == "time": report["finishedAt"] = "2026-10-11T08:00:00"
                with self.assertRaises(ValueError): observations(report, requested)

    def test_profile_error_preserves_successful_find_prefix(self):
        report, requested = fixture()
        report["status"] = "blocked"
        report["targets"][0]["status"] = None
        report["requests"].append({"targetRef": "a", "stage": "profile", "status": "returned",
            "httpStatus": 200, "code": "100000", "verificationRequired": False, "systemError3": True})
        self.assertTrue(observations(report, requested)[0]["coreComplete"])

    def test_duplicate_receipt_and_request_are_rejected(self):
        report, requested = fixture()
        report["requests"].append(copy.deepcopy(report["requests"][0]))
        with self.assertRaisesRegex(ValueError, "report_invalid"): observations(report, requested)
        report, requested = fixture()
        with self.assertRaisesRegex(ValueError, "duplicate_request"):
            observations(report, requested + requested)

    def test_idempotent_persistence_retains_latest_unauthorized_separately(self):
        report, requested = fixture()
        row = observations(report, requested)[0]
        with tempfile.TemporaryDirectory() as folder, CreatorIdentityStore(Path(folder) / "identities.sqlite") as store:
            for _ in range(2):
                store.observe_profile(row["market"], row["oecId"], row["handle"], row["observedAt"], row["evidenceRef"], row["payload"])
            report["finishedAt"] = "2026-10-12T08:00:00Z"
            for field in CORE_FIELDS:
                report["targets"][0]["find"]["fields"][field] = {"status": "unauthorized"}
            latest = observations(report, requested)[0]
            store.observe_profile(latest["market"], latest["oecId"], latest["handle"], latest["observedAt"], latest["evidenceRef"], latest["payload"])
            self.assertEqual(store.stats()["identities"], 1)
            self.assertEqual(store.stats()["observations"], 2)
            history = store.history("br", "123")
            self.assertEqual(len(history), 2)
            self.assertEqual(history[0]["payload"]["fields"]["med_gmv_revenue"]["value"]["decimal"], "100.01")
            self.assertEqual(history[1]["payload"]["fields"]["med_gmv_revenue"], {"status": "unauthorized"})
            self.assertEqual(store.get_by_oec("br", "123")["lastObservedAt"], latest["observedAt"])

    def test_unavailable_states_and_bad_amounts_are_not_zero(self):
        for state in ("absent", "no_value", "unauthorized", "error"):
            report, requested = fixture()
            report["targets"][0]["find"]["fields"]["med_gmv_revenue"] = {"status": state}
            self.assertEqual(observations(report, requested)[0]["payload"]["fields"]["med_gmv_revenue"], {"status": state})
        for amount in ("NaN", "Infinity", "-1", "1e999999", True):
            report, requested = fixture()
            report["targets"][0]["find"]["fields"]["med_gmv_revenue"]["value"]["decimal"] = amount
            self.assertEqual(observations(report, requested)[0]["payload"]["fields"]["med_gmv_revenue"]["status"], "error")

    def test_refresh_routes_are_bounded_and_identity_bound(self):
        for market in ("br", "my", "th", "uk", "it", "fr", "de"):
            creator = {"market": market, "creator_id": "creator_a", "oec_id": "123", "current_handle": "alice"}
            plan = core_refresh_plan(market, [creator])
            target = plan["probeInput"]["targets"][0]
            self.assertEqual(plan["expectedOecs"][target["ref"]], "123")
            if market in ("br", "my", "th"):
                self.assertNotIn("oecId", target)
                self.assertTrue(plan["probeInput"]["identityOnly"])
            else:
                self.assertEqual(target["oecId"], "123")
                self.assertEqual(plan["probeInput"]["profileTypeSets"], [[2]])
            self.assertFalse(plan["executionStarted"])
        with self.assertRaisesRegex(ValueError, "target_count_invalid"):
            core_refresh_plan("br", [creator] * 4)
        with self.assertRaisesRegex(ValueError, "identity_mismatch"):
            core_refresh_plan("br", [creator])


if __name__ == "__main__":
    unittest.main()
