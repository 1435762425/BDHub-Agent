"""BR supply uses the configured route without widening any write scope."""
from contextlib import contextmanager, nullcontext
from pathlib import Path
from types import ModuleType, SimpleNamespace
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from lib import global_source_transport as source
from lib import sample_transport as sample
from lib.second_cycle import CycleError, digest


def module(name, **attributes):
    result = ModuleType(name)
    result.__path__ = []
    result.__dict__.update(attributes)
    return result


@contextmanager
def fixture(market):
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        headers = root / 'headers.json'
        headers.write_text('fixture-identity')
        account = SimpleNamespace(name='acc2', profile_dir=root, headers_json=headers)
        identity = SimpleNamespace(partner_id_is_own=True, im_market_partner_id='123')
        identity.require_product_search = lambda: identity

        class Commerce:
            def __init__(self, _identity, _account, *, allow_write=False):
                self.allow_write = allow_write
                self.session = SimpleNamespace(trust_env=False, close=lambda: None)
                self.verification_attempts = self.verification_successes = 0

            def copy_session_from(self, _source):
                # The protocol layer copies identity/cookies, but not trust_env.
                self.copied = True

            def _xhr(self, **_kwargs):
                raise AssertionError('no request may leave this fixture')

        class Lease:
            def __init__(self, *_args, **_kwargs): pass
            def acquire(self): return SimpleNamespace(token='fixture-lease')
            def release(self, token): assert token == 'fixture-lease'

        vendor = module('bdhub', scheduled_relogin=SimpleNamespace(maintenance_due=lambda *_a, **_k: False))
        modules = {
            'bdhub': vendor,
            'bdhub.hub': module('bdhub.hub'),
            'bdhub.hub.markets': module('bdhub.hub.markets', identity_for=lambda *_a, **_k: identity),
            'bdhub.research': module('bdhub.research'),
            'bdhub.research.commerce_transport': module('bdhub.research.commerce_transport', CommerceTransport=Commerce),
            'bdhub.send': module('bdhub.send'),
            'bdhub.send.taplink': module('bdhub.send.taplink'),
            'bdhub.send.taplink.transport': module('bdhub.send.taplink.transport', account_for=lambda *_a, **_k: ({}, account)),
            'bdhub.send.sharelink': module('bdhub.send.sharelink'),
            'bdhub.send.sharelink.transport': module('bdhub.send.sharelink.transport', PICK_UP_SELECT_PATH=source.SELECT),
            'bdhub.enrich': module('bdhub.enrich'),
            'bdhub.enrich.profile_lease': module('bdhub.enrich.profile_lease', ProfileLease=Lease, ProfileBusyError=RuntimeError),
        }
        @contextmanager
        def store(*_args, **_kwargs): yield None
        with patch.dict(sys.modules, modules), \
             patch.object(source, 'ROOT', root), \
             patch.object(source, 'configure_vendored_bdhub'), \
             patch.object(source, '_profile_guard', return_value=nullcontext()), \
             patch('lib.legacy_runtime.configure_vendored_bdhub'), \
             patch('lib.market_accounts.load_config', return_value={'markets': {market: {'roles': {'supply': 'acc2'}}}}), \
             patch('lib.market_accounts.catalog_scope', return_value={'market': market, 'institutionFingerprint': digest('123')}), \
             patch('lib.market_accounts.catalog_read_account', return_value='acc2'), \
             patch('lib.account_identity.current_generation', return_value={'capabilities': {'product_select': {'state': 'verified'}}}), \
             patch('lib.second_cycle.CycleStore', store), \
             patch.object(sample, 'observed_read_contract', return_value=None):
            yield root


class BrazilSupplyProxyTests(unittest.TestCase):
    def test_br_main_and_read_lanes_use_environment_without_granting_writes(self):
        with fixture('br'):
            with source._opportunity_transport({}, market='br', account_name='acc2') as transport:
                self.assertTrue(transport.session.trust_env)
                self.assertFalse(transport.allow_write)
                lane = transport.fork_lane(lambda: None)
                self.assertTrue(lane.session.trust_env)
                self.assertFalse(lane.allow_write)
                self.assertEqual(lane.WRITE_ENDPOINTS, frozenset())
                with self.assertRaisesRegex(ValueError, 'selection_write_outside_intent'):
                    lane._xhr(method='POST', path=source.SELECT, params={}, payload={}, write=True)
                with self.assertRaisesRegex(ValueError, 'selection_lane_scope_invalid'):
                    transport.selection_lane(lambda: None)

    def test_br_lanes_preserve_the_parent_route_and_selection_scope(self):
        with fixture('br'):
            with source._opportunity_transport({}, market='br', account_name='acc2',
                                               selection_scope={'pid': 'campaign'}) as transport:
                self.assertTrue(transport.session.trust_env)
                transport.session.trust_env = False
                for lane in (transport.fork_lane(lambda: None), transport.selection_lane(lambda: None)):
                    self.assertFalse(lane.session.trust_env)
                    self.assertTrue(lane.allow_write)
                    self.assertEqual(lane.WRITE_ENDPOINTS, {(source.SELECT, 'POST')})
                    with self.assertRaisesRegex(ValueError, 'selection_write_outside_intent'):
                        lane._xhr(method='POST', path=source.SELECT, params={},
                                  payload={'product_id': 'other', 'campaign_id': 'campaign'}, write=True)

    def test_other_market_keeps_the_protocol_route(self):
        with fixture('my'):
            with source._opportunity_transport({}, market='my', account_name='acc2') as transport:
                self.assertFalse(transport.session.trust_env)
                self.assertFalse(transport.fork_lane(lambda: None).session.trust_env)

    def test_sample_reader_uses_environment_but_cannot_approve(self):
        with fixture('br') as root:
            report = {}
            with sample._account_sample_session(root, 'br', 'acc2', report) as adapter:
                self.assertTrue(adapter.transport.session.trust_env)
                self.assertFalse(adapter.transport.allow_write)
                self.assertEqual(adapter.transport.WRITE_ENDPOINTS, frozenset())
                with self.assertRaisesRegex(CycleError, 'sample_review_outside_intent'):
                    adapter.approve_once({'is_approved': True})
            self.assertTrue(report['identityFileUnchanged'])
            self.assertEqual(report['platformWrites'], 0)
            self.assertEqual(report['platformWriteAttempts'], 0)


if __name__ == '__main__':
    unittest.main()
