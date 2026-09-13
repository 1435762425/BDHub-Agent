import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from test_cycle_inbox import InboxTests
from lib.cycle_service import Service,route
from lib.second_cycle import CycleError
class ServiceTests(unittest.TestCase):
 _base_setup=InboxTests.setUp
 def setUp(self):self._base_setup();self.service=Service(self.s)
 tearDown=InboxTests.tearDown
 event=InboxTests.event
 ingest=InboxTests.ingest
 rel=InboxTests.rel
 def content(self,mid,text,format='text'):return dict(messageId=mid,format=format,text=text,nativeType='text' if format=='text' else 'image',rawSha256='hash-'+str(text))
 def add(self,mid,text,format='text'):
  self.ingest([self.event(mid)]);self.service.capture(self.p,'10',self.oec,[self.content(mid,text,format)])
 def baseline(self):self.ingest([]);self.now+=100
 def process(self):
  self.now+=61;p=self.s.db.execute('SELECT revision FROM inbox_pending').fetchone();return self.service.process(self.p,self.creator,p[0])
 def test_historical_body_capture_never_creates_case(self):
  self.add('1','Non mi contattare più');self.service.process_due(self.p)
  self.assertEqual(self.s.db.execute('SELECT count(*) FROM service_case').fetchone()[0],0);self.assertFalse(self.rel()['rejected'])
 def test_refusal_suppresses_marketing_without_reply(self):
  self.baseline();self.add('1','Non mi contattare più');d=self.process();self.assertEqual(d['action'],'suppress_marketing');self.assertTrue(self.rel()['rejected']);self.assertFalse(d['automaticReply'])
 def test_attachment_routes_human_and_duplicate_does_not_reopen(self):
  self.baseline();self.add('1',None,'attachment_or_unsupported');self.process();self.service.process_due(self.p)
  self.assertEqual(self.s.db.execute('SELECT count(*) FROM service_case').fetchone()[0],1);self.assertEqual(self.rel()['mode'],'human')
 def test_new_reply_blocks_stale_human_resolution(self):
  self.baseline();self.add('1','Il link non funziona');self.process();case=self.s.db.execute('SELECT * FROM service_case').fetchone();revision=self.rel()['revision']
  self.now+=5;self.add('2','Ho un altro problema')
  with self.assertRaisesRegex(CycleError,'case_changed'):self.service.resolve_case(self.p,case['id'],1,revision,'Checked')
 def test_human_resolution_preserves_rejection_and_requires_note(self):
  self.baseline();self.add('1','Non mi contattare più');self.process();case=self.s.db.execute('SELECT * FROM service_case').fetchone();revision=self.rel()['revision']
  with self.assertRaises(CycleError):self.service.resolve_case(self.p,case['id'],1,revision,'')
  self.service.resolve_case(self.p,case['id'],1,revision,'Refusal respected');self.assertTrue(self.rel()['rejected']);self.assertEqual(self.rel()['mode'],'auto')
  self.assertTrue(self.service.resolve_case(self.p,case['id'],1,revision,'Refusal respected')['duplicate'])
 def test_edit_invalidates_assessment_and_keeps_old_content(self):
  self.baseline();self.add('1','Grazie');self.process();self.now+=10;self.service.capture(self.p,'10',self.oec,[self.content('1','Il link non funziona')])
  self.assertEqual(self.s.db.execute('SELECT count(*) FROM inbox_content_version').fetchone()[0],2);self.assertEqual(self.s.db.execute('SELECT revision FROM inbox_pending').fetchone()[0],2)
 def test_ack_does_not_reopen_resolved_topic(self):
  self.baseline();self.add('1','Il link non funziona');self.process();case=self.s.db.execute('SELECT * FROM service_case').fetchone();self.service.resolve_case(self.p,case['id'],1,self.rel()['revision'],'Resolved by operator')
  self.now+=10;self.add('2','Grazie');d=self.process();self.assertEqual(d['category'],'acknowledgement');self.assertEqual(self.rel()['inbox_until'],0)
 def test_other_links_and_samples_keep_both_intents(self):
  d=route([self.content('1','Avete altri link per richiedere campioni?')]);self.assertEqual(d['action'],'human');self.assertEqual(d['category'],'multiple_requests')
 def test_edited_resolved_message_is_reassessed(self):
  self.baseline();self.add('1','Grazie');self.process();self.now+=10;self.service.capture(self.p,'10',self.oec,[self.content('1','Il link non funziona')]);d=self.process();self.assertEqual(d['category'],'link_issue')
 def test_missing_content_waits_instead_of_empty_answer(self):
  self.baseline();self.ingest([self.event('1')]);self.assertEqual(self.process()['state'],'awaiting_content')
 def test_negation_about_merchant_is_not_global_refusal(self):
  self.assertNotEqual(route([self.content('1','Non contattare il venditore')])['category'],'do_not_contact')
 def test_commission_requires_real_tool_and_multi_intent_not_dropped(self):
  self.assertEqual(route([self.content('1','Quanto è la commissione?')])['requiredTools'],['get_current_creator_commission'])
  self.assertEqual(route([self.content('1','Vorrei un campione e il catalogo')])['category'],'multiple_requests')
del InboxTests
if __name__=='__main__':unittest.main()
