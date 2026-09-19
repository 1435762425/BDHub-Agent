import json,sqlite3,sys,tempfile,time,unittest
from pathlib import Path
from contextlib import contextmanager,closing,ExitStack
from dataclasses import asdict
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from lib import cycle_burst as M
from lib.second_cycle import CycleStore,digest,encoded
from lib.cycle_delivery import Deliveries
from lib.catalog_binding import CatalogBindings,offer_fingerprint
from lib.schema_migrations import apply_database
from lib.italy_im_delivery import ItalyImDeliveryError
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
   patches.enter_context(patch.object(M,'ROOT',root));patches.enter_context(patch.object(M,'_authenticated',authenticated));patches.enter_context(patch.object(M,'sender_binding_sha256',lambda a:'a'*64));patches.enter_context(patch.object(M,'live_runtime',runtime));patches.enter_context(patch.object(M,'Inbox'));patches.enter_context(patch.object(M,'Service'));patches.enter_context(patch('socket.socket',side_effect=AssertionError('offline tests only')))
   yield db,plan,calls,auths
 def test_two_recipient_cohort_one_auth_no_duplicate_on_restart(self):
  with self.fixture() as (db,p,calls,auths):
   with self.assertRaisesRegex(M.CycleError,'frozen_batch_required'):M.run_cohort('batch',lanes=2)
   self.assertEqual((calls,auths),([],[]))
 def test_unknown_halts_other_recipient_and_restart_does_not_send(self):
  with self.fixture(fail_card=True) as (db,p,calls,auths):
   with self.assertRaisesRegex(M.CycleError,'frozen_batch_required'):M.run_cohort('batch',lanes=2)
   self.assertEqual((calls,auths),([],[]))
 def test_pause_between_components_blocks_further_dispatches(self):
  with self.fixture(pause=True) as (db,p,calls,auths):
   with self.assertRaisesRegex(M.CycleError,'frozen_batch_required'):M.run_cohort('batch',lanes=2)
   self.assertEqual((calls,auths),([],[]))


