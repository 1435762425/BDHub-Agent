from pathlib import Path
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))

from lib.kalodata_video_evidence import VIDEO_DETAIL_PATH,VIDEO_LIST_PATH  # noqa:E402
from unittest import mock  # noqa:E402

from lib.kalodata_video_scan import initialize,scan_one,status  # noqa:E402
from lib.schema_migrations import apply_database  # noqa:E402
from lib.second_cycle import CycleError,CycleStore  # noqa:E402


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

 def authorless(self,calls,missing):
  def request(path,payload):
   calls.append((path,payload['id']))
   if path==VIDEO_LIST_PATH:
    return {'success':True,'data':[
     {'id':video,'views':'1,500','sale':'0','revenue':'€0.00','create_time':f'2026/09/{12-rank}',
      'duration':'20s','description':'qualified','content_type':'video','ad':0,'ai_video':0}
     for rank,video in enumerate(('7674344776354860301','7674344776354860302','7674344776354860303'))]}
   if payload['id'] in missing:return {'success':True,'data':{'id':payload['id'],'views':'1,500'}}
   return {'success':True,'data':{'id':payload['id'],'handle':'creator.one','creator_id':'7592705921965884434',
      'views':'1,500','sale':'0','revenue':'€0.00','release_time':'2026/09/10','description':'qualified'}}
  return request

 def test_video_without_author_is_recorded_and_the_scan_goes_on(self):
  generation=initialize(self.root,[{'pid':PID,'units':300}],'2026-08-20','2026-09-18',clock=lambda:2.)
  calls=[];result=scan_one(self.root,generation['generationId'],
                           self.authorless(calls,{'7674344776354860301'}),clock=lambda:3.)
  self.assertEqual((result['status'],result['videos'],result['authorMissing'],result['leads'],result['networkRequests']),
                   ('completed',2,1,1,4))
  snapshot=status(self.root,generation['generationId'])
  self.assertEqual((snapshot['state'],snapshot['authorMissing'],snapshot['detailRequests']),('completed',1,3))
  with CycleStore(self.root/'var/second-cycle.sqlite') as store:
   skipped=store.db.execute("SELECT detail_state,handle,detail_payload_hash FROM kalodata_video_scan_item "
                            "WHERE video_id='7674344776354860301'").fetchone()
   self.assertEqual((skipped[0],skipped[1],len(skipped[2])),('author_missing',None,64))
   self.assertEqual(store.db.execute('SELECT count(*) FROM kalodata_video_author_cache '
                                     "WHERE video_id='7674344776354860301'").fetchone()[0],0)
   self.assertEqual(store.db.execute('SELECT video_id FROM video_lead_current').fetchone()[0],'7674344776354860302')
   self.assertEqual(tuple(store.db.execute('SELECT state,coverage,qualifying_videos,resolved_videos FROM kalodata_video_run')
                          .fetchone()),('completed_with_gaps','complete',3,2))

 def test_many_videos_without_author_still_stop_the_scan(self):
  generation=initialize(self.root,[{'pid':PID,'units':300}],'2026-08-20','2026-09-18',clock=lambda:2.)
  with mock.patch('lib.kalodata_video_scan.AUTHOR_MISSING_LIMIT',1),self.assertRaisesRegex(CycleError,'kalodata_video_author_missing'):
   scan_one(self.root,generation['generationId'],
            self.authorless([],{'7674344776354860301','7674344776354860302'}),clock=lambda:3.)
  with CycleStore(self.root/'var/second-cycle.sqlite') as store:
   self.assertEqual(dict(store.db.execute('SELECT video_id,detail_state FROM kalodata_video_scan_item')),
                    {'7674344776354860301':'author_missing','7674344776354860302':'pending',
                     '7674344776354860303':'pending'})
   self.assertEqual(store.db.execute('SELECT state FROM kalodata_video_scan_job').fetchone()[0],'detailing')
   self.assertEqual(store.db.execute('SELECT count(*) FROM video_lead_current').fetchone()[0],0)


if __name__=='__main__':unittest.main()
