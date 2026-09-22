import importlib.util
import unittest
from pathlib import Path

PATH=Path(__file__).resolve().parents[1]/'scripts/market-send-worker.py'
SPEC=importlib.util.spec_from_file_location('market_send_worker_cli',PATH)
MODULE=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(MODULE)


class MarketSendWorkerTests(unittest.TestCase):
    def test_an_empty_ready_pool_waits_without_relaunch_churn(self):
        self.assertEqual(MODULE.expected_wait_state('market_send_candidate_missing'),'waiting_pool')
        self.assertIsNone(MODULE.expected_wait_state('market_send_result_unknown'))


if __name__=='__main__':unittest.main()
