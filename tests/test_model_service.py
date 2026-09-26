import sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from lib import model_service as M  # noqa:E402
from lib.second_cycle import CycleError,CycleStore  # noqa:E402

class ModelServiceEpochTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.now=[1000.0]
  self.store=CycleStore(Path(self.tmp.name)/'db.sqlite',lambda:self.now[0]);self.key=M.service_key('DeepSeek','m')
 def tearDown(self):self.store.close();self.tmp.cleanup()
 def state(self):return M.paused(self.store.db,self.key,self.now[0])

 def test_a_late_success_from_before_the_pause_cannot_close_it(self):
  permits=[M.acquire(self.store,self.key) for _ in range(4)]
  for permit in permits[:3]:M.failed(self.store,permit,'provider_timeout')
  self.assertIsNotNone(self.state())
  M.succeeded(self.store,permits[3])  # the fourth call, admitted before the pause, returns late
  self.assertIsNotNone(self.state())
  M.failed(self.store,permits[3],'provider_timeout')  # nor does its late failure extend the pause
  with self.assertRaisesRegex(CycleError,'paused'):M.acquire(self.store,self.key)

 def test_only_the_current_probe_closes_and_a_second_probe_is_refused(self):
  for permit in [M.acquire(self.store,self.key) for _ in range(3)]:M.failed(self.store,permit,'provider_http_error')
  self.now[0]+=M.PAUSE_SECONDS+1
  probe=M.acquire(self.store,self.key);self.assertEqual(probe['mode'],'probe')
  with self.assertRaisesRegex(CycleError,'paused'):M.acquire(self.store,self.key)
  M.succeeded(self.store,probe);self.assertIsNone(self.state())
  # A probe result from the finished epoch changes nothing afterwards.
  M.failed(self.store,probe,'provider_timeout');self.assertIsNone(self.state())
  self.assertEqual(M.acquire(self.store,self.key)['mode'],'closed')

 def test_a_failed_probe_reopens_with_a_longer_pause(self):
  for permit in [M.acquire(self.store,self.key) for _ in range(3)]:M.failed(self.store,permit,'provider_timeout')
  first=self.state()['nextAt'];self.now[0]=first+1
  probe=M.acquire(self.store,self.key);M.failed(self.store,probe,'provider_timeout')
  self.assertGreater(self.state()['nextAt']-self.now[0],M.PAUSE_SECONDS)

 def test_local_input_and_bad_output_never_count(self):
  for _ in range(5):M.failed(self.store,M.acquire(self.store,self.key),'provider_input_invalid')
  self.assertIsNone(self.state())

if __name__=='__main__':unittest.main()
