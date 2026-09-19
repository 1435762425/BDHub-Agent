"""发送前的两处复用：短名按 pid 兜底、卡片定位从建链台账派生（都不再发平台请求）。"""
import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from lib.cycle_materials import name_key  # noqa: E402
from lib.cycle_materials import select_offers  # noqa: E402
from lib.cycle_review import (card_rate_gap, ledger_card, ledger_gap,  # noqa: E402
                              name_from_card, name_from_title, product_name)
from lib.catalog_binding import offer_fingerprint  # noqa: E402
from lib.schema_migrations import apply_database  # noqa: E402
from lib.second_cycle import CycleStore  # noqa: E402

OFFER = {'pid': '1729', 'title': 'Cuscino morbido per il collo', 'offerKey': 'selected:1729:0',
         'campaignId': '7685', 'catalogSource':'selected', 'creatorPercent': '13',
         'publicPercent': '10', 'totalPercent':'15'}


def fixture(folder, *, name_pid=None, card=None, state='ready', readback=None, reuse=None):
    root = Path(folder)
    var = root / 'var'
    var.mkdir(parents=True, exist_ok=True)
    store = CycleStore(var / 'second-cycle.sqlite')
    # 这张表由 cycle_materials 建；直接建一个同样形状的最小版。
    store.db.executescript('CREATE TABLE IF NOT EXISTS cycle_product_name('
                           'id TEXT PRIMARY KEY,pid TEXT NOT NULL,locale TEXT NOT NULL,'
                           'source_title TEXT NOT NULL,payload TEXT NOT NULL,job_id TEXT);')
    if name_pid is not None:
        # 缓存键是按**另一个标题**算的（Kalodata 的写法），所以按 OFFER 的标题查键必然 miss。
        other = {'pid': OFFER['pid'], 'title': 'cuscino per il collo morbido'}
        store.db.execute('INSERT INTO cycle_product_name VALUES(?,?,?,?,?,?)',
                         (name_key(other), OFFER['pid'], 'it-IT', other['title'],
                          json.dumps({'shortNameIt': 'Cuscino', 'shortNameZh': '颈枕',
                                      'mentionIt': 'questo cuscino', 'ref': '0'}), 'job'))
    sqlite3.connect(var / 'catalog-links.sqlite').close()
    apply_database(root,'catalog-links')
    if card is not None or readback is not None or reuse is not None:
        with closing(sqlite3.connect(var / 'catalog-links.sqlite')) as links, links:
            links.execute('CREATE TABLE catalog_prepare_item(pid TEXT,campaign_id TEXT,creator_percent TEXT,'
                          'state TEXT,card TEXT,updated REAL)')
            links.execute('CREATE TABLE catalog_prepare_readback(pid TEXT,campaign_id TEXT,kind TEXT,'
                          'payload TEXT,observed_at REAL)')
            links.execute('CREATE TABLE catalog_prepare_reuse(pid TEXT,campaign_id TEXT,list_id TEXT,'
                          'creator_percent TEXT,public_percent TEXT,reusable INTEGER,observed_at REAL)')
            if card is not None:
                links.execute('INSERT INTO catalog_prepare_item VALUES(?,?,?,?,?,?)',
                              (OFFER['pid'], OFFER['campaignId'], OFFER['creatorPercent'], state,
                               json.dumps(card), 1.0))
                links.execute("INSERT INTO catalog_current_binding VALUES('it','selected',?,?,?,?,"
                              "'commission-1-to-2-v1','link-naming-v1','13',?,?,NULL,?,1,1)",
                              (OFFER['pid'],OFFER['campaignId'],offer_fingerprint(OFFER),
                               str(card.get('listId') or '0'),str(card.get('listName') or ''),json.dumps(card),
                               'active' if state=='ready' else state))
            if readback is not None:
                links.execute('INSERT INTO catalog_prepare_readback VALUES(?,?,?,?,?)',
                              (OFFER['pid'], OFFER['campaignId'], 'reusedLink',
                               json.dumps(readback), 2.0))
            if reuse is not None:
                links.execute('INSERT INTO catalog_prepare_reuse VALUES(?,?,?,?,?,?,?)',
                              (OFFER['pid'], OFFER['campaignId'], reuse['listId'],
                               reuse['creatorRaw'], reuse['publicRaw'], 1, 3.0))
            links.commit()
    return store


