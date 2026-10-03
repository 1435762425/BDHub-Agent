import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from lib.account_identity import current_generation,publish_generation,promote_capabilities
from lib.agent_reply_v2 import authorize_rollout,settle_pilot_capability
from lib.cycle_auto_reply import AutoReplies
from lib.cycle_inbox import Inbox
from lib.market_agent_reply import run_reply
from lib.schema_migrations import apply_database
from lib.second_cycle import CycleError,CycleStore,encoded
from lib.template_library import DEFAULT_AGENT_SETTING,save_agent_setting


class AgentPilotCompletionTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.now=1700000000
  with CycleStore(self.root/'var/second-cycle.sqlite') as store:self.plan=store.plan('bjn-local-research','br')
  apply_database(self.root,'second-cycle')
  self.store=CycleStore(self.root/'var/second-cycle.sqlite',lambda:self.now);Inbox(self.store);self.replies=AutoReplies(self.store)
  (self.root/'config').mkdir()
  (self.root/'config/market-accounts.json').write_text((ROOT/'config/market-accounts.json').read_text())
  setting={k:v for k,v in DEFAULT_AGENT_SETTING.items() if k not in ('revision','updatedAt')}
  save_agent_setting(self.store,self.plan,0,{**setting,'enabled':True})
  authorize_rollout(self.store,self.plan,'br','pilot','page-original-pilot')
  publish_generation(self.store,market='br',account='acc1',role='communications',reason='baseline',
   identity={'browserRef':'browser','httpRef':'http','imRef':'im','institutionFingerprint':'institution'},
   capabilities={'inbox_read':{'state':'verified'},'agent_reply':{'state':'not_tested'}})
  self.now+=1;self.insert('original','unknown',self.now)
 def tearDown(self):self.store.close();self.tmp.cleanup()
 def insert(self,rid,state,started):
  proof={'status':'confirmed','requestRef':'request-'+rid,'conversationId':'123','messageId':'456','evidenceRef':'exact-original-history'}
  self.store.db.execute("""INSERT INTO service_reply(id,plan_id,creator_id,pending_revision,oec,cid,kind,text,context_hash,
    state,request_ref,receipt,proof,created,started,control_revision,sender_account,sender_identity)
    VALUES(?,?,?,1,'789','123','agent_generated_v2','Hello','context',?,?,?,?,?,?,1,'acc1','777')""",
    (rid,self.plan,'creator-'+rid,state,'request-'+rid,encoded({'requestRef':'request-'+rid,'messageId':'456'}),
     encoded(proof) if state=='confirmed' else None,started,started))
 def proof(self,source=None):
  proof={'status':'confirmed','requestRef':'request-original','conversationId':'123','messageId':'456','evidenceRef':'exact-original-history'}
  if source:proof['source']=source
  return proof
 def confirmed(self,source=None):self.replies.confirm('original',self.proof(source))
 def capability(self):return current_generation(self.store,'br','acc1')['capabilities']['agent_reply']
 def controls(self):
  return [tuple(r) for r in self.store.db.execute('SELECT * FROM control_event ORDER BY plan_id,event_id')]
 def settle(self):return settle_pilot_capability(self.root,self.store,self.plan,'br')
 def test_confirmed_original_pilot_promotes_once_without_granting_full(self):
  self.confirmed('passive_inbox');controls=self.controls()
  self.assertTrue(self.settle())
  self.assertEqual(self.capability()['state'],'verified')
  self.assertEqual(self.capability()['evidenceRef'],'service-reply:original')
  before=self.store.db.total_changes
  self.assertFalse(self.settle());self.assertEqual(self.store.db.total_changes,before)
  self.assertEqual(self.controls(),controls)
 def test_unknown_or_isolated_pilot_cannot_use_another_confirmed_reply(self):
  self.insert('later','confirmed',self.now+1)
  for state in ('unknown','isolated'):
   self.store.db.execute('UPDATE service_reply SET state=? WHERE id=\'original\'',(state,))
   self.assertFalse(self.settle());self.assertEqual(self.capability()['state'],'not_tested')
 def test_missing_pilot_grant_cannot_promote(self):
  self.confirmed();self.store.db.execute("DELETE FROM control_event WHERE event_id='agent-v2-first-send'")
  before=self.store.db.total_changes
  self.assertFalse(self.settle());self.assertEqual(self.store.db.total_changes,before)
  self.assertEqual(self.capability()['state'],'not_tested')
 def test_later_pilot_grant_cannot_authorize_an_older_reply(self):
  self.confirmed()
  self.store.db.execute("UPDATE control_event SET payload=? WHERE event_id='agent-v2-first-send'",
                       (encoded({'requestId':'page-later-pilot','authorizedAt':self.now+1}),))
  with self.assertRaisesRegex(CycleError,'agent_pilot_authorization_invalid'):self.settle()
  self.assertEqual(self.capability()['state'],'not_tested')
 def test_missing_original_identity_and_changed_account_remain_blocked(self):
  self.confirmed()
  for account,identity,code in [('acc1',None,'reply_original_sender_unknown'),(None,'777','reply_original_sender_unknown'),
                                 ('acc2','777','reply_original_account_changed')]:
   with self.subTest(account=account,identity=identity):
    self.store.db.execute('UPDATE service_reply SET sender_account=?,sender_identity=? WHERE id=\'original\'',(account,identity))
    with self.assertRaisesRegex(CycleError,code):self.settle()
    self.assertEqual(self.capability()['state'],'not_tested')
 def test_changed_communications_account_cannot_inherit_original_proof(self):
  self.confirmed()
  with patch('lib.market_accounts.load_config',return_value={'markets':{'br':{'roles':{'communications':'acc2'}}}}):
   with self.assertRaisesRegex(CycleError,'reply_original_account_changed'):self.settle()
  self.assertIsNone(current_generation(self.store,'br','acc2'))
 def test_missing_published_identity_cannot_be_replaced_from_a_reply(self):
  self.confirmed();before=self.store.db.total_changes
  with patch('lib.account_identity.current_generation',return_value=None):
   with self.assertRaisesRegex(CycleError,'identity_capability_promotion_invalid'):self.settle()
  self.assertEqual(self.store.db.total_changes,before)
 def test_inexact_proof_or_passive_receipt_cannot_promote(self):
  self.confirmed('passive_inbox')
  for changed in ({'requestRef':'another-request'},{'conversationId':'999'},{'messageId':'other-message'},{'evidenceRef':None}):
   with self.subTest(changed=changed):
    self.store.db.execute('UPDATE service_reply SET proof=? WHERE id=\'original\'',(encoded(self.proof('passive_inbox')|changed),))
    with self.assertRaisesRegex(CycleError,'reply_proof_invalid'):self.settle()
    self.assertEqual(self.capability()['state'],'not_tested')
 def test_crash_before_publication_retries_only_local_capability_completion(self):
  self.confirmed();controls=self.controls()
  with patch('lib.account_identity.promote_capabilities',side_effect=RuntimeError('injected_crash')):
   with self.assertRaisesRegex(RuntimeError,'injected_crash'):self.settle()
  self.assertEqual(self.replies.get('original')['state'],'confirmed')
  self.assertTrue(self.settle());self.assertEqual(self.controls(),controls)
 def test_crash_after_publication_is_a_zero_write_replay(self):
  self.confirmed()
  def publish_then_crash(*args,**kwargs):promote_capabilities(*args,**kwargs);raise RuntimeError('injected_crash')
  with patch('lib.account_identity.promote_capabilities',side_effect=publish_then_crash):
   with self.assertRaisesRegex(RuntimeError,'injected_crash'):self.settle()
  before=self.store.db.total_changes
  self.assertFalse(self.settle());self.assertEqual(self.store.db.total_changes,before)
 def test_active_original_readback_completes_capability_with_zero_new_sends(self):
  @contextmanager
  def authenticated(*_args,**kwargs):
   self.assertTrue(kwargs['read_only'])
   yield {'auth':SimpleNamespace(account_name='acc1',im_id='777'),
          'session':SimpleNamespace(conversation=lambda *_:SimpleNamespace(conversation_id='123')),'adapter':None}
  adapter=SimpleNamespace(readback=lambda *_args,**_kwargs:self.proof())
  with patch('lib.market_agent_reply.authenticated',authenticated), \
       patch('lib.market_agent_reply.write_gate',side_effect=AssertionError('new_write_forbidden')), \
       patch('lib.italy_im_delivery.ItalyImDeliveryAdapter',return_value=adapter):
   result=run_reply(self.root,self.store,self.replies,self.replies.get('original'),'br',pilot=True)
  self.assertEqual((result['state'],result['platformWrites'],result['realSends']),('confirmed',0,0))
  self.assertEqual(self.capability()['state'],'verified')
 def test_existing_full_authorization_is_preserved_during_completion(self):
  self.confirmed();authorize_rollout(self.store,self.plan,'br','full','page-full-after-proof');controls=self.controls()
  self.assertTrue(self.settle());self.assertEqual(self.controls(),controls)


if __name__=='__main__':unittest.main()
