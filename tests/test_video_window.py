from datetime import datetime,timedelta,timezone
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from lib.video_window import bounds,current


class WindowTests(unittest.TestCase):
    def test_window_matches_thirty_day_query_with_existing_two_day_lag(self):
        now=datetime(2026,9,25,12,tzinfo=timezone(timedelta(hours=8))).timestamp()
        start,end=bounds(now)
        self.assertEqual((str(start),str(end)),('2026-08-25','2026-09-23'))
        self.assertTrue(current(str(start),now));self.assertTrue(current(str(end),now))
        self.assertFalse(current(str(start-timedelta(days=1)),now))
        self.assertFalse(current(str(end+timedelta(days=1)),now))
        for invalid in ('',None,'not-a-date','2026-02-30'):
            self.assertFalse(current(invalid,now))
    def test_beijing_midnight_expires_old_edge_without_mutating_history(self):
        before=datetime(2026,9,25,15,59,59,tzinfo=timezone.utc).timestamp()
        self.assertTrue(current('2026-08-25',before))
        self.assertFalse(current('2026-08-25',before+1))


if __name__=='__main__':unittest.main()
