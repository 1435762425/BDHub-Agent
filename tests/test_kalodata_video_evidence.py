import json
from contextlib import closing
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))

from lib.kalodata_video_evidence import (VIDEO_DETAIL_PATH,VIDEO_LIST_PATH,collect,compact_int,release_date,
                                         parse_video_detail,parse_video_list,persist,status)  # noqa:E402
from lib.schema_migrations import apply_database  # noqa:E402
from lib.second_cycle import CycleError,CycleStore  # noqa:E402


PID='1729765843626072790'


def video(video_id='7674344776354860308',views='1.2万',sale='0'):
 return {'id':video_id,'views':views,'sale':sale,'revenue':'€0.00','create_time':'2026/09/10',
         'duration':'33s','description':'Exact PID video','content_type':'video','ad':0,'ai_video':0}


def detail(video_id='7674344776354860308',handle='alice.creator'):
 return {'success':True,'data':{**video(video_id),'handle':handle,'creator_id':'7592705921965884434',
                                'release_time':'2026/09/10'}}


class VideoEvidenceTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);(self.root/'var').mkdir()
  self.db=self.root/'var/second-cycle.sqlite'
  with CycleStore(self.db):pass
  apply_database(self.root,'second-cycle',clock=lambda:1.)
 def tearDown(self):self.temp.cleanup()

 def test_list_filters_by_views_and_detail_binds_the_author(self):
  listed=parse_video_list({'success':True,'data':[video(),video('7674344776354860309','999')]},PID,
                          window_start='2026-08-20',window_end='2026-09-18')
  self.assertEqual((listed['rowsReceived'],len(listed['candidates'])),(2,1))
  row=parse_video_detail(detail(),listed['candidates'][0],123.)
  self.assertEqual((row['handle'],row['kalodataCreatorId'],row['views']),('alice.creator','7592705921965884434',12000))
  self.assertIn('/@alice.creator/video/',row['videoUrl'])
  self.assertEqual(compact_int('1.2万'),12000)
  self.assertEqual(release_date('2026年9月10日').isoformat(),'2026-09-10')

 def test_collect_keeps_resolved_evidence_and_reports_missing_authors(self):
  calls=[]
  def request(path,payload):
   calls.append((path,payload['id']))
   if path==VIDEO_LIST_PATH:return {'success':True,'data':[video(),video('7674344776354860310','5000')]}
   if payload['id']=='7674344776354860308':return detail()
   return {'success':True,'data':{**video(payload['id']),'handle':'','creator_id':''}}
  report=collect(PID,'2026-08-20','2026-09-18',request,clock=lambda:123.)
  self.assertEqual((report['qualifyingVideos'],report['resolvedVideos'],report['networkRequests']),(2,1,3))
  self.assertEqual(report['state'],'completed_with_gaps');self.assertEqual(report['errors'][0]['code'],'kalodata_video_author_missing')
  self.assertEqual([path for path,_ in calls],[VIDEO_LIST_PATH,VIDEO_DETAIL_PATH,VIDEO_DETAIL_PATH])

 def test_collect_does_not_cap_a_complete_page_at_twenty_videos(self):
  rows=[video(str(7674344776354860400+index)) for index in range(25)]
  def request(path,payload):
   if path==VIDEO_LIST_PATH:return {'success':True,'data':rows}
   return detail(payload['id'])
  report=collect(PID,'2026-08-20','2026-09-18',request,clock=lambda:123.)
  self.assertEqual((report['pagesRead'],report['qualifyingVideos'],report['selectedVideos'],
                    report['resolvedVideos'],report['coverage'],report['networkRequests']),
                   (1,25,25,25,'complete',26))

 def test_collect_paginates_until_the_publication_window_is_complete(self):
  first=[video(str(7674344776354860500+index),'999') for index in range(50)]
  second=[video('7674344776354860600','5000')]
  calls=[]
  def request(path,payload):
   calls.append((path,payload.get('pageNo')))
   if path==VIDEO_LIST_PATH:return {'success':True,'data':first if payload['pageNo']==1 else second}
   return detail(payload['id'])
  report=collect(PID,'2026-08-20','2026-09-18',request,clock=lambda:123.)
  self.assertEqual((report['pagesRead'],report['rowsReceived'],report['resolvedVideos'],
                    report['coverage'],report['state'],report['networkRequests']),
                   (2,51,1,'complete','completed',3))
  self.assertEqual(calls[:2],[(VIDEO_LIST_PATH,1),(VIDEO_LIST_PATH,2)])

 def test_page_cap_is_visible_and_never_reported_as_complete(self):
  rows=[video(str(7674344776354860700+index),'999') for index in range(50)]
  report=collect(PID,'2026-08-20','2026-09-18',lambda *_:{'success':True,'data':rows},
                 max_pages=1,clock=lambda:123.)
  self.assertEqual((report['coverage'],report['state'],report['pagesRead']),
                   ('page_cap','completed_with_gaps',1))

 def test_persist_and_zero_sale_join_are_idempotent_and_do_not_change_pool(self):
  report=collect(PID,'2026-08-20','2026-09-18',lambda path,payload:
    {'success':True,'data':[video()]} if path==VIDEO_LIST_PATH else detail(),clock=lambda:123.)
  with closing(sqlite3.connect(self.root/'var/kalodata-leads.sqlite')) as db,db:
   db.execute('CREATE TABLE leads_page(pid TEXT,cursor TEXT,payload TEXT,PRIMARY KEY(pid,cursor))')
   db.execute('INSERT INTO leads_page VALUES(?,?,?)',(PID,'',json.dumps({'sourceRows':[
    {'id':'7592705921965884434','handle':'alice.old','sale':0},
    {'id':'7592705921965884435','handle':'seller','sale':2}]})))
  with closing(sqlite3.connect(self.root/'var/creator-identities.sqlite')) as db,db:
   db.execute('CREATE TABLE creator_identity(creator_id TEXT,market TEXT,current_handle TEXT,handle_conflict INTEGER)')
   db.execute("INSERT INTO creator_identity VALUES('creator-a','it','alice.creator',0)")
  with CycleStore(self.db) as store:
   first=persist(store,report);second=persist(store,report);snapshot=status(self.root,store,PID)
   self.assertFalse(first['cached']);self.assertTrue(second['cached'])
   self.assertEqual((snapshot['runs'],snapshot['videos'],len(snapshot['candidates'])),(1,1,1))
   self.assertEqual((snapshot['candidates'][0]['handle'],snapshot['candidates'][0]['knownOec'],
                     snapshot['candidates'][0]['maxViews'],snapshot['candidates'][0]['maxVideoSale']),
                    ('alice.creator',True,12000,0))
   self.assertEqual((snapshot['candidates'][0]['sourceHandle'],snapshot['candidates'][0]['kalodataCreatorId'],
                     snapshot['candidateSummary']['candidatePairs']),
                    ('alice.old','7592705921965884434',1))
   self.assertEqual((snapshot['candidates'][0]['latestAgeDays'],
                     snapshot['candidateSummaryByRecency']['30d']['candidatePairs']),(8,1))
   self.assertEqual(snapshot['candidateSummary']['pidsScanned'],1)
   self.assertEqual(snapshot['candidateSummaryByPolicy']['30d_views10000']['candidatePairs'],1)
   self.assertFalse(snapshot['sendPoolChanged']);self.assertEqual(snapshot['platformWrites'],0)
   with self.assertRaises(sqlite3.DatabaseError):store.db.execute("UPDATE kalodata_video_evidence SET views=1")

 def test_invalid_shapes_are_not_silently_accepted(self):
  with self.assertRaisesRegex(CycleError,'video_list_invalid'):
   parse_video_list({'success':True,'data':{}},PID,window_start='2026-08-20',window_end='2026-09-18')
  with self.assertRaisesRegex(CycleError,'author_missing'):
   listed=parse_video_list({'success':True,'data':[video()]},PID,
                           window_start='2026-08-20',window_end='2026-09-18')
   parse_video_detail({'success':True,'data':video()},listed['candidates'][0],1.)


if __name__=='__main__':unittest.main()
