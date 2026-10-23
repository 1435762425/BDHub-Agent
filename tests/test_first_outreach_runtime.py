import fcntl
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from lib import first_outreach as first
from lib import first_outreach_runtime as runtime
from lib import sample_operations as samples
from lib.second_cycle import CycleError, CycleStore, encoded


class FirstOutreachRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        with CycleStore(self.root / 'var/second-cycle.sqlite') as store:
            self.plan = store.plan('bjn-local-research', 'br')
            runtime.ensure_schema(store)
        self.launcher = Mock(return_value=123)

    def tearDown(self):
        self.temp.cleanup()

    def body(self, action='prepare', revision=0, request='first-request-0001', **fields):
        return {'action': action, 'market': 'br', 'requestId': request, 'expectedRevision': revision, **fields}

    def queue(self, body=None):
        body = body or self.body()
        return runtime.mutate(self.root, body['action'], 'br', body, launcher=self.launcher)

    def job(self):
        with CycleStore(self.root / 'var/second-cycle.sqlite', readonly=True) as store:
            return dict(store.db.execute('SELECT * FROM first_outreach_job LIMIT 1').fetchone())

    def set_control(self, mode='stopped', approvals=False):
        with CycleStore(self.root / 'var/second-cycle.sqlite') as store:
            store.db.execute('INSERT INTO first_outreach_control(plan_id,mode,approvals_enabled) VALUES(?,?,?) ON CONFLICT(plan_id) DO UPDATE SET mode=excluded.mode,approvals_enabled=excluded.approvals_enabled', (self.plan, mode, int(approvals)))

    def test_requests_are_exact_market_bound_and_revisioned(self):
        for action in runtime.ACTIONS:
            body = self.body(action, **({'approvalsEnabled': False} if action == 'set-approvals' else {}))
            self.assertEqual(runtime.validate_request(action, 'br', body), body)
        for change in ({'market': 'it'}, {'requestId': 'x'}, {'expectedRevision': True}, {'expectedRevision': -1}, {'secret': 'bad'}, {'approvalsEnabled': True}, {'action': 'stop'}):
            with self.assertRaisesRegex(CycleError, 'invalid_first_outreach_request'):
                runtime.validate_request('prepare', 'br', {**self.body(), **change})

    def test_status_absent_state_is_readonly_and_explicitly_unavailable(self):
        absent = self.root / 'absent'
        with self.assertRaisesRegex(CycleError, 'state_unavailable'):
            runtime.status(absent)
        self.assertFalse(absent.exists())
        path = self.root / 'var/second-cycle.sqlite'
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        with patch.object(runtime, 'launch_worker') as launch, patch.object(samples, 'sync') as sync:
            result = runtime.status(self.root)
        self.assertEqual(result['platformWrites'], 0)
        self.assertFalse(result['readiness']['ready'])
        self.assertEqual(result['control'], {'revision': 0, 'mode': 'stopped', 'approvalsEnabled': False, 'updatedAt': 0})
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), before)
        launch.assert_not_called()
        sync.assert_not_called()

    def test_register_before_launch_duplicate_reuses_original_job_and_cas(self):
        def launch(root):
            self.assertEqual(self.job()['state'], 'queued')
            return 123
        body = self.body()
        first_state = runtime.mutate(self.root, 'prepare', 'br', body, launcher=launch)
        self.assertEqual(first_state['control']['revision'], 1)
        self.queue(body)
        self.assertEqual(self.job()['request_id'], body['requestId'])
        with CycleStore(self.root / 'var/second-cycle.sqlite', readonly=True) as store:
            self.assertEqual(store.db.execute('SELECT count(*) FROM first_outreach_job').fetchone()[0], 1)
            self.assertEqual(first.control(store)['revision'], 1)
        with self.assertRaisesRegex(CycleError, 'revision_conflict'):
            self.queue(self.body(request='first-stale-request'))
        with self.assertRaisesRegex(CycleError, 'request_conflict'):
            self.queue(self.body('sync'))
        with self.assertRaisesRegex(CycleError, 'request_conflict'):
            self.queue(self.body('stop', 1))

    def test_control_request_id_cannot_be_borrowed_for_a_job(self):
        self.queue(self.body('stop', request='first-stop-0001'))
        with self.assertRaisesRegex(CycleError, 'request_conflict'):
            self.queue(self.body('prepare', 1, request='first-stop-0001'))
        self.launcher.assert_not_called()

    def test_crash_resumes_same_job_at_saved_stage(self):
        self.queue(self.body('sync'))
        with patch.object(runtime, '_refresh', return_value={'state': 'complete'}) as refresh, \
                patch.object(samples, 'sync', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                runtime.worker_tick(self.root)
        self.assertEqual((self.job()['state'], self.job()['stage']), ('running', 1))
        with patch.object(runtime, '_refresh') as refresh, patch.object(samples, 'sync', return_value={'state': 'complete'}) as sync, \
                patch.object(runtime, '_enrich', return_value={'state': 'complete'}) as enrich, \
                patch.object(first, 'prepare', return_value={'state': 'complete', 'completed': True}) as prepare:
            self.assertEqual(runtime.worker_tick(self.root)['state'], 'complete')
        refresh.assert_not_called()
        sync.assert_called_once()
        enrich.assert_called_once()
        prepare.assert_called_once()
        self.assertEqual((self.job()['state'], self.job()['stage']), ('complete', 4))
        self.assertEqual(self.job()['request_id'], 'first-request-0001')

    def test_stop_interrupts_new_job_steps_but_does_not_disable_approvals(self):
        self.queue(self.body())
        self.queue(self.body('set-approvals', 1, 'first-approvals-1', approvalsEnabled=True))
        self.queue(self.body('stop', 2, 'first-stop-00001'))
        with patch.object(runtime, '_enrich') as enrich:
            self.assertEqual(runtime.worker_tick(self.root)['state'], 'stopped')
        enrich.assert_not_called()
        self.assertEqual(self.job()['error'], 'first_outreach_job_stopped')
        self.assertTrue(runtime._cfg(self.root)['approvalsEnabled'])
        self.assertEqual(runtime._cfg(self.root)['mode'], 'stopped')

    def test_stopped_worker_never_sends_and_approval_switch_is_independent(self):
        with patch.object(runtime, '_send') as send, patch.object(samples, 'run_one') as review:
            runtime.worker_tick(self.root)
        send.assert_not_called()
        review.assert_not_called()
        self.set_control(approvals=True)
        with patch.object(runtime, '_send') as send, patch.object(samples, 'sync', return_value={}) as sync, patch.object(samples, 'run_one', return_value={}) as review:
            runtime.worker_tick(self.root)
            runtime.worker_tick(self.root)
        send.assert_not_called()
        sync.assert_called_once()
        review.assert_called_once()

    def test_unknown_send_is_original_reconciliation_even_after_stop(self):
        with patch.object(runtime, '_send_unknown', return_value=1), patch.object(runtime, '_send', return_value={'state': 'waiting_reconciliation'}) as send:
            runtime.worker_tick(self.root)
        self.assertTrue(send.call_args.kwargs['reconcile_only'])
        self.assertEqual(runtime._cfg(self.root)['mode'], 'stopped')

    def test_single_worker_os_lock_prevents_a_second_executor(self):
        with (self.root / 'var/first-outreach-br.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with patch.object(runtime, 'worker_tick') as tick:
                self.assertEqual(runtime.worker(self.root, once=True, stopped=lambda: False)['state'], 'busy')
            tick.assert_not_called()

    def test_temporary_account_wait_keeps_original_job_resumable(self):
        self.queue()
        with patch.object(runtime, '_enrich', side_effect=CycleError('account_busy')):
            self.assertEqual(runtime.worker_tick(self.root)['state'], 'waiting_account')
        self.assertEqual((self.job()['state'], self.job()['stage']), ('running', 0))
        with patch.object(runtime, '_enrich', return_value={'state': 'complete'}), patch.object(first, 'prepare', return_value={'state': 'complete'}):
            runtime.worker_tick(self.root)
        self.assertEqual(self.job()['state'], 'complete')

    def test_paused_catalog_refresh_does_not_prepare_older_source(self):
        self.queue(self.body('sync'))
        with patch.object(runtime, '_refresh', return_value={'state': 'paused', 'reason': 'account_busy'}), patch.object(first, 'prepare') as prepare:
            runtime.worker_tick(self.root)
        prepare.assert_not_called()
        self.assertEqual((self.job()['state'], self.job()['stage']), ('running', 0))

    def test_unverified_sender_blocks_page_start_without_creating_authorization(self):
        with patch.object(runtime, '_send_capability', return_value=False):
            with self.assertRaisesRegex(CycleError, 'send_capability_unverified'):
                self.queue(self.body('start'))
        self.assertEqual(runtime._cfg(self.root)['mode'], 'stopped')
        self.launcher.assert_not_called()

    def test_unverified_sample_approval_does_not_block_independent_send_readiness(self):
        base = first.status(self.root)
        base['readiness'] = {'ready': True, 'blockers': []}
        base['runtime'] = {'state': 'stopped', 'reason': None, 'updatedAt': None}
        with patch.object(first, 'status', return_value=base), patch.object(runtime, '_send_capability', return_value=True):
            value = runtime.status(self.root)
        self.assertTrue(value['readiness']['ready'])
        self.assertIsNone(value['runtime']['reason'])

    def test_public_status_projects_no_private_fields_and_counts_all_applications(self):
        applications = [{'applicationId': str(index), 'oecId': '100', 'pid': '200', 'campaignId': '300',
                         'state': 'pending', 'paymentRequired': False} for index in range(1, 103)]
        with CycleStore(self.root / 'var/second-cycle.sqlite') as store:
            samples.store_records(store, 'br', 'acc11', applications, evidence_ref='test-evidence', complete=True)
        base = first.status(self.root)
        base['generation'] = {'completedAt': 123, 'private': 'never-return'}
        base['control']['secret'] = 'never-return'
        with patch.object(first, 'status', return_value=base), patch.object(first, 'assess_application', return_value={'eligible': False, 'reason': 'sample_not_recommended_by_first_outreach'}):
            result = runtime.status(self.root)
        self.assertEqual(result['counts']['pendingApplications'], 102)
        self.assertEqual(result['counts']['needsReview'], 100)
        self.assertEqual(len(result['applications']), 100)
        self.assertEqual(result['runtime']['reason'], 'sample_application_assessment_incomplete')
        self.assertEqual(result['sourceUpdatedAt'], 123)
        self.assertEqual(set(result['control']), {'revision', 'mode', 'approvalsEnabled', 'updatedAt'})
        self.assertNotIn('never-return', encoded(result))

    def test_worker_launcher_reuses_live_pid_and_uses_fixed_argv_when_dead(self):
        with patch.object(runtime, 'worker_state', return_value={'running': True, 'pid': 321}) as state, patch.object(runtime.subprocess, 'Popen') as popen:
            self.assertEqual(runtime.launch_worker(self.root), 321)
            popen.assert_not_called()
        with patch.object(runtime, 'worker_state', return_value={'running': False, 'pid': None}), patch.object(runtime.subprocess, 'Popen', return_value=Mock(pid=123)) as popen:
            self.assertEqual(runtime.launch_worker(self.root), 123)
        self.assertEqual(popen.call_args.args[0], [str(self.root / '.venv/bin/python'), str(self.root / 'scripts/first-outreach.py'), 'worker', '--market', 'br'])
        self.assertEqual(popen.call_args.kwargs['env']['PYTHONDONTWRITEBYTECODE'], '1')
        self.assertTrue(popen.call_args.kwargs['start_new_session'])

    def test_cli_status_and_mutation_validation_do_not_launch_live_workers(self):
        script = Path(__file__).resolve().parents[1] / 'scripts/first-outreach.py'
        spec = importlib.util.spec_from_file_location('first_cli_test', script)
        cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cli)
        with patch.object(cli, 'ROOT', self.root), patch.object(cli, 'mutate', return_value={}) as mutate, patch('builtins.print'):
            self.assertEqual(cli.main(['status', '--market', 'br']), 0)
            mutate.assert_not_called()
            self.assertEqual(cli.main(['prepare', '--market', 'br', '--json', json.dumps(self.body())]), 0)
            mutate.assert_called_once()
            self.assertEqual(cli.main(['status', '--market', 'br', '--json', '{}']), 2)


if __name__ == '__main__':
    unittest.main()
