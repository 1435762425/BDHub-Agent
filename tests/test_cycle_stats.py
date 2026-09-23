"""按天统计：北京日边界、只算已确认、排除历史补录。"""
import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from lib.cycle_stats import BEIJING, beijing_day, daily, day_bounds, day_detail  # noqa: E402

# 2026-09-15 12:00 北京时间。固定时刻，让边界断言不随"现在"漂移。
NOON = datetime(2026, 9, 15, 12, 0, tzinfo=BEIJING).timestamp()


def at(day, hour, minute=0, second=0):
    return datetime(2026, 9, day, hour, minute, second, tzinfo=BEIJING).timestamp()


def fixture(folder):
    var = Path(folder) / 'var'
    var.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(var / 'second-cycle.sqlite')) as conn, conn:
        conn.executescript('''
            CREATE TABLE plan(id TEXT,institution TEXT,market TEXT);
            CREATE TABLE cycle_delivery(id TEXT,plan_id TEXT,creator_id TEXT,oec TEXT,pid TEXT,
                                        snapshot TEXT,state TEXT);
            CREATE TABLE cycle_delivery_part(delivery_id TEXT,kind TEXT,state TEXT,started REAL);
            CREATE TABLE inbox_event(plan_id TEXT,cid TEXT,message_id TEXT,oec TEXT,kind TEXT,
                                     occurred_ms INTEGER,payload TEXT,historical INTEGER,observed_at REAL);
            CREATE TABLE inbox_content_head(plan_id TEXT,cid TEXT,message_id TEXT,hash TEXT);
            CREATE TABLE inbox_content_version(plan_id TEXT,cid TEXT,message_id TEXT,hash TEXT,
                                                payload TEXT,observed REAL);
            CREATE TABLE relationship(plan_id TEXT,creator_id TEXT,oec TEXT);
            CREATE TABLE service_reply(id TEXT,plan_id TEXT,creator_id TEXT,oec TEXT,text TEXT,
                                       state TEXT,started REAL);
            CREATE TABLE service_case(id TEXT,plan_id TEXT,creator_id TEXT,reason TEXT,state TEXT,
                                      created REAL);''')
        conn.execute("INSERT INTO plan VALUES('p','bjn-local-research','it')")
        # 09-14 23:59:59 与 09-15 00:00:00 各一条：左闭右开，谁也不能跨日。
        for index, (creator, started) in enumerate([('c1', at(14, 23, 59, 59)), ('c2', at(15, 0, 0, 0)),
                                                    ('c3', at(15, 23, 59, 59))]):
            snapshot = {'handle': f'old_{creator}', 'name': {'shortNameIt': f'prodotto {index}'},
                        'offer': {'creatorPercent': '13', 'catalogSource': 'selected'},
                        'message': {'textIt': f'messaggio {index}'}, 'private': 'must-not-leak'}
            conn.execute('INSERT INTO cycle_delivery VALUES(?,?,?,?,?,?,?)',
                         (f'd{index}', 'p', creator, f'oec{index}', f'172948000000000000{index}',
                          json.dumps(snapshot), 'confirmed'))
            conn.execute('INSERT INTO cycle_delivery_part VALUES(?,?,?,?)',
                         (f'd{index}', 'card', 'confirmed', started))
            conn.execute('INSERT INTO cycle_delivery_part VALUES(?,?,?,?)',
                         (f'd{index}', 'text', 'confirmed', started))
        # 发出去了但结果未确认：不能算触达。
        conn.execute('INSERT INTO cycle_delivery VALUES(?,?,?,?,?,?,?)',
                     ('d9', 'p', 'c9', 'oec9', '1729480000000000009',
                      json.dumps({'handle': 'old_c9', 'message': {'textIt': 'pending'}}), 'unknown'))
        conn.execute('INSERT INTO cycle_delivery_part VALUES(?,?,?,?)', ('d9', 'card', 'result_unknown', at(15, 10)))
        for message_id, kind, occurred, historical in [
                ('1', 'creatorReplies', at(15, 9), 0),
                ('2', 'creatorReplies', at(15, 20), 1),        # 历史补录：不算今天
                ('3', 'showcaseNotifications', at(15, 11), 0),
                ('4', 'ourMessages', at(15, 8), 0),
                ('5', 'creatorReplies', at(14, 23, 59, 59), 0)]:
            conn.execute('INSERT INTO inbox_event VALUES(?,?,?,?,?,?,?,?,?)',
                         ('p', 'cid', message_id, 'oec', kind, int(occurred * 1000),
                          json.dumps({'messageId': message_id}), historical, NOON))
        conn.execute('INSERT INTO relationship VALUES(?,?,?)', ('p', 'reply_creator', 'oec'))
        conn.execute('INSERT INTO inbox_content_head VALUES(?,?,?,?)', ('p', 'cid', '1', 'h1'))
        conn.execute('INSERT INTO inbox_content_version VALUES(?,?,?,?,?,?)',
                     ('p', 'cid', '1', 'h1', json.dumps({'format': 'text', 'text': 'Grazie!'}), NOON))
        conn.execute('INSERT INTO service_reply VALUES(?,?,?,?,?,?,?)',
                     ('r1', 'p', 'reply_creator', 'oec', 'Risposta storica', 'confirmed', at(15, 12)))
        conn.execute('INSERT INTO service_reply VALUES(?,?,?,?,?,?,?)',
                     ('r2', 'p', 'reply_creator', 'oec', 'Non inviata', 'ready', at(15, 12)))
        conn.execute('INSERT INTO service_case VALUES(?,?,?,?,?,?)',
                     ('case1', 'p', 'reply_creator', 'catalog_request', 'open', at(15, 13)))
        conn.execute('INSERT INTO service_case VALUES(?,?,?,?,?,?)',
                     ('case2', 'p', 'reply_creator', 'sample_request', 'resolved', at(15, 13)))
        conn.commit()
    with closing(sqlite3.connect(var / 'creator-identities.sqlite')) as conn, conn:
        conn.execute('CREATE TABLE creator_identity(creator_id TEXT,market TEXT,oec_id TEXT,current_handle TEXT)')
        conn.execute('INSERT INTO creator_identity VALUES(?,?,?,?)', ('c2', 'it', 'oec1', 'current_c2'))
        conn.execute('INSERT INTO creator_identity VALUES(?,?,?,?)', ('reply_creator', 'it', 'oec', 'current_reply'))
        conn.commit()


