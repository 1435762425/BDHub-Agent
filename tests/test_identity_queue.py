"""The identity (OECID) stage: what counts as in the pool, waiting, or eliminated."""
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from lib.identity_queue import by_creator, counts, status  # noqa: E402

PLAN = 'cycle-test-plan'


def fixture(folder, *, edges, resolved, unresolved, queued, blocked, handles=None):
    """One lead per entry; handles default to one per lead so handle counts are easy to reason about."""
    var = Path(folder) / 'var'
    var.mkdir(parents=True, exist_ok=True)
    handles = handles or {}
    with sqlite3.connect(var / 'second-cycle.sqlite') as conn:
        conn.executescript('''
            CREATE TABLE plan(id TEXT,institution TEXT,market TEXT,state TEXT);
            CREATE TABLE source_edge(plan_id TEXT,source_id TEXT,payload TEXT,PRIMARY KEY(plan_id,source_id));
            CREATE TABLE cycle_identity_resolution(plan_id TEXT,source_id TEXT,creator_id TEXT,oec TEXT,
                evidence_ref TEXT,PRIMARY KEY(plan_id,source_id));
            CREATE TABLE cycle_identity_outcome(plan_id TEXT,source_id TEXT,status TEXT,
                PRIMARY KEY(plan_id,source_id));''')
        conn.execute("INSERT INTO plan VALUES(?,'bjn-local-research','it','active')", (PLAN,))
        total = resolved + unresolved + queued + blocked + edges
        for index in range(total):
            handle = handles.get(index, f'handle{index}')
            conn.execute('INSERT INTO source_edge VALUES(?,?,?)',
                         (PLAN, f's{index}', json.dumps({'sourceKind': 'kalodata_http', 'sourceHandle': handle})))
        cursor = 0
        for _ in range(resolved):
            conn.execute('INSERT INTO cycle_identity_resolution VALUES(?,?,?,?,?)',
                         (PLAN, f's{cursor}', f'creator{cursor}', f'749{cursor:016d}', 'ref'))
            conn.execute("INSERT INTO cycle_identity_outcome VALUES(?,?,'completed')", (PLAN, f's{cursor}'))
            cursor += 1
        for status_name, size in (('unresolved', unresolved), ('queued', queued), ('blocked', blocked)):
            for _ in range(size):
                conn.execute('INSERT INTO cycle_identity_outcome VALUES(?,?,?)', (PLAN, f's{cursor}', status_name))
                cursor += 1
        conn.commit()
    return var


class Counts(unittest.TestCase):
    def test_the_three_groups_add_up_to_every_lead(self):
        with tempfile.TemporaryDirectory() as folder:
            fixture(folder, edges=4, resolved=3, unresolved=2, queued=5, blocked=1)
            measured = counts(folder)
            self.assertEqual(measured['leads'], 15)
            self.assertEqual(measured['resolvedLeads'], 3)
            self.assertEqual(measured['resolvedCreators'], 3)
            self.assertEqual(measured['unresolvedLeads'], 2)
            self.assertEqual(measured['pendingLeads'], 10)
            self.assertEqual(measured['pendingBreakdown'], {'unhanded': 4, 'queued': 5, 'blocked': 1})
            self.assertTrue(measured['reconciled'])

    def test_one_handle_shows_up_as_one_creator_but_several_leads(self):
        with tempfile.TemporaryDirectory() as folder:
            # Four leads, one of them queued, all for the same handle: the Find work is one handle.
            fixture(folder, edges=3, resolved=1, unresolved=0, queued=0, blocked=0,
                    handles={0: 'same', 1: 'same', 2: 'same', 3: 'same'})
            measured = counts(folder)
            self.assertEqual(measured['leads'], 4)
            self.assertEqual(measured['resolvedLeads'], 1)
            self.assertEqual(measured['pendingCreators'], 1)
            self.assertEqual(measured['pendingLeads'], 3)

    def test_a_workspace_without_the_identity_tables_reports_unavailable(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / 'var').mkdir(parents=True, exist_ok=True)
            self.assertIsNone(counts(folder))
            payload = status(folder)
            self.assertFalse(payload['available'])
            self.assertEqual(payload['config'], {'batchSize': 2000, 'cohortSize': 20})
            self.assertIsNone(payload['run'])

    def test_status_carries_config_run_and_the_published_channel(self):
        with tempfile.TemporaryDirectory() as folder:
            fixture(folder, edges=0, resolved=0, unresolved=0, queued=1, blocked=0)
            payload = status(folder)
            self.assertTrue(payload['available'])
            self.assertEqual(payload['pendingCreators'], 1)
            self.assertIsNone(payload['run'])
            # A workspace with no published acceptance reads fine and reports it as unpublished --
            # that is a different fact from "the policy could not be read at all".
            self.assertEqual(sorted(payload['policy']),
                             ['acceptanceId', 'lanes', 'published', 'qps', 'readable'])
            self.assertTrue(payload['policy']['readable'])
            self.assertFalse(payload['policy']['published'])


