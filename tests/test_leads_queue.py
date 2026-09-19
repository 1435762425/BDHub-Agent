"""Lead query queue: scope, ordering, refresh age, and the clock that was missing."""
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from lib.leads_queue import (DEFAULTS, Ledger, build, config_path, eligible_products,  # noqa: E402
                             linked_products, load, plan, queried_from_jobs, queue_items,
                             save_config, status, sync, validate)
from lib.schema_migrations import apply_database  # noqa: E402

DAY = 86400
NOW = 1_789_400_000.0


def fixture(folder, *, eligible=None, linked=None, products=None):
    """Minimal collection + catalog databases with only the columns the queue reads."""
    products = products or {}
    var = Path(folder) / 'var'
    var.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(var / 'global-source.sqlite') as conn:
        conn.executescript('CREATE TABLE global_source_screen_run(run_id TEXT,source_run TEXT,updated REAL);'
                           'CREATE TABLE global_source_screen(run_id TEXT,pid TEXT,state TEXT);'
                           'CREATE TABLE global_source_product(run_id TEXT,pid TEXT,payload TEXT);')
        conn.execute("INSERT INTO global_source_screen_run VALUES('s1','r1',1.0)")
        for pid in eligible or []:
            conn.execute("INSERT INTO global_source_screen VALUES('s1',?,'eligible')", (pid,))
            payload = {'product_id': pid, 'title': f'title-{pid}',
                       'sales': f'{products.get(pid, 0)} 已售'}
            conn.execute('INSERT INTO global_source_product VALUES(?,?,?)', ('r1', pid, json.dumps(payload)))
        conn.commit()
    sqlite3.connect(var / 'catalog-links.sqlite').close()
    apply_database(folder,'catalog-links')
    with sqlite3.connect(var / 'catalog-links.sqlite') as conn:
        for pid,state in (linked or {}).items():
            conn.execute("INSERT INTO catalog_current_binding VALUES('it','selected',?,'1','f',?,'commission-1-to-2-v1','link-naming-v1','13','name','{}',NULL,?,1,1)",
                         (pid,'list-'+pid,state))


def campaign_fixture(folder, *, chosen=None, sales=None, with_sales=True):
    """非全托的池子账本 ＋ 一份带了 sales 的快照（队列排序要用）。"""
    var = Path(folder) / 'var'
    with sqlite3.connect(var / 'campaign-screen.sqlite') as conn:
        conn.executescript('CREATE TABLE campaign_pool_run(run_id TEXT,source TEXT,source_snapshot TEXT,'
                           'created REAL,counts TEXT);'
                           'CREATE TABLE campaign_pool_item(run_id TEXT,pid TEXT,state TEXT,campaign_id TEXT);')
        conn.execute("INSERT INTO campaign_pool_run VALUES('p1','campaign','snap-1',1.0,'{}')")
        for index, pid in enumerate(chosen or []):
            conn.execute("INSERT INTO campaign_pool_item VALUES('p1',?,'chosen','900')", (pid,))
        conn.commit()
    with sqlite3.connect(var / 'second-cycle.sqlite') as conn:
        conn.executescript('CREATE TABLE plan(id TEXT,institution TEXT,market TEXT,state TEXT);'
                           'CREATE TABLE catalog(id TEXT PRIMARY KEY,plan_id TEXT,source TEXT,observed REAL,state TEXT,payload TEXT);'
                           'CREATE TABLE catalog_head(plan_id TEXT,source TEXT,snapshot_id TEXT,PRIMARY KEY(plan_id,source));')
        conn.execute("INSERT INTO plan VALUES('pl','bjn-local-research','it','active')")
        offers = [{'pid': pid, 'campaignId': '900', 'title': f'camp-{pid}',
                   **({'sales': str((sales or {}).get(pid, 0))} if with_sales else {})}
                  for pid in (chosen or [])]
        conn.execute('INSERT INTO catalog VALUES(?,?,?,?,?,?)',
                     ('snap-1', 'pl', 'live-it-campaign', 1.0, 'complete',
                      json.dumps(offers, ensure_ascii=False)))
        conn.execute('INSERT INTO catalog_head VALUES(?,?,?)', ('pl', 'live-it-campaign', 'snap-1'))
        conn.commit()


