import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))

from lib.cycle_delivery import Deliveries  # noqa:E402
from lib.cycle_inbox import Inbox  # noqa:E402
from lib.cycle_service import Service  # noqa:E402
from lib.reply_events import DeepSeekClassifier,JevClassifier,backfill,classify,load_policy,review,status  # noqa:E402
from lib.schema_migrations import apply_database  # noqa:E402
from lib.second_cycle import CycleError,CycleStore,digest  # noqa:E402
from test_second_cycle import NOW,edge,offer  # noqa:E402


class ReplyEvents(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);(self.root/'var').mkdir()
  self.db=self.root/'var/second-cycle.sqlite';self.now=NOW
  o=offer(endAt=NOW+60*86400,catalogSource='selected',campaignId='0',totalPercent='15',
          managementType='full_managed',managementEvidenceRef='fixture',stock=None)
  with CycleStore(self.db,clock=lambda:self.now) as s:
   self.plan=s.plan('bjn-local-research','it');s.publish(self.plan,'source',NOW,[o]);s.import_edges(self.plan,[edge()])
   deliveries=Deliveries(s)
   candidate={'planRevision':1,'creatorId':'c1','oecId':'123','handle':'creator','identityEvidence':'proof',
    'controlRevision':1,'pid':'1','offer':o,'offerFingerprint':digest(o),'materialKey':'m',
    'name':{'mentionIt':'prodotto'},'nameSource':'fixture','card':{'listId':'99'},
    'source':edge(),'relationshipUnlocked':False,
    'message':{'version':4,'deliveryOrder':'card_then_text','textIt':'Ciao!'}}
   delivery=deliveries.prepare(self.plan,candidate)['id']
   s.db.execute("UPDATE cycle_delivery_part SET state='confirmed',started=? WHERE delivery_id=?",
                (NOW-10,delivery));s.db.execute("UPDATE cycle_delivery SET state='confirmed' WHERE id=?",(delivery,))
   inbox=Inbox(s);service=Service(s);inbox.ingest(self.plan,'10','123',{'identityVerified':True,'hasMore':False,'events':[]})
   self.now+=100
   event={'conversationId':'10','oecId':'123','messageId':'1001','kind':'creatorReplies',
          'createTimeRaw':int(self.now*1000)}
   inbox.ingest(self.plan,'10','123',{'identityVerified':True,'hasMore':False,'events':[event]})
   service.capture(self.plan,'10','123',[{'messageId':'1001','format':'text','text':'Certo, farò un video',
    'nativeType':'text','rawSha256':'proof'}])
  apply_database(self.root,'second-cycle',clock=lambda:NOW+200)

 def tearDown(self):self.temp.cleanup()

 def test_backfill_builds_immutable_episode_turn_and_link_without_effects(self):
  with CycleStore(self.db,clock=lambda:self.now) as s:
   first=backfill(s);second=backfill(s);snapshot=status(s)
   self.assertEqual((first['episodesAdded'],first['turnsAdded'],first['linksAdded']),(1,1,1))
   self.assertEqual((second['episodesAdded'],second['turnsAdded'],second['linksAdded']),(0,0,0))
   self.assertEqual(snapshot['counts']['turns'],1);self.assertEqual(snapshot['counts']['episodes'],1)
   self.assertEqual(snapshot['items'][0]['episodes'][0]['pid'],'1')
   self.assertEqual((first['platformWrites'],first['modelCalls']),(0,0))
   with self.assertRaises(sqlite3.DatabaseError):s.db.execute("UPDATE inbound_turn SET text='changed'")

 def test_five_action_shadow_is_evidence_bound_reviewed_and_never_executable(self):
  with CycleStore(self.db,clock=lambda:self.now) as s:
   backfill(s);turn=s.db.execute('SELECT turn_id FROM inbound_turn').fetchone()[0]
   def call(*_args,**_kwargs):
    return {'content':json.dumps({'schemaVersion':'bdhub.reply-classification.v1',
      'action':'collaboration_ack','intentCode':'collaboration_confirmed',
      'evidenceMessageIds':['1001'],'evidenceQuotes':['farò un video'],'confidence':0.98,
      'humanReason':None,'templateKey':'collaboration_ack_v1',
      'meaningZh':'达人确认会制作视频'}),'usage':{'totalTokens':30}}
   result=classify(s,turn,'request-shadow-0001',DeepSeekClassifier(call))
   self.assertEqual(result['action'],'collaboration_ack');self.assertFalse(result['executionAllowed'])
   self.assertIn('Perfetto',result['templateText'])
   again=classify(s,turn,'request-shadow-0001',DeepSeekClassifier(lambda *_a,**_k:1/0))
   self.assertTrue(again['cached'])
   checked=review(s,result['classificationId'],0,'correct',None,'符合参考答案')
   self.assertEqual(checked['revision'],1);self.assertFalse(checked['automaticReply'])

 def test_model_cannot_invent_evidence_or_free_text_and_jev_is_not_guessed(self):
  with CycleStore(self.db,clock=lambda:self.now) as s:
   backfill(s);turn=s.db.execute('SELECT turn_id FROM inbound_turn').fetchone()[0]
   bad={'schemaVersion':'bdhub.reply-classification.v1','action':'collaboration_ack',
    'intentCode':'collaboration_confirmed','evidenceMessageIds':['1001'],'evidenceQuotes':['invented'],
    'confidence':1,'humanReason':None,'templateKey':'collaboration_ack_v1','meaningZh':'确认'}
   with self.assertRaisesRegex(CycleError,'evidence'):
    classify(s,turn,'request-shadow-0002',DeepSeekClassifier(lambda *_a,**_k:{'content':json.dumps(bad)}))
   with self.assertRaisesRegex(CycleError,'jev_not_configured'):
    classify(s,turn,'request-jev-0001',JevClassifier(self.root))
   self.assertEqual(load_policy()['automaticRepliesEnabled'],False)


if __name__=='__main__':unittest.main()
