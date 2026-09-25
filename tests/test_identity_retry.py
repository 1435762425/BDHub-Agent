import json,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock,patch
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from lib.second_cycle import CycleStore
from lib.schema_migrations import apply_database
from lib.identity_retry import record,snapshot,classify,recover_started,project_isolated,locked
from lib.creator_discovery import CreatorDiscoveryStore,preview

class IdentityBudgetTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.now=1800000000.
  with CycleStore(self.root/'var/second-cycle.sqlite') as store:self.plan=store.plan('bjn-local-research','it')
  apply_database(self.root,'second-cycle')
 def tearDown(self):self.tmp.cleanup()
 def record(self,handle,event,category='individual',market='it',reason='find_business_error'):
  record(self.root,market,'acc6',handle,event,category,reason,now=self.now)
 def test_one_budget_across_source_restart_and_case_normalization(self):
  self.record('@Alice','pid-1');self.record('alice','pid-1')
  self.assertEqual(snapshot(self.root,'it',now=self.now)['deferred'],{'alice'})
  self.now+=300;self.record('ALICE','pid-2')
  self.assertEqual(snapshot(self.root,'it',now=self.now)['retryAt']['alice'],self.now+1800)
  self.now+=1800;self.record('alice','video-3')
  self.assertEqual(snapshot(self.root,'it',now=self.now+7*86400)['isolated'],{'alice'})
  self.assertEqual(snapshot(self.root,'uk',now=self.now)['isolated'],set())
  with CycleStore(self.root/'var/second-cycle.sqlite',readonly=True) as s:
   self.assertEqual(s.db.execute('SELECT failures FROM identity_handle_budget').fetchone()[0],3)
   self.assertFalse(s.db.execute("SELECT 1 FROM cycle_identity_outcome WHERE status='unresolved'").fetchone())
 def test_shared_cohort_fault_is_one_account_incident_and_spends_no_handles(self):
  for h in ['a','b','c']:self.record(h,'same-cohort','shared',reason='request_or_signer_error')
  state=snapshot(self.root,'it',now=self.now)
  self.assertEqual(state['accountWait']['failures'],1);self.assertEqual(state['accountWait']['next_at'],self.now+60)
  self.assertFalse(state['isolated'] or state['deferred'])
  self.now+=60;self.record('a','next-cohort','shared')
  self.assertEqual(snapshot(self.root,'it',now=self.now)['accountWait']['next_at'],self.now+900)
  self.now+=900;self.record('a','third-cohort','shared')
  self.assertEqual(snapshot(self.root,'it',now=self.now)['accountWait']['next_at'],self.now+3600)
  self.assertIsNone(snapshot(self.root,'it',now=self.now+3600)['accountWait'])
 def test_it_new_discovery_batch_cannot_bypass_isolated_handle(self):
  for i in range(3):self.record('alice','old-'+str(i));self.now+=1800
  with CreatorDiscoveryStore(self.root/'var',now=lambda:self.now) as ds:
   a=preview('it','new source','alice\nbob');batch=ds.submit('it','new source','alice\nbob',a['previewHash'],'new-source-00001')
   group=ds.claim_cohort('worker',[batch['id']],limit=20)
   self.assertEqual([i['handle'] for i in group['items']],['bob'])
 def test_waiting_account_prevents_all_new_find_claims(self):
  self.record('a','cohort','shared')
  with CreatorDiscoveryStore(self.root/'var',now=lambda:self.now) as ds:
   a=preview('it','source','newperson');batch=ds.submit('it','source','newperson',a['previewHash'],'new-source-00002')
   self.assertIsNone(ds.claim_cohort('worker',[batch['id']],limit=20))
 def test_saved_probe_is_applied_once_without_new_platform_read(self):
  report={'requests':[{'stage':'find','targetRef':'t','httpStatus':200,'status':'returned','code':'7','verificationRequired':False}],'targets':[]}
  file=self.root/'var/saved.json';file.write_text(json.dumps(report))
  evidence={'report':'var/saved.json','retryReport':'var/missing.json','requested':[{'ref':'t','handle':'alice'}]}
  record(self.root,'uk','acc11','alice','probe:started','started',None,evidence=evidence,now=self.now)
  apply=Mock();recover_started(self.root,'uk','acc11',apply);recover_started(self.root,'uk','acc11',apply)
  self.assertEqual(apply.call_count,1)
  with CycleStore(self.root/'var/second-cycle.sqlite',readonly=True) as s:self.assertEqual(s.db.execute('SELECT failures FROM identity_handle_budget').fetchone()[0],1)
 def test_interrupted_probe_with_no_report_is_account_fault_not_no_match(self):
  evidence={'report':'var/missing.json','retryReport':'var/missing2.json','requested':[]}
  record(self.root,'uk','acc11','alice','probe:started','started',None,evidence=evidence,now=self.now)
  apply=Mock();recover_started(self.root,'uk','acc11',apply);apply.assert_not_called()
  state=snapshot(self.root,'uk');self.assertIsNotNone(state['accountWait']);self.assertFalse(state['isolated'] or state['deferred'])
 def test_migration_does_not_rewrite_old_attempts_and_is_idempotent(self):
  with CreatorDiscoveryStore(self.root/'var') as ds:
   a=preview('it','old source','alice');ds.submit('it','old source','alice',a['previewHash'],'legacy-source-001')
   ds._db.execute("UPDATE discovery_item SET status='blocked',attempt_no=8,reason='request_or_signer_error'")
   before=[tuple(r) for r in ds._db.execute('SELECT * FROM discovery_item')]
   self.assertFalse(apply_database(self.root,'second-cycle')['appliedNow'])
   self.assertEqual(before,[tuple(r) for r in ds._db.execute('SELECT * FROM discovery_item')])
 def test_new_published_identity_clears_account_wait_but_not_handle_budget(self):
  self.record('alice','a');self.record('alice','account','shared')
  with CycleStore(self.root/'var/second-cycle.sqlite') as store:
   store.db.execute("INSERT INTO account_identity_generation(generation_id,market,account,role,reason,capability_json,state,created_at,published_at) VALUES('new','it','acc6','communications','refresh','{}','published',?,?)",(self.now,self.now+1))
  state=snapshot(self.root,'it',now=self.now+2)
  self.assertIsNone(state['accountWait']);self.assertEqual(state['deferred'],{'alice'})
 def test_single_handle_failure_allows_later_chunk_while_account_fault_stops_it(self):
  from lib.market_identity import _run
  items=[{'sourceId':str(i),'handle':'person'+str(i),'pid':str(i)} for i in range(6)]
  config={'markets':{'br':{'roles':{'communications':'acc1'}}}}
  calls=[]
  def probe(root,market,account,chunk,targets,tokens,runner,profile_canary,sleep):
   calls.append(chunk)
   value={'requests':[{'stage':'find','targetRef':t['ref'],'httpStatus':200,'status':'returned','code':'999' if t['handle']=='person0' else '0','verificationRequired':False} for t in targets],
    'targets':[{'targetRef':t['ref'],'currentHandleResolved':t['handle']!='person0','find':{'identity':{'handle':t['handle'],'market':'br','oecId':'123'}}} for t in targets]}
   return SimpleNamespace(returncode=0),[self.root/'var/proof'],value,False
  applied={'resolvedHandles':2,'unresolvedHandles':0,'newBindings':2,'profileVerified':0,'blockedHandles':1,'blockCode':'market_identity_blocked'}
  with patch('lib.market_identity.load_config',return_value=config),patch('lib.market_identity.reuse_judgments'), \
    patch('lib.market_identity.pending',return_value={'items':items}),patch('lib.market_identity._probe_with_init_retry',side_effect=probe), \
    patch('lib.market_identity._apply',return_value=applied):
   result=_run(self.root,'br',6,clock=lambda:self.now)
  self.assertEqual(result['networkRuns'],2);self.assertEqual(len(calls),2)
  self.assertEqual(snapshot(self.root,'br',now=self.now)['deferred'],{'person0'})
 def test_runtime_lock_does_not_let_two_consumers_spend_same_handle(self):
  with locked(self.root,'uk'):
   with self.assertRaisesRegex(Exception,'identity_executor_busy'):
    with locked(self.root,'uk'):pass

