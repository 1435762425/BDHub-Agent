from copy import deepcopy
import importlib.util
from pathlib import Path
import tempfile
import unittest

from test_profile_probe_summary import report, known_report

SPEC = importlib.util.spec_from_file_location("creator_identity_import", Path(__file__).resolve().parents[1] / "scripts/import-creator-identities.py")
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


def discovery():
    value = report()
    value["requests"] = [{"targetRef": "test-ref", "stage": "find", "status": "returned", "httpStatus": 200,
                          "code": "0", "verificationRequired": False}]
    return value


class IdentityImportTests(unittest.TestCase):
    def test_discovery_then_oec_rename_keeps_identity_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temp, M.CreatorIdentityStore(Path(temp) / "identities.sqlite") as store:
            first = discovery()
            M.import_reports(store, [first])
            old = store.get_by_oec("it", "123456789")
            refresh = known_report("new.name")
            refresh["finishedAt"] = "2026-09-13T00:00:00Z"
            M.import_reports(store, [refresh])
            current = store.get_by_oec("it", "123456789")
            self.assertEqual(current["creatorId"], old["creatorId"])
            self.assertEqual(current["currentHandle"], "new.name")
            before = store.stats()
            self.assertEqual(M.import_reports(store, [first, refresh]), before)
            self.assertEqual(len(store.find_handle_candidates("it", "private_handle")), 1)

    def test_unresolved_handle_remains_lead_without_canonical_identity(self):
        with tempfile.TemporaryDirectory() as temp, M.CreatorIdentityStore(Path(temp) / "identities.sqlite") as store:
            value = discovery()
            value["targets"] = [{"targetRef": "pending", "externalId": "external-ref", "requestedHandle": "unknown.handle",
                                 "status": "unresolved", "reason": "no_exact_handle"}]
            M.import_reports(store, [value])
            self.assertIsNone(store.get_by_oec("it", "123456789"))
            self.assertEqual(store.stats()["pendingLeads"], 1)

    def test_missing_or_challenged_find_receipt_rejected_before_writes(self):
        for challenged in (True, None):
            with self.subTest(challenged=challenged), tempfile.TemporaryDirectory() as temp, M.CreatorIdentityStore(Path(temp) / "identities.sqlite") as store:
                value = discovery()
                if challenged:
                    value["requests"][0]["verificationRequired"] = True
                else:
                    value.pop("requests")
                before = store.stats()
                with self.assertRaises(ValueError):
                    M.import_reports(store, [value])
                self.assertEqual(store.stats(), before)

    def test_partial_oec_profile_does_not_erase_previous_handle(self):
        with tempfile.TemporaryDirectory() as temp, M.CreatorIdentityStore(Path(temp) / "identities.sqlite") as store:
            M.import_reports(store, [discovery()])
            previous = store.get_by_oec("it", "123456789")
            partial = known_report(None)
            partial["finishedAt"] = "2026-09-13T00:00:00Z"
            M.import_reports(store, [partial])
            current = store.get_by_oec("it", "123456789")
            self.assertEqual(current["currentHandle"], previous["currentHandle"])
            self.assertEqual(current["currentHandleVerifiedAt"], previous["currentHandleVerifiedAt"])
            self.assertNotEqual(current["lastObservedAt"], previous["lastObservedAt"])


if __name__ == "__main__":
    unittest.main()
