"""Delivery-state tests with local FakeExternal only; all approvals use temp DBs."""
import hashlib
import json
import sqlite3
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from lib.second_live_trial import LiveTrialError, LiveTrialStore, EXPIRY_SECONDS


class Clock:
    def __init__(self):
        self.value = 1_800_000_000.0

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


def source_item(number=1, **patch):
    value = {"opportunityId": f"second-it-{number}", "creatorId": "creator_" + f"{number:032x}",
             "oecId": str(9_007_199_254_740_993_001 + number), "handle": f"creator.{number}",
             "draftId": "template-draft_" + "b" * 64, "textIt": f"Ciao! Proposta {number}.\nTi interessa?",
             "translationZh": f"你好！第 {number} 份合作想法，有兴趣吗？", "contextFingerprint": "c" * 64}
    value.update(patch)
    return value


class FakeExternal:
    def __init__(self):
        self.conversations = {}
        self.messages = {}
        self.create_calls = 0
        self.send_calls = 0

    def create_conversation(self, oec_id):
        self.create_calls += 1
        cid = f"cid-{oec_id}"
        self.conversations[cid] = oec_id
        return cid

    def send(self, cid, request_ref, text):
        self.send_calls += 1
        message_id = f"message-{self.send_calls}"
        self.messages[request_ref] = {"conversationId": cid, "messageId": message_id, "text": text}
        return {"conversationId": cid, "requestRef": request_ref, "messageId": message_id,
                "accepted": True, "evidenceRef": f"fake-response:{message_id}"}

    def read_conversation(self, cid):
        return {"kind": "conversation_confirmed", "conversationId": cid,
                "recipientOecId": self.conversations[cid], "evidenceRef": f"fake-read:{cid}"}

    def read_message(self, request_ref):
        message = self.messages[request_ref]
        return {"kind": "message_confirmed", "conversationId": message["conversationId"],
                "recipientOecId": self.conversations[message["conversationId"]], "requestRef": request_ref,
                "messageId": message["messageId"], "textSha256": hashlib.sha256(message["text"].encode()).hexdigest(),
                "evidenceRef": f"fake-read:{message['messageId']}"}


class LiveTrialTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.clock = Clock()
        self.store = LiveTrialStore(self.tmp.name, now=self.clock)
        self.external = FakeExternal()

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def freeze(self, request="trial-1", count=2, **item_patch):
        return self.store.create_trial(request, [source_item(i, **item_patch) for i in range(1, count + 1)], "a" * 64)

    def approve(self, trial):
        return self.store.approve(trial["trialId"], trial["snapshotHash"])

    def claim(self, trial, owner="runner", seconds=90):
        return self.store.claim(trial["trialId"], owner, seconds)

    def reopen(self):
        self.store.close()
        self.store = LiveTrialStore(self.tmp.name, now=self.clock)

    def begin(self, lease, stage, ref):
        return self.store.begin_attempt(lease["itemId"], lease["owner"], lease["fence"], stage, ref)

    def conversation(self, lease, ref="create-1"):
        attempt = self.begin(lease, "create_conversation", ref)
        self.assertTrue(attempt["dispatchAllowed"])
        with LiveTrialStore(self.tmp.name, now=self.clock) as observer:
            before = observer.get_trial(lease["trialId"])["items"][0]
            self.assertEqual(before["state"], "creating_conversation")
            self.assertEqual(before["attempts"][0]["state"], "inflight")
        cid = self.external.create_conversation(lease["item"]["oecId"])
        current = self.store.record_conversation(lease["itemId"], lease["owner"], lease["fence"], attempt["attemptId"], cid, "fake-response:conversation")
        self.assertEqual(current["state"], "conversation_ready")
        return attempt, cid

    def send(self, lease, cid, ref="send-1"):
        attempt = self.begin(lease, "send_message", ref)
        self.assertTrue(attempt["dispatchAllowed"])
        with LiveTrialStore(self.tmp.name, now=self.clock) as observer:
            before = observer.get_trial(lease["trialId"])["items"][0]
            self.assertEqual(before["state"], "submitting")
            self.assertEqual(next(a for a in before["attempts"] if a["stage"] == "send_message")["state"], "inflight")
        receipt = self.external.send(cid, ref, lease["item"]["textIt"])
        return attempt, receipt

    def record_receipt(self, lease, attempt, receipt):
        return self.store.record_send_receipt(lease["itemId"], lease["owner"], lease["fence"], attempt["attemptId"], receipt)

    def confirm_message(self, lease, ref="send-1"):
        return self.store.confirm(lease["itemId"], lease["owner"], lease["fence"], self.external.read_message(ref))

    def assert_error(self, code, call):
        with self.assertRaises(LiveTrialError) as caught:
            call()
        self.assertEqual(caught.exception.code, code)

    def test_frozen_snapshot_covers_all_content_expiry_identity_and_template_version(self):
        item = source_item(textIt="Ciao!\nUn'idea ☀️", handle=None)
        trial = self.store.create_trial("freeze", [item], "a" * 64)
        snapshot = trial["snapshot"]
        self.assertEqual(snapshot["account"], "acc6")
        self.assertEqual(snapshot["campaignId"], "italy-second-pilot")
        self.assertEqual(snapshot["market"], "it")
        self.assertEqual(snapshot["items"][0]["draftId"], item["draftId"])
        self.assertEqual(snapshot["items"][0]["oecId"], item["oecId"])
        encoded = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        self.assertEqual(trial["snapshotHash"], hashlib.sha256(encoded).hexdigest())
        self.assertFalse(trial["approved"])
        self.assertFalse(trial["expired"])
        item["textIt"] = "changed caller object"
        self.assertEqual(self.store.get_trial(trial["trialId"])["snapshot"]["items"][0]["textIt"], "Ciao!\nUn'idea ☀️")
        self.clock.advance(EXPIRY_SECONDS)
        self.assertTrue(self.store.get_trial(trial["trialId"])["expired"])
        self.assertFalse(list(Path(self.tmp.name).glob("*policy*")))

    def test_same_request_returns_original_expired_snapshot_and_changes_conflict(self):
        trial = self.freeze()
        self.clock.advance(EXPIRY_SECONDS + 1)
        again = self.freeze()
        self.assertEqual(again["trialId"], trial["trialId"])
        self.assertEqual(again["snapshotHash"], trial["snapshotHash"])
        self.assertEqual(again["expiresAt"], trial["expiresAt"])
        for changed in (source_item(textIt="different"), source_item(translationZh="不同"), source_item(draftId="template-draft_" + "d" * 64), source_item(contextFingerprint="d" * 64)):
            self.assert_error("request_conflict", lambda: self.store.create_trial("trial-1", [changed, source_item(2)], "a" * 64))
        self.assert_error("request_conflict", lambda: self.store.create_trial("trial-1", [source_item(1), source_item(2)], "d" * 64))

    def test_item_bounds_canonical_ids_and_unknown_fields_are_rejected(self):
        for invalid in ([], [source_item(i) for i in range(1, 5)]):
            self.assert_error("invalid_input", lambda: self.store.create_trial("bad", invalid, "a" * 64))
        for patch in ({"oecId": 9_007_199_254_740_993_002}, {"oecId": "00123"}, {"creatorId": "kalodata-123"}, {"textIt": " "}, {"account": "other"}):
            self.assert_error("invalid_input", lambda: self.store.create_trial("bad", [source_item(**patch)], "a" * 64))
        self.assert_error("duplicate_recipient", lambda: self.store.create_trial("bad", [source_item(), source_item(2, oecId=source_item()["oecId"])], "a" * 64))

    def test_snapshot_and_item_content_are_database_immutable(self):
        trial = self.freeze(count=1)
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.db.execute("UPDATE live_trial SET snapshot_json='{}' WHERE id=?", (trial["trialId"],))
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.db.execute("UPDATE live_trial_item SET item_json='{}' WHERE id=?", (trial["items"][0]["itemId"],))
        self.assertEqual(self.store.get_trial(trial["trialId"])["snapshotHash"], trial["snapshotHash"])

    def test_approval_is_explicit_hash_bound_and_begin_cannot_bypass_it(self):
        trial = self.freeze()
        self.assertIsNone(self.claim(trial))
        item = trial["items"][0]["itemId"]
        self.assert_error("approval_required", lambda: self.store.begin_attempt(item, "forged-worker", 1, "create_conversation", "not-approved"))
        self.assert_error("snapshot_mismatch", lambda: self.store.approve(trial["trialId"], "f" * 64))
        self.assertFalse(self.store.get_trial(trial["trialId"])["approved"])
        approved = self.approve(trial)
        self.assertTrue(approved["approved"])
        self.assertEqual(self.approve(trial)["approvedAt"], approved["approvedAt"])

    def test_expired_unapproved_trial_cannot_be_approved(self):
        trial = self.freeze()
        self.clock.advance(EXPIRY_SECONDS)
        self.assert_error("trial_expired", lambda: self.approve(trial))
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM live_campaign_recipient").fetchone()[0], 0)

    def test_happy_path_stages_and_candidate_does_not_mean_confirmed(self):
        trial = self.approve(self.freeze())
        lease = self.claim(trial)
        create, cid = self.conversation(lease)
        send, receipt = self.send(lease, cid)
        candidate = self.record_receipt(lease, send, receipt)
        self.assertEqual(candidate["state"], "accepted_candidate")
        self.assertIsNone(candidate["confirmation"])
        self.assertIsNone(self.claim(trial, "other-runner"))
        confirmed = self.confirm_message(lease)
        self.assertEqual(confirmed["state"], "confirmed")
        next_lease = self.claim(trial, "other-runner")
        self.assertEqual(next_lease["itemId"], trial["items"][1]["itemId"])
        self.assertEqual((self.external.create_calls, self.external.send_calls), (1, 1))

    def test_stage_begin_and_receipt_replays_never_authorize_duplicate_dispatch(self):
        trial = self.approve(self.freeze(count=1))
        lease = self.claim(trial)
        attempt = self.begin(lease, "create_conversation", "one-create")
        again = self.begin(lease, "create_conversation", "one-create")
        self.assertTrue(attempt["dispatchAllowed"])
        self.assertFalse(again["dispatchAllowed"])
        self.assertEqual(again["attemptId"], attempt["attemptId"])
        self.assert_error("request_conflict", lambda: self.begin(lease, "create_conversation", "different-create"))
        cid = self.external.create_conversation(lease["item"]["oecId"])
        for _ in range(2):
            self.store.record_conversation(lease["itemId"], lease["owner"], lease["fence"], attempt["attemptId"], cid, "same-proof")
        send, receipt = self.send(lease, cid)
        first = self.record_receipt(lease, send, receipt)
        event_count = len(self.store.get_trial(trial["trialId"])["events"])
        replay = self.record_receipt(lease, send, receipt)
        self.assertEqual(first, replay)
        self.assertEqual(len(self.store.get_trial(trial["trialId"])["events"]), event_count)
        self.assertFalse(self.begin(lease, "send_message", "send-1")["dispatchAllowed"])
        self.assertEqual(self.external.send_calls, 1)

    def test_receipts_and_readback_must_match_original_cid_nonce_recipient_and_text(self):
        trial = self.approve(self.freeze(count=1))
        lease = self.claim(trial)
        create, cid = self.conversation(lease)
        send, receipt = self.send(lease, cid)
        for patch in ({"conversationId": "wrong-cid"}, {"requestRef": "wrong-nonce"}):
            self.assert_error("receipt_mismatch", lambda: self.record_receipt(lease, send, {**receipt, **patch}))
        self.assert_error("invalid_receipt", lambda: self.record_receipt(lease, send, {**receipt, "accepted": False}))
        self.record_receipt(lease, send, receipt)
        evidence = self.external.read_message("send-1")
        for patch in ({"conversationId": "wrong-cid"}, {"recipientOecId": source_item(2)["oecId"]}, {"requestRef": "wrong"}, {"messageId": "wrong-message"}, {"textSha256": "0" * 64}):
            self.assert_error("evidence_mismatch", lambda: self.store.confirm(lease["itemId"], lease["owner"], lease["fence"], {**evidence, **patch}))
        self.assertEqual(self.store.get_trial(trial["trialId"])["items"][0]["state"], "accepted_candidate")
        self.assert_error("attempt_mismatch", lambda: self.store.error(lease["itemId"], lease["owner"], lease["fence"], create["attemptId"], "wrong_stage"))
        self.confirm_message(lease)

    def test_positive_delivery_cannot_be_bypassed_with_new_template_trial_or_handle(self):
        trial = self.approve(self.freeze(count=1))
        lease = self.claim(trial)
        _, cid = self.conversation(lease)
        send, receipt = self.send(lease, cid)
        self.record_receipt(lease, send, receipt)
        self.confirm_message(lease)
        another = self.store.create_trial("changed-template", [source_item(handle="renamed.creator", draftId="template-draft_" + "f" * 64)], "d" * 64)
        self.assert_error("campaign_recipient_conflict", lambda: self.approve(another))
        self.assertEqual(self.external.send_calls, 1)

    def test_pause_cancels_not_started_items_without_approval_or_network(self):
        trial = self.freeze()
        paused = self.store.pause(trial["trialId"], trial["snapshotHash"])
        self.assertTrue(paused["paused"])
        self.assertTrue(all(item["state"] == "failed_not_sent" and item["errorCode"] == "paused_before_send" for item in paused["items"]))
        self.assert_error("trial_paused", lambda: self.approve(trial))
        self.assertIsNone(self.claim(trial))
        self.assertEqual((self.external.create_calls, self.external.send_calls), (0, 0))

    def test_pause_known_conversation_cancels_send_but_preserves_cid(self):
        trial = self.approve(self.freeze())
        lease = self.claim(trial)
        _, cid = self.conversation(lease)
        paused = self.store.pause(trial["trialId"], trial["snapshotHash"])
        self.assertEqual(paused["items"][0]["conversationId"], cid)
        self.assertEqual(paused["items"][0]["state"], "failed_not_sent")
        self.assert_error("trial_paused", lambda: self.begin(lease, "send_message", "no-send"))
        self.assertTrue(self.approve(self.freeze("new-review", count=1))["approved"])
        self.assertEqual(self.external.send_calls, 0)

    def test_pause_during_create_preserves_inflight_then_cancels_unsent_message(self):
        trial = self.approve(self.freeze())
        lease = self.claim(trial)
        attempt = self.begin(lease, "create_conversation", "creating")
        cid = self.external.create_conversation(lease["item"]["oecId"])
        paused = self.store.pause(trial["trialId"], trial["snapshotHash"])
        self.assertEqual(paused["items"][0]["state"], "creating_conversation")
        done = self.store.record_conversation(lease["itemId"], lease["owner"], lease["fence"], attempt["attemptId"], cid, "late-create-result")
        self.assertEqual(done["conversationId"], cid)
        self.assertEqual(done["state"], "failed_not_sent")
        self.assertTrue(self.store.get_trial(trial["trialId"])["complete"])
        self.assertEqual(self.external.send_calls, 0)

    def test_pause_during_send_does_not_claim_retraction_and_readback_can_complete(self):
        trial = self.approve(self.freeze())
        lease = self.claim(trial)
        _, cid = self.conversation(lease)
        send, receipt = self.send(lease, cid)
        paused = self.store.pause(trial["trialId"], trial["snapshotHash"])
        self.assertEqual([item["state"] for item in paused["items"]], ["submitting", "failed_not_sent"])
        self.record_receipt(lease, send, receipt)
        self.assertEqual(self.confirm_message(lease)["state"], "confirmed")
        self.assertEqual(self.store.get_trial(trial["trialId"])["snapshotHash"], trial["snapshotHash"])
        self.assertEqual(self.external.send_calls, 1)

    def test_begin_enforces_expiry_even_with_live_lease_but_late_receipt_is_recordable(self):
        trial = self.approve(self.freeze(count=1))
        self.clock.advance(EXPIRY_SECONDS - 1)
        lease = self.claim(trial, seconds=60)
        create = self.begin(lease, "create_conversation", "near-expiry")
        cid = self.external.create_conversation(lease["item"]["oecId"])
        self.clock.advance(2)
        self.store.record_conversation(lease["itemId"], lease["owner"], lease["fence"], create["attemptId"], cid, "late-known-cid")
        self.assert_error("trial_expired", lambda: self.begin(lease, "send_message", "expired-send"))
        self.assertIsNone(self.claim(trial))
        self.assertEqual(self.external.send_calls, 0)

    def test_late_send_candidate_and_confirmation_after_expiry_preserve_actual_evidence(self):
        trial = self.approve(self.freeze(count=1))
        self.clock.advance(EXPIRY_SECONDS - 1)
        lease = self.claim(trial, seconds=60)
        _, cid = self.conversation(lease)
        send, receipt = self.send(lease, cid)
        self.clock.advance(2)
        self.record_receipt(lease, send, receipt)
        self.assertEqual(self.confirm_message(lease)["state"], "confirmed")
        self.assertTrue(self.store.get_trial(trial["trialId"])["expired"])

    def test_lost_create_receipt_on_reopen_becomes_unknown_and_requires_positive_readback(self):
        trial = self.approve(self.freeze())
        old = self.claim(trial, "old", 1)
        attempt = self.begin(old, "create_conversation", "lost-create")
        cid = self.external.create_conversation(old["item"]["oecId"])
        self.clock.advance(2)
        self.reopen()
        recovered = self.claim(trial, "new")
        self.assertTrue(recovered["readOnly"])
        self.assertEqual(recovered["nextAction"], "verify_conversation")
        self.assertEqual(recovered["item"]["state"], "result_unknown")
        self.assertEqual(recovered["item"]["attempts"][0]["state"], "result_unknown")
        self.assert_error("stale_lease", lambda: self.store.record_conversation(old["itemId"], old["owner"], old["fence"], attempt["attemptId"], cid, "late-old-worker"))
        self.assert_error("campaign_result_unknown", lambda: self.begin(recovered, "create_conversation", "lost-create"))
        another = self.store.create_trial("other-recipient", [source_item(3)], "a" * 64)
        self.approve(another)
        self.assertIsNone(self.claim(another))
        same = self.freeze("same-oec-new-trial", count=1)
        self.assert_error("campaign_recipient_conflict", lambda: self.approve(same))
        ready = self.store.confirm(recovered["itemId"], recovered["owner"], recovered["fence"], self.external.read_conversation(cid))
        self.assertEqual(ready["state"], "conversation_ready")
        self.assertFalse(self.store.get_trial(trial["trialId"])["blockedByUnknown"])
        self.assertEqual(self.external.create_calls, 1)

    def test_reopened_known_cid_is_not_recreated_or_sent_before_readback(self):
        trial = self.approve(self.freeze(count=1))
        lease = self.claim(trial, seconds=1)
        _, cid = self.conversation(lease)
        self.clock.advance(2)
        self.reopen()
        recovered = self.claim(trial, "new")
        self.assertEqual(recovered["item"]["conversationId"], cid)
        self.assertEqual(recovered["nextAction"], "verify_conversation")
        self.assert_error("readback_required", lambda: self.begin(recovered, "send_message", "send-after-reopen"))
        self.store.confirm(recovered["itemId"], recovered["owner"], recovered["fence"], self.external.read_conversation(cid))
        send, receipt = self.send(recovered, cid, "send-after-reopen")
        self.record_receipt(recovered, send, receipt)
        self.confirm_message(recovered, "send-after-reopen")
        self.assertEqual((self.external.create_calls, self.external.send_calls), (1, 1))

    def test_unverified_create_candidate_survives_restart_and_requires_readback_before_send(self):
        trial = self.approve(self.freeze(count=1));old = self.claim(trial, "old", 1)
        attempt = self.begin(old, "create_conversation", "candidate-create")
        cid = self.external.create_conversation(old["item"]["oecId"])
        candidate = self.store.record_conversation(old["itemId"], old["owner"], old["fence"], attempt["attemptId"], cid, "create-response", verified=False)
        self.assertEqual(candidate["conversationId"], cid);self.assertTrue(candidate["requiresReconciliation"])
        self.assert_error("readback_required", lambda: self.begin(old, "send_message", "too-early"))
        self.clock.advance(2);self.reopen();current = self.claim(trial, "reader")
        self.assertEqual(current["nextAction"], "verify_conversation");self.assertTrue(current["readOnly"])
        self.assertEqual(current["item"]["conversationId"], cid)
        unknown = self.store.mark_verification_unresolved(current["itemId"], current["owner"], current["fence"], "conversation_read_unresolved")
        self.assertEqual(unknown["state"], "result_unknown");self.assertEqual(unknown["conversationId"], cid)
        self.assertEqual(unknown["attempts"][0]["fence"], old["fence"])
        self.assertFalse(unknown["attempts"][0]["result"]["verified"])
        self.assert_error("campaign_result_unknown", lambda: self.begin(current, "send_message", "blocked"))
        ready = self.store.confirm(current["itemId"], current["owner"], current["fence"], self.external.read_conversation(cid))
        self.assertEqual(ready["state"], "conversation_ready");self.assertFalse(ready["requiresReconciliation"])
        self.assertEqual((self.external.create_calls, self.external.send_calls), (1, 0))

    def test_paused_unverified_create_retains_candidate_until_positive_readback(self):
        trial = self.approve(self.freeze(count=1));lease = self.claim(trial)
        attempt = self.begin(lease, "create_conversation", "candidate-create")
        cid = self.external.create_conversation(lease["item"]["oecId"])
        self.store.record_conversation(lease["itemId"], lease["owner"], lease["fence"], attempt["attemptId"], cid, "response", verified=False)
        paused = self.store.pause(trial["trialId"], trial["snapshotHash"])
        self.assertEqual(paused["items"][0]["state"], "conversation_ready")
        self.assertTrue(paused["items"][0]["requiresReconciliation"])
        self.assertEqual(self.claim(trial)["nextAction"], "verify_conversation")
        self.store.mark_verification_unresolved(lease["itemId"], lease["owner"], lease["fence"], "read_failed")
        self.assert_error("campaign_recipient_conflict", lambda: self.approve(self.freeze("replacement", count=1)))
        result = self.store.confirm(lease["itemId"], lease["owner"], lease["fence"], self.external.read_conversation(cid))
        self.assertEqual(result["state"], "failed_not_sent");self.assertEqual(self.external.send_calls, 0)

    def test_current_reader_can_mark_old_fence_text_candidate_unresolved_without_losing_receipt(self):
        trial = self.approve(self.freeze(count=1));old = self.claim(trial, "writer", 1)
        _, cid = self.conversation(old);attempt, receipt = self.send(old, cid)
        self.record_receipt(old, attempt, receipt);self.clock.advance(2);self.reopen()
        current = self.claim(trial, "reader")
        unknown = self.store.mark_verification_unresolved(current["itemId"], current["owner"], current["fence"], "message_not_found")
        self.assertEqual(unknown["state"], "result_unknown");self.assertEqual(unknown["sendReceipt"], receipt)
        self.assertEqual(unknown["attempts"][-1]["fence"], old["fence"])
        event_count = len(self.store.get_trial(trial["trialId"])["events"])
        self.store.mark_verification_unresolved(current["itemId"], current["owner"], current["fence"], "message_not_found")
        self.assertEqual(len(self.store.get_trial(trial["trialId"])["events"]), event_count)
        self.assert_error("stale_lease", lambda: self.store.mark_verification_unresolved(old["itemId"], old["owner"], old["fence"], "late_reader"))
        self.assertEqual(self.confirm_message(current)["state"], "confirmed")
        self.assertEqual(self.external.send_calls, 1)

    def test_verification_uncertainty_requires_current_lease_and_existing_verification_work(self):
        trial = self.approve(self.freeze(count=1));lease = self.claim(trial)
        self.assert_error("verification_not_pending", lambda: self.store.mark_verification_unresolved(lease["itemId"], lease["owner"], lease["fence"], "no_prior_attempt"))
        self.assert_error("invalid_input", lambda: self.store.mark_verification_unresolved(lease["itemId"], lease["owner"], lease["fence"], "unsafe detail"))

    def test_lost_send_receipt_on_reopen_never_resends_or_releases_guard(self):
        trial = self.approve(self.freeze())
        old = self.claim(trial, "old", 1)
        _, cid = self.conversation(old)
        attempt, receipt = self.send(old, cid)
        self.clock.advance(2)
        self.reopen()
        recovered = self.claim(trial, "new")
        self.assertEqual(recovered["item"]["state"], "result_unknown")
        self.assertEqual(recovered["nextAction"], "verify_message")
        self.assertIsNone(recovered["item"]["sendReceipt"])
        self.assert_error("campaign_result_unknown", lambda: self.begin(recovered, "send_message", "send-1"))
        self.assert_error("stale_lease", lambda: self.record_receipt(old, attempt, receipt))
        self.assert_error("campaign_recipient_conflict", lambda: self.approve(self.freeze("different-trial", count=1)))
        self.assertEqual(self.confirm_message(recovered)["state"], "confirmed")
        self.assertEqual(self.external.send_calls, 1)
        self.assertEqual(self.claim(trial)["itemId"], trial["items"][1]["itemId"])

    def test_reopened_candidate_retains_receipt_and_only_requires_message_readback(self):
        trial = self.approve(self.freeze(count=1))
        lease = self.claim(trial, seconds=1)
        _, cid = self.conversation(lease)
        attempt, receipt = self.send(lease, cid)
        self.record_receipt(lease, attempt, receipt)
        self.clock.advance(2)
        self.reopen()
        recovered = self.claim(trial, "reconciler")
        self.assertEqual(recovered["item"]["state"], "accepted_candidate")
        self.assertEqual(recovered["item"]["sendReceipt"], receipt)
        self.assertEqual(recovered["nextAction"], "verify_message")
        self.assert_error("readback_required", lambda: self.begin(recovered, "send_message", "send-1"))
        self.confirm_message(recovered)
        self.assertEqual(self.external.send_calls, 1)

    def test_unknown_cannot_be_reclassified_as_not_sent_by_absence_or_new_trial(self):
        trial = self.approve(self.freeze())
        lease = self.claim(trial)
        _, cid = self.conversation(lease)
        attempt, _ = self.send(lease, cid)
        unknown = self.store.error(lease["itemId"], lease["owner"], lease["fence"], attempt["attemptId"], "read_timeout")
        self.assertEqual(unknown["state"], "result_unknown")
        self.assert_error("not_sent_proof_required", lambda: self.store.error(lease["itemId"], lease["owner"], lease["fence"], attempt["attemptId"], "not_found_in_recent_page", True, "fake-negative-page"))
        self.assert_error("campaign_result_unknown", lambda: self.begin(lease, "send_message", "new-nonce"))
        self.assert_error("campaign_recipient_conflict", lambda: self.approve(self.freeze("new-id", count=1)))

    def test_positive_proof_of_no_dispatch_is_terminal_and_allows_reviewed_replacement(self):
        trial = self.approve(self.freeze())
        lease = self.claim(trial)
        attempt = self.begin(lease, "create_conversation", "never-dispatched")
        self.assert_error("not_sent_proof_required", lambda: self.store.error(lease["itemId"], lease["owner"], lease["fence"], attempt["attemptId"], "local_build_failed", True))
        result = self.store.error(lease["itemId"], lease["owner"], lease["fence"], attempt["attemptId"], "local_build_failed", True, "fake-local-before-network")
        self.assertEqual(result["state"], "failed_not_sent")
        replacement = self.freeze("reviewed-replacement", count=1)
        self.assertTrue(self.approve(replacement)["approved"])
        self.assertEqual(self.claim(trial)["itemId"], trial["items"][1]["itemId"])
        self.assertEqual((self.external.create_calls, self.external.send_calls), (0, 0))

    def test_candidate_cannot_be_declared_not_sent(self):
        trial = self.approve(self.freeze(count=1))
        lease = self.claim(trial)
        _, cid = self.conversation(lease)
        attempt, receipt = self.send(lease, cid)
        self.record_receipt(lease, attempt, receipt)
        self.assert_error("not_sent_proof_required", lambda: self.store.error(lease["itemId"], lease["owner"], lease["fence"], attempt["attemptId"], "misread", True, "incorrect-negative"))
        self.assertEqual(self.store.get_trial(trial["trialId"])["items"][0]["state"], "accepted_candidate")


    def test_reused_owner_name_cannot_use_old_fence_after_recovery(self):
        trial = self.approve(self.freeze(count=1))
        old = self.claim(trial, "same-worker-name", 1)
        attempt = self.begin(old, "create_conversation", "old-create")
        cid = self.external.create_conversation(old["item"]["oecId"])
        self.clock.advance(2)
        self.reopen()
        current = self.claim(trial, "same-worker-name")
        self.assertGreater(current["fence"], old["fence"])
        self.assert_error("stale_lease", lambda: self.store.record_conversation(old["itemId"], old["owner"], old["fence"], attempt["attemptId"], cid, "old-result"))
        self.store.confirm(current["itemId"], current["owner"], current["fence"], self.external.read_conversation(cid))
        self.assert_error("stale_lease", lambda: self.begin(old, "send_message", "stale-owner-send"))
        self.assertEqual(self.external.send_calls, 0)

    def test_unknown_blocks_another_item_even_when_it_was_claimed_earlier(self):
        first = self.approve(self.freeze("first", count=1))
        second = self.approve(self.store.create_trial("second", [source_item(2)], "a" * 64))
        a, b = self.claim(first, "a"), self.claim(second, "b")
        self.assertIsNotNone(b)
        attempt = self.begin(a, "create_conversation", "becomes-unknown")
        self.store.error(a["itemId"], a["owner"], a["fence"], attempt["attemptId"], "unknown_external_result")
        self.assert_error("campaign_result_unknown", lambda: self.begin(b, "create_conversation", "must-not-dispatch"))
        self.assertEqual(self.store.get_trial(second["trialId"])["items"][0]["attempts"], [])


    def test_global_reservation_is_atomic_under_concurrent_approval(self):
        first, second = self.freeze("one", count=1), self.freeze("two", count=1, handle="changed.name")
        barrier = threading.Barrier(2)
        def approve(trial):
            with LiveTrialStore(self.tmp.name, now=self.clock) as store:
                barrier.wait(timeout=5)
                try:
                    store.approve(trial["trialId"], trial["snapshotHash"])
                    return "approved"
                except LiveTrialError as error:
                    return error.code
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(approve, (first, second)))
        self.assertCountEqual(outcomes, ["approved", "campaign_recipient_conflict"])
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM live_campaign_recipient").fetchone()[0], 1)

    def test_approval_conflict_does_not_partially_reserve_other_recipients(self):
        self.approve(self.freeze("reserved", count=1))
        competing = self.store.create_trial("competing", [source_item(2), source_item(1)], "a" * 64)
        self.assert_error("campaign_recipient_conflict", lambda: self.approve(competing))
        independent = self.store.create_trial("independent", [source_item(2)], "a" * 64)
        self.assertTrue(self.approve(independent)["approved"])
        self.assertFalse(self.store.get_trial(competing["trialId"])["approved"])

    def test_concurrent_claims_do_not_advance_second_item_or_share_a_lease(self):
        trial = self.approve(self.freeze())
        barrier = threading.Barrier(2)
        def claim(owner):
            with LiveTrialStore(self.tmp.name, now=self.clock) as store:
                barrier.wait(timeout=5)
                return store.claim(trial["trialId"], owner)
        with ThreadPoolExecutor(max_workers=2) as pool:
            leases = list(pool.map(claim, ("owner-a", "owner-b")))
        winners = [lease for lease in leases if lease is not None]
        self.assertEqual(len(winners), 1)
        self.assertEqual(winners[0]["itemId"], trial["items"][0]["itemId"])
        self.assertIsNone(self.store.get_trial(trial["trialId"])["items"][1]["leaseOwner"])

    def test_concurrent_begin_authorizes_only_one_fake_external_call(self):
        trial = self.approve(self.freeze(count=1))
        lease = self.claim(trial)
        barrier = threading.Barrier(2)
        def begin(_):
            with LiveTrialStore(self.tmp.name, now=self.clock) as store:
                barrier.wait(timeout=5)
                return store.begin_attempt(lease["itemId"], lease["owner"], lease["fence"], "create_conversation", "exactly-one")
        with ThreadPoolExecutor(max_workers=2) as pool:
            attempts = list(pool.map(begin, (1, 2)))
        for attempt in attempts:
            if attempt["dispatchAllowed"]:
                self.external.create_conversation(lease["item"]["oecId"])
        self.assertEqual(sum(attempt["dispatchAllowed"] for attempt in attempts), 1)
        self.assertEqual(self.external.create_calls, 1)
        self.assertEqual(len(self.store.get_trial(trial["trialId"])["items"][0]["attempts"]), 1)


if __name__ == "__main__":
    unittest.main()
