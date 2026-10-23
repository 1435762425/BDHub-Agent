"""Either BR sender may finish the same bounded read-only recovery, never resend."""
from contextlib import contextmanager, ExitStack
import fcntl
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

from lib import first_outreach, market_send_canary
from lib.cycle_delivery import Deliveries
from lib.delivery_reconciliation import read_original
from lib.schema_migrations import apply_database
from lib.second_cycle import CycleError, CycleStore, encoded
import test_first_delivery_runtime as first_tests
from test_first_im_transport import fixture


def original_conversation(store, plan, now, *, mode='market-continuous-v1',
                          did='original-second', market='br', sender='acc1', proxy=True):
    snapshot = {'market': market, 'executionMode': mode, 'senderAccount': sender, 'senderImId': '777',
                'creatorId': did, 'oecId': '456', 'pid': '2', 'imUseEnvironmentProxy': proxy}
    store.db.execute('INSERT INTO cycle_delivery VALUES(?,?,?,?,?,?,?,?,?,?)',
                     (did, plan, did, '456', '2', did + '-source', encoded(snapshot), now, now + 1800, 'unknown'))
    for kind in ('card', 'text'):
        store.db.execute('INSERT INTO cycle_delivery_part(delivery_id,kind,request_ref,state) VALUES(?,?,?,?)',
                         (did, kind, did + '-' + kind, 'ready'))
    store.db.execute('INSERT INTO cycle_conversation_intent VALUES(?,?,?,?,?)',
                     (did, did + '-create', 'inflight', None, None))
    return did


class SharedReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.now = 1792750000.0
        path = self.root / 'var/second-cycle.sqlite'
        with CycleStore(path) as store:
            self.plan = store.plan('bjn-local-research', 'br')
        apply_database(self.root, 'second-cycle')
        self.store = CycleStore(path, lambda: self.now)
        self.deliveries = Deliveries(self.store)
        first_outreach.ensure_schema(self.store)
        self.store.db.execute('INSERT INTO first_outreach_control VALUES(?,?,?,?,?,?,?,?)',
            (self.plan, 'stopped', 0, 4, self.now, 'original-first-grant', 3, None))
        self.store.db.execute('INSERT INTO continuous_send_control VALUES(?,?,?,?,?,?,?,?,?)',
            (self.plan, 1, 0, 1, '16:30', '24:00', 'standard', 7, self.now))

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def controls(self):
        return [tuple(self.store.db.execute('SELECT * FROM ' + name).fetchone()) for name in
                ('first_outreach_control', 'continuous_send_control')]

    @contextmanager
    def platform(self, *, im_id='777'):
        calls = []
        session = Mock()
        session.initialize.return_value = {'conversations': [], 'hasMore': False, 'nextCursor': '0'}
        @contextmanager
        def readonly(_root, market, _report, **kwargs):
            self.assertEqual(market, 'br')
            self.assertIs(kwargs['read_only'], True)
            calls.append(kwargs)
            yield {'auth': SimpleNamespace(account_name='acc1', im_id=im_id), 'session': session}
        config = {'markets': {'br': {'roles': {'communications': 'acc1'}},
                              'my': {'roles': {'communications': 'acc8'}}}}
        with ExitStack() as stack:
            stack.enter_context(patch.object(market_send_canary, 'load_config', return_value=config))
            stack.enter_context(patch('lib.market_accounts.load_config', return_value=config))
            stack.enter_context(patch.object(market_send_canary, 'CycleStore',
                                             lambda path: CycleStore(path, lambda: self.now)))
            stack.enter_context(patch.object(market_send_canary, 'authenticated',
                                             side_effect=AssertionError('new send scope must not open')))
            stack.enter_context(patch('lib.market_im_runtime.authenticated', readonly))
            yield calls, session

    def tick(self, purpose='first', **kwargs):
        return market_send_canary.run(self.root, 'br', 'shared-recovery-' + purpose,
                                      canary=False, page_control=True, purpose=purpose, **kwargs)

    def test_first_and_second_share_original_budget_without_changing_authority_or_request(self):
        did = original_conversation(self.store, self.plan, self.now)
        original = self.deliveries.get(did)
        intent = self.deliveries.conversation_intent(did)
        before = self.controls()
        with self.platform() as (calls, session):
            self.assertEqual(self.tick()['state'], 'waiting_reconciliation')
            self.assertEqual(self.tick('second')['state'], 'waiting_reconciliation')
            self.assertEqual(len(calls), 1)
            self.now += 300
            result = self.tick('second')
            self.assertEqual(result['state'], 'quarantined_unknown')
            self.assertEqual(len(calls), 2)
            self.assertTrue(all(call['use_environment_proxy'] for call in calls))
            self.assertEqual(self.tick(reconcile_only=True)['state'], 'nothing_to_reconcile')
            session.create_once.assert_not_called()
        self.assertEqual((result['platformWrites'], result['realSends']), (0, 0))
        self.assertEqual(self.deliveries.get(did)['snapshot'], original['snapshot'])
        self.assertEqual(self.deliveries.conversation_intent(did), intent)
        self.assertEqual([p['state'] for p in self.deliveries.get(did)['parts']], ['ready', 'ready'])
        attempts = list(self.store.db.execute("SELECT payload FROM cycle_delivery_check WHERE delivery_id=? AND kind='verification_attempt_conversation'", (did,)))
        self.assertEqual([json.loads(row[0])['attempt'] for row in attempts], [1, 2])
        self.assertEqual(self.controls(), before)

    def test_second_can_reconcile_first_after_plan_and_both_senders_are_paused(self):
        did = original_conversation(self.store, self.plan, self.now, mode='market-first-v1', proxy=False)
        self.store.control(self.plan, 'operator-plan-pause', 1, 'paused')
        before = self.controls()
        with self.platform() as (calls, _session):
            result = self.tick('second')
            self.assertEqual(result['deliveryId'], did)
            self.assertEqual(result['state'], 'waiting_reconciliation')
            self.assertIs(calls[0]['use_environment_proxy'], False)
            self.now += 300
            self.assertEqual(self.tick()['state'], 'quarantined_unknown')
            self.assertEqual(self.tick(reconcile_only=True)['state'], 'nothing_to_reconcile')
            for purpose in ('first', 'second'):
                with self.assertRaisesRegex(CycleError, 'plan_paused'):
                    self.tick(purpose)
        self.assertEqual(self.store._plan(self.plan)['state'], 'paused')
        self.assertEqual(self.controls(), before)

    def test_another_market_or_another_plan_is_never_reconciled(self):
        other_market = self.store.plan('bjn-local-research', 'my')
        other_plan = self.store.plan('other-institution', 'br')
        for plan, market, did in ((other_market, 'my', 'other-market'), (other_plan, 'br', 'other-plan')):
            original_conversation(self.store, plan, self.now, did=did, market=market)
        with self.platform() as (calls, _session):
            self.assertEqual(self.tick(reconcile_only=True)['state'], 'nothing_to_reconcile')
            self.assertEqual(calls, [])
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM cycle_delivery_check').fetchone()[0], 0)

    def test_shared_executor_lock_prevents_other_purpose_recovery_during_original_execution(self):
        original_conversation(self.store, self.plan, self.now)
        with (self.root / 'var/delivery-executor-br.lock').open('a') as lock, self.platform() as (calls, _session):
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(CycleError, 'delivery_executor_busy'):
                self.tick('second')
            self.assertEqual(calls, [])
            self.assertEqual(self.store.db.execute('SELECT count(*) FROM cycle_delivery_check').fetchone()[0], 0)
            fcntl.flock(lock, fcntl.LOCK_UN)
            self.assertEqual(self.tick()['state'], 'waiting_reconciliation')

    def test_original_market_and_account_cannot_change_before_readonly_auth(self):
        for changed, code in (({'market': 'my'}, 'reconciliation_original_market_changed'),
                               ({'senderAccount': 'acc2'}, 'reconciliation_original_account_changed')):
            with self.subTest(changed=changed), self.platform() as (calls, _session):
                with self.assertRaisesRegex(CycleError, code):
                    read_original(self.root, 'br', {'snapshot': {'market': 'br', 'senderAccount': 'acc1',
                        'senderImId': '777', 'oecId': '456', **changed}}, 'conversation', {'state': 'inflight'})
                self.assertEqual(calls, [])

    def test_original_im_identity_is_checked_before_any_platform_read(self):
        with self.platform(im_id='replacement') as (_calls, session):
            with self.assertRaisesRegex(CycleError, 'reconciliation_original_identity_changed'):
                read_original(self.root, 'br', {'snapshot': {'market': 'br', 'senderAccount': 'acc1',
                    'senderImId': '777', 'oecId': '456'}}, 'conversation', {'state': 'inflight'})
            session.initialize.assert_not_called()

    def test_first_recovery_completes_only_original_second_canary_authority_and_capability(self):
        from lib.account_identity import current_generation, publish_generation
        from lib.market_send_control import control, mutate, page_start_authorization
        publish_generation(self.store, market='br', account='acc1', role='communications', reason='baseline',
            identity={'browserRef': 'browser', 'httpRef': 'http', 'imRef': 'im', 'institutionFingerprint': 'agency'},
            capabilities={'message_send': {'state': 'not_tested'}})
        with patch('lib.market_send_control.require_send_template_approval'):
            mutate(self.store, ROOT, 'br', action='start', request_id='original-second-canary-grant', expected_revision=7)
        first_before = tuple(self.store.db.execute('SELECT * FROM first_outreach_control').fetchone())
        snapshot = {'market': 'br', 'executionMode': 'market-canary-v1', 'senderAccount': 'acc1',
                    'senderImId': '777', 'pageStart': page_start_authorization(self.store, 'br')}
        did = 'original-second-canary'
        self.store.db.execute('INSERT INTO cycle_delivery VALUES(?,?,?,?,?,?,?,?,?,?)',
            (did, self.plan, did, '456', '2', did + '-source', encoded(snapshot), self.now, self.now + 1800, 'unknown'))
        for kind, state in (('card', 'confirmed'), ('text', 'unknown')):
            self.store.db.execute('INSERT INTO cycle_delivery_part(delivery_id,kind,request_ref,state,started) VALUES(?,?,?,?,?)',
                (did, kind, did + '-' + kind, state, self.now))
        with self.platform(), patch('lib.delivery_reconciliation.read_original', return_value={
                'status': 'confirmed', 'messageId': 'original-text-id', 'evidenceRef': 'original-text-proof'}):
            result = self.tick()
        self.assertTrue(result['canaryCompleted'])
        self.assertTrue(result['canaryStopRequested'])
        self.assertEqual((result['platformWrites'], result['realSends']), (0, 0))
        self.assertEqual(current_generation(self.store, 'br', 'acc1')['capabilities']['message_send']['state'], 'verified')
        self.assertTrue(control(self.store, 'br')['stopRequested'])
        self.assertEqual(tuple(self.store.db.execute('SELECT * FROM first_outreach_control').fetchone()), first_before)

    def test_isolating_second_unknown_allows_next_authorized_first_creator_only(self):
        with fixture(first_tests.FirstDeliveryRuntimeTests) as case:
            case.prepare(); case.start()
            did = original_conversation(case.s, case.plan, case.now)
            read = Mock(return_value={'status': 'result_unknown'})
            with case.runtime(), patch('lib.delivery_reconciliation.read_original', read):
                first = market_send_canary.run(case.root, 'br', 'shared-first-1', purpose='first')
                self.assertEqual(first['deliveryId'], did)
                self.assertEqual(case.sent, [])
                case.now += 300
                self.assertEqual(market_send_canary.run(case.root, 'br', 'shared-first-2', purpose='first')['state'],
                                 'quarantined_unknown')
                result = market_send_canary.run(case.root, 'br', 'shared-first-3', purpose='first')
            self.assertEqual(result['state'], 'confirmed')
            self.assertNotEqual(result['deliveryId'], did)
            self.assertEqual([kind for kind, _ref in case.sent], ['card', 'text'])
            self.assertEqual(case.d.get(did)['state'], 'quarantined_unknown')
            self.assertEqual(read.call_count, 2)


if __name__ == '__main__': unittest.main()
