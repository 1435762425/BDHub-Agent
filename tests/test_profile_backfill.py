import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from lib.creator_identity import CreatorIdentityStore, IdentityConflict
from lib.market_identity import _apply
from lib.profile_backfill import apply, preview
from lib.profile_completion import summarize_profile
from lib.second_cycle import CycleStore
from lib.schema_migrations import apply_database


def fixture():
    wrap = lambda value: {"status": 0, "is_authorized": True, "value": value}
    found = summarize_profile({"handle": wrap("alice"), "creator_oecuid": wrap("123"), "selection_region": wrap("BR"),
        "med_gmv_revenue": wrap({"value": "100.01", "symbol": "R$"}), "video_gmv": wrap({"value": "100.01"}),
        "live_gmv": wrap({"value": "0"}), "industry_groups": wrap([{"id": "10", "name": "Beauty", "value": "1"}])})
    request = {"ref": "a", "handle": "alice", "sourceId": "source-a", "pid": "1" * 19}
    report = {"schema": "bdhub.market-profile-probe.v1", "market": "br", "account": "acc2", "status": "completed",
        "finishedAt": "2026-10-09T01:00:00+00:00", "oldDatabaseWrites": 0, "realSends": 0, "identityFileUnchanged": True,
        "targets": [{"targetRef": "a", "externalId": "source-a", "status": "identity_verified", "currentHandleResolved": True,
                     "find": found, "oecId": "123"}],
        "requests": [{"targetRef": "a", "stage": "find", "status": "returned", "httpStatus": 200, "code": "0",
                      "verificationRequired": False, "systemError3": False}]}
    return report, request


class ProfileBackfillTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.folder = self.root / "var/market-identity-br-test/output"
        self.folder.mkdir(parents=True)
        self.report, self.request = fixture()
        self.write_report()
        self.store = CreatorIdentityStore(self.root / "var/creator-identities.sqlite")
        self.addCleanup(self.store.close)

    def write_report(self):
        (self.folder / "report.private.json").write_text(json.dumps(self.report))
        (self.folder.parent / "targets.private.json").write_text(json.dumps({"market": "br", "identityOnly": True,
            "targets": [{"ref": "a", "handle": "alice", "externalId": "source-a"}]}))

    def bind_original(self):
        digest = hashlib.sha256(json.dumps(self.report, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        ref = f"market-profile-probe:{digest}:a"
        lead = self.store.record_handle_lead("br", "alice", self.report["finishedAt"], ref + ":lead")
        return self.store.resolve_handle_lead(lead["leadId"], "123", self.report["finishedAt"], ref + ":find", exact_find={
            "market": "br", "queriedHandle": "alice", "returnedHandle": "alice", "oecId": "123", "httpStatus": 200,
            "code": 0, "verificationRequired": False})["identity"]

    def test_preview_has_no_writes_and_unconsumed_report_is_not_a_profile_source(self):
        before = self.store.stats()
        result = preview(self.root)
        self.assertEqual(result["observations"], 0)
        self.assertEqual(result["skipped"]["not_bound_by_original_identity_ledger"], 1)
        self.assertEqual(self.store.stats(), before)

    def test_same_plan_is_idempotent_and_keeps_original_time(self):
        creator = self.bind_original()
        initial = preview(self.root)
        self.assertEqual(initial["observations"], 1)
        self.assertEqual(initial["pendingObservations"], 1)
        self.assertEqual(apply(self.root, initial["previewHash"], report_files=initial["reportFiles"])["imported"], 1)
        self.assertEqual(apply(self.root, initial["previewHash"], report_files=initial["reportFiles"])["imported"], 0)
        history = self.store.history("br", "123")
        self.assertEqual(len(history), 2)
        profile = next(r for r in history if r["evidenceRef"].endswith(":find-profile"))
        self.assertEqual(profile["observedAt"], "2026-10-09T01:00:00.000000Z")
        self.assertEqual(profile["payload"]["fields"]["med_gmv_revenue"]["value"]["decimal"], "100.01")
        self.assertEqual(self.store.get_by_oec("br", "123")["creatorId"], creator["creatorId"])
        self.assertNotIn("pid", json.dumps(profile["payload"]))

    def test_historical_import_does_not_refresh_current_name_or_date(self):
        creator = self.bind_original()
        latest = self.store.observe_profile("br", "123", "new.alice", "2026-10-12T00:00:00Z", "newest:profile")
        initial = preview(self.root)
        apply(self.root, initial["previewHash"])
        self.assertEqual(self.store.get_by_oec("br", "123"), latest)
        self.assertEqual(creator["creatorId"], latest["creatorId"])

    def test_changed_report_account_or_amount_cannot_be_imported_under_old_proof(self):
        self.bind_original()
        plan = preview(self.root)
        self.report["account"] = "acc5"
        self.write_report()
        self.assertEqual(preview(self.root)["observations"], 0)
        with self.assertRaisesRegex(ValueError, "preview_changed"):
            apply(self.root, plan["previewHash"])
        self.assertEqual(len(self.store.history("br", "123")), 1)

    def test_frozen_file_scope_ignores_later_reports_and_rejects_external_paths(self):
        self.bind_original()
        plan = preview(self.root)
        other = self.root / "var/market-identity-br-later/output"
        other.mkdir(parents=True)
        (other / "report.private.json").write_text("{}")
        self.assertEqual(apply(self.root, plan["previewHash"], report_files=plan["reportFiles"])["imported"], 1)
        with self.assertRaisesRegex(ValueError, "input_invalid"):
            preview(self.root, report_files=["../outside/report.private.json"])

    def test_existing_stable_identity_is_required_for_guarded_import(self):
        before = self.store.stats()
        with self.assertRaises(IdentityConflict):
            self.store.observe_known_profile("creator_unknown", "br", "123", "alice", "2026-10-09T00:00:00Z", "wrong")
        self.assertEqual(self.store.stats(), before)

    def prepare_cycle(self):
        (self.root / "config").mkdir()
        for file in ("market-accounts.json", "markets.json"):
            shutil.copyfile(ROOT / "config" / file, self.root / "config" / file)
        with CycleStore(self.root / "var/second-cycle.sqlite") as store:
            store.plan("bjn-local-research", "br")
        apply_database(self.root, "second-cycle")

    def test_normal_identity_apply_also_saves_find_profile_without_extra_request(self):
        self.prepare_cycle()
        result = _apply(self.root, "br", self.report, [self.request], expected_account="acc2")
        self.assertEqual(result["resolvedHandles"], 1)
        self.assertEqual(result["coreProfilesSaved"], 1)
        self.assertEqual(result["coreProfileErrors"], 0)
        self.assertEqual(len(self.store.history("br", "123")), 2)
        self.assertEqual(_apply(self.root, "br", self.report, [self.request], expected_account="acc2")["coreProfilesSaved"], 1)
        self.assertEqual(len(self.store.history("br", "123")), 2)

    def test_bad_optional_profile_keeps_a_confirmed_identity(self):
        self.prepare_cycle()
        self.report["targets"][0]["find"]["fields"] = {"wrong": "shape"}
        result = _apply(self.root, "br", self.report, [self.request], expected_account="acc2")
        self.assertEqual(result["resolvedHandles"], 1)
        self.assertEqual(result["coreProfilesSaved"], 0)
        self.assertEqual(result["coreProfileErrors"], 1)
        self.assertIsNotNone(self.store.get_by_oec("br", "123"))


if __name__ == "__main__":
    unittest.main()
