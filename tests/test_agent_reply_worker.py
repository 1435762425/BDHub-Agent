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
 def test_scheduled_replies_keep_clear_of_the_send_window_on_both_sides(self):
  stamp=lambda h,m:datetime(2026,9,20,h,m,tzinfo=BEIJING).timestamp()
  late=['16:30','24:00']
  self.assertEqual([WORKER.near_send_window(late,30,stamp(h,m)) for h,m in ((15,59),(16,0),(23,59),(0,15),(0,30))],
                   [False,True,True,True,False])
  day=['10:00','12:00']
  self.assertEqual([WORKER.near_send_window(day,30,stamp(h,m)) for h,m in ((9,29),(9,30),(12,29),(12,30))],
                   [False,True,True,False])
 def test_one_time_authorization_requires_a_bounded_request_id(self):
  self.assertEqual(WORKER.authorized_request('agent-now-0001'),'agent-now-0001')
  with self.assertRaisesRegex(Exception,'agent_reply_authorization_invalid'):
   WORKER.authorized_request('../unsafe')
 def test_due_pending_survives_the_old_short_freeze_deadline(self):
  db=self.store.db
  db.execute('CREATE TABLE inbox_pending(plan_id TEXT,creator_id TEXT,state TEXT,due_at REAL)')
  db.execute('CREATE TABLE relationship(plan_id TEXT,creator_id TEXT,mode TEXT,rejected INTEGER,inbox_until REAL)')
  db.execute('CREATE TABLE service_case(plan_id TEXT,creator_id TEXT,state TEXT)')
  db.execute("INSERT INTO inbox_pending VALUES('p','c','policy_review',10)")
  db.execute("INSERT INTO relationship VALUES('p','c','auto',0,11)")
  rows=WORKER.pending_rows(self.store,'p',1000)
  self.assertEqual([row['creator_id'] for row in rows],['c'])
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
 def test_unknown_delivery_or_reply_holds_the_agent_before_any_model_call(self):
  from lib.cycle_auto_reply import reply_blocker
  db=self.store.db
  db.execute('CREATE TABLE cycle_delivery(plan_id TEXT,state TEXT)')
  db.execute('CREATE TABLE service_reply(plan_id TEXT,state TEXT)')
  db.execute("INSERT INTO cycle_delivery VALUES('p','quarantined_unknown')")
  db.execute("INSERT INTO service_reply VALUES('p','confirmed')")
  db.execute("INSERT INTO cycle_delivery VALUES('other','unknown')")
  self.assertIsNone(reply_blocker(db,'p'))
  db.execute("INSERT INTO service_reply VALUES('p','inflight')")
  self.assertEqual(reply_blocker(db,'p'),'reply_unknown')
  db.execute("INSERT INTO cycle_delivery VALUES('p','unknown')")
  self.assertEqual(reply_blocker(db,'p'),'delivery_unknown')
 def test_dispatch_mutex_is_limited_to_the_same_market(self):
  db=self.store.db
  db.execute('CREATE TABLE plan(id TEXT,market TEXT)')
  db.execute('CREATE TABLE cycle_delivery(id TEXT,plan_id TEXT)')
  db.execute('CREATE TABLE cycle_delivery_part(delivery_id TEXT,state TEXT)')
  db.execute("INSERT INTO plan VALUES('br-plan','br'),('it-plan','it')")
  db.execute("INSERT INTO cycle_delivery VALUES('br-delivery','br-plan')")
  db.execute("INSERT INTO cycle_delivery_part VALUES('br-delivery','inflight')")
  self.assertTrue(WORKER.send_dispatch_active(self.store,'br'))
  self.assertFalse(WORKER.send_dispatch_active(self.store,'my'))
  db.execute("UPDATE cycle_delivery_part SET state='confirmed'")
  self.assertFalse(WORKER.send_dispatch_active(self.store,'br'))
 def test_failed_model_turn_is_deferred_without_blocking_a_new_turn(self):
  db=self.store.db
  db.execute('CREATE TABLE agent_reply_decision_v2(plan_id TEXT,turn_id TEXT,mode TEXT,state TEXT,created_at REAL)')
  db.execute("INSERT INTO agent_reply_decision_v2 VALUES('p','old','production','unknown',100)")
  self.assertFalse(WORKER.decision_retry_ready(self.store,'p','old',200))
  self.assertTrue(WORKER.decision_retry_ready(self.store,'p','old',3700))
  self.assertTrue(WORKER.decision_retry_ready(self.store,'p','new',200))
  db.execute("INSERT INTO agent_reply_decision_v2 VALUES('p','old','production','unknown',200)")
  db.execute("INSERT INTO agent_reply_decision_v2 VALUES('p','old','production','unknown',300)")
  self.assertFalse(WORKER.decision_retry_ready(self.store,'p','old',10000))
 def test_waiting_for_creator_is_not_reclassified_without_new_input(self):
  db=self.store.db
  db.execute('CREATE TABLE inbox_pending(plan_id TEXT,creator_id TEXT,state TEXT,due_at REAL)')
  db.execute('CREATE TABLE relationship(plan_id TEXT,creator_id TEXT,mode TEXT,rejected INTEGER)')
  db.execute('CREATE TABLE service_case(plan_id TEXT,creator_id TEXT,state TEXT)')
  db.execute("INSERT INTO relationship VALUES('p','c','auto',0)")
  db.execute("INSERT INTO inbox_pending VALUES('p','c','waiting_contact',10)")
  self.assertEqual(WORKER.pending_rows(self.store,'p',1000),[])
  db.execute("UPDATE inbox_pending SET state='awaiting_content'")
  self.assertEqual(len(WORKER.pending_rows(self.store,'p',1000)),1)

if __name__=='__main__':unittest.main()
