import sys,unittest,tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from lib.second_cycle import CycleStore,CycleError
from lib.cycle_scheduler import Scheduler,PERIODS
class SchedulerTests(unittest.TestCase):
 def setUp(self):
  self.t=tempfile.TemporaryDirectory();self.now=1000;self.s=CycleStore(Path(self.t.name)/'db',lambda:self.now);self.p=self.s.plan('test','it');self.q=Scheduler(self.s);self.q.initialize(self.p)
 def tearDown(self):self.s.close();self.t.cleanup()
 def test_single_claim_persisted_across_restart(self):
  j=self.q.claim(self.p);self.q=Scheduler(self.s);self.assertIsNone(self.q.claim(self.p));self.q.complete(self.p,j['stage'],j['run_id'],True,{});self.assertIsNotNone(self.q.claim(self.p))
 def test_failure_backoff_and_stale_run(self):
  j=self.q.claim(self.p);self.q.complete(self.p,j['stage'],j['run_id'],False,{'error':'unavailable'})
  r=self.s.db.execute('SELECT * FROM cycle_schedule WHERE stage=?',(j['stage'],)).fetchone();self.assertEqual(r['due'],1120);self.assertEqual(r['failures'],1)
  with self.assertRaises(CycleError):self.q.complete(self.p,j['stage'],j['run_id'],True,{})
 def test_catalog_checkpoint_continues_without_daily_delay(self):
  j=self.q.claim(self.p);self.q.complete(self.p,j['stage'],j['run_id'],True,{'checkpointed':True})
  r=self.s.db.execute('SELECT due,failures FROM cycle_schedule WHERE stage=?',(j['stage'],)).fetchone();self.assertEqual(tuple(r),(1045,0))
 def test_sending_blocks_catalog_but_allows_replenishment(self):
  j=self.q.claim(self.p,('catalog_selected','catalog_campaign','materials_check'));self.assertIn(j['stage'],('kalodata','identity_reconcile','reply_facts'))
 def test_pause_does_not_dispatch(self):
  self.s.control(self.p,'pause',1,'paused');self.assertIsNone(self.q.claim(self.p))
 def test_initialization_does_not_reset_due_or_failures(self):
  j=self.q.claim(self.p);self.q.complete(self.p,j['stage'],j['run_id'],True,{});self.now+=1;self.q.initialize(self.p)
  due=self.s.db.execute('SELECT due FROM cycle_schedule WHERE stage=?',(j['stage'],)).fetchone()[0];self.assertEqual(due,1000+PERIODS[j['stage']])
if __name__=='__main__':unittest.main()
