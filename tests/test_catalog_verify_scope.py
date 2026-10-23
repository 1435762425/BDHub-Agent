"""Original market/account recovery contracts; isolated SQLite and fake HTTP only."""
import importlib.util
import json
import sqlite3
import sys
import tempfile
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
module_spec = importlib.util.spec_from_file_location('catalog_verify_scope_cli', ROOT / 'scripts/catalog-link-prepare.py')
prepare = importlib.util.module_from_spec(module_spec)
module_spec.loader.exec_module(prepare)

from lib.catalog_links import CatalogLinks
from lib.catalog_prepare import CatalogPreparation, CARD, MEMBERS
from lib.schema_migrations import apply_database


class ReadTransport:
    def __init__(self, *, market='it', account='acc9', after_read=None):
        self.identity = SimpleNamespace(market=market)
        self._account = SimpleNamespace(name=account)
        self.after_read = after_read
        self.requests = []

    def _params(self):
        return {}

    def _xhr(self, *, method, path, params, payload, write):
        if method != 'GET' or write:
            raise AssertionError('verification_must_not_write')
        self.requests.append((path, params))
        if path == CARD:
            data = {'total': 1, 'list': [{'product_list_id': '789', 'campaign_id': '0',
                    'product_list_name': 'BJN test', 'campaign_products': [
                        {'product_id': '123', 'creator_commission_percent': 1300}]}]}
        elif path == MEMBERS:
            data = {'total_num': 1, 'campaign_products': [{'product_id': '123',
                    'campaign_id': '456', 'creator_commission_percent': 1300,
                    'plan_commission_percent': 1000, 'product_status': 2, 'stock': 500}]}
            if self.after_read:
                self.after_read()
        else:
            raise AssertionError(path)
        return SimpleNamespace(payload={'code': 0, 'data': data})

    def require_read(self, response):
        return response.payload


class CreateTransport(ReadTransport):
    def __init__(self, mode):
        super().__init__(account='acc6')
        self.mode = mode
        self.writes = []
        self.members = 0
        self.session = SimpleNamespace(close=lambda: None)

    def fork_lane(self, pace):
        return self

    def _xhr(self, *, method, path, params, payload, write):
        if write:
            if method != 'POST' or path != prepare.CREATE:
                raise AssertionError('unexpected_fake_write')
            self.writes.append(payload)
            return SimpleNamespace(http_status=200, code=0, has_turing=False, ambiguous=False,
                                   payload={'code': 0, 'data': {'list': {
                                       'url': 'https://affiliate.tiktok.com/test', 'product_list_id': '789'}}})
        if path == CARD and self.mode != 'existing' and not self.writes:
            self.requests.append((path, params))
            return SimpleNamespace(payload={'code': 0, 'data': {'total': 0, 'list': []}})
        if path == MEMBERS:
            self.members += 1
            if self.mode == 'unknown' and self.members <= 3:
                self.requests.append((path, params))
                return SimpleNamespace(payload={'code': 0, 'data': {'total_num': 0}})
        return super()._xhr(method=method, path=path, params=params, payload=payload, write=write)


class VerifyScopeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'var').mkdir()
        (self.root / 'config').mkdir()
        (self.root / 'config/catalog-link-policy.json').write_text(
            (ROOT / 'config/catalog-link-policy.json').read_text())
        sqlite3.connect(self.root / 'var/catalog-links.sqlite').close()
        apply_database(self.root, 'catalog-links')
        self.prep = CatalogPreparation(self.root)
        self.addCleanup(self.prep.close)
        self.ledger = CatalogLinks(self.root)
        self.addCleanup(self.ledger.db.close)
        for active in (patch.object(prepare, 'ROOT', self.root),
                       patch.dict(prepare.SCOPE, {'market': 'it', 'account': 'acc9', 'route': 'selected'}, clear=True)):
            active.start()
            self.addCleanup(active.stop)

    def add_intent(self, *, market='it', account='acc9', state='unknown', suffix=''):
        run_id = 'run-' + market + account + suffix
        intent_id = 'intent-' + market + account + suffix
        scope = {'market': market, 'account': account, 'route': 'selected'}
        offer = {'pid': '123', 'campaignId': '456', 'catalogSource': 'selected',
                 'creatorPercent': '13', 'publicPercent': '10', 'totalPercent': '15'}
        spec = {'market': market, 'account': account, 'route': 'selected', 'pid': '123',
                'campaignId': '456', 'creatorPercent': '13', 'listName': 'BJN test',
                'purpose': 'catalog_batch_link', 'policyVersion': 'commission-1-to-2-v1',
                'namingVersion': 'link-naming-v1', 'offer': offer, 'sourceRun': run_id}
        with self.prep.db:
            self.prep.db.execute('INSERT INTO catalog_prepare_run VALUES(?,?,?,?,?,?)',
                                 (run_id, 'fixture', market, 'source', json.dumps(scope), 1))
            self.prep.db.execute('INSERT INTO catalog_link_intent VALUES(?,?,?,?,?,?,?,?,?)',
                                 (intent_id, '123', account, state, json.dumps(spec),
                                  json.dumps({'list_id': '789', 'responseHash': 'original-receipt'}),
                                  json.dumps({'reason': 'original-timeout'}), 1, 1))
        self.prep.seed(run_id, [{'pid': '123', 'campaignId': '456', 'catalogSource': 'selected'}], scope)
        with self.prep.db:
            self.prep.db.execute("UPDATE catalog_prepare_item SET state='unknown',intent_id=?,error='original-timeout',updated=1 WHERE run_id=?",
                                 (intent_id, run_id))
        return run_id, intent_id

    def snapshot(self, run_id, intent_id):
        return (dict(self.prep.db.execute('SELECT * FROM catalog_prepare_item WHERE run_id=?', (run_id,)).fetchone()),
                self.ledger.get(intent_id))

    def verify(self, transport=None, *, limit=None):
        transport = transport or ReadTransport()
        opened = []

        @contextmanager
        def reader(report, **kwargs):
            opened.append(kwargs)
            yield transport

        report = {'platformWrites': 0}
        with patch.object(prepare, 'opportunity_reader', reader):
            result = prepare.step_verify(self.prep, report, limit=limit)
        return result, report, opened, transport

    def test_cross_market_row_does_not_consume_limit_or_change(self):
        other = self.add_intent(market='br', account='acc2')
        target = self.add_intent()
        before_other = self.snapshot(*other)
        before_target = self.snapshot(*target)
        result, report, opened, transport = self.verify(limit=1)
        self.assertEqual(result, {'verified': 1})
        self.assertEqual(report['verifyItems'], 1)
        self.assertEqual(self.snapshot(*other), before_other)
        after_item, after_intent = self.snapshot(*target)
        self.assertEqual((after_item['state'], after_intent['state']), ('ready', 'verified'))
        self.assertEqual(after_intent['receipt'], before_target[1]['receipt'])
        self.assertEqual(after_intent['spec'], before_target[1]['spec'])
        self.assertEqual((after_intent['readback']['market'], after_intent['readback']['readAccount']), ('it', 'acc9'))
        self.assertEqual(len(after_intent['readback']['evidenceRefs']), 2)
        self.assertEqual((opened[0]['market'], opened[0]['account_name']), ('it', 'acc9'))
        self.assertEqual(len(transport.requests), 2)
        self.assertEqual(report['platformWrites'], 0)

    def test_changed_current_account_does_not_recover_old_intent(self):
        target = self.add_intent()
        before = self.snapshot(*target)
        prepare.SCOPE['account'] = 'acc6'
        result, report, opened, transport = self.verify()
        self.assertEqual((result, report['verifyItems'], opened, transport.requests), ({}, 0, [], []))
        self.assertEqual(self.snapshot(*target), before)

    def test_only_foreign_market_does_not_open_platform_session(self):
        target = self.add_intent(market='br', account='acc2')
        before = self.snapshot(*target)
        result, report, opened, _ = self.verify()
        self.assertEqual((result, report['verifyItems'], opened), ({}, 0, []))
        self.assertEqual(self.snapshot(*target), before)

    def test_never_created_catalog_has_nothing_to_verify(self):
        self.ledger.db.execute('DROP TABLE catalog_link_intent')
        result, report, opened, _ = self.verify()
        self.assertEqual((result, report['verifyItems'], opened), ({}, 0, []))

    def test_run_and_intent_must_agree_on_frozen_scope(self):
        target = self.add_intent()
        for run_scope, spec_changes, row_account in [
            ({'market': 'br'}, {}, 'acc9'), ({'account': 'acc6'}, {}, 'acc9'),
            ({}, {'market': 'br'}, 'acc9'), ({}, {'account': 'acc6'}, 'acc9'),
            ({}, {}, 'acc6'),
        ]:
            with self.subTest(run_scope=run_scope, spec=spec_changes, row_account=row_account):
                scope = {'market': 'it', 'account': 'acc9', 'route': 'selected'} | run_scope
                spec = self.ledger.get(target[1])['spec'] | {'market': 'it', 'account': 'acc9'} | spec_changes
                with self.prep.db:
                    self.prep.db.execute('UPDATE catalog_prepare_run SET scope=? WHERE id=?', (json.dumps(scope), target[0]))
                    self.prep.db.execute('UPDATE catalog_link_intent SET account=?,spec=? WHERE id=?', (row_account, json.dumps(spec), target[1]))
                before = self.snapshot(*target)
                result, report, opened, _ = self.verify()
                self.assertEqual((result, report['verifyItems'], opened), ({}, 0, []))
                self.assertEqual(self.snapshot(*target), before)

    def test_scope_is_rechecked_after_platform_read_before_settlement(self):
        target = self.add_intent()
        before = self.snapshot(*target)

        def changed_scope():
            with self.prep.db:
                self.prep.db.execute("UPDATE catalog_prepare_run SET market='br' WHERE id=?", (target[0],))

        result, _, _, _ = self.verify(ReadTransport(after_read=changed_scope))
        self.assertEqual(result, {'scope_mismatch': 1})
        self.assertEqual(self.snapshot(*target), before)

    def test_actual_session_scope_must_match_before_reading(self):
        target = self.add_intent()
        before = self.snapshot(*target)
        for transport in (ReadTransport(account='acc6'), ReadTransport(market='br')):
            with self.assertRaisesRegex(ValueError, 'catalog_transport_scope_mismatch'):
                self.verify(transport)
            self.assertEqual(transport.requests, [])
            self.assertEqual(self.snapshot(*target), before)

    def test_both_confirmation_paths_reject_wrong_or_missing_read_scope(self):
        for state, method in [('unknown', self.ledger.confirm), ('prepared', self.ledger.confirm_existing_standard)]:
            with self.subTest(state=state):
                run_id, intent_id = self.add_intent(state=state, suffix=state)
                card = {'market': 'it', 'readAccount': 'acc9', 'state': 'verified_read_only',
                        'pid': '123', 'listId': '789', 'sourceCampaignId': '456', 'wireCampaignId': '0',
                        'creatorPercent': '13', 'verifiedListName': 'BJN test', 'checkedAt': 2}
                before = self.snapshot(run_id, intent_id)
                for changes in ({'market': 'br'}, {'readAccount': 'acc6'}, {'market': None}, {'readAccount': None}):
                    with self.assertRaisesRegex(ValueError, 'catalog_link_read_scope_mismatch'):
                        method(intent_id, card | changes)
                    self.assertEqual(self.snapshot(run_id, intent_id), before)
                    self.assertEqual(self.prep.db.execute('SELECT count(*) FROM catalog_current_binding').fetchone()[0],
                                     0 if state == 'unknown' else 1)
                self.assertEqual(method(intent_id, card)['state'], 'verified')

    def test_existing_verified_legacy_artifact_is_not_rewritten(self):
        target = self.add_intent(state='verified')
        legacy = {'state': 'verified_read_only', 'listId': '789', 'checkedAt': 1}
        self.ledger.db.execute('UPDATE catalog_link_intent SET readback=? WHERE id=?', (json.dumps(legacy), target[1]))
        before = self.ledger.get(target[1])
        result, _, _, transport = self.verify()
        self.assertEqual(result, {'already_verified': 1})
        self.assertEqual(self.ledger.get(target[1]), before)
        self.assertEqual(self.prep.item(target[0], '123', '456')['card'], legacy)
        self.assertEqual(transport.requests, [])

    def test_verified_artifact_with_explicit_wrong_scope_cannot_make_row_ready(self):
        target = self.add_intent(state='verified')
        for scope in ({'market': 'br'}, {'readAccount': 'acc6'}):
            card = {'state': 'verified_read_only', 'listId': '789', 'checkedAt': 1} | scope
            self.ledger.db.execute('UPDATE catalog_link_intent SET readback=? WHERE id=?', (json.dumps(card), target[1]))
            before = self.snapshot(*target)
            result, _, _, transport = self.verify()
            self.assertEqual(result, {'scope_mismatch': 1})
            self.assertEqual(self.snapshot(*target), before)
            self.assertEqual(transport.requests, [])

    def test_cardless_requeue_and_run_selection_stay_in_original_scope(self):
        target = self.add_intent()
        foreign = self.add_intent(market='br', account='acc2')
        old_account = self.add_intent(account='acc6', state='verified')
        with self.prep.db:
            self.prep.db.execute("UPDATE catalog_prepare_item SET state='review',card=NULL")
        foreign_before = self.snapshot(*foreign)
        old_before = self.snapshot(*old_account)
        self.assertEqual(prepare.requeue_cardless(self.prep, 'selected'), 1)
        self.assertEqual(prepare.cardless_runs(self.prep, 'selected'), [target[0]])
        self.assertEqual(self.snapshot(*foreign), foreign_before)
        self.assertEqual(self.snapshot(*old_account), old_before)

    def exercise_create(self, mode):
        """All confirmation callers use real card parsing with a non-default fake account."""
        sqlite3.connect(self.root / 'var/second-cycle.sqlite').close()
        apply_database(self.root, 'second-cycle')
        run_id, intent_id = self.add_intent(account='acc6', state='prepared')
        from bdhub.send.taplink.protocol import create_payload
        intent = self.ledger.get(intent_id)
        payload = create_payload(pid='123', campaign_id='456', creator_pct='13', name='BJN test', route='selected')
        with self.prep.db:
            self.prep.db.execute("UPDATE catalog_prepare_item SET state='prepared' WHERE run_id=?", (run_id,))
            self.prep.db.execute('UPDATE catalog_link_intent SET spec=?,receipt=NULL,readback=NULL WHERE id=?',
                                 (json.dumps(intent['spec'] | {'payload': payload}), intent_id))
        prepare.SCOPE['account'] = 'acc6'
        self.prep.policy['unknownReadbackDelaysSeconds'] = [0, 0]
        transport = CreateTransport(mode)

        @contextmanager
        def creator(*args, **kwargs):
            yield transport

        fresh = {'123': {'product_id': '123', 'title': 'test', 'commission_rate': '1500',
                         'open_collab_rate': '1000', 'product_status': 2, 'stock': 500}}
        report = {'platformWrites': 0}
        with patch.object(prepare, 'opportunity_card_creator_batch', creator), \
                patch.object(prepare, 'fresh_offers', return_value=fresh), \
                patch.object(prepare.time, 'sleep', lambda _: None):
            result = prepare.step_create(self.prep, run_id, 1, report)
        after = self.ledger.get(intent_id)
        self.assertEqual(after['state'], 'verified')
        self.assertEqual(self.prep.item(run_id, '123', '456')['state'], 'ready')
        self.assertEqual((after['readback']['market'], after['readback']['readAccount']), ('it', 'acc6'))
        self.assertEqual(transport.writes, [] if mode == 'existing' else [payload])
        self.assertEqual(self.prep.db.execute('SELECT count(*) FROM catalog_link_intent').fetchone()[0], 1)
        return result, report

    def test_creation_preflight_preserves_actual_read_scope(self):
        result, _ = self.exercise_create('existing')
        self.assertEqual(result['created'][0]['state'], 'existing_standard')

    def test_creation_receipt_readback_preserves_actual_read_scope(self):
        result, _ = self.exercise_create('created')
        self.assertEqual(result['created'][0]['state'], 'verified')

    def test_first_sample_short_campaign_low_stock_creates_and_reads_back_without_second_thresholds(self):
        sqlite3.connect(self.root / 'var/second-cycle.sqlite').close()
        apply_database(self.root, 'second-cycle')
        now = time.time()
        run_id, intent_id = self.add_intent(account='acc6', state='prepared')
        from bdhub.send.taplink.protocol import create_payload
        payload = create_payload(pid='123', campaign_id='456', creator_pct='13', name='BJN test', route='campaign')
        original = self.ledger.get(intent_id)
        offer = original['spec']['offer'] | {'catalogSource': 'campaign', 'offerKey': 'campaign:123:456',
                'firstSampleMaterial': True, 'title': 'sample', 'stock': '1', 'available': True, 'endAt': now + 86400,
                'sample': {'supported': True, 'quota': 3, 'observedAt': now, 'evidenceRef': 'sample-proof'}}
        spec = original['spec'] | {'route': 'campaign', 'offer': offer, 'payload': payload}
        with self.prep.db:
            scope = {'market': 'it', 'account': 'acc6', 'route': 'campaign'}
            self.prep.db.execute('UPDATE catalog_prepare_run SET scope=? WHERE id=?', (json.dumps(scope), run_id))
            self.prep.db.execute("UPDATE catalog_prepare_item SET state='prepared',catalog_source='campaign',listing=? WHERE run_id=?", (json.dumps(offer), run_id))
            self.prep.db.execute('UPDATE catalog_link_intent SET spec=?,receipt=NULL,readback=NULL WHERE id=?', (json.dumps(spec), intent_id))
        prepare.SCOPE.update(account='acc6', route='campaign')
        self.prep.policy['unknownReadbackDelaysSeconds'] = [0, 0]

        class SampleTransport(CreateTransport):
            stock = 1
            sample_quota = 3
            def _xhr(self, **kwargs):
                from lib.cycle_catalog import CAMPAIGNS, PRODUCTS
                if kwargs['path'] == CAMPAIGNS:
                    return SimpleNamespace(payload={'code': 0, 'data': {'total_num': 1, 'campaign': [{
                        'campaign_id': '456', 'crs_campaign_type': '7', 'promotion_end_time': int(now + 86400)}]}})
                if kwargs['path'] == PRODUCTS:
                    return SimpleNamespace(payload={'code': 0, 'data': {'total_num': 1, 'campaign_product': [{
                        'product_id': '123', 'product_name': 'sample', 'total_commission_percent': 1500,
                        'plan_commission_percent': 1000, 'stock': self.stock, 'sample_quota': self.sample_quota, 'product_status': 2}]}})
                response = super()._xhr(**kwargs)
                if kwargs['path'] == MEMBERS:
                    for row in response.payload['data'].get('campaign_products', []):
                        row['stock'] = self.stock
                if kwargs['path'] == CARD:
                    for row in response.payload['data'].get('list', []):
                        row['campaign_id'] = '456'
                return response
        transport = SampleTransport('created')

        @contextmanager
        def creator(*args, **kwargs):
            yield transport
        with patch.object(prepare, 'FIRST_START', None):
            self.assertEqual(prepare.campaign_fresh_offers(transport, [offer], now), {})
        with patch.object(prepare, 'FIRST_START', {'requestId': 'original-continue', 'revision': 1}):
            fresh = prepare.campaign_fresh_offers(transport, [offer], now)
            self.assertEqual((fresh['123']['sample']['quota'], fresh['123']['stock']), (3, '1'))
            self.assertTrue(prepare.plan_bounds(fresh['123'])['firstSampleMaterial'])
            with patch.object(prepare, 'opportunity_card_creator_batch', creator), patch.object(prepare.time, 'sleep', lambda _: None):
                result = prepare.step_create(self.prep, run_id, 1, {'platformWrites': 0})
        self.assertEqual(result['created'][0]['state'], 'verified')
        self.assertEqual(transport.writes, [payload])
        after = self.ledger.get(intent_id)
        self.assertEqual(after['state'], 'verified')
        self.assertFalse(after['readback']['stockRequired'])
        self.assertEqual(self.prep.item(run_id, '123', '456', 'campaign')['state'], 'ready')
        # The frozen marker also governs original-intent recovery after this
        # first worker exits; an ordinary intent retains the stock threshold.
        def read(path, params):
            response = transport._xhr(method='GET', path=path, params=params, payload=None, write=False)
            return response.payload, 'same-original-read'
        ordinary = dict(after)
        ordinary['spec'] = after['spec'] | {'offer': {k: v for k, v in after['spec']['offer'].items() if k != 'firstSampleMaterial'}}
        self.assertIsNone(prepare.verify_created_card(read, ordinary, after['receipt'], read_scope={'market': 'it', 'readAccount': 'acc6'}))
        for stock in (None, 0):
            transport.stock = stock
            with patch.object(prepare, 'FIRST_START', {'requestId': 'original-continue', 'revision': 1}):
                fresh = prepare.campaign_fresh_offers(transport, [offer], now)
            self.assertEqual(fresh['123']['sample']['quota'], 3)
            self.assertIsNotNone(prepare.verify_created_card(read, after, after['receipt'], read_scope={'market': 'it', 'readAccount': 'acc6'}))
            self.assertEqual(prepare.inspection.inspect_card(after['spec']['offer'], read, expected_list_id='789', expected_name='BJN test')['state'], 'verified_read_only')
            self.assertIsNone(prepare.verify_created_card(read, ordinary, after['receipt'], read_scope={'market': 'it', 'readAccount': 'acc6'}))
        transport.sample_quota = 0
        with patch.object(prepare, 'FIRST_START', {'requestId': 'original-continue', 'revision': 1}):
            self.assertEqual(prepare.campaign_fresh_offers(transport, [offer], now), {})

    def test_creation_batch_unknown_readback_preserves_actual_read_scope_without_repost(self):
        _, report = self.exercise_create('unknown')
        self.assertEqual(len(report['unknownReconcile']['settled']), 1)
        self.assertEqual(report['unknownReconcile']['skippedUnknown'], [])


if __name__ == '__main__':
    unittest.main()
