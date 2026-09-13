import json,sqlite3,sys,tempfile,time,unittest
from pathlib import Path
from contextlib import contextmanager,closing,ExitStack
from dataclasses import asdict
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from lib import cycle_burst as M
from lib.second_cycle import CycleStore,encoded
from lib.cycle_delivery import Deliveries
from test_second_cycle import offer,edge
from test_second_live_runtime import card

class BudgetTests(unittest.TestCase):
 def test_aggregate_budget_has_no_idle_burst(self):
  now=[100.];b=M.RequestBudget(3,clock=lambda:now[0],sleep=lambda t:now.__setitem__(0,now[0]+t))
  b.acquire();b.acquire();self.assertAlmostEqual(now[0],100+1/3)
  now[0]+=10;b.acquire();b.acquire();self.assertAlmostEqual(now[0],110+2/3)
 def test_different_product_reads_can_overlap(self):
  import threading
  from concurrent.futures import ThreadPoolExecutor
  cache=M.CardCache();barrier=threading.Barrier(2)
  def work(pid):
   return cache.get({'offer':{'pid':pid},'card':{'listId':pid}},lambda:(barrier.wait(timeout=2),pid)[1])
  with ThreadPoolExecutor(max_workers=2) as pool:self.assertEqual(list(pool.map(work,['1','2'])),['1','2'])
 def test_card_cache_expires_and_does_not_reuse_on_failed_refresh(self):
  now=[0];cache=M.CardCache(clock=lambda:now[0]);c={'offer':{'pid':'1'},'card':{'listId':'2'}};calls=[]
  def load():calls.append(1);return 'proof'
  self.assertEqual(cache.get(c,load),'proof');now[0]=9;cache.get(c,load);self.assertEqual(len(calls),1)
  now[0]=10
  def failed():raise ValueError('failed')
  with self.assertRaises(ValueError):cache.get(c,failed)
  cache.get(c,load);self.assertEqual(len(calls),2)
  cache.get(c|{'offer':{'pid':'1','creatorPercent':'13'}},load);self.assertEqual(len(calls),3)