class FrozenCohortTests(unittest.TestCase):
 @contextmanager
 def fixture(self,*,reject_first=False,unknown_first=False):
  with tempfile.TemporaryDirectory() as tmp,ExitStack() as patches:
   root=Path(tmp);var=root/'var';(var/'cycle-send').mkdir(parents=True)
   db=var/'second-cycle.sqlite';now=time.time()
   pids=['1729571380453480001','1729571380453480002']
   offers=[];edges=[]
   for index,pid in enumerate(pids):
    offers.append(offer(pid,endAt=now+60*86400,catalogSource='selected',campaignId='0',
      totalPercent='15',managementType='full_managed',managementEvidenceRef='fixture',stock=None))
    edges.append(edge(pid,person=f'c{index+1}',source=f'e{index+1}'))
   with CycleStore(db) as s:
    plan=s.plan('bjn-local-research','it');s.publish(plan,'s',now,offers);s.import_edges(plan,edges);Deliveries(s)
   with closing(sqlite3.connect(var/'catalog-links.sqlite')) as links:links.execute('CREATE TABLE seed(id)')
   apply_database(root,'second-cycle');apply_database(root,'catalog-links')
   raw_cards=[]
   bindings=CatalogBindings(root)
   try:
    for index,o in enumerate(offers):
     card_payload={'state':'verified_read_only','pid':o['pid'],'sourceCampaignId':'0',
      'wireCampaignId':'0','listId':f'86507651826159849{index+10}','listName':f'fixture {index}',
      'verifiedListName':f'fixture {index}','campaignName':'','creatorPercent':'12','publicPercent':'10',
      'checkedAt':now,'evidenceRefs':[f'proof-{index}']}
     raw_cards.append(card_payload)
     bindings.promote({'market':'it','route':'selected','pid':o['pid'],'campaignId':'0',
      'creatorPercent':'12','listName':card_payload['listName'],'policyVersion':'commission-1-to-2-v1',
      'namingVersion':'link-naming-v1','offer':o},card_payload,now=now)
   finally:bindings.close()
   candidates=[]
   for index,(o,e,card_payload) in enumerate(zip(offers,edges,raw_cards)):
    candidates.append({'planRevision':1,'creatorId':e['creatorId'],'oecId':e['oec'],
     'handle':e['creatorId'],'identityEvidence':'identity','controlRevision':1,'pid':o['pid'],
     'offer':o,'offerFingerprint':digest(o),'materialKey':'material','name':{'mentionIt':'prodotto'},
     'nameSource':'缓存','card':card_payload,'source':e,'relationshipUnlocked':False,
     'message':{'version':4,'deliveryOrder':'card_then_text','textIt':'Hello'}})
   auth={'source':'current_user_request','scope':'pool_to_send','maxPeople':2,'requestedPeople':2,
    'widenLocalGate':False,'sendWindow':None,'institutionNewContactRollingCap':500,
    'materialPolicy':'frozen-current-binding-v1','note':'fixture'}
   with CycleStore(db) as s:
    s.db.execute("INSERT INTO cycle_bulk VALUES('frozen',?,2,?,'running',?)",(plan,encoded(auth),now))
    s.db.execute("INSERT INTO cycle_bulk_freeze(batch_id,request_id,preview_hash,config_json,authorization_json,revision,state,authorized_at,created_at) VALUES('frozen','request-1',?, ?, ?,2,'running',?,?)",
     ('a'*64,encoded({'count':2}),encoded(auth),now,now))
    for index,c in enumerate(candidates):
     s.db.execute("INSERT INTO cycle_bulk_item(batch_id,creator_id,handle,state) VALUES('frozen',?,?,'pending')",(c['creatorId'],c['handle']))
     s.db.execute('INSERT INTO cycle_bulk_candidate VALUES(?,?,?,?,?,?,?,?,?,?,?)',
      ('frozen',index,c['creatorId'],c['oecId'],c['pid'],c['source']['sourceId'],c['offer']['offerKey'],
       offer_fingerprint(c['offer']),c['card']['listId'],encoded(c),digest(c)))
   with closing(sqlite3.connect(var/'creator-identities.sqlite')) as ids:ids.execute('CREATE TABLE empty(id)')
   with closing(sqlite3.connect(var/'it-conversations.sqlite')) as idx,idx:
    idx.execute('CREATE TABLE conversation(scope,cid,oec,kind,seen)')
    idx.executemany("INSERT INTO conversation VALUES('it:acc6',?,?,2,?)",[('88','123',now),('99','456',now)])
   import threading
   sender_lock=threading.Lock();calls=[];auths=[]
   @contextmanager
   def authenticated(report,**kw):report['sendCapability']='canary';auths.append(1);yield (None,None,None,None,lambda:False,lambda:None)
   @contextmanager
   def runtime(binding,report,**kw):
    reads=SimpleNamespace(conversation=lambda cid,oec,**kw:SimpleNamespace(conversation_id=cid,oec_id=oec),history_summary=lambda *a,**k:{'identityVerified':True,'hasMore':False,'senderCounts':{'ourMessages':0},'outboundCreateTimeRaw':[],'contents':[]})
    class Adapter:
     def send(self,conv,ref,before,kind,sent_card=None):
      scope={'oecId':conv.oec_id,'conversationId':conv.conversation_id,'market':'it','account':'acc6','requestRef':ref,'componentKind':kind,'stage':'send_message'}
      if sent_card is not None:scope.update(productId=sent_card.product_id,listId=sent_card.list_id,bindingSha256=sent_card.binding_sha256)
      before(scope);calls.append((conv.oec_id,kind))
      if unknown_first and conv.oec_id=='123' and kind=='card':raise RuntimeError('network_lost')
      if reject_first and conv.oec_id=='123' and kind=='card':
       raise ItalyImDeliveryError('it_delivery_send_rejected',outcome='rejected',native_status=1,
        check_code=0,response_ref='im-response:'+'b'*64)
      return {'requestRef':ref}
     def send_card_once(self,conv,sent_card,ref,before_dispatch):return self.send(conv,ref,before_dispatch,'card',sent_card)
     def send_once(self,conv,text,ref,before_dispatch):return self.send(conv,ref,before_dispatch,'text')
     def readback_card(self,*a,**kw):return {'status':'confirmed','messageId':'1','evidenceRef':'check-card'}
     def readback(self,*a,**kw):return {'status':'confirmed','messageId':'2','evidenceRef':'check-text'}
    @contextmanager
    def gate():
     with sender_lock:yield lambda:None
    yield {'reads':reads,'adapter':Adapter(),'write_gate':gate,'validate_card':lambda c:kw['card_validator'](c)}
   patches.enter_context(patch.object(M,'ROOT',root));patches.enter_context(patch.object(M,'_authenticated',authenticated));patches.enter_context(patch.object(M,'sender_binding_sha256',lambda a:'a'*64));patches.enter_context(patch.object(M,'live_runtime',runtime));patches.enter_context(patch.object(M,'Inbox'));patches.enter_context(patch.object(M,'Service'));patches.enter_context(patch('socket.socket',side_effect=AssertionError('offline tests only')))
   yield root,db,plan,calls,auths

 def test_frozen_batch_uses_only_snapshot_and_local_binding(self):
  with self.fixture() as (root,db,plan,calls,auths):
   result=M.run_cohort('frozen',lanes=2)
   self.assertEqual(len(auths),1);self.assertEqual(len(calls),4)
   self.assertTrue(all(item['state']=='confirmed' for item in result['items']))

 def test_changed_batch_authorization_is_rejected_before_authentication(self):
  with self.fixture() as (root,db,plan,calls,auths):
   with CycleStore(db) as s:
    authorization=json.loads(s.db.execute("SELECT authorization FROM cycle_bulk WHERE id='frozen'").fetchone()[0])
    authorization['note']='tampered-after-freeze'
    s.db.execute("UPDATE cycle_bulk SET authorization=? WHERE id='frozen'",(encoded(authorization),))
   with self.assertRaisesRegex(M.CycleError,'frozen_batch_required'):M.run_cohort('frozen',lanes=2)
   self.assertEqual((calls,auths),([],[]))

 def test_changed_binding_marks_items_stale_without_platform_calls(self):
  with self.fixture() as (root,db,plan,calls,auths):
   with closing(sqlite3.connect(root/'var/catalog-links.sqlite')) as links,links:
    links.execute("UPDATE catalog_current_binding SET list_id='999'")
   result=M.run_cohort('frozen',lanes=2)
   self.assertEqual(calls,[])
   self.assertTrue(all(item['state']=='material_stale' for item in result['items']))

 def test_one_explicit_card_rejection_does_not_stop_other_pid(self):
  with self.fixture(reject_first=True) as (root,db,plan,calls,auths):
   result=M.run_cohort('frozen',lanes=2)
   states={item['creatorId']:item['state'] for item in result['items']}
   self.assertEqual(states,{'c1':'material_stale','c2':'confirmed'})
   with CycleStore(db) as s:self.assertEqual(s.db.execute('SELECT state FROM cycle_bulk').fetchone()[0],'running')

 def test_unknown_in_frozen_batch_halts_all_lanes(self):
  with self.fixture(unknown_first=True) as (root,db,plan,calls,auths):
   M.run_cohort('frozen',lanes=2)
   self.assertIn(('123','card'),calls);self.assertNotIn(('123','text'),calls)
   sent=len(calls)
   with CycleStore(db) as s:
    self.assertEqual(s.db.execute('SELECT state FROM cycle_bulk').fetchone()[0],'waiting_reconciliation')
    self.assertEqual(s.db.execute('SELECT state FROM cycle_bulk_freeze').fetchone()[0],'waiting_reconciliation')
   with self.assertRaisesRegex(M.CycleError,'bulk_not_running'):M.run_cohort('frozen',lanes=2)
   self.assertEqual(len(calls),sent)

