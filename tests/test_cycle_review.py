import sys,unittest,tempfile,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from lib.second_cycle import CycleStore,CycleError,digest
from lib.cycle_review import ReviewBatches,_position_rows,latest_review
from test_second_cycle import offer,edge,NOW
class ReviewTests(unittest.TestCase):
 def setUp(self):
  self.t=tempfile.TemporaryDirectory();self.now=NOW;self.s=CycleStore(Path(self.t.name)/'db',lambda:self.now);self.p=self.s.plan('test','it');self.o=offer(title='test',endAt=NOW+90*86400)
  self.s.publish(self.p,'source',NOW,[self.o]);self.s.import_edges(self.p,[edge()]);self.b=ReviewBatches(self.s)
  self.req={'planId':self.p,'limit':3,'template':'standard'}
  self.c={'planRevision':1,'creatorId':'c1','oecId':'123','handle':'test','controlRevision':1,'offer':self.o,'offerFingerprint':digest(self.o),'name':{'mentionIt':'questo prodotto','shortNameZh':'商品'},'source':{}}
  self.e={'123':{'legacy':{},'platform':{'hasPermission':True}}}
 def tearDown(self):self.s.close();self.t.cleanup()
 def create(self):return self.b.create('request',self.req,[self.c],self.e,[])
 def test_permission_true_does_not_enable_send(self):
  r=self.create();self.assertFalse(r['executionAllowed']);self.assertIn('institution_market_quota_not_verified',r['items'][0]['blockingReasons'])
 def test_request_replay_keeps_frozen_content(self):
  a=self.create();self.now+=20;self.assertEqual(a,self.create())
 def test_historical_outbound_needs_window_verification_not_permanent_exclusion(self):
  for n in (4,5):
   self.e['123']['remoteHistory']={'status':'observed_summary','history':{'senderCounts':{'ourMessages':n}}}
   r=self.b.create('count'+str(n),self.req,[self.c],self.e,[])
   self.assertIn('message_allowance_window_unverified',r['items'][0]['blockingReasons'])
   self.assertFalse(r['items'][0]['allowanceReview']['permanentExclusion']);self.assertIsNone(r['items'][0]['allowanceReview']['resetAt'])
 def test_old_messages_record_age_without_guessing_reset(self):
  stamp=int((self.now-60*86400)*1000)
  self.e['123']['remoteHistory']={'status':'observed_summary','history':{'senderCounts':{'ourMessages':5},'outboundCreateTimeRaw':[stamp]*5,'outboundTimeMissingCount':0}}
  a=self.create()['items'][0]['allowanceReview']
  self.assertEqual(a['elapsedDaysSinceObservedOutbound'],60);self.assertFalse(a['automaticResetApplied']);self.assertIsNone(a['resetAt'])
 def test_reply_is_evidence_not_automatic_controller_unlock(self):
  self.e['123']['remoteHistory']={'status':'observed_summary','history':{'senderCounts':{'ourMessages':5,'creatorReplies':1}}}
  r=self.create()['items'][0]
  self.assertIn('interaction_evidence_not_applied_to_controller',r['blockingReasons'])
  self.assertNotIn('message_allowance_window_unverified',r['blockingReasons']);self.assertFalse(r['executionAllowed'])
 def test_request_conflict(self):
  self.create()
  with self.assertRaises(CycleError):self.b.existing('request',self.req|{'limit':2})
 def test_control_change_during_evidence_read(self):
  self.s.control(self.p,'human',1,'human','c1')
  with self.assertRaisesRegex(CycleError,'relationship_changed'):self.create()
 def test_offer_change_during_read(self):
  self.s.publish(self.p,'source',NOW+1,[self.o|{'creatorPercent':'13'}])
  with self.assertRaisesRegex(CycleError,'offer_changed'):self.create()
 def test_legacy_rejection_remains_visible(self):
  self.e['123']['legacy']={'manualState':'rejected'}
  self.assertIn('legacy_relationship_needs_review',self.create()['items'][0]['blockingReasons'])
 def test_expiry_and_immutable_record(self):
  self.create();self.now+=1801;self.assertTrue(latest_review(self.s,self.p)['expired'])
  with self.assertRaises(Exception):self.s.db.execute("UPDATE cycle_review_batch SET payload='{}'")
 def test_b_only_position_freezes_representative_video_evidence(self):
  self.s.db.execute('''CREATE TABLE video_lead_current(
   generation_id TEXT,pid TEXT,kalodata_creator_id TEXT,handle TEXT,run_id TEXT,video_id TEXT,
   views INTEGER,released_at TEXT,video_sale INTEGER,observed_at REAL)''')
  self.s.db.execute('INSERT INTO video_lead_current VALUES(?,?,?,?,?,?,?,?,?,?)',
                    ('g','2','k1','video.handle','run-1','video-1',12000,'2026-09-18',0,self.now))
  rows=_position_rows(self.s,self.p,{},lambda creator,oec:{'handle':'video.handle'},[('c1','2')])
  self.assertEqual(len(rows),1)
  source=json.loads(rows[0]['payload'])
  self.assertEqual((source['sourceClass'],source['videoId'],source['videoViews']),('B','video-1',12000))
  self.assertTrue(source['sourceId'].startswith('video:run-1:'))
if __name__=='__main__':unittest.main()
