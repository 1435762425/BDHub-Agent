import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from lib.cycle_delivery import Deliveries
from lib.delivery_reconciliation import reconcile, serialized, _scan
from lib.outreach_policy import isolated_creators, marketing_isolated
from lib.second_cycle import CycleStore,CycleError,digest
from test_second_cycle import offer,edge,NOW


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.now=NOW
        self.store=CycleStore(self.root/'db',lambda:self.now);self.plan=self.store.plan('test','it')
        self.store.publish(self.plan,'s',NOW,[offer()]);self.store.import_edges(self.plan,[edge()])
        r=self.store.db.execute('SELECT * FROM relationship').fetchone()
        self.c={'creatorId':r['creator_id'],'oecId':r['oec'],'pid':offer()['pid'],
            'source':{'sourceId':edge()['sourceId']},'offer':offer(),'planRevision':1,'controlRevision':1,
            'message':{'version':4,'deliveryOrder':'card_then_text','textIt':'Hello'}}
        self.d=Deliveries(self.store);self.did=self.d.prepare(self.plan,self.c)['id']

    def tearDown(self):
        self.store.close();self.tmp.cleanup()

    def begin(self,kind):
        self.d.begin(self.did,kind,authorized_snapshot_hash=digest(self.c),recipient_verified=True,allowance_verified=True)

    def proof(self,kind):
        p=next(p for p in self.d.get(self.did)['parts'] if p['kind']==kind)
        return {'status':'confirmed','kind':kind,'requestRef':p['request_ref'],'oecId':self.c['oecId'],
                'messageId':'123','evidenceRef':'original-readback'}

    def check(self,reader):
        return reconcile(self.root,'it',self.d,self.did,reader=reader)

    def test_conversation_scan_budget_survives_restart_and_keeps_unsent_parts(self):
        self.d.prepare_conversation(self.did);self.d.begin_conversation(self.did,digest(self.c))
        before=self.d.conversation_intent(self.did).copy()
        read=Mock(return_value={'status':'result_unknown','scanComplete':True,'matchingConversations':['991']})
        self.assertEqual(self.check(read)['state'],'waiting_reconciliation')
        self.assertEqual(self.check(read)['state'],'waiting_reconciliation');self.assertEqual(read.call_count,1)
        self.now+=300;self.d=Deliveries(self.store)
        self.assertEqual(self.check(read)['state'],'quarantined_unknown');self.assertEqual(read.call_count,2)
        self.assertEqual(self.d.conversation_intent(self.did),before)
        self.assertEqual([p['state'] for p in self.d.get(self.did)['parts']],['ready','ready'])
        self.assertEqual(self.check(read)['state'],'quarantined_unknown');self.assertEqual(read.call_count,2)
        self.assertEqual(isolated_creators(self.store.db,self.plan),{self.c['creatorId']})
        self.assertEqual(self.store.db.execute('SELECT mode FROM relationship').fetchone()[0],'auto')
        self.assertFalse(self.store.db.execute("SELECT 1 FROM sqlite_master WHERE name='service_case'").fetchone())
        self.assertEqual(self.d.get(self.did)['snapshot'],self.c)

    def test_failed_reads_are_never_absence_but_budget_ends(self):
        self.begin('card');self.d.unknown(self.did,'card')
        read=Mock(side_effect=RuntimeError('secret transport info'))
        for state in ['waiting_reconciliation','waiting_reconciliation','quarantined_unknown']:
            self.assertEqual(self.check(read)['state'],state);self.now+=300
        checks=[json.loads(r[0]) for r in self.store.db.execute('SELECT payload FROM cycle_delivery_check')]
        self.assertFalse(any(c.get('reason')=='it_delivery_history_not_found' for c in checks))
        self.assertNotIn('secret',json.dumps(checks))
        self.assertEqual(self.d.get(self.did)['parts'][0]['state'],'unknown')

    def test_successful_absence_retains_two_reads_300_seconds_rule(self):
        self.begin('card');self.d.unknown(self.did,'card')
        read=Mock(return_value={'status':'result_unknown','reason':'it_delivery_history_not_found','messageId':None})
        self.assertEqual(self.check(read)['state'],'waiting_reconciliation');self.now+=299
        self.assertEqual(self.check(read)['state'],'waiting_reconciliation');self.assertEqual(read.call_count,1)
        self.now+=1;self.assertEqual(self.check(read)['state'],'quarantined_unknown')

    def test_text_unknown_preserves_card_and_real_human_case(self):
        from lib.cycle_service import Service
        Service(self.store)
        self.begin('card');self.d.confirm(self.did,'card',self.proof('card'));self.begin('text');self.d.unknown(self.did,'text')
        self.store.db.execute("INSERT INTO service_case VALUES('case-real',?,?,'open',0,'fee_request',?,?,'not_sent')",
                             (self.plan,self.c['creatorId'],self.now,self.now))
        read=Mock(return_value={'status':'result_unknown'})
        for _ in range(3):self.check(read);self.now+=300
        self.assertEqual([p['state'] for p in self.d.get(self.did)['parts']],['confirmed','unknown'])
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM service_case').fetchone()[0],1)
        self.assertEqual(self.store.db.execute('SELECT state FROM service_case').fetchone()[0],'open')
        self.d.confirm(self.did,'text',self.proof('text'));self.d.confirm(self.did,'text',self.proof('text'))
        self.assertEqual(self.d.get(self.did)['state'],'quarantined_unknown')
        self.now+=4*86400
        with self.assertRaisesRegex(CycleError,'marketing_isolated'):
            self.d._eligible(self.plan,self.c)

    def test_late_card_receipt_never_reopens_unsent_text(self):
        self.begin('card');self.d.unknown(self.did,'card');self.d.isolate_technical(self.did,'card','verification_budget_exhausted')
        self.d.confirm(self.did,'card',self.proof('card'))
        self.assertEqual(self.d.get(self.did)['state'],'quarantined_unknown')
        self.assertEqual(self.d.get(self.did)['parts'][1]['state'],'ready')
        with self.assertRaisesRegex(CycleError,'marketing_isolated'):self.begin('text')

    def test_verified_card_only_closes_original_component(self):
        self.begin('card');self.d.unknown(self.did,'card')
        read=Mock(return_value=self.proof('card'))
        self.assertEqual(self.check(read)['state'],'partial_delivery')
        self.assertEqual([p['state'] for p in self.d.get(self.did)['parts']],['confirmed','cancelled'])
        self.assertEqual(read.call_count,1)

    def test_persisted_attempt_before_crash_consumes_budget_and_wall_time(self):
        self.begin('card');self.d.unknown(self.did,'card')
        self.d.record_check(self.did,'verification_attempt_card',{'attempt':1,'deadline':self.now+900})
        self.now+=901;read=Mock()
        self.assertEqual(self.check(read)['state'],'quarantined_unknown');read.assert_not_called()

    def test_same_market_new_plan_and_pid_cannot_bypass_isolation(self):
        self.begin('card');self.d.unknown(self.did,'card');self.d.isolate_technical(self.did,'card','exhausted')
        other=self.store.plan('other','it');different=self.store.plan('other','uk')
        self.assertTrue(marketing_isolated(self.store.db,other,'different-creator',self.c['oecId']))
        self.assertFalse(marketing_isolated(self.store.db,different,self.c['creatorId'],self.c['oecId']))
        # Another OEC stays eligible; technical isolation does not alter plan or relationship controls.
        self.assertFalse(marketing_isolated(self.store.db,self.plan,'other','999'))

    def test_executor_lock_blocks_reconciliation_until_owner_returns(self):
        @serialized('it')
        def nested(root):raise AssertionError('overlapped active executor')
        @serialized('it')
        def owner(root):
            with self.assertRaisesRegex(CycleError,'delivery_executor_busy'):nested(root)
        owner(self.root)
        @serialized('it')
        def later(root):return 'released'
        self.assertEqual(later(self.root),'released')

    def test_scan_is_bounded_and_never_turns_match_into_receipt(self):
        session=Mock();session.initialize.return_value={'conversations':[{'conversationId':'88','oecId':self.c['oecId']}],
            'hasMore':False,'nextCursor':'1'}
        proof=_scan(session,self.c['oecId'])
        self.assertEqual(proof['status'],'result_unknown');self.assertEqual(proof['matchingConversations'],['88'])
        session.initialize.return_value={'conversations':[],'hasMore':True,'nextCursor':'1'}
        self.assertFalse(_scan(session,self.c['oecId'])['scanComplete'])

