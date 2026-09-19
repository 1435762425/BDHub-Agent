"""Lead batch execution: what reaches the platform, what is recorded, and what stops the run."""
import fcntl
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'scripts/lib'))
from lib.leads_queue import Ledger, build, status  # noqa: E402
from lib.schema_migrations import apply_database  # noqa: E402
from lib.second_cycle import CycleError  # noqa: E402

import importlib.util  # noqa: E402

spec = importlib.util.spec_from_file_location('leads_run', ROOT / 'scripts/leads-run.py')
leads_run = importlib.util.module_from_spec(spec)
spec.loader.exec_module(leads_run)


def kalodata_body(rows):
    return {'success': True, 'data': {'list': rows}}


def creator(handle, sale=500, revenue=1000):
    return {'id': f'kid-{handle}', 'handle': handle, 'nickname': handle, 'sale': sale,
            'revenue': revenue, 'video_revenue': 100, 'live_revenue': 900, 'followers': 42}


class FakeProvider:
    """Stands in for the real transport so no test ever spends Kalodata quota."""

    def __init__(self, responses):
        self.responses = responses
        self.calls = []
        self.proxy = ''

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def request(self, path, payload):
        self.calls.append(payload['id'])
        answer = self.responses.get(payload['id'], kalodata_body([]))
        if isinstance(answer, Exception):
            raise answer
        return answer


def factory(provider):
    return lambda root: provider


def fixture(folder, pids, *, body=None, campaign='7600438925614876449'):
    var = Path(folder) / 'var'
    var.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(var / 'global-source.sqlite') as conn:
        conn.executescript('CREATE TABLE global_source_screen_run(run_id TEXT,source_run TEXT,updated REAL);'
                           'CREATE TABLE global_source_screen(run_id TEXT,pid TEXT,state TEXT);'
                           'CREATE TABLE global_source_product(run_id TEXT,pid TEXT,payload TEXT);')
        conn.execute("INSERT INTO global_source_screen_run VALUES('s1','r1',1.0)")
        for pid in pids:
            conn.execute("INSERT INTO global_source_screen VALUES('s1',?,'eligible')", (pid,))
            conn.execute('INSERT INTO global_source_product VALUES(?,?,?)',
                         ('r1', pid, json.dumps({'product_id': pid, 'title': f't-{pid}', 'sales': '900 已售'})))
        conn.commit()
    sqlite3.connect(var / 'catalog-links.sqlite').close();apply_database(folder,'catalog-links')
    with sqlite3.connect(var / 'catalog-links.sqlite') as conn:
        for pid in pids:
            conn.execute("INSERT INTO catalog_current_binding VALUES('it','selected',?,?,?,'9','commission-1-to-2-v1','link-naming-v1','13','name','{}',NULL,'active',1,1)",
                         (pid,campaign,'f-'+pid))
    with sqlite3.connect(var / 'second-cycle.sqlite') as conn:
        conn.executescript("CREATE TABLE plan(id TEXT,institution TEXT,market TEXT);"
                           "CREATE TABLE source_edge(plan_id TEXT,source_id TEXT,payload TEXT,PRIMARY KEY(plan_id,source_id));")
        conn.execute("INSERT INTO plan VALUES('plan-it','bjn-local-research','it')")
        conn.commit()
    apply_database(folder,'second-cycle')
    lock = Path(folder) / 'browser.lock'
    lock.write_bytes(b'')
    return lock


