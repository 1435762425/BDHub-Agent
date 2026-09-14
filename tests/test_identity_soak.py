import json,sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from lib.batch_task_service import TaskService
from lib.batch_tasks import preparation_gate,BatchError
from lib.identity_soak import IdentitySoak,status
from lib.second_cycle import encoded
class SoakTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.s=TaskService(Path(self.tmp.name)/'db',lambda:1000)
  p=self.s.preview({'institution':'bjn-local-research','market':'it','target':1000,'prepareNow':True});self.task=self.s.confirm(p['token'],'t')['id'];self.soak=IdentitySoak(self.s)
  with self.s.tasks.tx():
   for i in range(1100):self.s.db.execute('INSERT INTO batch_member VALUES(?,?,?,?)',(self.task,str(i+1),'m',encoded(self.member(i+1))))
  self.id=self.soak.start(self.task,expected_revision=1)
 def tearDown(self):self.s.close();self.tmp.cleanup()
 def member(self,i):return {'oec':str(i),'materialKey':'m','checks':dict(source=True,identity=True,relationship=True,offer=True,name=True,taplink=True)}
 def report(self,oecs):return {'soakRun':self.id,'account':'acc6','qps':12,'status':'completed','identityFileUnchanged':True,'targets':[{'status':'identity_verified','oecId':str(o)} for o in oecs],'requests':[{'status':'returned','httpStatus':200,'code':'0','verificationRequired':False}],'counters':{'request_count':1}}
 def test_total_2100_and_old_history_preserved(self):
  spec=self.s.tasks.get(self.task)['spec'];self.assertEqual((spec['target'],spec['reserve']),(2000,100));self.assertEqual(self.soak.start(self.task,expected_revision=1),self.id)
  members=[self.member(i+1) for i in range(2100)];self.assertTrue(preparation_gate(2000,members,reserve=100)['complete']);self.assertFalse(preparation_gate(2000,members)['complete'])
  self.assertEqual(self.s.db.execute('SELECT count(*) FROM batch_member').fetchone()[0],1100)
 def test_new_count_requires_12qps_proof_and_deduplicates(self):
  members=[self.member(i+1) for i in range(1102)]
  self.soak.record(self.id,'g1',self.report(['1','1101']),['1','1101','1101'])
  for m in members[1100:]:self.s.db.execute('INSERT INTO batch_member VALUES(?,?,?,?)',(self.task,m['oec'],'m',encoded(m)))
  self.soak.checkpoint(self.id,members);self.assertEqual(status(self.s,self.task)['newCandidates'],1)
  self.soak.record(self.id,'g1',self.report(['1','1101']),['1101']);self.assertEqual(status(self.s,self.task)['cohorts'],1)
  with self.assertRaises(BatchError):self.soak.record(self.id,'bad',self.report(['1102'])|{'qps':3},['1102'])
 def test_1000_new_eligible_completes_validation(self):
  proof=[str(i) for i in range(1101,2101)];self.soak.record(self.id,'g',self.report(proof),proof)
  self.soak.checkpoint(self.id,[self.member(i) for i in range(1,2101)]);self.assertEqual(self.soak.config(self.id)['state'],'completed')
 def test_consecutive_failures_stop_high_rate(self):
  v=self.report([])|{'status':'blocked','requests':[{'status':'error'}]}
  for i in range(3):self.soak.record(self.id,'failure'+str(i),v,[])
  self.assertEqual(self.soak.config(self.id)['state'],'attention');self.assertFalse(self.soak.allowed(self.id))
 def test_429_stops_even_if_later_replay_succeeded(self):
  v=self.report([]);v['requests'][0]['attempts']=[{'httpStatus':429},{'httpStatus':200}]
  self.soak.record(self.id,'limited',v,[]);self.assertEqual(self.soak.config(self.id)['stop_reason'],'platform_rate_limit')
if __name__=='__main__':unittest.main()
