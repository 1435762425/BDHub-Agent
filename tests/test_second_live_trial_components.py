"""Ordered card/text relationship bundles; all storage and HTTP effects are fake."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
import sqlite3
import threading
import unittest

import test_second_live_trial as base
from test_italy_im_delivery import card
from lib.italy_im_delivery import CARD_CONTENT
from lib.second_live_trial import LiveTrialStore, EXPIRY_SECONDS


def cards(count=2):
    return [asdict(card(product_id=str(123 + n), list_id=str(456 + n), campaign_id=str(789 + n))) for n in range(count)]


class LiveTrialComponentTests(unittest.TestCase):
    setUp = base.LiveTrialTest.setUp
    tearDown = base.LiveTrialTest.tearDown
    freeze = base.LiveTrialTest.freeze
    approve = base.LiveTrialTest.approve
    claim = base.LiveTrialTest.claim
    reopen = base.LiveTrialTest.reopen
    conversation = base.LiveTrialTest.conversation
    begin = base.LiveTrialTest.begin
    record_receipt = base.LiveTrialTest.record_receipt
    send = base.LiveTrialTest.send
    confirm_message = base.LiveTrialTest.confirm_message
    assert_error = base.LiveTrialTest.assert_error

    def bundle(self, count=2, people=1, request="bundle"):
        return self.approve(self.freeze(request, count=people, cards=cards(count)))

    def start_card(self, trial, lease, *, ref=None, record=True):
        current = self.claim(trial, lease["owner"])
        self.assertEqual(current["nextAction"], "send_card")
        component = current["component"]
        request_ref = ref or "card-" + str(component["position"])
        attempt = self.store.begin_attempt(lease["itemId"], lease["owner"], lease["fence"], "send_card", request_ref,
                                           component_id=component["componentId"])
        self.assertTrue(attempt["dispatchAllowed"])
        receipt = self.external.send(current["item"]["conversationId"], request_ref, CARD_CONTENT)
        if record:self.record_receipt(lease, attempt, receipt)
        return component, attempt, receipt

    def card_evidence(self, component, request_ref):
        value = component["card"]
        return {**self.external.read_message(request_ref), "kind": "card_confirmed", "componentId": component["componentId"],
                "productId": value["product_id"], "listId": value["list_id"], "campaignId": value["campaign_id"],
                "bindingSha256": value["binding_sha256"]}

    def confirm_card(self, lease, component, request_ref):
        return self.store.confirm(lease["itemId"], lease["owner"], lease["fence"], self.card_evidence(component, request_ref))

    def ready(self, trial, *, seconds=90):
        lease = self.claim(trial, seconds=seconds)
        self.conversation(lease)
        return lease

    def test_card_range_binding_and_order_are_validated_and_frozen(self):
        for value in ([], cards(5), [cards(1)[0], cards(1)[0]], [{**cards(1)[0], "verified": False}], [{**cards(1)[0], "account_name": "acc1"}]):
            self.assert_error("invalid_cards", lambda: self.freeze("invalid", count=1, cards=value))
        trial = self.freeze(count=1, cards=cards())
        self.assertEqual(trial["snapshot"]["items"][0]["cards"], cards())
        self.assertEqual([c["componentKind"] for c in trial["items"][0]["components"]], ["card", "card", "text"])
        self.assert_error("request_conflict", lambda: self.freeze(count=1, cards=list(reversed(cards()))))
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.db.execute("UPDATE live_trial_component SET frozen_json='{}'")

    def test_one_to_four_cards_each_confirm_before_exactly_one_text(self):
        for count in (1, 2, 4):
            with self.subTest(count=count):
                trial = self.approve(self.store.create_trial("size-" + str(count), [base.source_item(count, cards=cards(count))], "a" * 64))
                lease = self.claim(trial)
                self.conversation(lease, ref="create-size-" + str(count))
                self.assert_error("component_dependency_unconfirmed", lambda: self.begin(lease, "send_message", "premature-text"))
                for index in range(count):
                    component, attempt, _ = self.start_card(trial, lease, ref=f"size-{count}-card-{index}")
                    self.assertEqual(attempt["componentKind"], "card")
                    self.assertEqual(attempt["bindingSha256"], component["card"]["binding_sha256"])
                    self.assertEqual(self.claim(trial)["nextAction"], "verify_card")
                    self.assert_error("component_dependency_unconfirmed", lambda: self.begin(lease, "send_message", "premature-text"))
                    done = self.confirm_card(lease, component, attempt["requestRef"])
                    self.assertEqual(done["state"], "conversation_ready")
                current = self.claim(trial)
                self.assertEqual(current["nextAction"], "send_message");self.assertEqual(current["component"]["componentKind"], "text")
                attempt, receipt = self.send(lease, current["item"]["conversationId"], ref="text-size-" + str(count))
                self.record_receipt(lease, attempt, receipt)
                final = self.confirm_message(lease, attempt["requestRef"])
                self.assertEqual(final["state"], "confirmed");self.assertFalse(final["partialDelivery"])
                self.assertEqual([c["state"] for c in final["components"]], ["confirmed"] * (count + 1))
                self.assertEqual(len(final["attempts"]), count + 2)
        self.assertEqual(self.external.send_calls, 10)

    def test_card_candidate_does_not_unlock_next_card_or_text(self):
        trial = self.bundle();lease = self.ready(trial)
        first, attempt, receipt = self.start_card(trial, lease)
        second = self.store.get_trial(trial["trialId"])["items"][0]["components"][1]
        self.assert_error("component_dependency_unconfirmed", lambda: self.store.begin_attempt(lease["itemId"], lease["owner"], lease["fence"], "send_card", "card-1", component_id=second["componentId"]))
        self.assert_error("component_dependency_unconfirmed", lambda: self.begin(lease, "send_message", "text"))
        self.assertFalse(self.store.begin_attempt(lease["itemId"], lease["owner"], lease["fence"], "send_card", attempt["requestRef"], component_id=first["componentId"])["dispatchAllowed"])
        self.assert_error("request_conflict", lambda: self.store.begin_attempt(lease["itemId"], lease["owner"], lease["fence"], "send_card", "new-nonce", component_id=first["componentId"]))
        self.record_receipt(lease, attempt, receipt)
        self.assertEqual(self.external.send_calls, 1)

    def test_card_evidence_is_bound_to_component_identity_nonce_content_and_card(self):
        trial = self.bundle();lease = self.ready(trial)
        component, attempt, _ = self.start_card(trial, lease)
        evidence = self.card_evidence(component, attempt["requestRef"])
        mutations = ({"productId": "124"}, {"listId": "457"}, {"campaignId": "790"}, {"bindingSha256": "0" * 64},
                     {"recipientOecId": "another"}, {"conversationId": "another"}, {"requestRef": "another"},
                     {"textSha256": "0" * 64}, {"messageId": "another"})
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                self.assert_error("evidence_mismatch", lambda: self.store.confirm(lease["itemId"], lease["owner"], lease["fence"], {**evidence, **mutation}))
        self.assertEqual(self.store.get_trial(trial["trialId"])["items"][0]["components"][0]["state"], "accepted_candidate")
        self.confirm_card(lease, component, attempt["requestRef"])

    def test_old_card_confirmation_replay_does_not_reset_a_later_inflight_component(self):
        trial = self.bundle();lease = self.ready(trial)
        first, attempt, _ = self.start_card(trial, lease)
        self.confirm_card(lease, first, attempt["requestRef"])
        self.start_card(trial, lease, record=False)
        replay = self.confirm_card(lease, first, attempt["requestRef"])
        self.assertEqual(replay["state"], "submitting")
        self.assertEqual([c["state"] for c in replay["components"]], ["confirmed", "inflight", "pending"])

    def test_previous_conversation_error_cannot_override_completed_card_facts(self):
        trial = self.bundle();lease = self.claim(trial)
        created, _ = self.conversation(lease)
        component, attempt, _ = self.start_card(trial, lease)
        self.confirm_card(lease, component, attempt["requestRef"])
        self.assert_error("attempt_mismatch", lambda: self.store.error(lease["itemId"], lease["owner"], lease["fence"], created["attemptId"], "late_old_error"))
        self.assertEqual(self.claim(trial)["component"]["position"], 1)

    def test_reopen_after_card_confirmation_preserves_it_and_rechecks_same_conversation(self):
        trial = self.bundle();old = self.ready(trial, seconds=1)
        component, attempt, _ = self.start_card(trial, old)
        self.confirm_card(old, component, attempt["requestRef"])
        cid = self.store.get_trial(trial["trialId"])["items"][0]["conversationId"]
        self.clock.advance(2);self.reopen()
        current = self.claim(trial, "restarted")
        self.assertEqual(current["nextAction"], "verify_conversation");self.assertTrue(current["readOnly"])
        self.assertEqual(current["component"]["position"], 1)
        self.store.confirm(current["itemId"], current["owner"], current["fence"], self.external.read_conversation(cid))
        self.assertEqual(self.claim(trial, "restarted")["nextAction"], "send_card")
        self.assert_error("stale_lease", lambda: self.store.begin_attempt(old["itemId"], old["owner"], old["fence"], "send_card", "stale", component_id=current["component"]["componentId"]))
        self.assertEqual(self.external.send_calls, 1)

    def test_lost_card_response_recovers_read_only_and_blocks_entire_campaign(self):
        trial = self.bundle();old = self.ready(trial, seconds=1)
        component, attempt, receipt = self.start_card(trial, old, record=False)
        self.clock.advance(2);self.reopen()
        current = self.claim(trial, "reconciler")
        self.assertEqual(current["nextAction"], "verify_card");self.assertTrue(current["readOnly"])
        self.assertEqual(current["component"]["requestRef"], attempt["requestRef"])
        self.assertEqual(current["component"]["state"], "result_unknown")
        self.assert_error("stale_lease", lambda: self.record_receipt(old, attempt, receipt))
        self.assert_error("campaign_result_unknown", lambda: self.store.begin_attempt(current["itemId"], current["owner"], current["fence"], "send_card", "replacement", component_id=component["componentId"]))
        other = self.approve(self.store.create_trial("other", [base.source_item(2, cards=cards())], "a" * 64))
        self.assertIsNone(self.claim(other))
        self.assert_error("campaign_recipient_conflict", lambda: self.bundle(request="same-oec"))
        self.confirm_card(current, component, attempt["requestRef"])
        self.assertEqual(self.claim(trial, "reconciler")["component"]["position"], 1)
        self.assertEqual(self.external.send_calls, 1)

    def test_reopened_card_candidate_keeps_receipt_and_cannot_be_reposted(self):
        trial = self.bundle();old = self.ready(trial, seconds=1)
        component, attempt, receipt = self.start_card(trial, old)
        self.clock.advance(2);self.reopen();current = self.claim(trial, "new")
        self.assertEqual(current["nextAction"], "verify_card");self.assertEqual(current["component"]["sendReceipt"], receipt)
        self.assert_error("readback_required", lambda: self.store.begin_attempt(current["itemId"], current["owner"], current["fence"], "send_card", attempt["requestRef"], component_id=component["componentId"]))
        self.confirm_card(current, component, attempt["requestRef"])
        self.assertEqual(self.external.send_calls, 1)

    def test_unverified_create_candidate_blocks_card_until_same_cid_readback(self):
        trial = self.bundle();lease = self.claim(trial)
        attempt = self.begin(lease, "create_conversation", "candidate-only")
        cid = self.external.create_conversation(lease["item"]["oecId"])
        self.store.record_conversation(lease["itemId"], lease["owner"], lease["fence"], attempt["attemptId"], cid, "response", verified=False)
        current = self.claim(trial)
        self.assertEqual(current["nextAction"], "verify_conversation")
        self.assert_error("readback_required", lambda: self.store.begin_attempt(lease["itemId"], lease["owner"], lease["fence"], "send_card", "too-soon", component_id=current["component"]["componentId"]))
        self.store.confirm(lease["itemId"], lease["owner"], lease["fence"], self.external.read_conversation(cid))
        self.assertEqual(self.claim(trial)["nextAction"], "send_card")
        self.assertEqual(self.external.send_calls, 0)

    def test_new_reader_can_mark_second_card_unknown_preserving_first_card_and_candidate(self):
        trial = self.bundle();old = self.ready(trial, seconds=1)
        first, sent, _ = self.start_card(trial, old)
        self.confirm_card(old, first, sent["requestRef"])
        second, attempt, receipt = self.start_card(trial, old)
        self.clock.advance(2);self.reopen();reader = self.claim(trial, "new-reader")
        unknown = self.store.mark_verification_unresolved(reader["itemId"], reader["owner"], reader["fence"], "card_history_unresolved")
        self.assertEqual(unknown["state"], "result_unknown");self.assertTrue(unknown["partialDelivery"])
        self.assertEqual([c["state"] for c in unknown["components"]], ["confirmed", "result_unknown", "pending"])
        self.assertEqual(unknown["components"][1]["sendReceipt"], receipt)
        self.assertEqual(unknown["attempts"][-1]["fence"], old["fence"])
        self.assertEqual(self.claim(trial, "new-reader")["nextAction"], "verify_card")
        other = self.approve(self.store.create_trial("other-person", [base.source_item(2, cards=cards())], "a" * 64))
        self.assertIsNone(self.claim(other))
        self.assert_error("campaign_recipient_conflict", lambda: self.bundle(request="cannot-repeat-cards"))
        self.confirm_card(reader, second, attempt["requestRef"])
        self.assertEqual(self.claim(trial, "new-reader")["nextAction"], "send_message")
        self.assertEqual(self.external.send_calls, 2)

    def test_expired_candidate_can_be_marked_unresolved_by_current_reader(self):
        trial = self.bundle(count=1);old = self.ready(trial, seconds=1)
        component, attempt, receipt = self.start_card(trial, old)
        self.clock.advance(EXPIRY_SECONDS + 1);self.reopen();reader = self.claim(trial, "reader")
        self.assertTrue(reader["readOnly"])
        unknown = self.store.mark_verification_unresolved(reader["itemId"], reader["owner"], reader["fence"], "expired_readback_failed")
        self.assertEqual(unknown["state"], "result_unknown");self.assertEqual(unknown["components"][0]["sendReceipt"], receipt)
        self.assertEqual(self.confirm_card(reader, component, attempt["requestRef"])["state"], "partial_delivery")
        self.assertEqual(self.external.send_calls, 1)

    def test_pause_after_card_preserves_partial_delivery_and_recipient_guard(self):
        trial = self.bundle(people=2);lease = self.ready(trial)
        component, attempt, _ = self.start_card(trial, lease)
        self.confirm_card(lease, component, attempt["requestRef"])
        paused = self.store.pause(trial["trialId"], trial["snapshotHash"])
        self.assertEqual([i["state"] for i in paused["items"]], ["partial_delivery", "failed_not_sent"])
        self.assertTrue(paused["items"][0]["partialDelivery"])
        self.assertNotIn(lease["itemId"], paused["events"][-1]["data"]["cancelledBeforeSend"])
        self.assert_error("campaign_recipient_conflict", lambda: self.bundle(request="must-not-repeat-card"))

    def test_pause_during_card_allows_readback_but_never_unlocks_text(self):
        trial = self.bundle(count=1);lease = self.ready(trial)
        component, attempt, receipt = self.start_card(trial, lease, record=False)
        self.store.pause(trial["trialId"], trial["snapshotHash"])
        self.record_receipt(lease, attempt, receipt)
        final = self.confirm_card(lease, component, attempt["requestRef"])
        self.assertEqual(final["state"], "partial_delivery");self.assertTrue(final["partialDelivery"])
        self.assertEqual(final["components"][-1]["state"], "pending")
        self.assertIsNone(self.claim(trial))
        self.assert_error("campaign_recipient_conflict", lambda: self.bundle(request="new-after-pause"))

    def test_text_not_dispatched_after_card_is_partial_and_never_releases_oec(self):
        trial = self.bundle(count=1);lease = self.ready(trial)
        component, attempt, _ = self.start_card(trial, lease)
        self.confirm_card(lease, component, attempt["requestRef"])
        text = self.begin(lease, "send_message", "text-build-failed")
        result = self.store.error(lease["itemId"], lease["owner"], lease["fence"], text["attemptId"], "local_build_failed", True, "positive-zero-post-proof")
        self.assertEqual(result["state"], "partial_delivery");self.assertTrue(result["partialDelivery"])
        self.assertEqual([c["state"] for c in result["components"]], ["confirmed", "failed_not_sent"])
        self.assert_error("campaign_recipient_conflict", lambda: self.bundle(request="replacement-text"))

    def test_second_card_not_dispatched_keeps_first_card_fact_and_guard(self):
        trial = self.bundle();lease = self.ready(trial)
        component, attempt, _ = self.start_card(trial, lease)
        self.confirm_card(lease, component, attempt["requestRef"])
        current = self.claim(trial)
        second = self.store.begin_attempt(lease["itemId"], lease["owner"], lease["fence"], "send_card", "second-local-failure", component_id=current["component"]["componentId"])
        result = self.store.error(lease["itemId"], lease["owner"], lease["fence"], second["attemptId"], "local_build_failed", True, "zero-post-proof")
        self.assertEqual(result["state"], "partial_delivery")
        self.assert_error("campaign_recipient_conflict", lambda: self.bundle(request="replacement-card"))

    def test_first_card_definitely_not_dispatched_can_release_wholly_unsent_recipient(self):
        trial = self.bundle();lease = self.ready(trial);current = self.claim(trial)
        first = self.store.begin_attempt(lease["itemId"], lease["owner"], lease["fence"], "send_card", "zero-post", component_id=current["component"]["componentId"])
        result = self.store.error(lease["itemId"], lease["owner"], lease["fence"], first["attemptId"], "local_build_failed", True, "zero-post-proof")
        self.assertEqual(result["state"], "failed_not_sent");self.assertFalse(result["partialDelivery"])
        self.assertTrue(self.bundle(request="reviewed-replacement")["approved"])
        self.assertEqual(self.external.send_calls, 0)

    def test_text_unknown_after_card_blocks_campaign_but_readback_can_finish(self):
        trial = self.bundle(count=1);lease = self.ready(trial)
        component, attempt, _ = self.start_card(trial, lease)
        self.confirm_card(lease, component, attempt["requestRef"])
        current = self.claim(trial);attempt, _ = self.send(lease, current["item"]["conversationId"], "text-lost")
        unknown = self.store.error(lease["itemId"], lease["owner"], lease["fence"], attempt["attemptId"], "read_timeout")
        self.assertEqual(unknown["state"], "result_unknown");self.assertTrue(unknown["partialDelivery"])
        self.assertEqual(self.claim(trial)["nextAction"], "verify_message")
        self.assert_error("not_sent_proof_required", lambda: self.store.error(lease["itemId"], lease["owner"], lease["fence"], attempt["attemptId"], "negative_page", True, "not-enough"))
        self.assert_error("campaign_recipient_conflict", lambda: self.bundle(request="same-oec-again"))
        final = self.confirm_message(lease, "text-lost")
        self.assertEqual(final["state"], "confirmed");self.assertEqual(self.external.send_calls, 2)

    def test_late_card_confirmation_after_expiry_preserves_partial_guard(self):
        trial = self.bundle(count=1);self.clock.advance(EXPIRY_SECONDS - 1)
        lease = self.ready(trial, seconds=60);component, attempt, _ = self.start_card(trial, lease)
        self.clock.advance(2)
        final = self.confirm_card(lease, component, attempt["requestRef"])
        self.assertEqual(final["state"], "partial_delivery");self.assertEqual(final["errorCode"], "expired_after_card")
        self.assert_error("campaign_recipient_conflict", lambda: self.bundle(request="new-expiry-window"))

    def test_concurrent_card_begin_yields_one_intent_and_uses_same_owner(self):
        trial = self.bundle();lease = self.ready(trial);component = self.claim(trial)["component"]
        barrier = threading.Barrier(2)
        def begin(_):
            with LiveTrialStore(self.tmp.name, now=self.clock) as store:
                barrier.wait(timeout=5)
                return store.begin_attempt(lease["itemId"], lease["owner"], lease["fence"], "send_card", "one-card", component_id=component["componentId"])
        with ThreadPoolExecutor(max_workers=2) as pool:attempts = list(pool.map(begin, (1, 2)))
        self.assertEqual(sum(value["dispatchAllowed"] for value in attempts), 1)
        self.assertEqual(len({value["attemptId"] for value in attempts}), 1)
        self.assertIsNone(self.claim(trial, "other-account-worker"))
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM live_campaign_recipient").fetchone()[0], 1)


if __name__ == "__main__":unittest.main()