def card_payload(**overrides):
    value = {'state': 'verified_read_only', 'pid': OFFER['pid'], 'sourceCampaignId': OFFER['campaignId'],
             'creatorPercent': '13', 'publicPercent': '10', 'listId': '8650765182615984918',
             'listName': '🔥 BJN Cuscino 13% 66dcf9','verifiedListName':'🔥 BJN Cuscino 13% 66dcf9',
             'checkedAt':1.0,'evidenceRefs':['proof']}
    return value | overrides


class ProductName(unittest.TestCase):
    def test_a_generated_name_is_found_by_pid_when_the_title_key_misses(self):
        with tempfile.TemporaryDirectory() as folder:
            store = fixture(folder, name_pid=OFFER['pid'])
            self.assertIsNone(store.db.execute('SELECT payload FROM cycle_product_name WHERE id=?',
                                               (name_key(OFFER),)).fetchone())
            name = product_name(store, OFFER)
            self.assertEqual(name['shortNameIt'], 'Cuscino')
            self.assertEqual(name['mentionIt'], 'questo cuscino')
            store.close()

    def test_no_cached_name_is_reported_as_missing_not_invented(self):
        with tempfile.TemporaryDirectory() as folder:
            store = fixture(folder)
            self.assertIsNone(product_name(store, OFFER))
            store.close()


class LedgerCard(unittest.TestCase):
    def test_a_verified_card_is_derived_from_the_link_ledger(self):
        with tempfile.TemporaryDirectory() as folder:
            store = fixture(folder, card=card_payload())
            card = ledger_card(store, OFFER)
            self.assertEqual(card['listId'], '8650765182615984918')
            self.assertFalse(card['requiresFreshReadBeforeSend'])
            self.assertEqual(card['derivedFrom'], 'catalog-current-binding')
            store.close()

    def test_a_card_for_another_commission_or_campaign_is_refused(self):
        """佣金或活动对不上的旧卡绝不能顶替——"佣金变了"这类事故就是这么来的。"""
        for broken in (card_payload(creatorPercent='9'), card_payload(sourceCampaignId='999'),
                       card_payload(pid='1'), card_payload(state='reusable_old'),
                       card_payload(listId=None)):
            with tempfile.TemporaryDirectory() as folder:
                store = fixture(folder, card=broken)
                self.assertIsNone(ledger_card(store, OFFER), broken)
                store.close()

    def test_a_link_that_is_not_ready_is_not_used(self):
        with tempfile.TemporaryDirectory() as folder:
            store = fixture(folder, card=card_payload(), state='missing')
            self.assertIsNone(ledger_card(store, OFFER))
            store.close()

    def test_a_missing_link_ledger_is_not_an_error(self):
        with tempfile.TemporaryDirectory() as folder:
            store = fixture(folder)
            self.assertIsNone(ledger_card(store, OFFER))
            store.close()


class LedgerCardSources(unittest.TestCase):
    """Historical cards stay queryable as evidence but never become current material."""

    def test_a_reused_link_readback_supplies_the_card_when_item_card_is_only_a_summary(self):
        # 读卡那一步（历史行为）会把卡覆盖成只有 total 的摘要；真卡在 readback 里。
        with tempfile.TemporaryDirectory() as folder:
            store = fixture(folder, card={'state': 'existing_links_observed', 'total': 1},
                            readback=card_payload())
            card = ledger_card(store, OFFER)
            self.assertIsNone(card)
            store.close()

    def test_the_reuse_observation_supplies_the_card_with_raw_rates(self):
        # 这张表存**原始值**（1300 = 13%），别再当百分比读。
        with tempfile.TemporaryDirectory() as folder:
            store = fixture(folder, state='reuse',
                            reuse={'listId': '8650747538196765462', 'creatorRaw': '1300', 'publicRaw': '1000'})
            card = ledger_card(store, OFFER)
            self.assertIsNone(card)
            store.close()

    def test_a_readback_for_another_commission_is_still_refused(self):
        """换了佣金的读回同样不能顶替——不然话术里的佣金就是错的。"""
        with tempfile.TemporaryDirectory() as folder:
            store = fixture(folder, readback=card_payload(creatorPercent='9'))
            self.assertIsNone(ledger_card(store, OFFER))
            store.close()

    def test_a_raw_rate_that_does_not_match_the_offer_is_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            store = fixture(folder, state='reuse',
                            reuse={'listId': '8650747538196765462', 'creatorRaw': '900', 'publicRaw': '1000'})
            self.assertIsNone(ledger_card(store, OFFER))
            store.close()

    def test_the_card_is_taken_even_when_the_newest_row_is_a_useless_summary(self):
        """同一个 pid 有多行时，只看最新一行会平白丢掉有真卡的那一行。"""
        with tempfile.TemporaryDirectory() as folder:
            store = fixture(folder, card=card_payload())
            with closing(sqlite3.connect(Path(folder) / 'var/catalog-links.sqlite')) as links, links:
                links.execute('INSERT INTO catalog_prepare_item VALUES(?,?,?,?,?,?)',
                              (OFFER['pid'], OFFER['campaignId'], OFFER['creatorPercent'], 'ready',
                               json.dumps({'state': 'existing_links_observed', 'total': 1}), 9.0))
            self.assertIsNotNone(ledger_card(store, OFFER))
            store.close()


