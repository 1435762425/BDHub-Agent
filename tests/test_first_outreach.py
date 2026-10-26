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
    def test_status_reuses_locale_only_within_one_request_and_preserves_name_keys(self):
        from lib.cycle_materials import market_locale, name_key
        self.prepare()
        expected = name_key(self.offer(), 'br')
        with patch('lib.cycle_materials.market_locale', wraps=market_locale) as locale:
            state = first.status(self.root)
            self.assertEqual(locale.call_count, 1)
        self.assertEqual(state['materials']['missingNames'], 0)
        with patch('lib.cycle_materials.market_locale', side_effect=AssertionError('locale_already_resolved')):
            self.assertEqual(name_key(self.offer(), 'br', locale='pt-BR'), expected)
            self.assertIsNotNone(first._localized_name(self.s, self.offer(), locale='pt-BR'))
        # A later request still reads current configuration rather than retaining a global cache.
        with patch('lib.cycle_materials.market_locale', return_value='changed-locale') as locale:
            state = first.status(self.root)
            self.assertEqual(locale.call_count, 1)
        self.assertEqual(state['materials']['missingNames'], 1)

    def test_material_holds_excludes_prepared_and_other_markets_using_existing_partial_index(self):
        self.links.execute('CREATE TABLE catalog_link_intent(pid TEXT,state TEXT,spec TEXT)')
        self.links.execute("CREATE UNIQUE INDEX catalog_link_open_market_pid ON catalog_link_intent(json_extract(spec,'$.market'),pid) WHERE state IN ('prepared','submitted','receipt_saved','unknown')")
        rows=[('1','prepared','br'),('2','submitted','br'),('3','receipt_saved','br'),
              ('4','unknown','br'),('5','unknown','my'),('6','confirmed','br')]
        self.links.executemany('INSERT INTO catalog_link_intent VALUES(?,?,?)',
            [(pid,state,encoded({'market':market})) for pid,state,market in rows])
        self.links.commit()
        statements=[];connect=sqlite3.connect
        def traced(*args,**kwargs):
            connection=connect(*args,**kwargs);connection.set_trace_callback(statements.append);return connection
        with patch.object(first.sqlite3,'connect',side_effect=traced):
            self.assertEqual(first.material_holds(self.s), {'2','3','4'})
        query=next(statement for statement in statements if statement.startswith('SELECT pid FROM catalog_link_intent'))
        plan=[row[3] for row in self.links.execute('EXPLAIN QUERY PLAN '+query)]
        self.assertTrue(any('USING INDEX catalog_link_open_market_pid' in step for step in plan),plan)

    def test_public_products_count_deduplicates_alternate_campaign_offers(self):
        original = self.offer()
        alternative = self.offer(campaignId='other-campaign', offerKey='other-offer')
        self.s.publish(self.plan, 'catalog', self.now + 1, [original, alternative])
        self.assertEqual(first.status(self.root)['counts']['products'], 1)

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
        self.s.db.execute('CREATE TABLE cycle_product_name(id TEXT,pid TEXT,locale TEXT,source_title TEXT,payload TEXT)')
        from lib.cycle_materials import name_key
        self.s.db.execute('INSERT INTO cycle_product_name VALUES(?,?,?,?,?)', (name_key(self.offer(), 'br'), '1', 'pt-BR', self.offer()['title'], encoded({'mention': 'produto', 'shortNameZh': '商品'})))
        self.links = sqlite3.connect(self.root / 'var/catalog-links.sqlite')
        self.links.execute('CREATE TABLE catalog_current_binding(market TEXT,catalog_source TEXT,pid TEXT,campaign_id TEXT,state TEXT,offer_fingerprint TEXT,card_payload TEXT)')
        self.links.execute('INSERT INTO catalog_current_binding VALUES(?,?,?,?,?,?,?)', ('br', 'selected', '1', 'campaign1', 'active', offer_fingerprint(self.offer()), encoded({'listId': '100'})))
        self.links.commit()

    def tearDown(self):
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

    def profile(self, oec, *, category='601450', gmv='0', timestamp=None, status='value', video=None, live=None,
                window=None, video_currency='R$', live_currency='R$'):
        observed = timestamp if timestamp is not None else self.now
        from datetime import datetime, timezone
        date = datetime.fromtimestamp(observed, timezone.utc).isoformat()
        fields = {'industry_groups': {'status': status, 'value': [{'id': category, 'label': 'Beauty', 'weight': '1'}]},
                  'med_gmv_revenue': {'status': 'zero' if gmv == '0' else 'value', 'value': {'decimal': gmv, 'rawSymbol': 'R$'}},
                  'sales_performance_end_time': {'status': 'value', 'value': self.now if window is None else window}}
        for name, amount, currency in (('video_gmv', video, video_currency), ('live_gmv', live, live_currency)):
            fields[name] = {'status': 'absent'} if amount is None else {'status': 'zero' if amount == '0' else 'value', 'value': {'decimal': amount, 'rawSymbol': currency}}
        return self.identities.observe_profile('br', oec, 'creator' + oec, date, 'evidence-' + oec + '-' + str(observed), {'fields': fields})

    def prepare(self):
        return first.prepare(self.root, store=self.s)

    def start(self, action='start'):
        cfg = first.control(self.s)
        return first.change_control(self.s, self.root, 'br', 'request-' + action + '-' + str(cfg['revision']), action, cfg['revision'])

    def get_candidate(self):
        return first.candidate(self.root, 'br', self.s, self.plan, {'conversations': []})

    def test_known_recipient_message_space_skips_top_creator_before_freezing(self):
        from lib.cycle_inbox import Inbox
        person = self.profile('234', gmv='1000')
        self.prepare()
        self.start()
        self.s.db.execute('INSERT INTO relationship(plan_id,creator_id,oec) VALUES(?,?,?)',
                          (self.plan, person['creatorId'], '234'))
        inbox = Inbox(self.s)
        def ingest(ids):
            inbox.ingest(self.plan, '88', '234', {'identityVerified': True, 'hasMore': False,
                'events': [{'conversationId': '88', 'oecId': '234', 'kind': 'ourMessages',
                            'messageId': mid, 'createTimeRaw': int((self.now - 259201) * 1000)}
                           for mid in ids]})
        ingest(['1', '2', '3'])
        self.assertEqual(self.get_candidate()['oecId'], '234')
        ingest(['1', '2', '3', '4'])
        before = self.s.db.execute('SELECT count(*) FROM cycle_delivery').fetchone()[0]
        self.assertEqual(self.get_candidate()['oecId'], '123')
        self.assertEqual(self.s.db.execute('SELECT count(*) FROM cycle_delivery').fetchone()[0], before)
        self.s.db.execute('UPDATE relationship SET unlocked=1 WHERE plan_id=? AND oec=?',
                          (self.plan, '234'))
        self.assertEqual(self.get_candidate()['oecId'], '234')

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

    def test_unknown_card_isolates_only_original_pid_and_next_product_matches_without_new_grant(self):
        alternative = self.offer('2', creatorPercent='11')
        self.s.publish(self.plan, 'catalog', self.now + 1, [self.offer(), alternative])
        from lib.cycle_materials import name_key
        self.s.db.execute('INSERT INTO cycle_product_name VALUES(?,?,?,?,?)',
                          (name_key(alternative, 'br'), '2', 'pt-BR', alternative['title'], encoded({'mention': 'alternativa', 'shortNameZh': '备用品'})))
        self.links.execute('INSERT INTO catalog_current_binding VALUES(?,?,?,?,?,?,?)',
                           ('br', 'selected', '2', alternative['campaignId'], 'active', offer_fingerprint(alternative), encoded({'listId': '200'})))
        self.links.commit()
        self.prepare()
        grant = self.start()
        original_fingerprint = first._catalog_fingerprint(self.s, self.plan)
        self.links.execute('CREATE TABLE catalog_link_intent(id TEXT PRIMARY KEY,pid TEXT,state TEXT,spec TEXT)')
        original = ('original-card', '1', 'unknown', encoded({'market': 'br', 'account': 'acc2', 'campaignId': 'campaign1'}))
        self.links.execute('INSERT INTO catalog_link_intent VALUES(?,?,?,?)', original)
        self.links.execute('INSERT INTO catalog_link_intent VALUES(?,?,?,?)', ('foreign-card', '2', 'unknown', encoded({'market': 'fr'})))
        self.links.commit()
        self.assertNotEqual(first._catalog_fingerprint(self.s, self.plan), original_fingerprint)
        self.assertEqual([offer['pid'] for offer in first.material_offers(self.s)], ['2'])
        with self.assertRaisesRegex(CycleError, 'prepare_required'):
            self.get_candidate()
        self.prepare()
        self.assertEqual(self.get_candidate()['pid'], '2')
        self.assertEqual(first.control(self.s)['firstStart'], grant['firstStart'])
        self.assertEqual(tuple(self.links.execute("SELECT * FROM catalog_link_intent WHERE id='original-card'").fetchone()), original)
        # Only real settlement releases the original material. Its old unknown
        # request is never replaced as a way to use another product.
        self.links.execute("UPDATE catalog_link_intent SET state='verified' WHERE id='original-card'")
        self.links.commit()
        with self.assertRaisesRegex(CycleError, 'prepare_required'):
            self.get_candidate()
        self.prepare()
        self.assertEqual(self.get_candidate()['pid'], '1')
        self.links.execute("UPDATE catalog_link_intent SET state='prepared' WHERE id='original-card'")
        self.links.commit()
        self.assertEqual([offer['pid'] for offer in first.material_offers(self.s)], ['1'])

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

    def test_profile_page_matches_fresh_bindings_latest_ties_and_market_scope(self):
        for oec in ('234', '345', '456', '567', '678'):
            self.profile(oec)
        db = self.identities._db
        def observation(oec, evidence, *, payload=None, creator=None, seconds=20):
            row = dict(db.execute("SELECT * FROM identity_observation WHERE market='br' AND oec_id=? ORDER BY rowid LIMIT 1", (oec,)).fetchone())
            row.update(event_id='event-' + evidence, evidence_ref=evidence,
                       observed_at='same-time-' + str(seconds), observed_us=row['observed_us'] + seconds * 1_000_000,
                       fingerprint='fingerprint-' + evidence)
            if payload is not None:
                row['payload_json'] = payload
            if creator is not None:
                row['creator_id'] = creator
            db.execute('INSERT INTO identity_observation(' + ','.join(row) + ') VALUES(' + ','.join('?' for _ in row) + ')', tuple(row.values()))
        observation('123', 'first-tie:profile')
        observation('123', 'last-tie:profile')
        observation('123', 'later-row:find-profile')
        observation('234', 'invalid-json', payload='{bad', seconds=30)
        observation('234', 'not-core-fields', payload='{"fields":[]}', seconds=40)
        observation('678', 'wrong-creator', creator=self.person['creatorId'], seconds=30)
        db.execute("UPDATE creator_identity SET handle_conflict=1 WHERE market='br' AND oec_id='345'")
        db.execute("UPDATE creator_identity SET current_handle=NULL WHERE market='br' AND oec_id='456'")
        db.execute("UPDATE creator_identity SET current_handle='' WHERE market='br' AND oec_id='567'")
        person = dict(db.execute("SELECT * FROM creator_identity WHERE market='br' AND oec_id='123'").fetchone())
        person.update(creator_id='person-without-profile', oec_id='789', current_handle='without-profile')
        db.execute('INSERT INTO creator_identity(' + ','.join(person) + ') VALUES(' + ','.join('?' for _ in person) + ')', tuple(person.values()))
        db.commit()
        from datetime import datetime, timezone
        self.identities.observe_profile('my', '123', 'other_market', datetime.fromtimestamp(self.now, timezone.utc).isoformat(),
                                        'other-market-profile', {'fields': {}})
        expected = {oec: first._profile(db, 'br', oec) for oec in ('123', '234', '345', '456', '567', '678', '789')}
        statements = [];db.set_trace_callback(statements.append)
        try:
            page = first._profile_page(db, 'br', '', 100)
        finally:
            db.set_trace_callback(None)
        self.assertEqual(len(statements), 1)
        self.assertEqual({row['oec_id']: row['profile'] for row in page}, expected)
        self.assertEqual(expected['123']['evidenceRef'], 'last-tie:profile')
        self.assertEqual(expected['234']['evidenceRef'], 'evidence-234-' + str(self.now))
        self.assertTrue(all(expected[oec] is None for oec in ('345', '456', '567', '678', '789')))
        self.assertEqual([row['oec_id'] for row in first._profile_page(db, 'br', '123', 2)], ['234', '345'])
        other = first._profile_page(db, 'my', '', 100)
        self.assertEqual(len(other), 1)
        self.assertEqual(other[0]['profile']['handle'], 'other_market')
        self.assertNotEqual(other[0]['profile']['creatorId'], expected['123']['creatorId'])

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
            self.assertTrue(paused['hasMoreLocalWork'])
            self.assertEqual(paused['localProgress'], {'fromOec': '', 'toOec': '123', 'scanned': 1})
            self.assertIsNone(first._head(self.s, self.plan))
            resumed = first.prepare(self.root, store=self.s, max_pages=2)
        self.assertEqual(resumed['generationId'], paused['generationId'])
        self.assertEqual((resumed['state'], resumed['scanned'], resumed['matchedCreators']), ('complete', 3, 3))
        self.assertEqual(self.s.db.execute('SELECT count(*) FROM first_outreach_candidate').fetchone()[0], 3)

    def test_prepare_stop_callback_persists_checkpoint_without_publishing_partial_head(self):
        result = first.prepare(self.root, store=self.s, should_stop=lambda: True)
        self.assertEqual(result['state'], 'paused')
        self.assertEqual(result['scanned'], 0)
        self.assertFalse(result['hasMoreLocalWork'])
        self.assertEqual(result['localProgress']['scanned'], 0)
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

    def test_mixed_first_markers_cannot_bypass_first_start_authorization(self):
        delivery, candidate = self.make_delivery()
        for changes in ({'executionMode': 'market-continuous-v1'}, {'outreachPurpose': 'second'},
                        {'source': {'kind': 'second'}}):
            with self.assertRaisesRegex(CycleError, 'first_outreach_source_invalid'):
                first.require_delivery(self.s, candidate | changes, delivery['id'])
        with self.assertRaisesRegex(CycleError, 'first_outreach_plan_changed'):
            first.require_delivery(self.s, candidate, delivery['id'], plan='another-plan')

    def test_missing_exact_name_or_card_is_explicitly_not_ready_and_start_refused(self):
        self.prepare()
        self.s.db.execute('DELETE FROM cycle_product_name')
        state = first.status(self.root)
        self.assertIn('first_outreach_materials_unavailable', state['readiness']['blockers'])
        self.assertEqual(state['materials']['missingNames'], 1)
        self.assertTrue(state['candidates'][0]['message'])
        with self.assertRaisesRegex(CycleError, 'first_outreach_materials_unavailable'):
            self.start()
        self.assertEqual(first.control(self.s)['mode'], 'stopped')

    def test_name_from_previous_product_title_is_not_used_as_current_material(self):
        changed = self.offer(title='New product title')
        self.s.publish(self.plan, 'catalog', self.now + 1, [changed])
        self.prepare()
        self.assertIsNone(first._localized_name(self.s, changed))
        with self.assertRaisesRegex(CycleError, 'first_outreach_materials_unavailable'):
            self.start()

    def test_content_performance_ranks_before_total_within_same_window_and_category(self):
        self.profile('234', gmv='1000', video='80', live='30')
        self.profile('345', gmv='200', video='20', live='90')
        self.profile('456', category='602000', gmv='900000', video='900000')
        self.prepare()
        self.start()
        self.assertEqual(self.get_candidate()['oecId'], '345')
        self.assertEqual(self.s.db.execute('SELECT count(*) FROM first_outreach_candidate').fetchone()[0], 3)

    def test_content_tie_uses_exact_total_gmv_and_missing_content_falls_back_without_zero(self):
        self.profile('234', gmv='200', video='90')
        self.profile('345', gmv='100', live='90')
        self.profile('567', gmv='91')
        self.prepare()
        self.start()
        self.assertEqual(self.get_candidate()['oecId'], '567')
        row = self.s.db.execute("SELECT payload FROM first_outreach_candidate WHERE oec='567'").fetchone()
        value = json.loads(row[0])
        self.assertIsNone(value['videoGmv'])
        self.assertIsNone(value['liveGmv'])
        self.assertIsNone(value['contentGmv'])
        self.s.db.execute("UPDATE relationship SET mode='human' WHERE oec='567'")
        self.assertEqual(self.get_candidate()['oecId'], '234')

    def test_other_window_or_currency_content_is_not_compared_as_current_brl(self):
        self.profile('234', gmv='2', video='1', window=self.now)
        self.profile('345', gmv='9000', live='8000', window=self.now - 86400)
        self.profile('456', gmv='0', video='900000', video_currency='$')
        self.prepare()
        self.start()
        self.assertEqual(self.get_candidate()['oecId'], '234')
        foreign = json.loads(self.s.db.execute("SELECT payload FROM first_outreach_candidate WHERE oec='456'").fetchone()[0])
        self.assertIsNone(foreign['videoGmv'])
        self.assertIsNone(foreign['contentGmv'])

    def test_unauthorized_latest_content_does_not_restore_older_performance(self):
        profile = first.latest_profile(self.root, 'br', '123')
        profile['payload']['fields']['video_gmv'] = {'status': 'unauthorized', 'value': {'decimal': '90000', 'rawSymbol': 'R$'}}
        profile['payload']['fields']['live_gmv'] = {'status': 'absent'}
        performance = first._performance(profile)
        self.assertIsNone(performance['contentGmv'])
        self.assertEqual(performance['gmv'], '0')
        self.assertEqual(performance['contentKey'], performance['gmvKey'])

    def test_existing_local_candidate_projection_can_add_content_sort_key(self):
        self.s.db.execute('DROP TABLE first_outreach_candidate')
        self.s.db.execute('''CREATE TABLE first_outreach_candidate(
          generation_id TEXT NOT NULL,creator_id TEXT NOT NULL,oec TEXT NOT NULL,pid TEXT NOT NULL,
          offer_key TEXT NOT NULL,gmv TEXT,payload TEXT NOT NULL,gmv_window REAL,gmv_key TEXT NOT NULL DEFAULT '',
          PRIMARY KEY(generation_id,creator_id))''')
        first.ensure_schema(self.s)
        self.assertIn('content_gmv_key', {row['name'] for row in self.s.db.execute('PRAGMA table_info(first_outreach_candidate)')})
        self.assertEqual(self.prepare()['matchedCreators'], 1)


if __name__ == '__main__':
    unittest.main()
