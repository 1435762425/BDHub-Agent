import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from lib import first_outreach as first
from lib.catalog_binding import offer_fingerprint
from lib.creator_identity import CreatorIdentityStore
from lib.cycle_delivery import Deliveries
from lib.second_cycle import CycleError, CycleStore, digest, encoded


class FirstOutreachTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.now = time.time()
        self.s = CycleStore(self.root / 'var/second-cycle.sqlite', lambda: self.now)
        self.plan = self.s.plan('bjn-local-research', 'br')
        first.ensure_schema(self.s)
        self.identities = CreatorIdentityStore(self.root / 'var/creator-identities.sqlite')
        self.person = self.profile('123')
        self.s.publish(self.plan, 'catalog', self.now, [self.offer()])
        self.d = Deliveries(self.s)
        self.s.db.execute('CREATE TABLE cycle_product_name(id TEXT,pid TEXT,locale TEXT,payload TEXT)')
        self.s.db.execute('INSERT INTO cycle_product_name VALUES(?,?,?,?)', ('name1', '1', 'pt-BR', encoded({'mention': 'produto', 'shortNameZh': '商品'})))
        self.links = sqlite3.connect(self.root / 'var/catalog-links.sqlite')
        self.links.execute('CREATE TABLE catalog_current_binding(market TEXT,catalog_source TEXT,pid TEXT,campaign_id TEXT,state TEXT,offer_fingerprint TEXT,card_payload TEXT)')
        self.links.execute('INSERT INTO catalog_current_binding VALUES(?,?,?,?,?,?,?)', ('br', 'selected', '1', 'campaign1', 'active', offer_fingerprint(self.offer()), encoded({'listId': '100'})))
        self.links.commit()
        self.patches = [
            patch('lib.template_library.require_send_template_approval', return_value={'ready': True}),
            patch('lib.template_library.next_approved_send_template', return_value={'id': 'standard', 'revision': 1}),
            patch('lib.template_library.render_send_template', side_effect=lambda spec, name, offer, handle, market: {'version': 4, 'deliveryOrder': 'card_then_text', 'textIt': 'Hello ' + handle}),
            patch('lib.market_registry.market', return_value={'locale': 'pt-BR'}),
        ]
        for mock in self.patches:
            mock.start()

    def tearDown(self):
        for mock in self.patches:
            mock.stop()
        self.identities.close()
        self.links.close()
        self.s.close()
        self.temp.cleanup()

    def offer(self, pid='1', **changes):
        return dict({'pid': pid, 'offerKey': 'offer' + pid, 'campaignId': 'campaign' + pid,
                     'catalogSource': 'selected', 'categoryId': '601450', 'title': 'Product ' + pid,
                     'creatorPercent': '12', 'publicPercent': '10', 'totalPercent': '15',
                     'stock': '1', 'available': True, 'endAt': self.now + 86400,
                     'sample': {'supported': True, 'quota': 3, 'observedAt': self.now, 'evidenceRef': 'sample-current'},
                     'evidenceRef': 'catalog-current'}, **changes)

    def profile(self, oec, *, category='601450', gmv='0', timestamp=None, status='value'):
        observed = timestamp if timestamp is not None else self.now
        from datetime import datetime, timezone
        date = datetime.fromtimestamp(observed, timezone.utc).isoformat()
        return self.identities.observe_profile('br', oec, 'creator' + oec, date, 'evidence-' + oec + '-' + str(observed),
                    {'fields': {'industry_groups': {'status': status, 'value': [{'id': category, 'label': 'Beauty', 'weight': '1'}]},
                                'med_gmv_revenue': {'status': 'zero' if gmv == '0' else 'value', 'value': {'decimal': gmv, 'rawSymbol': 'R$'}},
                                'sales_performance_end_time': {'status': 'value', 'value': self.now}}})

    def prepare(self):
        return first.prepare(self.root, store=self.s)

    def start(self, action='start'):
        cfg = first.control(self.s)
        return first.change_control(self.s, self.root, 'br', 'request-' + action + '-' + str(cfg['revision']), action, cfg['revision'])

    def get_candidate(self):
        return first.candidate(self.root, 'br', self.s, self.plan, {'conversations': []})

    def make_delivery(self):
        self.prepare()
        self.start()
        c = self.get_candidate()
        return self.d.prepare(self.plan, c), c

    def proof(self, delivery, kind):
        return {'status': 'confirmed', 'requestRef': next(p['request_ref'] for p in delivery['parts'] if p['kind'] == kind),
                'oecId': delivery['oec'], 'messageId': 'message-' + kind, 'evidenceRef': 'history-' + kind, 'kind': kind}

    def confirm_delivery(self, delivery):
        for kind in ('card', 'text'):
            self.d.begin(delivery['id'], kind, authorized_snapshot_hash=digest(delivery['snapshot']), recipient_verified=True, allowance_verified=True)
            self.d.confirm(delivery['id'], kind, self.proof(delivery, kind))

    def test_sample_offer_has_its_own_rules_and_does_not_require_second_outreach_thresholds(self):
        self.assertTrue(first.assess_first_offer(self.offer(stock='1', publicPercent='12'), self.now)['eligible'])
        for changes in ({'available': None}, {'endAt': self.now}, {'categoryId': None},
                        {'sample': {'supported': True, 'quota': 0, 'observedAt': self.now, 'evidenceRef': 'x'}},
                        {'sample': {'supported': True, 'quota': True, 'observedAt': self.now, 'evidenceRef': 'x'}},
                        {'sample': {'supported': None, 'quota': 9, 'observedAt': self.now, 'evidenceRef': 'x'}},
                        {'sample': {'supported': True, 'quota': 9, 'observedAt': self.now}}):
            self.assertFalse(first.assess_first_offer(self.offer(**changes), self.now)['eligible'], changes)

    def test_prepare_matches_primary_only_and_zero_gmv_is_allowed_without_authorizing(self):
        self.profile('234', category='602000', gmv='9000')
        result = self.prepare()
        self.assertEqual((result['products'], result['matchedCreators']), (1, 1))
        self.assertEqual(first.control(self.s)['mode'], 'stopped')
        self.assertFalse(first.control(self.s)['approvalsEnabled'])
        self.assertEqual(self.s.db.execute('SELECT count(*) FROM relationship').fetchone()[0], 0)
        with self.assertRaisesRegex(CycleError, 'first_outreach_stopped'):
            self.get_candidate()

    def test_unique_primary_group_required_and_unknown_is_not_replaced_by_secondary(self):
        payload = {'fields': {'industry_groups': {'status': 'value', 'value': [
            {'id': '-1', 'weight': '.7'}, {'id': '601450', 'weight': '.3'}]}}}
        self.assertIsNone(first.main_category(payload))
        payload['fields']['industry_groups']['value'][0] = {'id': '602000', 'weight': '.3'}
        self.assertIsNone(first.main_category(payload))

    def test_latest_unauthorized_snapshot_does_not_borrow_old_category(self):
        self.profile('123', status='unauthorized', timestamp=self.now + 1)
        self.assertEqual(self.prepare()['matchedCreators'], 0)

    def test_exact_find_is_not_a_profile_snapshot_and_gmv_only_sorts(self):
        self.profile('234', gmv='10000')
        self.profile('345', gmv='0')
        from datetime import datetime, timezone
        self.identities.observe_profile('br', '234', 'creator234', datetime.fromtimestamp(self.now + 1, timezone.utc).isoformat(), 'exact-proof', {'exactFind': {'code': 0}})
        self.assertEqual(self.prepare()['matchedCreators'], 3)
        self.start()
        self.assertEqual(self.get_candidate()['oecId'], '234')

    def test_generation_published_only_when_complete_and_catalog_drift_keeps_old_head(self):
        first_head = self.prepare()['generationId']
        original = first._catalog_fingerprint
        calls = 0
        def changed(store, plan):
            nonlocal calls
            calls += 1
            return original(store, plan) if calls == 1 else 'changed'
        with patch.object(first, '_catalog_fingerprint', side_effect=changed):
            with self.assertRaisesRegex(CycleError, 'first_outreach_catalog_changed'):
                self.prepare()
        self.assertEqual(self.s.db.execute('SELECT generation_id FROM first_outreach_head').fetchone()[0], first_head)
        self.assertEqual(self.s.db.execute("SELECT count(*) FROM first_outreach_generation WHERE state='failed'").fetchone()[0], 1)

    def test_control_is_idempotent_revision_checked_and_independent_from_second_send(self):
        self.prepare()
        cfg = first.change_control(self.s, self.root, 'br', 'request-first-start', 'start', 0)
        self.assertEqual(cfg['mode'], 'canary')
        self.assertTrue(first.change_control(self.s, self.root, 'br', 'request-first-start', 'start', 0)['duplicate'])
        with self.assertRaisesRegex(CycleError, 'first_outreach_request_conflict'):
            first.change_control(self.s, self.root, 'br', 'request-first-start', 'stop', 1)
        with self.assertRaisesRegex(CycleError, 'first_outreach_revision_conflict'):
            first.change_control(self.s, self.root, 'br', 'request-stop-stale', 'stop', 0)
        self.assertFalse(first._table(self.s, 'continuous_send_control'))
        self.start('stop')
        with self.assertRaisesRegex(CycleError, 'first_outreach_stopped'):
            first.dispatch_allowed(self.s)

    def test_continue_requires_real_confirmation_and_enabling_approvals_does_not_start_send(self):
        self.prepare()
        with self.assertRaisesRegex(CycleError, 'first_outreach_canary_required'):
            self.start('continue')
        cfg = first.change_control(self.s, self.root, 'br', 'request-approval-on', 'set-approvals', 0, approvals_enabled=True)
        self.assertTrue(cfg['approvalsEnabled'])
        self.assertEqual(cfg['mode'], 'stopped')

    def test_candidate_reuses_shared_relationship_and_is_frozen_to_page_start(self):
        delivery, c = self.make_delivery()
        self.assertEqual((c['outreachPurpose'], c['executionMode'], c['source']['kind']), ('first', first.MODE, 'first_category'))
        self.assertEqual(self.s.db.execute('SELECT count(*) FROM relationship').fetchone()[0], 1)
        first.require_delivery(self.s, c, delivery['id'])
        self.start('stop')
        with self.assertRaisesRegex(CycleError, 'first_outreach_stopped'):
            first.require_delivery(self.s, c, delivery['id'])
        self.start()
        with self.assertRaisesRegex(CycleError, 'first_outreach_authorization_changed'):
            first.require_delivery(self.s, c, delivery['id'])

    def test_actual_confirmation_consumes_canary_and_requires_continue(self):
        delivery, c = self.make_delivery()
        self.confirm_delivery(delivery)
        cfg = first.control(self.s)
        self.assertEqual(cfg['mode'], 'stopped')
        self.assertEqual(cfg['canaryDeliveryId'], delivery['id'])
        self.assertEqual(self.start('continue')['mode'], 'continuous')
        self.assertIsNone(self.get_candidate())

    def test_unknown_is_not_confirmed_or_resent_and_does_not_allow_continue(self):
        delivery, c = self.make_delivery()
        self.d.begin(delivery['id'], 'card', authorized_snapshot_hash=digest(c), recipient_verified=True, allowance_verified=True)
        self.d.unknown(delivery['id'], 'card')
        self.assertEqual(first.control(self.s)['mode'], 'canary')
        self.assertIsNone(self.get_candidate())
        with self.assertRaisesRegex(CycleError, 'first_outreach_result_unknown'):
            self.start('continue')
        self.assertFalse(first.assess_application(self.s, self.root, 'br', {'oecId': '123', 'pid': '1', 'campaignId': 'campaign1'})['eligible'])

    def test_latest_profile_and_sample_quota_are_checked_again_before_dispatch(self):
        delivery, c = self.make_delivery()
        self.profile('123', category='602000', timestamp=self.now + 1)
        with self.assertRaisesRegex(CycleError, 'first_outreach_match_changed'):
            first.require_delivery(self.s, c, delivery['id'])
        self.profile('123', timestamp=self.now + 2)
        changed = self.offer(sample={'supported': True, 'quota': 0, 'observedAt': self.now + 1, 'evidenceRef': 'depleted'})
        self.s.publish(self.plan, 'catalog', self.now + 1, [changed])
        with self.assertRaisesRegex(CycleError, 'first_outreach_offer_changed'):
            first.require_delivery(self.s, c, delivery['id'])

    def test_approval_scope_requires_exact_confirmed_triple_and_current_category(self):
        delivery, c = self.make_delivery()
        self.confirm_delivery(delivery)
        application = {'oecId': '123', 'pid': '1', 'campaignId': 'campaign1'}
        self.assertTrue(first.assess_application(self.s, self.root, 'br', application)['eligible'])
        for change in ({'oecId': '234'}, {'pid': '2'}, {'campaignId': 'campaign2'}, {'creatorId': 'wrong'}):
            self.assertFalse(first.assess_application(self.s, self.root, 'br', application | change)['eligible'])
        self.profile('123', status='unauthorized', timestamp=self.now + 1)
        self.assertEqual(first.assess_application(self.s, self.root, 'br', application)['reason'], 'sample_current_profile_unavailable')

    def test_approval_rechecks_depleted_sample_and_current_manual_takeover(self):
        delivery, c = self.make_delivery()
        self.confirm_delivery(delivery)
        application = {'oecId': '123', 'pid': '1', 'campaignId': 'campaign1'}
        self.s.db.execute("UPDATE relationship SET mode='human'")
        self.assertEqual(first.assess_application(self.s, self.root, 'br', application)['reason'], 'sample_relationship_requires_review')
        self.s.db.execute("UPDATE relationship SET mode='auto'")
        self.s.publish(self.plan, 'catalog', self.now + 1, [self.offer(sample={'supported': True, 'quota': 0, 'observedAt': self.now, 'evidenceRef': 'empty'})])
        self.assertEqual(first.assess_application(self.s, self.root, 'br', application)['reason'], 'sample_current_offer_ineligible')

    def test_shared_cooldown_and_pause_exclude_first_candidate(self):
        self.prepare()
        self.start()
        c = self.get_candidate()
        self.s.db.execute("UPDATE relationship SET mode='human'")
        self.assertIsNone(self.get_candidate())
        self.s.db.execute("UPDATE relationship SET mode='auto'")
        old = dict(c, executionMode='market-continuous-v1', outreachPurpose='second', source={'sourceId': 'second-source'})
        self.s.db.execute('INSERT INTO cycle_delivery VALUES(?,?,?,?,?,?,?,?,?,?)', ('second1', self.plan, c['creatorId'], c['oecId'], '2', 'second-source', encoded(old), self.now, self.now + 1800, 'confirmed'))
        self.s.db.execute('INSERT INTO cycle_delivery_part(delivery_id,kind,request_ref,state,started) VALUES(?,?,?,?,?)', ('second1', 'card', 'second-card', 'confirmed', self.now))
        self.assertIsNone(self.get_candidate())
        self.now += 72 * 3600
        self.s.publish(self.plan, 'catalog', self.now + 1, [self.offer()])
        self.prepare()
        self.assertIsNotNone(self.get_candidate())

    def test_status_is_readonly_and_missing_store_does_not_create_anything(self):
        missing = self.root / 'missing'
        self.assertEqual(first.status(missing)['control']['mode'], 'stopped')
        self.assertFalse(missing.exists())
        self.prepare()
        before = self.s.db.total_changes
        result = first.status(self.root)
        self.assertEqual(result['counts']['matchedCreators'], 1)
        self.assertEqual(result['platformWrites'], 0)
        self.assertEqual(before, self.s.db.total_changes)

    def test_prepare_checkpoints_batch_and_resumes_without_restarting_scan(self):
        self.profile('234')
        self.profile('345')
        with patch.object(first, 'PAGE_SIZE', 1):
            paused = first.prepare(self.root, store=self.s, max_pages=1)
            self.assertEqual((paused['state'], paused['scanned']), ('paused', 1))
            self.assertIsNone(first._head(self.s, self.plan))
            resumed = first.prepare(self.root, store=self.s)
        self.assertEqual(resumed['generationId'], paused['generationId'])
        self.assertEqual((resumed['state'], resumed['scanned'], resumed['matchedCreators']), ('complete', 3, 3))
        self.assertEqual(self.s.db.execute('SELECT count(*) FROM first_outreach_candidate').fetchone()[0], 3)

    def test_prepare_stop_callback_persists_checkpoint_without_publishing_partial_head(self):
        result = first.prepare(self.root, store=self.s, should_stop=lambda: True)
        self.assertEqual(result['state'], 'paused')
        self.assertEqual(result['scanned'], 0)
        self.assertIsNone(first._head(self.s, self.plan))
        self.assertEqual(first.prepare(self.root, store=self.s)['generationId'], result['generationId'])

    def test_exact_decimal_ranking_preserves_large_amount_difference(self):
        self.profile('234', gmv='9007199254740992.01')
        self.profile('345', gmv='9007199254740992.02')
        self.prepare()
        self.start()
        self.assertEqual(self.get_candidate()['oecId'], '345')

    def test_unknown_currency_or_window_keeps_creator_without_comparing_gmv(self):
        profile = first.latest_profile(self.root, 'br', '123')
        profile['payload']['fields']['med_gmv_revenue']['value']['rawSymbol'] = '$'
        self.assertEqual(first._gmv(profile), (None, None, ''))
        profile['payload']['fields']['med_gmv_revenue']['value']['rawSymbol'] = 'R$'
        profile['payload']['fields']['sales_performance_end_time']['status'] = 'unauthorized'
        self.assertEqual(first._gmv(profile), (None, None, ''))

    def test_isolated_unknown_keeps_evidence_without_blocking_whole_market(self):
        delivery, c = self.make_delivery()
        self.s.db.execute("UPDATE cycle_delivery SET state='quarantined_unknown' WHERE id=?", (delivery['id'],))
        self.s.db.execute("UPDATE cycle_delivery_part SET state='unknown' WHERE delivery_id=? AND kind='card'", (delivery['id'],))
        self.assertEqual(first._unknown(self.s, self.plan), 0)
        self.assertIsNone(self.get_candidate())


if __name__ == '__main__':
    unittest.main()