class LegacyRejectionTests(unittest.TestCase):
 def test_cycle_burst_rejects_a_running_bulk_without_freeze_before_authentication(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);(root/'var').mkdir();db=root/'var/second-cycle.sqlite'
   with CycleStore(db) as store:
    plan=store.plan('bjn-local-research','it')
    store.db.executescript('CREATE TABLE cycle_bulk(id TEXT PRIMARY KEY,plan_id TEXT,target INTEGER,authorization TEXT,state TEXT,created REAL);CREATE TABLE cycle_bulk_item(batch_id TEXT,creator_id TEXT,handle TEXT,state TEXT,delivery_id TEXT,reason TEXT,retry_at REAL DEFAULT 0,retry_count INTEGER DEFAULT 0,PRIMARY KEY(batch_id,creator_id));')
    auth={'source':'current_user_request','maxPeople':1};store.db.execute("INSERT INTO cycle_bulk VALUES('legacy',?,1,?,'running',1)",(plan,encoded(auth)));store.db.execute("INSERT INTO cycle_bulk_item VALUES('legacy','c1','h','pending',NULL,NULL,0,0)")
   with patch.object(M,'ROOT',root),patch.object(M,'_authenticated',side_effect=AssertionError('must reject before auth')):
    with self.assertRaisesRegex(M.CycleError,'frozen_batch_required'):M.run_cohort('legacy')

if __name__=='__main__':unittest.main()
