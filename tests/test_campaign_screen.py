"""非全托筛分：口径、原因码、以及「一个商品只算一次原因」。"""
import json
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from lib.campaign_screen import (SOURCES, build, evaluate, pick, pool, recorded, record,  # noqa: E402
                                 run_id, status)
from lib.cycle_catalog import commission_calculator, commission_rule  # noqa: E402

AT = 1789257600
RULE = commission_rule(ROOT)
CALC = commission_calculator(RULE)


def offer(pid='1' * 19, campaign='900', *, total='15', public='12', stock='101',
          end=None, available=True, campaign_type='5', **rest):
    """One normalized campaign offer, as the catalogue snapshot stores it."""
    end_at = end or (datetime.fromtimestamp(AT, timezone.utc) + timedelta(days=200)).isoformat()
    return {'pid': pid, 'campaignId': campaign, 'offerKey': f'campaign:{pid}:{campaign}',
            'catalogSource': 'campaign', 'totalPercent': total, 'publicPercent': public,
            'stock': stock, 'endAt': end_at, 'available': available, 'rating': '4.5',
            'creatorPercent': None, 'campaignType': campaign_type, **rest}


def fixture(folder, offers, *, source='campaign'):
    var = Path(folder) / 'var'
    var.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(var / 'second-cycle.sqlite') as conn:
        conn.executescript('''
            CREATE TABLE plan(id TEXT,institution TEXT,market TEXT,state TEXT);
            CREATE TABLE catalog(id TEXT PRIMARY KEY,plan_id TEXT,source TEXT,observed REAL,state TEXT,payload TEXT);
            CREATE TABLE catalog_head(plan_id TEXT,source TEXT,snapshot_id TEXT,PRIMARY KEY(plan_id,source));''')
        conn.execute("INSERT INTO plan VALUES('p1','bjn-local-research','it','active')")
        conn.execute('INSERT INTO catalog VALUES(?,?,?,?,?,?)',
                     ('snap-1', 'p1', f'live-it-{source}', float(AT), 'complete',
                      json.dumps(offers, ensure_ascii=False)))
        conn.execute('INSERT INTO catalog_head VALUES(?,?,?)', ('p1', f'live-it-{source}', 'snap-1'))
        conn.commit()
    return var


class Verdict(unittest.TestCase):
    def evaluate(self, value):
        return evaluate(value, calculate=CALC, at=AT)

    def test_an_eligible_offer_gets_the_confirmed_commission(self):
        item = self.evaluate(offer())
        self.assertEqual(item['state'], 'eligible')
        self.assertEqual(item['reasons'], [])
        self.assertEqual(item['creatorPercent'], '13')     # 总15 公开12 → 机构2 达人13

    def test_a_thin_gap_is_ineligible_and_counted_once(self):
        item = self.evaluate(offer(total='13', public='12'))
        self.assertEqual(item['state'], 'ineligible')
        self.assertEqual(item['reasons'], ['insufficient_commission_gap'])
        # 佣金是我们自己算的：不足就是不足，不能同时再报一次「平台没给达人佣金」。
        self.assertNotIn('missing_creatorPercent', item['reasons'])

    def test_a_missing_public_commission_is_named_by_its_own_field(self):
        item = self.evaluate(offer(public=None))
        self.assertIn('missing_publicPercent', item['reasons'])
        self.assertNotIn('missing_creatorPercent', item['reasons'])

    def test_a_missing_total_is_named_once(self):
        item = self.evaluate(offer(total=None))
        self.assertIn('missing_total_commission', item['reasons'])
        self.assertNotIn('missing_creatorPercent', item['reasons'])

    def test_the_stock_gate_applies_to_campaign_and_disappears_for_full_managed(self):
        self.assertIn('stock_not_over_100', self.evaluate(offer(stock='100'))['reasons'])
        self.assertIn('missing_stock', self.evaluate(offer(stock=None))['reasons'])
        # 全托身份有官方证据时不看库存（库存例外不扩散，只对全托生效）。
        full = offer(stock='0', managementType='full_managed', managementEvidenceRef='official:1')
        self.assertNotIn('stock_not_over_100', self.evaluate(full)['reasons'])
        self.assertNotIn('missing_stock', self.evaluate(offer(stock=None, managementType='full_managed',
                                                             managementEvidenceRef='official:1'))['reasons'])

    def test_unavailable_and_short_expiry_are_both_reported(self):
        soon = (datetime.fromtimestamp(AT, timezone.utc) + timedelta(days=10)).isoformat()
        item = self.evaluate(offer(available=False, end=soon))
        self.assertIn('unavailable', item['reasons'])
        self.assertIn('expiry_not_over_45_days', item['reasons'])


