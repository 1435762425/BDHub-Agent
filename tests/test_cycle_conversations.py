import sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from lib.cycle_conversations import ConversationIndex

def page(cid='1',oec='2',more=True,nxt=10):
 return dict(conversations=[dict(conversationId=cid,oecId=oec,conversationType=2,ticketPresent=True)],hasMore=more,nextCursor=nxt,invalidConversations=0,otherMarketConversations=0)
class IndexTests(unittest.TestCase):
 def setUp(self):
  self.t=tempfile.TemporaryDirectory();self.path=Path(self.t.name)/'db';self.x=ConversationIndex(self.path);self.x.state('it:acc6')
 def tearDown(self):self.x.close();self.t.cleanup()
 def test_restart_resumes_cursor_and_preserves_lookup(self):
  self.x.save('it:acc6',0,page());self.x.close();self.x=ConversationIndex(self.path)
  self.assertEqual(self.x.state('it:acc6')['cursor'],10);self.assertEqual(self.x.find('it:acc6','2')[0]['conversationId'],'1');self.assertEqual(self.x.find('mx:acc6','2'),[])
 def test_conflicting_identity_rolls_back_page_and_cursor(self):
  self.x.save('it:acc6',0,page())
  with self.assertRaisesRegex(ValueError,'identity_conflict'):self.x.save('it:acc6',10,page(oec='99',nxt=20))
  self.assertEqual(self.x.state('it:acc6')['cursor'],10);self.assertEqual(self.x.find('it:acc6','2')[0]['conversationId'],'1')
 def test_cursor_cycle_rejected(self):
  self.x.save('it:acc6',0,page())
  with self.assertRaisesRegex(ValueError,'cursor_cycle'):self.x.save('it:acc6',10,page(nxt=0))
 def test_terminal_scan_no_repeat_and_no_sensitive_payload(self):
  self.x.save('it:acc6',0,page(more=False));self.assertEqual(self.x.state('it:acc6')['state'],'complete')
  with self.assertRaisesRegex(ValueError,'cursor_changed'):self.x.save('it:acc6',10,page())
  saved=self.x.db.execute('SELECT payload FROM scan_page').fetchone()[0];self.assertNotIn('ticket',saved)
if __name__=='__main__':unittest.main()
