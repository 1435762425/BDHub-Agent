import importlib.util
import unittest
from pathlib import Path

PATH=Path(__file__).resolve().parents[1]/'scripts/market-send-worker.py'
SPEC=importlib.util.spec_from_file_location('market_send_worker_cli',PATH)
MODULE=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(MODULE)


class MarketSendWorkerTests(unittest.TestCase):
    def test_an_empty_ready_pool_waits_without_relaunch_churn(self):
        self.assertEqual(MODULE.expected_wait_state('market_send_candidate_missing'),'waiting_pool')
        self.assertEqual(MODULE.expected_wait_state('ProfileBusyError'),'waiting_account')
        self.assertEqual(MODULE.expected_wait_state('local_store_busy'),'waiting_account')
        self.assertIsNone(MODULE.expected_wait_state('it_delivery_dispatch_not_allowed'))
        self.assertEqual(MODULE.expected_wait_state('new_contact_capacity_reached'),'waiting_capacity')
        self.assertEqual(MODULE.expected_wait_state('it_delivery_send_rejected'),'waiting_platform_refusal')
        self.assertEqual(MODULE.expected_wait_state('market_send_result_unknown'),'waiting_reconciliation')
        self.assertEqual(MODULE.next_delay(sent=False,waiting='waiting_reconciliation',interval=30),300)
        self.assertEqual(MODULE.expected_wait_state('market_send_conversation_result_unknown'),'waiting_reconciliation')

    def test_confirmed_send_has_no_fixed_thirty_second_gap(self):
        self.assertEqual(MODULE.next_delay(sent=True,waiting=None,interval=30),0.25)
        self.assertEqual(MODULE.next_delay(sent=True,waiting=None,interval=30,inbox_waiting_now=True),3)
        self.assertEqual(MODULE.next_delay(sent=False,waiting='waiting_account',interval=30),3)
        self.assertEqual(MODULE.next_delay(sent=False,waiting='waiting_pool',interval=30),30)
        self.assertEqual(MODULE.next_delay(sent=False,waiting='waiting_capacity',interval=30),300)

    def test_an_unverified_market_sends_one_page_started_canary_then_stops(self):
        unverified={'capabilities':{'message_send':{'state':'not_tested'}}}
        verified={'capabilities':{'message_send':{'state':'verified'}}}
        self.assertTrue(MODULE.first_send_canary(None))
        self.assertTrue(MODULE.first_send_canary(unverified))
        self.assertFalse(MODULE.first_send_canary(verified))
        self.assertTrue(MODULE.stops_after({'state':'confirmed','realSends':0,
            'canaryCompleted':True,'canaryStopRequested':True}))
        self.assertFalse(MODULE.stops_after({'state':'confirmed','realSends':1}))
        self.assertFalse(MODULE.stops_after({'state':'confirmed','canaryCompleted':True,'canaryStopRequested':False}))


    def test_a_page_start_launches_the_sender_for_any_non_it_market(self):
        import sys
        from unittest.mock import patch
        sys.path.insert(0,str(PATH.parents[0]))
        import lib.market_send_control as control
        from lib.second_cycle import CycleError
        root=PATH.parents[1]
        with patch.object(control,'launch',side_effect=lambda _root,market:{'pid':1,'market':market}):
            for market in ('br','my','uk','de','fr'):self.assertEqual(control.launch_worker(root,market)['market'],market)
            with self.assertRaisesRegex(CycleError,'market_send_runtime_unavailable'):control.launch_worker(root,'it')

if __name__=='__main__':unittest.main()
