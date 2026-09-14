import copy,json,sys,unittest,tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from lib.market_accounts import validate_config,maintenance_plan,route_proposal,evidence_summary
ROOT=Path(__file__).resolve().parents[1]
class AccountPolicyTests(unittest.TestCase):
 def config(self):return json.loads((ROOT/'config/market-accounts.json').read_text())
 def test_pair_cannot_share_accounts_between_markets(self):
  c=self.config();self.assertEqual(validate_config(c)['markets']['it']['accounts'],['acc6','acc9'])
  c['markets']['mx']=copy.deepcopy(c['markets']['it'])
  with self.assertRaises(ValueError):validate_config(c)
 def test_double_maintenance_and_unimplemented_switch_rejected(self):
  c=self.config();c['lifecycle']['enableNewMaintenanceWorker']=True
  with self.assertRaises(ValueError):validate_config(c)
  c=self.config();c['markets']['it']['automaticRoleSwitchEnabled']=True
  with self.assertRaises(ValueError):validate_config(c)
 def test_maintenance_due_uses_login_success_and_drains_writes(self):
  args=dict(last_success=100,role='supply',active_writes=0,other_maintaining=False)
  self.assertEqual(maintenance_plan(now=100+46*3600-1,**args)['decision'],'not_due')
  self.assertEqual(maintenance_plan(now=100+46*3600,**args)['decision'],'maintenance_ready')
  self.assertEqual(maintenance_plan(now=100+48*3600,**(args|{'active_writes':1}))['decision'],'drain_inflight')
  self.assertEqual(maintenance_plan(now=100+48*3600,**(args|{'other_maintaining':True}))['decision'],'wait_maintenance_slot')
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
if __name__=='__main__':unittest.main()