class TransientLock(unittest.TestCase):
    """The stage shares its database with a resident writer, so a locked read must be retried."""

    def test_a_locked_read_is_retried_rather_than_reported_as_unavailable(self):
        import lib.identity_queue as queue
        with tempfile.TemporaryDirectory() as folder:
            fixture(folder, edges=0, resolved=1, unresolved=0, queued=1, blocked=0)
            real, calls = queue._counts_once, []

            def flaky(root):
                calls.append(root)
                if len(calls) < 3:
                    raise sqlite3.OperationalError('database is locked')
                return real(root)

            queue._counts_once = flaky
            try:
                measured = counts(folder)
            finally:
                queue._counts_once = real
            self.assertEqual(len(calls), 3)
            self.assertTrue(measured['reconciled'])

    def test_a_lock_that_never_clears_is_still_an_error(self):
        import lib.identity_queue as queue
        with tempfile.TemporaryDirectory() as folder:
            fixture(folder, edges=0, resolved=1, unresolved=0, queued=0, blocked=0)
            real = queue._counts_once

            def always_locked(root):
                raise sqlite3.OperationalError('database is locked')

            queue._counts_once = always_locked
            try:
                with self.assertRaises(sqlite3.OperationalError):
                    counts(folder)
            finally:
                queue._counts_once = real


if __name__ == '__main__':
    unittest.main()


