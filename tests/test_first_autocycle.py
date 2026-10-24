"""The durable first-outreach loop advances without creating a new grant."""
import importlib.util
import json
import sqlite3
from pathlib import Path
import sys
import tempfile
import time
from contextlib import closing
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

    def test_only_forward_local_matching_with_another_page_gets_a_short_continuation(self):
        supply = {'state': 'paused', 'hasMoreLocalWork': True,
                  'localProgress': {'fromOec': '100', 'toOec': '200', 'scanned': 500}}
        result = {'state': 'waiting_window', 'supply': supply}
        self.assertEqual(runtime.worker_delay(result, 5), 0.25)
        self.assertEqual(runtime.worker_delay(result | {'state': 'attention', 'reason': 'first_outreach_catalog_changed'}, 5), 0.25)
        self.assertEqual(runtime.worker_delay(result | {'realSends': 1}, 5), 0.5)
        for state in ('stopped', 'paused', 'waiting_account', 'waiting_capacity', 'confirmed'):
            with self.subTest(state=state):
                self.assertEqual(runtime.worker_delay(result | {'state': state}, 5), 5)
        self.assertEqual(runtime.worker_delay(result | {'state': 'waiting_reconciliation'}, 5), 60)
        self.assertEqual(runtime.worker_delay(result | {'state': 'attention', 'reason': 'first_outreach_result_unknown'}, 5), 5)
        for progress in ({'fromOec': '100', 'toOec': '200', 'scanned': 0},
                         {'fromOec': '100', 'toOec': '100', 'scanned': 500},
                         {'fromOec': '200', 'toOec': '100', 'scanned': 500},
                         {'fromOec': '100', 'toOec': '200', 'scanned': True}):
            with self.subTest(progress=progress):
                self.assertEqual(runtime.worker_delay(result | {'supply': supply | {'localProgress': progress}}, 5), 5)
        self.assertEqual(runtime.worker_delay(result | {'supply': supply | {'hasMoreLocalWork': False}}, 5), 5)
        self.assertEqual(runtime.worker_delay(result | {'supply': supply | {'state': 'complete'}}, 5), 5)

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

    def test_unchanged_materials_back_off_persistently_without_new_intents_or_grants(self):
        self.fresh_supply()
        original = runtime._queue_supply(self.root, 'first_outreach_prepare_required')
        control = runtime._cfg(self.root)
        with patch.object(runtime, '_cards', return_value={'state': 'complete', 'remaining': 2}):
            for unchanged, delay in enumerate((30, 60, 120, 240, 480, 960, 1920, 3600, 3600)):
                runtime._job_update(self.root, self.record(), stage=6)
                result = runtime.process_job(self.root, self.record())
                saved = self.record()  # Reopen the database, as after a worker restart.
                progress = json.loads(saved['material_progress'])
                self.assertEqual((result['retryAfterSeconds'], progress['unchanged']), (delay, unchanged))
                self.assertEqual((saved['request_id'], saved['payload']), (original['request_id'], original['payload']))
                self.assertGreater(saved['next_attempt'], time.time())
                self.assertIsNone(runtime._claim(self.root))
        self.assertEqual(runtime._cfg(self.root), control)
        self.assertEqual(saved['error'], 'first_outreach_materials_no_progress')
        with CycleStore(self.path, readonly=True) as store:
            self.assertEqual(store.db.execute('SELECT count(*) FROM first_outreach_job').fetchone()[0], 1)
            self.assertEqual(store.db.execute('SELECT count(*) FROM first_outreach_request').fetchone()[0], 1)

    def test_material_progress_or_new_catalog_evidence_restores_normal_delay(self):
        self.fresh_supply()
        runtime._queue_supply(self.root, 'first_outreach_prepare_required')
        with patch.object(runtime, '_cards', return_value={'state': 'complete', 'remaining': 2}) as cards:
            for _ in range(2):
                runtime._job_update(self.root, self.record(), stage=6)
                runtime.process_job(self.root, self.record())
            self.assertEqual(json.loads(self.record()['material_progress'])['unchanged'], 1)
            cards.return_value = {'state': 'complete', 'remaining': 1}
            runtime._job_update(self.root, self.record(), stage=6)
            self.assertEqual(runtime.process_job(self.root, self.record())['retryAfterSeconds'], 30)
            runtime._job_update(self.root, self.record(), stage=6)
            self.assertEqual(runtime.process_job(self.root, self.record())['retryAfterSeconds'], 60)
            with CycleStore(self.path) as store:
                store.publish(self.plan, 'live-br-campaign', self.now + 1, [])
            runtime._job_update(self.root, self.record(), stage=6)
            self.assertEqual(runtime.process_job(self.root, self.record())['retryAfterSeconds'], 30)

    def classified_materials(self):
        self.fresh_supply()
        job = runtime._queue_supply(self.root, 'first_outreach_prepare_required')
        runtime._job_update(self.root, job, stage=6)
        folder = self.root / 'var/first-outreach';folder.mkdir()
        report = folder / 'original-materials.json'
        report.write_text(json.dumps({'summary': {'runId': 'original-material-run'}}))
        offers = [{'pid': str(100 + n), 'campaignId': str(200 + n), 'catalogSource': 'campaign',
                   'offerKey': 'offer-' + str(n), 'creatorPercent': None if n == 0 else '13'} for n in range(4)]
        with closing(sqlite3.connect(self.root / 'var/catalog-links.sqlite')) as db, db:
            db.executescript('''CREATE TABLE catalog_prepare_item(run_id,pid,campaign_id,catalog_source,state,error,blocker,intent_id,updated);
                CREATE TABLE catalog_link_intent(id,pid,spec,state,receipt,readback);''')
            proof = {'reason': 'superseded_by_live_offer_change', 'platformCreateAttempts': 0, 'observedAt': self.now}
            db.execute('INSERT INTO catalog_link_intent VALUES(?,?,?,?,?,?)',
                       ('original-intent', offers[1]['pid'], json.dumps({'market': 'br'}), 'superseded', None, json.dumps(proof)))
            for number, offer in enumerate(offers):
                db.execute('INSERT INTO catalog_prepare_item VALUES(?,?,?,?,?,?,?,?,?)',
                           ('original-material-run', offer['pid'], offer['campaignId'], 'campaign',
                            'missing' if number < 2 else 'ready', 'original-error', None,
                            'original-intent' if number == 1 else None, self.now))
        result = {'state': 'complete', 'remaining': 3, 'reportRef': str(report.relative_to(self.root))}
        def coverage(_root, _store, _market, targets):
            ready = {offer['offerKey'] for offer in targets if offer['offerKey'] == 'offer-3'}
            return {'readyOfferKeys': ready, 'missingNames': 0, 'missingLinks': len(targets) - len(ready)}
        return offers, result, coverage

    def test_classified_gaps_close_only_the_pass_and_keep_intents_and_eligibility(self):
        offers, result, coverage = self.classified_materials()
        before = runtime._cfg(self.root);job = self.record()
        with closing(sqlite3.connect(self.root / 'var/catalog-links.sqlite')) as db, db:
            original = db.execute('SELECT * FROM catalog_link_intent').fetchall()
        with patch.object(first, 'material_offers', return_value=offers), \
             patch.object(first, '_materials', side_effect=coverage), patch.object(runtime, '_cards', return_value=result):
            outcome = runtime.process_job(self.root, job)
            with CycleStore(self.path, readonly=True) as store:
                supply = runtime._automation_status(store, first.control(store))['supply']
        self.assertEqual(outcome['state'], 'partial')
        self.assertEqual((self.record()['state'], self.record()['error']),
                         ('complete', 'first_outreach_materials_waiting_evidence'))
        self.assertEqual((supply['state'], supply['materials']['readyOffers'], supply['materials']['remaining']), ('partial', 1, 3))
        self.assertEqual(supply['materials']['actionable'], 0)
        self.assertEqual(sum(supply['materials']['gapCounts'].values()), 3)
        self.assertEqual(supply['materials']['gapCounts']['commission_terms_unavailable'], 1)
        self.assertEqual(supply['materials']['gapCounts']['prior_intent_superseded'], 1)
        self.assertEqual(supply['materials']['gapCounts']['binding_not_current'], 1)
        self.assertEqual((self.record()['request_id'], self.record()['payload']), (job['request_id'], job['payload']))
        self.assertEqual(runtime._cfg(self.root), before)
        with closing(sqlite3.connect(self.root / 'var/catalog-links.sqlite')) as db, db:
            self.assertEqual(db.execute('SELECT * FROM catalog_link_intent').fetchall(), original)

    def test_submitted_original_intent_never_closes_as_a_classified_terminal_gap(self):
        offers, result, coverage = self.classified_materials()
        with closing(sqlite3.connect(self.root / 'var/catalog-links.sqlite')) as db, db:
            db.execute("UPDATE catalog_link_intent SET state='unknown'")
        with patch.object(first, 'material_offers', return_value=offers), \
             patch.object(first, '_materials', side_effect=coverage), patch.object(runtime, '_cards', return_value=result):
            outcome = runtime.process_job(self.root, self.record())
        self.assertEqual(outcome['state'], 'paused')
        self.assertEqual(self.record()['state'], 'running')
        self.assertEqual(outcome['materials']['gapCounts']['original_intent_pending'], 1)
        self.assertEqual(outcome['materials']['actionable'], 1)
        with closing(sqlite3.connect(self.root / 'var/catalog-links.sqlite')) as db, db:
            self.assertEqual(db.execute('SELECT state FROM catalog_link_intent').fetchone()[0], 'unknown')

    def test_known_zero_commission_is_not_classified_as_a_missing_field(self):
        offers, result, coverage = self.classified_materials()
        for value in (0, '0'):
            with self.subTest(value=value), patch.object(first, 'material_offers', return_value=offers), \
                 patch.object(first, '_materials', side_effect=coverage), patch.object(runtime, '_cards', return_value=result):
                offers[0]['creatorPercent'] = value
                runtime._job_update(self.root, self.record(), stage=6)
                outcome = runtime.process_job(self.root, self.record())
            self.assertEqual(outcome['materials']['gapCounts']['commission_terms_unavailable'], 0)
            self.assertEqual(outcome['materials']['gapCounts']['material_preparation_pending'], 1)
            self.assertEqual(self.record()['state'], 'running')

    def test_material_signature_ignores_repeated_read_timestamps_but_stop_takes_precedence(self):
        offers, result, coverage = self.classified_materials()
        with patch.object(first, 'material_offers', return_value=offers), patch.object(first, '_materials', side_effect=coverage):
            before = runtime._material_snapshot(self.root, self.record(), result)
            with closing(sqlite3.connect(self.root / 'var/catalog-links.sqlite')) as db, db:
                db.execute('UPDATE catalog_prepare_item SET updated=updated+300')
                proof = {'reason': 'superseded_by_live_offer_change', 'platformCreateAttempts': 0, 'observedAt': self.now + 300}
                db.execute('UPDATE catalog_link_intent SET readback=?', (json.dumps(proof),))
            after = runtime._material_snapshot(self.root, self.record(), result)
        self.assertEqual(before['signature'], after['signature'])
        stopped = [False];job = self.record()
        def cards(*_args):
            stopped[0] = True
            return result
        with patch.object(runtime, '_cards', side_effect=cards), \
             patch.object(runtime, '_material_snapshot', side_effect=AssertionError('stopped_must_not_advance')):
            outcome = runtime.process_job(self.root, job, stopped=lambda: stopped[0])
        self.assertEqual(outcome['reason'], 'first_outreach_job_stopped')
        self.assertEqual(self.record()['stage'], job['stage'])
        self.assertIsNone(self.record()['material_progress'])

    def test_original_six_hour_bound_retains_remaining_gap_evidence(self):
        self.fresh_supply()
        runtime._queue_supply(self.root, 'first_outreach_prepare_required')
        runtime._job_update(self.root, self.record(), stage=6)
        with CycleStore(self.path) as store:
            store.db.execute('UPDATE first_outreach_job SET created=created-?', (runtime.SUPPLY_REFRESH_SECONDS + 1,))
        with patch.object(runtime, '_cards', return_value={'state': 'complete', 'remaining': 2}):
            outcome = runtime.process_job(self.root, self.record())
        self.assertEqual((outcome['state'], self.record()['state']), ('partial', 'complete'))
        self.assertEqual(json.loads(self.record()['material_progress'])['remaining'], 2)

    def test_six_hour_pass_can_close_but_next_maintenance_keeps_original_unknown_for_verify(self):
        offers, result, coverage = self.classified_materials()
        with closing(sqlite3.connect(self.root / 'var/catalog-links.sqlite')) as db, db:
            db.execute("UPDATE catalog_link_intent SET state='unknown'")
            original = db.execute('SELECT * FROM catalog_link_intent').fetchall()
        with CycleStore(self.path) as store:
            store.db.execute('UPDATE first_outreach_job SET created=created-?', (runtime.SUPPLY_REFRESH_SECONDS + 1,))
        with patch.object(first, 'material_offers', return_value=offers), \
             patch.object(first, '_materials', side_effect=coverage), patch.object(runtime, '_cards', return_value=result):
            outcome = runtime.process_job(self.root, self.record())
        prior = self.record()
        self.assertEqual((outcome['state'], prior['state']), ('partial', 'complete'))
        self.assertEqual(outcome['materials']['gapCounts']['original_intent_pending'], 1)
        with CycleStore(self.path) as store:
            store.publish(self.plan, 'live-br-campaign', self.now + 1, [])
        following = runtime._queue_supply(self.root)
        self.assertEqual(json.loads(following['payload'])['firstStart'], self.start)
        runtime._job_update(self.root, following, stage=5)
        with patch.object(runtime, '_cards', return_value={'state': 'complete', 'remaining': 3}) as cards:
            runtime.process_job(self.root, self.record())
        self.assertEqual(cards.call_args.args[2], 'verify')
        with closing(sqlite3.connect(self.root / 'var/catalog-links.sqlite')) as db:
            self.assertEqual(db.execute('SELECT * FROM catalog_link_intent').fetchall(), original)

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