class ConfigFile(unittest.TestCase):
    def test_defaults_are_the_confirmed_policy(self):
        self.assertEqual(DEFAULTS['refreshDays'], 7)
        self.assertEqual(validate({})['leadsPerPid'], 20)

    def test_out_of_range_values_are_refused(self):
        for bad in [{'refreshDays': 0}, {'refreshDays': 91}, {'leadsPerPid': 51},
                    {'windowDays': 0}, {'refreshDays': '7'}, 'nope']:
            with self.assertRaises(ValueError):
                validate(bad)

    def test_a_rejected_value_never_touches_the_file(self):
        with tempfile.TemporaryDirectory() as folder:
            save_config(folder, {'refreshDays': 14})
            before = config_path(folder).read_text(encoding='utf-8')
            with self.assertRaises(ValueError):
                save_config(folder, {'refreshDays': 0})
            self.assertEqual(config_path(folder).read_text(encoding='utf-8'), before)
            self.assertEqual(load(folder)['refreshDays'], 14)


class Scope(unittest.TestCase):
    def test_only_eligible_products_with_a_valid_link_enter_the_queue(self):
        with tempfile.TemporaryDirectory() as folder:
            fixture(folder, eligible=['a', 'b', 'c'],
                    linked={'a': 'active', 'b': 'waiting_refresh', 'c': 'inactive'})
            built = build(folder, now=NOW)
            self.assertEqual(built['eligible'], 3)
            self.assertEqual(built['linked'], 1)
            self.assertEqual([row['pid'] for row in built['firstTime']], ['a'])
            self.assertEqual(built['scope'], 1)

    def test_only_active_standard_bindings_are_included(self):
        with tempfile.TemporaryDirectory() as folder:
            fixture(folder, eligible=['a', 'b'], linked={'a': 'active', 'b': 'waiting_refresh'})
            built = build(folder, now=NOW)
            self.assertEqual([row['pid'] for row in built['firstTime']], ['a'])

    def test_first_time_work_is_ordered_by_cumulative_sales(self):
        with tempfile.TemporaryDirectory() as folder:
            fixture(folder, eligible=['low', 'high', 'mid'],
                    linked={'low': 'active', 'high': 'active', 'mid': 'active'},
                    products={'low': 120, 'high': 9000, 'mid': 800})
            built = build(folder, now=NOW)
            self.assertEqual([row['pid'] for row in built['firstTime']], ['high', 'mid', 'low'])
            self.assertEqual([row['units'] for row in built['firstTime']], [9000, 800, 120])


class RefreshAge(unittest.TestCase):
    def test_a_fresh_query_waits_and_an_old_one_is_due(self):
        with tempfile.TemporaryDirectory() as folder:
            fixture(folder, eligible=['fresh', 'old'], linked={'fresh': 'active', 'old': 'active'})
            ledger = Ledger(folder)
            try:
                ledger.record('fresh', at=NOW - 2 * DAY)
                ledger.record('old', at=NOW - 8 * DAY)
            finally:
                ledger.close()
            built = build(folder, now=NOW)
            self.assertEqual([row['pid'] for row in built['due']], ['old'])
            self.assertEqual(built['waiting'], 1)
            self.assertEqual(built['firstTime'], [])

    def test_the_refresh_age_comes_from_the_config(self):
        with tempfile.TemporaryDirectory() as folder:
            fixture(folder, eligible=['p'], linked={'p': 'active'})
            ledger = Ledger(folder)
            try:
                ledger.record('p', at=NOW - 8 * DAY)
            finally:
                ledger.close()
            self.assertEqual(len(build(folder, now=NOW)['due']), 1)
            self.assertEqual(build(folder, now=NOW, config=validate({'refreshDays': 30}))['due'], [])
            self.assertEqual(build(folder, now=NOW, config=validate({'refreshDays': 30}))['waiting'], 1)

    def test_due_work_is_oldest_first(self):
        with tempfile.TemporaryDirectory() as folder:
            fixture(folder, eligible=['a', 'b'], linked={'a': 'active', 'b': 'active'})
            ledger = Ledger(folder)
            try:
                ledger.record('a', at=NOW - 9 * DAY)
                ledger.record('b', at=NOW - 20 * DAY)
            finally:
                ledger.close()
            self.assertEqual([row['pid'] for row in build(folder, now=NOW)['due']], ['b', 'a'])