class Daily(unittest.TestCase):
    def test_a_beijing_day_is_left_closed_and_right_open(self):
        self.assertEqual(beijing_day(NOON), '2026-09-15')
        start, end = day_bounds('2026-09-15')
        self.assertEqual(start, at(15, 0))
        self.assertEqual(end, at(16, 0))
        # 23:59:59 属于当天；次日 00:00:00 不属于。
        self.assertTrue(start <= at(15, 23, 59, 59) < end)
        self.assertFalse(start <= at(16, 0, 0, 0) < end)

    def test_missing_database_is_unavailable_rather_than_zero(self):
        with tempfile.TemporaryDirectory() as folder:
            state = daily(folder)
            self.assertFalse(state['available'])
            self.assertEqual(state['days'], [])

    def test_only_confirmed_deliveries_count_as_reached(self):
        with tempfile.TemporaryDirectory() as folder:
            fixture(folder)
            days = {row['date']: row for row in daily(folder, count=3, now=NOON)['days']}
            # 09-14 23:59:59 那条落在 14 日，09-15 的两条落在 15 日。
            self.assertEqual(days['2026-09-14']['cards'], 1)
            self.assertEqual(days['2026-09-15']['cards'], 2)
            self.assertEqual(days['2026-09-15']['creators'], 2)
            # 结果未确认的那条只进 unconfirmed，不算触达。
            self.assertEqual(days['2026-09-15']['unconfirmed'], 1)
            self.assertEqual(days['2026-09-15']['creators'], 2)

    def test_historical_imports_are_never_counted_as_today(self):
        with tempfile.TemporaryDirectory() as folder:
            fixture(folder)
            days = {row['date']: row for row in daily(folder, count=3, now=NOON)['days']}
            # 今天有 2 条真实回复（一条 live、一条历史补录被排除），1 条橱窗。
            self.assertEqual(days['2026-09-15']['replies'], 1)
            self.assertEqual(days['2026-09-15']['showcase'], 1)
            self.assertEqual(days['2026-09-15']['ourMessages'], 1)
            self.assertEqual(days['2026-09-14']['replies'], 1)

    def test_auto_replies_and_open_cases_are_separate_from_sending(self):
        with tempfile.TemporaryDirectory() as folder:
            fixture(folder)
            state = daily(folder, count=3, now=NOON)
            days = {row['date']: row for row in state['days']}
            # 只有 confirmed 的自动回复算发出；ready 那条还没发。
            self.assertEqual(days['2026-09-15']['autoReplies'], 1)
            self.assertEqual(days['2026-09-15']['casesOpened'], 2)
            # 未结事项是"现在还有几件"，不按天切。
            self.assertEqual(state['openCases'], 1)

    def test_totals_add_up_across_the_window(self):
        with tempfile.TemporaryDirectory() as folder:
            fixture(folder)
            state = daily(folder, count=3, now=NOON)
            self.assertEqual(state['totals']['cards'],
                             sum(row['cards'] for row in state['days']))
            self.assertEqual(state['totals']['cards'], 3)
            self.assertEqual(state['timezone'], 'Asia/Shanghai')

    def test_the_day_list_ends_with_today_in_beijing(self):
        with tempfile.TemporaryDirectory() as folder:
            fixture(folder)
            days = daily(folder, count=4, now=NOON)['days']
            self.assertEqual([row['date'] for row in days],
                             ['2026-09-12', '2026-09-13', '2026-09-14', '2026-09-15'])
            # 再往后一天（北京时间）就换到今天。
            self.assertEqual(beijing_day(NOON + 12 * 3600), '2026-09-16')

    def test_day_detail_is_bounded_read_only_and_reconciles_with_the_summary(self):
        with tempfile.TemporaryDirectory() as folder:
            fixture(folder)
            detail = day_detail(folder, '2026-09-15', limit=100)
            self.assertTrue(detail['available'])
            self.assertFalse(detail['platformWrites'])
            self.assertEqual(detail['total'], 8)
            self.assertEqual(len(detail['items']), 8)
            self.assertIsNone(detail['nextOffset'])
            kinds = {kind: sum(item['kind'] == kind for item in detail['items'])
                     for kind in ('delivery', 'reply', 'showcase', 'auto_reply', 'case')}
            self.assertEqual(kinds, {'delivery': 3, 'reply': 1, 'showcase': 1,
                                     'auto_reply': 1, 'case': 2})
            reply = next(item for item in detail['items'] if item['kind'] == 'reply')
            self.assertEqual((reply['handle'], reply['text']), ('current_reply', 'Grazie!'))
            delivery = next(item for item in detail['items'] if item['ref'] == 'd1')
            self.assertEqual((delivery['handle'], delivery['handleAtEvent']), ('current_c2', 'old_c2'))
            self.assertEqual(delivery['product'], 'prodotto 1')
            self.assertNotIn('private', delivery)

    def test_day_detail_pages_without_changing_the_daily_total(self):
        with tempfile.TemporaryDirectory() as folder:
            fixture(folder)
            first = day_detail(folder, '2026-09-15', limit=3)
            second = day_detail(folder, '2026-09-15', offset=3, limit=3)
            self.assertEqual((first['total'], second['total']), (8, 8))
            self.assertEqual(first['nextOffset'], 3)
            self.assertEqual(second['nextOffset'], 6)
            self.assertFalse({item['ref'] for item in first['items']} &
                             {item['ref'] for item in second['items']})

    def test_market_plan_scope_keeps_same_day_rows_separate(self):
        with tempfile.TemporaryDirectory() as folder:
            fixture(folder);path=Path(folder)/'var/second-cycle.sqlite'
            with closing(sqlite3.connect(path)) as conn,conn:
                conn.execute("INSERT INTO plan VALUES('p-br','bjn-local-research','br')")
                conn.execute("INSERT INTO cycle_delivery VALUES('br-d','p-br','br-c','br-o','1729480000000000099',?,'confirmed')",(json.dumps({'handle':'br_creator'}),))
                conn.execute("INSERT INTO cycle_delivery_part VALUES('br-d','card','confirmed',?)",(at(15,14),))
                conn.execute("INSERT INTO relationship VALUES('p-br','br-c','br-o')")
                conn.execute("INSERT INTO inbox_event VALUES('p-br','br-cid','br-m','br-o','creatorReplies',?,?,0,?)",(int(at(15,15)*1000),json.dumps({'messageId':'br-m'}),NOON))
                conn.execute("INSERT INTO service_case VALUES('br-case','p-br','br-c','human','open',?)",(at(15,16),))
            it=daily(folder,market='it',count=1,now=NOON);br=daily(folder,market='br',count=1,now=NOON)
            self.assertEqual((it['days'][0]['cards'],br['days'][0]['cards']),(2,1))
            self.assertEqual((it['days'][0]['replies'],br['days'][0]['replies']),(1,1))
            self.assertEqual((it['openCases'],br['openCases']),(1,1))
            br_detail=day_detail(folder,'2026-09-15',market='br',limit=100)
            self.assertEqual({item['ref'] for item in br_detail['items']},{'br-d','br-m','br-case'})

    def test_day_detail_rejects_unbounded_or_non_calendar_queries(self):
        with tempfile.TemporaryDirectory() as folder:
            fixture(folder)
            for day in ('2026-02-30', '2026/09/15', None):
                with self.assertRaisesRegex(ValueError, 'invalid_inbox_day'):
                    day_detail(folder, day)
            with self.assertRaisesRegex(ValueError, 'invalid_inbox_limit'):
                day_detail(folder, '2026-09-15', limit=101)
            with self.assertRaisesRegex(ValueError, 'invalid_inbox_offset'):
                day_detail(folder, '2026-09-15', offset=5001)


if __name__ == '__main__':
    unittest.main()
