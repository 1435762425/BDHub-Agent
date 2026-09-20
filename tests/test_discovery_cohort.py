import json,sqlite3,sys,tempfile,unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from lib.creator_discovery import CreatorDiscoveryStore,CreatorDiscoveryWorker,preview
from lib.creator_identity import CreatorIdentityStore
from lib.discovery_cohort import run_cohort,slice_report
from test_creator_discovery import report_for,T0
class CohortTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.store=CreatorDiscoveryStore(self.root,now=lambda:T0)
  self.ids=CreatorIdentityStore(self.root/'creator-identities.sqlite')
  text='\n'.join('new'+str(i) for i in range(20));label='Kalodata 二发线索身份解析';v=preview('it',label,text);self.batch=self.store.submit('it',label,text,v['previewHash'],'test')['id']
  with closing(sqlite3.connect(self.root/'second-cycle.sqlite')) as c,c:
   c.executescript('CREATE TABLE plan(id,state,market); CREATE TABLE cycle_identity_outbox(plan_id,batch_id,settled);');c.execute("INSERT INTO plan VALUES('p','active','it')");c.execute('INSERT INTO cycle_identity_outbox VALUES(?,?,0)',('p',self.batch))
  self.calls=0
 def tearDown(self):self.ids.close();self.store.close();self.tmp.cleanup()
 def executor(self,path,output):
  self.calls+=1;targets=json.loads(path.read_text())['targets'];parts=[]
  for i,t in enumerate(targets):
   r=report_for(t,oec=str(123000+i));r['identityOnly']=True;r['targets'][0].update(status='identity_verified',profiles=[],profileCollection='not_requested');r['requests']=r['requests'][:1];parts.append(r)
  body=parts[0]|{'targets':[r['targets'][0] for r in parts],'requests':[r['requests'][0] for r in parts]}
  output.mkdir(parents=True);(output/'report.private.json').write_text(json.dumps(body))
 def test_twenty_targets_one_process_all_proofs_separate(self):
  w=CreatorDiscoveryWorker(self.store,executor=self.executor);r=run_cohort(w)
  self.assertEqual(self.calls,1);self.assertEqual(r['targets'],20);self.assertEqual(self.store.detail(self.batch)['batch']['counts']['completed'],20)
  self.assertEqual(self.ids._db.execute('SELECT count(*) FROM creator_identity').fetchone()[0],20)
  self.assertEqual(self.store._db.execute("SELECT count(*) FROM discovery_item WHERE status='running'").fetchone()[0],0)
 def test_fifty_targets_stay_in_one_bounded_process(self):
  text='\n'.join('fifty'+str(i) for i in range(50));v=preview('it','fifty',text);batch=self.store.submit('it','fifty',text,v['previewHash'],'fifty')['id']
  with closing(sqlite3.connect(self.root/'second-cycle.sqlite')) as c,c:
   c.execute('INSERT INTO cycle_identity_outbox VALUES(?,?,0)',('p',batch))
  w=CreatorDiscoveryWorker(self.store,executor=self.executor);r=run_cohort(w,50,only_batch=batch)
  self.assertEqual(r['targets'],50);self.assertEqual(self.store.detail(batch)['batch']['counts']['completed'],50)
 def test_claim_is_bounded_and_other_workers_cannot_join(self):
  g=self.store.claim_cohort('one',[self.batch],10);self.assertEqual(len(g['items']),10)
  self.assertIsNone(self.store.claim('two'));self.assertIsNone(self.store.claim_cohort('two',[self.batch]))
  self.assertIsNone(self.store.recover_cohort('two'))
 def test_duplicate_prefix_does_not_shrink_a_full_cohort(self):
  batches=[]
  for i in range(70):
   label='older'+str(i);v=preview('it',label,'duplicate');batches.append(self.store.submit('it',label,'duplicate',v['previewHash'],'dup'+str(i))['id'])
  self.store.now=lambda:T0+100
  text='\n'.join('unique'+str(i) for i in range(19));v=preview('it','new',text);batches.append(self.store.submit('it','new',text,v['previewHash'],'unique')['id'])
  group=self.store.claim_cohort('worker',batches,20);self.assertEqual(len(group['items']),20);self.assertEqual(len({i['handle'] for i in group['items']}),20)
 def test_recovery_replays_saved_group_without_network(self):
  old=self.store.claim_cohort('old',[self.batch],10)
  folder=self.store.output_root/'cohorts'/old['id'];folder.mkdir(parents=True);file=folder/'targets.private.json'
  file.write_text(json.dumps({'targets':[{'ref':i['id'],'externalId':i['id'],'handle':i['handle']} for i in old['items']]}));self.executor(file,folder/'output')
  with patch('lib.creator_discovery._alive',return_value=False):
   run_cohort(CreatorDiscoveryWorker(self.store,executor=lambda *a: self.fail('unexpected network')))
  self.assertEqual(self.store.detail(self.batch)['batch']['counts']['completed'],10)
 def test_sibling_failure_keeps_verified_read_and_unrequested_is_not_success(self):
  item={'id':'a','handle':'alice'};r=report_for({'ref':'a','handle':'alice','externalId':'a'})
  r.update(status='blocked',reason='remote_error',identityOnly=True);r['targets'][0].update(status='identity_verified',profiles=[],profileCollection='not_requested');r['requests']=r['requests'][:1]
  self.assertEqual(slice_report(r,item)['status'],'completed');self.assertIsNone(slice_report(r,{'id':'b'}))
  r['identityFileUnchanged']=False;self.assertEqual(slice_report(r,item)['status'],'blocked')
 def test_an_unrequested_cohort_sibling_does_not_consume_a_real_attempt(self):
  group=self.store.claim_cohort('worker',[self.batch],10);item=group['items'][0]
  self.store.defer_busy(item,'worker',consume_attempt=False)
  row=self.store._db.execute('SELECT status,attempt_no FROM discovery_item WHERE id=?',(item['id'],)).fetchone()
  self.assertEqual(tuple(row),('queued',1))
 # 「被挡住」＝问过但没拿到平台的回答：必须能重试，而且每一次尝试的取证不能互相覆盖。
 def test_a_blocked_item_is_claimed_again_with_its_own_attempt_directory(self):
  store=self.store;item_id=self.store.detail(self.batch)['items'][0]['id']
  with store.transaction():
   store._db.execute("UPDATE discovery_item SET status='blocked',reason='request_or_signer_error',retry_at=0,attempt_no=1 WHERE id=?",(item_id,))
  # 单条路径（soak/旧入口）默认不碰被挡住的项；补 OECID 走的 claim_cohort 开着重试。
  off={r['id'] for r in store._next_items(20,[self.batch],distinct=True,retry_blocked=False)}
  self.assertNotIn(item_id,off)
  on={r['id'] for r in store._next_items(20,[self.batch],distinct=True,retry_blocked=True)}
  self.assertIn(item_id,on)
  # 领到 = 又尝试了一次：attempt_no +1，于是这一轮的取证写进 attempt-2/，上一次的原样留档。
  group=store.claim_cohort('w',[self.batch],1)
  self.assertEqual(group['items'][0]['attempt_no'],2)
  self.assertEqual(store.detail(self.batch)['batch']['counts']['retryableBlocked'],0)

 def test_attempts_are_capped_so_a_hopeless_item_stops_holding_the_batch(self):
  store=self.store;item_id=self.store.detail(self.batch)['items'][0]['id']
  with store.transaction():
   store._db.execute("UPDATE discovery_item SET status='blocked',reason='request_or_signer_error',retry_at=0,attempt_no=2 WHERE id=?",(item_id,))
  # 还有一次机会：批次不算结束，outbox 不能结算掉。
  self.assertEqual(store.detail(self.batch)['batch']['counts']['retryableBlocked'],1)
  self.assertIn(item_id,{r['id'] for r in store._next_items(20,[self.batch],distinct=True,retry_blocked=True)})
  with store.transaction():
   store._db.execute("UPDATE discovery_item SET attempt_no=3 WHERE id=?",(item_id,))
  # 三次用尽：不再自动重试，也不再吊着 outbox。
  self.assertNotIn(item_id,{r['id'] for r in store._next_items(20,[self.batch],distinct=True,retry_blocked=True)})
  self.assertEqual(store.detail(self.batch)['batch']['counts']['retryableBlocked'],0)

if __name__=='__main__':unittest.main()
