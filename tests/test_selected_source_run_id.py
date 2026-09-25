import sys
import unittest
from datetime import datetime,timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from lib.operations_policy import selected_source_run_id
from lib.second_cycle import digest
from lib.workflow_recovery import source_id


class SelectedSourceRunId(unittest.TestCase):
    def test_both_call_sites_agree(self):
        started=datetime(2026,9,14,9,30,tzinfo=timezone.utc).timestamp()
        expected='it-global-20260914-'+digest(['run-1','selected'])[:12]
        self.assertEqual(selected_source_run_id('it','run-1',started),expected)
        self.assertEqual(source_id({'market':'it','run_id':'run-1','started_at':started}),expected)

    def test_start_just_after_1600_utc_gets_the_next_beijing_date(self):
        started=datetime(2026,9,14,16,0,1,tzinfo=timezone.utc).timestamp()
        self.assertEqual(selected_source_run_id('it','run-1',started),
                         'it-global-20260915-'+digest(['run-1','selected'])[:12])
        self.assertEqual(selected_source_run_id('it','run-1',started-2),
                         'it-global-20260914-'+digest(['run-1','selected'])[:12])

    def test_market_prefix_is_kept(self):
        started=datetime(2026,9,14,9,30,tzinfo=timezone.utc).timestamp()
        self.assertTrue(selected_source_run_id('br','run-1',started).startswith('br-global-20260914-'))


if __name__=='__main__':unittest.main()
