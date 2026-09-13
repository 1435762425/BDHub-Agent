import sys,tempfile,unittest
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from lib.batch_tasks import BatchTasks,BatchError,normalize_spec,preparation_gate,in_window,reply_stage_enabled
from lib.second_cycle import digest
class BatchTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.now=datetime(2026,9,13,22,0,tzinfo=ZoneInfo('Asia/Shanghai')).timestamp();self.path=Path(self.tmp.name)/'db';self.s=BatchTasks(self.path,lambda:self.now)
  self.spec={'institution':'bjn','market':'it','target':2,'startTime':'22:00','endTime':'01:00'}
 def tearDown(self):self.s.close();self.tmp.cleanup()
 def create(self,key='one',spec=None):
  v=spec or self.spec;return self.s.create(v,key,digest(normalize_spec(v)))['id']
 def member(self,oec,**changes):return {'oec':str(oec),'institution':'bjn','market':'it','materialKey':'shared-card','checks':dict(source=True,identity=True,relationship=True,offer=True,name=True,taplink=True)|changes}
 def fill(self,id):
  for i in range(3):self.s.record_member(id,self.member(i+1)|{'market':self.s.get(id)['spec']['market']})
  self.s.freeze(id)
 def test_large_fixed_goal_and_no_automatic_target(self):
  self.assertEqual(normalize_spec(self.spec|{'target':5000})['reserve'],500)
  for v in (None,'auto',True,0,1.5):
   with self.assertRaises(BatchError):normalize_spec(self.spec|{'target':v})
 def test_confirmation_replay_and_scope_changes(self):
  id=self.create();self.assertEqual(id,self.create())
  with self.assertRaises(BatchError):self.s.create(self.spec,'other','bad-hash')
  with self.assertRaises(BatchError):self.create(spec=self.spec|{'target':3})
 def test_all_formal_and_reserve_materials_required(self):
  id=self.create()
  self.s.record_member(id,self.member(1));self.s.record_member(id,self.member(2))
  with self.assertRaisesRegex(BatchError,'full_preparation'):self.s.freeze(id)
  self.s.record_member(id,self.member(3,taplink=False))
  with self.assertRaisesRegex(BatchError,'full_preparation'):self.s.freeze(id)
  self.assertEqual(self.s.readiness(id)['issues'],{'taplink':1})
  with self.assertRaises(BatchError):self.s.record_member(id,self.member(3),expected_revision=1)
  self.s.record_member(id,self.member(3),expected_revision=self.s.get(id)['revision']);self.s.freeze(id);self.assertEqual(self.s.get(id)['state'],'ready')
 def test_same_card_can_serve_multiple_distinct_creators(self):
  id=self.create();self.fill(id);self.assertEqual(self.s.readiness(id)['materialGroups'],1)
  self.assertTrue(self.s.can_start(id)['stageEligible']);self.assertFalse(self.s.can_start(id)['platformDispatchAuthorized'])
  self.s.close();self.s=BatchTasks(self.path,lambda:self.now);self.assertEqual(self.s.get(id)['state'],'ready')
 def test_no_duplicate_creator_can_fill_target(self):
  with self.assertRaisesRegex(BatchError,'duplicate_creator'):preparation_gate(2,[self.member(1),self.member(1)])
 def test_explicit_partial_does_not_reduce_target(self):
  id=self.create();self.s.record_member(id,self.member(1))
  with self.assertRaises(BatchError):self.s.allow_partial(id,expected_revision=1,confirmed=False)
  self.s.allow_partial(id,expected_revision=self.s.get(id)['revision'],confirmed=True);self.assertEqual(self.s.get(id)['spec']['target'],2);self.assertTrue(self.s.can_start(id)['stageEligible'])
 def test_scope_fifo_but_other_market_independent(self):
  old=self.create();self.now+=1;new=self.create('two');other=self.create('mx',self.spec|{'market':'mx'})
  self.fill(new);self.fill(other)
  self.assertIn('earlier_task_in_scope',self.s.can_start(new)['reasons']);self.assertTrue(self.s.can_start(other)['stageEligible'])
 def test_member_market_and_selected_product_scope_are_enforced(self):
  id=self.create()
  with self.assertRaisesRegex(BatchError,'scope_mismatch'):self.s.record_member(id,self.member(1)|{'market':'mx'})
  scoped=self.create('scoped',self.spec|{'productScope':{'kind':'pids','values':['1729480033890900437']}})
  with self.assertRaisesRegex(BatchError,'outside_product_scope'):self.s.record_member(scoped,self.member(1)|{'pid':'1729480050548447690'})
  self.s.record_member(scoped,self.member(1)|{'pid':'1729480033890900437'})
 def test_cross_midnight_and_manual_reply_pause(self):
  spec=normalize_spec(self.spec)
  for hour,allowed in [(22,True),(0,True),(1,False),(12,False)]:
   at=datetime(2026,9,14,hour,0,tzinfo=ZoneInfo('Asia/Shanghai')).timestamp();self.assertEqual(in_window(spec,at),allowed)
  dated=normalize_spec(self.spec|{'startDate':'2026-09-14'})
  self.assertFalse(in_window(dated,datetime(2026,9,14,0,30,tzinfo=ZoneInfo('Asia/Shanghai')).timestamp()))
  self.assertTrue(in_window(dated,datetime(2026,9,15,0,30,tzinfo=ZoneInfo('Asia/Shanghai')).timestamp()))
  self.assertFalse(reply_stage_enabled(task_allows=True,manual_pause=True,scope_phase='replying'))
  self.assertFalse(reply_stage_enabled(task_allows=True,manual_pause=False,scope_phase='sending'))
  self.assertTrue(reply_stage_enabled(task_allows=True,manual_pause=False,scope_phase='replying'))
if __name__=='__main__':unittest.main()
