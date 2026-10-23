import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from lib import first_catalog, first_outreach_runtime as runtime, sample_operations as samples
from lib.creator_identity import CreatorIdentityStore
from lib.cycle_materials import Materials
from lib.draft_provider import DraftProviderError
from lib.second_cycle import CycleError, CycleStore, encoded


class FirstModelRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.path = self.root / 'var/second-cycle.sqlite'
        self.now = time.time()
        self.store = CycleStore(self.path)
        self.plan = self.store.plan('bjn-local-research', 'br')
        runtime.ensure_schema(self.store)
        self.store.db.execute("INSERT INTO first_outreach_control(plan_id,mode,revision,start_request,start_revision) VALUES(?,'continuous',1,'original-continue',1)", (self.plan,))
        with CreatorIdentityStore(self.root / 'var/creator-identities.sqlite') as identities:
            from datetime import datetime, timezone
            identities.observe_profile('br', '111', 'creator111', datetime.fromtimestamp(self.now, timezone.utc).isoformat(), 'profile-ref',
                {'fields': {'industry_groups': {'status': 'value', 'value': [{'id': '601450', 'label': 'Beauty', 'weight': '1'}]}}})

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def offers(self, pids, *, classified=False, at=0):
        rows = [{'pid': str(pid), 'offerKey': 'campaign:' + str(pid) + ':456', 'campaignId': '456',
                 'catalogSource': 'campaign', 'title': 'Batom ' + str(pid), 'available': True,
                 'endAt': self.now + 86400, 'evidenceRef': 'catalog-proof',
                 'sample': {'supported': True, 'quota': 3, 'observedAt': self.now, 'evidenceRef': 'sample-proof'},
                 **({'categoryId': str(601450 + i)} if classified else {})} for i, pid in enumerate(pids)]
        self.store.publish(self.plan, 'live-br-campaign', self.now + at, rows)
        return rows

    @staticmethod
    def category_model(messages, **kwargs):
        data = json.loads(messages[1]['content'])
        return {'content': encoded({'products': [{'pid': row['pid'], 'categoryId': '601450'} for row in data['products']]}),
                'responseId': 'original-response'}

    @staticmethod
    def name_model(messages, **kwargs):
        data = json.loads(messages[1]['content'])
        return {'content': encoded({'items': [{'ref': row['ref'], 'shortName': 'Batom', 'mention': 'este batom',
                                              'shortNameZh': '口红'} for row in data['items']]})}

    def due(self, table, job=None):
        self.store.db.execute('UPDATE first_model_retry SET next_at=0 WHERE table_name=?' + (' AND job_id=?' if job else ''),
                              (table, job) if job else (table,))

    def test_unsent_failure_retries_original_job_after_persisted_backoff(self):
        self.offers([123])
        calls = []
        def unsent(*args, **kwargs):
            calls.append('not-sent')
            raise DraftProviderError('provider_not_configured', outcome='request_not_sent')
        first = first_catalog.enrich(self.root, model=unsent)
        original = dict(self.store.db.execute('SELECT * FROM first_category_job').fetchone())
        self.assertEqual((first['state'], first['retryableBatches']), ('partial', 1))
        self.assertGreater(first['nextRetryAt'], time.time())
        first_catalog.enrich(self.root, model=unsent)
        self.assertEqual(calls, ['not-sent'])
        self.due('first_category_job')
        self.assertEqual(first_catalog.enrich(self.root, model=self.category_model)['state'], 'complete')
        current = dict(self.store.db.execute('SELECT * FROM first_category_job').fetchone())
        self.assertEqual((original['id'], original['inputs']), (current['id'], current['inputs']))
        meta = self.store.db.execute('SELECT * FROM first_model_retry').fetchone()
        self.assertEqual((meta['attempts'], meta['cycle_attempts']), (2, 1))
        self.assertEqual(json.loads(meta['history'])[0]['outcome'], 'request_not_sent')

    def test_unknown_batches_keep_members_and_three_attempt_budget_across_reordering(self):
        self.offers(range(123, 129))
        requests = []
        def model(messages, **kwargs):
            pids = [row['pid'] for row in json.loads(messages[1]['content'])['products']]
            requests.append(pids)
            if '123' in pids:
                raise DraftProviderError('provider_response_invalid', outcome='outcome_unknown', receipt={'responseId': 'unknown-' + str(len(requests))})
            return self.category_model(messages, **kwargs)
        first_catalog.enrich(self.root, model=model, max_batches=1, retry_cycle='catalog-1')
        original = dict(self.store.db.execute("SELECT * FROM first_category_job WHERE state='retry_wait'").fetchone())
        second = first_catalog.enrich(self.root, model=model, max_batches=1, retry_cycle='catalog-1')
        self.assertEqual((second['state'], second['classified']), ('partial', 1))
        self.offers([100, *range(123, 129)], at=1)
        first_catalog.enrich(self.root, model=model, max_batches=1, retry_cycle='catalog-1')
        self.assertEqual(requests[-1], ['100'])
        for _ in range(2):
            self.due('first_category_job', original['id'])
            first_catalog.enrich(self.root, model=model, retry_cycle='catalog-1')
        exhausted = first_catalog.enrich(self.root, model=model, retry_cycle='catalog-1')
        self.assertEqual((exhausted['state'], exhausted['isolatedBatches']), ('partial', 1))
        self.assertEqual(sum('123' in group for group in requests), 3)
        self.assertTrue(all(group == ['123', '124', '125', '126', '127'] for group in requests if '123' in group))
        self.assertEqual(self.store.db.execute('SELECT inputs FROM first_category_job WHERE id=?', (original['id'],)).fetchone()[0], original['inputs'])
        final = first_catalog.enrich(self.root, model=self.category_model, retry_cycle='catalog-2')
        self.assertEqual(final['state'], 'complete')
        history = json.loads(self.store.db.execute('SELECT history FROM first_model_retry WHERE job_id=?', (original['id'],)).fetchone()[0])
        self.assertEqual([entry['outcome'] for entry in history], ['outcome_unknown'] * 3 + ['confirmed'])

    def test_saved_original_response_replays_after_crash_without_new_model_call(self):
        self.offers([123])
        def crash(*args, **kwargs):
            raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            first_catalog.enrich(self.root, model=crash)
        original = self.store.db.execute('SELECT * FROM first_category_job').fetchone()
        inputs = json.loads(original['inputs'])
        receipt = self.category_model([{}, {'content': encoded(inputs)}])
        self.store.db.execute("UPDATE first_category_job SET state='response_saved',response=?", (encoded(receipt),))
        result = first_catalog.enrich(self.root, model=lambda *args, **kwargs: self.fail('saved response must not call provider'))
        self.assertEqual((result['state'], result['classified']), ('complete', 1))

    def test_crash_without_response_retains_unknown_then_uses_same_batch_after_backoff(self):
        self.offers([123])
        with self.assertRaises(KeyboardInterrupt):
            first_catalog.enrich(self.root, model=lambda *args, **kwargs: (_ for _ in ()).throw(KeyboardInterrupt()))
        result = first_catalog.enrich(self.root, model=lambda *args, **kwargs: self.fail('crash backoff must run before another attempt'))
        self.assertEqual(result['state'], 'partial')
        self.due('first_category_job')
        self.assertEqual(first_catalog.enrich(self.root, model=self.category_model)['state'], 'complete')
        history = json.loads(self.store.db.execute('SELECT history FROM first_model_retry').fetchone()[0])
        self.assertEqual(history[0]['outcome'], 'outcome_unknown')

    def test_live_first_model_owner_prevents_another_call_of_the_same_job(self):
        self.offers([123])
        nested = []
        def model(messages, **kwargs):
            with patch('lib.model_preparation_retry.pid_alive', return_value=True):
                nested.append(first_catalog.enrich(self.root, model=lambda *args, **options: self.fail('live model call duplicated')))
            return self.category_model(messages, **kwargs)
        result = first_catalog.enrich(self.root, model=model)
        self.assertEqual(result['state'], 'complete')
        self.assertEqual((nested[0]['state'], nested[0]['retryableBatches']), ('partial', 1))
        self.assertEqual(self.store.db.execute('SELECT attempts FROM first_model_retry').fetchone()[0], 1)

    def test_legacy_name_call_waits_for_deadline_and_live_executor_then_replays_late_receipt(self):
        offers = self.offers([123], classified=True)
        from lib.cycle_materials import name_key
        from lib.second_cycle import digest
        Materials(self.store, 'br')
        job = 'names-' + digest([name_key(offers[0], 'br')])
        self.store.db.execute('INSERT INTO cycle_name_job VALUES(?,?,?,?,?)', (job, 'request_started', encoded(offers), None, None))
        self.store.db.execute('INSERT INTO cycle_name_reservation VALUES(?,?)', (name_key(offers[0], 'br'), job))
        blocked = lambda *args, **kwargs: self.fail('original caller is still active')
        initial = first_catalog.prepare_names(self.root, model=blocked)
        self.assertGreater(initial['nextRetryAt'], time.time() + 100)
        self.due('cycle_name_job')
        with patch('lib.model_preparation_retry.legacy_names_executor_alive', return_value=True):
            waiting = first_catalog.prepare_names(self.root, model=blocked)
        self.assertEqual(waiting['state'], 'partial')
        self.assertEqual(self.store.db.execute('SELECT state FROM cycle_name_job').fetchone()[0], 'request_started')
        self.assertEqual(self.store.db.execute('SELECT attempts FROM first_model_retry').fetchone()[0], 0)
        receipt = self.name_model([{}, {'content': encoded({'items': [{'ref': '0', 'title': offers[0]['title']}]})}])
        self.store.db.execute("UPDATE cycle_name_job SET state='response_saved',response=?", (encoded(receipt),))
        result = first_catalog.prepare_names(self.root, model=blocked)
        self.assertEqual(result['state'], 'complete')
        self.assertEqual(self.store.db.execute('SELECT state FROM cycle_name_job').fetchone()[0], 'response_saved')

    def test_expired_legacy_name_call_can_retry_only_after_original_executor_is_gone(self):
        offers = self.offers([123], classified=True)
        from lib.cycle_materials import name_key
        from lib.second_cycle import digest
        Materials(self.store, 'br')
        job = 'names-' + digest([name_key(offers[0], 'br')])
        self.store.db.execute('INSERT INTO cycle_name_job VALUES(?,?,?,?,?)', (job, 'request_started', encoded(offers), None, None))
        self.store.db.execute('INSERT INTO cycle_name_reservation VALUES(?,?)', (name_key(offers[0], 'br'), job))
        first_catalog.prepare_names(self.root, model=lambda *args, **kwargs: self.fail('first-seen window'))
        self.due('cycle_name_job')
        with patch('lib.model_preparation_retry.legacy_names_executor_alive', return_value=False):
            first_catalog.prepare_names(self.root, model=lambda *args, **kwargs: self.fail('unknown result needs backoff'))
        self.due('cycle_name_job')
        self.assertEqual(first_catalog.prepare_names(self.root, model=self.name_model)['state'], 'complete')

    def test_name_recovery_is_opt_in_keeps_original_schema_and_reservations(self):
        rows = self.offers(range(123, 129), classified=True)
        def unknown(*args, **kwargs):
            raise DraftProviderError('provider_response_invalid', outcome='outcome_unknown', receipt={'responseId': 'unknown-name'})
        first = first_catalog.prepare_names(self.root, model=unknown, retry_cycle='catalog-1')
        self.assertEqual(first['state'], 'paused')
        original = self.store.db.execute('SELECT * FROM cycle_name_job').fetchone()
        self.assertEqual(len(self.store.db.execute('PRAGMA table_info(cycle_name_job)').fetchall()), 5)
        # A resident caller still cannot retry an unknown using the default API.
        with self.assertRaisesRegex(CycleError, 'previous_request'):
            Materials(self.store, 'br').prepare_names(json.loads(original['inputs']), self.name_model)
        second = first_catalog.prepare_names(self.root, model=self.name_model, retry_cycle='catalog-1')
        self.assertEqual((second['state'], second['prepared']), ('partial', 1))
        self.due('cycle_name_job', original['id'])
        final = first_catalog.prepare_names(self.root, model=self.name_model, retry_cycle='catalog-1')
        self.assertEqual((final['state'], final['prepared']), ('complete', 5))
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM cycle_name_job').fetchone()[0], 2)
        self.assertEqual(self.store.db.execute('SELECT inputs FROM cycle_name_job WHERE id=?', (original['id'],)).fetchone()[0], original['inputs'])
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM cycle_name_reservation').fetchone()[0], len(rows))
        # The old five-value INSERT remains valid after first-only retry setup.
        self.store.db.execute('INSERT INTO cycle_name_job VALUES(?,?,?,?,?)', ('legacy-resident', 'ready', '[]', '{}', None))

    def test_name_unknown_cycle_budget_is_three_and_next_supply_cycle_reuses_original(self):
        self.offers([123], classified=True)
        calls = []
        def unknown(*args, **kwargs):
            calls.append('unknown')
            raise DraftProviderError('provider_response_invalid', outcome='outcome_unknown', receipt={'responseId': 'unknown-' + str(len(calls))})
        for _ in range(3):
            first_catalog.prepare_names(self.root, model=unknown, retry_cycle='catalog-1')
            self.due('cycle_name_job')
        original = dict(self.store.db.execute('SELECT * FROM cycle_name_job').fetchone())
        isolated = first_catalog.prepare_names(self.root, model=unknown, retry_cycle='catalog-1')
        self.assertEqual((calls, isolated['isolatedBatches']), (['unknown'] * 3, 1))
        self.assertEqual(first_catalog.prepare_names(self.root, model=self.name_model, retry_cycle='catalog-2')['state'], 'complete')
        current = self.store.db.execute('SELECT * FROM cycle_name_job').fetchone()
        self.assertEqual((current['id'], current['inputs']), (original['id'], original['inputs']))
        history = json.loads(self.store.db.execute('SELECT history FROM first_model_retry').fetchone()[0])
        self.assertEqual([entry['outcome'] for entry in history], ['outcome_unknown'] * 3 + ['confirmed'])

    def test_partial_batch_status_is_readonly_and_exposes_retry_time(self):
        self.offers([123])
        first_catalog.enrich(self.root, model=lambda *args, **kwargs: (_ for _ in ()).throw(DraftProviderError('provider_not_configured')))
        with CycleStore(self.path, readonly=True) as ro:
            status = runtime._automation_status(ro, runtime.first.control(ro))
            self.assertEqual(ro.db.total_changes, 0)
        self.assertEqual(status['supply']['state'], 'partial')
        self.assertEqual(status['supply']['reason'], 'first_model_batches_retrying:1')
        self.assertGreater(status['supply']['nextRunAt'], time.time())

    def test_goal_observed_projects_real_fulfilment_without_counting_our_approval(self):
        app = {'applicationId': '9001', 'oecId': '111', 'platformCreatorId': '222', 'pid': '123', 'campaignId': '456',
               'state': 'content_pending', 'paymentRequired': False}
        samples.store_records(self.store, 'br', 'acc2', [app], evidence_ref='real-stage', complete=True)
        sample = samples.status(self.root)
        sample['counts'] = {'goal_observed': 1}
        sample['tracking'] = [{'applicationId': app['applicationId'], 'reviewState': 'goal_observed', 'platformStage': 'completed'}]
        row = {'application_id': app['applicationId'], 'payload': encoded(app), 'observed': self.now, 'review_state': 'goal_observed'}
        with patch.object(samples, 'status', return_value=sample), patch.object(runtime, '_application_rows', return_value=[row]):
            result = runtime.status(self.root)
        self.assertEqual(result['applications'][0]['state'], 'completed')
        self.assertEqual(result['counts']['approved'], 0)


if __name__ == '__main__':
    unittest.main()
