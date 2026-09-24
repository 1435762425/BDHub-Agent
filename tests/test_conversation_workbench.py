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
  with self.assertRaisesRegex(CycleError,'manual_reconcile_scope_invalid'):
   reconcile_manual_reply(self.store,'999',request_id,market='br',verify=verify)

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
