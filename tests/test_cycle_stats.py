"""按天统计：北京日边界、只算已确认、排除历史补录。"""
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from lib.cycle_stats import BEIJING, beijing_day, daily, day_bounds  # noqa: E402

# 2026-09-15 12:00 北京时间。固定时刻，让边界断言不随"现在"漂移。
NOON = datetime(2026, 9, 15, 12, 0, tzinfo=BEIJING).timestamp()


def at(day, hour, minute=0, second=0):
    return datetime(2026, 9, day, hour, minute, second, tzinfo=BEIJING).timestamp()


def fixture(folder):
    var = Path(folder) / 'var'
    var.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(var / 'second-cycle.sqlite') as conn:
        conn.executescript('''
            CREATE TABLE cycle_delivery(id TEXT,plan_id TEXT,creator_id TEXT,state TEXT);
            CREATE TABLE cycle_delivery_part(delivery_id TEXT,kind TEXT,state TEXT,started REAL);
            CREATE TABLE inbox_event(plan_id TEXT,cid TEXT,message_id TEXT,oec TEXT,kind TEXT,
                                     occurred_ms INTEGER,historical INTEGER,observed_at REAL);
            CREATE TABLE service_reply(id TEXT,state TEXT,started REAL);
            CREATE TABLE service_case(id TEXT,state TEXT,created REAL);''')
        # 09-14 23:59:59 与 09-15 00:00:00 各一条：左闭右开，谁也不能跨日。
        for index, (creator, started) in enumerate([('c1', at(14, 23, 59, 59)), ('c2', at(15, 0, 0, 0)),
                                                    ('c3', at(15, 23, 59, 59))]):
            conn.execute('INSERT INTO cycle_delivery VALUES(?,?,?,?)', (f'd{index}', 'p', creator, 'confirmed'))
            conn.execute('INSERT INTO cycle_delivery_part VALUES(?,?,?,?)',
                         (f'd{index}', 'card', 'confirmed', started))
            conn.execute('INSERT INTO cycle_delivery_part VALUES(?,?,?,?)',
                         (f'd{index}', 'text', 'confirmed', started))
        # 发出去了但结果未确认：不能算触达。
        conn.execute('INSERT INTO cycle_delivery VALUES(?,?,?,?)', ('d9', 'p', 'c9', 'unknown'))
        conn.execute('INSERT INTO cycle_delivery_part VALUES(?,?,?,?)', ('d9', 'card', 'result_unknown', at(15, 10)))
        for message_id, kind, occurred, historical in [
                ('1', 'creatorReplies', at(15, 9), 0),
                ('2', 'creatorReplies', at(15, 20), 1),        # 历史补录：不算今天
                ('3', 'showcaseNotifications', at(15, 11), 0),
                ('4', 'ourMessages', at(15, 8), 0),
                ('5', 'creatorReplies', at(14, 23, 59, 59), 0)]:
            conn.execute('INSERT INTO inbox_event VALUES(?,?,?,?,?,?,?,?)',
                         ('p', 'cid', message_id, 'oec', kind, int(occurred * 1000), historical, NOON))
        conn.execute('INSERT INTO service_reply VALUES(?,?,?)', ('r1', 'confirmed', at(15, 12)))
        conn.execute('INSERT INTO service_reply VALUES(?,?,?)', ('r2', 'ready', at(15, 12)))
        conn.execute('INSERT INTO service_case VALUES(?,?,?)', ('case1', 'open', at(15, 13)))
        conn.execute('INSERT INTO service_case VALUES(?,?,?)', ('case2', 'resolved', at(15, 13)))
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


if __name__ == '__main__':
    unittest.main()
