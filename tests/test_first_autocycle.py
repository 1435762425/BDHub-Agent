"""The durable first-outreach loop advances without creating a new grant."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'vendor'))
from lib import first_catalog, first_outreach as first, first_outreach_runtime as runtime
from lib import sample_operations as samples
from lib.second_cycle import CycleError, CycleStore, encoded


class FirstAutocycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.path = self.root / 'var/second-cycle.sqlite'
        self.now = time.time()
        self.start = {'requestId': 'confirmed-continue-001', 'revision': 7}
        with CycleStore(self.path) as store:
            self.plan = store.plan('bjn-local-research', 'br')
            runtime.ensure_schema(store)
            store.db.execute('INSERT INTO first_outreach_control(plan_id,mode,revision,start_request,start_revision) VALUES(?,?,?,?,?)',
                             (self.plan, 'continuous', 9, self.start['requestId'], self.start['revision']))
            store.db.execute('INSERT INTO first_outreach_request VALUES(?,?,?,?,?)',
                             (self.start['requestId'], self.plan, encoded(['br', 'continue', None]), 7, self.now))

    def tearDown(self):
        self.temp.cleanup()

    def record(self):
        with CycleStore(self.path, readonly=True) as store:
            return dict(store.db.execute('SELECT * FROM first_outreach_job ORDER BY created DESC LIMIT 1').fetchone())

    def fresh_supply(self):
        with CycleStore(self.path) as store:
            store.publish(self.plan, 'live-br-campaign', self.now, [])
            store.db.execute('INSERT INTO first_outreach_generation(id,plan_id,created,completed,state,catalog_fingerprint) VALUES(?,?,?,?,?,?)',
                             ('fresh-head', self.plan, self.now, self.now, 'complete', 'fingerprint'))
            store.db.execute('INSERT INTO first_outreach_head VALUES(?,?)', (self.plan, 'fresh-head'))

    def test_automatic_supply_is_durable_idempotent_and_never_creates_a_grant(self):
        before = runtime._cfg(self.root)
        a = runtime._queue_supply(self.root)
        b = runtime._queue_supply(self.root)
        self.assertEqual((a['action'], a['request_id']), ('sync', b['request_id']))
        self.assertEqual(json.loads(a['payload'])['firstStart'], self.start)
        self.assertEqual(runtime._cfg(self.root), before)
        with CycleStore(self.path, readonly=True) as store:
            self.assertEqual(store.db.execute('SELECT count(*) FROM first_outreach_request').fetchone()[0], 1)
            self.assertEqual(store.db.execute('SELECT count(*) FROM first_outreach_job').fetchone()[0], 1)
            self.assertIsNone(runtime._automation_status(store, first.control(store))['supply']['nextRunAt'])

    def test_automatic_supply_does_not_start_while_manually_stopped_or_in_canary(self):
        for mode in ('stopped', 'canary'):
            with CycleStore(self.path) as store:
                store.db.execute('UPDATE first_outreach_control SET mode=?', (mode,))
            self.assertIsNone(runtime._queue_supply(self.root))

    def test_recent_exhausted_candidates_do_not_repeat_a_full_catalog_refresh(self):
        self.fresh_supply()
        self.assertIsNone(runtime._queue_supply(self.root, 'market_send_candidate_missing'))
        job = runtime._queue_supply(self.root, 'first_outreach_prepare_required')
        self.assertEqual(job['action'], 'prepare')

    def test_read_failure_keeps_request_stage_and_backoff_while_send_continues(self):
        original = runtime._queue_supply(self.root)
        with patch.object(runtime, '_send_unknown', return_value=0), \
                patch.object(runtime, '_send', return_value={'state': 'continuous', 'realSends': 1}) as send, \
                patch.object(runtime, '_refresh', side_effect=CycleError('account_busy')) as refresh:
            first_tick = runtime.worker_tick(self.root)
            second_tick = runtime.worker_tick(self.root)
        job = self.record()
        self.assertEqual((job['request_id'], job['stage'], job['state']), (original['request_id'], 0, 'running'))
        self.assertGreater(job['next_attempt'], time.time())
        self.assertEqual(job['failures'], 1)
        self.assertEqual(first_tick['supply']['retryAfterSeconds'], 30)
        self.assertEqual(second_tick['state'], 'continuous')
        self.assertEqual(send.call_count, 2)
        refresh.assert_called_once()

    def test_successful_read_slice_clears_old_failure_without_restarting_stage(self):
        job = runtime._queue_supply(self.root)
        original = runtime._cfg(self.root)
        runtime._job_update(self.root, job, state='running', error='workflow_resource_busy', failure=True)
        with patch.object(runtime, '_refresh', return_value={'state': 'paused', 'reason': 'first_catalog_read_budget'}):
            runtime.process_job(self.root, self.record())
        current = self.record()
        self.assertEqual((current['request_id'], current['stage'], current['state']), (job['request_id'], 0, 'running'))
        self.assertIsNone(current['error'])
        self.assertEqual(current['failures'], 0)
        self.assertLessEqual(current['next_attempt'], time.time())
        self.assertEqual(runtime._cfg(self.root), original)

    def test_bounded_prepare_resumes_exact_stage_and_leaves_time_for_samples(self):
        self.fresh_supply()
        job = runtime._queue_supply(self.root, 'first_outreach_prepare_required')
        runtime._job_update(self.root, job, stage=2)
        with CycleStore(self.path) as store:
            store.db.execute('UPDATE first_outreach_control SET approvals_enabled=1')
        with patch.object(runtime, '_send_unknown', return_value=0), \
                patch.object(runtime, '_send', return_value={'state': 'continuous'}) as send, \
                patch.object(runtime, '_refresh') as refresh, patch.object(runtime, '_enrich') as enrich, \
                patch.object(first, 'prepare', return_value={'state': 'paused'}) as prepare, \
                patch.object(samples, 'sync', return_value={}) as sync, \
                patch.object(samples, 'run_one', return_value={'state': 'idle'}) as review:
            runtime.worker_tick(self.root)
        self.assertEqual((self.record()['request_id'], self.record()['stage']), (job['request_id'], 2))
        self.assertEqual(prepare.call_args.kwargs['max_pages'], 4)
        send.assert_called_once()
        sync.assert_called_once()
        review.assert_called_once()
        refresh.assert_not_called()
        enrich.assert_not_called()

    def test_stop_invalidates_queued_automatic_work_without_touching_approval_switch(self):
        runtime._queue_supply(self.root)
        with CycleStore(self.path) as store:
            store.db.execute("UPDATE first_outreach_control SET mode='stopped',approvals_enabled=1")
        with patch.object(runtime, '_send') as send, patch.object(runtime, '_refresh') as refresh, \
                patch.object(samples, 'sync', return_value={}), patch.object(samples, 'run_one', return_value={'state': 'idle'}):
            runtime.worker_tick(self.root)
        self.assertEqual(self.record()['error'], 'first_outreach_job_stopped')
        self.assertEqual(runtime._cfg(self.root)['mode'], 'stopped')
        self.assertTrue(runtime._cfg(self.root)['approvalsEnabled'])
        send.assert_not_called()
        refresh.assert_not_called()

    def test_material_loop_reuses_card_ledger_and_yields_after_one_pass(self):
        self.fresh_supply()
        job = runtime._queue_supply(self.root, 'first_outreach_prepare_required')
        runtime._job_update(self.root, job, stage=6)
        job = self.record()
        with patch.object(runtime, '_cards', return_value={'state': 'complete', 'remaining': 2}) as cards:
            result = runtime.process_job(self.root, job)
        self.assertEqual(result['reason'], 'first_outreach_materials_unavailable')
        self.assertEqual((self.record()['stage'], self.record()['request_id']), (4, job['request_id']))
        self.assertGreater(self.record()['next_attempt'], time.time())
        cards.assert_called_once()
        self.assertEqual(cards.call_args.args[2], 'create')

    def test_sample_failures_have_separate_backoff_and_do_not_overwrite_sends(self):
        self.fresh_supply()
        with CycleStore(self.path) as store:
            store.db.execute('UPDATE first_outreach_control SET approvals_enabled=1')
        with patch.object(runtime, '_send_unknown', return_value=0), \
                patch.object(runtime, '_send', return_value={'state': 'continuous'}) as send, \
                patch.object(samples, 'sync', side_effect=CycleError('sample_read_failed')) as sync:
            runtime.worker_tick(self.root)
            runtime.worker_tick(self.root)
        with CycleStore(self.path, readonly=True) as store:
            row = dict(store.db.execute('SELECT * FROM first_outreach_runtime').fetchone())
            automation = runtime._automation_status(store, first.control(store))
        self.assertEqual(row['state'], 'continuous')
        self.assertEqual(row['sample_state'], 'attention')
        self.assertEqual(automation['samples']['reason'], 'sample_read_failed')
        self.assertGreater(automation['samples']['nextRunAt'], time.time())
        self.assertEqual(send.call_count, 2)
        sync.assert_called_once()

    def test_approval_switch_off_preserves_only_existing_fulfilment_reads(self):
        with CycleStore(self.path) as store:
            store.db.execute("UPDATE first_outreach_control SET mode='stopped'")
        with patch.object(samples, 'has_followups', return_value=True), \
                patch.object(samples, 'sync', return_value={}) as sync, \
                patch.object(samples, 'run_one') as review, patch.object(runtime, '_send') as send:
            runtime.worker_tick(self.root)
        sync.assert_called_once()
        self.assertFalse(sync.call_args.kwargs['stopped']())
        review.assert_not_called()
        send.assert_not_called()

    def test_material_authorization_requires_original_continue_receipt_and_revision(self):
        with CycleStore(self.path) as store:
            self.assertEqual(first.require_material_grant(store, self.start)['mode'], 'continuous')
            for invalid in ({**self.start, 'revision': 8}, {**self.start, 'requestId': 'invented-grant'}):
                with self.assertRaises(CycleError):
                    first.require_material_grant(store, invalid)
            store.db.execute('DELETE FROM first_outreach_request')
            with self.assertRaisesRegex(CycleError, 'material_authorization_invalid'):
                first.require_material_grant(store, self.start)

    def test_material_cli_checks_original_grant_on_every_write(self):
        spec = importlib.util.spec_from_file_location('first_materials_cli_test', ROOT / 'scripts/catalog-link-prepare.py')
        cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cli)
        with patch.object(cli, 'ROOT', self.root), patch.object(cli, 'FIRST_START', self.start):
            self.assertFalse(cli.first_materials_stopped())
            with CycleStore(self.path) as store:
                store.db.execute("UPDATE first_outreach_control SET mode='stopped'")
            self.assertTrue(cli.first_materials_stopped())
        prep = Mock()
        prep.policy = {}
        with patch.object(cli, 'CatalogLinks') as ledger, patch('lib.link_naming.load', return_value={'version': 'v1'}):
            cli.step_create(prep, 'original-run', 1, {}, stopped=lambda: True)
        prep.claim_create.assert_not_called()
        ledger.return_value.begin.assert_not_called()

    def test_shared_second_outreach_unknown_keeps_original_readback_alive(self):
        with CycleStore(self.path) as store:
            store.db.executescript('''CREATE TABLE cycle_delivery(id TEXT,plan_id TEXT,state TEXT);
                CREATE TABLE cycle_delivery_part(delivery_id TEXT,state TEXT);
                CREATE TABLE cycle_conversation_intent(delivery_id TEXT,state TEXT);''')
            store.db.execute('INSERT INTO cycle_delivery VALUES(?,?,?)', ('second-original', self.plan, 'unknown'))
            store.db.execute('INSERT INTO cycle_delivery VALUES(?,?,?)', ('isolated-original', self.plan, 'quarantined_unknown'))
            store.db.execute('INSERT INTO cycle_delivery_part VALUES(?,?)', ('isolated-original', 'unknown'))
            store.db.execute("UPDATE first_outreach_control SET mode='stopped'")
        with patch.object(runtime, '_send', return_value={'state': 'waiting_reconciliation'}) as send:
            self.assertEqual(runtime._send_unknown(self.root), 1)
            runtime.worker_tick(self.root)
        self.assertTrue(send.call_args.kwargs['reconcile_only'])
        self.assertEqual(runtime._cfg(self.root)['mode'], 'stopped')

    def test_required_cards_reuse_normal_cli_and_frozen_grant_without_canary(self):
        offer = {'pid': '123', 'campaignId': '456', 'offerKey': 'campaign:123:456', 'catalogSource': 'campaign',
                 'title': 'Batom', 'categoryId': '601450', 'available': True, 'endAt': self.now + 86400, 'evidenceRef': 'catalog-proof',
                 'sample': {'supported': True, 'quota': 2, 'observedAt': self.now, 'evidenceRef': 'sample-proof'}}
        with CycleStore(self.path) as store:
            store.publish(self.plan, 'live-br-campaign', self.now, [offer])
        calls = []
        def runner(command, **kwargs):
            calls.append(command)
            return SimpleNamespace(returncode=0, stdout=encoded({'state': 'completed', 'platformWrites': 1}))
        result = first_catalog.prepare_cards(self.root, 'original-auto-job', self.start, 'create', runner=runner)
        self.assertEqual((result['platformWrites'], result['remaining']), (1, 2))
        self.assertIn('--first-request', calls[0])
        self.assertIn(self.start['requestId'], calls[0])
        self.assertNotIn('--canary', calls[0])
        self.assertEqual(calls[0][2], 'create')
        with CycleStore(self.path) as store:
            store.db.execute("UPDATE first_outreach_control SET mode='stopped'")
        with self.assertRaises(CycleError):
            first_catalog.prepare_cards(self.root, 'original-auto-job', self.start, 'create', runner=runner)
        self.assertEqual(len(calls), 1)

    def test_existing_catalog_workflow_owns_material_progress(self):
        with CycleStore(self.path) as store:
            store.db.executescript("""CREATE TABLE workflow_run(run_id TEXT,market TEXT,state TEXT);
                CREATE TABLE workflow_stage_run(run_id TEXT,stage TEXT,state TEXT);
                INSERT INTO workflow_run VALUES('original-workflow','br','running');
                INSERT INTO workflow_stage_run VALUES('original-workflow','taplink_prepare','queued');""")
        runner = Mock()
        result = first_catalog.prepare_cards(self.root, 'original-auto-job', self.start, 'seed', runner=runner)
        self.assertEqual(result['reason'], 'first_material_waiting_workflow')
        runner.assert_not_called()

    def test_no_executable_products_still_allows_original_card_verification(self):
        calls = []
        def runner(command, **kwargs):
            calls.append(command)
            return SimpleNamespace(returncode=0, stdout=encoded({'state': 'completed', 'platformWrites': 0}))
        self.assertEqual(first_catalog.prepare_cards(self.root, 'original-card-followup', self.start, 'verify', runner=runner)['state'], 'complete')
        self.assertEqual(calls[0][2], 'verify')
        self.assertNotIn('--pids', calls[0])
        first_catalog.prepare_cards(self.root, 'original-card-followup', self.start, 'create', runner=runner)
        self.assertEqual(len(calls), 1)

    def test_model_attempt_cycle_does_not_reset_for_each_retry_job(self):
        self.fresh_supply()
        first_cycle = runtime._model_cycle(self.root)
        a = runtime._queue_supply(self.root, 'first_outreach_prepare_required')
        runtime._job_update(self.root, a, state='complete')
        b = runtime._queue_supply(self.root, 'first_outreach_prepare_required')
        self.assertNotEqual(a['request_id'], b['request_id'])
        self.assertEqual(runtime._model_cycle(self.root), first_cycle)
        with CycleStore(self.path) as store:
            store.publish(self.plan, 'live-br-campaign', self.now + 1, [])
        self.assertNotEqual(runtime._model_cycle(self.root), first_cycle)

    def test_one_catalog_read_fragment_preserves_original_run_path(self):
        calls = []
        def runner(command, **kwargs):
            calls.append(command)
            return SimpleNamespace(returncode=0, stdout=encoded({'status': 'paused'}))
        for _ in range(2):
            result = first_catalog.refresh(self.root, request_id='same-catalog-request', runner=runner,
                                           max_chunks=1, max_requests=5, timeout=180)
            self.assertEqual(result['reason'], 'first_catalog_read_budget')
        self.assertEqual(calls[0], calls[1])
        self.assertEqual(calls[0][-1], '5')


if __name__ == '__main__':
    unittest.main()
