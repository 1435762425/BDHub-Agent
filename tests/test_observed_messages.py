import json
import unittest
from pathlib import Path

import test_conversation_workbench as fixture
from lib.cycle_inbox import Inbox
from lib.cycle_service import Service
from lib.cycle_auto_reply import AutoReplies
from lib.conversation_workbench import conversation_detail,list_conversations
from lib.observed_messages import outbound_messages,replied_after
from lib.agent_reply_v2 import production_context
from test_second_cycle import NOW


class ObservedMessagesTests(unittest.TestCase):
    def setUp(self):
        fixture.ConversationWorkbenchTests.setUp(self)
        AutoReplies(self.store)
    tearDown=fixture.ConversationWorkbenchTests.tearDown

    def observe(self,mid='2000',stamp=NOW+10,text='Balasan dari pusat institusi',cid='999',oec='123'):
        event={'messageId':mid,'kind':'ourMessages','createTimeRaw':int(stamp*1000),
               'messageType':1000,'conversationId':cid,'oecId':oec}
        Inbox(self.store).ingest(self.plan,cid,oec,{'identityVerified':True,'hasMore':False,'events':[event]})
        body={'messageId':mid,'format':'text','text':text,'nativeType':'text','rawSha256':'test'}
        Service(self.store).capture(self.plan,cid,oec,[body])

    def test_backend_reply_is_preserved_and_visible_once(self):
        self.observe();self.observe()
        rows=conversation_detail(self.root,self.store,'999')['timeline']
        self.assertEqual([(row['source'],row['text']) for row in rows if row['id']=='platform-2000'],
                         [('platform','Balasan dari pusat institusi')])
        self.assertTrue(replied_after(self.store.db,self.plan,'999','123',NOW))
        self.assertEqual(outbound_messages(self.store.db,self.plan,'998','123'),[])

    def test_registered_message_id_is_not_displayed_as_a_second_platform_message(self):
        reply=AutoReplies(self.store).prepare_manual(self.plan,'creator-1','999','Ciao',1,'manual-dedupe-001')
        self.store.db.execute('UPDATE service_reply SET receipt=? WHERE id=?',
                              (json.dumps({'messageId':'2000'}),reply['id']))
        self.observe()
        self.assertEqual(outbound_messages(self.store.db,self.plan,'999','123'),[])

    def test_same_message_id_in_another_conversation_does_not_hide_backend_reply(self):
        Inbox(self.store).ingest(self.plan,'888','123',{'identityVerified':True,'hasMore':False,'events':[]})
        reply=AutoReplies(self.store).prepare_manual(self.plan,'creator-1','888','Ciao',1,'manual-other-cid-001')
        self.store.db.execute('UPDATE service_reply SET receipt=? WHERE id=?',
                              (json.dumps({'messageId':'2000'}),reply['id']))
        self.observe()
        self.assertEqual(len(outbound_messages(self.store.db,self.plan,'999','123')),1)

    def test_outbound_only_conversation_can_be_opened(self):
        self.store.db.execute("INSERT INTO relationship VALUES(?,?,?,'auto',0,0,0,1)",
                              (self.plan,'creator-2','456'))
        self.observe(cid='888',oec='456')
        rows=list_conversations(self.root,self.store,'waiting')['items']
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['conversationId'],'888')
        detail=conversation_detail(self.root,self.store,'888')
        self.assertIsNone(detail['latestTurnId'])
        self.assertEqual(detail['timeline'][0]['source'],'platform')

    def test_ai_context_includes_prior_operator_reply_and_excludes_future_message(self):
        self.observe('2000',NOW-1,'Earlier operator reply')
        self.observe('2001',NOW+1,'Later operator reply')
        context=production_context(Path(__file__).resolve().parents[1],
                                   self.store,self.plan,'it','turn-'+'a'*24)
        messages={row['id']:row for row in context['messages']}
        self.assertEqual(messages['platform-2000']['text'],'Earlier operator reply')
        self.assertNotIn('platform-2001',messages)

    def test_operator_reply_defers_old_turn_but_new_creator_reply_can_resume(self):
        from test_agent_reply_worker import WORKER
        self.store.db.execute('DELETE FROM service_case')
        self.store.db.execute("UPDATE relationship SET mode='auto'")
        self.store.db.execute("UPDATE inbox_pending SET state='awaiting_content',due_at=?",(NOW,))
        self.assertEqual(len(WORKER.pending_rows(self.store,self.plan,NOW)),1)
        self.observe()
        self.assertEqual(WORKER.pending_rows(self.store,self.plan,NOW+11),[])
        self.store.db.execute('INSERT INTO inbound_turn VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
            ('turn-'+'b'*24,self.plan,'creator-1','123','999','2002','c'*64,'text','A new question',int((NOW+20)*1000),0,NOW+20))
        self.assertEqual(len(WORKER.pending_rows(self.store,self.plan,NOW+21)),1)


if __name__=='__main__':unittest.main()
