import hashlib
import sqlite3
import sys
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from lib import market_agent_reply as M  # noqa:E402
from lib.second_cycle import CycleError  # noqa:E402


class Store:
 def __init__(self):
  self.db=sqlite3.connect(':memory:');self.db.row_factory=sqlite3.Row
  self.db.execute('CREATE TABLE service_reply(id TEXT,state TEXT,started REAL,receipt TEXT,proof TEXT)')
  self.db.execute("INSERT INTO service_reply VALUES('reply-1','ready',NULL,NULL,NULL)")
 def clock(self):return 1000
 @contextmanager
 def tx(self):yield
 def close(self):self.db.close()


class Replies:
 def __init__(self,store,reply):self.store=store;self.reply=reply
 def get(self,_id):
  row=self.store.db.execute("SELECT * FROM service_reply WHERE id='reply-1'").fetchone()
  return self.reply|dict(row)
 def begin(self,_id):raise CycleError('reply_context_changed')
 def confirm(self,_id,_proof):self.store.db.execute("UPDATE service_reply SET state='confirmed' WHERE id='reply-1'")
 def unknown(self,_id):self.store.db.execute("UPDATE service_reply SET state='unknown' WHERE id='reply-1'")


class MarketAgentReplyTests(unittest.TestCase):
 def setUp(self):
  self.store=Store()
  self.reply={'id':'reply-1','kind':'agent_generated_v2','state':'ready','plan_id':'plan-1',
              'cid':'123','oec':'456','request_ref':'request-1','text':'Ciao'}
  self.replies=Replies(self.store,self.reply)
 def tearDown(self):self.store.close()

 def test_stale_ready_reply_is_settled_without_a_platform_write(self):
  conversation=SimpleNamespace(conversation_id='123')
  session=SimpleNamespace(conversation=lambda *_:conversation,
                          history_summary=lambda *_args,**_kwargs:{'contents':[]})
  def send(_conversation,_text,_request,*,before_dispatch):
   before_dispatch({'market':'br','oecId':'456','conversationId':'123',
                    'componentKind':'text','requestRef':'request-1',
                    'textSha256':hashlib.sha256(b'Ciao').hexdigest()})
   self.fail('dispatch must not continue after changed context')
  runtime={'session':session,'adapter':SimpleNamespace(send_once=send),'auth':object()}
  @contextmanager
  def authenticated(*_args,**_kwargs):yield runtime
  @contextmanager
  def gate(*_args,**_kwargs):yield lambda:None
  with patch.object(M,'authenticated',authenticated),patch.object(M,'write_gate',gate), \
       patch.object(M,'agent_setting',return_value={'enabled':True,'replyStart':'00:00','replyEnd':'24:00'}), \
       patch.object(M,'Inbox') as inbox,patch.object(M,'Service') as service:
   result=M.run_reply(ROOT,self.store,self.replies,self.reply,'br',authorized_now=True)
  self.assertEqual(result['state'],'cancelled')
  self.assertEqual(result['platformWrites'],0)
  self.assertEqual(self.replies.get('reply-1')['state'],'cancelled')

 def test_wrapped_stale_ready_refusal_is_also_cancelled(self):
  from lib.italy_im_delivery import ItalyImDeliveryError
  original=self.replies.begin
  def wrapped(*args):
   try:original(*args)
   except CycleError:raise ItalyImDeliveryError('it_delivery_dispatch_not_allowed')
  self.replies.begin=wrapped
  self.test_stale_ready_reply_is_settled_without_a_platform_write()

 def test_unknown_reply_readback_uses_read_only_auth_and_no_write_gate(self):
  self.store.db.execute("UPDATE service_reply SET state='unknown' WHERE id='reply-1'")
  self.reply['state']='unknown'
  options={};conversation=SimpleNamespace(conversation_id='123')
  session=SimpleNamespace(conversation=lambda *_:conversation)
  adapter=SimpleNamespace(readback=lambda *_args,**_kwargs:{'status':'confirmed'})
  @contextmanager
  def authenticated(*_args,**kwargs):
   options.update(kwargs)
   yield {'session':session,'adapter':None,'auth':object()}
  with patch.object(M,'authenticated',authenticated), \
       patch.object(M,'write_gate',side_effect=AssertionError('write_gate_forbidden')), \
       patch('lib.italy_im_delivery.ItalyImDeliveryAdapter',return_value=adapter):
   result=M.run_reply(ROOT,self.store,self.replies,self.reply,'br')
  self.assertTrue(options['read_only'])
  self.assertEqual(result['state'],'confirmed')
  self.assertEqual((result['platformWrites'],result['realSends']),(0,0))


if __name__=='__main__':unittest.main()