class Build(unittest.TestCase):
    def test_the_funnel_counts_offers_pids_and_the_multi_campaign_ones(self):
        with tempfile.TemporaryDirectory() as folder:
            pids = ['1' * 19, '2' * 19]
            offers = [offer(pid=pids[0], campaign='900'),
                      offer(pid=pids[0], campaign='901'),           # 同一 PID 两个活动 → 要挑
                      offer(pid=pids[1], campaign='900'),
                      offer(pid=pids[1], campaign='902', total='13', public='12')]  # 差不足
            fixture(folder, offers)
            built = build(folder, source='campaign', rule=RULE, at=AT)
            self.assertTrue(built['available'])
            self.assertEqual(built['offers'], 4)
            self.assertEqual(built['counts'], {'eligible': 3, 'ineligible': 1})
            self.assertEqual(built['eligiblePids'], 2)
            self.assertEqual(built['multiCampaignPids'], 1)
            self.assertEqual(built['reasons'], {'insufficient_commission_gap': 1})

    def test_a_workspace_without_the_snapshot_says_so_instead_of_zero(self):
        with tempfile.TemporaryDirectory() as folder:
            payload = status(folder, source='campaign')
            self.assertFalse(payload['available'])
            self.assertEqual(payload['reason'], 'snapshot_missing')

    def test_the_run_id_is_stable_for_the_same_snapshot_and_rule(self):
        self.assertEqual(run_id('campaign', 'snap-1', RULE), run_id('campaign', 'snap-1', RULE))
        self.assertNotEqual(run_id('campaign', 'snap-1', RULE), run_id('selected', 'snap-1', RULE))

    def test_only_the_two_channels_are_accepted(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(SOURCES, ('campaign', 'selected'))
            with self.assertRaises(ValueError):
                build(folder, source='other', rule=RULE, at=AT)


class Pool(unittest.TestCase):
    """入池：一个 PID 一条，只带被选中的活动；不合格的 PID 不硬挑。"""

    def test_one_pid_with_both_an_eligible_and_an_ineligible_campaign_still_gets_one_row(self):
        # 这个坑真的踩过：按 offer 状态分桶会让这种 PID 在"已入池"和"未入池"里各出现一次。
        with tempfile.TemporaryDirectory() as folder:
            fixture(folder, [offer(pid='1' * 19, campaign='900'),
                             offer(pid='1' * 19, campaign='901', total='13', public='12')])
            built = build(folder, source='campaign', rule=RULE, at=AT)
            projected = built['pool']
            self.assertEqual(projected['counts'], {'chosen': 1})
            self.assertEqual(len(projected['items']), 1)
            self.assertEqual(projected['items'][0]['campaignId'], '900')
            self.assertTrue(projected['reconciled'])

    def test_the_rule_prefers_commission_then_later_end_then_smaller_campaign_id(self):
        later = (datetime.fromtimestamp(AT, timezone.utc) + timedelta(days=365)).isoformat()
        earlier = (datetime.fromtimestamp(AT, timezone.utc) + timedelta(days=200)).isoformat()
        # 佣金更高者胜，即使截止更早
        self.assertEqual(pick([{'campaignId': '1', 'creatorPercent': '10', 'endAt': later},
                               {'campaignId': '2', 'creatorPercent': '15', 'endAt': earlier}])['campaignId'], '2')
        # 佣金相同 → 截止更晚者胜
        self.assertEqual(pick([{'campaignId': '3', 'creatorPercent': '10', 'endAt': earlier},
                               {'campaignId': '2', 'creatorPercent': '10', 'endAt': later}])['campaignId'], '2')
        # 都相同 → 活动 ID 最小（确定性）
        self.assertEqual(pick([{'campaignId': '9', 'creatorPercent': '10', 'endAt': later},
                               {'campaignId': '7', 'creatorPercent': '10', 'endAt': later}])['campaignId'], '7')

    def test_a_pid_without_any_eligible_campaign_is_held_with_its_reasons(self):
        with tempfile.TemporaryDirectory() as folder:
            fixture(folder, [offer(pid='2' * 19, campaign='900', total='13', public='12')])
            projected = build(folder, source='campaign', rule=RULE, at=AT)['pool']
            self.assertEqual(projected['counts'], {'held': 1})
            self.assertEqual(projected['items'][0]['campaignId'], None)
            self.assertEqual(projected['items'][0]['reasons'], ['insufficient_commission_gap'])

    def test_the_losing_campaigns_are_kept_as_alternatives(self):
        with tempfile.TemporaryDirectory() as folder:
            fixture(folder, [offer(pid='3' * 19, campaign='900', total='15', public='12'),
                             offer(pid='3' * 19, campaign='901', total='15', public='12'),
                             offer(pid='3' * 19, campaign='902', total='13', public='12')])
            item = build(folder, source='campaign', rule=RULE, at=AT)['pool']['items'][0]
            self.assertEqual(item['campaignId'], '900')
            self.assertEqual([a['campaignId'] for a in item['alternatives']], ['901'])

    def test_the_projection_is_the_only_place_a_pid_appears(self):
        with tempfile.TemporaryDirectory() as folder:
            fixture(folder, [offer(pid='4' * 19, campaign='900'), offer(pid='5' * 19, campaign='901'),
                             offer(pid='5' * 19, campaign='902', stock='100')])
            projected = pool(build(folder, source='campaign', rule=RULE, at=AT))
            pids = [item['pid'] for item in projected['items']]
            self.assertEqual(len(pids), len(set(pids)))
            self.assertTrue(projected['reconciled'])


class Recorded(unittest.TestCase):
    def test_a_recorded_run_reads_back_with_its_counts(self):
        with tempfile.TemporaryDirectory() as folder:
            fixture(folder, [offer(), offer(pid='2' * 19, total='13', public='12')])
            built = build(folder, source='campaign', rule=RULE, at=AT)
            self.assertIsNone(recorded(folder, 'campaign'))
            record(folder, built, clock=lambda: 1000.0)
            saved = recorded(folder, 'campaign')
            self.assertEqual(saved['runId'], built['runId'])
            self.assertEqual(saved['snapshot'], 'snap-1')
            self.assertEqual(saved['counts']['states'], {'eligible': 1, 'ineligible': 1})
            self.assertEqual(saved['counts']['eligiblePids'], 1)
            self.assertEqual(saved['poolCounts'], {'chosen': 1, 'held': 1})
            # Recording twice replaces the run instead of duplicating rows.
            record(folder, built, clock=lambda: 1001.0)
            with sqlite3.connect(Path(folder) / 'var/campaign-screen.sqlite') as conn:
                self.assertEqual(conn.execute('SELECT count(*) FROM campaign_screen').fetchone()[0], 2)
                self.assertEqual(conn.execute('SELECT count(*) FROM campaign_pool_item').fetchone()[0], 2)

    def test_an_unavailable_build_cannot_be_recorded(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ValueError):
                record(folder, {'available': False})


class LinkTargets(unittest.TestCase):
    """建链目标：已入池商品（一个 PID 一条）＋按**当前**规则重算的佣金，并支持跨渠道排除。"""
    def prepare(self, folder, offers, *, record_run=True):
        fixture(folder, offers)
        config = Path(folder) / 'config'
        config.mkdir(parents=True, exist_ok=True)
        (config / 'catalog-link-policy.json').write_text((ROOT / 'config/catalog-link-policy.json').read_text())
        if record_run:
            record(folder, build(folder, source='campaign', rule=RULE, at=AT), clock=lambda: 1000.0)

    def test_targets_come_from_the_chosen_pool_with_recomputed_commission(self):
        from lib.campaign_screen import link_targets
        with tempfile.TemporaryDirectory() as folder:
            self.prepare(folder, [offer(pid='1' * 19, campaign='900', total='15', public='12'),
                                  offer(pid='2' * 19, campaign='900', total='13', public='12')])
            built = link_targets(folder)
            self.assertTrue(built['available'])
            self.assertEqual(built['runId'], run_id('campaign', 'snap-1', RULE))
            self.assertEqual([t['pid'] for t in built['targets']], ['1' * 19])
            target = built['targets'][0]
            self.assertEqual((target['campaignId'], target['catalogSource']), ('900', 'campaign'))
            # 佣金按当前规则重算：总15 公开12 → 机构2 达人13
            self.assertEqual((target['creatorPercent'], target['agencyPercent'], target['totalPercent']), ('13', '2', '15'))
            self.assertEqual(target['offer']['stats']['creatorRaw'], 1300)
            # 非全托不是全托：库存门槛必须留着
            self.assertIsNone(target['offer'].get('managementType'))
            self.assertEqual(target['offer']['listing']['stock'], '101')

    def test_exclude_drops_cross_channel_pids_and_says_so(self):
        from lib.campaign_screen import link_targets
        with tempfile.TemporaryDirectory() as folder:
            self.prepare(folder, [offer(pid='1' * 19), offer(pid='2' * 19)])
            built = link_targets(folder, exclude=['1' * 19])
            self.assertEqual([t['pid'] for t in built['targets']], ['2' * 19])
            self.assertEqual(built['skipped']['excluded'], 1)

    def test_a_chosen_pid_missing_from_the_snapshot_is_reported_not_silently_dropped(self):
        from lib.campaign_screen import link_targets
        with tempfile.TemporaryDirectory() as folder:
            self.prepare(folder, [offer(pid='1' * 19)])
            # 池子里的商品在快照里被换掉：必须如实报"计划缺失"，不能静默少建一条空链。
            conn = sqlite3.connect(Path(folder) / 'var/campaign-screen.sqlite')
            conn.execute("UPDATE campaign_pool_item SET campaign_id='999' WHERE pid=?", ('1' * 19,))
            conn.commit(); conn.close()
            built = link_targets(folder)
            self.assertEqual(built['targets'], [])
            self.assertEqual(built['skipped']['plan_missing'], 1)

    def test_no_recorded_pool_is_reported_rather_than_guessed(self):
        from lib.campaign_screen import SCHEMA, link_targets
        with tempfile.TemporaryDirectory() as folder:
            self.prepare(folder, [offer()], record_run=False)
            built = link_targets(folder)
            self.assertEqual((built['available'], built['reason'], built['targets']),
                             (False, 'campaign_screen_missing', []))
            # 账本在、但还没有池子 run：要说清楚"池子还没算过"，不能报成"目标为零"。
            conn = sqlite3.connect(Path(folder) / 'var/campaign-screen.sqlite')
            conn.executescript(SCHEMA); conn.commit(); conn.close()
            built = link_targets(folder)
            self.assertEqual((built['available'], built['reason'], built['targets']),
                             (False, 'campaign_pool_missing', []))


if __name__ == '__main__':
    unittest.main()
