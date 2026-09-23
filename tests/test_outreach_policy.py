"""Creator-level 72-hour policy across the pool, candidate and dispatch contracts."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from lib.cycle_delivery import Deliveries
from lib.cycle_inbox import Inbox
from lib.cycle_review import choose_candidates
from lib.outreach_policy import MARKETING_COOLDOWN_SECONDS, last_contact_by_creator
from lib.second_cycle import CycleError, CycleStore, digest
from test_second_cycle import NOW, edge, offer


class OutreachPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.now = NOW
        self.store = CycleStore(Path(self.temp.name) / 'test.sqlite', lambda: self.now)
        self.plan = self.store.plan('test', 'my')
        self.offers = [offer(pid, endAt=NOW + 90 * 86400) for pid in ('1', '2')]
        self.store.publish(self.plan, 'test', NOW, self.offers)
        self.store.import_edges(self.plan, [edge(), edge(pid='2', source='e2')])
        self.deliveries = Deliveries(self.store)
        Inbox(self.store)
        self.candidate = {'creatorId': 'c1', 'oecId': '123', 'pid': '1',
                          'source': {'sourceId': 'e1'}, 'offer': self.offers[0],
                          'planRevision': 1, 'controlRevision': 1, 'conversationId': '88',
                          'message': {'version': 4, 'deliveryOrder': 'card_then_text', 'textIt': 'Hello'}}

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def outbound(self, mid='10', cid='88', oec='123', stamp=NOW, plan=None):
        self.store.db.execute('INSERT INTO inbox_event VALUES(?,?,?,?,?,?,?,?,?)',
                              (plan or self.plan, cid, mid, oec, 'ourMessages', int(stamp * 1000), '{}', 1, NOW))

    def begin(self, delivery_id, kind):
        return self.deliveries.begin(delivery_id, kind, authorized_snapshot_hash=digest(self.candidate),
                                     recipient_verified=True, allowance_verified=True)

    def test_prepare_and_capacity_apply_72h_to_external_outbound_for_both_relationships(self):
        self.outbound(stamp=NOW - 259199)
        for unlocked in (0, 1):
            self.store.db.execute('UPDATE relationship SET unlocked=?', (unlocked,))
            for elapsed, blocked in ((259199, True), (259200, False), (259201, False)):
                with self.subTest(unlocked=unlocked, elapsed=elapsed):
                    self.now = NOW + elapsed - 259199
                    if blocked:
                        with self.assertRaisesRegex(CycleError, 'marketing_cooldown'):
                            self.deliveries._eligible(self.plan, self.candidate)
                        self.assertEqual(self.store._eligible_people(self.plan), set())
                    else:
                        self.deliveries._eligible(self.plan, self.candidate)
                        self.assertEqual(self.store._eligible_people(self.plan), {'c1'})
        self.assertEqual(MARKETING_COOLDOWN_SECONDS, 259200)

    def test_candidate_selection_uses_observed_external_contact(self):
        self.outbound(stamp=NOW - 259199)
        rows = [{'payload': json.dumps(edge()), 'creator_id': 'c1', 'oec': '123', 'evidence_ref': 'proof'}]
        with patch('lib.cycle_review.select_offers', return_value=self.offers), \
             patch('lib.cycle_review._position_rows', return_value=rows):
            candidates, skipped = choose_candidates(self.store, self.plan, lambda *_: None,
                                                      positions=[('c1', '1')])
            self.assertEqual(candidates, [])
            self.assertEqual(skipped[0]['reason'], 'marketing_cooldown')
            self.now += 1
            _, skipped = choose_candidates(self.store, self.plan, lambda *_: None,
                                             positions=[('c1', '1')])
            self.assertEqual(skipped[0]['reason'], 'current_identity_missing')

    def test_institution_message_after_prepare_blocks_actual_dispatch(self):
        delivery_id = self.deliveries.prepare(self.plan, self.candidate)['id']
        self.outbound()
        with self.assertRaisesRegex(CycleError, 'marketing_cooldown'):
            self.begin(delivery_id, 'card')
        self.assertTrue(all(part['started'] is None for part in self.deliveries.get(delivery_id)['parts']))

    def test_own_confirmed_card_is_excluded_by_conversation_and_message_id(self):
        delivery_id = self.deliveries.prepare(self.plan, self.candidate)['id']
        ref = self.begin(delivery_id, 'card')['requestRef']
        self.deliveries.confirm(delivery_id, 'card', {'status': 'confirmed', 'requestRef': ref,
            'oecId': '123', 'kind': 'card', 'messageId': '10', 'evidenceRef': 'readback'})
        self.outbound(mid='10')
        self.deliveries._eligible(self.plan, self.candidate, current_delivery_id=delivery_id)
        # Another conversation with the same ID is not the original card.
        self.outbound(mid='10', cid='99')
        with self.assertRaisesRegex(CycleError, 'marketing_cooldown'):
            self.begin(delivery_id, 'text')
        self.store.db.execute("DELETE FROM inbox_event WHERE cid='99'")
        self.outbound(mid='11')
        with self.assertRaisesRegex(CycleError, 'marketing_cooldown'):
            self.begin(delivery_id, 'text')
        self.store.db.execute("DELETE FROM inbox_event WHERE message_id='11'")
        self.begin(delivery_id, 'text')

    def test_inbox_cooldown_does_not_cross_market_or_creator(self):
        self.outbound(oec='999')
        other = self.store.plan('test', 'br')
        self.store.import_edges(other, [edge()])
        self.outbound(plan=other)
        self.assertEqual(last_contact_by_creator(self.store.db, self.plan), {})
        self.assertEqual(last_contact_by_creator(self.store.db, other), {'c1': NOW})


if __name__ == '__main__':
    unittest.main()
