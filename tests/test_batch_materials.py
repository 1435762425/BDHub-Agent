import sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from lib.batch_task_service import TaskService
from lib.batch_materials import BatchMaterials,apply_materials
from lib.second_cycle import digest,CycleError
from test_second_cycle import offer,NOW
class MaterialTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.s=TaskService(Path(self.tmp.name)/'db',lambda:NOW)
  self.spec={'institution':'bjn-local-research','market':'it','target':2,'startDate':'2026-09-15','startTime':'22:00','endTime':'01:00'}
  c=self.s.preview(self.spec);self.task=self.s.confirm(c['token'],'t');self.id=self.task['id'];self.jobs=BatchMaterials(self.s)
  self.offer=offer(title='Cuscino',campaignId='2',catalogSource='selected')
  self.members=[{'oec':str(i),'institution':self.spec['institution'],'market':'it','pid':'1','offerKey':'o1','offerFingerprint':digest(self.offer),'materialKey':'one-card','checks':dict(source=True,identity=True,relationship=True,offer=True,name=True,taplink=False)} for i in range(1,4)]
  self.report={'state':'waiting_adapters','target':2,'candidates':3,'blockers':['task_taplink_adapter_pending'],'stages':[{'key':'taplink','done':0,'required':3}]}
  self.card={'state':'verified_read_only','pid':'1','sourceCampaignId':'2','creatorPercent':'12','listId':'999','wireCampaignId':'0','checkedAt':NOW,'evidenceRefs':['proof']}
 def tearDown(self):self.s.close();self.tmp.cleanup()
 def test_one_material_for_many_members_and_full_gate_freezes(self):
  self.jobs.plan(self.task,self.members,[self.offer]);self.jobs.plan(self.task,self.members,[self.offer]);self.assertEqual(len(self.jobs.pending(self.id,self.members)),1)
  self.jobs.save(self.id,'one-card','ready',self.card)
  self.s.tick(lambda spec:(self.report,self.members))
  self.assertEqual(self.s.tasks.get(self.id)['state'],'ready');self.assertEqual(self.s.tasks.readiness(self.id)['ready'],3);self.assertFalse(self.s.tasks.can_start(self.id)['platformDispatchAuthorized'])
 def test_missing_card_or_unknown_does_not_freeze(self):
  self.jobs.plan(self.task,self.members,[self.offer]);self.jobs.save(self.id,'one-card','waiting',error='task_card_creation_unresolved')
  r,m=apply_materials(self.s,self.id,self.report,self.members);self.assertEqual(r['verifiedReady'],0);self.assertIn('task_card_creation_unresolved',r['blockers'])
 def test_wrong_commission_or_campaign_not_accepted(self):
  self.jobs.plan(self.task,self.members,[self.offer])
  for changes in ({'creatorPercent':'15'},{'sourceCampaignId':'9'},{'wireCampaignId':'2'},{'evidenceRefs':[]}):
   with self.assertRaises(CycleError):self.jobs.save(self.id,'one-card','ready',self.card|changes)
 def test_changed_offer_cannot_reuse_prior_material(self):
  self.jobs.plan(self.task,self.members,[self.offer]);self.jobs.save(self.id,'one-card','ready',self.card)
  members=[m|{'offerFingerprint':digest(self.offer|{'creatorPercent':'13'})} for m in self.members]
  self.assertEqual(apply_materials(self.s,self.id,self.report,members)[0]['verifiedReady'],0)
 def test_pause_prevents_late_material_commit(self):
  self.jobs.plan(self.task,self.members,[self.offer]);self.s.control(self.id,'pause',1);self.jobs.save(self.id,'one-card','ready',self.card)
  self.assertEqual(self.jobs.pending(self.id,self.members)[0]['state'],'queued')
if __name__=='__main__':unittest.main()