class Locking(unittest.TestCase):
    """The browser lock has exactly one owner: the provider. Taking it twice deadlocked the batch."""

    def test_a_busy_reader_defers_the_whole_batch_without_asking_for_anything(self):
        with tempfile.TemporaryDirectory() as folder:
            pids = ['1' * 19, '2' * 19]
            lock = fixture(folder, pids)
            provider = FakeProvider({pid: kalodata_body([creator('x')]) for pid in pids})

            def busy(root):
                # What the real provider does when another Kalodata reader holds the lock.
                raise BlockingIOError(11, 'resource temporarily unavailable')

            report = leads_run.run(folder, limit=10, provider_factory=busy,
                                   clock=lambda: 1000.0, browser_lock=lock)
            self.assertEqual(report['stopped'], 'browser_lock_busy')
            self.assertEqual(report['networkRequests'], 0)
            self.assertEqual(report['done'], 0)
            self.assertEqual(provider.calls, [])
            # Nothing was recorded, so both products are still waiting at the head of the queue.
            self.assertEqual(len(build(folder, now=1000.0)['firstTime']), 2)

    def test_a_missing_lock_file_stops_before_any_request(self):
        with tempfile.TemporaryDirectory() as folder:
            pids = ['1' * 19]
            fixture(folder, pids)
            provider = FakeProvider({pids[0]: kalodata_body([creator('x')])})
            report = leads_run.run(folder, limit=10, provider_factory=factory(provider),
                                   clock=lambda: 1000.0,
                                   browser_lock=Path(folder) / 'no-such-lock')
            self.assertEqual(report['stopped'], 'browser_lock_missing')
            self.assertEqual(provider.calls, [])

    def test_the_runner_does_not_take_the_lock_itself(self):
        # A second flock on another fd in the same process is refused, which is what silently
        # turned every real batch into an instant ``browser_lock_busy``. The runner must therefore
        # never flock the path it was handed; only the provider may.
        with tempfile.TemporaryDirectory() as folder:
            pids = ['1' * 19]
            lock = fixture(folder, pids)
            provider = FakeProvider({pids[0]: kalodata_body([creator('x')])})
            seen = {}

            def factory_that_locks(root):
                handle = lock.open('rb')
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                seen['locked'] = True
                provider._handle = handle
                return provider

            report = leads_run.run(folder, limit=10, provider_factory=factory_that_locks,
                                   clock=lambda: 1000.0, browser_lock=lock)
            self.assertTrue(seen.get('locked'))
            self.assertEqual(report['done'], 1)
            provider._handle.close()


