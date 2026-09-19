import os,sys,tempfile,unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from lib.jobs import save  # noqa:E402
from lib.material_maintenance import next_due,scheduler_state,start_scheduler,stop_scheduler,tick  # noqa:E402

TZ=ZoneInfo('Asia/Shanghai')
def at(day,hour):return datetime(2026,9,day,hour,tzinfo=TZ).timestamp()

class FakeJobs:
 def __init__(self):self.calls=[];self.states={}
 def start(self,root,name,config):
  self.calls.append((name,config));record={'name':name,'startedAt':100+len(self.calls),'running':True,'platformWrites':False,'progress':None};self.states[name]=record;return record
 def state(self,root,name):return self.states.get(name)
 def finish(self,name,success=True):
  row=self.states[name];row['running']=False
  row['progress']=({'status':'done' if success else 'blocked'} if name=='campaignCollect'
                   else {'phase':'done' if success else 'read'})

class Schedule(unittest.TestCase):
 def test_daily_and_weekly_due_use_beijing_slots(self):
  daily={'at':'03:00'};weekly={'at':'05:00','weekday':0}
  self.assertEqual(next_due(daily,'campaign',None,at(21,2)),at(21,3))
  self.assertEqual(next_due(daily,'campaign',None,at(21,4)),at(21,3))
  self.assertEqual(next_due(weekly,'selected',None,at(20,12)),at(21,5))
  self.assertEqual(next_due(weekly,'selected',None,at(21,6)),at(21,5))

 def test_campaign_chains_source_then_read_only_links_before_selected_weekly(self):
  with tempfile.TemporaryDirectory() as folder:
   root=Path(folder);jobs=FakeJobs();save(root,{'jobs':{
    'campaign_material_refresh':{'enabled':True,'at':'03:00'},
    'selected_taplink_verify':{'enabled':True,'at':'05:00','weekday':0}}})
   first=tick(root,now=at(21,6),starter=jobs.start,reader=jobs.state)
   self.assertEqual((first['phase'],jobs.calls[0][0]),('campaign_collect','campaignCollect'))
   jobs.finish('campaignCollect');second=tick(root,now=at(21,6)+60,starter=jobs.start,reader=jobs.state)
   self.assertEqual((second['phase'],jobs.calls[1][0]),('campaign_links','linksCampaign'))
   self.assertEqual(jobs.calls[1][1]['creates'],0)
   jobs.finish('linksCampaign');third=tick(root,now=at(21,6)+120,starter=jobs.start,reader=jobs.state)
   self.assertIn('campaign',third['lastSuccess']);self.assertIsNone(third['phase'])
   fourth=tick(root,now=at(21,6)+180,starter=jobs.start,reader=jobs.state)
   self.assertEqual((fourth['phase'],jobs.calls[2][0]),('selected_links','links'))
   self.assertEqual(jobs.calls[2][1]['creates'],0)

 def test_failed_child_keeps_last_success_and_retries_later(self):
  with tempfile.TemporaryDirectory() as folder:
   root=Path(folder);jobs=FakeJobs();save(root,{'jobs':{'campaign_material_refresh':{'enabled':True,'at':'03:00'}}})
   tick(root,now=at(21,6),starter=jobs.start,reader=jobs.state);jobs.finish('campaignCollect',False)
   failed=tick(root,now=at(21,6)+60,starter=jobs.start,reader=jobs.state)
   self.assertEqual(failed['error'],'maintenance_job_incomplete');self.assertEqual(failed['lastSuccess'],{})
   self.assertEqual(len(jobs.calls),1)
   tick(root,now=at(21,6)+120,starter=jobs.start,reader=jobs.state);self.assertEqual(len(jobs.calls),1)
   tick(root,now=at(21,7)+120,starter=jobs.start,reader=jobs.state);self.assertEqual(len(jobs.calls),2)

class FakeChild:
 pid=os.getpid()
class Spawn:
 def __call__(self,*args,**kwargs):return FakeChild()

class Control(unittest.TestCase):
 def test_start_and_stop_only_control_the_local_scheduler(self):
  with tempfile.TemporaryDirectory() as folder:
   root=Path(folder);started=start_scheduler(root,clock=lambda:100,spawn=Spawn())
   self.assertTrue(started['running']);self.assertFalse(started['stopping'])
   stopped=stop_scheduler(root);self.assertTrue(stopped['stopping'])
   with self.assertRaisesRegex(ValueError,'already_running'):start_scheduler(root,spawn=Spawn())

if __name__=='__main__':unittest.main()
