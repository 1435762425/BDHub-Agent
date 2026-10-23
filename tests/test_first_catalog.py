import json
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from lib import first_catalog
from lib.creator_identity import CreatorIdentityStore
from lib.second_cycle import CycleError, CycleStore


class FirstCatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.now = time.time()
        self.s = CycleStore(self.root / 'var/second-cycle.sqlite', lambda: self.now)
        self.plan = self.s.plan('bjn-local-research', 'br')
        self.item = {'pid': '123', 'offerKey': 'campaign:123:456', 'campaignId': '456',
                     'catalogSource': 'campaign', 'title': 'Batom hidratante', 'available': True,
                     'endAt': self.now + 86400, 'evidenceRef': 'catalog-ref',
                     'sample': {'supported': True, 'quota': 3, 'observedAt': self.now, 'evidenceRef': 'sample-ref'}}
        self.s.publish(self.plan, 'live-br-campaign', self.now, [self.item])
        with CreatorIdentityStore(self.root / 'var/creator-identities.sqlite') as identities:
            from datetime import datetime, timezone
            identities.observe_profile('br', '111', 'creator111', datetime.fromtimestamp(self.now, timezone.utc).isoformat(), 'profile-ref',
                 {'fields': {'industry_groups': {'status': 'value', 'value': [{'id': '601450', 'label': 'Beauty', 'weight': '1'}]}}})
        self.calls = []

    def tearDown(self):
        self.s.close()
        self.temp.cleanup()

    def model(self, messages, **kwargs):
        self.calls.append(messages)
        return {'content': json.dumps({'products': [{'pid': '123', 'categoryId': '601450'}]})}

    def test_projection_is_attributable_and_leaves_platform_catalog_unchanged(self):
        result = first_catalog.enrich(self.root, model=self.model)
        self.assertEqual((result['classified'], result['platformWrites']), (1, 0))
        enriched = first_catalog.offers(self.s, self.plan)[0][1]
        self.assertEqual((enriched['categoryId'], enriched['categoryMethod']), ('601450', 'ai_title_v1'))
        self.assertTrue(enriched['categoryEvidenceRef'].startswith('first-category-'))
        self.assertEqual(self.s._offers(self.plan)[0][1], self.item)
        first_catalog.enrich(self.root, model=self.model)
        self.assertEqual(len(self.calls), 1)

    def test_model_cannot_invent_category_ids_or_products(self):
        for output in ({'products': [{'pid': '123', 'categoryId': 'invented'}]},
                       {'products': [{'pid': 'another', 'categoryId': '601450'}]},
                       {'products': []}):
            with self.subTest(output=output):
                with self.assertRaisesRegex(CycleError, 'output_invalid'):
                    first_catalog._classifications({'content': json.dumps(output)}, [{'pid': '123'}], [{'id': '601450', 'name': 'Beauty'}])

    def test_ambiguous_classification_is_retained_without_becoming_a_match(self):
        result = first_catalog.enrich(self.root, model=lambda *a, **k: {'content': json.dumps({'products': [{'pid': '123', 'categoryId': None}]})})
        self.assertEqual(result['classified'], 0)
        self.assertNotIn('categoryId', first_catalog.offers(self.s, self.plan)[0][1])

    def test_title_change_does_not_reuse_old_semantic_classification(self):
        first_catalog.enrich(self.root, model=self.model)
        self.s.publish(self.plan, 'live-br-campaign', self.now + 1, [self.item | {'title': 'Power tool'}])
        self.assertNotIn('categoryId', first_catalog.offers(self.s, self.plan)[0][1])

    def test_new_observed_vocabulary_can_reclassify_previously_ambiguous_title(self):
        first_catalog.enrich(self.root, model=lambda *a, **k: {'content': json.dumps({'products': [{'pid': '123', 'categoryId': None}]})})
        new_vocabulary = [{'id': '601450', 'name': 'Beauty'}, {'id': '602000', 'name': 'Fashion'}]
        with patch.object(first_catalog, 'taxonomy', return_value=new_vocabulary):
            first_catalog.enrich(self.root, model=self.model)
        self.assertEqual(first_catalog.offers(self.s, self.plan)[0][1]['categoryId'], '601450')

    def test_no_samples_does_not_spend_a_model_request(self):
        self.s.publish(self.plan, 'live-br-campaign', self.now + 1, [self.item | {'sample': self.item['sample'] | {'quota': 0}}])
        self.assertEqual(first_catalog.enrich(self.root, model=self.model)['classified'], 0)
        self.assertEqual(self.calls, [])

    def test_interrupted_model_intent_is_not_reissued(self):
        class LostResponse(Exception):
            outcome = 'outcome_unknown'
            code = 'provider_timeout'
        def lost(*a, **k):
            self.calls.append('lost')
            raise LostResponse()
        self.assertEqual(first_catalog.enrich(self.root, model=lost)['state'], 'partial')
        self.assertEqual(first_catalog.enrich(self.root, model=lost)['state'], 'partial')
        self.assertEqual(self.calls, ['lost'])

    def test_refresh_resumes_exact_original_run_and_never_joins_campaigns(self):
        calls = []
        def runner(argv, **kwargs):
            calls.append(argv)
            return SimpleNamespace(returncode=0, stdout=json.dumps({'status': 'paused' if len(calls) == 1 else 'completed', 'offers': 1}))
        result = first_catalog.refresh(self.root, request_id='catalog-request-123', runner=runner)
        self.assertEqual(result['state'], 'complete')
        self.assertEqual(calls[0], calls[1])
        self.assertTrue(calls[0][1].endswith('/scripts/sync-cycle-catalog.py'))
        self.assertNotIn('--join', calls[0])
        calls.clear()
        first_catalog.refresh(self.root, request_id='catalog-request-456', runner=runner, stopped=lambda: True)
        self.assertEqual(calls, [])

    def test_classification_fragment_keeps_completed_batches_and_resumes_remaining_products(self):
        self.s.publish(self.plan, 'live-br-campaign', self.now + 1,
                       [self.item | {'pid': str(123 + i), 'offerKey': 'offer-' + str(i)} for i in range(6)])
        calls = []
        def model(messages, **kwargs):
            inputs = json.loads(messages[1]['content'])
            calls.append(inputs['products'])
            return {'content': json.dumps({'products': [{'pid': row['pid'], 'categoryId': '601450'} for row in inputs['products']]})}
        first_pass = first_catalog.enrich(self.root, model=model, max_batches=1)
        second_pass = first_catalog.enrich(self.root, model=model, max_batches=1)
        self.assertEqual((first_pass['state'], first_pass['classified']), ('paused', 5))
        self.assertEqual((second_pass['state'], second_pass['classified']), ('complete', 1))
        self.assertEqual([len(batch) for batch in calls], [5, 1])

    def test_sample_name_fragments_use_existing_intents_and_do_not_repeat_unknown_model_calls(self):
        self.s.publish(self.plan, 'live-br-campaign', self.now + 1,
                       [self.item | {'pid': str(123 + i), 'offerKey': 'offer-' + str(i),
                                     'categoryId': str(601450 + i)} for i in range(6)])
        calls = []
        def model(messages, **kwargs):
            inputs = json.loads(messages[1]['content'])
            calls.append(inputs['items'])
            return {'content': json.dumps({'items': [{'ref': row['ref'], 'shortName': 'Batom',
                      'mention': 'este batom', 'shortNameZh': '口红'} for row in inputs['items']]})}
        self.assertEqual(first_catalog.prepare_names(self.root, model=model)['state'], 'paused')
        self.assertEqual(first_catalog.prepare_names(self.root, model=model)['state'], 'complete')
        self.assertEqual(first_catalog.prepare_names(self.root, model=model)['prepared'], 0)
        self.assertEqual([len(batch) for batch in calls], [5, 1])
        self.s.publish(self.plan, 'live-br-campaign', self.now + 2,
                       [self.item | {'pid': '999', 'categoryId': '601450', 'offerKey': 'unknown-offer'}])
        unknown = []
        def lost(*args, **kwargs):
            unknown.append('submitted')
            raise RuntimeError('response unavailable')
        self.assertEqual(first_catalog.prepare_names(self.root, model=lost)['state'], 'partial')
        resumed = first_catalog.prepare_names(self.root, model=lost)
        self.assertEqual((unknown, resumed['unresolvedNames']), (['submitted'], 1))

    def test_cross_market_actions_are_refused_before_model_or_platform(self):
        with self.assertRaisesRegex(CycleError, 'market_unsupported'):
            first_catalog.enrich(self.root, 'my', model=self.model)
        with self.assertRaisesRegex(CycleError, 'request_invalid'):
            first_catalog.refresh(self.root, 'my', request_id='catalog-request-123')
        self.assertEqual(self.calls, [])


if __name__ == '__main__':
    unittest.main()
