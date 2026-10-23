"""The BR first-outreach connection is frozen without changing existing senders."""
from contextlib import contextmanager
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

from lib import market_im_runtime, market_send_canary
from lib.delivery_reconciliation import read_original
from lib.second_cycle import digest
import test_first_delivery_runtime as first_tests
import test_market_canary_binding as second_tests


@contextmanager
def fixture(case_type):
    case = case_type('runTest')
    case.setUp()
    try:
        yield case
    finally:
        case.tearDown()


@contextmanager
def capture_connection():
    original = market_send_canary.authenticated
    calls = []
    @contextmanager
    def authenticate(*args, **kwargs):
        calls.append(kwargs)
        with original(*args, **kwargs) as value:
            yield value
    with patch.object(market_send_canary, 'authenticated', authenticate):
        yield calls


class FirstImTransportTests(unittest.TestCase):
    def test_first_uses_direct_connection_and_freezes_it_with_the_original_sender(self):
        with fixture(first_tests.FirstDeliveryRuntimeTests) as case:
            case.prepare(); case.start()
            with case.runtime(), capture_connection() as calls:
                result = market_send_canary.run(case.root, 'br', 'first-direct-transport',
                                               canary=False, page_control=True, purpose='first')
            self.assertEqual(result['state'], 'confirmed')
            self.assertIs(calls[0]['use_environment_proxy'], False)
            snapshot = json.loads(case.s.db.execute('SELECT snapshot FROM cycle_delivery WHERE id=?',
                                                   (result['deliveryId'],)).fetchone()[0])
            self.assertIs(snapshot['imUseEnvironmentProxy'], False)
            self.assertEqual((snapshot['senderAccount'], snapshot['senderImId']), ('acc1', '777'))

    def test_other_market_second_sender_keeps_environment_proxy_and_no_new_frozen_override(self):
        with fixture(second_tests.MarketCanaryBindingTests) as case:
            with case.runtime(candidate=case.candidate(2)), capture_connection() as calls:
                result = case.tick()
            self.assertEqual(result['state'], 'confirmed')
            self.assertIs(calls[0]['use_environment_proxy'], True)
            snapshot = case.deliveries.get(result['deliveryId'])['snapshot']
            self.assertNotIn('imUseEnvironmentProxy', snapshot)

    def test_new_br_second_freezes_direct_but_legacy_ready_second_keeps_environment_proxy(self):
        for existing, expected in ((False, False), (True, True)):
            with self.subTest(existing=existing), fixture(first_tests.FirstDeliveryRuntimeTests) as case:
                case.prepare(); case.start()
                candidate = case.get_candidate()
                offer = case.offer(stock='101', rating='3.8', endAt=case.now + 46 * 86400)
                case.s.publish(case.plan, 'catalog', case.now + 1, [offer])
                candidate.update(offer=offer, offerFingerprint=digest(offer), conversationId='999',
                    executionMode='market-continuous-v1', outreachPurpose='second',
                    source={'sourceId': 'br-second-transport', 'sourceClass': 'A'},
                    senderAccount='acc1', senderImId='777')
                if existing:
                    original = case.d.prepare(case.plan, candidate)
                with case.runtime(), capture_connection() as calls, \
                     patch.object(market_send_canary, '_candidate', return_value=candidate), \
                     patch.object(market_send_canary, '_dispatch_allowed'), \
                     patch.object(market_send_canary, 'require_send_template_approval'), \
                     patch('lib.market_send_control.settle_confirmed_canaries', return_value=[]), \
                     patch('lib.market_send_control.unbound_confirmed_canary', return_value=None):
                    result = market_send_canary.run(case.root, 'br', 'br-second-transport',
                                                   canary=False, page_control=True)
                self.assertEqual(result['state'], 'confirmed')
                self.assertIs(calls[0]['use_environment_proxy'], expected)
                snapshot = case.d.get(result['deliveryId'])['snapshot']
                if existing:
                    self.assertEqual(snapshot, original['snapshot'])
                    self.assertNotIn('imUseEnvironmentProxy', snapshot)
                else:
                    self.assertIs(snapshot['imUseEnvironmentProxy'], False)

    def test_existing_first_delivery_keeps_its_frozen_connection_or_legacy_default(self):
        for frozen, expected in (({'imUseEnvironmentProxy': False}, False), ({}, True)):
            with self.subTest(frozen=frozen), fixture(first_tests.FirstDeliveryRuntimeTests) as case:
                case.prepare(); case.start()
                candidate = case.get_candidate() | {'conversationId': '999', 'senderAccount': 'acc1',
                                                    'senderImId': '777', **frozen}
                original = case.d.prepare(case.plan, candidate)
                with case.runtime(), capture_connection() as calls:
                    result = market_send_canary.run(case.root, 'br', 'first-resume-transport',
                                                   canary=False, page_control=True, purpose='first')
                self.assertEqual(result['deliveryId'], original['id'])
                self.assertIs(calls[0]['use_environment_proxy'], expected)
                self.assertEqual(case.d.get(original['id'])['snapshot'], original['snapshot'])

    def test_original_reconciliation_keeps_frozen_connection_and_legacy_default(self):
        for frozen, expected in (({'imUseEnvironmentProxy': False}, False),
                                 ({'imUseEnvironmentProxy': True}, True), ({}, True)):
            with self.subTest(frozen=frozen):
                session = Mock()
                session.initialize.return_value = {'conversations': [], 'hasMore': False, 'nextCursor': '0'}
                calls = []
                @contextmanager
                def authenticated(_root, _market, _report, **kwargs):
                    calls.append(kwargs)
                    yield {'auth': SimpleNamespace(account_name='acc1', im_id='777'), 'session': session}
                with patch('lib.market_accounts.load_config', return_value={
                        'markets': {'br': {'roles': {'communications': 'acc1'}}}}), \
                     patch.object(market_im_runtime, 'authenticated', authenticated):
                    proof = read_original(Path('/unused'), 'br', {'snapshot': {
                        'senderAccount': 'acc1', 'senderImId': '777', 'oecId': '123', **frozen}},
                        'conversation', {'state': 'inflight', 'cid': None, 'request_ref': 'original-request'})
                self.assertEqual(proof['status'], 'result_unknown')
                self.assertIs(calls[0]['read_only'], True)
                self.assertIs(calls[0]['use_environment_proxy'], expected)
                session.initialize.assert_called_once_with(0)
                session.create_once.assert_not_called()

    def test_proxy_option_rejects_non_boolean_before_any_auth_or_config_read(self):
        for value in (None, 0, 1, 'false', [], {}):
            with self.subTest(value=value), \
                 patch.object(market_im_runtime, 'load_config', side_effect=AssertionError('must fail before config')):
                with self.assertRaisesRegex(ValueError, 'market_im_proxy_setting_invalid'):
                    with market_im_runtime.authenticated(Path('/unused'), 'br', {}, use_environment_proxy=value):
                        self.fail('invalid proxy setting was accepted')


if __name__ == '__main__': unittest.main()
