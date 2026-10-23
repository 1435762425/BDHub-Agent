"""BR read challenges may use the canary stage while generic batches stay closed."""
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'vendor'))

from bdhub.enrich import pure_http_worker
from bdhub.hub.markets import MARKETS, MarketCapabilityError, capability_status, require_capability
from bdhub.research.commerce_transport import CommerceTransport
from bdhub.research.product_source_transport import SourceTransport

LIST = '/api/v1/affiliate/partner/sample/records/list'
REVIEW = '/api/v1/affiliate/partner/campaign/sample/budget/review'


class BrPureHttpCanaryTests(unittest.TestCase):
    def session(self):
        # Exercise the real gate/replay code without credentials or remote calls.
        transport = object.__new__(CommerceTransport)
        transport.identity = SimpleNamespace(market='br')
        transport.fp = 'original-fp'
        transport.verification_successes = 0
        transport._solve_verification = Mock(side_effect=lambda _header: setattr(transport, 'fp', 'verified-fp'))
        return transport

    def result(self, *, challenge=False):
        return SimpleNamespace(http_status=200, code=10000 if challenge else 0,
            has_turing=challenge, ambiguous=False, system_error_3=challenge, payload={'code': 10000 if challenge else 0})

    def test_br_is_canary_and_other_market_stages_are_unchanged(self):
        self.assertEqual(capability_status('br', 'pure_http'), 'canary')
        self.assertEqual({market: capability_status(market, 'pure_http') for market in MARKETS if market != 'br'},
                         {market: 'enabled' for market in ('mx', 'my', 'th', 'uk', 'it', 'jp', 'us', 'de', 'fr')})
        with self.assertRaises(MarketCapabilityError) as blocked:
            require_capability('br', 'pure_http')
        self.assertEqual((blocked.exception.market, blocked.exception.capability, blocked.exception.status),
                         ('br', 'pure_http', 'canary'))

    def test_actual_read_challenge_verifies_and_replays_only_the_original_read(self):
        transport = self.session()
        calls = []
        def read(**request):
            calls.append(request)
            if len(calls) == 1:
                transport._verification_header = 'observed-challenge'
                return self.result(challenge=True)
            return self.result()
        body = {'search_params': [], 'cur_page': 1, 'page_size': 20}
        with patch.object(SourceTransport, '_xhr', side_effect=read):
            result = transport._xhr(method='POST', path=LIST, params={'aid': '360019'}, payload=body, write=False)
        transport._solve_verification.assert_called_once_with('observed-challenge')
        self.assertEqual(result.code, 0)
        self.assertEqual(transport.verification_successes, 1)
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(call['path'] == LIST and call['payload'] == body and call['write'] is False for call in calls))
        self.assertEqual(calls[0]['params'], {'aid': '360019'})
        self.assertEqual(calls[1]['params'], {'aid': '360019', 'fp': 'verified-fp'})

    def test_no_challenge_read_does_not_claim_verification_success(self):
        transport = self.session()
        with patch.object(SourceTransport, '_xhr', return_value=self.result()) as read:
            transport._xhr(method='POST', path=LIST, params={}, payload={}, write=False)
        read.assert_called_once()
        transport._solve_verification.assert_not_called()
        self.assertEqual(transport.verification_successes, 0)

    def test_write_challenge_is_never_verified_or_replayed(self):
        transport = self.session()
        def write(**_request):
            transport._verification_header = 'observed-challenge'
            return self.result(challenge=True)
        with patch.object(SourceTransport, '_xhr', side_effect=write) as post:
            result = transport._xhr(method='POST', path=REVIEW, params={}, payload={'is_approved': True}, write=True)
        post.assert_called_once()
        transport._solve_verification.assert_not_called()
        self.assertEqual((result.code, transport.verification_successes), (10000, 0))

    def test_generic_http_batch_entrypoints_stop_before_inputs_runtime_or_accounts(self):
        with patch.object(pure_http_worker.cfgmod, 'load', side_effect=AssertionError('batch config must not load')), \
             patch.object(pure_http_worker, 'validate_runtime', side_effect=AssertionError('batch runtime must not load')):
            for call in (lambda: pure_http_worker.run('/unused.csv', market='br', account_names=['acc2'], limit=1),
                         lambda: pure_http_worker.run_pairs([], [], None, market='br')):
                with self.assertRaisesRegex(MarketCapabilityError, 'market_capability_unavailable:br:pure_http:canary'):
                    call()


if __name__ == '__main__': unittest.main()
