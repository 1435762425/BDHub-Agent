from contextlib import contextmanager
import copy
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib.second_cycle import CycleStore, CycleError, encoded
from lib import sample_operations as ops
from lib import first_outreach
from lib.sample_transport import normalize_record
from test_sample_transport import contract, raw

BINDING = {'market': 'br', 'account': 'acc2', 'institutionFingerprint': 'test-agency', 'identitySha256': 'test-generation'}


class FakeSessions:
    def __init__(self, records, *, after='approved', failure=None, stopped_on_write=None, quota=1):
        self.records = copy.deepcopy(records)
        self.quota = quota
        self.after = after
        self.failure = failure
        self.writes = []
        self.sessions = []
        self.stopped_on_write = stopped_on_write

    @contextmanager
    def __call__(self, root, market, account, report, **options):
        self.sessions.append({'market': market, 'account': account, **options})
        owner = self
        class Adapter:
            binding = BINDING
            def read_page(self, page=1):
                return {'records': copy.deepcopy(owner.records), 'total': len(owner.records), 'page': page,
                        'pageSize': 5, 'evidenceRef': 'test-read:' + str(len(owner.writes))}
            def refresh_sample_quota(self, offer):
                return offer['sample'] | {'pid': offer['pid'], 'campaignId': offer['campaignId'], 'available': True, 'quota': owner.quota, 'observedAt': time.time()}
            def approve_once(self, payload):
                owner.writes.append(copy.deepcopy(payload))
                if owner.failure:
                    raise owner.failure
                for row in owner.records:
                    row['state'] = owner.after
                return {'status': 'accepted', 'evidenceRef': 'test-accepted'}
        if options.get('frozen') and self.stopped_on_write:
            self.stopped_on_write()
        yield Adapter()


class SampleOperationsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = self.root / 'var/second-cycle.sqlite'
        self.store = CycleStore(self.path)
        self.addCleanup(self.store.close)
        self.plan = self.store.plan('bjn-local-research', 'br')
        first_outreach.ensure_schema(self.store)
        ops.ensure_schema(self.store)
        ops.authorize(self.store, 'br', True, 'test-start-0001', root=self.root)
        ops.record_capability(self.store, 'br', 'acc2', 'verified', 'test-canary', contract=contract(), binding=BINDING)
        self.application = normalize_record(raw(), contract=contract())
        self.offer = {'pid': '401', 'campaignId': '501', 'offerKey': 'campaign:401:501',
                      'sample': {'supported': True, 'quota': 1, 'observedAt': time.time(), 'evidenceRef': 'test-quota'}}

    def assess(self, store, root, market, application):
        return {'eligible': True, 'offer': copy.deepcopy(self.offer), 'deliveryId': 'test-delivery'}

    def prepare(self, application=None, **options):
        return ops._prepare(self.store, self.root, 'br', 'acc2', application or self.application,
                            BINDING, contract=contract(), quota_proof=self.offer['sample'] | {'pid': '401', 'campaignId': '501', 'available': True}, assessor=options.pop('assessor', self.assess), **options)

    def run_one(self, sessions, **options):
        with patch.object(ops, '_account', return_value='acc2'):
            return ops.run_one(self.root, transport_factory=sessions, assessor=self.assess, **options)

    def test_status_missing_database_has_no_side_effect(self):
        new = self.root / 'empty'
        before = list(self.root.iterdir())
        self.assertFalse(ops.status(new)['enabled'])
        self.assertFalse(new.exists())
        self.assertEqual(list(self.root.iterdir()), before)

    def test_status_and_query_are_readonly(self):
        ops.store_records(self.store, 'br', 'acc2', [self.application], evidence_ref='test-sync')
        before = self.path.stat().st_mtime_ns
        result = ops.status(self.root)
        self.assertTrue(result['enabled'])
        self.assertEqual(result['capability']['state'], 'verified')
        self.assertEqual(len(result['records']), 1)
        self.assertEqual(self.path.stat().st_mtime_ns, before)

    def test_new_review_confirmed_only_by_platform_lifecycle(self):
        sessions = FakeSessions([self.application])
        result = self.run_one(sessions)
        self.assertEqual(result['state'], 'approved')
        self.assertEqual(len(sessions.writes), 1)
        self.assertEqual(sessions.writes[0]['creator_id'], '301')
        self.run_one(sessions)
        self.assertEqual(len(sessions.writes), 1)

    def test_seller_wait_is_distinct_from_platform_approval(self):
        sessions = FakeSessions([self.application], after='seller_pending')
        self.assertEqual(self.run_one(sessions)['state'], 'seller_pending')
        self.assertEqual(ops.status(self.root)['counts'], {'seller_pending': 1})

    def test_seller_pending_later_becomes_platform_approved_without_new_write(self):
        sessions = FakeSessions([self.application], after='seller_pending')
        self.assertEqual(self.run_one(sessions)['state'], 'seller_pending')
        self.store.db.execute('UPDATE sample_review_check SET observed=observed-61')
        sessions.records[0]['state'] = 'approved'
        self.assertEqual(self.run_one(sessions)['state'], 'approved')
        self.assertEqual(len(sessions.writes), 1)
        self.assertNotIn('frozen', sessions.sessions[-1])

    def test_remote_quota_can_refresh_an_old_catalog_without_republishing_it(self):
        self.offer['sample']['observedAt'] = time.time() - 172800
        self.assertEqual(self.run_one(FakeSessions([self.application]))['state'], 'approved')
        self.assertLess(self.offer['sample']['observedAt'], time.time() - 170000)

    def test_remote_quota_drop_before_dispatch_prevents_a_write(self):
        sessions = FakeSessions([self.application], stopped_on_write=lambda: setattr(sessions, 'quota', 0))
        with self.assertRaisesRegex(CycleError, 'quota_unverified'):
            self.run_one(sessions)
        self.assertFalse(sessions.writes)
        self.assertEqual(ops.status(self.root)['counts'], {'ready': 1})

    def test_success_response_without_lifecycle_stays_unknown(self):
        sessions = FakeSessions([self.application], after='pending')
        self.assertEqual(self.run_one(sessions)['state'], 'unknown')
        self.assertEqual(self.run_one(sessions)['state'], 'unknown')
        self.assertEqual(len(sessions.writes), 1)
        self.assertNotIn('frozen', sessions.sessions[-1])

    def test_unknown_timeout_reconciles_original_intent_even_when_paused(self):
        sessions = FakeSessions([self.application], failure=TimeoutError('test timeout'))
        with self.assertRaises(TimeoutError):
            self.run_one(sessions)
        intent = dict(self.store.db.execute('SELECT * FROM sample_review_intent').fetchone())
        original_ref = intent['request_ref']
        ops.authorize(self.store, 'br', False, 'test-stop-0002', root=self.root)
        sessions.failure = None
        sessions.records[0]['state'] = 'approved'
        self.assertEqual(self.run_one(sessions)['state'], 'approved')
        self.assertEqual(len(sessions.writes), 1)
        self.assertEqual(self.store.db.execute('SELECT request_ref FROM sample_review_intent').fetchone()[0], original_ref)
        self.assertEqual(sessions.sessions[-1]['account'], 'acc2')
        self.assertEqual(sessions.sessions[-1]['expected_binding'], BINDING)

    def test_other_creator_same_application_cannot_confirm(self):
        intent, _ = self.prepare()
        intent = ops._begin(self.store, intent, self.application)
        changed = self.application | {'state': 'approved', 'oecId': '999'}
        self.assertEqual(ops._confirm_from_records(self.store, intent, [changed], 'test-mismatch'), 'unknown')

    def test_application_absence_does_not_prove_no_effect(self):
        intent, _ = self.prepare()
        intent = ops._begin(self.store, intent, self.application)
        self.assertEqual(ops._confirm_from_records(self.store, intent, [], 'test-empty'), 'unknown')

    def test_concurrent_quota_reservations_cannot_overspend(self):
        intent, created = self.prepare()
        self.assertTrue(created)
        other = self.application | {'applicationId': '102', 'oecId': '202', 'platformCreatorId': '302'}
        with CycleStore(self.path) as second:
            with self.assertRaisesRegex(CycleError, 'quota_reserved'):
                ops._prepare(second, self.root, 'br', 'acc2', other, BINDING, contract=contract(), quota_proof=self.offer['sample'] | {'pid': '401', 'campaignId': '501', 'available': True}, assessor=self.assess)
        again, resumable = self.prepare()
        self.assertEqual(again['id'], intent['id'])
        self.assertTrue(resumable)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM sample_review_intent').fetchone()[0], 1)

    def test_unknown_reserves_quota_even_after_new_observation(self):
        intent, _ = self.prepare()
        intent = ops._begin(self.store, intent, self.application)
        with self.store.tx():
            self.store.db.execute("UPDATE sample_review_intent SET state='unknown' WHERE id=?", (intent['id'],))
        self.offer['sample']['observedAt'] = time.time()
        other = self.application | {'applicationId': '102', 'oecId': '202', 'platformCreatorId': '302'}
        with self.assertRaisesRegex(CycleError, 'quota_reserved'):
            self.prepare(other)

    def test_paid_unknown_payment_and_unrecommended_stay_for_handling(self):
        for payment in (True, None):
            with self.assertRaisesRegex(CycleError, 'payment_requires_handling'):
                self.prepare(self.application | {'paymentRequired': payment})
        with self.assertRaisesRegex(CycleError, 'not_recommended'):
            self.prepare(assessor=lambda *args: {'eligible': False, 'reason': 'not_recommended'})
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM sample_review_intent').fetchone()[0], 0)

    def test_new_application_id_does_not_repeat_the_same_sample(self):
        self.offer['sample']['quota'] = 5
        self.prepare()
        with self.assertRaisesRegex(CycleError, 'duplicate_intent'):
            self.prepare(self.application | {'applicationId': '102'})

    def test_multiple_visible_applications_for_same_creator_wait(self):
        duplicate = self.application | {'applicationId': '102'}
        ops.store_records(self.store, 'br', 'acc2', [self.application, duplicate], evidence_ref='test-duplicates')
        with self.assertRaisesRegex(CycleError, 'duplicate_application'):
            self.prepare()
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM sample_review_intent').fetchone()[0], 0)

    def test_ready_intent_cannot_reuse_stale_quota_after_pause(self):
        self.prepare()
        self.offer['sample']['observedAt'] = time.time() - 901
        with self.assertRaisesRegex(CycleError, 'quota_unverified'):
            self.prepare()

    def test_stale_quota_and_noninteger_quota_are_not_approval_evidence(self):
        for quota, observed in ((1, time.time() - 901), ('1', time.time()), (True, time.time()), (0, time.time())):
            self.offer['sample'].update(quota=quota, observedAt=observed)
            with self.assertRaisesRegex(CycleError, 'quota_unverified'):
                self.prepare()

    def test_pause_before_dispatch_prevents_any_write(self):
        sessions = FakeSessions([self.application], stopped_on_write=lambda: ops.authorize(self.store, 'br', False, 'test-stop-0002', root=self.root))
        with self.assertRaisesRegex(CycleError, 'paused'):
            self.run_one(sessions)
        self.assertEqual(sessions.writes, [])
        self.assertEqual(ops.status(self.root)['counts'], {'ready': 1})

    def test_never_dispatched_intent_releases_reservation_when_application_changes(self):
        self.prepare()
        changed = self.application | {'state': 'cancelled'}
        ops._settle_unsubmitted(self.store, 'br', [changed], complete=True)
        self.assertEqual(ops.status(self.root)['counts'], {'cancelled': 1})
        other = self.application | {'applicationId': '102', 'oecId': '202', 'platformCreatorId': '302'}
        self.assertTrue(self.prepare(other)[1])

    def test_no_authorization_or_unverified_capability_never_opens_session(self):
        sessions = FakeSessions([self.application])
        ops.record_capability(self.store, 'br', 'acc2', 'readonly', 'test-read')
        self.assertEqual(self.run_one(sessions)['state'], 'capability_unverified')
        self.assertFalse(sessions.sessions)
        ops.authorize(self.store, 'br', False, 'test-stop-0002', root=self.root)
        self.assertEqual(self.run_one(sessions)['state'], 'paused')
        self.assertFalse(sessions.sessions)

    def test_read_sync_cannot_authorize_or_promote_review_capability(self):
        ops.authorize(self.store, 'br', False, 'test-stop-0002', root=self.root)
        self.store.db.execute('DELETE FROM sample_capability')
        sessions = FakeSessions([self.application])
        with patch.object(ops, '_account', return_value='acc2'):
            ops.sync(self.root, transport_factory=sessions)
        self.assertEqual(ops.capability(self.store, 'br', 'acc2')['state'], 'readonly')
        self.assertFalse(ops.status(self.root)['enabled'])
        self.assertFalse(sessions.writes)

    def test_immutable_intent_and_original_account_cannot_change(self):
        intent, _ = self.prepare()
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'immutable'):
            self.store.db.execute("UPDATE sample_review_intent SET account_name='acc1' WHERE id=?", (intent['id'],))
        ops.store_records(self.store, 'br', 'acc2', [self.application], evidence_ref='first')
        ops.store_records(self.store, 'br', 'acc1', [self.application | {'state': 'approved'}], evidence_ref='other')
        record = ops.query_records(self.store)[0]
        self.assertEqual(record['account'], 'acc2')
        self.assertEqual(record['application']['state'], 'pending')

    def test_non_br_is_rejected_before_any_io(self):
        with self.assertRaisesRegex(CycleError, 'market_not_supported'):
            ops.status(self.root / 'nothing', 'mx')


if __name__ == '__main__':
    unittest.main()
