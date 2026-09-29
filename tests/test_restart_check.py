import sqlite3,sys,tempfile,unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
import lib.restart_check as check  # noqa:E402

class RestartCheckTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);(self.root/'var').mkdir()
  with closing(sqlite3.connect(self.root/'var/second-cycle.sqlite')) as db,db:
   for table in check.CONTROL_TABLES:db.execute(f'CREATE TABLE {table}(k,v)')
   db.executescript("""CREATE TABLE cycle_delivery_part(delivery_id,kind,request_ref,state);
     CREATE TABLE service_reply(state);CREATE TABLE cycle_delivery(id,state);CREATE TABLE cycle_conversation_intent(delivery_id,state);
     CREATE TABLE workflow_stage_claim(stage_run_id);
     INSERT INTO market_automation_setting VALUES('it',1);
     INSERT INTO cycle_delivery_part VALUES('d1','card','r1','confirmed'),('d1','text','r2','confirmed');""")
  self.patches=[patch.object(check,'processes',return_value={'head':'h','runtimeDirty':None,'alive':[{'role':'scheduler','current':True}]})]
  for p in self.patches:p.start()
 def tearDown(self):
  for p in self.patches:p.stop()
  self.tmp.cleanup()
 def db(self):return closing(sqlite3.connect(self.root/'var/second-cycle.sqlite'))

 def test_a_replaced_reference_fails_even_with_equal_count_and_uniqueness(self):
  base=check.preflight(self.root)
  for market in check.MARKETS:(self.root/f'var/im-session-{market}.json').write_text('{"state":"ready"}')
  with self.db() as db,db:db.execute("INSERT INTO cycle_delivery_part VALUES('d2','card','r3','ready')")
  self.assertTrue(check.postflight(self.root,base)['ok'])  # new rows after the baseline are fine
  with self.db() as db,db:db.execute("UPDATE cycle_delivery_part SET request_ref='rX' WHERE request_ref='r2'")
  result=check.postflight(self.root,base)
  self.assertFalse(result['ok'])
  self.assertEqual([c['name'] for c in result['checks'] if not c['ok']],['request_refs_kept'])

 def test_changed_controls_or_an_unready_session_fail(self):
  base=check.preflight(self.root)
  with self.db() as db,db:db.execute("UPDATE market_automation_setting SET v=0")
  (self.root/'var/im-session-it.json').write_text('{"state":"starting"}')
  failed={c['name'] for c in check.postflight(self.root,base)['checks'] if not c['ok']}
  self.assertEqual(failed,{'controls_unchanged','im_sessions_ready'})

 def test_drained_counts_a_conversation_being_opened_as_inflight(self):
  with self.db() as db,db:
   db.execute("INSERT INTO cycle_delivery VALUES('d9','running')")
   db.execute("INSERT INTO cycle_conversation_intent VALUES('d9','inflight')")
  with patch('lib.operations_scheduler.scheduler_state',return_value={'running':False}):
   result=check.drained(self.root)
  self.assertFalse(result['ok'])
  self.assertEqual(result['checks'][2]['detail']['conversationIntents'],1)
  with patch('lib.operations_scheduler.scheduler_state',return_value={'running':False}):
   self.assertFalse(check.drained(self.root,workers_stopped=True)['checks'][-1]['ok'])

 def test_release_id_is_a_plain_name(self):
  for bad in ('../x','a','a b',''):
   with self.assertRaises(ValueError):check.evidence_path(self.root,bad,'preflight')

if __name__=='__main__':unittest.main()
