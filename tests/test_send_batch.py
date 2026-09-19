"""从发送池到正式发送：按池子顺序播种、额度/窗口闸门、跳过原因（只读预检）。"""
import json
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from lib.second_cycle import CycleError  # noqa: E402
from lib.schema_migrations import apply_database  # noqa: E402
from lib.send_batch import (CONFIG_DEFAULT, NEW_CONTACT_LIMIT, _window_arg,  # noqa: E402
                            capacity, freeze_batch, load_config, preview, save_and_status,
                            promote_reserves, save_config, start_batch, status, stop_batch,
                            validate_config, window_state)

# 2026-09-15 12:00 北京时间（窗口判定按北京时间，所以这个数必须算准）。
NOON = 1789444800.0


def slot(creator, pid):
    return {'creatorId': creator, 'pid': pid, 'layer': 'ready'}


def candidate(creator, pid):
    return {'creatorId': creator, 'pid': pid, 'handle': creator, 'oecId': '1' + creator,
            'offer': {'pid': pid, 'offerKey': f'selected:{pid}:0', 'creatorPercent': '13',
                      'publicPercent': '10', 'totalPercent': '15', 'campaignId': '7',
                      'catalogSource': 'selected', 'stock': '101', 'available': True,
                      'endAt': 2000000000},
            'offerFingerprint': 'a' * 64, 'planRevision': 1, 'controlRevision': 1,
            'name': {'shortNameZh': '商品' + pid, 'shortNameIt': 'prodotto',
                     'mentionIt': 'questo prodotto'}, 'nameSource': '缓存',
            'card': {'listId': '9' + str(pid), 'pid': pid, 'sourceCampaignId': '7',
                     'wireCampaignId': '0', 'listName': 'BJN prodotto 13%',
                     'campaignName': '', 'checkedAt': NOON, 'evidenceRefs': ['proof'],
                     'state': 'verified_read_only', 'creatorPercent': '13', 'publicPercent': '10'},
            'identityEvidence': 'identity-proof', 'relationshipUnlocked': False,
            'source': {'sourceId': 's-' + pid, 'windowEnd': 1, 'units': 1}}


class Window(unittest.TestCase):
    def test_no_window_means_always_open(self):
        state = window_state(None, NOON)
        self.assertFalse(state['enabled'])
        self.assertTrue(state['open'])

    def test_inside_and_outside(self):
        self.assertTrue(window_state(('09:00', '24:00'), NOON)['open'])
        self.assertFalse(window_state(('03:00', '04:00'), NOON)['open'])

    def test_24_00_is_the_end_of_the_day_not_an_invalid_hour(self):
        self.assertEqual(_window_arg(['09:00', '24:00']), ('09:00', '24:00'))
        for bad in [['25:00', '26:00'], ['09:00', '09:00'], ['09:00'], None]:
            if bad is None:
                self.assertIsNone(_window_arg(None))
            else:
                with self.assertRaises(CycleError):
                    _window_arg(bad)


def capacity_fixture(folder, *, reserved=0, delivered=0):
    root = Path(folder)
    var = root / 'var'
    var.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(var / 'second-cycle.sqlite') as conn:
        conn.executescript('''
            CREATE TABLE plan(id TEXT,institution TEXT,market TEXT,state TEXT);
            CREATE TABLE cycle_contact_reservation(plan_id TEXT,oec TEXT,reserved REAL);
            CREATE TABLE cycle_delivery(id TEXT,plan_id TEXT,creator_id TEXT,oec TEXT,state TEXT);
            CREATE TABLE cycle_delivery_part(delivery_id TEXT,kind TEXT,state TEXT,started REAL);''')
        conn.execute("INSERT INTO plan VALUES('p','bjn-local-research','it','active')")
        for index in range(reserved):
            conn.execute('INSERT INTO cycle_contact_reservation VALUES(?,?,?)',
                         ('p', f'res{index}', NOON - 60))
        for index in range(delivered):
            conn.execute('INSERT INTO cycle_delivery VALUES(?,?,?,?,?)',
                         (f'd{index}', 'p', f'c{index}', f'del{index}', 'confirmed'))
            conn.execute("INSERT INTO cycle_delivery_part VALUES(?,?,?,?)",
                         (f'd{index}', 'card', 'confirmed', NOON - 120))
        conn.commit()
    # 身份库也要在：`preview` 会只读地拿"当前 handle"，缺文件会直接报打不开。
    with sqlite3.connect(var / 'creator-identities.sqlite') as identities:
        identities.execute('CREATE TABLE IF NOT EXISTS creator_identity('
                           'creator_id TEXT,oec_id TEXT,market TEXT,current_handle TEXT,handle_conflict INTEGER)')
        identities.commit()
    return root


