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

 def test_a_video_repeated_on_the_next_page_is_stored_once(self):
  generation=initialize(self.root,[{'pid':PID,'units':300}],'2026-08-20','2026-09-18',clock=lambda:2.)
  first=[str(7674344776354860400+n) for n in range(50)]
  # A late-indexed video moved the list down one place: page 2 starts with page 1's last video.
  second=[first[-1],'7674344776354860300','7674344776354860299']
  def request(path,payload):
   if path==VIDEO_LIST_PATH:
    ids=first if payload['pageNo']==1 else second
    return {'success':True,'data':[{'id':video,'views':'1,500','sale':'0','revenue':'€0.00',
      'create_time':'2026/09/15' if payload['pageNo']==1 else '2026/09/'+('15' if video==first[-1] else '10'),
      'duration':'20s','description':'qualified','content_type':'video','ad':0,'ai_video':0} for video in ids]}
   return {'success':True,'data':{'id':payload['id'],'handle':'creator.one','creator_id':'7592705921965884434',
      'views':'1,500','sale':'0','revenue':'€0.00','release_time':'2026/09/10','description':'qualified'}}
  result=scan_one(self.root,generation['generationId'],request,clock=lambda:3.)
  self.assertEqual((result['status'],result['pages'],result['videos']),('completed',2,52))
  with CycleStore(self.root/'var/second-cycle.sqlite') as store:
   self.assertEqual(store.db.execute('SELECT count(*),count(DISTINCT video_id) FROM kalodata_video_scan_item').fetchone()[:],
                    (52,52))

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

 def projection(self):
  with CycleStore(self.root/'var/second-cycle.sqlite',readonly=True) as store:
   return ([tuple(r) for r in store.db.execute('SELECT pid,run_id FROM kalodata_video_head ORDER BY pid')],
           [tuple(r) for r in store.db.execute('SELECT pid,run_id,video_id FROM video_lead_current ORDER BY pid')])

 def test_initialization_and_failed_scan_preserve_previous_complete_pids(self):
  second_pid='1729000000000000002';scope=[{'pid':PID,'units':300},{'pid':second_pid,'units':200}]
  first=initialize(self.root,scope,'2026-08-20','2026-09-18',clock=lambda:2.)['generationId']
  scan_one(self.root,first,self.requester([]),clock=lambda:3.)
  scan_one(self.root,first,self.requester([]),clock=lambda:4.)
  before=self.projection()
  newer=initialize(self.root,scope,'2026-08-21','2026-09-19',clock=lambda:5.)['generationId']
  self.assertEqual(self.projection(),before)
  with self.assertRaisesRegex(CycleError,'quota'):
   scan_one(self.root,newer,lambda *_:(_ for _ in ()).throw(CycleError('quota')),clock=lambda:6.)
  self.assertEqual(self.projection(),before)
  # A completed empty PID removes its old projection only. The second incomplete PID stays.
  scan_one(self.root,newer,lambda *_:{'success':True,'data':[]},clock=lambda:7.)
  head,rows=self.projection()
  self.assertEqual([r[0] for r in rows],[second_pid])
  self.assertEqual(head[1],before[0][1]);self.assertNotEqual(head[0],before[0][0])

 def test_report_head_current_and_job_publish_atomically_and_recover(self):
  first=initialize(self.root,[{'pid':PID,'units':300}],'2026-08-20','2026-09-18',clock=lambda:2.)['generationId']
  scan_one(self.root,first,self.requester([]),clock=lambda:3.)
  before=self.projection();newer=initialize(self.root,[{'pid':PID,'units':300}],'2026-08-21','2026-09-19',clock=lambda:4.)['generationId']
  def fail_after_head(*args):
   self.assertEqual(self.projection(),before)  # A concurrent reader still sees the old committed head.
   raise RuntimeError('publish-crash')
  with mock.patch('lib.kalodata_video_scan._publish_current',side_effect=fail_after_head),self.assertRaisesRegex(RuntimeError,'publish-crash'):
   scan_one(self.root,newer,self.requester([]),clock=lambda:5.)
  self.assertEqual(self.projection(),before)
  with CycleStore(self.root/'var/second-cycle.sqlite',readonly=True) as store:
   self.assertEqual(store.db.execute('SELECT count(*) FROM kalodata_video_run').fetchone()[0],1)
   self.assertEqual(store.db.execute('SELECT state FROM kalodata_video_scan_job WHERE generation_id=?',(newer,)).fetchone()[0],'detailing')
  calls=[];scan_one(self.root,newer,self.requester(calls),clock=lambda:6.)
  self.assertEqual(calls,[]);self.assertNotEqual(self.projection(),before)

 def test_resumes_legacy_report_committed_before_current_projection(self):
  from lib.kalodata_video_evidence import persist
  generation=initialize(self.root,[{'pid':PID,'units':300}],'2026-08-20','2026-09-18',clock=lambda:2.)['generationId']
  captured=[]
  def crash(store,report,_generation):
   captured.append(report.copy());raise RuntimeError('old-interruption')
  with mock.patch('lib.kalodata_video_scan._publish_current',side_effect=crash),self.assertRaises(RuntimeError):
   scan_one(self.root,generation,self.requester([]),clock=lambda:3.)
  with CycleStore(self.root/'var/second-cycle.sqlite') as store:persist(store,captured[0])
  calls=[];scan_one(self.root,generation,self.requester(calls),clock=lambda:4.)
  self.assertEqual(calls,[]);self.assertEqual(status(self.root,generation)['completed'],1)
  self.assertEqual(len(self.projection()[1]),1)

 def test_late_old_generation_cannot_replace_a_newer_complete_head(self):
  scope=[{'pid':PID,'units':300}]
  older=initialize(self.root,scope,'2026-08-20','2026-09-18',clock=lambda:2.)['generationId']
  newer=initialize(self.root,scope,'2026-08-21','2026-09-19',clock=lambda:3.)['generationId']
  scan_one(self.root,newer,self.requester([]),clock=lambda:4.)
  before=self.projection()
  with self.assertRaisesRegex(CycleError,'video_scan_generation_superseded'):
   scan_one(self.root,older,self.requester([]),clock=lambda:5.)
  self.assertEqual(self.projection(),before)


if __name__=='__main__':unittest.main()
