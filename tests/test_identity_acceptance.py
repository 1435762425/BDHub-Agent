import sys,unittest,tempfile,json,sqlite3
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from lib.identity_acceptance import evaluate,production_policy,SCHEMA
from lib.second_cycle import encoded,digest
class AcceptanceTests(unittest.TestCase):
 def fixtures(self):
  samples=[{'itemId':str(i),'oec':str(1000+i),'handle':'real'+str(i)} for i in range(500)]
  records={m['itemId']:{'status':'completed','oec':m['oec'],'report':{'account':'acc6','market':'it','qps':12,'identityFileUnchanged':True,'targets':[{'requestedHandle':m['handle'],'oecId':m['oec']}],'requests':[{'targetRef':m['itemId'],'status':'returned','httpStatus':200,'code':'0','verificationRequired':False,'durationMs':1200}]}} for m in samples}
  return samples,records
 def test_500_successes_pass_and_incomplete_does_not(self):
  s,r=self.fixtures();self.assertTrue(evaluate(s,r)['passed']);del r['0'];self.assertFalse(evaluate(s,r)['final'])
 def test_bad_identity_or_rate_limit_cannot_publish(self):
  s,r=self.fixtures();r['0']['oec']='wrong';self.assertFalse(evaluate(s,r)['passed']);self.assertTrue(evaluate(s,r)['final'])
  s,r=self.fixtures();r['0']['report']['requests'][0]['attempts']=[{'httpStatus':429}];self.assertFalse(evaluate(s,r)['passed'])
 def test_errors_misses_latency_and_unresolved_verification_are_measured(self):
  s,r=self.fixtures()
  for i in range(6):r[str(i)]['status']='unresolved';r[str(i)]['oec']=None
  self.assertFalse(evaluate(s,r)['passed'])
  s,r=self.fixtures()
  for i in range(30):r[str(i)]['report']['requests'][0]['durationMs']=11000
  self.assertFalse(evaluate(s,r)['passed'])
  s,r=self.fixtures();q=r['0']['report']['requests'][0];q.update(status='error',attempts=[{'verificationRequired':True}]);self.assertEqual(evaluate(s,r)['unresolvedVerification'],1);self.assertFalse(evaluate(s,r)['passed'])
 def test_promotion_requires_matching_immutable_passed_evidence(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);(root/'var').mkdir();self.assertEqual(production_policy(root)['qps'],3)
   c=sqlite3.connect(root/'var/batch-tasks.sqlite');c.executescript(SCHEMA);metrics=evaluate(*self.fixtures())
   c.execute('INSERT INTO identity_acceptance VALUES(?,?,?,?,?,?,?,?)',('a','task','soak','batch','passed','{}',1,encoded(metrics)))
   c.execute('INSERT INTO identity_runtime_policy VALUES(?,?,?,?,?,?)',('acc6',12,9,'a',digest(metrics),1));c.commit()
   self.assertEqual(production_policy(root)['qps'],12);self.assertEqual(production_policy(root,'acc1')['qps'],3);self.assertEqual(production_policy(root,market='mx')['qps'],3)
   c.execute("UPDATE identity_runtime_policy SET result_hash='bad'");c.commit()
   with self.assertRaises(ValueError):production_policy(root)
   c.close()
if __name__=='__main__':unittest.main()