class Capacity(unittest.TestCase):
    def test_only_the_last_24_hours_count(self):
        with tempfile.TemporaryDirectory() as folder:
            root = capacity_fixture(folder, reserved=3, delivered=4)
            state = capacity(root, now=NOON)
            self.assertEqual((state['limit'], state['used'], state['remaining']),
                             (NEW_CONTACT_LIMIT, 7, NEW_CONTACT_LIMIT - 7))

    def test_an_old_delivery_falls_out_of_the_window(self):
        with tempfile.TemporaryDirectory() as folder:
            root = capacity_fixture(folder, delivered=2)
            var = root / 'var'
            with sqlite3.connect(var / 'second-cycle.sqlite') as conn:
                conn.execute('UPDATE cycle_delivery_part SET started=?', (NOON - 90000,))
                conn.commit()
            self.assertEqual(capacity(root, now=NOON)['used'], 0)


class Preview(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = capacity_fixture(self.tmp.name)
        self.calls = []

    def tearDown(self):
        self.tmp.cleanup()

    def run_preview(self, slots, accepted, **kwargs):
        def pool_reader():
            return {'available': True, 'layers': {'ready': len(slots)}, 'pools': {'ready': slots}}

        def chooser(store, plan, person, limit, positions=None):
            self.calls.append(('positions', list(positions)))
            keep = {str(pid) for pid in accepted}
            return ([candidate(c, p) for c, p in positions if str(p) in keep], [])

        return preview(self.root, pool_reader=pool_reader, chooser=chooser, now=NOON, **kwargs)

    def test_the_pool_order_is_the_send_order(self):
        slots = [slot(f'c{i}', f'p{i}') for i in range(5)]
        state = self.run_preview(slots, [f'p{i}' for i in range(5)], count=3)
        # 取满可发层（不只看前 3 个槽位），按池子顺序挑，样例就是前 3 条。
        self.assertEqual(self.calls[0][1], [(f'c{i}', f'p{i}') for i in range(5)])
        self.assertEqual(state['sendable'], 3)
        self.assertEqual((state['reserveRequested'],state['reserveReady'],state['frozenTotal']),(1,1,4))
        self.assertTrue(state['fullPreparation'])
        self.assertEqual([s['pid'] for s in state['samples']], ['p0', 'p1', 'p2'])

    def test_it_walks_past_slots_the_gate_rejects(self):
        slots = [slot(f'c{i}', f'p{i}') for i in range(6)]
        # 前 4 个槽位过不了复检（缺卡片/被控制），批次仍要从后面凑满。
        state = self.run_preview(slots, ['p4', 'p5'], count=2)
        self.assertEqual(state['sendable'], 2)
        self.assertEqual([s['pid'] for s in state['samples']], ['p4', 'p5'])

    def test_the_local_capacity_gate_stops_the_batch_unless_widened(self):
        with tempfile.TemporaryDirectory() as folder:
            self.root = capacity_fixture(folder, reserved=NEW_CONTACT_LIMIT - 2)
            slots = [slot(f'c{i}', f'p{i}') for i in range(5)]
            state = self.run_preview(slots, [f'p{i}' for i in range(5)], count=5)
            self.assertEqual(state['sendable'], 2)
            self.assertEqual(state['skipped'].get('local_capacity_reached'), 3)
            # 越界探测：显式越过本地闸门，额度不再限制这一批。
            wide = self.run_preview(slots, [f'p{i}' for i in range(5)], count=5, widen=True)
            self.assertEqual(wide['sendable'], 5)
            self.assertNotIn('local_capacity_reached', wide['skipped'])
            self.assertTrue(wide['authorization']['widenLocalGate'])

    def test_a_closed_window_still_allows_freezing_but_not_dispatch(self):
        slots = [slot(f'c{i}', f'p{i}') for i in range(4)]
        state = self.run_preview(slots, [f'p{i}' for i in range(4)], count=4,
                                 window=('03:00', '04:00'))
        self.assertEqual(state['sendable'], 4)
        self.assertNotIn('outside_send_window', state['skipped'])
        self.assertFalse(state['window']['open'])

    def test_the_authorization_records_what_was_actually_allowed(self):
        state = self.run_preview([slot('c0', 'p0'),slot('c1','p1')], ['p0','p1'], count=1)
        self.assertEqual(state['authorization']['maxPeople'], 1)
        self.assertEqual(state['authorization']['scope'], 'pool_to_send')
        self.assertEqual((state['authorization']['reservePeople'],state['authorization']['frozenPeople']),(1,2))
        self.assertEqual(state['authorization']['reservePolicy'],'ceil-10-percent-v1')
        self.assertIsNone(state['authorization']['sendWindow'])

    def test_arbitrary_target_uses_ceiling_ten_percent_reserve(self):
        slots=[slot(f'c{i}',f'p{i}') for i in range(151)]
        state=self.run_preview(slots,[f'p{i}' for i in range(151)],count=137)
        self.assertEqual((state['requested'],state['reserveRequested'],state['required']),(137,14,151))
        self.assertEqual((state['sendable'],state['reserveReady'],state['frozenTotal']),(137,14,151))
        self.assertTrue(state['fullPreparation'])

    def test_an_unavailable_pool_is_not_reported_as_zero_sendable(self):
        state = preview(self.root, pool_reader=lambda: {'available': False}, now=NOON, count=10)
        self.assertFalse(state['available'])
        self.assertEqual(state['sendable'], 0)
        self.assertEqual(state['samples'], [])


class FrozenBatch(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = capacity_fixture(self.tmp.name)
        apply_database(self.root, 'second-cycle', clock=lambda: NOON)
        save_config(self.root, {'count': 1})
        self.slots = [slot('creator0', '100'), slot('creator1', '200')]

    def tearDown(self):
        self.tmp.cleanup()

    def pool_reader(self):
        return {'available': True, 'layers': {'ready': len(self.slots)},
                'pools': {'ready': self.slots}}

    @staticmethod
    def chooser(store, plan, person, limit, positions=None):
        return [candidate(creator, pid) for creator, pid in positions], []

    def freeze(self, request_id='request-0001'):
        shown = preview(self.root, count=1, now=NOON, pool_reader=self.pool_reader,
                        chooser=self.chooser)
        return freeze_batch(self.root, request_id, shown['previewHash'], now=NOON,
                            pool_reader=self.pool_reader, chooser=self.chooser), shown

    def test_freeze_is_idempotent_and_preserves_exact_order_without_platform_effects(self):
        batch, shown = self.freeze()
        duplicate, _ = self.freeze()
        self.assertEqual(batch['batchId'], duplicate['batchId'])
        self.assertEqual(batch['previewHash'], shown['previewHash'])
        self.assertEqual((batch['target'],batch['attempted'],batch['reserveTotal']), (1,1,1))
        with sqlite3.connect(self.root / 'var/second-cycle.sqlite') as db:
            rows = list(db.execute('SELECT position_order,creator_id,pid,candidate_json,candidate_hash '
                                   'FROM cycle_bulk_candidate ORDER BY position_order'))
            self.assertEqual([(row[0], row[1], row[2]) for row in rows],
                             [(0, 'creator0', '100'), (1, 'creator1', '200')])
            self.assertEqual([json.loads(row[3])['batchRole'] for row in rows], ['formal','reserve'])
            self.assertEqual(db.execute('SELECT count(*) FROM cycle_bulk_item').fetchone()[0],1)
            self.assertFalse(db.execute("SELECT 1 FROM sqlite_master WHERE name='cycle_platform_signal'").fetchone())
            with self.assertRaises(sqlite3.DatabaseError):
                db.execute("UPDATE cycle_bulk_candidate SET pid='300'")

    def test_a_changed_preview_is_rejected_before_any_batch_is_written(self):
        shown = preview(self.root, count=1, now=NOON, pool_reader=self.pool_reader,
                        chooser=self.chooser)
        self.slots.reverse()
        with self.assertRaisesRegex(CycleError, 'preview_conflict'):
            freeze_batch(self.root, 'request-0002', shown['previewHash'], now=NOON,
                         pool_reader=self.pool_reader, chooser=self.chooser)
        with sqlite3.connect(self.root / 'var/second-cycle.sqlite') as db:
            self.assertEqual(db.execute('SELECT count(*) FROM cycle_bulk_freeze').fetchone()[0], 0)

    def test_freeze_requires_the_full_target_and_ten_percent_reserve(self):
        self.slots = self.slots[:1]
        shown = preview(self.root, count=1, now=NOON, pool_reader=self.pool_reader, chooser=self.chooser)
        self.assertEqual((shown['sendable'],shown['reserveReady'],shown['required']), (1,0,2))
        self.assertFalse(shown['fullPreparation'])
        with self.assertRaisesRegex(CycleError,'full_preparation_required'):
            freeze_batch(self.root,'request-0004',shown['previewHash'],now=NOON,
                         pool_reader=self.pool_reader,chooser=self.chooser)

    def test_definite_failure_promotes_one_frozen_reserve_but_unknown_does_not(self):
        batch,_=self.freeze('request-0005')
        with sqlite3.connect(self.root/'var/second-cycle.sqlite') as db:
            db.row_factory=sqlite3.Row
            db.execute("UPDATE cycle_bulk SET state='running' WHERE id=?",(batch['batchId'],))
            db.execute("UPDATE cycle_bulk_freeze SET state='running' WHERE batch_id=?",(batch['batchId'],))
            db.execute("INSERT INTO cycle_delivery VALUES(?,?,?,?,?)",('unknown-delivery','p','creator0','1creator0','unknown'))
            db.execute("UPDATE cycle_bulk_item SET state='sending',delivery_id='unknown-delivery' WHERE batch_id=?",(batch['batchId'],))
            db.commit()
        from lib.second_cycle import CycleStore
        with CycleStore(self.root/'var/second-cycle.sqlite') as store:
            self.assertEqual(promote_reserves(store,batch['batchId']),0)
        with sqlite3.connect(self.root/'var/second-cycle.sqlite') as db:
            db.execute("UPDATE cycle_bulk_item SET state='recipient_limit' WHERE batch_id=?",(batch['batchId'],))
            db.execute("UPDATE cycle_delivery SET state='cancelled' WHERE id='unknown-delivery'")
            db.commit()
        with CycleStore(self.root/'var/second-cycle.sqlite') as store:
            self.assertEqual(promote_reserves(store,batch['batchId']),1)
            self.assertEqual(promote_reserves(store,batch['batchId']),0)
        state=status(self.root,pool_reader=lambda:{'available':False})['batch']
        self.assertEqual((state['target'],state['attempted'],state['reservePromoted'],state['reserveRemaining']),(1,2,1,0))

    def test_start_and_stop_use_revisions_and_do_not_implicitly_launch(self):
        batch, _ = self.freeze('request-0003')
        started = start_batch(self.root, batch['batchId'], 1, confirmed=True, now=NOON)
        self.assertEqual((started['state'], started['revision']), ('starting', 2))
        stopped = stop_batch(self.root, batch['batchId'], 2, now=NOON + 1)
        self.assertEqual((stopped['state'], stopped['revision']), ('stop_requested', 3))
        duplicate = stop_batch(self.root, batch['batchId'], 2, now=NOON + 2)
        self.assertTrue(duplicate['duplicate'])
        with self.assertRaisesRegex(CycleError, 'revision_conflict'):
            start_batch(self.root, batch['batchId'], 1, confirmed=True, now=NOON)


if __name__ == '__main__':
    unittest.main()


def test_save_answers_with_the_new_config_already_applied(tmp_path):
    """保存的**回答**必须是保存后的预检。

    反过来的话，页面会拿旧配置的预检去配新配置的数字——真的显示出过"一批 500 条却能发 600"。
    """
    (tmp_path / 'config').mkdir()
    (tmp_path / 'config/send-batch.json').write_text(json.dumps(
        {'count': 600, 'widen': True, 'windowEnabled': False, 'window': ['09:00', '24:00']}),
        encoding='utf-8')
    out = save_and_status(tmp_path, {'count': 500, 'widen': False},
                          pool_reader=lambda: {'available': False})
    assert out['saved'] is True
    assert out['config']['count'] == 500 and out['config']['widen'] is False
    # 预检按新配置算：池子读不到时 requested/widen 也必须已经是新值，不能是旧的 600/True。
    assert out['preview']['requested'] == 500
    assert out['preview']['widen'] is False
    assert out['config'] == status(tmp_path, pool_reader=lambda: {'available': False})['config']


def test_a_saved_config_that_cannot_be_read_back_is_reported_not_faked(tmp_path):
    """配置写坏了不能被当成"读到了"：保存后自己再读一遍必须一致。"""
    (tmp_path / 'config').mkdir()
    out = save_and_status(tmp_path, {'count': 1000, 'windowEnabled': True,
                                     'window': ['09:00', '24:00']},
                          pool_reader=lambda: {'available': False})
    assert out['config'] == {'count': 1000, 'widen': False, 'windowEnabled': True,
                             'window': ['09:00', '24:00']}
    raw = json.loads((tmp_path / 'config/send-batch.json').read_text(encoding='utf-8'))
    assert raw == out['config']


def test_a_broken_config_file_falls_back_to_defaults_without_throwing(tmp_path):
    (tmp_path / 'config').mkdir()
    (tmp_path / 'config/send-batch.json').write_text('{"count": "很多", "widen": 1}', encoding='utf-8')
    assert load_config(tmp_path) == dict(CONFIG_DEFAULT)
    # 写坏的配置要有**具名**的拒绝理由，不能是 `1 <= "很多"` 那种 TypeError：
    # 页面上只会显示"读不到"，而这里能说清是哪一个字段坏了。
    for bad in ({'count': '很多'}, {'count': None}, {'count': 1.5}, {'count': 0}, {'count': 2001}):
        try:
            validate_config(bad)
        except CycleError:
            continue
        raise AssertionError(f'{bad} 应该被具名拒绝')
