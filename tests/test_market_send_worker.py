import importlib.util
import fcntl
import io
import json
import os
import sqlite3
import tempfile
import threading
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch

PATH=Path(__file__).resolve().parents[1]/'scripts/market-send-worker.py'
SPEC=importlib.util.spec_from_file_location('market_send_worker_cli',PATH)
MODULE=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(MODULE)
from lib import market_send_worker as lifecycle


class MarketSendWorkerTests(unittest.TestCase):
    def worker_tick(self,*,recovering,stopped=False,window_open=False,error=None,once=True,plan_active=True):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        root=Path(temporary.name);(root/'var').mkdir()
        store=Mock();context=Mock();context.__enter__=Mock(return_value=store);context.__exit__=Mock(return_value=False)
        store.db.execute.return_value.fetchone.return_value={'id':'plan-br','state':'active' if plan_active else 'paused'}
        send={'stopRequested':stopped,'automaticEnabled':not stopped,'runRequested':not stopped,
              'window':['16:30','24:00']}
        with ExitStack() as stack:
            values={
                'ROOT':root,'STOP':False,'CycleStore':Mock(return_value=context),
                'market_path_keys':Mock(return_value=['br']),
                'setting':Mock(return_value={'continuousSendEnabled':not stopped}),
                'window_state':Mock(return_value={'open':window_open}),
                'has_pending_reconciliation':Mock(side_effect=recovering if isinstance(recovering,list) else None,
                                                   return_value=recovering if not isinstance(recovering,list) else False),
                'run':Mock(side_effect=error,return_value={'state':'waiting_reconciliation','platformWrites':0,'realSends':0}),
                'write':Mock(),'inbox_waiting':Mock(return_value=False),
            }
            for name,value in values.items():stack.enter_context(patch.object(MODULE,name,value))
            stack.enter_context(patch('sys.argv',['market-send-worker.py','--market','br']+(['--once'] if once else [])))
            stack.enter_context(patch('lib.market_send_control.control',return_value=send))
            stack.enter_context(patch('lib.market_send_control._control_for_plan',return_value=send))
            stack.enter_context(patch('lib.market_send_control.settle_confirmed_canaries',return_value=[]))
            stack.enter_context(patch('lib.market_accounts.load_config',return_value={'markets':{'br':{'roles':{'communications':'acc1'}}}}))
            stack.enter_context(patch('lib.account_identity.current_generation',return_value={'capabilities':{'message_send':{'state':'verified'}}}))
            stack.enter_context(patch('lib.runtime_release.register'))
            stack.enter_context(patch('sys.stderr',new=io.StringIO()))
            if not once:
                # Advance through only local backoff; no real waiting or platform calls.
                stack.enter_context(patch.object(MODULE.time,'monotonic',side_effect=range(0,100000,1000)))
            result=MODULE.main()
        return result,values

    def test_pending_submission_is_read_only_even_after_stop_outside_window(self):
        result,values=self.worker_tick(recovering=True,stopped=True)
        self.assertEqual(result,0)
        self.assertTrue(values['run'].call_args.kwargs['reconcile_only'])
        self.assertTrue(values['run'].call_args.kwargs['page_control'])

    def test_paused_plan_still_only_verifies_its_submitted_intent(self):
        result,values=self.worker_tick(recovering=True,plan_active=False)
        self.assertEqual(result,0)
        self.assertTrue(values['run'].call_args.kwargs['reconcile_only'])
        result,values=self.worker_tick(recovering=False,plan_active=False,window_open=True)
        self.assertEqual(result,0);values['run'].assert_not_called()

    def test_stopped_sender_without_a_submitted_intent_never_runs(self):
        result,values=self.worker_tick(recovering=False,stopped=True)
        self.assertEqual(result,0);values['run'].assert_not_called()

    def test_window_still_blocks_a_fresh_send(self):
        result,values=self.worker_tick(recovering=False)
        self.assertEqual(result,0);values['run'].assert_not_called()
        self.assertEqual(values['write'].call_args.args[1]['state'],'waiting_window')

    def test_existing_authorization_inside_window_keeps_the_normal_send_path(self):
        result,values=self.worker_tick(recovering=False,window_open=True)
        self.assertEqual(result,0)
        self.assertFalse(values['run'].call_args.kwargs['reconcile_only'])

    def test_submitted_unknown_waits_for_original_intent_instead_of_exiting(self):
        result,values=self.worker_tick(recovering=[False,True],window_open=True,
            error=ValueError('it_delivery_send_unknown'))
        self.assertEqual(result,0)
        self.assertEqual(values['write'].call_args.args[1]['state'],'waiting_reconciliation')

    def test_unrecorded_unknown_error_is_not_guessed_to_be_a_recoverable_submission(self):
        result,values=self.worker_tick(recovering=False,window_open=True,
            error=ValueError('it_delivery_send_unknown'))
        self.assertEqual(result,2)
        self.assertEqual(values['write'].call_args.args[1]['state'],'attention')

    def test_read_network_failure_has_bounded_local_backoff_without_relaunch_churn(self):
        result,values=self.worker_tick(recovering=False,window_open=True,
            error=ValueError('im_transport_error'),once=False)
        self.assertEqual(result,2);self.assertEqual(values['run'].call_count,5)
        states=[call.args[1]['state'] for call in values['write'].call_args_list]
        self.assertEqual(states,['waiting_transport']*4+['attention'])
        self.assertEqual([MODULE.transport_retry_delay('im_transport_error',n) for n in range(1,6)],
                         [3,15,60,300,None])
        self.assertIsNone(MODULE.transport_retry_delay('im_response_invalid',1))

    def test_raw_ledger_contention_waits_without_hiding_other_database_errors(self):
        import sqlite3
        for message in ('database is locked','database table is locked','database is busy'):
            code=MODULE.error_code(sqlite3.OperationalError(message))
            self.assertEqual(MODULE.expected_wait_state(code),'waiting_account')
        code=MODULE.error_code(sqlite3.OperationalError('database disk image is malformed'))
        self.assertEqual(code,'OperationalError');self.assertIsNone(MODULE.expected_wait_state(code))

    def test_an_empty_ready_pool_waits_without_relaunch_churn(self):
        self.assertEqual(MODULE.expected_wait_state('market_send_candidate_missing'),'waiting_pool')
        self.assertEqual(MODULE.expected_wait_state('ProfileBusyError'),'waiting_account')
        self.assertEqual(MODULE.expected_wait_state('local_store_busy'),'waiting_account')
        self.assertIsNone(MODULE.expected_wait_state('it_delivery_dispatch_not_allowed'))
        self.assertEqual(MODULE.expected_wait_state('new_contact_capacity_reached'),'waiting_capacity')
        self.assertEqual(MODULE.expected_wait_state('it_delivery_send_rejected'),'waiting_platform_refusal')
        self.assertEqual(MODULE.expected_wait_state('market_send_result_unknown'),'waiting_reconciliation')
        self.assertEqual(MODULE.next_delay(sent=False,waiting='waiting_reconciliation',interval=30),300)
        self.assertEqual(MODULE.expected_wait_state('market_send_conversation_result_unknown'),'waiting_reconciliation')

    def test_confirmed_send_has_no_fixed_thirty_second_gap(self):
        self.assertEqual(MODULE.next_delay(sent=True,waiting=None,interval=30),0.25)
        self.assertEqual(MODULE.next_delay(sent=True,waiting=None,interval=30,inbox_waiting_now=True),3)
        self.assertEqual(MODULE.next_delay(sent=False,waiting='waiting_account',interval=30),3)
        self.assertEqual(MODULE.next_delay(sent=False,waiting='waiting_pool',interval=30),30)
        self.assertEqual(MODULE.next_delay(sent=False,waiting='waiting_capacity',interval=30),300)

    def test_an_unverified_market_sends_one_page_started_canary_then_stops(self):
        unverified={'capabilities':{'message_send':{'state':'not_tested'}}}
        verified={'capabilities':{'message_send':{'state':'verified'}}}
        self.assertTrue(MODULE.first_send_canary(None))
        self.assertTrue(MODULE.first_send_canary(unverified))
        self.assertFalse(MODULE.first_send_canary(verified))
        self.assertTrue(MODULE.stops_after({'state':'confirmed','realSends':0,
            'canaryCompleted':True,'canaryStopRequested':True}))
        self.assertFalse(MODULE.stops_after({'state':'confirmed','realSends':1}))
        self.assertFalse(MODULE.stops_after({'state':'confirmed','canaryCompleted':True,'canaryStopRequested':False}))


    def test_a_page_start_launches_the_sender_for_any_non_it_market(self):
        import sys
        from unittest.mock import patch
        sys.path.insert(0,str(PATH.parents[0]))
        import lib.market_send_control as control
        from lib.second_cycle import CycleError
        root=PATH.parents[1]
        with patch.object(control,'launch',side_effect=lambda _root,market:{'pid':1,'market':market}):
            for market in ('br','my','uk','de','fr'):self.assertEqual(control.launch_worker(root,market)['market'],market)
            with self.assertRaisesRegex(CycleError,'market_send_runtime_unavailable'):control.launch_worker(root,'it')


class MarketSenderLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);(self.root/'var').mkdir()

    def test_parallel_launches_share_one_child_and_one_status_owner(self):
        barrier=threading.Barrier(2);results=[];errors=[]
        def launch():
            try:barrier.wait(timeout=3);results.append(lifecycle.launch(self.root,'br'))
            except BaseException as error:errors.append(error)
        with patch.object(lifecycle.subprocess,'Popen',return_value=SimpleNamespace(pid=os.getpid())) as spawn, \
             patch('lib.process_liveness.record_alive',return_value=True):
            threads=[threading.Thread(target=launch) for _ in range(2)]
            for thread in threads:thread.start()
            for thread in threads:thread.join(timeout=5)
        self.assertFalse(errors);self.assertTrue(all(not thread.is_alive() for thread in threads))
        spawn.assert_called_once();self.assertEqual(len(results),2)
        self.assertEqual([item['pid'] for item in results],[os.getpid(),os.getpid()])

    def test_held_worker_lock_prevents_duplicate_even_if_status_is_missing(self):
        with (self.root/'var/market-send-worker-br.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            with patch.object(lifecycle.subprocess,'Popen') as spawn:
                result=lifecycle.launch(self.root,'br')
        spawn.assert_not_called();self.assertTrue(result['running'])
        self.assertEqual(result['reason'],'worker_lock_busy')
        self.assertFalse(lifecycle.state_path(self.root,'br').exists())

    def test_fast_child_status_is_not_overwritten_by_starting(self):
        children=[]
        def spawn(*_,**__):
            child=threading.Thread(target=lifecycle.write_state,args=(self.root,'br',
                {'running':True,'state':'waiting_window','pid':os.getpid()}))
            child.start();children.append(child);return SimpleNamespace(pid=os.getpid())
        with patch.object(lifecycle.subprocess,'Popen',side_effect=spawn):lifecycle.launch(self.root,'br')
        for child in children:child.join(timeout=5)
        self.assertTrue(all(not child.is_alive() for child in children))
        self.assertEqual(json.loads(lifecycle.state_path(self.root,'br').read_text())['state'],'waiting_window')

    def test_recovery_detection_excludes_unsubmitted_and_isolated_work(self):
        db=sqlite3.connect(':memory:');self.addCleanup(db.close)
        db.row_factory=sqlite3.Row
        db.executescript('''CREATE TABLE plan(id,institution,market);
            CREATE TABLE cycle_delivery(id,plan_id,state,snapshot);
            CREATE TABLE cycle_delivery_part(delivery_id,state);
            CREATE TABLE cycle_conversation_intent(delivery_id,state);
            INSERT INTO plan VALUES('br-plan','bjn-local-research','br');
            INSERT INTO plan VALUES('it-plan','bjn-local-research','it');''')
        store=SimpleNamespace(db=db)
        for market,mode in [('br','market-continuous-v1'),('br','market-first-v1'),('it','continuous-v1')]:
            with self.subTest(market=market,mode=mode):
                db.execute('DELETE FROM cycle_delivery');db.execute('DELETE FROM cycle_delivery_part');db.execute('DELETE FROM cycle_conversation_intent')
                db.execute('INSERT INTO cycle_delivery VALUES(?,?,?,?)',('delivery',market+'-plan','ready',json.dumps({'executionMode':mode})))
                db.execute("INSERT INTO cycle_delivery_part VALUES('delivery','ready')")
                self.assertFalse(lifecycle.has_pending_reconciliation(store,market))
                db.execute("INSERT INTO cycle_conversation_intent VALUES('delivery','ready')")
                self.assertFalse(lifecycle.has_pending_reconciliation(store,market))
                db.execute("UPDATE cycle_conversation_intent SET state='inflight'")
                self.assertTrue(lifecycle.has_pending_reconciliation(store,market))
                self.assertFalse(lifecycle.has_pending_reconciliation(store,'fr'))
                db.execute("UPDATE cycle_delivery SET state='quarantined_unknown'")
                self.assertFalse(lifecycle.has_pending_reconciliation(store,market))
        db.execute("CREATE INDEX cycle_one_active_delivery ON cycle_delivery(plan_id) WHERE state IN ('ready','running','unknown')")
        statements=[];db.set_trace_callback(statements.append)
        self.assertFalse(lifecycle.has_pending_reconciliation(store,'br'))
        self.assertTrue(any('INDEXED BY cycle_one_active_delivery' in statement for statement in statements))

if __name__=='__main__':unittest.main()