class CohortTests(unittest.TestCase):
 @contextmanager
 def fixture(self,*,fail_card=False,pause=False):
  with tempfile.TemporaryDirectory() as tmp,ExitStack() as patches:
   root=Path(tmp);var=root/'var';(var/'cycle-send').mkdir(parents=True)
   db=var/'second-cycle.sqlite';now=time.time();o=offer(endAt=now+60*86400)
   with CycleStore(db) as s:
    plan=s.plan('bjn-local-research','it');s.publish(plan,'s',now,[o]);s.import_edges(plan,[edge(),edge(person='c2',source='e2')]);Deliveries(s)
    s.db.executescript('CREATE TABLE cycle_bulk(id TEXT PRIMARY KEY,plan_id TEXT,target INTEGER,authorization TEXT,state TEXT,created REAL); CREATE TABLE cycle_bulk_item(batch_id TEXT,creator_id TEXT,handle TEXT,state TEXT,delivery_id TEXT,reason TEXT,retry_at REAL DEFAULT 0,retry_count INTEGER DEFAULT 0,PRIMARY KEY(batch_id,creator_id)); CREATE TABLE cycle_bulk_timing(id INTEGER PRIMARY KEY,batch_id TEXT,stage TEXT,seconds REAL,exit_code INTEGER,at REAL);')
    s.db.execute("INSERT INTO cycle_bulk VALUES('batch',?,2,?,'running',?)",(plan,encoded({'source':'current_user_request','maxPeople':2}),now))
    for c in ('c1','c2'):s.db.execute("INSERT INTO cycle_bulk_item(batch_id,creator_id,handle,state) VALUES('batch',?,?,'pending')",(c,c))
   with closing(sqlite3.connect(var/'creator-identities.sqlite')) as ids:ids.execute('CREATE TABLE empty(id)')
   with closing(sqlite3.connect(var/'it-conversations.sqlite')) as idx,idx:
    idx.execute('CREATE TABLE conversation(scope,cid,oec,kind,seen)')
    idx.executemany("INSERT INTO conversation VALUES('it:acc6',?,?,2,?)",[('88','123',now),('99','456',now)])
   import threading
   sender_lock=threading.Lock()
   verified=card();proof={'listId':verified.list_id};calls=[];auths=[]
   cs=[{'creatorId':c,'oecId':oec,'handle':c,'pid':'1','source':{'sourceId':src},'offer':o,'planRevision':1,'controlRevision':1,'name':{},'card':proof} for c,oec,src in [('c1','123','e1'),('c2','456','e2')]]
   @contextmanager
   def authenticated(report,**kw):report['sendCapability']='canary';auths.append(1);yield (None,None,None,None,lambda:False,lambda:None)
   @contextmanager
   def runtime(binding,report,**kw):
    reads=SimpleNamespace(conversation=lambda cid,oec,**kw:SimpleNamespace(conversation_id=cid,oec_id=oec),history_summary=lambda *a,**k:{'identityVerified':True,'hasMore':False,'senderCounts':{'ourMessages':0},'outboundCreateTimeRaw':[],'contents':[]})
    class Adapter:
     def send(self,conv,ref,before,kind):
      before({'oecId':conv.oec_id,'conversationId':conv.conversation_id,'market':'it','account':'acc6','requestRef':ref,'componentKind':kind,'stage':'send_message','productId':verified.product_id,'listId':verified.list_id,'bindingSha256':verified.binding_sha256})
      # The unmodified global outbox gate guarantees no other unverified message.
      with CycleStore(db) as s:
       assert s.db.execute("SELECT count(*) FROM cycle_delivery_part WHERE state IN ('inflight','accepted','unknown')").fetchone()[0]<=2
      calls.append((conv.oec_id,kind))
      if fail_card and kind=='card':raise RuntimeError('network_lost')
      return {'requestRef':ref}
     def send_card_once(self,conv,card,ref,before_dispatch):return self.send(conv,ref,before_dispatch,'card')
     def send_once(self,conv,text,ref,before_dispatch):return self.send(conv,ref,before_dispatch,'text')
     def readback_card(self,*a,**kw):
      if pause:
       with CycleStore(db) as s:s.control(plan,'pause',1,'paused')
      return {'status':'confirmed','messageId':'1','evidenceRef':'check-card'}
     def readback(self,*a,**kw):return {'status':'confirmed','messageId':'2','evidenceRef':'check-text'}
    @contextmanager
    def gate():
     with sender_lock:yield lambda:None
    yield {'reads':reads,'adapter':Adapter(),'write_gate':gate,'validate_card':lambda c:kw['card_validator'](c)}
   patches.enter_context(patch.object(M,'ROOT',root));patches.enter_context(patch.object(M,'_authenticated',authenticated));patches.enter_context(patch.object(M,'sender_binding_sha256',lambda a:'a'*64));patches.enter_context(patch.object(M,'live_runtime',runtime));patches.enter_context(patch.object(M,'fresh_card',lambda *a,**kw:(verified,proof)));patches.enter_context(patch.object(M,'choose_candidates',lambda *a:(cs,[])));patches.enter_context(patch.object(M,'render',lambda *a:{'version':4,'deliveryOrder':'card_then_text','textIt':'Hello'}));patches.enter_context(patch.object(M,'Inbox'));patches.enter_context(patch.object(M,'Service'));patches.enter_context(patch('socket.socket',side_effect=AssertionError('offline tests only')))
   yield db,plan,calls,auths
 def test_two_recipient_cohort_one_auth_no_duplicate_on_restart(self):
  with self.fixture() as (db,p,calls,auths):
   result=M.run_cohort('batch',lanes=2);self.assertEqual(len(auths),1);self.assertEqual(len(calls),4);self.assertTrue(all(r['state']=='confirmed' for r in result['items']))
   M.run_cohort('batch',lanes=2);self.assertEqual(len(calls),4);self.assertEqual(len(auths),1)
 def test_unknown_halts_other_recipient_and_restart_does_not_send(self):
  with self.fixture(fail_card=True) as (db,p,calls,auths):
   M.run_cohort('batch',lanes=2);self.assertEqual(len(calls),1)
   with CycleStore(db) as s:self.assertEqual(s.db.execute('SELECT state FROM cycle_bulk').fetchone()[0],'waiting_reconciliation')
   with self.assertRaisesRegex(M.CycleError,'bulk_not_running'):M.run_cohort('batch',lanes=2)
   self.assertEqual(len(calls),1)
 def test_pause_between_components_blocks_further_dispatches(self):
  with self.fixture(pause=True) as (db,p,calls,auths):
   M.run_cohort('batch',lanes=2);self.assertTrue(1<=len(calls)<=2);self.assertTrue(all(kind=='card' for _,kind in calls))
   sent=len(calls);M.run_cohort('batch',lanes=2);self.assertEqual(len(calls),sent)

if __name__=='__main__':unittest.main()