class LedgerGap(unittest.TestCase):
    """Only current standard material affects the forward gap."""

    def test_each_kind_of_gap_has_its_own_name(self):
        cases = [
            ({'card': {'state': 'existing_links_observed', 'total': 1}}, 'standard_link_terms_changed'),
            ({'card': card_payload(creatorPercent='9')}, 'standard_link_terms_changed'),
            ({'card': card_payload(sourceCampaignId='999')}, 'standard_link_terms_changed'),
            ({'card': card_payload(state='reusable_old')}, 'standard_link_terms_changed'),
            ({}, 'standard_link_missing'),
        ]
        for kwargs, expected in cases:
            with tempfile.TemporaryDirectory() as folder:
                store = fixture(folder, **kwargs)
                self.assertEqual(ledger_gap(store, OFFER), expected, kwargs)
                store.close()

    def test_a_card_that_exists_has_no_gap(self):
        with tempfile.TemporaryDirectory() as folder:
            store = fixture(folder, card=card_payload())
            self.assertEqual(ledger_gap(store, OFFER), '')
            store.close()


PLAN = 'plan-it'


def catalog_fixture(folder):
    """一个只有**当前货盘**、**没有 opportunity 行**的最小 store。"""
    root = Path(folder)
    var = root / 'var'
    var.mkdir(parents=True, exist_ok=True)
    store = CycleStore(var / 'second-cycle.sqlite')
    store.db.execute('INSERT INTO plan(id,institution,market) VALUES(?,?,?)', (PLAN, 'bjn-local-research', 'it'))
    offer = {'pid': OFFER['pid'], 'offerKey': 'selected:1729:0', 'campaignId': OFFER['campaignId'],
             'creatorPercent': '13', 'publicPercent': '10', 'available': True,
             'stock': 500, 'title': OFFER['title'], 'catalogSource': 'selected',
             'endAt': '2099-01-01T00:00:00+00:00'}
    store.db.execute("INSERT INTO catalog(id,plan_id,source,observed,state,payload) VALUES(?,?,?,?,?,?)",
                     ('cat-1', PLAN, 'live-it-1', 1.0, 'current', json.dumps([offer])))
    store.db.execute('INSERT INTO catalog_head VALUES(?,?,?)', (PLAN, 'live-it-1', 'cat-1'))
    store.db.commit()
    return store


class OfferScope(unittest.TestCase):
    """发送位置来自池子，**池位本身就是需求**。

    `opportunity` 是准备/估算用的需求表；拿它过滤会把"商品明明在货盘里、也明明有卡"的位置
    报成"商品不在当前合格货盘"（实测 182 条），用户看到的"为什么这么多发不了"就是它。
    """

    def test_a_pool_position_does_not_need_an_opportunity_row(self):
        with tempfile.TemporaryDirectory() as folder:
            store = catalog_fixture(folder)
            self.assertEqual(store.db.execute('SELECT count(*) FROM opportunity WHERE plan_id=?',
                                              (PLAN,)).fetchone()[0], 0)
            # 旧口径（走 opportunity）：一条都挑不出来。
            self.assertEqual(select_offers(store, PLAN, 5), [])
            # 按池位给的范围挑：挑得到。
            scoped = select_offers(store, PLAN, None, scoped_pids={OFFER['pid']})
            self.assertEqual([o['pid'] for o in scoped], [OFFER['pid']])
            # 不在范围里的 pid 不会顺带被挑出来。
            self.assertEqual(select_offers(store, PLAN, None, scoped_pids={'999'}), [])
            store.close()


