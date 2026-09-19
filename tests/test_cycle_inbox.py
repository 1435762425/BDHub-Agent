import sys,unittest,tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from lib.second_cycle import CycleStore,CycleError
from lib.cycle_inbox import Inbox,REPLY_BATCH_SECONDS,inbox_status
from test_second_cycle import offer,edge,NOW
class InboxTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.now=NOW;self.s=CycleStore(Path(self.tmp.name)/'db',lambda:self.now);self.p=self.s.plan('test','it');self.s.publish(self.p,'s',NOW,[offer()]);self.s.import_edges(self.p,[edge()]);self.i=Inbox(self.s)
  r=self.s.db.execute('SELECT * FROM relationship').fetchone();self.oec=r['oec'];self.creator=r['creator_id']
 def tearDown(self):self.s.close();self.tmp.cleanup()
 def event(self,mid='1',kind='creatorReplies',stamp=None):return dict(messageId=mid,kind=kind,createTimeRaw=int((self.now if stamp is None else stamp)*1000),messageType=7,conversationId='10',oecId=self.oec)
 def ingest(self,events,more=False):return self.i.ingest(self.p,'10',self.oec,dict(events=events,hasMore=more,identityVerified=True))
 def rel(self):return self.s.db.execute('SELECT * FROM relationship').fetchone()
 def test_baseline_unlocks_without_reply_task(self):
  self.ingest([self.event()]);self.assertEqual(self.rel()['unlocked'],1);self.assertEqual(self.rel()['inbox_until'],0);self.assertEqual(inbox_status(self.s,self.p)['pendingContent'],0)
 def test_replay_and_restart_preserve_dedupe(self):
  e=self.event();self.ingest([e]);self.i=Inbox(self.s);rev=self.rel()['revision'];r=self.ingest([e]);self.assertEqual(r['added'],0);self.assertEqual(self.rel()['revision'],rev)
 def test_new_reply_debounce_extends_once(self):
  old=self.event(kind='ourMessages');self.ingest([old]);self.now+=100;new=self.event('2');self.ingest([new,old]);deadline=self.now+REPLY_BATCH_SECONDS;self.assertEqual(self.rel()['inbox_until'],deadline)
  self.now+=30;self.ingest([new,old]);self.assertEqual(self.rel()['inbox_until'],deadline)
  self.ingest([self.event('3'),new]);self.assertEqual(self.rel()['inbox_until'],self.now+REPLY_BATCH_SECONDS)
 def test_showcase_does_not_schedule_followup_or_clear_rejection(self):
  self.s.control(self.p,'reject',1,'human',self.creator,reject=True);self.ingest([self.event(kind='showcaseNotifications')]);self.assertEqual(self.rel()['mode'],'human');self.assertTrue(self.rel()['rejected']);self.assertEqual(self.rel()['inbox_until'],0)
 def test_late_history_does_not_trigger(self):
  self.ingest([]);self.now+=100;self.ingest([self.event(stamp=NOW-100)]);self.assertEqual(inbox_status(self.s,self.p)['pendingContent'],0)
 def test_gap_never_queues_auto_reply(self):
  self.ingest([self.event()]);self.now+=100;r=self.ingest([self.event('2')],more=True);self.assertEqual(r['state'],'gap');self.assertEqual(r['liveReplies'],0);self.assertGreater(self.rel()['inbox_until'],0)
 def test_identity_and_event_conflict_atomic(self):
  e=self.event();self.ingest([e]);bad=dict(e,kind='ourMessages')
  with self.assertRaises(CycleError):self.ingest([bad,self.event('2')])
  self.assertEqual(inbox_status(self.s,self.p)['events'],1)
 def test_missing_timestamp_never_live(self):
  self.ingest([]);e=self.event();e['createTimeRaw']=None;self.ingest([e]);self.assertEqual(inbox_status(self.s,self.p)['pendingContent'],0)
if __name__=='__main__':unittest.main()
