import importlib.util,sys,json,unittest,io
from contextlib import contextmanager
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from test_cycle_auto_reply import AutoReplyTests
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('auto_runner_test',ROOT/'scripts/run-auto-replies.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
class RunnerTests(unittest.TestCase):
 _base_setup=AutoReplyTests._base_setup;_service_setup=AutoReplyTests._service_setup
 setUp=AutoReplyTests.setUp;tearDown=AutoReplyTests.tearDown;event=AutoReplyTests.event;ingest=AutoReplyTests.ingest;rel=AutoReplyTests.rel;content=AutoReplyTests.content;add=AutoReplyTests.add;baseline=AutoReplyTests.baseline;process=AutoReplyTests.process;enable=AutoReplyTests.enable;handoff=AutoReplyTests.handoff
 def fake_runtime(self,q,timeout=False):
  owner=self;self.sent=0
  event=json.loads(self.s.db.execute('SELECT payload FROM inbox_event').fetchone()[0]);content=json.loads(self.s.db.execute('SELECT payload FROM inbox_content_version').fetchone()[0])
  class Adapter:
   def send_once(self,conv,text,ref,before_dispatch):
    before_dispatch({'oecId':q['oec'],'conversationId':q['cid'],'componentKind':'text','requestRef':ref,'textSha256':module.hashlib.sha256(text.encode()).hexdigest()});owner.sent+=1
    if timeout:raise TimeoutError()
    return {'requestRef':ref,'messageId':'99'}
   def readback(self,conv,text,ref,**kw):return {'status':'confirmed','conversationId':q['cid'],'requestRef':ref,'messageId':'99','evidenceRef':'exact'}
  reads=SimpleNamespace(conversation=lambda *a:SimpleNamespace(conversation_id=q['cid']),history_summary=lambda *a,**kw:{'identityVerified':True,'hasMore':False,'events':[event],'contents':[content]})
  @contextmanager
  def gate():yield lambda:None
  @contextmanager
  def runtime(*a,**kw):yield {'reads':reads,'adapter':Adapter(),'write_gate':gate}
  return runtime
 def test_native_path_sends_ack_and_records_confirmation(self):
  q=self.handoff();rt=self.fake_runtime(q)
  with patch.object(module,'ROOT',Path(self.tmp.name)),patch.object(module,'read_sender_binding',return_value='binding'),patch.object(module,'live_runtime',rt):
   (Path(self.tmp.name)/'var').mkdir();self.assertEqual(module.run_reply(self.s,self.auto,q),'confirmed')
  self.assertEqual(self.sent,1)
 def test_timeout_recovery_uses_readback_only(self):
  q=self.handoff();rt=self.fake_runtime(q,timeout=True)
  with patch.object(module,'ROOT',Path(self.tmp.name)),patch.object(module,'read_sender_binding',return_value='binding'),patch.object(module,'live_runtime',rt):
   (Path(self.tmp.name)/'var').mkdir()
   with self.assertRaises(TimeoutError):module.run_reply(self.s,self.auto,q)
   self.assertEqual(module.run_reply(self.s,self.auto,self.auto.get(q['id'])),'confirmed')
  self.assertEqual(self.sent,1)
 def test_retired_v1_executable_cannot_enable_automatic_reply(self):
  output=io.StringIO()
  with redirect_stdout(output):self.assertEqual(module.main(),2)
  self.assertEqual(json.loads(output.getvalue())['error'],'legacy_auto_reply_retired')
del AutoReplyTests
if __name__=='__main__':unittest.main()