class OriginalReadTests(unittest.TestCase):
    def test_changed_account_never_reads_another_account(self):
        from unittest.mock import patch
        from lib.delivery_reconciliation import read_original
        with patch('lib.market_accounts.load_config',return_value={'markets':{'uk':{'roles':{'communications':'replacement'}}}}), \
             patch('lib.market_im_runtime.authenticated',side_effect=AssertionError('wrong account read')):
            with self.assertRaisesRegex(CycleError,'reconciliation_original_account_changed'):
                read_original(Path('/unused'),'uk',{'snapshot':{}},'conversation',{})

    def test_all_market_conversation_scans_are_read_only_and_never_create(self):
        from contextlib import contextmanager
        from types import SimpleNamespace
        from unittest.mock import patch
        from lib.delivery_reconciliation import read_original,LEGACY_ACCOUNTS
        for market,account in LEGACY_ACCOUNTS.items():
            with self.subTest(market=market):
                session=Mock();session.initialize.return_value={'conversations':[], 'hasMore':False,'nextCursor':'0'}
                @contextmanager
                def auth(root,m,report,**kwargs):
                    self.assertEqual(m,market);self.assertTrue(kwargs['read_only'])
                    yield {'auth':SimpleNamespace(account_name=account,im_id='99'),'session':session}
                with patch('lib.market_accounts.load_config',return_value={'markets':{market:{'roles':{'communications':account}}}}), \
                     patch('lib.market_im_runtime.authenticated',side_effect=auth), \
                     patch('lib.second_live_runtime.sender_binding_sha256',return_value='frozen'):
                    proof=read_original(Path('/unused'),market,{'snapshot':{'oecId':'1','senderBindingHash':'frozen'}},
                                        'conversation',{'state':'inflight','cid':None})
                self.assertEqual(proof['status'],'result_unknown');session.create_once.assert_not_called()