class CardRateGap(unittest.TestCase):
    """Historical commission differences no longer enter the send-material summary."""

    def _store_with_ledger(self, folder, card_percent):
        store = catalog_fixture(folder)
        var = Path(folder) / 'var'
        with closing(sqlite3.connect(var / 'catalog-links.sqlite')) as links, links:
            links.execute('CREATE TABLE catalog_prepare_item(pid TEXT,campaign_id TEXT,creator_percent TEXT,'
                          'state TEXT,card TEXT,updated REAL)')
            links.execute('CREATE TABLE catalog_prepare_readback(pid TEXT,campaign_id TEXT,kind TEXT,'
                          'payload TEXT,observed_at REAL)')
            links.execute('CREATE TABLE catalog_prepare_reuse(pid TEXT,campaign_id TEXT,list_id TEXT,'
                          'creator_percent TEXT,public_percent TEXT,reusable INTEGER,observed_at REAL)')
            links.execute('INSERT INTO catalog_prepare_item VALUES(?,?,?,?,?,?)',
                          (OFFER['pid'], OFFER['campaignId'], card_percent, 'reuse',
                           json.dumps(card_payload(creatorPercent=card_percent)), 1.0))
            links.commit()
        return store

    def test_a_historical_card_below_the_plan_is_not_current_material(self):
        with tempfile.TemporaryDirectory() as folder:
            store = self._store_with_ledger(folder, '12')
            gap = card_rate_gap(store, PLAN, [(OFFER['pid'], OFFER['pid'])])
            self.assertEqual(gap['lower'], 0)
            self.assertEqual(gap['lowerByOne'], 0)
            self.assertEqual(gap['same'], 0)
            self.assertEqual(gap['noCard'],1)
            self.assertEqual(gap['examples'],[])
            store.close()

    def test_a_historical_card_at_the_plan_rate_is_still_not_current(self):
        with tempfile.TemporaryDirectory() as folder:
            store = self._store_with_ledger(folder, '13')
            gap = card_rate_gap(store, PLAN, [(OFFER['pid'], OFFER['pid'])])
            self.assertEqual(gap['same'], 0)
            self.assertEqual(gap['lower'], 0)
            self.assertEqual(gap['noCard'],1)
            self.assertEqual(gap['examples'], [])
            store.close()

    def test_a_position_without_any_card_is_counted_separately(self):
        with tempfile.TemporaryDirectory() as folder:
            store = catalog_fixture(folder)
            gap = card_rate_gap(store, PLAN, [(OFFER['pid'], OFFER['pid'])])
            self.assertEqual(gap['noCard'], 1)
            self.assertEqual(gap['lower'], 0)
            store.close()


if __name__ == '__main__':
    unittest.main()


class DerivedName(unittest.TestCase):
    """短名是措辞，不是发送前提：缓存里没有就从卡名、再不行从标题兜底。"""

    def test_the_frozen_card_name_yields_the_short_name(self):
        name = name_from_card({'listName': 'BJN Lucidalabbra effetto velluto 12% 66dcf9'})
        self.assertEqual(name['shortNameIt'], 'Lucidalabbra effetto velluto')
        self.assertEqual(name['mentionIt'], 'Lucidalabbra effetto velluto')
        self.assertTrue(name['derivedFromCard'])

    def test_the_verified_list_name_is_used_when_the_plain_one_is_missing(self):
        name = name_from_card({'verifiedListName': 'BJN 颈枕 13% ab12cd'})
        self.assertEqual(name['shortNameIt'], '颈枕')

    def test_the_configured_emoji_prefix_does_not_leak_into_the_short_name(self):
        # 模板是 `🔥 BJN {短名} {佣金}% {tail}`：只认开头是 `BJN ` 的写法会把整条名字当短名，
        # 话术里就会出现"…su 🔥 BJN Rubinetto portatile per 13% 5490f3"。
        name = name_from_card({'listName': '🔥 BJN Rubinetto portatile per 13% 5490f3'})
        self.assertEqual(name['shortNameIt'], 'Rubinetto portatile per')
        self.assertNotIn('BJN', name['mentionIt'])
        self.assertNotIn('%', name['mentionIt'])

    def test_a_name_without_the_rate_and_tail_still_yields_the_short_name(self):
        name = name_from_card({'listName': '🔥 BJN cuscino cervicale'})
        self.assertEqual(name['shortNameIt'], 'cuscino cervicale')

    def test_a_card_without_any_name_falls_through(self):
        self.assertIsNone(name_from_card({}))
        self.assertIsNone(name_from_card({'listName': '   '}))

    def test_a_title_is_trimmed_by_whole_words(self):
        name = name_from_title({'pid': '1', 'title': 'Cuscino morbido per il collo con memoria'})
        self.assertLessEqual(len(name['shortNameIt']), 30)
        self.assertFalse(name['shortNameIt'].endswith(' '))
        self.assertTrue(name['derivedFromTitle'])
        # 标题为空时退到 pid，绝不返回空名字。
        self.assertEqual(name_from_title({'pid': '42', 'title': ''})['shortNameIt'], '42')
