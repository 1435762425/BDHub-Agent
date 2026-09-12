"""Read-only source projection tests using isolated synthetic cohorts and identities."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from lib.creator_identity import CreatorIdentityStore
from lib.second_outreach_source import ITALIAN_PRODUCT_NAMES, SecondOutreachSourceError, build_second_source, load_second_source


PIDS = list(ITALIAN_PRODUCT_NAMES)


def cohort():
    source = {"ref": "fixture", "observedAt": 1789016335000, "windowStart": 1787702400000,
              "windowEnd": 1788911999999, "windowBasis": "calendar_date_unknown_timezone"}
    creators, edges = [], []
    for index in range(48):
        external_id = str(7000000000000000000 + index)
        creator_id = "it-kalodata-" + external_id
        creators.append({"id": creator_id, "market": "it", "oecId": None, "externalIdentity": {"namespace": "kalodata", "id": external_id},
                         "name": f"@fixture_{index}", "source": {**source, "ref": f"fixture:creator:{external_id}"}})
        edges.append({"id": f"edge-{index}", "creatorId": creator_id, "market": "it", "pid": PIDS[0], "units": index + 1,
                      "format": "video", "source": {**source, "ref": f"fixture:pair:{index}"}})
    return {"dataset": {"id": "italy-pilot-fixture", "mode": "imported-offline", "sourceRefs": ["fixture sha256:" + "a" * 64]},
            "batch": {"creators": creators, "products": [{"id": "product-" + pid, "market": "it", "pid": pid, "title": "fixture product", "source": source} for pid in PIDS],
                      "evidence": edges, "offers": [], "demands": []}}


class SecondOutreachSourceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory();self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name);(self.root / "var").mkdir()
        self.document = cohort();self.source_path = self.root / "var/italy-offline-batch.json"
        self.expected = self.write_source()
        self.identities = CreatorIdentityStore(self.root / "var/creator-identities.sqlite");self.addCleanup(self.identities.close)

    def write_source(self):
        raw = json.dumps(self.document, ensure_ascii=False, sort_keys=True).encode()
        self.source_path.write_bytes(raw)
        return hashlib.sha256(raw).hexdigest()

    def load(self):
        return load_second_source(self.root, expected_source_sha256=self.expected)

    def link(self, index=0, oec_id="7490000000000000001", *, source="probe_source_reference"):
        creator = self.document["batch"]["creators"][index]
        handle = creator["name"][1:]
        lead = self.identities.record_handle_lead("it", handle, "2026-09-10T00:00:00Z", f"source-lead:{index}:{oec_id}",
            external_source=source, external_id=creator["externalIdentity"]["id"])
        result = self.identities.resolve_handle_lead(lead["leadId"], oec_id, "2026-09-12T00:00:00Z", f"find:{index}:{oec_id}", exact_find={
            "market": "it", "queriedHandle": handle, "returnedHandle": handle, "oecId": oec_id,
            "httpStatus": 200, "code": 0, "verificationRequired": False})
        return result["identity"]

    def test_fixed_cohort_is_reproducible_read_only_and_keeps_historical_ownership_unverified(self):
        self.link()
        before = self.identities.stats();source_bytes = self.source_path.read_bytes()
        first, second = self.load(), self.load()
        self.assertEqual(first, second);self.assertEqual(self.identities.stats(), before);self.assertEqual(self.source_path.read_bytes(), source_bytes)
        self.assertEqual(len(first["opportunities"]), 48)
        self.assertEqual(first["summary"]["currentRecipientsVerified"], 1);self.assertEqual(first["summary"]["unresolvedRecipients"], 47)
        self.assertTrue(all(row["historicalOwnership"] == "unverified" for row in first["opportunities"]))
        self.assertEqual(first["summary"]["historicalOwnershipVerified"], 0)

    def test_current_oec_survives_rename_and_reused_source_handle_does_not_switch_recipient(self):
        original = self.link();before = self.load()["opportunities"][0]
        self.identities.observe_profile("it", original["oecId"], "renamed_current", "2026-09-13T00:00:00Z", "rename")
        replacement = self.identities.observe_profile("it", "7490000000000000999", "fixture_0", "2026-09-13T00:00:00Z", "reused-handle")
        after = self.load()["opportunities"][0]
        self.assertEqual(after["id"], before["id"]);self.assertNotEqual(after["fingerprint"], before["fingerprint"])
        self.assertEqual(after["sourceHandle"], "fixture_0");self.assertEqual(after["currentRecipient"]["handle"], "renamed_current")
        self.assertEqual(after["currentRecipient"]["creatorId"], original["creatorId"])
        self.assertNotEqual(after["currentRecipient"]["creatorId"], replacement["creatorId"])

    def test_same_handle_without_explicit_resolution_is_not_an_identity_join(self):
        self.identities.observe_profile("it", "7490000000000000001", "fixture_0", "2026-09-12T00:00:00Z", "only-profile")
        first = self.load()["opportunities"][0]
        self.assertIsNone(first["currentRecipient"]);self.assertEqual(first["recipientStatus"], "unresolved")
        self.link(source="discovery_batch")
        self.assertIsNone(self.load()["opportunities"][0]["currentRecipient"])

    def test_handle_conflict_keeps_verified_oec_but_removes_unsafe_salutation(self):
        identity = self.link();before = self.load()["opportunities"][0]
        self.identities.observe_profile("it", identity["oecId"], "conflicting_name", "2026-09-12T00:00:00Z", "same-time-name-conflict")
        after = self.load()["opportunities"][0]
        self.assertEqual(after["recipientStatus"], "verified")
        self.assertEqual(after["currentRecipient"]["creatorId"], identity["creatorId"])
        self.assertEqual(after["currentRecipient"]["oecId"], identity["oecId"])
        self.assertIsNone(after["currentRecipient"]["handle"])
        self.assertEqual(after["id"], before["id"]);self.assertNotEqual(after["fingerprint"], before["fingerprint"])

    def test_two_explicit_resolutions_remain_ambiguous_and_are_not_autoselected(self):
        first = self.link(oec_id="7490000000000000001");second = self.link(oec_id="7490000000000000002")
        result = self.load()["opportunities"][0]
        self.assertEqual(result["recipientStatus"], "ambiguous");self.assertIsNone(result["currentRecipient"])
        self.assertEqual({row["creatorId"] for row in result["recipientCandidates"]}, {first["creatorId"], second["creatorId"]})
        self.assertEqual(self.load()["candidates"], [])

    def test_duplicate_edges_do_not_double_sales_and_different_windows_are_not_summed(self):
        self.document["batch"]["evidence"].append(deepcopy(self.document["batch"]["evidence"][0]))
        older = deepcopy(self.document["batch"]["evidence"][0]);older["id"] = "older-observation";older["units"] = 1000;older["source"]["observedAt"] -= 1000
        self.document["batch"]["evidence"].append(older);self.expected = self.write_source()
        result = self.load();self.assertEqual(result["opportunities"][0]["products"][0]["units"], 1)
        self.assertEqual(result["summary"]["uniquePairs"], 48)
        self.assertEqual(result["opportunities"][0]["products"][0]["windowBasis"], "calendar_date_unknown_timezone")
        changed = deepcopy(self.document["batch"]["evidence"][0]);changed["units"] += 2
        self.document["batch"]["evidence"].append(changed);self.expected = self.write_source()
        with self.assertRaises(SecondOutreachSourceError) as error:self.load()
        self.assertEqual(error.exception.code, "conflicting_source_evidence")

    def test_more_than_three_products_are_bounded_with_explicit_omission_count(self):
        base = self.document["batch"]["evidence"][0]
        for index, pid in enumerate(PIDS[1:], 1):
            self.document["batch"]["evidence"].append({**deepcopy(base), "id": "extra-" + pid, "pid": pid, "units": 10 + index})
        self.expected = self.write_source();result = self.load()
        opportunity = result["opportunities"][0]
        self.assertEqual(len(opportunity["products"]), 3);self.assertEqual(opportunity["omittedProducts"], 2)

    def test_missing_provenance_wrong_namespace_and_numeric_ids_are_rejected(self):
        for change in ("source", "namespace", "numeric"):
            doc = deepcopy(self.document)
            if change == "source":doc["batch"]["evidence"][0].pop("source")
            elif change == "namespace":doc["batch"]["creators"][0]["externalIdentity"]["namespace"] = "tiktok"
            else:doc["batch"]["creators"][0]["externalIdentity"]["id"] = 7000000000000000000
            with self.subTest(change=change), self.assertRaises(SecondOutreachSourceError):
                build_second_source(doc, [], source_sha256="a" * 64)

    def test_source_hash_change_requires_explicit_expected_hash_and_preserves_opportunity_id(self):
        before = self.load();self.document["batch"]["evidence"][0]["units"] += 7
        new_hash = self.write_source()
        with self.assertRaises(SecondOutreachSourceError) as error:self.load()
        self.assertEqual(error.exception.code, "source_hash_mismatch")
        self.expected = new_hash;after = self.load()
        self.assertEqual(after["opportunities"][0]["id"], before["opportunities"][0]["id"])
        self.assertNotEqual(after["sourceFingerprint"], before["sourceFingerprint"])
        self.assertNotEqual(after["opportunities"][0]["fingerprint"], before["opportunities"][0]["fingerprint"])

    def test_candidates_are_at_most_three_current_identities_not_live_eligibility(self):
        for index in range(5):self.link(index=index, oec_id=str(7490000000000000001 + index))
        result = self.load();self.assertEqual(len(result["candidates"]), 3)
        self.assertEqual(result["candidatePurpose"], "readiness_verification_only")
        self.assertTrue(all(row["currentRecipient"] and row["historicalOwnership"] == "unverified" for row in result["candidates"]))


if __name__ == "__main__":unittest.main()
