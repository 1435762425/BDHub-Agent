import copy,json,sys,unittest,tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from lib.market_accounts import validate_config,maintenance_plan,route_proposal,evidence_summary,catalog_read_account
ROOT=Path(__file__).resolve().parents[1]
class AccountPolicyTests(unittest.TestCase):
 def config(self):return json.loads((ROOT/'config/market-accounts.json').read_text())
 def test_pair_cannot_share_accounts_between_markets(self):
  c=self.config();self.assertEqual(validate_config(c)['markets']['it']['accounts'],['acc6','acc9'])
  c['markets']['mx']=copy.deepcopy(c['markets']['it'])
  with self.assertRaises(ValueError):validate_config(c)
 def test_maintenance_authority_mismatch_and_unimplemented_switch_rejected(self):
  c=self.config();c['lifecycle']['enableNewMaintenanceWorker']=False
  with self.assertRaises(ValueError):validate_config(c)
  c=self.config();c['markets']['it']['automaticRoleSwitchEnabled']=True
  with self.assertRaises(ValueError):validate_config(c)
 def test_maintenance_due_uses_login_success_and_drains_writes(self):
  args=dict(last_success=100,role='supply',active_writes=0,other_maintaining=False,policy=self.config()['lifecycle'])
  for role in ('supply','communications'):
   self.assertEqual(maintenance_plan(now=100+72*3600-1,**(args|{'role':role}))['decision'],'not_due')
   self.assertEqual(maintenance_plan(now=100+72*3600,**(args|{'role':role}))['decision'],'maintenance_ready')
  self.assertEqual(maintenance_plan(now=100+72*3600,**(args|{'active_writes':1}))['decision'],'drain_inflight')
  self.assertEqual(maintenance_plan(now=100+72*3600,**(args|{'other_maintaining':True}))['decision'],'wait_maintenance_slot')
  self.assertEqual(maintenance_plan(now=100+72*3600-1,operation='identity_refresh',**args)['decision'],'not_due')
  self.assertEqual(maintenance_plan(now=100+72*3600,operation='identity_refresh',**args)['decision'],'maintenance_ready')
 def test_periodic_checks_and_early_login_stay_disabled(self):
  c=self.config();self.assertIsNone(c['lifecycle']['healthPollMinutes']);self.assertIsNone(c['lifecycle']['deepCheckHours'])
  for key,value in [('healthPollMinutes',5),('deepCheckHours',6),('standbyEarlyMaintenanceHours',2)]:
   changed=copy.deepcopy(c);changed['lifecycle'][key]=value
   with self.assertRaises(ValueError):validate_config(changed)
 def test_routing_obeys_capability_pause_and_unknown_affinity(self):
  pair=self.config()['markets']['it'];states={'acc6':{'health':'healthy','busy':False,'capabilities':{'send':'verified'}},'acc9':{'health':'healthy','busy':False,'capabilities':{'catalog_read':'verified'}}}
  self.assertEqual(route_proposal(pair,'catalog_read',states)['account'],'acc9')
  self.assertEqual(route_proposal(pair,'send',states,paused=True)['state'],'paused')
  self.assertEqual(route_proposal(pair,'send',states|{'acc6':{'busy':True}})['state'],'waiting_verified_account')
  self.assertEqual(route_proposal(pair,'send',states,pinned_account='acc6',result_unknown=True)['account'],'acc6')
  self.assertEqual(route_proposal(pair,'send',states,paused=True,pinned_account='acc6',result_unknown=True)['state'],'verify_original')
  self.assertFalse(route_proposal(pair,'catalog_read',states)['execute'])
 def test_read_evidence_cannot_be_reused_for_another_market_or_sequential_guards(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);(root/'var').mkdir();pair=self.config()['markets']['it'];pair['validationEvidence']='var/proof.json'
   report={'passed':True,'sameInstitution':True,'sameMarket':True,'independentGuardsOverlapSeconds':2,'accounts':[{'account':a,'market':'it','state':'passed_readonly','identityFileUnchanged':True,'startedAt':1,'capabilities':{}} for a in pair['accounts']]}
   path=root/'var/proof.json';path.write_text(json.dumps(report))
   self.assertEqual(evidence_summary(root,pair)['state'],'verified_readonly')
   self.assertEqual(evidence_summary(root,pair,'mx')['state'],'not_verified')
   report['independentGuardsOverlapSeconds']=0;path.write_text(json.dumps(report))
   self.assertEqual(evidence_summary(root,pair)['state'],'not_verified')
 def test_catalog_routing_requires_evidence_and_preserves_pinned_actor(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);(root/'config').mkdir();(root/'var').mkdir();cfg=self.config();pair=cfg['markets']['it'];pair['validationEvidence']='var/proof.json'
   (root/'config/market-accounts.json').write_text(json.dumps(cfg))
   with self.assertRaises(ValueError):catalog_read_account(root)
   proof={'passed':True,'sameInstitution':True,'sameMarket':True,'independentGuardsOverlapSeconds':1,'accounts':[{'account':a,'market':'it','state':'passed_readonly','identityFileUnchanged':True,'startedAt':1,'capabilities':{'catalog_read':'verified'}} for a in pair['accounts']]}
   (root/'var/proof.json').write_text(json.dumps(proof))
   self.assertEqual(catalog_read_account(root),'acc9');self.assertEqual(catalog_read_account(root,'acc6'),'acc6')
   with self.assertRaises(ValueError):catalog_read_account(root,'acc11')
if __name__=='__main__':unittest.main()
