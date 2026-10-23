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
from lib.second_cycle import CycleError, CycleStore, digest, encoded


class FirstOutreachRuntimeTests(unittest.TestCase):
    def test_canary_policy_assesses_only_observed_pending_without_counting_other_states(self):
        from tests.test_sample_transport import read_contract, br_raw, seller_policy
        from lib.sample_transport import normalize_record, build_canary_contract
        from lib.sample_cost_policy import POLICY_PATH
        binding = {'market': 'br', 'account': 'acc2', 'institutionFingerprint': 'agency', 'identitySha256': 'current-headers'}
        policy = seller_policy()
        policy_path = self.root / POLICY_PATH
        policy_path.parent.mkdir(parents=True)
        policy_path.write_text(encoded(policy))
        observed = build_canary_contract(read_contract(binding), policy,
            pending_ui_evidence_ref='observed:pending-ui', request_candidate_ref='documented:review')
        rows = [normalize_record(br_raw(i, status=10 if i < 6 else 100), contract=observed) for i in range(22)]
        with CycleStore(self.root / 'var/second-cycle.sqlite') as store:
            samples.record_capability(store, 'br', 'acc2', 'canary', 'canary-proof', contract=observed, binding=binding)
            samples.store_records(store, 'br', 'acc2', rows, evidence_ref='all-state-proof', complete=True,
                binding=binding, contract=observed, read_scope='all_states', platform_counts={'TO_REVIEW_1': 6, 'COMPLETED': 16})
        with patch.object(samples, '_account', return_value='acc2'), patch.object(runtime, '_sample_binding_current', return_value=True), \
                patch.object(first, 'assess_application', return_value={'eligible': False, 'reason': 'sample_not_recommended_by_first_outreach'}):
            value = runtime.status(self.root)
        self.assertEqual(value['applicationCoverage'], 'complete')
        self.assertEqual((value['counts']['pendingApplications'], value['counts']['needsReview']), (6, 6))
        self.assertEqual(value['counts']['unknown'], 0)
        self.assertEqual(sum(row['state'] == 'pending' for row in value['applications']), 6)
        self.assertEqual(sum(row['state'] == 'sample_application_state_unverified' for row in value['applications']), 16)
        self.assertTrue(all(row['reason'] == 'sample_not_recommended_by_first_outreach'
                            for row in value['applications'] if row['state'] == 'pending'))
        self.assertFalse(value['control']['approvalsEnabled'])

    def test_current_readonly_all_state_counts_do_not_grant_approval_or_guess_row_status(self):
        from tests.test_sample_transport import read_contract, br_raw
        from lib.sample_transport import normalize_record
        binding = {'market': 'br', 'account': 'acc2', 'institutionFingerprint': 'agency', 'identitySha256': 'current-headers'}
        observed_contract = read_contract(binding)
        rows = [normalize_record(br_raw(i, status=10 if i < 6 else 100), contract=observed_contract) for i in range(22)]
        with CycleStore(self.root / 'var/second-cycle.sqlite') as store:
            samples.record_capability(store, 'br', 'acc2', 'readonly', 'readonly-proof', contract=observed_contract, binding=binding)
            samples.store_records(store, 'br', 'acc2', rows, evidence_ref='all-state-proof', complete=True,
                                  binding=binding, contract=observed_contract, read_scope='all_states',
                                  platform_counts={'TO_REVIEW_1': 6, 'COMPLETED': 16})
        with patch.object(samples, '_account', return_value='acc2'), patch.object(runtime, '_sample_binding_current', return_value=True):
            value = runtime.status(self.root)
            self.assertEqual(value['applicationCoverage'], 'complete')
            self.assertEqual((value['counts']['pendingApplications'], value['counts']['needsReview']), (6, 6))
            self.assertEqual(value['counts']['unknown'], 0)
            self.assertEqual(len(value['applications']), 22)
            self.assertTrue(all(row['state'] == 'sample_application_state_unverified' for row in value['applications']))
            self.assertFalse(value['control']['approvalsEnabled'])
            with CycleStore(self.root / 'var/second-cycle.sqlite') as store:
                store.db.execute("UPDATE sample_sync SET platform_counts=?", (encoded({'UNRECOGNIZED': 22}),))
                self.assertEqual(runtime._application_coverage(self.root, store), 'unverified')

    def test_verified_all_state_proof_survives_later_unproved_observation(self):
        from tests.test_sample_transport import contract, read_contract, br_raw, seller_policy
        from lib.sample_transport import normalize_record
        binding = {'market': 'br', 'account': 'acc2', 'institutionFingerprint': 'agency', 'identitySha256': 'current-headers'}
        verified = contract() | {'readScope': 'all_states', 'recordPaths': read_contract(binding)['recordPaths'],
                                 'statusMap': {'10': 'pending', '100': 'completed'}, 'costPolicy': seller_policy()}
        rows = [normalize_record(br_raw(i, status=10 if i < 6 else 52), contract=verified) for i in range(22)]
        with CycleStore(self.root / 'var/second-cycle.sqlite') as store:
            samples.record_capability(store, 'br', 'acc2', 'verified', 'verified-proof', contract=verified, binding=binding)
            proof = samples.store_records(store, 'br', 'acc2', rows, evidence_ref='all-state-proof', complete=True,
                    binding=binding, contract=verified, read_scope='all_states', platform_counts={'TO_REVIEW_1': 6, 'CANCELED': 16})
            samples.store_records(store, 'br', 'acc2', rows, evidence_ref='later-run-one-observation', complete=True)
            with patch.object(samples, '_account', return_value='acc2'), patch.object(runtime, '_sample_binding_current', return_value=True):
                snapshot = runtime._coverage_snapshot(self.root, store)
                self.assertEqual((snapshot['id'], snapshot['platformCounts']['TO_REVIEW_1']), (proof['syncId'], 6))
                result = runtime.status(self.root)
        self.assertEqual(result['applicationCoverage'], 'complete')
        self.assertEqual(result['counts']['pendingApplications'], 6)
        self.assertEqual(result['counts']['needsReview'], 6)

    def test_unclassified_product_does_not_block_other_ready_matches(self):
        base = first.status(self.root)
        base['readiness'] = {'ready': True, 'blockers': []}
        base['counts']['products'] = 1
        base['counts']['matchedCreators'] = 1
        base['runtime']['reason'] = None
        with patch.object(first, 'status', return_value=base), patch.object(runtime, '_category_blockers', return_value=['first_category_incomplete']), patch.object(runtime, '_send_capability', return_value=True):
            value = runtime.status(self.root)
        self.assertTrue(value['readiness']['ready'])
        self.assertEqual(value['runtime']['reason'], 'first_category_incomplete')

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
        self.assertEqual(result['applicationCoverage'], 'unverified')
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
        self.assertEqual(value['applicationCoverage'], 'unverified')
        self.assertIsNone(value['runtime']['reason'])

    def test_application_coverage_requires_current_verified_contract_and_sync_provenance(self):
        from tests.test_sample_transport import contract
        binding = {'market': 'br', 'account': 'acc11', 'institutionFingerprint': 'agency', 'identitySha256': 'current-headers'}
        with CycleStore(self.root / 'var/second-cycle.sqlite') as store:
            samples.ensure_schema(store)
            # Columns may be absent on a pre-upgrade ledger; they never imply coverage.
            self.assertEqual(runtime._application_coverage(self.root, store), 'unverified')
            samples.record_capability(store, 'br', 'acc11', 'verified', 'verified-contract', contract=contract(), binding=binding)
            samples.store_records(store, 'br', 'acc11', [], evidence_ref='old-filtered-empty', complete=True)
            with patch.object(samples, '_account', return_value='acc11'), patch.object(runtime, '_sample_binding_current', return_value=True):
                self.assertEqual(runtime._application_coverage(self.root, store), 'unverified')
                columns = {row[1] for row in store.db.execute('PRAGMA table_info(sample_sync)')}
                for column in ('binding_fingerprint', 'contract_fingerprint'):
                    if column not in columns:
                        store.db.execute('ALTER TABLE sample_sync ADD COLUMN ' + column + ' TEXT')
                store.db.execute("UPDATE sample_sync SET binding_fingerprint=?,contract_fingerprint=?", (digest(binding), digest(contract())))
                self.assertEqual(runtime._application_coverage(self.root, store), 'complete')
                store.db.execute("UPDATE sample_sync SET contract_fingerprint='older-contract'")
                self.assertEqual(runtime._application_coverage(self.root, store), 'unverified')
                store.db.execute('UPDATE sample_sync SET contract_fingerprint=?', (digest(contract()),))
                with patch.object(runtime, '_sample_binding_current', return_value=False):
                    self.assertEqual(runtime._application_coverage(self.root, store), 'unverified')
                store.db.execute("UPDATE sample_capability SET state='readonly',contract=NULL,binding=NULL WHERE market='br' AND account_name='acc11'")
                self.assertEqual(runtime._application_coverage(self.root, store), 'unverified')

    def test_application_coverage_rejects_stale_or_different_account_sync(self):
        from tests.test_sample_transport import contract
        binding = {'market': 'br', 'account': 'acc11', 'institutionFingerprint': 'agency', 'identitySha256': 'current-headers'}
        with CycleStore(self.root / 'var/second-cycle.sqlite') as store:
            samples.record_capability(store, 'br', 'acc11', 'verified', 'verified-contract', contract=contract(), binding=binding)
            samples.store_records(store, 'br', 'acc11', [], evidence_ref='stale-sync', complete=True)
            columns = {row[1] for row in store.db.execute('PRAGMA table_info(sample_sync)')}
            for column in ('binding_fingerprint', 'contract_fingerprint'):
                if column not in columns:
                    store.db.execute('ALTER TABLE sample_sync ADD COLUMN ' + column + ' TEXT')
            store.db.execute('UPDATE sample_sync SET binding_fingerprint=?,contract_fingerprint=?,observed=?', (digest(binding), digest(contract()), time.time() - 3600))
            with patch.object(samples, '_account', return_value='acc11'), patch.object(runtime, '_sample_binding_current', return_value=True):
                self.assertEqual(runtime._application_coverage(self.root, store), 'unverified')
                store.db.execute("UPDATE sample_sync SET observed=?,account_name='acc2'", (time.time(),))
                self.assertEqual(runtime._application_coverage(self.root, store), 'unverified')

    def test_binding_coverage_checks_current_project_headers_without_platform_session(self):
        headers = self.root / 'test-current-headers.json'
        headers.write_bytes(b'local-test-header')
        binding = {'market': 'br', 'account': 'acc11', 'institutionFingerprint': 'agency', 'identitySha256': hashlib.sha256(headers.read_bytes()).hexdigest()}
        with patch('lib.legacy_runtime.project_identity_paths', return_value={'market': 'br', 'headersJson': headers}):
            self.assertTrue(runtime._sample_binding_current(self.root, 'acc11', binding))
            headers.write_bytes(b'new-local-test-header')
            self.assertFalse(runtime._sample_binding_current(self.root, 'acc11', binding))

    def test_isolated_unknown_approval_stays_unknown_in_public_ledger(self):
        snapshot = samples.status(self.root)
        snapshot['counts'] = {'quarantined_unknown': 2, 'approved': 3}
        with patch.object(samples, 'status', return_value=snapshot):
            value = runtime.status(self.root)
        self.assertEqual(value['counts']['unknown'], 2)
        self.assertEqual(value['counts']['approved'], 3)
        self.assertEqual(value['applicationCoverage'], 'unverified')

    def test_sample_unknown_does_not_block_independent_first_send_readiness(self):
        snapshot = samples.status(self.root)
        snapshot['counts'] = {'quarantined_unknown': 2, 'unknown': 1}
        base = first.status(self.root)
        base['readiness'] = {'ready': True, 'blockers': []}
        base['runtime'] = {'state': 'stopped', 'reason': None, 'updatedAt': None}
        with patch.object(samples, 'status', return_value=snapshot), patch.object(first, 'status', return_value=base), patch.object(runtime, '_send_capability', return_value=True):
            value = runtime.status(self.root)
        self.assertEqual(value['counts']['unknown'], 3)
        self.assertTrue(value['readiness']['ready'])
        self.assertEqual(value['runtime']['state'], 'stopped')

    def test_complete_current_snapshot_excludes_historical_pending_applications(self):
        from tests.test_sample_transport import contract
        binding = {'market': 'br', 'account': 'acc11', 'institutionFingerprint': 'agency', 'identitySha256': 'current-headers'}
        application = {'applicationId': '1', 'oecId': '100', 'pid': '200', 'campaignId': '300', 'state': 'pending', 'paymentRequired': False}
        with CycleStore(self.root / 'var/second-cycle.sqlite') as store:
            samples.record_capability(store, 'br', 'acc11', 'verified', 'verified-contract', contract=contract(), binding=binding)
            now = time.time()
            samples.store_records(store, 'br', 'acc11', [application], observed_at=now, evidence_ref='earlier-pending', complete=True, binding=binding, contract=contract())
            samples.store_records(store, 'br', 'acc11', [{**application, 'applicationId': '2'}], observed_at=now + .01, evidence_ref='current-pending', complete=True, binding=binding, contract=contract())
        with patch.object(samples, '_account', return_value='acc11'), patch.object(runtime, '_sample_binding_current', return_value=True):
            value = runtime.status(self.root)
            self.assertEqual(value['applicationCoverage'], 'complete')
            self.assertEqual(value['counts']['pendingApplications'], 1)
            self.assertEqual([row['id'] for row in value['applications']], ['2'])
            with CycleStore(self.root / 'var/second-cycle.sqlite') as store:
                samples.store_records(store, 'br', 'acc11', [], observed_at=now + .02, evidence_ref='current-empty', complete=True, binding=binding, contract=contract())
                self.assertEqual(store.db.execute('SELECT count(*) FROM sample_application').fetchone()[0], 2)
            value = runtime.status(self.root)
        self.assertEqual(value['applicationCoverage'], 'complete')
        self.assertEqual(value['counts']['pendingApplications'], 0)
        self.assertEqual(value['applications'], [])

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
        self.assertEqual(result['automation']['samples']['reason'], 'sample_application_assessment_incomplete')
        self.assertEqual(result['runtime']['reason'], base['runtime']['reason'])
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
