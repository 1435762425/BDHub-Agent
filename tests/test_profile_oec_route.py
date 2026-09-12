"""Known OEC refresh must never rediscover the creator using an old handle."""
import json
from pathlib import Path
import tempfile
import unittest

from test_profile_probe_pipeline import ProbeHarness, profile, probe


class OecRouteTests(unittest.TestCase):
    def harness(self, path, *, handle=None, replies=None):
        harness = ProbeHarness(path, replies=replies)
        target = {"ref": "known-1", "oecId": "123456789"}
        if handle is not None:
            target["handle"] = handle
        harness.targets.write_text(json.dumps({"market": "it", "targets": [target]}))
        return harness

    def test_oec_only_input_fetches_profile_without_find(self):
        with tempfile.TemporaryDirectory() as temp:
            harness = self.harness(Path(temp), replies=[{"code": 0, "creator_profile": profile()}] * 2)
            code, report = harness.run()
            self.assertEqual(code, 0)
            self.assertEqual([stage for stage, _ in harness.calls], ["profile", "profile"])
            target = report["targets"][0]
            self.assertIsNone(target["requestedHandle"])
            self.assertTrue(target["currentPlatformIdentityVerified"])
            self.assertEqual(target["merged"]["identity"]["handle"], "italian.creator")

    def test_oec_wins_over_old_handle_and_accepts_remote_rename(self):
        with tempfile.TemporaryDirectory() as temp:
            harness = self.harness(Path(temp), handle="previous.name", replies=[{"code": 0, "creator_profile": profile()}] * 2)
            code, report = harness.run()
            self.assertEqual(code, 0)
            target = report["targets"][0]
            self.assertTrue(target["handleChangedFromAudit"])
            self.assertEqual(target["auditHandle"], "previous.name")
            self.assertTrue(all(stage == "profile" and body["creator_oec_id"] == "123456789" for stage, body in harness.calls))
            self.assertNotIn("previous.name", json.dumps([body for _, body in harness.calls]))

    def test_missing_remote_name_is_not_filled_with_audit_name(self):
        with tempfile.TemporaryDirectory() as temp:
            shell = profile(handle={"is_authorized": True})
            harness = self.harness(Path(temp), handle="previous.name", replies=[{"code": 0, "creator_profile": shell}] * 3)
            code, report = harness.run()
            self.assertEqual(code, 0)
            target = report["targets"][0]
            self.assertIsNone(target["merged"]["identity"]["handle"])
            self.assertFalse(target["currentHandleResolved"])
            self.assertTrue(target["currentPlatformIdentityVerified"])
            self.assertEqual(harness.calls[-1][1]["profile_types"], [1, 6])

    def test_wrong_oec_stops_before_supplement_or_find(self):
        with tempfile.TemporaryDirectory() as temp:
            harness = self.harness(Path(temp), replies=[{"code": 0, "creator_profile": profile(creator_oecuid={"value": "9999"})}])
            code, report = harness.run()
            self.assertEqual(code, 2)
            self.assertEqual(len(harness.calls), 1)
            self.assertEqual(report["reason"], "profile_identity_mismatch")

    def test_failed_oec_request_never_falls_back_to_old_handle(self):
        with tempfile.TemporaryDirectory() as temp:
            harness = self.harness(Path(temp), handle="recycled.name", replies=[{"code": 100000}])
            code, report = harness.run()
            self.assertEqual(code, 2)
            self.assertEqual([stage for stage, _ in harness.calls], ["profile"])
            self.assertEqual(report["targets"][0]["requestedOecId"], "123456789")

    def test_handle_hint_is_not_an_oec_request_and_numeric_ids_rejected(self):
        target = probe.normalize_target({"ref": "new", "handle": "@NEW.HANDLE", "externalId": "source:1", "historicalOecHint": "123"})
        self.assertEqual(target["inputKind"], "handle_discovery")
        self.assertIsNone(target["oecId"])
        for oec in (123456789, True, "", "１２３", "1.5", "123 token"):
            with self.assertRaises(ValueError):
                probe.normalize_target({"ref": "bad", "oecId": oec})


if __name__ == "__main__":
    unittest.main()
