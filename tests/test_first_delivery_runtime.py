from contextlib import contextmanager, ExitStack
from types import SimpleNamespace
from unittest.mock import patch

import test_first_outreach as domain_tests
from lib.cycle_delivery import Deliveries
from lib.market_send_canary import run
from lib.second_cycle import CycleError, CycleStore


class FirstDeliveryRuntimeTests(domain_tests.FirstOutreachTests):
    """Exercise the real shared runtime with an isolated ledger and fake platform."""
    @contextmanager
    def runtime(self, *, preflight=None, fail_send=None):
        self.sent = []
        def send(kind):
            def dispatch(_conversation, _body, ref, *, before_dispatch):
                before_dispatch({'market': 'br', 'account': 'acc1', 'componentKind': kind, 'stage': 'send_message'})
                self.sent.append((kind, ref))
                if fail_send == kind:
                    raise TimeoutError('lost response')
                return {'requestRef': ref, 'messageId': kind + '-id'}
            return dispatch
        adapter = SimpleNamespace(send_card_once=send('card'), send_once=send('text'),
                                  readback_card=lambda *a, **k: {'status': 'confirmed', 'messageId': 'card-id', 'evidenceRef': 'card-proof'},
                                  readback=lambda *a, **k: {'status': 'confirmed', 'messageId': 'text-id', 'evidenceRef': 'text-proof'})
        @contextmanager
        def authenticated(*args, **kwargs):
            yield {'auth': SimpleNamespace(im_id='777'), 'adapter': adapter, 'partnerHost': 'https://fixture.invalid',
                   'session': SimpleNamespace(initialize=lambda _: {'conversations': [{'oecId': '123', 'conversationId': '999'}]},
                                              conversation=lambda *a: SimpleNamespace(conversation_id='999'))}
        @contextmanager
        def gate(*args, **kwargs):
            yield lambda: None
        with ExitStack() as stack:
            for name, value in [('load_config', lambda _: {'markets': {'br': {'roles': {'communications': 'acc1'}}}}),
                                ('CycleStore', lambda path: CycleStore(path, lambda: self.now)), ('authenticated', authenticated),
                                ('write_gate', gate), ('_binding_current', lambda *a: None),
                                ('_preflight_or_close', preflight or (lambda *a: None)),
                                ('descriptor', lambda *a: SimpleNamespace())]:
                stack.enter_context(patch('lib.market_send_canary.' + name, value))
            stack.enter_context(patch('lib.market_send_control.control', return_value={'window': ['00:00', '24:00']}))
            yield

    def test_real_runtime_confirms_one_invitation_and_consumes_only_first_grant(self):
        self.prepare(); self.start()
        with self.runtime():
            result = run(self.root, 'br', 'first-runtime-123', canary=False, page_control=True, purpose='first')
        self.assertEqual((result['state'], result['realSends']), ('confirmed', 1))
        self.assertEqual([item[0] for item in self.sent], ['card', 'text'])
        from lib.first_outreach import control
        self.assertEqual(control(self.s)['mode'], 'stopped')
        self.assertEqual(control(self.s)['canaryDeliveryId'], result['deliveryId'])

    def test_lost_card_response_remains_original_unknown_and_never_sends_text(self):
        self.prepare(); self.start()
        with self.runtime(fail_send='card'):
            with self.assertRaises(TimeoutError):
                run(self.root, 'br', 'first-runtime-unknown', canary=False, page_control=True, purpose='first')
        row = self.s.db.execute('SELECT id FROM cycle_delivery').fetchone()
        delivery = Deliveries(self.s).get(row[0])
        self.assertEqual(delivery['state'], 'unknown')
        self.assertEqual([item[0] for item in self.sent], ['card'])
        self.assertEqual([part['state'] for part in delivery['parts']], ['unknown', 'ready'])

    def test_mixed_execution_modes_cannot_borrow_first_offer_qualification(self):
        self.prepare(); self.start()
        candidate = self.get_candidate()
        for changes in ({'executionMode': 'market-continuous-v1'}, {'outreachPurpose': 'second'}):
            with self.subTest(changes=changes):
                with self.assertRaisesRegex(CycleError, 'first_outreach_source_invalid'):
                    self.d.prepare(self.plan, candidate | changes)


# Reuse the isolated fixture without rerunning every domain test in this module.
for _name in tuple(name for name in dir(domain_tests.FirstOutreachTests) if name.startswith('test_')):
    if _name not in FirstDeliveryRuntimeTests.__dict__:
        setattr(FirstDeliveryRuntimeTests, _name, None)