class ByCreator(unittest.TestCase):
    """达人身份的分类：单位是去重 handle，而且必须一次互斥。"""

    def fixture(self, folder):
        var = Path(folder) / 'var'
        var.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(var / 'second-cycle.sqlite') as conn:
            conn.executescript('''
                CREATE TABLE plan(id TEXT,institution TEXT,market TEXT,state TEXT);
                CREATE TABLE source_edge(plan_id TEXT,source_id TEXT,payload TEXT);
                CREATE TABLE cycle_identity_resolution(plan_id TEXT,source_id TEXT,creator_id TEXT);
                CREATE TABLE cycle_identity_outcome(plan_id TEXT,source_id TEXT,status TEXT);''')
            conn.execute("INSERT INTO plan VALUES('p','bjn-local-research','it','active')")
            # c1 两条线索都拿到了位置；c2 一条有位置、一条没有；c3 判过找不到、还有一条没判；
            # c4 从没判过；c5 同一个达人的两条线索都判了找不到。
            edges = [('s1', 'c1'), ('s2', 'c1'), ('s3', 'c2'), ('s4', 'c2'),
                     ('s5', 'c3'), ('s6', 'c3'), ('s7', 'c4'), ('s8', 'c5'), ('s9', 'c5')]
            for source_id, handle in edges:
                # 每条线索挂自己的商品：位置＝达人×商品，所以 pid 必须真实存在。
                conn.execute('INSERT INTO source_edge VALUES(?,?,?)', ('p', source_id, json.dumps(
                    {'sourceKind': 'kalodata_http', 'sourceHandle': handle, 'pid': 'prod-' + source_id})))
            for source_id in ('s1', 's2', 's3'):
                conn.execute('INSERT INTO cycle_identity_resolution VALUES(?,?,?)', ('p', source_id, 'creator'))
            for source_id in ('s5', 's8', 's9'):
                conn.execute("INSERT INTO cycle_identity_outcome VALUES(?,?,'unresolved')",
                             ('p', source_id))
            conn.commit()

    def test_the_three_classes_are_mutually_exclusive_and_add_up(self):
        with tempfile.TemporaryDirectory() as folder:
            self.fixture(folder)
            c = by_creator(folder)
            # c1 c2 有位置 → 已就位；c3 c5 判过找不到 → 搜索不到；c4 没判过 → 还没判过。
            self.assertEqual((c['resolved'], c['unresolved'], c['unknown']), (2, 2, 1))
            self.assertEqual(c['handles'], 5)
            self.assertTrue(c['reconciled'])
            # 关键：c3 既有"找不到"的一条、又有"还没判"的一条，**只能算一次**。
            self.assertEqual(c['resolved'] + c['unresolved'] + c['unknown'], c['handles'])

    def test_reachable_positions_are_creator_times_product(self):
        """达人级一次：一位达人有了 OECID，他名下的**所有**线索商品都成为位置。

        平台回答的是"这个 handle 是谁"，与商品无关（商品卡也只按商品核验），所以不需要每个商品各自
        再查一次身份。c1 两条线索、c2 两条线索 → 4 个位置；c3/c5 判过找不到、c4 没判过，都不成位置。
        """
        with tempfile.TemporaryDirectory() as folder:
            self.fixture(folder)
            c = by_creator(folder)
            self.assertEqual(c['leads'], 9)
            self.assertEqual(c['positions'], 4)
            self.assertTrue(c['reconciled'])

    def test_a_blocked_creator_is_its_own_bucket_not_never_asked(self):
        """「被挡住」＝问过但没拿到平台的回答（请求/签名失败、账号起不来）。它不是"没问过"，
        也不是"找不到"：必须单独一列，而且要说得出原因，因为它得靠重试解决。"""
        with tempfile.TemporaryDirectory() as folder:
            self.fixture(folder)
            var = Path(folder) / 'var'
            with sqlite3.connect(var / 'second-cycle.sqlite') as conn:
                conn.execute('INSERT INTO source_edge VALUES(?,?,?)', ('p', 's10', json.dumps(
                    {'sourceKind': 'kalodata_http', 'sourceHandle': 'c6', 'pid': 'prod-s10'})))
                conn.execute("INSERT INTO cycle_identity_outcome VALUES('p','s10','blocked')")
                conn.commit()
            # 原因来自名单库：被挡住的那条 item 记着为什么没成。
            with sqlite3.connect(var / 'creator-discovery.sqlite') as items:
                items.execute('CREATE TABLE discovery_item(id TEXT,handle TEXT,status TEXT,reason TEXT,attempt_no INTEGER)')
                items.execute("INSERT INTO discovery_item VALUES('i1','c6','blocked','request_or_signer_error',1)")
                items.commit()
            c = by_creator(folder)
            # c6 落在"被挡住"，不再落进"从没判过"；c4 才是真的从没提交过。
            self.assertEqual((c['resolved'], c['unresolved'], c['blocked'], c['unknown']), (2, 2, 1, 1))
            self.assertEqual(c['resolved'] + c['unresolved'] + c['blocked'] + c['unknown'], c['handles'])
            self.assertTrue(c['reconciled'])
            self.assertEqual(c['blockedReasons'], [{'reason': 'request_or_signer_error', 'count': 1}])

    def test_a_missing_database_is_unavailable_not_zero(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertIsNone(by_creator(folder))
