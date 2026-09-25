import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))

from lib.cycle_delivery import Deliveries  # noqa:E402
from lib.cycle_auto_reply import AutoReplies  # noqa:E402
from lib.cycle_inbox import Inbox  # noqa:E402
from lib.cycle_service import Service  # noqa:E402
from lib.reply_events import DeepSeekClassifier,apply_turn_review,backfill,batch_classify,classify,evaluation_summary,load_policy,review,review_turn,status  # noqa:E402
from lib.agent_reply_v2 import production_context  # noqa:E402
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
   self.assertIn('Perfetto',snapshot['templates']['collaboration_ack']['text'])
   self.assertEqual((first['platformWrites'],first['modelCalls']),(0,0))
   with self.assertRaises(sqlite3.DatabaseError):s.db.execute("UPDATE inbound_turn SET text='changed'")

 def test_card_then_text_completion_keeps_one_immutable_episode(self):
  with CycleStore(self.db,clock=lambda:self.now) as s:
   delivery=s.db.execute('SELECT id FROM cycle_delivery LIMIT 1').fetchone()[0]
   s.db.execute("UPDATE cycle_delivery_part SET state='ready',started=NULL WHERE delivery_id=? AND kind='text'",(delivery,))
   s.db.execute("UPDATE cycle_delivery SET state='running' WHERE id=?",(delivery,))
   first=backfill(s)
   self.assertEqual(first['episodesAdded'],1)
   s.db.execute("UPDATE cycle_delivery_part SET state='confirmed',started=? WHERE delivery_id=? AND kind='text'",(self.now,delivery))
   s.db.execute("UPDATE cycle_delivery SET state='confirmed' WHERE id=?",(delivery,))
   second=backfill(s)
   self.assertEqual(second['episodesAdded'],0)
   self.assertEqual(s.db.execute('SELECT count(*) FROM outbound_episode').fetchone()[0],1)

 def test_agent_history_shows_only_components_confirmed_before_the_turn(self):
  with CycleStore(self.db,clock=lambda:self.now) as s:
   AutoReplies(s)
   delivery=s.db.execute('SELECT id FROM cycle_delivery LIMIT 1').fetchone()[0]
   s.db.execute("UPDATE cycle_delivery_part SET state='ready',started=NULL WHERE delivery_id=? AND kind='text'",(delivery,))
   backfill(s)
   turn=s.db.execute('SELECT turn_id FROM inbound_turn LIMIT 1').fetchone()[0]
   before=production_context(ROOT,s,self.plan,'it',turn)['messages']
   self.assertEqual([row['format'] for row in before if row['direction']=='outbound'],['product_card'])
   self.assertFalse(any(row.get('text')=='Ciao!' for row in before))
   s.db.execute("UPDATE cycle_delivery_part SET state='confirmed',started=? WHERE delivery_id=? AND kind='text'",(self.now-5,delivery))
   after=production_context(ROOT,s,self.plan,'it',turn)['messages']
   self.assertTrue(any(row.get('text')=='Ciao!' for row in after))
   s.db.execute("UPDATE cycle_delivery_part SET started=? WHERE delivery_id=? AND kind='text'",(self.now+5,delivery))
   future=production_context(ROOT,s,self.plan,'it',turn)['messages']
   self.assertFalse(any(row.get('text')=='Ciao!' for row in future))

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

 def test_model_cannot_invent_evidence_or_free_text_and_retired_provider_is_rejected(self):
  with CycleStore(self.db,clock=lambda:self.now) as s:
   backfill(s);turn=s.db.execute('SELECT turn_id FROM inbound_turn').fetchone()[0]
   bad={'schemaVersion':'bdhub.reply-classification.v1','action':'collaboration_ack',
    'intentCode':'collaboration_confirmed','evidenceMessageIds':['1001'],'evidenceQuotes':['invented'],
    'confidence':1,'humanReason':None,'templateKey':'collaboration_ack_v1','meaningZh':'确认'}
   with self.assertRaisesRegex(CycleError,'evidence'):
    classify(s,turn,'request-shadow-0002',DeepSeekClassifier(lambda *_a,**_k:{'content':json.dumps(bad)}))
   too_long={**bad,'action':'human','intentCode':'needs_human','evidenceQuotes':['farò un video'],
             'humanReason':'x'*501,'templateKey':None}
   with self.assertRaisesRegex(CycleError,'human_reason'):
    classify(s,turn,'request-shadow-0003',DeepSeekClassifier(lambda *_a,**_k:{'content':json.dumps(too_long)}))
   class Retired:
    provider='jev'
    def classify(self,*_):raise AssertionError('retired provider must not execute')
   count=s.db.execute('SELECT count(*) FROM reply_classification').fetchone()[0]
   with self.assertRaisesRegex(CycleError,'reply_provider_not_supported'):
    classify(s,turn,'request-jev-0001',Retired())
   self.assertEqual(s.db.execute('SELECT count(*) FROM reply_classification').fetchone()[0],count)
   with self.assertRaisesRegex(CycleError,'reply_batch_invalid'):
    batch_classify(s,['jev'],10,root=self.root)
   self.assertEqual(load_policy()['automaticRepliesEnabled'],False)
 def test_single_provider_batch_preserves_read_only_historical_comparison(self):
  with CycleStore(self.db,clock=lambda:self.now) as s:
   backfill(s)
   class Fake:
    model='fixture'
    def __init__(self,provider):self.provider=provider
    def classify(self,context):
     action='human' if self.provider=='deepseek' else 'collaboration_ack'
     return ({'schemaVersion':'bdhub.reply-classification.v1','action':action,
      'intentCode':'fixture_'+action,'evidenceMessageIds':['1001'],'evidenceQuotes':['farò un video'],
      'confidence':.8,'humanReason':'需人工' if action=='human' else None,
      'templateKey':None if action=='human' else 'collaboration_ack_v1','meaningZh':'确认会制作视频'},None)
   report=batch_classify(s,['deepseek'],10,root=self.root,classifier_factory=Fake)
   self.assertEqual((report['turns'],report['ready'],report['modelCalls']),(1,1,1))
   self.assertEqual(set(status(s)['providers']),{'deepseek'})
   self.assertNotIn('jev',evaluation_summary(s)['providers'])
   row=s.db.execute("SELECT * FROM reply_classification WHERE provider='deepseek'").fetchone()
   historic=json.loads(row['decision_json']);historic.update(action='collaboration_ack',humanReason=None,templateKey='collaboration_ack_v1')
   with s.tx():s.db.execute('INSERT INTO reply_classification VALUES(?,?,?,?,?,?,?,?,?,?,?)',
    ('historical-jev','historical-request',row['input_hash'],row['policy_version'],'jev','jev-1.13.0','ready',row['input_json'],row['response_json'],json.dumps(historic),row['created_at']))
   before=evaluation_summary(s);self.assertEqual((before['paired'],before['disagreements'],before['reviewedTurns']),(1,1,0))
   turn=s.db.execute('SELECT turn_id FROM inbound_turn').fetchone()[0]
   review_turn(s,turn,0,'collaboration_ack','参考答案')
   after=evaluation_summary(s);self.assertEqual(after['providers']['jev']['accuracy'],1.0)
   self.assertEqual(after['providers']['deepseek']['falseHuman'],1)
 def test_turn_truth_can_be_corrected_by_appending_a_new_revision(self):
  with CycleStore(self.db,clock=lambda:self.now) as s:
   backfill(s);turn=s.db.execute('SELECT turn_id FROM inbound_turn').fetchone()[0]
   self.assertEqual(review_turn(s,turn,0,'human','初次判断')['revision'],1)
   self.assertEqual(review_turn(s,turn,1,'no_reply','复核后修正')['revision'],2)
   current=status(s)['items'][0]['review']
   self.assertEqual((current['revision'],current['correct_action'],current['note']),(2,'no_reply','复核后修正'))
 def context_revisions(self,s):
  relationship=s.db.execute('SELECT revision FROM relationship WHERE creator_id=?',('c1',)).fetchone()[0]
  pending=s.db.execute('SELECT revision FROM inbox_pending WHERE creator_id=?',('c1',)).fetchone()[0]
  return relationship,pending
 def test_apply_no_reply_releases_only_current_live_turn_and_is_idempotent(self):
  with CycleStore(self.db,clock=lambda:self.now) as s:
   backfill(s);turn=s.db.execute('SELECT turn_id FROM inbound_turn').fetchone()[0];review_turn(s,turn,0,'no_reply','无需回复')
   control,pending=self.context_revisions(s);result=apply_turn_review(s,turn,1,control,pending,'apply-request-0001')
   self.assertEqual(result['state'],'resolved_no_reply');self.assertEqual(s.db.execute('SELECT inbox_until FROM relationship').fetchone()[0],0)
   self.assertEqual(s.db.execute('SELECT state FROM inbox_pending').fetchone()[0],'resolved_no_reply')
   self.assertTrue(apply_turn_review(s,turn,1,control,pending,'apply-request-0001')['duplicate'])
 def test_apply_template_stages_fixed_text_without_sending_or_unfreezing(self):
  with CycleStore(self.db,clock=lambda:self.now) as s:
   backfill(s);turn=s.db.execute('SELECT turn_id FROM inbound_turn').fetchone()[0];review_turn(s,turn,0,'collaboration_ack','合作确认')
   control,pending=self.context_revisions(s);result=apply_turn_review(s,turn,1,control,pending,'apply-request-0002')
   self.assertEqual(result['state'],'template_ready');self.assertFalse(result['automaticReply']);self.assertEqual(result['platformWrites'],0)
   self.assertGreater(s.db.execute('SELECT inbox_until FROM relationship').fetchone()[0],0)
   candidate=s.db.execute('SELECT action,state,template_text FROM review_reply_candidate').fetchone();self.assertEqual((candidate[0],candidate[1]),('collaboration_ack','reviewed_ready'));self.assertIn('Perfetto',candidate[2])
 def test_apply_human_opens_case_and_keeps_creator_frozen(self):
  with CycleStore(self.db,clock=lambda:self.now) as s:
   backfill(s);turn=s.db.execute('SELECT turn_id FROM inbound_turn').fetchone()[0];review_turn(s,turn,0,'human','佣金异常')
   control,pending=self.context_revisions(s);result=apply_turn_review(s,turn,1,control,pending,'apply-request-0003')
   self.assertEqual(result['state'],'human');self.assertEqual(s.db.execute('SELECT mode FROM relationship').fetchone()[0],'human')
   self.assertEqual(s.db.execute("SELECT count(*) FROM service_case WHERE state='open'").fetchone()[0],1)
   self.assertGreater(s.db.execute('SELECT inbox_until FROM relationship').fetchone()[0],0)


if __name__=='__main__':unittest.main()