class ClassificationTests(unittest.TestCase):
 def receipt(self,**changes):return {'targetRef':'a','stage':'find','status':'returned','httpStatus':200,'code':'0','verificationRequired':False}|changes
 def test_effective_no_match_and_confirmed_find_survive_profile_failure(self):
  for target in [{'targetRef':'a','status':'unresolved','reason':'no_exact_handle'}, {'targetRef':'a','currentHandleResolved':True}]:
   value={'status':'blocked','targets':[target],'requests':[self.receipt(),self.receipt(stage='profile',code='100000')]}
   self.assertEqual(classify(value,'a')[0],'success')
 def test_captcha_login_signer_http_and_unattempted_are_not_individual_failures(self):
  for r in [self.receipt(code='16201010'),self.receipt(verificationRequired=True),self.receipt(httpStatus=500),self.receipt(status='error')]:
   self.assertEqual(classify({'requests':[r]},'a')[0],'shared')
  missing=self.receipt(status='error');missing.pop('verificationRequired')
  self.assertEqual(classify({'requests':[missing]},'a'),('shared','transport_unavailable'))
  self.assertEqual(classify({'reason':'probe_initialization_or_validation_error','requests':[]},'a')[0],'shared')
  self.assertEqual(classify({'requests':[self.receipt()]},'unattempted')[0],'unattempted')
 def test_target_business_error_and_malformed_target_spend_individual_budget(self):
  self.assertEqual(classify({'requests':[self.receipt(code='999')]},'a')[0],'individual')
  self.assertEqual(classify({'requests':[self.receipt()],'targets':[{'targetRef':'a'}]},'a')[0],'individual')