class Execution(unittest.TestCase):
    def test_a_successful_run_records_leads_and_starts_the_refresh_clock(self):
        with tempfile.TemporaryDirectory() as folder:
            pids = ['1' * 19, '2' * 19]
            lock = fixture(folder, pids)
            provider = FakeProvider({pid: kalodata_body([creator(f'user{index}')])
                                     for index, pid in enumerate(pids)})
            report = leads_run.run(folder, limit=10, provider_factory=factory(provider),
                                   clock=lambda: 1000.0, browser_lock=lock)
            self.assertEqual(report['done'], 2)
            self.assertEqual(report['leads'], 2)
            self.assertIsNone(report['stopped'])
            self.assertEqual(report['platformWrites'], 0)
            self.assertEqual(sorted(provider.calls), sorted(pids))
            ledger = Ledger(folder)
            try:
                self.assertEqual(set(ledger.known()), set(pids))
                self.assertEqual(ledger.attempts(), {})
            finally:
                ledger.close()
            with sqlite3.connect(Path(folder) / 'var/second-cycle.sqlite') as conn:
                self.assertEqual(conn.execute('SELECT count(*) FROM source_edge').fetchone()[0], 2)
            # The products leave the first-time queue.
            self.assertEqual(build(folder, now=1000.0)['firstTime'], [])

    def test_only_the_top_of_the_queue_is_run(self):
        with tempfile.TemporaryDirectory() as folder:
            pids = ['1' * 19, '2' * 19, '3' * 19]
            lock = fixture(folder, pids)
            provider = FakeProvider({pid: kalodata_body([creator('u' + pid[-1])]) for pid in pids})
            report = leads_run.run(folder, limit=1, provider_factory=factory(provider),
                                   clock=lambda: 1000.0, browser_lock=lock)
            self.assertEqual(report['done'], 1)
            self.assertEqual(len(provider.calls), 1)

    def test_quota_exhaustion_stops_the_batch_at_once(self):
        with tempfile.TemporaryDirectory() as folder:
            pids = ['1' * 19, '2' * 19, '3' * 19]
            lock = fixture(folder, pids)
            quota = {'success': False, 'message': json.dumps({'cause': 'DETAIL.ACCESS_TIMES'})}
            provider = FakeProvider({pid: (quota if pid == pids[1] else kalodata_body([creator('x')]))
                                     for pid in pids})
            report = leads_run.run(folder, limit=10, provider_factory=factory(provider),
                                   clock=lambda: 1000.0, browser_lock=lock)
            self.assertEqual(report['stopped'], 'kalodata_daily_quota_exhausted')
            self.assertEqual(report['done'], 1)
            # The third product was never asked for once the budget ran out.
            self.assertEqual(provider.calls, [pids[0], pids[1]])

    def test_a_failed_query_gets_no_clock_and_stays_in_the_queue(self):
        with tempfile.TemporaryDirectory() as folder:
            pids = ['1' * 19]
            lock = fixture(folder, pids)
            provider = FakeProvider({pids[0]: CycleError('kalodata_business_rejected')})
            report = leads_run.run(folder, limit=10, provider_factory=factory(provider),
                                   clock=lambda: 1000.0, browser_lock=lock)
            self.assertEqual(report['errors'], [{'pid': pids[0], 'code': 'kalodata_business_rejected'}])
            ledger = Ledger(folder)
            try:
                self.assertEqual(ledger.known(), {})          # no refresh clock was started
                self.assertEqual(ledger.attempts()[pids[0]]['attempts'], 1)
            finally:
                ledger.close()
            self.assertEqual([row['pid'] for row in build(folder, now=1000.0)['firstTime']], pids)

    def test_a_product_that_keeps_failing_leaves_the_queue_instead_of_burning_the_budget(self):
        with tempfile.TemporaryDirectory() as folder:
            pids = ['1' * 19]
            lock = fixture(folder, pids)
            provider = FakeProvider({pids[0]: CycleError('kalodata_business_rejected')})
            for _ in range(3):
                leads_run.run(folder, limit=10, provider_factory=factory(provider),
                              clock=lambda: 1000.0, browser_lock=lock)
            built = build(folder, now=1000.0)
            self.assertEqual(built['firstTime'], [])
            self.assertEqual([row['pid'] for row in built['stuck']], pids)
            self.assertEqual(status(folder, now=1000.0)['stuck'], 1)

    def test_a_locked_reader_defers_instead_of_double_reading(self):
        with tempfile.TemporaryDirectory() as folder:
            pids = ['1' * 19]
            lock = fixture(folder, pids)
            provider = FakeProvider({pids[0]: kalodata_body([creator('x')])})
            other = leads_run.run(folder, limit=10, provider_factory=factory(provider),
                                  clock=lambda: 1000.0, browser_lock=lock)
            self.assertEqual(other['done'], 1)

    def test_an_empty_queue_runs_nothing_and_says_so(self):
        with tempfile.TemporaryDirectory() as folder:
            lock = fixture(folder, [])
            provider = FakeProvider({})
            report = leads_run.run(folder, limit=10, provider_factory=factory(provider),
                                   clock=lambda: 1000.0, browser_lock=lock)
            self.assertEqual(report['stopped'], 'queue_empty')
            self.assertEqual(provider.calls, [])

    def test_a_page_receipt_is_reused_instead_of_requesting_the_same_page_twice(self):
        with tempfile.TemporaryDirectory() as folder:
            pids = ['1' * 19]
            lock = fixture(folder, pids)
            provider = FakeProvider({pids[0]: kalodata_body([creator('x')])})
            leads_run.run(folder, limit=10, provider_factory=factory(provider),
                          clock=lambda: 1000.0, browser_lock=lock)
            ledger = Ledger(folder)
            try:
                saved = ledger.db.execute('SELECT count(*) FROM leads_page').fetchone()[0]
            finally:
                ledger.close()
            self.assertEqual(saved, 1)


if __name__ == '__main__':
    unittest.main()
