"""Read-only IM auth must release the profile before a long inbox scan."""
import sys
import tempfile
import types
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from lib import market_im_runtime as runtime


class MarketImRuntimeLockingTests(unittest.TestCase):
    def test_read_only_releases_after_auth_but_sender_keeps_lease(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            headers = root / 'headers.json'
            headers.write_text('original')
            state = {'held': False, 'auth_reads': 0, 'read_busy': 2}
            account = SimpleNamespace(name='acc1', profile_dir=root, headers_json=headers)
            identity = SimpleNamespace(aid='1', partner_id='2', im_market='3',
                                       im_market_partner_id='4', home='https://partner.example')
            identity.require_product_search = lambda: identity

            class BusyError(Exception): pass

            class Lease:
                def __init__(self, *_args, **kwargs): self.read_only = kwargs['operation'] == 'agent-im-read'
                def __enter__(self):
                    if self.read_only and state['read_busy']:
                        state['read_busy'] -= 1
                        raise BusyError()
                    self_test.assertFalse(state['held'])
                    state['held'] = True
                def __exit__(self, *_args): state['held'] = False

            class Reader:
                def __init__(self, *_args, **_kwargs):
                    self.session = SimpleNamespace(trust_env=False, close=lambda: None)
                    self.headers = {'User-Agent': 'fixture'}
                def _xhr(self, **kwargs):
                    self_test.assertTrue(state['held'])
                    state['auth_reads'] += 1
                    path = kwargs['path']
                    if path == runtime.INFO:
                        return {'partner_biz_role_info': {'market_list': [
                            {'market_region': '3', 'market_id': '5',
                             'type_list': [{'partner_id': '2'}]}]}}
                    if path == runtime.IM_ID: return {'im_id': '6'}
                    return {'token': 'secret', 'api_url': 'https://im.example'}
                def require_read(self, value): return value

            class Session:
                def __init__(self, _auth, _report, **kwargs):
                    self.maintenance_due = kwargs['maintenance_due']
                    self.request_budget = kwargs['request_budget']
                def __enter__(self): return self
                def __exit__(self, *_args): pass

            def module(name, **attrs):
                value = types.ModuleType(name)
                for key, item in attrs.items(): setattr(value, key, item)
                return value

            vendor = module('bdhub')
            vendor.__path__ = []
            vendor.scheduled_relogin = SimpleNamespace(maintenance_due=lambda *_a, **_k: False)
            fake_modules = {
                'bdhub': vendor,
                'bdhub.enrich': module('bdhub.enrich'),
                'bdhub.enrich.profile_lease': module('bdhub.enrich.profile_lease', ProfileLease=Lease, ProfileBusyError=BusyError),
                'bdhub.hub': module('bdhub.hub'),
                'bdhub.hub.markets': module('bdhub.hub.markets', identity_for=lambda *_a, **_k: identity),
                'bdhub.research': module('bdhub.research'),
                'bdhub.research.commerce_transport': module('bdhub.research.commerce_transport', CommerceTransport=Reader),
                'bdhub.send': module('bdhub.send'),
                'bdhub.send.taplink': module('bdhub.send.taplink'),
                'bdhub.send.taplink.transport': module('bdhub.send.taplink.transport', account_for=lambda *_a, **_k: ({}, account)),
            }
            self_test = self
            @contextmanager
            def store(*_args, **_kwargs): yield None
            generation = {'capabilities': {'inbox_read': {'state': 'verified'},
                                           'message_send': {'state': 'verified'}}}
            with patch.dict(sys.modules, fake_modules), \
                 patch.object(runtime, 'configure_vendored_bdhub'), \
                 patch.object(runtime, 'load_config', return_value={'markets': {'br': {'roles': {'communications': 'acc1'}}}}), \
                 patch.object(runtime, 'ItalyImReadSession', Session), \
                 patch('lib.second_cycle.CycleStore', store), \
                 patch('lib.account_identity.current_generation', return_value=generation) as current:
                with runtime.authenticated(root, 'br', {}, read_only=True) as context:
                    self.assertFalse(state['held'])
                    self.assertEqual(state['read_busy'],0)
                    self.assertIsNone(context['adapter'])
                    self.assertAlmostEqual(context['session'].request_budget.interval, 0.5)
                    self.assertFalse(context['session'].maintenance_due())
                    headers.write_text('rotated')
                    with self.assertRaisesRegex(ValueError, 'market_send_identity_changed'):
                        context['session'].maintenance_due()
                    headers.write_text('original')
                from lib.italy_im_auth import ItalyImAuthContext
                borrowed_auth=ItalyImAuthContext('acc1','6',{'token':'private'}, {'market':'br','partner_host':'https://partner.example'}, {})
                checks=[]
                @contextmanager
                def borrow(*args,**kwargs):
                    self.assertFalse(state['held'])
                    yield borrowed_auth,lambda:checks.append('check')
                with patch('lib.im_session_owner.enabled',return_value=True),patch('lib.im_session_owner.borrow',borrow),patch.object(runtime,'ItalyImDeliveryAdapter',return_value=object()):
                    with runtime.authenticated(root,'br',{}) as context:
                        self.assertFalse(state['held']);self.assertIs(context['auth'],borrowed_auth)
                        self.assertFalse(context['session'].maintenance_due());self.assertEqual(checks,['check'])
                    from lib.second_cycle import CycleError
                    with self.assertRaisesRegex(CycleError,'business_failure'):
                        with runtime.authenticated(root,'br',{}):raise CycleError('business_failure')
                with patch.object(runtime, 'ItalyImDeliveryAdapter', return_value=object()):
                    with runtime.authenticated(root, 'br', {}) as context:
                        self.assertTrue(state['held'])
                        self.assertIsNotNone(context['adapter'])
                        self.assertAlmostEqual(context['session'].request_budget.interval, 1/3)
                    self.assertFalse(state['held'])
                    with runtime.authenticated(root, 'br', {}) as context:
                        self.assertTrue(context['session'].request_budget)
                    self.assertEqual(state['auth_reads'], 6)
                    current.return_value = generation | {'generationId': 'next'}
                    with runtime.authenticated(root, 'br', {}) as context:
                        self.assertFalse(context['session'].maintenance_due())
                    self.assertEqual(state['auth_reads'], 9)


if __name__ == '__main__': unittest.main()