class LedgerClock(unittest.TestCase):
    def test_backfill_gives_a_clock_without_overwriting_a_real_one(self):
        with tempfile.TemporaryDirectory() as folder:
            ledger = Ledger(folder)
            try:
                ledger.record('known', at=NOW - 30 * DAY)
                ledger.backfill(['known', 'legacy', 'legacy'], at=NOW)
                rows = ledger.known()
            finally:
                ledger.close()
            self.assertEqual(rows['known']['queried_at'], NOW - 30 * DAY)
            self.assertEqual(rows['legacy']['queried_at'], NOW)
            self.assertEqual(rows['legacy']['state'], 'backfilled')

    def test_recording_the_same_product_twice_keeps_one_row_and_moves_the_clock(self):
        with tempfile.TemporaryDirectory() as folder:
            ledger = Ledger(folder)
            try:
                ledger.record('p', at=NOW - 5 * DAY, leads=3)
                ledger.record('p', at=NOW, leads=7)
                rows = ledger.known()
            finally:
                ledger.close()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows['p']['queried_at'], NOW)
            self.assertEqual(rows['p']['leads'], 7)

    def test_a_queried_product_outside_the_current_scope_is_reported_not_dropped(self):
        with tempfile.TemporaryDirectory() as folder:
            fixture(folder, eligible=['a'], linked={'a': 'active'})
            ledger = Ledger(folder)
            try:
                ledger.record('gone', at=NOW - DAY)
            finally:
                ledger.close()
            self.assertEqual(build(folder, now=NOW)['unknownScope'], 1)


