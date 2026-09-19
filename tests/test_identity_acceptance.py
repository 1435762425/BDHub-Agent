import sys,unittest,tempfile,json,sqlite3,threading,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from lib.identity_acceptance import evaluate,production_policy,SCHEMA
from lib.profile_refresh import ProfileRefreshError
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
 def _published(self,root):
  c=sqlite3.connect(root/'var/batch-tasks.sqlite');c.executescript(SCHEMA);metrics=evaluate(*self.fixtures())
  c.execute('INSERT INTO identity_acceptance VALUES(?,?,?,?,?,?,?,?)',('a','task','soak','batch','passed','{}',1,encoded(metrics)))
  c.execute('INSERT INTO identity_runtime_policy VALUES(?,?,?,?,?,?)',('acc6',12,9,'a',digest(metrics),1));c.commit()
  return c
 def test_a_busy_database_is_named_not_crashed(self):
  """实测（2026-09-15）：常驻准备作业会在 batch-tasks 上持有多达 37 秒的写事务，而这个库是回滚
  日志模式——读者会被整个挡住。默认 5 秒 busy 超时正是"补 OECID 跑到第二轮突然 internal_error"的
  原因：错误从 production_policy 裸抛出去，最后只剩一个谁都看不懂的兜底码。"""
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);(root/'var').mkdir();c=self._published(root)
   blocker=sqlite3.connect(root/'var/batch-tasks.sqlite',isolation_level=None)
   try:
    blocker.execute('BEGIN EXCLUSIVE')
    with self.assertRaises(ProfileRefreshError) as raised:production_policy(root,wait_seconds=0.2)
    # 不是"内部错误"，是"这个库现在读不到"——两者对操作者的含义完全不同。
    self.assertEqual(raised.exception.code,'identity_policy_unreadable')
   finally:
    blocker.execute('ROLLBACK');blocker.close();c.close()
 def test_a_busy_database_is_waited_out_so_a_long_backfill_survives_it(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);(root/'var').mkdir();c=self._published(root)
   blocker=sqlite3.connect(root/'var/batch-tasks.sqlite',isolation_level=None,check_same_thread=False)
   blocker.execute('BEGIN EXCLUSIVE')
   def release():
    # 比 sqlite 默认的 5 秒 busy 超时更长：这正是实测里那次 37 秒占用会击穿的那道线。
    time.sleep(6.5);blocker.execute('ROLLBACK')
   thread=threading.Thread(target=release);thread.start()
   try:
    # 等待预算内的短暂占用必须被挺过去：一轮几十分钟的补号不能因为别人提交一次就整批停下。
    self.assertEqual(production_policy(root,wait_seconds=20.0)['qps'],12)
   finally:
    thread.join();blocker.close();c.close()
 def test_a_policy_that_contradicts_its_acceptance_is_named_and_never_retried(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);(root/'var').mkdir();c=self._published(root)
   c.execute("UPDATE identity_runtime_policy SET result_hash='bad'");c.commit();c.close()
   with self.assertRaises(ProfileRefreshError) as raised:production_policy(root,wait_seconds=5.0)
   self.assertEqual(raised.exception.code,'published_identity_policy_invalid')
if __name__=='__main__':unittest.main()
