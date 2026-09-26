import json,sqlite3,tempfile,unittest,sys
from contextlib import closing
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from lib.conversation_workbench import (complete_reviewed_human,confirm_manual_reply,conversation_detail,
 list_conversations,reconcile_manual_reply,reject_creator,resolve_manual,save_draft,set_collaboration,unread_backlog)
from lib.cycle_auto_reply import AutoReplies
from lib.cycle_inbox import Inbox
from lib.cycle_service import Service
from lib.schema_migrations import apply_database
from lib.second_cycle import CycleError,CycleStore
from test_second_cycle import NOW

class ConversationWorkbenchTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);(self.root/'var').mkdir();path=self.root/'var/second-cycle.sqlite'
  with CycleStore(path,lambda:NOW) as store:self.plan=store.plan('bjn-local-research','it')
  apply_database(self.root,'second-cycle',clock=lambda:NOW)
  self.store=CycleStore(path,lambda:NOW)
  Inbox(self.store);Service(self.store)
  self.store.db.execute("INSERT INTO relationship VALUES(?,?,?,'human',0,1,?,1)",(self.plan,'creator-1','123',NOW+100))
  self.store.db.execute("INSERT INTO inbox_checkpoint VALUES(?,?,?,?,?,'tracking')",(self.plan,'999','123',NOW-100,NOW))
  self.store.db.execute("INSERT INTO inbox_pending VALUES(?,?,1,?,'human')",(self.plan,'creator-1',NOW))
  self.store.db.execute("INSERT INTO inbound_turn VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",('turn-'+('a'*24),self.plan,'creator-1','123','999','1000','b'*64,'text','Ho un problema',int(NOW*1000),0,NOW))
  self.store.db.execute("INSERT INTO service_case VALUES(?,?,?,'open',1,'link_issue',?,?, 'not_sent')",('case-'+('c'*24),self.plan,'creator-1',NOW,NOW))
  with closing(sqlite3.connect(self.root/'var/creator-identities.sqlite')) as db,db:db.execute("CREATE TABLE creator_identity(creator_id,oec_id,market,current_handle,handle_conflict)");db.execute("INSERT INTO creator_identity VALUES('creator-1','123','it','alice',0)")
 def tearDown(self):self.store.close();self.tmp.cleanup()
 def test_unread_backlog_matches_the_list_unread_marks(self):
  self.store.db.execute("INSERT INTO relationship VALUES(?,?,?,'auto',0,1,?,1)",(self.plan,'creator-2','456',NOW+100))
  self.store.db.execute("INSERT INTO inbox_pending VALUES(?,?,1,?,'awaiting_classification')",(self.plan,'creator-2',NOW))
  self.store.db.execute("INSERT INTO inbound_turn VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",('turn-'+('d'*24),self.plan,'creator-2','456','998','1001','e'*64,'text','Ciao',int((NOW-3600)*1000),0,NOW-3600))
  self.store.db.execute("INSERT INTO relationship VALUES(?,?,?,'auto',0,1,?,1)",(self.plan,'creator-3','789',NOW+100))
  self.store.db.execute("INSERT INTO inbox_pending VALUES(?,?,1,?,'answered')",(self.plan,'creator-3',NOW))
  items=list_conversations(self.root,self.store,'all',limit=100)['items']
  self.assertEqual(sum(row['unread'] for row in items),2)
  self.assertEqual(unread_backlog(self.store,'it'),{'unread':2,'oldestAt':NOW-3600})
 def test_default_human_queue_explains_reason(self):
  result=list_conversations(self.root,self.store,'human')
  self.assertEqual(result['total'],1);self.assertEqual(result['items'][0]['humanReasonLabel'],'链接打不开')
 def test_manual_unknown_is_visible_and_only_original_intent_is_reconciled(self):
  replies=AutoReplies(self.store)
  request_id='manual-audit-unknown-001'
  frozen=replies.prepare_manual(self.plan,'creator-1','999','Ciao',1,request_id)
  self.store.db.execute("UPDATE service_reply SET state='unknown' WHERE id=?",(frozen['id'],))
  detail=conversation_detail(self.root,self.store,'999')
  self.assertEqual(detail['pendingManualReplies'],[{'id':frozen['id'],'kind':'manual',
                                                    'requestId':request_id,'state':'unknown'}])
  calls=[]
  def verify(store,current,reply):
   calls.append((reply['id'],reply['request_ref'],reply['state']))
   store.db.execute("UPDATE service_reply SET state='confirmed' WHERE id=?",(reply['id'],))
  result=reconcile_manual_reply(self.store,'999',request_id,verify=verify)
  self.assertEqual(calls,[(frozen['id'],request_id,'unknown')])
  self.assertEqual((result['state'],result['platformWrites'],result['realSends']),('confirmed',0,0))
  with self.assertRaisesRegex(CycleError,'manual_reconcile_intent_missing'):
   reconcile_manual_reply(self.store,'998',request_id,verify=verify)
  # Another market can never reach IT's manual intent (and has no manual sends of its own).
  with self.assertRaisesRegex(CycleError,'plan_missing|manual_reconcile_intent_missing'):
   reconcile_manual_reply(self.store,'999',request_id,market='br',verify=verify)

 def test_isolated_replies_form_a_technical_queue_and_stay_read_only_verifiable(self):
  self.store.db.execute("UPDATE relationship SET mode='auto' WHERE creator_id='creator-1'")
  self.store.db.execute("UPDATE service_case SET state='closed'")
  self.store.db.execute("UPDATE inbox_pending SET state='awaiting_classification'")
  replies=AutoReplies(self.store);request_id='manual-isolated-0001'
  frozen=replies.prepare_manual(self.plan,'creator-1','999','Ciao',1,request_id)
  self.store.db.execute("UPDATE service_reply SET state='isolated',started=?,sender_account='acc6' WHERE id=?",(NOW,frozen['id']))
  listed=list_conversations(self.root,self.store,'technical')
  self.assertEqual((listed['total'],listed['counts']['technical'],listed['counts']['agent']),(1,1,0))
  self.assertEqual((listed['items'][0]['humanReason'],listed['items'][0]['queueStatusLabel']),
                   ('reply_isolated','回复送达未知，已停止自动跟进'))
  detail=conversation_detail(self.root,self.store,'999')
  self.assertEqual(detail['pendingManualReplies'][0]['state'],'isolated')
  self.assertEqual(detail['technicalHold']['reason'],'reply_isolated')
  reads=[]
  result=reconcile_manual_reply(self.store,'999',request_id,verify=lambda store,current,reply:reads.append(reply['state']))
  self.assertEqual((reads,result['state'],result['platformWrites']),(['isolated'],'isolated',0))
 def test_held_ai_reply_is_listed_and_checked_only_by_its_original_request(self):
  self.store.db.execute("UPDATE relationship SET mode='auto' WHERE creator_id='creator-1'")
  replies=AutoReplies(self.store);request_ref='01234567-89ab-4cde-8fab-0123456789ab'
  self.store.db.execute("""INSERT INTO service_reply(id,plan_id,creator_id,pending_revision,oec,cid,kind,case_id,text,context_hash,
   state,request_ref,receipt,proof,created,started,control_revision,sender_account,sender_identity)
   VALUES('agent-reply-aaaaaaaaaaaaaaaaaaaaaaaa',?,'creator-1',1,'123','999','agent_generated_v2',NULL,'Ciao!','h','unknown',?,NULL,NULL,?,?,1,'acc6','hash')""",
   (self.plan,request_ref,NOW,NOW))
  held=conversation_detail(self.root,self.store,'999')['heldReplies']
  self.assertEqual([(r['state'],r['senderAccount'],r['deadlineAt']) for r in held],[('unknown','acc6',NOW+900)])
  reads=[]
  result=reconcile_manual_reply(self.store,'999',request_ref,verify=lambda store,current,reply:reads.append(reply['request_ref']))
  self.assertEqual((reads,result['kind'],result['platformWrites'],result['realSends']),([request_ref],'agent_generated_v2',0,0))
 def test_long_timeline_is_paged_newest_first_without_gaps_or_duplicates(self):
  from lib.conversation_workbench import TIMELINE_PAGE
  for n in range(TIMELINE_PAGE+40):
   self.store.db.execute("INSERT INTO inbound_turn VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
    (f'turn-{n:024x}',self.plan,'creator-1','123','999',str(2000+n),f'{n:064x}','text',f'm{n}',int((NOW+n)*1000),0,NOW+n))
  first=conversation_detail(self.root,self.store,'999')
  self.assertEqual((len(first['timeline']),first['timelineHasOlder']),(TIMELINE_PAGE,True))
  second=conversation_detail(self.root,self.store,'999',before=first['timelineCursor'])
  ids=[r['id'] for r in second['timeline']]+[r['id'] for r in first['timeline']]
  self.assertEqual(len(ids),len(set(ids)));self.assertEqual(len(ids),TIMELINE_PAGE+41)
  self.assertFalse(second['timelineHasOlder']);self.assertIsNone(second['timelineCursor'])
  with self.assertRaisesRegex(CycleError,'conversation_query_invalid'):
   conversation_detail(self.root,self.store,'999',before='not-a-cursor')
 def test_candidate_prefilter_is_a_superset_of_the_row_rule_when_receipt_cids_are_missing(self):
  from unittest.mock import patch
  from lib import conversation_workbench as W
  from lib.cycle_delivery import Deliveries
  Deliveries(self.store)
  self.store.db.execute("INSERT INTO relationship VALUES(?,?,?,'auto',0,1,0,1)",(self.plan,'creator-9','909'))
  # The only fact: an agency-backend message whose id also sits in an old receipt with no conversation id.
  self.store.db.execute("INSERT INTO inbox_event VALUES(?,?,?,?,?,?,?,0,?)",
   (self.plan,'990','7001','909','ourMessages',int(NOW*1000),json.dumps({'conversationId':'990','oecId':'909','messageId':'7001','kind':'ourMessages'}),NOW))
  Service(self.store).capture(self.plan,'990','909',[{'messageId':'7001','format':'text','text':'Ciao dal negozio','nativeType':'text','rawSha256':'z'}])
  self.store.db.execute("INSERT INTO cycle_delivery(id,plan_id,creator_id,oec,pid,source_id,snapshot,created,expires,state) VALUES('d9',?,'creator-9','909','1','s9','{}',?,?,'confirmed')",(self.plan,NOW,NOW+1))
  self.store.db.execute("INSERT INTO cycle_delivery_part(delivery_id,kind,request_ref,state,receipt) VALUES('d9','card','req-d9','confirmed',?)",(json.dumps({'messageId':'7001'}),))
  everyone=lambda db,plan:{r[0] for r in db.execute('SELECT creator_id FROM relationship WHERE plan_id=?',(plan,))}
  views=('all','human','technical','agent','waiting','completed')
  with patch.object(W,'_candidate_creators',everyone):
   reference=[list_conversations(self.root,self.store,view,limit=100) for view in views]
  current=[list_conversations(self.root,self.store,view,limit=100) for view in views]
  strip=lambda pages:[{k:v for k,v in page.items() if k!='stats'} for page in pages]
  self.assertEqual(strip(current),strip(reference))
  self.assertIn('creator-9',{row['creatorId'] for row in current[0]['items']})
 def test_cursor_walks_the_whole_queue_in_order_without_the_offset_cap(self):
  for n in range(230):
   creator=f'creator-q{n:03d}';oec=str(5000+n)
   self.store.db.execute("INSERT INTO relationship VALUES(?,?,?,'human',0,1,0,1)",(self.plan,creator,oec))
   if n%2:
    self.store.db.execute("INSERT INTO inbound_turn VALUES(?,?,?,?,?,?,?,?,?,?,0,?)",
     (f'turn-q{n}',self.plan,creator,oec,str(8000+n),str(9000+n),'h','text','ciao',int((NOW-1000*n)*1000),NOW))
  whole=list_conversations(self.root,self.store,'human',limit=100)
  expected=[row['creatorId'] for row in whole['items']]
  walked,after=[],None
  while True:
   page=list_conversations(self.root,self.store,'human',limit=100,after=after)
   self.assertEqual(page['total'],whole['total'])
   walked+=[row['creatorId'] for row in page['items']]
   self.assertEqual(page['nextCursor'] is None,page['nextOffset'] is None)
   if page['nextCursor'] is None:break
   after=page['nextCursor']
  self.assertEqual(len(walked),whole['total']);self.assertEqual(len(set(walked)),len(walked))
  self.assertEqual(walked[:100],expected)
  # Oldest wait first: the creator whose message is oldest leads the queue among dated rows.
  self.assertEqual(walked[0],'creator-q229')
  self.assertNotIn('_since',whole['items'][0]);self.assertGreater(whole['stats']['sqlStatements'],0)
  for bad in ('1~x~creator',f"9~1.0~c",'1~1.0~'):
   with self.assertRaisesRegex(CycleError,'conversation_query_invalid'):list_conversations(self.root,self.store,'human',after=bad)
  with self.assertRaisesRegex(CycleError,'conversation_query_invalid'):list_conversations(self.root,self.store,'human',offset=10,after=whole['nextCursor'])
 def test_recorded_receipts_read_by_sqlite_match_the_python_rules(self):
  from lib.conversation_workbench import _recorded_messages
  from lib.cycle_delivery import Deliveries
  Deliveries(self.store)
  rows=[('d-ok','{"conversationId":"501"}','{"messageId":"9001"}','{"messageId":9002}'),
        ('d-bad','not json','{bad','{"messageId":null}'),
        ('d-array','[1,2]','[{"messageId":"x"}]','"9003"'),
        ('d-intent','{}','{"messageId":"9004"}',None)]
  for delivery,snapshot,receipt,confirmation in rows:
   self.store.db.execute("INSERT INTO cycle_delivery(id,plan_id,creator_id,oec,pid,source_id,snapshot,created,expires,state) VALUES(?,?,?,?,?,?,?,?,?,'confirmed')",
    (delivery,self.plan,'creator-1','123','1','s-'+delivery,snapshot,NOW,NOW+1))
   self.store.db.execute("INSERT INTO cycle_delivery_part(delivery_id,kind,request_ref,state,receipt,confirmation) VALUES(?,?,?,'confirmed',?,?)",
    (delivery,'card','ref-'+delivery,receipt,confirmation))
  self.store.db.execute("INSERT INTO cycle_conversation_intent(delivery_id,request_ref,state,cid) VALUES('d-intent','conv-ref','confirmed','777')")
  tables={r[0] for r in self.store.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
  self.assertEqual({row for row in _recorded_messages(self.store.db,self.plan,tables) if row[2].startswith('900')},
                   {('123','501','9001'),('123','501','9002'),('123','777','9004')})
 def test_platform_messages_beyond_one_source_page_are_reachable(self):
  from lib.conversation_workbench import TIMELINE_PAGE
  total=TIMELINE_PAGE+101
  for n in range(total):
   mid=str(90000+n)
   self.store.db.execute("INSERT INTO inbox_event VALUES(?,?,?,?,?,?,?,0,?)",
    (self.plan,'999',mid,'123','ourMessages',int((NOW-total+n)*1000),json.dumps({'messageId':mid}),NOW))
   Service(self.store).capture(self.plan,'999','123',[{'messageId':mid,'format':'text','text':f'p{n}','nativeType':'text','rawSha256':mid}])
  seen=[];before=None
  for _ in range(10):
   page=conversation_detail(self.root,self.store,'999',before=before)
   seen=[r['id'] for r in page['timeline']]+seen
   if not page['timelineHasOlder']:break
   before=page['timelineCursor']
  platform=[i for i in seen if i.startswith('platform-')]
  self.assertEqual(len(platform),total);self.assertEqual(len(seen),len(set(seen)))
 def test_platform_messages_sharing_one_timestamp_are_all_reachable(self):
  from lib.conversation_workbench import TIMELINE_PAGE
  base=90000
  for total in (TIMELINE_PAGE+1,601,602,1000):
   self.store.db.execute("DELETE FROM inbox_event WHERE kind='ourMessages'")
   for n in range(total):
    mid=str(base+n)
    self.store.db.execute("INSERT OR IGNORE INTO inbox_event VALUES(?,?,?,?,?,?,?,0,?)",
     (self.plan,'999',mid,'123','ourMessages',int((NOW-500)*1000),json.dumps({'messageId':mid}),NOW))
    Service(self.store).capture(self.plan,'999','123',[{'messageId':mid,'format':'text','text':f'p{n}','nativeType':'text','rawSha256':mid}])
   seen=[];before=None
   for _ in range(20):
    page=conversation_detail(self.root,self.store,'999',before=before)
    seen=[r['id'] for r in page['timeline']]+seen
    if not page['timelineHasOlder']:break
    before=page['timelineCursor']
   platform=[i for i in seen if i.startswith('platform-')]
   self.assertEqual((total,len(platform)),(total,total));self.assertEqual(len(seen),len(set(seen)))
 def test_manual_reconcile_does_not_dispatch_a_ready_intent(self):
  replies=AutoReplies(self.store);request_id='manual-audit-ready-001'
  frozen=replies.prepare_manual(self.plan,'creator-1','999','Ciao',1,request_id)
  result=reconcile_manual_reply(self.store,'999',request_id,
                                verify=lambda *_:self.fail('ready intent must not be dispatched'))
  self.assertEqual(result['state'],'ready')
  self.assertEqual(replies.get(frozen['id'])['state'],'ready')
 def _release(self,outcome):
  detail=conversation_detail(self.root,self.store,'999')
  request_id='release-'+outcome+'-0001'
  result=resolve_manual(self.store,'999',detail['case']['id'],detail['latestTurnId'],outcome,
   detail['creator']['revision'],detail['case']['pendingRevision'],
   detail['creator']['collaboration']['revision'],request_id)
  self.assertEqual(result['outcome'],outcome)
  self.assertTrue(resolve_manual(self.store,'999',detail['case']['id'],detail['latestTurnId'],outcome,
   detail['creator']['revision'],detail['case']['pendingRevision'],
   detail['creator']['collaboration']['revision'],request_id)['duplicate'])
  rel=self.store.db.execute("SELECT mode,rejected FROM relationship WHERE creator_id='creator-1'").fetchone()
  self.assertEqual(rel['mode'],'auto')
  self.assertEqual(bool(rel['rejected']),outcome=='rejected')
  self.assertEqual(conversation_detail(self.root,self.store,'999')['creator']['collaboration']['status'],outcome)
 def test_manual_release_normal(self):self._release('normal')
 def test_manual_release_paid(self):self._release('paid')
 def test_manual_release_rejected(self):self._release('rejected')
 def test_manual_release_rejects_newer_message_or_control(self):
  detail=conversation_detail(self.root,self.store,'999')
  self.store.db.execute("UPDATE relationship SET revision=revision+1")
  with self.assertRaisesRegex(CycleError,'manual_resolution_changed'):
   resolve_manual(self.store,'999',detail['case']['id'],detail['latestTurnId'],'normal',
    detail['creator']['revision'],detail['case']['pendingRevision'],0,'release-stale-0001')
  self.assertEqual(self.store.db.execute('SELECT state FROM service_case').fetchone()[0],'open')
 def test_manual_case_keeps_new_message_and_can_release_after_reading_it(self):
  old=conversation_detail(self.root,self.store,'999')
  self.store.db.execute('INSERT INTO inbound_turn VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
   ('turn-'+('d'*24),self.plan,'creator-1','123','999','1001','e'*64,'text',
    'Ho un altro problema',int((NOW+1)*1000),0,NOW+1))
  self.store.db.execute("UPDATE inbox_pending SET revision=2,state='awaiting_classification'")
  self.store.db.execute("UPDATE relationship SET revision=revision+1")
  current=conversation_detail(self.root,self.store,'999')
  self.assertEqual(current['case']['id'],old['case']['id'])
  self.assertEqual(current['case']['pendingRevision'],2)
  self.assertEqual(current['latestTurnId'],'turn-'+('d'*24))
  result=resolve_manual(self.store,'999',current['case']['id'],current['latestTurnId'],'normal',
   current['creator']['revision'],current['case']['pendingRevision'],
   current['creator']['collaboration']['revision'],'release-after-new-message')
  self.assertEqual(result['state'],'resolved')
  self.assertEqual(self.store.db.execute('SELECT revision FROM service_resolution').fetchone()[0],2)
 def test_agent_queue_includes_waiting_replies_without_a_processing_category(self):
  self.store.db.execute('DELETE FROM service_case')
  self.store.db.execute("UPDATE relationship SET mode='auto'")
  self.store.db.execute("UPDATE inbox_pending SET state='awaiting_classification'")
  result=list_conversations(self.root,self.store,'agent')
  self.assertEqual(result['total'],1);self.assertTrue(result['items'][0]['unread'])
  self.assertNotIn('processing',result['counts'])
  with self.assertRaisesRegex(CycleError,'conversation_query_invalid'):
   list_conversations(self.root,self.store,'processing')
 def test_three_model_failures_are_visible_on_the_pending_conversation(self):
  from lib.template_library import DEFAULT_AGENT_SETTING,save_agent_setting
  enabled={key:value for key,value in DEFAULT_AGENT_SETTING.items() if key not in ('revision','updatedAt')}
  save_agent_setting(self.store,self.plan,0,{**enabled,'enabled':True})
  self.store.db.execute('DELETE FROM service_case')
  self.store.db.execute("UPDATE relationship SET mode='auto'")
  self.store.db.execute("UPDATE inbox_pending SET state='awaiting_content'")
  for index in range(3):
   self.store.db.execute("INSERT INTO agent_reply_decision_v2(decision_id,plan_id,market,creator_id,turn_id,"
                         "guide_revision,input_hash,input_json,provider,model,mode,state,created_at) "
                         "VALUES(?,?,?,?,?,1,'hash','{}','deepseek','test','production','unknown',?)",
                         ('agent-decision-'+str(index)*24,self.plan,'it','creator-1','turn-'+'a'*24,NOW+index))
  item=list_conversations(self.root,self.store,'agent')['items'][0]
  self.assertEqual(item['queueStatusLabel'],'AI 模型连续失败，需检查')
 def test_detail_timeline_and_draft_revision(self):
  detail=conversation_detail(self.root,self.store,'999');self.assertEqual(detail['creator']['handle'],'alice');self.assertEqual(detail['timeline'][0]['text'],'Ho un problema')
  saved=save_draft(self.store,'999','Risposta',0);self.assertEqual(saved['revision'],1)
  self.assertEqual(conversation_detail(self.root,self.store,'999')['draft']['text'],'Risposta')
 def test_reviewed_human_without_open_case_can_be_completed_once(self):
  self.store.db.execute('DELETE FROM service_case')
  turn=self.store.db.execute('SELECT turn_id FROM inbound_turn').fetchone()[0]
  self.store.db.execute('INSERT INTO turn_review VALUES(?,1,\'human\',\'\',?)',(turn,NOW))
  self.store.db.execute("UPDATE relationship SET mode='auto'")
  detail=conversation_detail(self.root,self.store,'999');self.assertTrue(detail['case']['virtual'])
  result=complete_reviewed_human(self.store,'999',turn,detail['creator']['revision'],detail['case']['pendingRevision'],'已人工核对')
  self.assertFalse(result['duplicate']);self.assertEqual(list_conversations(self.root,self.store,'human')['total'],0)
  duplicate=complete_reviewed_human(self.store,'999',turn,detail['creator']['revision'],detail['case']['pendingRevision'],'已人工核对')
  self.assertTrue(duplicate['duplicate'])
  self.assertEqual(self.store.db.execute('SELECT state FROM service_case').fetchone()[0],'resolved')
 def test_confirm_manual_item_does_not_require_a_send(self):
  detail=conversation_detail(self.root,self.store,'999')
  result=confirm_manual_reply(self.store,'999',detail['case']['id'],None,False,detail['creator']['revision'],detail['case']['pendingRevision'])
  self.assertEqual(result['state'],'resolved');self.assertIsNone(result['manualReplyId'])
  self.assertEqual(self.store.db.execute('SELECT state FROM service_case').fetchone()[0],'resolved')
 def test_collaboration_selector_is_revisioned_and_paid_blocks_without_marking_rejected(self):
  detail=conversation_detail(self.root,self.store,'999');self.assertEqual(detail['creator']['collaboration']['status'],'normal')
  result=set_collaboration(self.store,'999','paid',0,detail['creator']['revision'],'collaboration-request-0001')
  self.assertEqual(result['status'],'paid');self.assertEqual(result['source'],'manual')
  self.assertEqual(self.store.db.execute("SELECT rejected FROM relationship WHERE creator_id='creator-1'").fetchone()[0],0)
 def test_live_showcase_only_upgrades_the_system_default(self):
  self.store.db.execute('DELETE FROM inbox_checkpoint')
  result=Inbox(self.store).ingest(self.plan,'999','123',{'identityVerified':True,'hasMore':False,'events':[
   {'conversationId':'999','oecId':'123','kind':'showcaseNotifications','messageId':'2001','createTimeRaw':int(NOW*1000)}]})
  self.assertEqual(result['historical'],1)  # first read is baseline, not an automatic business event
  self.store.db.execute("UPDATE inbox_checkpoint SET baseline_at=?",(NOW-10,))
  Inbox(self.store).ingest(self.plan,'999','123',{'identityVerified':True,'hasMore':False,'events':[
   {'conversationId':'999','oecId':'123','kind':'showcaseNotifications','messageId':'2002','createTimeRaw':int(NOW*1000)}]})
  detail=conversation_detail(self.root,self.store,'999')
  self.assertEqual(detail['creator']['collaboration']['status'],'collaborated')
  showcase=[row for row in detail['timeline'] if row['kind']=='showcase']
  self.assertEqual([row['text'] for row in showcase],['达人已将商品添加到橱窗','达人已将商品添加到橱窗'])
 def test_manual_rejection_suppresses_all_future_positions(self):
  result=reject_creator(self.store,'999',1,'manual-reject-request')
  self.assertEqual(result['state'],'rejected')
  rel=self.store.db.execute('SELECT rejected,mode,inbox_until FROM relationship WHERE creator_id=\'creator-1\'').fetchone()
  self.assertEqual(tuple(rel),(1,'auto',0));self.assertEqual(self.store.db.execute('SELECT state FROM inbox_pending').fetchone()[0],'suppressed_no_reply')
  self.assertTrue(reject_creator(self.store,'999',1,'manual-reject-request')['duplicate'])

if __name__=='__main__':unittest.main()

class SendOutcomeTests(unittest.TestCase):
 setUp=ConversationWorkbenchTests.setUp;tearDown=ConversationWorkbenchTests.tearDown
 def cli(self):
  import importlib.util
  spec=importlib.util.spec_from_file_location('conversation_workbench_cli',Path(__file__).resolve().parents[1]/'scripts/conversation-workbench.py')
  module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
 def test_a_failed_send_closes_only_what_can_no_longer_be_submitted(self):
  cli=self.cli()
  from lib.manual_command import claim
  # No intent: the failing request's own command is closed, and that id can never create one later.
  claim(self.store,self.plan,'manual-never-created-1','999','h1')
  self.assertEqual(cli.send_outcome(self.store,'it','999','manual-never-created-1'),{'intent':'not_submitted'})
  with self.assertRaisesRegex(CycleError,'manual_command_closed'):
   claim(self.store,self.plan,'manual-never-created-1','999','h1')
  # A ready intent that never started is cancelled atomically before it is reported unsubmitted.
  claim(self.store,self.plan,'manual-ready-intent-1','999','h2')
  frozen=AutoReplies(self.store).prepare_manual(self.plan,'creator-1','999','Ciao',1,'manual-ready-intent-1')
  self.assertEqual(cli.send_outcome(self.store,'it','999','manual-ready-intent-1')['intent'],'not_submitted')
  self.assertEqual(AutoReplies(self.store).get(frozen['id'])['state'],'cancelled')
  # Once dispatch started the result is unresolved and nothing is closed.
  claim(self.store,self.plan,'manual-started-1','999','h3')
  started=AutoReplies(self.store).prepare_manual(self.plan,'creator-1','999','Ciao 2',1,'manual-started-1')
  self.store.db.execute("UPDATE service_reply SET state='inflight',started=? WHERE id=?",(NOW,started['id']))
  self.assertEqual(cli.send_outcome(self.store,'it','999','manual-started-1'),
                   {'intent':'unresolved','state':'inflight','replyId':started['id']})
 def test_reconcile_absence_keeps_a_live_command_open_and_fences_a_closed_one(self):
  cli=self.cli()
  from lib.manual_command import STALE_SECONDS,claim
  # The original request registered and is still before its intent: not found is not "not sent".
  claim(self.store,self.plan,'manual-in-flight-1','999','h')
  self.assertEqual(cli.reconcile_outcome(self.store,'it','999','manual-in-flight-1'),{'intent':'unresolved','state':'received'})
  self.store.db.execute("UPDATE manual_command SET updated_at=? WHERE request_id='manual-in-flight-1'",(NOW-STALE_SECONDS-1,))
  self.assertEqual(cli.reconcile_outcome(self.store,'it','999','manual-in-flight-1'),{'intent':'not_submitted'})
  # The original executor resuming afterwards can no longer create its intent.
  with self.assertRaisesRegex(CycleError,'manual_command_closed'):
   AutoReplies(self.store).prepare_manual(self.plan,'creator-1','999','Ciao',1,'manual-in-flight-1')
  # An id the backend never saw is tombstoned, so its request is refused if it ever arrives.
  self.assertEqual(cli.reconcile_outcome(self.store,'it','999','manual-unseen-0001'),{'intent':'not_submitted'})
  with self.assertRaisesRegex(CycleError,'manual_command_closed'):
   claim(self.store,self.plan,'manual-unseen-0001','999','h')
 def test_the_cli_accepts_every_text_the_bridge_accepts(self):
  import io,json as _json
  cli=self.cli()
  body=_json.dumps({'text':'中'*3500+'😀'*250,'expectedControlRevision':1,'requestId':'manual-size-0001'},ensure_ascii=False).encode()
  self.assertLessEqual(len(body),cli.MAX_INPUT_BYTES)
  original=sys.stdin
  try:
   sys.stdin=io.TextIOWrapper(io.BytesIO(body),encoding='utf-8');self.assertEqual(_json.loads(cli.read_input())['requestId'],'manual-size-0001')
   sys.stdin=io.TextIOWrapper(io.BytesIO(b'x'*(cli.MAX_INPUT_BYTES+1)),encoding='utf-8')
   with self.assertRaisesRegex(CycleError,'input_too_large'):cli.read_input()
  finally:sys.stdin=original
