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


if __name__=='__main__':unittest.main()
