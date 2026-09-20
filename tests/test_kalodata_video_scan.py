from pathlib import Path
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))

from lib.kalodata_video_evidence import VIDEO_DETAIL_PATH,VIDEO_LIST_PATH  # noqa:E402
from lib.kalodata_video_scan import initialize,scan_one,status  # noqa:E402
from lib.schema_migrations import apply_database  # noqa:E402
from lib.second_cycle import CycleStore  # noqa:E402


PID='1729000000000000001'


class VideoScanTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);(self.root/'var').mkdir()
  with CycleStore(self.root/'var/second-cycle.sqlite'):pass
  apply_database(self.root,'second-cycle',clock=lambda:1.)
 def tearDown(self):self.temp.cleanup()
 def requester(self,calls):
  def request(path,payload):
   calls.append((path,payload['id']))
   if path==VIDEO_LIST_PATH:
    return {'success':True,'data':[
     {'id':'7674344776354860309','views':'999','sale':'0','revenue':'€0.00',
      'create_time':'2026/09/11','duration':'20s','description':'small','content_type':'video','ad':0,'ai_video':0},
     {'id':'7674344776354860308','views':'1,200','sale':'0','revenue':'€0.00',
      'create_time':'2026/09/10','duration':'20s','description':'qualified','content_type':'video','ad':0,'ai_video':0}]}
   self.assertEqual(path,VIDEO_DETAIL_PATH)
   return {'success':True,'data':{'id':payload['id'],'handle':'creator.one','creator_id':'7592705921965884434',
      'views':'1,200','sale':'0','revenue':'€0.00','release_time':'2026/09/10','description':'qualified'}}
  return request

 def test_full_pid_is_checkpointed_published_and_counted(self):
  generation=initialize(self.root,[{'pid':PID,'units':300}],'2026-08-20','2026-09-18',clock=lambda:2.)
  calls=[];result=scan_one(self.root,generation['generationId'],self.requester(calls),clock=lambda:3.)
  self.assertEqual((result['status'],result['videos'],result['leads'],result['networkRequests']),
                   ('completed',1,1,2))
  snapshot=status(self.root,generation['generationId'])
  self.assertEqual((snapshot['state'],snapshot['completed'],snapshot['remaining'],snapshot['videoLeads']),
                   ('completed',1,0,1))
  with CycleStore(self.root/'var/second-cycle.sqlite') as store:
   row=store.db.execute('SELECT video_id,views,handle FROM video_lead_current').fetchone()
   self.assertEqual(tuple(row),('7674344776354860308',1200,'creator.one'))

 def test_author_cache_avoids_repeating_video_detail_in_a_new_generation(self):
  first=initialize(self.root,[{'pid':PID,'units':300}],'2026-08-20','2026-09-18',clock=lambda:2.)
  scan_one(self.root,first['generationId'],self.requester([]),clock=lambda:3.)
  second=initialize(self.root,[{'pid':PID,'units':300}],'2026-08-20','2026-09-18',min_views=1100,clock=lambda:4.)
  calls=[];result=scan_one(self.root,second['generationId'],self.requester(calls),clock=lambda:5.)
  self.assertEqual(result['networkRequests'],1)
  self.assertEqual([path for path,_ in calls],[VIDEO_LIST_PATH])


if __name__=='__main__':unittest.main()
