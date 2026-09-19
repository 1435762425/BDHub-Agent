import concurrent.futures
import importlib.util
from pathlib import Path
import sqlite3
import tempfile
import unittest
from contextlib import closing


SPEC = importlib.util.spec_from_file_location(
    "creator_identity_subject", Path(__file__).resolve().parents[1] / "scripts/lib/creator_identity.py")
identity = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(identity)

OEC = "9007199254740993123"
T1 = "2026-09-10T01:00:00Z"
T2 = "2026-09-11T01:00:00Z"
T3 = "2026-09-12T01:00:00Z"


def proof(handle="creator.old", oec_id=OEC, **changes):
    return {"market": "it", "queriedHandle": handle, "returnedHandle": handle,
            "oecId": oec_id, "httpStatus": 200, "code": 0, "verificationRequired": False, **changes}


class CreatorIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "creator-identities.sqlite"
        self.store = identity.CreatorIdentityStore(self.path)
        self.addCleanup(self.store.close)

    def observe(self, *, oec_id=OEC, handle="creator.old", at=T1, ref="profile:1", payload=None):
        return self.store.observe_profile("it", oec_id, handle, at, ref, payload)

    def lead(self, **changes):
        arguments = {"market": "it", "handle": "creator.old", "observed_at": T1,
                     "evidence_ref": "kalodata:row-1", "external_source": "kalodata", "external_id": "foreign-identity-1"}
        arguments.update(changes)
        return self.store.record_handle_lead(**arguments)

    def test_rename_retains_stable_identity_and_alias_history_across_reopen(self):
        before = self.observe()
        after = self.observe(handle="creator.new", at=T2, ref="profile:2")
        self.assertEqual(before["creatorId"], after["creatorId"])
        self.assertEqual(after["currentHandle"], "creator.new")
        self.assertEqual([row["handle"] for row in self.store.history("it", OEC)], ["creator.old", "creator.new"])
        old = self.store.find_handle_candidates("it", "creator.old")
        self.assertEqual(old[0]["creatorId"], before["creatorId"])
        self.assertFalse(old[0]["isCurrentHandle"])
        self.assertFalse(old[0]["routingAuthoritative"])
        with identity.CreatorIdentityStore(self.path) as reopened:
            self.assertEqual(reopened.get_by_oec("it", OEC), after)

    def test_reused_handle_keeps_separate_identities_and_returns_ambiguous_candidates(self):
        original = self.observe()
        renamed = self.observe(handle="creator.new", at=T2, ref="rename")
        replacement = self.observe(oec_id="123456789", at=T3, ref="new-owner")
        self.assertNotEqual(original["creatorId"], replacement["creatorId"])
        self.assertEqual(self.store.get_by_oec("it", OEC), renamed)
        candidates = self.store.find_handle_candidates("it", "@CREATOR.OLD")
        self.assertEqual(len(candidates), 2)
        self.assertEqual({row["oecId"] for row in candidates}, {OEC, "123456789"})
        self.assertFalse(any(row["routingAuthoritative"] for row in candidates))
        self.assertEqual(len(self.store.history("it", "123456789")), 1)

    def test_out_of_order_snapshot_does_not_roll_back_name_or_last_observed(self):
        newest = self.observe(handle="creator.new", at=T3, ref="fresh")
        late = self.observe(handle="creator.old", at=T1, ref="late-snapshot")
        self.assertEqual(newest, late)
        self.assertEqual([row["handle"] for row in self.store.history("it", OEC)], ["creator.old", "creator.new"])
        self.assertEqual(self.store.find_handle_candidates("it", "creator.old")[0]["currentHandle"], "creator.new")

    def test_partial_profile_retains_old_handle_without_claiming_new_handle_verification(self):
        before = self.observe()
        partial = self.observe(handle=None, at=T3, ref="partial", payload={"followers": 900})
        self.assertEqual(partial["creatorId"], before["creatorId"])
        self.assertEqual(partial["currentHandle"], before["currentHandle"])
        self.assertEqual(partial["currentHandleVerifiedAt"], before["currentHandleVerifiedAt"])
        self.assertEqual(partial["lastObservedAt"], "2026-09-12T01:00:00.000000Z")
        no_name = self.observe(oec_id="100", handle=None, at=T2, ref="new-partial")
        self.assertIsNone(no_name["currentHandle"])
        self.assertIsNone(no_name["currentHandleVerifiedAt"])

    def test_replay_is_idempotent_and_conflicting_payload_rolls_back(self):
        original = self.observe(payload={"b": 2, "a": 1})
        replay = self.observe(at="2026-09-10T09:00:00+08:00", payload={"a": 1, "b": 2})
        self.assertEqual(original, replay)
        self.assertEqual(len(self.store.history("it", OEC)), 1)
        for changes in ({"payload": {"a": 2}}, {"handle": "another.name"}):
            with self.subTest(changes=changes), self.assertRaises(identity.IdentityConflict):
                self.observe(**changes)
        self.assertEqual(self.store.get_by_oec("it", OEC), original)
        self.assertEqual(len(self.store.history("it", OEC)), 1)
        self.observe(ref="independent-source", payload={"b": 2, "a": 1})
        self.assertEqual(len(self.store.history("it", OEC)), 2)

    def test_equal_timestamp_name_disagreement_is_visible_until_newer_observation(self):
        first = self.observe()
        conflict = self.observe(handle="another.name", ref="independent-same-time")
        self.assertEqual(conflict["currentHandle"], first["currentHandle"])
        self.assertTrue(conflict["handleConflict"])
        confirmed = self.observe(handle="another.name", at=T2, ref="later")
        self.assertEqual(confirmed["currentHandle"], "another.name")
        self.assertFalse(confirmed["handleConflict"])

    def test_unknown_failure_does_not_delete_refresh_or_create_identity(self):
        before = self.observe()
        failure = self.store.record_profile_failure("it", OEC, T3, "failed-profile", "unknown")
        self.assertEqual(failure, before)
        self.assertEqual(self.store.get_by_oec("it", OEC), before)
        self.store.record_profile_failure("it", OEC, T3, "failed-profile", "unknown")
        self.assertEqual(len(self.store.history("it", OEC)), 2)
        self.assertIsNone(self.store.record_profile_failure("it", "999", T3, "failed-missing", "not_found"))
        self.assertIsNone(self.store.get_by_oec("it", "999"))
        self.assertEqual(self.store.history("it", "999")[0]["kind"], "failure")
        with self.assertRaises(identity.IdentityConflict):
            self.store.record_profile_failure("it", OEC, T3, "failed-profile", "timeout")

    def test_oec_requires_exact_string_and_timestamp_requires_timezone(self):
        for value in (9007199254740993123, 9007199254740993123.0, True, None, "", "1e18", "１２３", " 123", "+123"):
            with self.subTest(value_type=type(value).__name__), self.assertRaises(ValueError):
                self.observe(oec_id=value)
        for value in ("2026-09-10", "2026-09-10T01:00:00", 123, None):
            with self.subTest(timestamp=value), self.assertRaises(ValueError):
                self.observe(at=value)
        self.assertEqual(self.observe()["oecId"], OEC)

    def test_pending_lead_never_enters_canonical_identity_or_alias_queries(self):
        pending = self.lead(handle=" @Creator.Old ")
        replay = self.lead()
        self.assertEqual(pending, replay)
        self.assertEqual(pending["status"], "pending")
        self.assertIsNone(pending["creatorId"])
        self.assertFalse(pending["historicalCrossSourceIdentityProven"])
        self.assertEqual(self.store.find_handle_candidates("it", "creator.old"), [])
        self.assertIsNone(self.store.get_by_oec("it", OEC))
        with closing(sqlite3.connect(self.path)) as database:
            self.assertEqual(database.execute("SELECT COUNT(*) FROM creator_identity").fetchone()[0], 0)
        with self.assertRaises(identity.IdentityConflict):
            self.lead(external_id="different-external-id")

    def test_exact_find_resolves_current_lead_without_proving_external_history(self):
        pending = self.lead()
        resolved = self.store.resolve_handle_lead(pending["leadId"], OEC, T2, "find:1", exact_find=proof())
        self.assertEqual(resolved["lead"]["status"], "resolved")
        self.assertEqual(resolved["lead"]["creatorId"], resolved["identity"]["creatorId"])
        self.assertFalse(resolved["lead"]["historicalCrossSourceIdentityProven"])
        self.assertEqual(resolved["lead"]["externalId"], "foreign-identity-1")
        self.assertEqual(self.store.resolve_handle_lead(pending["leadId"], OEC, T2, "find:1", exact_find=proof()), resolved)
        self.assertEqual(len(self.store.history("it", OEC)), 1)
        renamed = self.observe(handle="creator.new", at=T3, ref="later-profile")
        self.assertEqual(renamed["creatorId"], resolved["lead"]["creatorId"])
        replay = self.store.resolve_handle_lead(pending["leadId"], OEC, T2, "find:1", exact_find=proof())
        self.assertEqual(replay["identity"]["currentHandle"], "creator.new")

    def test_lead_resolution_rejects_inexact_unverified_wrong_market_or_old_find(self):
        pending = self.lead()
        invalid_proofs = [
            {}, proof(returnedHandle="similar.creator"), proof(queriedHandle="other.query"),
            proof(market="mx"), proof(verificationRequired=True), proof(code=False),
            proof(code="0"), proof(httpStatus=403), proof(oecId=123), proof(oecId="999"),
        ]
        for invalid in invalid_proofs:
            with self.subTest(evidence=invalid), self.assertRaises(ValueError):
                self.store.resolve_handle_lead(pending["leadId"], OEC, T2, "find-invalid", exact_find=invalid)
        with self.assertRaises(ValueError):
            self.store.resolve_handle_lead(pending["leadId"], OEC, "2026-09-09T01:00:00Z", "find-old", exact_find=proof())
        self.assertEqual(self.store.get_handle_lead(pending["leadId"])["status"], "pending")
        self.assertIsNone(self.store.get_by_oec("it", OEC))

    def test_resolved_lead_cannot_migrate_to_new_handle_owner(self):
        pending = self.lead()
        original = self.store.resolve_handle_lead(pending["leadId"], OEC, T2, "find:1", exact_find=proof())
        with self.assertRaises(identity.IdentityConflict):
            self.store.resolve_handle_lead(pending["leadId"], "123", T3, "find:2", exact_find=proof(oec_id="123"))
        self.assertIsNone(self.store.get_by_oec("it", "123"))
        self.assertEqual(self.store.get_handle_lead(pending["leadId"])["creatorId"], original["identity"]["creatorId"])
        new_lead = self.lead(observed_at=T3, evidence_ref="new-current-lead")
        replacement = self.store.resolve_handle_lead(new_lead["leadId"], "123", T3, "find:2", exact_find=proof(oec_id="123"))
        self.assertNotEqual(replacement["identity"]["creatorId"], original["identity"]["creatorId"])
        self.assertEqual(len(self.store.find_handle_candidates("it", "creator.old")), 2)

    def test_resolution_transaction_rolls_back_if_profile_evidence_conflicts(self):
        before = self.observe(at=T2, ref="same-find", payload={"original": True})
        pending = self.lead()
        with self.assertRaises(identity.IdentityConflict):
            self.store.resolve_handle_lead(pending["leadId"], OEC, T2, "same-find", exact_find=proof())
        self.assertEqual(self.store.get_handle_lead(pending["leadId"])["status"], "pending")
        self.assertEqual(self.store.get_by_oec("it", OEC), before)
        self.assertEqual(len(self.store.history("it", OEC)), 1)

    def test_concurrent_connections_create_one_stable_identity(self):
        def observe_from_connection(index):
            with identity.CreatorIdentityStore(self.path) as store:
                return store.observe_profile("it", OEC, "creator.old", T1, f"parallel:{index}")["creatorId"]
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            ids = list(executor.map(observe_from_connection, range(16)))
        self.assertEqual(len(set(ids)), 1)
        self.assertEqual(len(self.store.history("it", OEC)), 16)
        with closing(sqlite3.connect(self.path)) as database:
            self.assertEqual(database.execute("SELECT COUNT(*) FROM creator_identity").fetchone()[0], 1)

    def test_concurrent_identical_event_is_persisted_once(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            identities = list(executor.map(lambda _: self.observe(), range(16)))
        self.assertEqual(len({row["creatorId"] for row in identities}), 1)
        self.assertEqual(self.store.stats()["observations"], 1)

    def test_stats_distinguishes_pending_leads_from_canonical_and_replay_is_stable(self):
        self.assertEqual(self.store.stats(), {"identities": 0, "observations": 0, "leads": 0, "pendingLeads": 0, "resolvedLeads": 0})
        pending = self.lead()
        self.assertEqual(self.store.stats(), {"identities": 0, "observations": 0, "leads": 1, "pendingLeads": 1, "resolvedLeads": 0})
        self.store.resolve_handle_lead(pending["leadId"], OEC, T2, "find:1", exact_find=proof())
        expected = {"identities": 1, "observations": 1, "leads": 1, "pendingLeads": 0, "resolvedLeads": 1}
        self.assertEqual(self.store.stats(), expected)
        self.lead()
        self.store.resolve_handle_lead(pending["leadId"], OEC, T2, "find:1", exact_find=proof())
        self.assertEqual(self.store.stats(), expected)

    def test_same_oec_in_another_market_is_a_separate_identity(self):
        italy = self.observe()
        mexico = self.store.observe_profile("mx", OEC, "creator.old", T1, "mx-source")
        self.assertNotEqual(italy["creatorId"], mexico["creatorId"])
        self.assertEqual(len(self.store.find_handle_candidates("it", "creator.old")), 1)

    def test_refresh_target_routes_by_same_oec_after_rename_and_handle_reuse(self):
        original = self.observe()
        old_target = self.store.refresh_target("it", OEC, "refresh:old")
        self.assertEqual(old_target, {"ref": "refresh:old", "oecId": OEC,
                                      "externalId": original["creatorId"], "handle": "creator.old"})
        self.observe(handle="creator.new", at=T2, ref="rename")
        self.observe(oec_id="999", at=T3, ref="handle-new-owner")
        # Alias discovery must never participate in generation of a route.
        self.store.find_handle_candidates = lambda *_args: self.fail("refresh must not search handles")
        new_target = self.store.refresh_target("it", OEC, "refresh:new")
        self.assertEqual(new_target["handle"], "creator.new")
        self.assertEqual(new_target["oecId"], old_target["oecId"])
        self.assertEqual(new_target["externalId"], old_target["externalId"])
        self.assertEqual(self.store.refresh_target("it", "999", "refresh:replacement")["oecId"], "999")

    def test_refresh_target_requires_canonical_and_does_not_invent_missing_handle(self):
        self.lead()
        with self.assertRaises(LookupError):
            self.store.refresh_target("it", OEC, "pending-is-not-canonical")
        partial = self.observe(handle=None)
        target = self.store.refresh_target("it", OEC, "refresh:partial")
        self.assertEqual(target, {"ref": "refresh:partial", "oecId": OEC, "externalId": partial["creatorId"]})
        with self.assertRaises(LookupError):
            self.store.refresh_target("mx", OEC, "wrong-market")

    def test_observation_sql_is_append_only_and_unrelated_database_is_rejected(self):
        self.observe()
        with closing(sqlite3.connect(self.path)) as database,database:
            for sql in ("UPDATE identity_observation SET handle='wrong'", "DELETE FROM identity_observation"):
                with self.subTest(sql=sql), self.assertRaises(sqlite3.IntegrityError):
                    database.execute(sql)
        unrelated = self.path.parent / "other.sqlite"
        with closing(sqlite3.connect(unrelated)) as database,database:
            database.execute("CREATE TABLE unrelated(value TEXT)")
        before = unrelated.read_bytes()
        with self.assertRaisesRegex(ValueError, "not a creator identity store"):
            identity.CreatorIdentityStore(unrelated)
        self.assertEqual(unrelated.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