class JobStores(unittest.TestCase):
    """Only a request that actually reached the platform may start a product's refresh clock."""

    def _jobs(self, folder, rows, table='batch_source_job'):
        var = Path(folder) / 'var'
        var.mkdir(parents=True, exist_ok=True)
        db = var / ('second-cycle.sqlite' if table == 'source_job' else 'batch-tasks.sqlite')
        with sqlite3.connect(db) as conn:
            conn.execute(f'CREATE TABLE {table}(pid TEXT,state TEXT)')
            conn.executemany(f'INSERT INTO {table} VALUES(?,?)', rows)
            conn.commit()

    def test_a_queued_or_identity_blocked_job_is_not_a_query(self):
        with tempfile.TemporaryDirectory() as folder:
            self._jobs(folder, [('done', 'completed'), ('waiting', 'queued'),
                                ('blind', 'awaiting_identity'), ('noreply', 'blocked')])
            self.assertEqual(queried_from_jobs(folder), {'done'})

    def test_both_task_stores_are_read(self):
        with tempfile.TemporaryDirectory() as folder:
            self._jobs(folder, [('a', 'completed')])
            self._jobs(folder, [('b', 'completed'), ('c', 'obsolete')], table='source_job')
            self.assertEqual(queried_from_jobs(folder), {'a', 'b'})

    def test_a_missing_store_is_not_an_error(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(queried_from_jobs(folder), set())

    def test_sync_records_completed_jobs_and_retracts_what_was_never_queried(self):
        with tempfile.TemporaryDirectory() as folder:
            self._jobs(folder, [('done', 'completed'), ('waiting', 'queued')])
            ledger = Ledger(folder)
            try:
                # Reproduce the earlier mistake: a clock written for a job that never ran.
                ledger.backfill(['done', 'waiting'], at=NOW - 100 * DAY, note='wrong')
            finally:
                ledger.close()
            result = sync(folder, at=NOW)
            ledger = Ledger(folder)
            try:
                rows = ledger.known()
            finally:
                ledger.close()
            self.assertEqual(result['queried'], 1)
            self.assertEqual(result['removed'], 1)
            # The product that never ran is out of the ledger entirely...
            self.assertNotIn('waiting', rows)
            # ...and our own estimate is re-stated rather than kept at an old guess.
            self.assertEqual(rows['done']['queried_at'], NOW)
            self.assertEqual(rows['done']['state'], 'backfilled')

    def test_sync_never_touches_a_real_recorded_time(self):
        with tempfile.TemporaryDirectory() as folder:
            self._jobs(folder, [('p', 'completed')])
            ledger = Ledger(folder)
            try:
                ledger.record('p', at=NOW - 3 * DAY, state='completed', leads=4)
            finally:
                ledger.close()
            sync(folder, at=NOW)
            ledger = Ledger(folder)
            try:
                row = ledger.known()['p']
            finally:
                ledger.close()
            self.assertEqual(row['queried_at'], NOW - 3 * DAY)
            self.assertEqual(row['state'], 'completed')


class Batch(unittest.TestCase):
    """The batch size is a ceiling. It is never reached by adding work that is not due."""

    def _queue(self, folder):
        fixture(folder, eligible=['a', 'b', 'c', 'd'],
                linked={'a': 'active', 'b': 'active', 'c': 'active', 'd': 'active'},
                products={'a': 400, 'b': 300, 'c': 200, 'd': 100})
        ledger = Ledger(folder)
        try:
            # c is fresh and d expired, so only d belongs in the queue.
            ledger.record('c', at=NOW - 2 * DAY)
            ledger.record('d', at=NOW - 30 * DAY)
        finally:
            ledger.close()

    def test_the_batch_takes_the_top_of_the_queue(self):
        with tempfile.TemporaryDirectory() as folder:
            self._queue(folder)
            planned = plan(folder, now=NOW, batch_size=2)
            self.assertEqual(planned['dueQueue'], 3)  # a, b first-time + d expired
            self.assertEqual([row['pid'] for row in planned['items']], ['a', 'b'])
            self.assertEqual(planned['first'], 2)
            self.assertEqual(planned['refresh'], 0)

    def test_a_short_queue_is_not_padded_from_products_that_are_not_due(self):
        with tempfile.TemporaryDirectory() as folder:
            self._queue(folder)
            planned = plan(folder, now=NOW, batch_size=1000)
            self.assertEqual(planned['dueQueue'], 3)
            self.assertEqual(planned['taken'], 3)
            self.assertEqual(planned['shortfall'], 997)
            self.assertFalse(planned['padded'])
            # The fresh product is excluded and no refresh is invented to reach the ceiling.
            self.assertEqual(planned['notYetDue'], 1)
            self.assertNotIn('c', [row['pid'] for row in planned['items']])

    def test_first_time_work_is_served_before_expired_refreshes(self):
        with tempfile.TemporaryDirectory() as folder:
            self._queue(folder)
            planned = plan(folder, now=NOW, batch_size=3)
            self.assertEqual([row['kind'] for row in planned['items']], ['first', 'first', 'due'])
            self.assertEqual(planned['items'][-1]['pid'], 'd')

    def test_a_batch_of_zero_or_a_silly_size_is_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            self._queue(folder)
            for bad in (0, -1, 5001, 'ten'):
                with self.assertRaises(ValueError):
                    plan(folder, now=NOW, batch_size=bad)

    def test_status_reports_the_same_batch_it_would_run(self):
        with tempfile.TemporaryDirectory() as folder:
            self._queue(folder)
            state = status(folder, now=NOW, batch_size=2)
            self.assertEqual(state['dueQueue'], 3)
            self.assertEqual(state['taken'], 2)
            self.assertEqual(state['batchFirst'], 2)
            self.assertEqual(state['shortfall'], 0)
            self.assertFalse(state['padded'])


class Reporting(unittest.TestCase):
    def test_status_counts_add_up_to_the_scope(self):
        with tempfile.TemporaryDirectory() as folder:
            fixture(folder, eligible=['a', 'b', 'c'], linked={'a': 'active', 'b': 'active', 'c': 'active'},
                    products={'a': 10, 'b': 20, 'c': 30})
            ledger = Ledger(folder)
            try:
                ledger.record('b', at=NOW - DAY)
                ledger.record('c', at=NOW - 10 * DAY)
            finally:
                ledger.close()
            state = status(folder, now=NOW)
            self.assertEqual(state['scope'], 3)
            self.assertEqual(state['firstTime'] + state['due'] + state['waiting'], state['scope'])
            self.assertEqual(state['firstTime'], 1)
            self.assertEqual(state['due'], 1)
            self.assertEqual(state['waiting'], 1)

    def test_a_missing_catalogue_reports_empty_rather_than_raising(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(eligible_products(folder), {})
            self.assertEqual(linked_products(folder), {})
            state = status(folder, now=NOW)
            self.assertEqual(state['scope'], 0)
            self.assertEqual(state['firstTime'], 0)


if __name__ == '__main__':
    unittest.main()


class BothChannels(unittest.TestCase):
    """队列是**跨渠道**的：渠道只挂在商品与链接上，找达人是同一件事。

    回归：作用域原来只读全托的筛分账本，于是非全托的商品即使已经备好链接也进不了队列
    （实测 444 个被挡在外面，页面上的队列数一动不动）。
    """

    def test_a_campaign_product_with_a_link_enters_the_queue(self):
        with tempfile.TemporaryDirectory() as folder:
            fixture(folder, eligible=[], linked={})
            campaign_fixture(folder, chosen=['1729474628908391280'], sales={'1729474628908391280': 777})
            # 给它一条可用链接：这是进队列的前提（先备链再找达人）
            with sqlite3.connect(Path(folder) / 'var/catalog-links.sqlite') as conn:
                conn.execute("INSERT INTO catalog_current_binding VALUES('it','campaign','1729474628908391280','900','f','9','commission-1-to-2-v1','link-naming-v1','13','name','{}',NULL,'active',1,1)")
            products = eligible_products(folder)
            self.assertIn('1729474628908391280', products)
            self.assertEqual(products['1729474628908391280']['channel'], 'campaign')
            self.assertEqual(products['1729474628908391280']['units'], 777)
            built = build(folder)
            self.assertEqual(built['scope'], 1)
            self.assertEqual(built['byChannel'], {'selected': 0, 'campaign': 1})
            self.assertEqual(built['unitsUnknown'], 0)

    def test_a_campaign_product_without_sales_is_ranked_but_flagged(self):
        with tempfile.TemporaryDirectory() as folder:
            fixture(folder, eligible=[], linked={})
            campaign_fixture(folder, chosen=['1729474628908391280'], with_sales=False)
            with sqlite3.connect(Path(folder) / 'var/catalog-links.sqlite') as conn:
                conn.execute("INSERT INTO catalog_current_binding VALUES('it','campaign','1729474628908391280','900','f','9','commission-1-to-2-v1','link-naming-v1','13','name','{}',NULL,'active',1,1)")
            built = build(folder)
            # 没有销量就按 0 排序，但必须**如实标记**，不能假装有数据。
            self.assertEqual(built['unitsUnknown'], 1)
            self.assertEqual(built['byChannel']['campaign'], 1)

    def test_a_product_in_both_channels_keeps_the_full_managed_row(self):
        """同一个商品两条渠道都有时，保留全托那条：它带真实销量数据。"""
        with tempfile.TemporaryDirectory() as folder:
            fixture(folder, eligible=['1729474628908391280'], linked={'1729474628908391280': 'active'},
                    products={'1729474628908391280': 999})
            campaign_fixture(folder, chosen=['1729474628908391280'], sales={'1729474628908391280': 1})
            products = eligible_products(folder)
            self.assertEqual(products['1729474628908391280']['channel'], 'selected')
            self.assertEqual(products['1729474628908391280']['units'], 999)


if __name__ == '__main__':
    unittest.main()
