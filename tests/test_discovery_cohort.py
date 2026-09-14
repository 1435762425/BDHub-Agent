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
 def test_claim_is_bounded_and_other_workers_cannot_join(self):
  g=self.store.claim_cohort('one',[self.batch],10);self.assertEqual(len(g['items']),10)
  self.assertIsNone(self.store.claim('two'));self.assertIsNone(self.store.claim_cohort('two',[self.batch]))
  self.assertIsNone(self.store.recover_cohort('two'))
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
if __name__=='__main__':unittest.main()
