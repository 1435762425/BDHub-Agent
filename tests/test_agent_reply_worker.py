import importlib.util
import sqlite3
import unittest
from datetime import datetime,timezone,timedelta
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
SPEC=importlib.util.spec_from_file_location('agent_reply_worker',ROOT/'scripts/run-agent-replies.py')
WORKER=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(WORKER)
BEIJING=timezone(timedelta(hours=8))

class Store:
 def __init__(self):
  self.db=sqlite3.connect(':memory:');self.db.row_factory=sqlite3.Row
 def close(self):self.db.close()

class AgentReplyWorkerTests(unittest.TestCase):
 def setUp(self):self.store=Store()
 def tearDown(self):self.store.close()
 def test_reply_window_is_short_and_end_exclusive(self):
  setting={'replyStart':'15:00','replyEnd':'16:00'}
  stamp=lambda h,m:datetime(2026,9,20,h,m,tzinfo=BEIJING).timestamp()
  self.assertFalse(WORKER.inside(setting,stamp(14,59)))
  self.assertTrue(WORKER.inside(setting,stamp(15,0)))
  self.assertFalse(WORKER.inside(setting,stamp(16,0)))
 def test_waiting_send_batch_does_not_block_reply_window_but_active_dispatch_does(self):
  db=self.store.db
  db.execute('CREATE TABLE cycle_delivery_part(state TEXT)')
  db.execute('CREATE TABLE cycle_bulk_freeze(batch_id TEXT,state TEXT,created_at REAL)')
  db.execute('CREATE TABLE cycle_bulk_runtime(batch_id TEXT,phase TEXT)')
  db.execute("INSERT INTO cycle_bulk_freeze VALUES('batch','running',1)")
  db.execute("INSERT INTO cycle_bulk_runtime VALUES('batch','waiting_window')")
  self.assertFalse(WORKER.send_dispatch_active(self.store))
  db.execute("UPDATE cycle_bulk_runtime SET phase='cohort'")
  self.assertTrue(WORKER.send_dispatch_active(self.store))
  db.execute("UPDATE cycle_bulk_runtime SET phase='waiting_window'")
  db.execute("INSERT INTO cycle_delivery_part VALUES('inflight')")
  self.assertTrue(WORKER.send_dispatch_active(self.store))
 def test_operator_review_wins_before_model_classification(self):
  self.store.db.execute('CREATE TABLE turn_review(turn_id TEXT,revision INTEGER,correct_action TEXT)')
  self.store.db.execute("INSERT INTO turn_review VALUES('turn-1',1,'human')")
  self.assertEqual(WORKER.reviewed_action(self.store,{'turn_id':'turn-1'})['action'],'human')

if __name__=='__main__':unittest.main()
