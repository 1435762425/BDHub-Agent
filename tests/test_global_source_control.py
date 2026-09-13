import sys,unittest,tempfile,json,io,importlib.util,fcntl
from pathlib import Path
from contextlib import redirect_stdout
from unittest.mock import patch,Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from lib.global_source import GlobalSources
spec=importlib.util.spec_from_file_location('global_control_test',Path(__file__).resolve().parents[1]/'scripts/global-source-control.py');M=importlib.util.module_from_spec(spec);spec.loader.exec_module(M)
class ControlTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);p=self.root/'var/cycle-catalog-it-20260913';p.mkdir(parents=True);self.scope={'market':'it','account':'acc6','institutionFingerprint':'a'*64};(p/'selected.json').write_text(json.dumps({'scope':self.scope}))
  self.patch=patch.object(M,'ROOT',self.root);self.patch.start();self.spawn=patch.object(M.subprocess,'Popen',return_value=Mock(pid=12345));self.popen=self.spawn.start()
 def tearDown(self):self.spawn.stop();self.patch.stop();self.tmp.cleanup()
 def call(self,id):
  out=io.StringIO()
  with patch.object(sys,'stdin',io.StringIO(json.dumps({'action':'sync','requestId':id}))),redirect_stdout(out):M.main()
  return json.loads(out.getvalue())
 def test_replay_starts_only_once(self):
  a=self.call('one');self.assertEqual(a,self.call('one'));self.assertEqual(self.popen.call_count,1);self.assertFalse(a['executionAllowed'])
 def test_running_reader_is_reused(self):
  s=GlobalSources(self.root/'var/global-source.sqlite');s.start('active',self.scope);s.close()
  with (self.root/'var/global-source-worker.lock').open('a') as lock:
   fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);r=self.call('another')
  self.assertEqual(r['state'],'already_running');self.assertEqual(r['runId'],'active');self.popen.assert_not_called()
 def test_equal_request_prefix_does_not_reuse_a_finished_run(self):
  a=self.call('sameprefix123-a');s=GlobalSources(self.root/'var/global-source.sqlite');s.page(a['runId'],1,{'products':[],'has_more':False,'total':0});s.finish_session(a['runId'],True);s.close()
  b=self.call('sameprefix123-b');self.assertNotEqual(a['runId'],b['runId'])
if __name__=='__main__':unittest.main()
