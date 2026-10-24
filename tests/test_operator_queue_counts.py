"""Operator-only counts stay identical to full rows while avoiding display-only work."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from lib import conversation_workbench as workbench
from lib.cycle_auto_reply import AutoReplies
from lib.cycle_inbox import Inbox
from lib.cycle_service import Service
from lib.operations_console import _human_queue
from lib.schema_migrations import apply_database
from lib.second_cycle import CycleError,CycleStore,digest

NOW=1_790_000_000.0


class OperatorQueueCountsTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);(self.root/'var').mkdir()
  self.path=self.root/'var/second-cycle.sqlite'
  with CycleStore(self.path,lambda:NOW) as store:
   self.plans={market:store.plan('bjn-local-research',market) for market in ('it','br')}
  apply_database(self.root,'second-cycle',clock=lambda:NOW)
  self.store=CycleStore(self.path,lambda:NOW);Inbox(self.store);Service(self.store);AutoReplies(self.store)
  self.serial=0;self.creators={}

 def tearDown(self):
  self.store.close();self.tmp.cleanup()

 def add(self,key,*,market='it',mode='auto',pending=None,case=False,hold=None,reply=None,reply_kind='agent_generated_v2'):
  self.serial+=1;plan=self.plans[market];creator='creator-'+key
  oec=str(10000+self.serial);cid=str(20000+self.serial)
  self.store.db.execute('INSERT INTO relationship VALUES(?,?,?,?,0,1,0,1)',(plan,creator,oec,mode))
  if pending:self.store.db.execute('INSERT INTO inbox_pending VALUES(?,?,1,?,?)',(plan,creator,NOW,pending))
  if case:self.store.db.execute("INSERT INTO service_case VALUES(?,?,?,'open',1,'link_issue',?,?,'not_sent')",('case-'+key+'-'+market,plan,creator,NOW,NOW))
  if hold:self.store.db.execute('INSERT INTO inbox_checkpoint VALUES(?,?,?,?,?,?)',(plan,cid,oec,NOW-100,NOW,hold))
  if reply:self.store.db.execute("INSERT INTO service_reply(id,plan_id,creator_id,pending_revision,oec,cid,kind,text,context_hash,state,request_ref,created) VALUES(?,?,?,1,?,?,?,'Ciao','hash',?,?,?)",('reply-'+key+'-'+market,plan,creator,oec,cid,reply_kind,reply,'reply-request-'+key+'-'+market,NOW))
  value={'plan':plan,'creator':creator,'oec':oec,'cid':cid};self.creators[(market,key)]=value
  return value

 def turn(self,value,*,action=None,at=NOW,review=None,historical=0):
  self.serial+=1;turn_id='turn-'+digest([value,self.serial])[:24]
  self.store.db.execute("INSERT INTO inbound_turn VALUES(?,?,?,?,?,?,?,'text','Ciao',?,?,?)",(turn_id,value['plan'],value['creator'],value['oec'],value['cid'],str(self.serial),digest(turn_id),int(at*1000),historical,at))
  if action:self.store.db.execute("INSERT INTO reply_classification VALUES(?,?,?,'v1','deepseek','model','ready',?,NULL,?,?)",('classification-'+turn_id,'classification-request-'+turn_id,digest(turn_id),json.dumps({'turn':{'turnId':turn_id}}),json.dumps({'action':action}),at))
  if review:self.store.db.execute("INSERT INTO turn_review VALUES(?,1,?,'checked',?)",(turn_id,review,at))
  return turn_id

 def compare(self,expected,market='it'):
  with CycleStore(self.path,lambda:NOW,readonly=True) as reader:
   reader.db.execute('PRAGMA query_only=ON');reader.db.execute('BEGIN')
   full=workbench.list_conversations(self.root,reader,'all',limit=100,market=market)
   actual=workbench.operator_queue_counts(reader,market)
   self.assertEqual(actual,expected)
   self.assertEqual(actual,{key:full['counts'][key] for key in ('human','technical')})
   self.assertEqual(_human_queue(self.root,reader,market),actual)

 def test_case_mode_and_pending_human_precede_technical_holds_without_a_turn(self):
  self.add('case',case=True,reply='isolated')
  self.add('mode',mode='human',hold='gap')
  self.add('pending',pending='human',hold='backfilling')
  self.compare({'human':3,'technical':0})

 def test_latest_human_decision_needs_unresolved_pending_and_precedes_technical(self):
  self.turn(self.add('active',pending='awaiting_classification',reply='isolated'),action='human')
  self.turn(self.add('no-pending'),action='human')
  self.turn(self.add('resolved',pending='resolved_by_human'),action='human')
  self.turn(self.add('resolved-held',pending='resolved_by_human',hold='gap'),action='human')
  latest=self.add('new-turn',pending='awaiting_classification')
  self.turn(latest,action='human',at=NOW-10);self.turn(latest,action='review_no_followup',at=NOW)
  self.turn(self.add('review-away',pending='awaiting_classification'),action='human',review='review_no_followup')
  self.turn(self.add('review-to-human',pending='awaiting_classification'),action='review_no_followup',review='human')
  self.compare({'human':2,'technical':1})

 def test_answered_no_reply_and_review_only_human_still_need_a_person(self):
  for pending in ('answered','no_reply'):self.turn(self.add(pending,pending=pending),action='human')
  self.turn(self.add('review-only',pending='awaiting_classification'),review='human')
  self.turn(self.add('historical',pending='answered'),review='human',historical=1)
  self.turn(self.add('resolved',pending='resolved_by_human'),review='human')
  self.compare({'human':4,'technical':0})

 def test_gap_backfill_and_isolated_without_inbound_are_technical_and_only_in_their_market(self):
  for key,kwargs in [('gap',{'hold':'gap'}),('backfill',{'hold':'backfilling'}),('isolated',{'reply':'isolated'})]:self.add(key,**kwargs)
  self.add('tracking',hold='tracking');self.add('confirmed',reply='confirmed')
  self.add('same',market='it')
  self.add('same',market='br',mode='human');self.add('br-gap',market='br',hold='gap')
  # Orphan facts do not become conversations and another market's facts cannot hold this relation.
  self.store.db.execute("INSERT INTO service_case VALUES('orphan',?,'creator-orphan','open',1,'human',?,?,'not_sent')",(self.plans['it'],NOW,NOW))
  same=self.creators[('it','same')]
  self.store.db.execute("INSERT INTO inbox_checkpoint VALUES(?,?,?,?,?,'gap')",(self.plans['br'],'99999',same['oec'],NOW,NOW))
  self.compare({'human':0,'technical':3})
  self.compare({'human':1,'technical':1},'br')

 def test_technical_holds_use_oec_across_cids_and_do_not_exclude_paused_or_rejected_relations(self):
  multi=self.add('multi',hold='tracking')
  self.store.db.execute("INSERT INTO inbox_checkpoint VALUES(?,?,?,?,?,'gap')",(multi['plan'],'888888',multi['oec'],NOW,NOW))
  self.add('paused',mode='paused',hold='backfilling')
  rejected=self.add('rejected',hold='gap')
  self.store.db.execute('UPDATE relationship SET rejected=1 WHERE plan_id=? AND creator_id=?',(rejected['plan'],rejected['creator']))
  self.add('manual-isolated',reply='isolated',reply_kind='manual')
  self.compare({'human':0,'technical':4})

 def test_fast_counts_do_not_construct_receipts_external_messages_or_presentation(self):
  self.turn(self.add('human',pending='human'),action='human')
  self.add('technical',reply='isolated')
  with CycleStore(self.path,lambda:NOW,readonly=True) as reader:
   reader.db.execute('PRAGMA query_only=ON')
   names=('_candidate_creators','_recorded_messages','_latest_external','_latest_showcase','_handles','_human_label','agent_setting')
   from contextlib import ExitStack
   with ExitStack() as stack:
    for name in names:stack.enter_context(patch.object(workbench,name,side_effect=AssertionError('display path used')))
    stack.enter_context(patch('lib.reply_scope.unresolved',side_effect=AssertionError('reply scope used')))
    self.assertEqual(workbench.operator_queue_counts(reader),{'human':1,'technical':1})

 def test_empty_is_zero_but_missing_plan_or_classification_table_is_unavailable(self):
  self.compare({'human':0,'technical':0})
  with self.assertRaisesRegex(CycleError,'plan_missing'):workbench.operator_queue_counts(self.store,'uk')
  self.assertIsNone(_human_queue(self.root,self.store,'uk'))
  self.store.db.execute('DROP TABLE inbox_pending')
  with self.assertRaisesRegex(CycleError,'conversation_queue_unavailable'):workbench.operator_queue_counts(self.store)
  self.assertIsNone(_human_queue(self.root,self.store,'it'))

 def test_optional_reply_table_and_unreadable_core_decision_have_distinct_availability(self):
  self.add('human',mode='human');self.add('gap',hold='gap')
  self.store.db.execute('DROP TABLE service_reply')
  self.assertEqual(workbench.operator_queue_counts(self.store),{'human':1,'technical':1})
  self.assertEqual(_human_queue(self.root,self.store,'it'),{'human':1,'technical':1})
  turn=self.turn(self.add('corrupt',pending='awaiting_classification'),action='human')
  self.store.db.execute('UPDATE reply_classification SET decision_json=? WHERE classification_id=?',('{broken','classification-'+turn))
  with self.assertRaises(ValueError):workbench.operator_queue_counts(self.store)
  self.assertIsNone(_human_queue(self.root,self.store,'it'))


if __name__=='__main__':unittest.main()
