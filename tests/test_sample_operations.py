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
from test_sample_transport import br_raw, contract, raw, read_contract, seller_policy, canary_contract, status_evidence

BINDING = {'market': 'br', 'account': 'acc2', 'institutionFingerprint': 'test-agency', 'identitySha256': 'test-generation'}


class FakeSessions:
    def __init__(self, records, *, after='approved', failure=None, stopped_on_write=None, quota=1):
        self.records = copy.deepcopy(records)
        self.quota = quota
        self.binding = BINDING
        self.read_failure = None
        self.after = after
        self.failure = failure
        self.writes = []
        self.sessions = []
        self.stopped_on_write = stopped_on_write
        self.contract = None
        self.platform_counts = None
        self.raw_records = None
        self.after_native = None
        self.counter_by_native = None
        self.receipt = {'status': 'accepted', 'http': 200, 'code': 0, 'evidenceRef': 'test-accepted'}

    @contextmanager
    def __call__(self, root, market, account, report, **options):
        self.sessions.append({'market': market, 'account': account, **options})
        owner = self
        class Adapter:
            binding = owner.binding
            contract = options.get('contract') or owner.contract
            def read_page(self, page=1):
                if owner.read_failure:
                    raise owner.read_failure
                size = (self.contract or {}).get('readBody', {}).get('page_size', 5)
                records = [normalize_record(item, contract=self.contract) for item in owner.raw_records] if owner.raw_records is not None else owner.records
                counters = owner.platform_counts
                if owner.raw_records is not None and owner.counter_by_native is not None:
                    counters = {}
                    for item in owner.raw_records:
                        key = owner.counter_by_native[item['sample_status']]
                        counters[key] = counters.get(key, 0) + 1
                from lib.second_cycle import digest
                return {'records': copy.deepcopy(records[(page - 1) * size:page * size]), 'total': len(records), 'page': page,
                        'pageSize': size, 'evidenceRef': 'test-read:' + digest([owner.raw_records if owner.raw_records is not None else records, counters, page, size]),
                        'platformCounts': counters, 'readScope': (self.contract or {}).get('readScope')}
            def refresh_sample_quota(self, offer):
                return offer['sample'] | {'pid': offer['pid'], 'campaignId': offer['campaignId'], 'available': True, 'quota': owner.quota, 'observedAt': time.time()}
            def approve_once(self, payload):
                owner.writes.append(copy.deepcopy(payload))
                if owner.failure:
                    raise owner.failure
                for row in owner.records:
                    row['state'] = owner.after
                if owner.raw_records is not None and owner.after_native is not None:
                    for item in owner.raw_records:
                        item['sample_status'] = owner.after_native
                return copy.deepcopy(owner.receipt)
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
        self.assertEqual(self.run_one(sessions)['state'], 'waiting_reconciliation')
        self.store.db.execute('UPDATE sample_review_check SET observed=observed-61')
        self.store.db.execute('UPDATE sample_review_intent SET started=started-61')
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
        self.store.db.execute('UPDATE sample_review_check SET observed=observed-61')
        self.store.db.execute('UPDATE sample_review_intent SET started=started-61')
        self.assertEqual(self.run_one(sessions)['state'], 'goal_observed')
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
        ops.record_capability(self.store, 'br', 'acc2', 'readonly', 'test-read', contract=read_contract(), binding=BINDING)
        self.assertEqual(self.run_one(sessions)['state'], 'capability_unverified')
        self.assertFalse(sessions.sessions)
        ops.authorize(self.store, 'br', False, 'test-stop-0002', root=self.root)
        self.assertEqual(self.run_one(sessions)['state'], 'paused')
        self.assertFalse(sessions.sessions)

    def test_read_sync_cannot_authorize_or_promote_review_capability(self):
        ops.authorize(self.store, 'br', False, 'test-stop-0002', root=self.root)
        self.store.db.execute('DELETE FROM sample_capability')
        sessions = FakeSessions([normalize_record(br_raw(), contract=read_contract())])
        sessions.contract = read_contract()
        sessions.platform_counts = {'TO_REVIEW_1': 1}
        with patch.object(ops, '_account', return_value='acc2'):
            ops.sync(self.root, transport_factory=sessions)
        self.assertEqual(ops.capability(self.store, 'br', 'acc2')['state'], 'readonly')
        self.assertFalse(ops.status(self.root)['enabled'])
        self.assertFalse(sessions.writes)

    def test_unproven_empty_sync_does_not_prove_current_application_coverage_or_read_contract(self):
        self.store.db.execute('DELETE FROM sample_capability')
        sessions = FakeSessions([])
        with patch.object(ops, '_account', return_value='acc2'):
            ops.sync(self.root, transport_factory=sessions)
        row = self.store.db.execute('SELECT * FROM sample_sync').fetchone()
        self.assertEqual(row['complete'], 1)
        self.assertIsNone(row['binding_fingerprint'])
        self.assertIsNone(row['contract_fingerprint'])
        self.assertEqual(ops.capability(self.store, 'br', 'acc2')['state'], 'unknown')

    def test_readonly_capability_requires_read_contract_and_original_binding(self):
        for value, binding in ((None, BINDING), (contract(), BINDING),
                               (read_contract(), BINDING | {'identitySha256': 'other-generation'})):
            with self.assertRaises(CycleError):
                ops.record_capability(self.store, 'br', 'acc2', 'readonly', 'test-evidence', contract=value, binding=binding)

    def test_all_state_sync_records_22_members_without_granting_review_capability(self):
        self.store.db.execute('DELETE FROM sample_capability')
        statuses = [10] * 6 + [40] + [52] * 8 + [56] + [100] * 6
        records = [normalize_record(br_raw(i, native), contract=read_contract()) for i, native in enumerate(statuses)]
        sessions = FakeSessions(records)
        sessions.contract = read_contract()
        sessions.platform_counts = {'TO_REVIEW_1': 6, 'CANCELED': 9, 'COMPLETED': 6, 'CONTENT_PENDING': 1}
        with patch.object(ops, '_account', return_value='acc2'):
            result = ops.sync(self.root, transport_factory=sessions)
        self.assertEqual((result['records'], result['complete'], result['reviewCapability']), (22, True, 'readonly'))
        self.assertEqual(ops.capability(self.store, 'br', 'acc2')['contract'], read_contract())
        snapshot = ops.read_snapshot(self.store)
        self.assertEqual((snapshot['total'], snapshot['platformCounts']['TO_REVIEW_1']), (22, 6))
        self.assertEqual(snapshot['syncId'], result['syncId'])
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM sample_sync_member WHERE sync_id=?', (result['syncId'],)).fetchone()[0], 22)
        value = ops.status(self.root)
        self.assertEqual(value['applicationCounts'], {'unknown': 22})
        self.assertEqual(value['platformCounts'], sessions.platform_counts)
        self.assertEqual(value['capability']['state'], 'readonly')
        self.assertTrue(value['readCoverage']['complete'])
        self.assertFalse(sessions.writes)

    def test_readonly_sync_binding_mismatch_cannot_store_members_or_counts(self):
        ops.record_capability(self.store, 'br', 'acc2', 'readonly', 'test-read', contract=read_contract(), binding=BINDING)
        sessions = FakeSessions([normalize_record(br_raw(), contract=read_contract())])
        sessions.binding = BINDING | {'identitySha256': 'other-generation'}
        sessions.contract = read_contract()
        sessions.platform_counts = {'TO_REVIEW_1': 1}
        with patch.object(ops, '_account', return_value='acc2'):
            with self.assertRaisesRegex(CycleError, 'provenance_mismatch'):
                ops.sync(self.root, transport_factory=sessions)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM sample_sync').fetchone()[0], 0)

    def test_snapshot_cannot_reuse_old_account_binding_contract_or_member_count(self):
        ops.record_capability(self.store, 'br', 'acc2', 'readonly', 'test-read', contract=read_contract(), binding=BINDING)
        application = normalize_record(br_raw(), contract=read_contract())
        result = ops.store_records(self.store, 'br', 'acc2', [application], evidence_ref='current', complete=True,
            binding=BINDING, contract=read_contract(), read_scope='all_states', platform_counts={'TO_REVIEW_1': 1})
        self.assertIsNotNone(ops.read_snapshot(self.store))
        self.store.db.execute('DELETE FROM sample_sync_member WHERE sync_id=?', (result['syncId'],))
        self.assertIsNone(ops.read_snapshot(self.store))
        self.store.db.execute('INSERT INTO sample_sync_member VALUES(?,?)', (result['syncId'], application['applicationId']))
        self.store.db.execute("UPDATE sample_sync SET contract_fingerprint='old-contract'")
        self.assertIsNone(ops.read_snapshot(self.store))

    def test_named_platform_counts_must_match_complete_membership(self):
        ops.record_capability(self.store, 'br', 'acc2', 'readonly', 'test-read', contract=read_contract(), binding=BINDING)
        for counters, complete in (({'TO_REVIEW_1': 2}, True), ({'TO_REVIEW_1': 1}, False)):
            with self.assertRaisesRegex(CycleError, 'counts_incomplete'):
                ops.store_records(self.store, 'br', 'acc2', [self.application], evidence_ref='bad-counts', complete=complete,
                    binding=BINDING, contract=read_contract(), read_scope='all_states', platform_counts=counters)

    def test_verified_full_sync_records_current_binding_and_contract_provenance(self):
        from lib.second_cycle import digest
        sessions = FakeSessions([self.application])
        with patch.object(ops, '_account', return_value='acc2'):
            ops.sync(self.root, transport_factory=sessions)
        row = self.store.db.execute('SELECT * FROM sample_sync').fetchone()
        self.assertEqual(row['complete'], 1)
        self.assertEqual(row['binding_fingerprint'], digest(BINDING))
        self.assertEqual(row['contract_fingerprint'], digest(contract()))
        self.assertEqual(sessions.sessions[0]['expected_binding'], BINDING)

    def test_changed_binding_cannot_create_current_complete_coverage(self):
        sessions = FakeSessions([self.application])
        sessions.binding = BINDING | {'identitySha256': 'different-generation'}
        with patch.object(ops, '_account', return_value='acc2'):
            with self.assertRaisesRegex(CycleError, 'provenance_mismatch'):
                ops.sync(self.root, transport_factory=sessions)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM sample_sync').fetchone()[0], 0)
        with self.assertRaisesRegex(CycleError, 'provenance_mismatch'):
            ops.store_records(self.store, 'br', 'acc2', [self.application], evidence_ref='bad-binding', complete=True,
                              binding=sessions.binding, contract=contract())

    def test_old_sync_rows_migrate_with_null_provenance(self):
        self.store.db.execute('DROP TABLE sample_sync')
        self.store.db.execute('CREATE TABLE sample_sync(id TEXT PRIMARY KEY,market TEXT,account_name TEXT,observed REAL,evidence_ref TEXT,record_count INTEGER,complete INTEGER)')
        self.store.db.execute("INSERT INTO sample_sync VALUES('old','br','acc2',1,'old-evidence',0,1)")
        ops.ensure_schema(self.store)
        row = self.store.db.execute('SELECT * FROM sample_sync').fetchone()
        self.assertIsNone(row['binding_fingerprint'])
        self.assertIsNone(row['contract_fingerprint'])

    def test_complete_sync_members_cover_only_current_response_keep_history(self):
        from lib.second_cycle import digest
        ops.store_records(self.store, 'br', 'acc2', [self.application], evidence_ref='old-app', complete=True)
        current = self.application | {'applicationId': '102', 'oecId': '202', 'platformCreatorId': '302'}
        sessions = FakeSessions([current])
        with patch.object(ops, '_account', return_value='acc2'):
            result = ops.sync(self.root, transport_factory=sessions)
        current_rows = self.store.db.execute('SELECT a.application_id FROM sample_sync_member m JOIN sample_application a ON a.application_id=m.application_id AND a.market=? WHERE m.sync_id=?', ('br', result['syncId'])).fetchall()
        self.assertEqual([r[0] for r in current_rows], ['102'])
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM sample_application').fetchone()[0], 2)
        sessions.records = []
        with patch.object(ops, '_account', return_value='acc2'):
            empty = ops.sync(self.root, transport_factory=sessions)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM sample_sync_member WHERE sync_id=?', (empty['syncId'],)).fetchone()[0], 0)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM sample_application').fetchone()[0], 2)

    def test_unknown_checks_have_persistent_spacing_and_finite_budget(self):
        intent, _ = self.prepare()
        intent = ops._begin(self.store, intent, self.application)
        now = [time.time() + 60]
        self.store.clock = lambda: now[0]
        sessions = FakeSessions([self.application])
        for attempt in range(1, ops.MAX_UNKNOWN_CHECKS + 1):
            result = ops.reconcile(self.store, self.root, intent, transport_factory=sessions)
            self.assertEqual(len(sessions.sessions), attempt)
            self.assertEqual(self.store.db.execute('SELECT count(*) FROM sample_reconcile_attempt').fetchone()[0], attempt)
            if attempt < ops.MAX_UNKNOWN_CHECKS:
                self.assertEqual(result['state'], 'unknown')
                self.assertEqual(ops.reconcile(self.store, self.root, intent, transport_factory=sessions)['state'], 'waiting_reconciliation')
                self.assertEqual(len(sessions.sessions), attempt)
            now[0] += 60
        self.assertEqual(result['state'], 'quarantined_unknown')
        self.assertEqual(ops.reconcile(self.store, self.root, intent, transport_factory=sessions)['state'], 'quarantined_unknown')
        self.assertEqual(len(sessions.sessions), ops.MAX_UNKNOWN_CHECKS)
        self.assertFalse(sessions.writes)
        original = self.store.db.execute('SELECT * FROM sample_review_intent').fetchone()
        self.assertEqual(original['request_ref'], intent['request_ref'])
        with self.assertRaisesRegex(CycleError, 'duplicate_intent|duplicate_application'):
            self.prepare(self.application | {'applicationId': '102'})
        with self.assertRaisesRegex(CycleError, 'quota_reserved'):
            self.prepare(self.application | {'applicationId': '103', 'oecId': '203', 'platformCreatorId': '303'})

    def test_failed_reads_spend_persisted_unknown_budget_before_network(self):
        intent, _ = self.prepare()
        intent = ops._begin(self.store, intent, self.application)
        now = [time.time() + 60]
        self.store.clock = lambda: now[0]
        sessions = FakeSessions([self.application])
        sessions.read_failure = TimeoutError('test read timeout')
        for attempt in range(1, ops.MAX_UNKNOWN_CHECKS + 1):
            result = ops.reconcile(self.store, self.root, intent, transport_factory=sessions)
            row = self.store.db.execute('SELECT outcome FROM sample_reconcile_attempt WHERE attempt_number=?', (attempt,)).fetchone()
            self.assertEqual(row['outcome'], 'read_failed:TimeoutError')
            now[0] += 60
        self.assertEqual(result['state'], 'quarantined_unknown')
        self.assertFalse(sessions.writes)

    def test_later_normal_sync_passively_confirms_quarantined_original_intent(self):
        intent, _ = self.prepare()
        ops._begin(self.store, intent, self.application)
        self.store.db.execute("UPDATE sample_review_intent SET state='quarantined_unknown' WHERE id=?", (intent['id'],))
        wrong = self.application | {'state': 'approved', 'platformCreatorId': '999'}
        ops.store_records(self.store, 'br', 'acc2', [wrong], evidence_ref='wrong-creator')
        self.assertEqual(ops._intent(self.store, intent['id'])['state'], 'quarantined_unknown')
        exact = self.application | {'state': 'approved'}
        sessions = FakeSessions([exact])
        with patch.object(ops, '_account', return_value='acc2'):
            ops.sync(self.root, transport_factory=sessions)
        self.assertEqual(ops._intent(self.store, intent['id'])['state'], 'goal_observed')
        self.assertFalse(sessions.writes)

    def setup_canary(self):
        from lib.sample_cost_policy import POLICY_PATH
        path = self.root / POLICY_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(seller_policy()))
        candidate = canary_contract()
        ops.record_capability(self.store, 'br', 'acc2', 'canary', 'observed:pending-ui', contract=candidate, binding=BINDING)
        self.application = normalize_record(br_raw(), contract=candidate)
        self.offer.update(pid=self.application['pid'], campaignId=self.application['campaignId'])
        return candidate

    def test_arming_canary_keeps_existing_authorization_and_has_no_review_success_evidence(self):
        from lib.sample_cost_policy import POLICY_PATH
        path = self.root / POLICY_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(seller_policy()))
        ops.record_capability(self.store, 'br', 'acc2', 'readonly', 'read-evidence', contract=read_contract(), binding=BINDING)
        ops.authorize(self.store, 'br', False, 'test-stop-canary', root=self.root)
        cap = ops.arm_canary(self.store, self.root, 'br', 'acc2', pending_ui_evidence_ref='observed:pending-ui', request_candidate_ref='historical:review-source')
        self.assertEqual(cap['state'], 'canary')
        self.assertNotIn('review', cap['contract']['evidence'])
        self.assertFalse(first_outreach.control(self.store)['approvalsEnabled'])
        self.assertTrue(ops.arm_canary(self.store, self.root, 'br', 'acc2', pending_ui_evidence_ref='same', request_candidate_ref='same')['duplicate'])

    def test_canary_no_successful_recommendation_waits_without_a_write(self):
        self.setup_canary()
        sessions = FakeSessions([self.application])
        with patch.object(ops, '_account', return_value='acc2'):
            result = ops.run_one(self.root, transport_factory=sessions,
                                 assessor=lambda *args: {'eligible': False, 'reason': 'sample_not_recommended_by_first_outreach'})
        self.assertEqual(result['state'], 'waiting_eligible_application')
        self.assertFalse(sessions.writes)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM sample_review_intent').fetchone()[0], 0)

    def test_canary_old_unknown_same_triple_blocks_new_pending_without_write(self):
        self.setup_canary()
        older = self.application | {'applicationId': '99999', 'state': 'unknown', 'nativeStatus': 100}
        sessions = FakeSessions([self.application, older])
        result = self.run_one(sessions)
        self.assertEqual(result['state'], 'waiting_eligible_application')
        self.assertEqual(result['waiting'], [{'applicationId': self.application['applicationId'], 'reason': 'sample_duplicate_application'}])
        self.assertFalse(sessions.writes)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM sample_review_intent').fetchone()[0], 0)

    def test_canary_accepted_receipt_does_not_invent_lifecycle_or_verified_capability(self):
        candidate = self.setup_canary()
        sessions = FakeSessions([self.application], after='approved')
        result = self.run_one(sessions)
        self.assertEqual(result['state'], 'unknown')
        self.assertEqual(ops.capability(self.store, 'br', 'acc2')['state'], 'canary')
        self.assertEqual(len(sessions.writes), 1)
        intent = self.store.db.execute('SELECT * FROM sample_review_intent').fetchone()
        self.assertEqual(json.loads(intent['contract'])['costPolicy'], seller_policy())
        self.assertIsNone(json.loads(intent['application'])['paymentRequired'])
        self.assertNotIn('payment', json.loads(intent['payload']))

    def test_same_binding_canary_budget_cannot_be_rearmed_or_change_application(self):
        candidate = self.setup_canary()
        proof = self.offer['sample'] | {'pid': self.application['pid'], 'campaignId': self.application['campaignId'], 'available': True}
        intent, _ = ops._prepare(self.store, self.root, 'br', 'acc2', self.application, BINDING, contract=candidate, quota_proof=proof, assessor=self.assess)
        ops._begin(self.store, intent, self.application, root=self.root)
        self.store.db.execute("UPDATE sample_review_intent SET state='quarantined_unknown' WHERE id=?", (intent['id'],))
        other = self.application | {'applicationId': '999', 'oecId': '998', 'platformCreatorId': '997'}
        self.offer['sample']['quota'] = 100
        with self.assertRaisesRegex(CycleError, 'canary_already_started'):
            ops._prepare(self.store, self.root, 'br', 'acc2', other, BINDING, contract=candidate, quota_proof=proof, assessor=self.assess)
        self.assertEqual(self.run_one(FakeSessions([other]))['state'], 'waiting_canary_verification')

    def test_two_prepared_canaries_share_one_atomic_start_budget(self):
        candidate = self.setup_canary()
        self.offer['sample']['quota'] = 5
        proof = self.offer['sample'] | {'pid': self.application['pid'], 'campaignId': self.application['campaignId'], 'available': True}
        first, _ = ops._prepare(self.store, self.root, 'br', 'acc2', self.application, BINDING, contract=candidate, quota_proof=proof, assessor=self.assess)
        other = self.application | {'applicationId': '999', 'oecId': '998', 'platformCreatorId': '997'}
        second, _ = ops._prepare(self.store, self.root, 'br', 'acc2', other, BINDING, contract=candidate, quota_proof=proof, assessor=self.assess)
        ops._begin(self.store, first, self.application, root=self.root)
        with CycleStore(self.path) as another:
            with self.assertRaisesRegex(CycleError, 'canary_already_started'):
                ops._begin(another, second, other, root=self.root)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM sample_review_intent WHERE started IS NOT NULL').fetchone()[0], 1)

    def test_canary_pause_and_requeued_started_intent_cannot_send(self):
        candidate = self.setup_canary()
        sessions = FakeSessions([self.application])
        ops.authorize(self.store, 'br', False, 'stop-canary-before-write', root=self.root)
        self.assertEqual(self.run_one(sessions)['state'], 'paused')
        self.assertFalse(sessions.sessions)
        ops.authorize(self.store, 'br', True, 'start-canary-again', root=self.root)
        proof = self.offer['sample'] | {'pid': self.application['pid'], 'campaignId': self.application['campaignId'], 'available': True}
        intent, _ = ops._prepare(self.store, self.root, 'br', 'acc2', self.application, BINDING, contract=candidate, quota_proof=proof, assessor=self.assess)
        ops._begin(self.store, intent, self.application, root=self.root)
        self.store.db.execute("UPDATE sample_review_intent SET state='ready' WHERE id=?", (intent['id'],))
        with self.assertRaisesRegex(CycleError, 'already_attempted'):
            ops._begin(self.store, intent, self.application, root=self.root)
        self.assertFalse(sessions.writes)

    def test_cost_policy_change_between_read_and_permit_prevents_canary_write(self):
        from lib.sample_cost_policy import POLICY_PATH
        self.setup_canary()
        path = self.root / POLICY_PATH
        sessions = FakeSessions([self.application], stopped_on_write=lambda: path.write_text(json.dumps(seller_policy() | {'revision': 2})))
        with self.assertRaisesRegex(CycleError, 'cost_policy_changed'):
            self.run_one(sessions)
        self.assertFalse(sessions.writes)
        self.assertEqual(ops.status(self.root)['counts'], {'ready': 1})

    def test_canary_promotion_requires_accepted_receipt_and_actual_exact_final_read(self):
        candidate = self.setup_canary()
        proof = self.offer['sample'] | {'pid': self.application['pid'], 'campaignId': self.application['campaignId'], 'available': True}
        intent, _ = ops._prepare(self.store, self.root, 'br', 'acc2', self.application, BINDING, contract=candidate, quota_proof=proof, assessor=self.assess)
        ops._begin(self.store, intent, self.application, root=self.root)
        from lib.sample_transport import READ_PATHS
        verified = contract() | {'recordPaths': dict(READ_PATHS), 'statusMap': {'10': 'pending', '100': 'completed'}, 'costPolicy': seller_policy()}
        sessions = FakeSessions([self.application | {'state': 'completed', 'nativeStatus': 100}])
        with self.assertRaisesRegex(CycleError, 'review_unconfirmed'):
            ops.promote_canary(self.root, 'br', 'acc2', verified, transport_factory=sessions)
        self.assertFalse(sessions.sessions)
        self.store.db.execute("UPDATE sample_review_intent SET receipt=? WHERE id=?", (encoded({'status': 'accepted', 'http': 200, 'code': 0, 'evidenceRef': 'observed:review'}), intent['id']))
        for wrong in (verified | {'statusMap': {'10': 'approved', '99': 'pending'}},
                      verified | {'recordPaths': verified['recordPaths'] | {'platformCreatorId': 'creator_info.oec_id'}}):
            with self.assertRaisesRegex(CycleError, 'lifecycle_contract_mismatch'):
                ops.promote_canary(self.root, 'br', 'acc2', wrong, transport_factory=sessions)
        sessions.records = [self.application | {'state': 'approved', 'nativeStatus': 10}]
        self.assertEqual(ops.promote_canary(self.root, 'br', 'acc2', verified, transport_factory=sessions)['state'], 'waiting_canary_verification')
        sessions.records = [self.application | {'state': 'pending'}]
        self.assertEqual(ops.promote_canary(self.root, 'br', 'acc2', verified, transport_factory=sessions)['state'], 'waiting_canary_verification')
        self.assertEqual(ops.capability(self.store, 'br', 'acc2')['state'], 'canary')
        sessions.records = [self.application | {'state': 'completed', 'nativeStatus': 100, 'platformCreatorId': 'wrong'}]
        self.assertEqual(ops.promote_canary(self.root, 'br', 'acc2', verified, transport_factory=sessions)['state'], 'waiting_canary_verification')
        sessions.records = [self.application | {'state': 'completed', 'nativeStatus': 100}]
        self.assertEqual(ops.promote_canary(self.root, 'br', 'acc2', verified, transport_factory=sessions)['state'], 'verified')
        self.assertEqual(ops.capability(self.store, 'br', 'acc2')['state'], 'verified')
        self.assertFalse(sessions.writes)

    def test_sync_automatically_promotes_real_canary_effect_and_tracks_publication_after_stop(self):
        self.setup_canary()
        sessions = FakeSessions([])
        sessions.raw_records = [br_raw() | {'is_posted': False, 'video_count': 0, 'room_count': 0}]
        sessions.platform_counts = {'TO_REVIEW_1': 1}
        sessions.after_native = 40
        sessions.receipt = {'status': 'accepted', 'http': 200, 'code': 0, 'evidenceRef': 'observed:real-review-receipt'}
        with patch.object(ops, 'observed_status_evidence', return_value=status_evidence()), patch.object(ops, '_account', return_value='acc2'):
            result = ops.sync(self.root, transport_factory=sessions)
        self.assertEqual(result['reviewCapability'], 'canary')
        self.assertEqual(ops.capability(self.store, 'br', 'acc2')['contract']['statusMap']['40'], 'content_pending')
        self.assertEqual(self.run_one(sessions)['state'], 'approved')
        self.assertEqual(ops.capability(self.store, 'br', 'acc2')['state'], 'verified')
        self.assertEqual(len(sessions.writes), 1)
        self.assertEqual(ops.tracking(self.store)[0]['platformStage'], 'content_pending')
        self.assertTrue(ops.has_followups(self.root))
        ops.authorize(self.store, 'br', False, 'stop-new-posts-only', root=self.root)
        sessions.raw_records[0].update(sample_status=99, is_posted=True, video_count=1)
        sessions.platform_counts = {'UNOBSERVED_PHASE': 1}
        with patch.object(ops, '_account', return_value='acc2'):
            ops.sync(self.root, transport_factory=sessions)
        unknown_phase = ops.tracking(self.store)[0]
        self.assertEqual((unknown_phase['reviewState'], unknown_phase['platformStage'], unknown_phase['nativeStatus']), ('approved', 'unknown', 99))
        self.assertTrue(unknown_phase['isPosted'])
        self.assertTrue(ops.has_followups(self.root))
        sessions.raw_records[0].update(sample_status=100, is_posted=True, video_count=2, room_count=0)
        sessions.platform_counts = {'COMPLETED': 1}
        with patch.object(ops, '_account', return_value='acc2'):
            ops.sync(self.root, transport_factory=sessions)
            count_before = self.store.db.execute("SELECT count(*) FROM sample_review_check WHERE json_extract(payload,'$.kind')='lifecycle'").fetchone()[0]
            ops.sync(self.root, transport_factory=sessions)
        track = ops.tracking(self.store)[0]
        self.assertEqual((track['platformStage'], track['isPosted'], track['videoCount']), ('completed', True, 2))
        self.assertFalse(ops.has_followups(self.root))
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM sample_review_check WHERE json_extract(payload,'$.kind')='lifecycle'").fetchone()[0], count_before)
        self.assertEqual(len(sessions.writes), 1)

    def test_code_zero_without_original_positive_lifecycle_does_not_auto_promote(self):
        self.setup_canary()
        sessions = FakeSessions([])
        sessions.raw_records = [br_raw()]
        sessions.platform_counts = {'TO_REVIEW_1': 1}
        sessions.after_native = 10
        sessions.receipt = {'status': 'accepted', 'http': 200, 'code': 0, 'evidenceRef': 'observed:receipt'}
        with patch.object(ops, 'observed_status_evidence', return_value=status_evidence()), patch.object(ops, '_account', return_value='acc2'):
            ops.sync(self.root, transport_factory=sessions)
        self.assertEqual(self.run_one(sessions)['state'], 'unknown')
        self.assertEqual(ops.capability(self.store, 'br', 'acc2')['state'], 'canary')
        self.assertEqual(len(sessions.writes), 1)

    def test_original_identity_loss_is_explicit_and_does_not_rebind_the_intent(self):
        intent, _ = self.prepare()
        intent = ops._begin(self.store, intent, self.application)
        self.store.db.execute('UPDATE sample_review_check SET observed=observed-61')
        self.store.db.execute('UPDATE sample_review_intent SET started=started-61')
        @contextmanager
        def lost(*args, **kwargs):
            raise CycleError('sample_original_identity_changed')
            yield
        result = ops.reconcile(self.store, self.root, intent, transport_factory=lost)
        self.assertEqual(result['state'], 'waiting_original_identity')
        unchanged = ops._intent(self.store, intent['id'])
        self.assertEqual(unchanged['binding'], intent['binding'])
        self.assertEqual(unchanged['request_ref'], intent['request_ref'])
        self.assertEqual(unchanged['state'], 'quarantined_unknown')
        self.assertTrue(ops.has_followups(self.root))

    def test_lost_canary_receipt_goal_observed_can_try_next_new_application_without_replay(self):
        self.setup_canary()
        sessions = FakeSessions([])
        sessions.raw_records = [br_raw()]
        sessions.platform_counts = {'TO_REVIEW_1': 1}
        sessions.failure = TimeoutError('lost original review response')
        with patch.object(ops, 'observed_status_evidence', return_value=status_evidence()), patch.object(ops, '_account', return_value='acc2'):
            ops.sync(self.root, transport_factory=sessions)
        with self.assertRaises(TimeoutError):
            self.run_one(sessions)
        original = dict(self.store.db.execute('SELECT * FROM sample_review_intent').fetchone())
        sessions.failure = None
        sessions.raw_records[0]['sample_status'] = 40
        sessions.platform_counts = {'CONTENT_PENDING': 1}
        with patch.object(ops, 'observed_status_evidence', return_value=status_evidence()), patch.object(ops, '_account', return_value='acc2'):
            result = ops.sync(self.root, transport_factory=sessions)
        self.assertFalse(result['promoted'])
        settled = ops._intent(self.store, original['id'])
        self.assertEqual(settled['state'], 'goal_observed')
        self.assertIsNone(settled['receipt'])
        self.assertEqual(settled['request_ref'], original['request_ref'])
        self.assertEqual(ops.capability(self.store, 'br', 'acc2')['state'], 'canary')
        self.assertTrue(ops.tracking(self.store)[0]['possibleExternalCompletion'])
        self.assertFalse(ops.tracking(self.store)[0]['receiptConfirmed'])
        next_raw = br_raw(1)
        next_raw['product_info']['product_id'] = self.application['pid']
        next_raw['campaign_info']['campaign_id'] = self.application['campaignId']
        sessions.raw_records.append(next_raw)
        sessions.after_native = 40
        sessions.receipt = {'status': 'accepted', 'http': 200, 'code': 0, 'evidenceRef': 'observed:second-review-response'}
        self.assertEqual(self.run_one(sessions)['state'], 'approved')
        self.assertEqual(ops.capability(self.store, 'br', 'acc2')['state'], 'verified')
        self.assertEqual(len(sessions.writes), 2)
        self.assertNotEqual(sessions.writes[0]['creator_id'], sessions.writes[1]['creator_id'])
        self.assertEqual(ops._intent(self.store, original['id'])['state'], 'goal_observed')
        self.assertIsNone(ops._intent(self.store, original['id'])['receipt'])

    def test_ambiguous_code_zero_and_positive_lifecycle_do_not_claim_review_permission(self):
        self.setup_canary()
        sessions = FakeSessions([])
        sessions.raw_records = [br_raw()]
        sessions.platform_counts = {'TO_REVIEW_1': 1}
        sessions.after_native = 40
        sessions.receipt = {'status': 'unknown', 'http': 200, 'code': 0, 'ambiguous': True, 'evidenceRef': 'observed:ambiguous-response'}
        with patch.object(ops, 'observed_status_evidence', return_value=status_evidence()), patch.object(ops, '_account', return_value='acc2'):
            ops.sync(self.root, transport_factory=sessions)
        self.assertEqual(self.run_one(sessions)['state'], 'goal_observed')
        self.assertEqual(ops.capability(self.store, 'br', 'acc2')['state'], 'canary')
        self.assertFalse(ops.tracking(self.store)[0]['receiptConfirmed'])
        self.assertEqual(len(sessions.writes), 1)

    def test_strict_delta_automatically_proves_ready_shipping_completion_without_ui_enum_guesses(self):
        self.setup_canary()
        sessions = FakeSessions([])
        sessions.raw_records = [br_raw() | {'is_posted': False, 'video_count': 0, 'room_count': 0}]
        sessions.counter_by_native = {10: 'TO_REVIEW_1', 20: 'READY_TO_SHIP_1', 30: 'SHIPPED', 100: 'COMPLETED'}
        sessions.after_native = 20
        sessions.receipt = {'status': 'accepted', 'http': 200, 'code': 0, 'evidenceRef': 'observed:review-accepted'}
        with patch.object(ops, 'observed_status_evidence', return_value=None), patch.object(ops, '_account', return_value='acc2'):
            ops.sync(self.root, transport_factory=sessions)
        self.assertEqual(self.run_one(sessions)['state'], 'approved')
        current = ops.capability(self.store, 'br', 'acc2')
        self.assertEqual(current['state'], 'verified')
        self.assertEqual(current['contract']['statusMap'], {'10': 'pending', '20': 'ready_to_ship'})
        self.assertEqual(ops.tracking(self.store)[0]['platformStage'], 'ready_to_ship')
        ops.authorize(self.store, 'br', False, 'read-followup-only', root=self.root)
        sessions.raw_records[0]['sample_status'] = 30
        with patch.object(ops, '_account', return_value='acc2'):
            ops.sync(self.root, transport_factory=sessions)
        self.assertEqual(ops.tracking(self.store)[0]['platformStage'], 'shipped')
        self.assertTrue(ops.has_followups(self.root))
        sessions.raw_records[0].update(sample_status=100, is_posted=True, video_count=1)
        with patch.object(ops, '_account', return_value='acc2'):
            ops.sync(self.root, transport_factory=sessions)
        final = ops.tracking(self.store)[0]
        self.assertEqual((final['platformStage'], final['isPosted'], final['videoCount']), ('completed', True, 1))
        self.assertFalse(ops.has_followups(self.root))
        self.assertEqual(len(sessions.writes), 1)
        mappings = self.store.db.execute("SELECT payload FROM sample_review_check WHERE json_extract(payload,'$.kind')='status_mapping'").fetchall()
        self.assertEqual(len(mappings), 3)
        for row in mappings:
            observation = json.loads(row[0])['observation']
            self.assertEqual(observation['sourceKind'], 'single_application_delta')
            self.assertEqual(observation['delta']['before']['binding'], BINDING)
            self.assertEqual(observation['delta']['after']['binding'], BINDING)

    def test_concurrent_scope_change_does_not_learn_canary_lifecycle_or_promote(self):
        self.setup_canary()
        sessions = FakeSessions([])
        sessions.raw_records = [br_raw(), br_raw(1)]
        sessions.counter_by_native = {10: 'TO_REVIEW_1', 20: 'READY_TO_SHIP_1'}
        sessions.after_native = 20  # The fake changes both applications in the same response.
        sessions.receipt = {'status': 'accepted', 'http': 200, 'code': 0, 'evidenceRef': 'observed:accepted'}
        with patch.object(ops, 'observed_status_evidence', return_value=None), patch.object(ops, '_account', return_value='acc2'):
            ops.sync(self.root, transport_factory=sessions)
        self.assertEqual(self.run_one(sessions)['state'], 'unknown')
        current = ops.capability(self.store, 'br', 'acc2')
        self.assertEqual((current['state'], current['contract']['statusMap']), ('canary', {'10': 'pending'}))
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM sample_review_check WHERE json_extract(payload,'$.kind')='status_mapping'").fetchone()[0], 0)
        self.assertEqual(len(sessions.writes), 1)

    def test_identity_rotation_revalidates_new_reads_without_settling_original_unknown(self):
        from lib.sample_transport import extend_canary_contract
        candidate = self.setup_canary()
        candidate = extend_canary_contract(candidate, status_evidence())
        ops.record_capability(self.store, 'br', 'acc2', 'canary', 'old-evidence', contract=candidate, binding=BINDING)
        old_application = normalize_record(br_raw(), contract=candidate)
        proof = self.offer['sample'] | {'pid': old_application['pid'], 'campaignId': old_application['campaignId'], 'available': True}
        intent, _ = ops._prepare(self.store, self.root, 'br', 'acc2', old_application, BINDING, contract=candidate, quota_proof=proof, assessor=self.assess)
        ops._begin(self.store, intent, old_application, root=self.root)
        rotated = BINDING | {'identitySha256': 'new-normal-login'}
        fresh = FakeSessions([normalize_record(br_raw(status=40), contract=read_contract(rotated))])
        fresh.binding = rotated
        fresh.platform_counts = {'CONTENT_PENDING': 1}
        @contextmanager
        def revalidating(*args, **options):
            if options.get('expected_binding') == BINDING:
                raise CycleError('sample_original_identity_changed')
            options['contract'] = read_contract(rotated)
            with fresh(*args, **options) as adapter:
                yield adapter
        with patch.object(ops, 'observed_status_evidence', return_value=None), patch.object(ops, '_account', return_value='acc2'):
            result = ops.sync(self.root, transport_factory=revalidating)
        self.assertTrue(result['readerRebound'])
        current = ops.capability(self.store, 'br', 'acc2')
        self.assertEqual((current['state'], current['binding']), ('canary', rotated))
        self.assertEqual(current['contract']['statusEvidence']['sourceBinding'], BINDING)
        unchanged = ops._intent(self.store, intent['id'])
        self.assertEqual((unchanged['state'], unchanged['binding']), ('inflight', intent['binding']))
        self.assertFalse(fresh.writes)
        self.assertTrue(ops.read_snapshot(self.store)['complete'])
        self.store.db.execute("UPDATE sample_review_intent SET state='quarantined_unknown' WHERE id=?", (intent['id'],))
        next_record = fresh.records[0] | {'applicationId': '99999', 'oecId': '99998', 'platformCreatorId': '99997', 'state': 'pending', 'nativeStatus': 10}
        with patch.object(ops, '_account', return_value='acc2'):
            held = ops.run_one(self.root, transport_factory=FakeSessions([next_record]), assessor=self.assess)
        self.assertEqual(held['state'], 'waiting_canary_verification')

    def test_reader_rotation_cannot_cross_institution_or_swallow_verification_failure(self):
        self.setup_canary()
        original = ops.capability(self.store, 'br', 'acc2')
        changed = BINDING | {'identitySha256': 'new', 'institutionFingerprint': 'different-institution'}
        fresh = FakeSessions([normalize_record(br_raw(), contract=read_contract(changed))])
        fresh.binding = changed
        fresh.platform_counts = {'TO_REVIEW_1': 1}
        @contextmanager
        def wrong(*args, **options):
            if options.get('expected_binding'):
                raise CycleError('sample_original_identity_changed')
            options['contract'] = read_contract(changed)
            with fresh(*args, **options) as adapter:
                yield adapter
        with patch.object(ops, 'observed_status_evidence', return_value=None), patch.object(ops, '_account', return_value='acc2'):
            with self.assertRaisesRegex(CycleError, 'reader_rebind_unverified'):
                ops.sync(self.root, transport_factory=wrong)
        self.assertEqual(ops.capability(self.store, 'br', 'acc2')['binding'], original['binding'])
        @contextmanager
        def challenged(*args, **options):
            raise CycleError('sample_verification_required')
            yield
        with patch.object(ops, 'observed_status_evidence', return_value=None), patch.object(ops, '_account', return_value='acc2'):
            with self.assertRaisesRegex(CycleError, 'sample_verification_required'):
                ops.sync(self.root, transport_factory=challenged)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM sample_sync').fetchone()[0], 0)

    def test_promoted_cost_policy_contract_sync_keeps_complete_snapshot_provenance(self):
        from lib.sample_transport import READ_PATHS, OBSERVED_READ_BODY, record_contract_fingerprint
        from lib.second_cycle import digest
        candidate = self.setup_canary()
        proof = self.offer['sample'] | {'pid': self.application['pid'], 'campaignId': self.application['campaignId'], 'available': True}
        intent, _ = ops._prepare(self.store, self.root, 'br', 'acc2', self.application, BINDING,
                                contract=candidate, quota_proof=proof, assessor=self.assess)
        ops._begin(self.store, intent, self.application, root=self.root)
        self.store.db.execute('UPDATE sample_review_intent SET receipt=? WHERE id=?',
                              (encoded({'status': 'accepted', 'http': 200, 'code': 0, 'evidenceRef': 'observed:review'}), intent['id']))
        verified = contract() | {'recordPaths': dict(READ_PATHS), 'readBody': copy.deepcopy(OBSERVED_READ_BODY),
                                'readScope': 'all_states', 'statusCountersPath': 'sample_status_num_map',
                                'statusMap': {'10': 'pending', '100': 'completed'}, 'costPolicy': seller_policy()}
        terminal = normalize_record(br_raw(status=100), contract=verified)
        sessions = FakeSessions([terminal])
        sessions.platform_counts = {'COMPLETED': 1}
        self.assertEqual(ops.promote_canary(self.root, 'br', 'acc2', verified, transport_factory=sessions)['state'], 'verified')
        promoted = ops.capability(self.store, 'br', 'acc2')['contract']
        self.assertEqual(promoted['costPolicy'], seller_policy())
        with patch.object(ops, '_account', return_value='acc2'):
            result = ops.sync(self.root, transport_factory=sessions)
        row = self.store.db.execute('SELECT * FROM sample_sync WHERE id=?', (result['syncId'],)).fetchone()
        self.assertEqual(row['contract_fingerprint'], digest(promoted))
        application = json.loads(self.store.db.execute('SELECT payload FROM sample_application').fetchone()[0])
        self.assertEqual(application['contractFingerprint'], record_contract_fingerprint(promoted))
        self.assertNotEqual(application['contractFingerprint'], row['contract_fingerprint'])
        snapshot = ops.read_snapshot(self.store, account_name='acc2')
        self.assertEqual((snapshot['complete'], snapshot['total'], snapshot['syncId']), (True, 1, result['syncId']))
        self.assertEqual(snapshot['contractFingerprint'], digest(promoted))
        self.assertEqual(snapshot['platformCounts'], {'COMPLETED': 1})
        self.store.clock = lambda: row['observed']
        ops.record_capability(self.store, 'br', 'acc2', 'verified', 'policy-revision',
            contract=promoted | {'costPolicy': seller_policy() | {'revision': 2}}, binding=BINDING)
        self.assertIsNone(ops.read_snapshot(self.store, account_name='acc2'))
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
