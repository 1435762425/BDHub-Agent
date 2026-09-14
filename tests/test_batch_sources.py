import sys,tempfile,unittest,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from lib.batch_sources import BatchSources,pending_new_handles
from lib.batch_task_service import TaskService
from lib.second_cycle import CycleError,encoded
from test_second_cycle import offer,NOW

class Provider:
 def __init__(self,rows=None,error=None):self.calls=0;self.rows=rows if rows is not None else [{'handle':'creator.one','sale':5,'id':'k1'}];self.error=error
 def request(self,path,body):
  self.calls+=1
  if self.error:raise CycleError(self.error)
  return {'success':True,'data':self.rows}
class SourceTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'db';self.now=NOW;self.s=TaskService(self.path,lambda:self.now);self.q=BatchSources(self.s)
  self.spec={'institution':'bjn-local-research','market':'it','target':3000,'startDate':'2026-09-15','startTime':'22:00','endTime':'01:00'}
  card=self.s.preview(self.spec);self.id=self.s.confirm(card['token'],'one')['id'];self.offers=[offer(str(1729480033890900000+i),title='Cuscino',campaignId='2') for i in range(1000)]
 def tearDown(self):self.s.close();self.tmp.cleanup()
 def test_pending_supply_deduplicates_and_excludes_already_known_people(self):
  edges=[{'sourceId':str(i),'sourceHandle':h} for i,h in enumerate(['known','new','new','done','miss'])]
  self.assertEqual(pending_new_handles(edges,{'3'},{'4'},{'known'}),{'new'})
 def test_whole_goal_plans_330_pids_once_without_daily_quota_cap(self):
  r=self.q.plan(self.id,self.offers,0);self.assertEqual(r['created'],330);self.assertEqual(self.q.plan(self.id,self.offers,0)['created'],0)
  self.assertEqual(self.q.status(self.id)['dailyQuotaUsed'],0)
 def test_waiting_pid_uses_actual_pending_people_not_ten_per_pid(self):
  self.q.plan(self.id,self.offers,0)
  self.s.db.execute("UPDATE batch_source_job SET state='awaiting_identity'")
  r=self.q.plan(self.id,self.offers,0,pending_identity_count=12)
  self.assertEqual(r['created'],329)
 def test_unqueried_products_precede_legacy_queried_products(self):
  self.q.plan(self.id,self.offers,3299,previously_queried_pids={self.offers[0]['pid']})
  self.assertEqual(self.q.claim(self.id)['pid'],self.offers[1]['pid'])
 def test_5000_and_restricted_scope_not_silently_expanded(self):
  card=self.s.preview(self.spec|{'target':5000,'productScope':{'kind':'pids','values':[self.offers[0]['pid']]}});id=self.s.confirm(card['token'],'two')['id']
  r=self.q.plan(id,self.offers,0);self.assertEqual(r['created'],1);self.assertEqual(r['unplannedEstimate'],5490)
 def test_response_checkpoint_survives_failed_import_and_restart(self):
  self.q.plan(self.id,self.offers[:1],0);p=Provider()
  def fail(edges):raise OSError()
  self.assertEqual(self.q.once(self.id,p,fail)['status'],'retry_wait');self.assertEqual(p.calls,1)
  self.now+=61;self.s.close();self.s=TaskService(self.path,lambda:self.now);self.q=BatchSources(self.s)
  added=[];r=self.q.once(self.id,p,lambda edges:added.extend(edges));self.assertEqual(p.calls,1);self.assertEqual(len(added),1);self.assertEqual(r['status'],'page_saved')
 def test_pause_during_http_saves_receipt_without_import_then_resume(self):
  self.q.plan(self.id,self.offers[:1],0);base=Provider();s=self.s;id=self.id
  class Pausing:
   def request(self,path,body):s.control(id,'pause',s.tasks.get(id)['revision']);return base.request(path,body)
  added=[];self.assertEqual(self.q.once(id,Pausing(),added.extend)['status'],'paused_receipt_saved');self.assertEqual(added,[])
  s.control(id,'resume',s.tasks.get(id)['revision']);self.q.once(id,base,added.extend);self.assertEqual(base.calls,1);self.assertEqual(len(added),1)
 def test_auth_error_stops_other_pid_requests_until_explicit_resume(self):
  self.q.plan(self.id,self.offers,0);p=Provider(error='kalodata_auth_required')
  self.assertEqual(self.q.once(self.id,p,lambda e:None)['status'],'blocked');self.assertEqual(self.q.once(self.id,p,lambda e:None)['status'],'idle');self.assertEqual(p.calls,1)
  self.s.control(self.id,'pause',self.s.tasks.get(self.id)['revision']);self.s.control(self.id,'resume',self.s.tasks.get(self.id)['revision']);self.assertIsNotNone(self.q.claim(self.id))
 def test_local_only_confirmation_never_gains_new_scope(self):
  policy=self.s.detail(self.id)['policy']|{'authorizationScope':'local_preparation_only'};self.s.db.execute('UPDATE batch_policy SET payload=? WHERE task_id=?',(encoded(policy),self.id))
  self.assertEqual(self.q.plan(self.id,self.offers,0)['created'],0);self.assertIsNone(self.q.claim(self.id))
 def test_repeated_page_blocks_and_page_cap_is_not_exhaustive_coverage(self):
  self.q.plan(self.id,self.offers[:1],0);p=Provider([{'id':f'k{i}','handle':f'creator{i}','sale':1} for i in range(50)])
  first=self.q.once(self.id,p,lambda e:None);self.assertEqual(first['coverage'],'more_possible')
  second=self.q.once(self.id,p,lambda e:None);self.assertEqual(second['error'],'kalodata_repeated_page')
 def test_claim_fence_prevents_late_response_from_replacing_new_owner(self):
  self.q.plan(self.id,self.offers[:1],0);old=self.q.claim(self.id);self.now+=121;new=self.q.claim(self.id)
  self.assertGreater(new['fence'],old['fence'])
  with self.assertRaisesRegex(CycleError,'lease_lost'):self.q._owned(old)
 def test_source_status_does_not_commit_enclosing_confirmation_transaction(self):
  card=self.s.preview(self.spec);self.assertEqual(self.s.confirm(card['token'],'one')['id'],self.id)
if __name__=='__main__':unittest.main()
