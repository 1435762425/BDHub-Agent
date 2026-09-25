"""The identity backfill driver: how many rounds it runs, and what makes it stop."""
import importlib.util
import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

spec = importlib.util.spec_from_file_location('identity_batch', ROOT / 'scripts/identity-batch.py')
identity_batch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(identity_batch)


class Measured:
    """A stand-in for the real counts, moving exactly as the rounds say it should."""

    def __init__(self, *, pending, resolved=0, unresolved=0, found_per_round=0, not_found_per_round=0):
        self.pending, self.resolved, self.unresolved = pending, resolved, unresolved
        self.found_per_round, self.not_found_per_round = found_per_round, not_found_per_round

    def __call__(self):
        return {'pendingCreators': self.pending, 'resolvedCreators': self.resolved,
                'unresolvedCreators': self.unresolved, 'pendingLeads': self.pending, 'leads': self.pending}

    def settle(self, targets):
        handled = min(targets, self.pending)
        self.pending -= handled
        self.resolved += self.found_per_round
        self.unresolved += self.not_found_per_round


def rounds(driver, *, claim=20, fail_after=None, log=None):
    """A fake round runner: each call settles one cohort and records what it was asked to do."""
    calls = []

    def step(cohort_size):
        calls.append(cohort_size)
        if log is not None:
            log.append(len(calls))
        if fail_after is not None and len(calls) > fail_after:
            return {'ok': False, 'code': 'account_not_startable'}
        claimed = min(claim, driver.pending)
        driver.settle(claimed)
        return {'ok': True, 'code': None, 'targets': claimed,
                'cohortId': f'cohort-{len(calls)}', 'seconds': 1.0}

    return step, calls


class Run(unittest.TestCase):
    def _run(self, folder, measured, *, limit=2000, cohort_size=20, step=None, progress=None,
             rounds_max=None, stop=None):
        return identity_batch.run(folder, limit=limit, cohort_size=cohort_size, progress=progress,
                                  rounds=rounds_max, clock=lambda: 1000.0,
                                  measured=measured, round_runner=step, stop=stop,
                                  pause=lambda _seconds: None)

    def test_an_empty_backlog_never_calls_the_worker(self):
        with tempfile.TemporaryDirectory() as folder:
            step, calls = rounds(Measured(pending=0))
            report = self._run(folder, Measured(pending=0), step=step)
            self.assertEqual(report['stopReason'], 'nothing_pending')
            self.assertEqual(calls, [])
            self.assertEqual(report['platformWrites'], 0)

    def test_it_walks_the_backlog_until_nothing_is_left(self):
        with tempfile.TemporaryDirectory() as folder:
            measured = Measured(pending=45, found_per_round=1)
            step, calls = rounds(measured, claim=20)
            report = self._run(folder, measured, step=step)
            self.assertEqual(report['stopReason'], 'backlog_clear')
            self.assertEqual(report['rounds'], 3)
            self.assertEqual(report['claimed'], 45)
            self.assertEqual(report['pending'], 0)
            self.assertEqual(report['found'], 3)

    def test_the_limit_is_a_ceiling_on_handles_not_a_target(self):
        with tempfile.TemporaryDirectory() as folder:
            measured = Measured(pending=1000, found_per_round=1)
            step, calls = rounds(measured, claim=20)
            report = self._run(folder, measured, limit=45, step=step)
            self.assertEqual(report['stopReason'], 'limit_reached')
            self.assertEqual(report['claimed'], 60)
            self.assertEqual(len(calls), 3)
            self.assertEqual(report['pending'], 940)

    def test_a_short_backlog_runs_short_and_is_never_padded(self):
        with tempfile.TemporaryDirectory() as folder:
            measured = Measured(pending=8, found_per_round=0, not_found_per_round=0)
            step, calls = rounds(measured, claim=20)
            report = self._run(folder, measured, limit=2000, step=step)
            self.assertEqual(report['stopReason'], 'backlog_clear')
            self.assertEqual(report['rounds'], 1)
            self.assertEqual(report['pendingAtStart'], 8)
            self.assertFalse(report.get('padded', False))

    def test_a_refused_round_stops_the_run_with_its_own_code(self):
        with tempfile.TemporaryDirectory() as folder:
            measured = Measured(pending=100)
            step, _ = rounds(measured, fail_after=0)
            report = self._run(folder, measured, step=step)
            self.assertEqual(report['stopReason'], 'account_not_startable')
            self.assertEqual(report['rounds'], 1)
            self.assertEqual(report['pending'], 100)

    def test_a_failed_round_keeps_the_reason_it_died_of(self):
        """`internal_error` 本身不可诊断（2026-09-15 的补 OECID 就停在它上面，什么都没留下）：
        驱动从子进程抓到的 stderr 尾巴必须跟停止原因一起落进报告，页面才能说出真正的原因。"""
        with tempfile.TemporaryDirectory() as folder:
            progress = Path(folder) / 'job-identity-progress.json'
            measured = Measured(pending=100)

            def broken(cohort_size):
                return {'ok': False, 'code': 'internal_error', 'detail':
                        'Traceback (most recent call last):\n'
                        '  File "lib/discovery_cohort.py", line 24, in run_cohort\n'
                        'sqlite3.OperationalError: database is locked'}

            report = self._run(folder, measured, step=broken, progress=progress)
            self.assertEqual(report['stopReason'], 'internal_error')
            self.assertEqual(len(report['errors']), 1)
            self.assertEqual(report['errors'][0]['round'], 1)
            self.assertIn('database is locked', report['errors'][0]['detail'])
            published = json.loads(progress.read_text(encoding='utf-8'))
            self.assertIn('database is locked', published['errors'][0]['detail'])

    def test_a_failure_without_a_detail_still_reports_only_its_code(self):
        with tempfile.TemporaryDirectory() as folder:
            measured = Measured(pending=100)

            def silent(cohort_size):
                return {'ok': False, 'code': 'round_output_invalid'}

            report = self._run(folder, measured, step=silent)
            self.assertEqual(report['stopReason'], 'round_output_invalid')
            self.assertEqual(report['errors'], [])

    def test_a_locked_channel_is_retried_without_burning_a_round(self):
        """别人正持有那个库时这一轮**还没碰到平台**：重试不计入轮次、不动队列，只把等待写进进度。"""
        with tempfile.TemporaryDirectory() as folder:
            progress = Path(folder) / 'job-identity-progress.json'
            measured = Measured(pending=100, found_per_round=1)
            seen = []

            def step(cohort_size):
                seen.append(len(seen))
                if len(seen) <= 2:
                    return {'ok': False, 'code': 'identity_policy_unreadable'}
                return {'ok': True, 'code': None, 'targets': 20, 'seconds': 1.0}

            report = self._run(folder, measured, limit=20, step=step, progress=progress)
            self.assertEqual(len(seen), 3)
            self.assertEqual(report['rounds'], 1)
            self.assertEqual(report['claimed'], 20)
            self.assertEqual(report['retry'], 0)
            self.assertEqual(report['stopReason'], 'limit_reached')
            self.assertEqual(report['errors'], [])

    def test_a_lock_that_never_clears_still_ends_the_run_and_says_why(self):
        with tempfile.TemporaryDirectory() as folder:
            progress = Path(folder) / 'job-identity-progress.json'
            measured = Measured(pending=100)
            calls = []

            def locked(cohort_size):
                calls.append(cohort_size)
                return {'ok': False, 'code': 'identity_policy_unreadable'}

            report = self._run(folder, measured, limit=20, step=locked, progress=progress)
            # 重试是有上限的：连续用完就如实停下，并保留那个码（不是 internal_error）。
            self.assertEqual(len(calls), identity_batch.RETRIES + 1)
            self.assertEqual(report['stopReason'], 'identity_policy_unreadable')
            # 三次是"还没碰到平台的等待"，最后那一次是真正记进账的失败轮次。
            self.assertEqual(report['retry'], identity_batch.RETRIES)
            self.assertEqual(report['rounds'], 1)
            self.assertEqual(report['claimed'], 0)
            published = json.loads(progress.read_text(encoding='utf-8'))
            self.assertEqual(published['retry'], identity_batch.RETRIES)

    def test_a_queue_that_claims_nothing_twice_stops_instead_of_spinning(self):
        with tempfile.TemporaryDirectory() as folder:
            measured = Measured(pending=100)

            def stuck(cohort_size):
                return {'ok': True, 'code': None, 'targets': 0}

            report = self._run(folder, measured, step=stuck)
            self.assertEqual(report['stopReason'], 'queue_stalled')
            self.assertEqual(report['rounds'], identity_batch.IDLE_ROUNDS)
            self.assertEqual(report['claimed'], 0)

    def test_every_round_publishes_a_readable_step(self):
        with tempfile.TemporaryDirectory() as folder:
            progress = Path(folder) / 'job-identity-progress.json'
            measured = Measured(pending=25, found_per_round=2, not_found_per_round=1)
            step, _ = rounds(measured, claim=20)
            self._run(folder, measured, step=step, progress=progress)
            published = json.loads(progress.read_text(encoding='utf-8'))
            self.assertEqual(published['pendingAtStart'], 25)
            self.assertEqual(published['pending'], 0)
            self.assertEqual(published['claimed'], 25)
            self.assertEqual(published['found'], 4)
            self.assertEqual(published['notFound'], 2)
            self.assertEqual(published['stopReason'], 'backlog_clear')
            # 跑完之后不能还写着"第 N 轮进行中"。
            self.assertEqual(published['roundRunning'], 0)
            self.assertIsNone(published['roundStartedAt'])
            # No .tmp leftover: the file is replaced, never written in place.
            self.assertEqual([p.name for p in Path(folder).iterdir()], ['job-identity-progress.json'])

    def test_every_round_publishes_that_it_started(self):
        """每轮**开始时**也要发布一次：一轮几十秒，只在轮末发布的话进度条会长时间纹丝不动，
        操作者看到的就是"点了没反应"（实测就是这个症状）。"""
        with tempfile.TemporaryDirectory() as folder:
            progress = Path(folder) / 'job-identity-progress.json'
            seen = []
            measured = Measured(pending=40, found_per_round=1, not_found_per_round=0)
            inner, _ = rounds(measured, claim=20)

            def step(size):
                # 观察这一轮**执行期间**磁盘上的进度文件。
                if progress.exists():
                    seen.append(json.loads(progress.read_text(encoding='utf-8')))
                return inner(size)

            self._run(folder, measured, step=step, progress=progress)
            during = [row for row in seen if row.get('roundRunning')]
            self.assertTrue(during, '每轮开始时必须发布一次"进行中"')
            self.assertEqual(during[0]['roundRunning'], 1)
            self.assertIsNotNone(during[0]['roundStartedAt'])
            # 进行中的那次发布里，已领数还没有加上这一轮。
            self.assertEqual(during[0]['claimed'], 0)

    def test_the_stop_file_ends_the_batch_after_the_round_in_flight(self):
        with tempfile.TemporaryDirectory() as folder:
            stop = Path(folder) / 'job-identity.stop'
            measured = Measured(pending=100, found_per_round=0)
            step, calls = rounds(measured, claim=20)

            # The operator asks to stop while the first round is already running: that round finishes,
            # the next one never starts, and nothing about it is reported as a failure.
            def step_then_stop(cohort_size):
                outcome = step(cohort_size)
                stop.write_text('{}', encoding='utf-8')
                return outcome

            report = self._run(folder, measured, step=step_then_stop, stop=stop)
            self.assertEqual(report['stopReason'], 'stopped_by_operator')
            self.assertEqual(report['rounds'], 1)
            self.assertEqual(len(calls), 1)
            self.assertEqual(report['claimed'], 20)
            self.assertEqual(report['errors'], [])

    def test_a_stop_requested_before_the_first_round_runs_nothing(self):
        with tempfile.TemporaryDirectory() as folder:
            stop = Path(folder) / 'job-identity.stop'
            stop.write_text('{}', encoding='utf-8')
            measured = Measured(pending=100)
            step, calls = rounds(measured, claim=20)
            report = self._run(folder, measured, step=step, stop=stop)
            self.assertEqual(report['stopReason'], 'stopped_by_operator')
            self.assertEqual(calls, [])
            self.assertEqual(report['platformWrites'], 0)

    def test_bad_parameters_are_refused_before_anything_runs(self):
        with tempfile.TemporaryDirectory() as folder:
            measured = Measured(pending=5)
            step, calls = rounds(measured)
            for bad in ({'limit': 0}, {'limit': 200001}, {'limit': 'many'}):
                with self.assertRaises(ValueError):
                    identity_batch.run(folder, measured=measured, round_runner=step, **bad)
            for bad_cohort in (1, 2, 30, '20'):
                with self.assertRaises(ValueError):
                    identity_batch.run(folder, cohort_size=bad_cohort, measured=measured, round_runner=step)
            self.assertEqual(calls, [])


class OneRound(unittest.TestCase):
    """The driver reads the real worker subprocess, so the envelope plumbing is tested for real."""

    def _fake_worker(self, parent, body):
        root = Path(parent) / 'repo'
        (root / 'scripts').mkdir(parents=True)
        binary = root / '.venv/bin'
        binary.mkdir(parents=True)
        (binary / 'python').symlink_to(sys.executable)
        (root / 'scripts/creator-profile-refresh.py').write_text(body, encoding='utf-8')
        return root

    def test_a_named_refusal_reaches_the_driver_as_its_own_code(self):
        with tempfile.TemporaryDirectory() as parent:
            root = self._fake_worker(parent, (
                "import json,sys\n"
                "print(json.dumps({'error':{'code':'identity_policy_unreadable','message':'identity_policy_unreadable','status':503}}))\n"
                "sys.exit(1)\n"))
            outcome = identity_batch.one_round(root, 20)
            self.assertFalse(outcome['ok'])
            self.assertEqual(outcome['code'], 'identity_policy_unreadable')
            self.assertEqual(outcome['detail'], 'identity_policy_unreadable')

    def test_the_worker_explains_a_crash_without_publishing_it(self):
        # stderr 只报异常**类型**并指向本地日志：原始消息可能带着凭证或远端原文，不能进接口回答。
        with tempfile.TemporaryDirectory() as parent:
            root = self._fake_worker(parent, (
                "import json,sys\n"
                "sys.stderr.write('identity_worker_crash ValueError -- traceback: var/identity-worker-crash.log\\n')\n"
                "print(json.dumps({'error':{'code':'internal_error','message':'Profile refresh service error','status':500}}))\n"
                "sys.exit(1)\n"))
            outcome = identity_batch.one_round(root, 20)
            self.assertEqual(outcome['code'], 'internal_error')
            self.assertIn('ValueError', outcome['detail'])
            self.assertIn('identity-worker-crash.log', outcome['detail'])

    def test_a_round_that_returns_readable_targets_is_not_a_failure(self):
        with tempfile.TemporaryDirectory() as parent:
            root = self._fake_worker(parent, (
                "import json\n"
                "print(json.dumps({'cohort':{'id':'discovery_cohort_x','targets':20,'seconds':1.5}}))\n"))
            outcome = identity_batch.one_round(root, 20)
            self.assertTrue(outcome['ok'])
            self.assertEqual(outcome['targets'], 20)


if __name__ == '__main__':
    unittest.main()


class SkipJudged(unittest.TestCase):
    """达人身份一位查一次：判过的 handle 不再重复问平台。"""

    def test_the_driver_tells_the_worker_to_skip_judged_handles(self):
        # 实测一轮 1,180 条结算里 558 条是"同一位达人的其它线索"：不带上这个开关，补 OECID 会把
        # 已经判过的达人反复重查，白烧采集账号额度。
        class Result:
            returncode = 0
            stdout = json.dumps({'cohort': {'targets': 0, 'id': 'c', 'seconds': 1.0}})
            stderr = ''

        seen = {}
        original = identity_batch.subprocess.run
        identity_batch.subprocess.run = lambda command, **kw: (seen.update(command=command), Result())[1]
        try:
            outcome = identity_batch.one_round(ROOT, 20)
        finally:
            identity_batch.subprocess.run = original
        self.assertIn('--skip-judged', seen['command'])
        self.assertIn('--once', seen['command'])
        self.assertEqual(seen['command'][seen['command'].index('--cohort-size') + 1], '20')
        self.assertTrue(outcome['ok'])

    def test_a_scoped_round_passes_the_exact_discovery_batch(self):
        class Result:
            returncode = 0
            stdout = json.dumps({'cohort': {'targets': 0, 'id': 'c', 'seconds': 1.0}})
            stderr = ''
        batch='discovery_'+'a'*32;seen={};original=identity_batch.subprocess.run
        identity_batch.subprocess.run=lambda command,**kw:(seen.update(command=command),Result())[1]
        try:identity_batch.one_round(ROOT,20,only_batch=batch)
        finally:identity_batch.subprocess.run=original
        self.assertEqual(seen['command'][seen['command'].index('--only-batch')+1],batch)

    def test_a_full_cohort_can_use_several_exact_current_batches(self):
        class Result:
            returncode = 0
            stdout = json.dumps({'cohort': {'targets': 0, 'id': 'c', 'seconds': 1.0}})
            stderr = ''
        batches=['discovery_'+'a'*32,'discovery_'+'b'*32];seen={};original=identity_batch.subprocess.run
        identity_batch.subprocess.run=lambda command,**kw:(seen.update(command=command),Result())[1]
        try:identity_batch.one_round(ROOT,50,only_batch=batches)
        finally:identity_batch.subprocess.run=original
        indexes=[i for i,value in enumerate(seen['command']) if value=='--only-batch']
        self.assertEqual([seen['command'][i+1] for i in indexes],batches)


class CurrentScope(unittest.TestCase):
    def test_the_next_round_uses_the_exact_batch_with_most_current_unjudged_handles(self):
        with tempfile.TemporaryDirectory() as folder:
            var=Path(folder)/'var';var.mkdir()
            b1='discovery_'+'1'*32;b2='discovery_'+'2'*32;old='discovery_'+'0'*32
            with closing(sqlite3.connect(var/'second-cycle.sqlite')) as db, db:
                db.executescript('''
                  CREATE TABLE plan(id TEXT,institution TEXT,market TEXT,state TEXT);
                  CREATE TABLE lead_query_head(plan_id TEXT,pid TEXT,query_id TEXT);
                  CREATE TABLE lead_query_selection(query_id TEXT,source_id TEXT);
                  CREATE TABLE source_edge_index(plan_id TEXT,source_id TEXT,pid TEXT,source_handle TEXT,source_kind TEXT);
                  CREATE TABLE cycle_identity_resolution(plan_id TEXT,source_id TEXT,creator_id TEXT);
                  CREATE TABLE cycle_identity_outcome(plan_id TEXT,source_id TEXT,status TEXT);
                  CREATE TABLE cycle_identity_outbox(batch_id TEXT,payload TEXT,plan_id TEXT,settled INTEGER);
                ''')
                db.execute("INSERT INTO plan VALUES('p','bjn-local-research','it','active')")
                for n,handle in enumerate(('h1','h2','h3'),1):
                    db.execute('INSERT INTO lead_query_head VALUES(?,?,?)',('p',str(n),'q'+str(n)))
                    db.execute('INSERT INTO lead_query_selection VALUES(?,?)',('q'+str(n),'s'+str(n)))
                    db.execute("INSERT INTO source_edge_index VALUES('p',?,?,?,'kalodata_http')",('s'+str(n),str(n),handle))
                db.execute('INSERT INTO cycle_identity_outbox VALUES(?,?,?,0)',(b1,json.dumps({'handles':['h1','h2']}),'p'))
                db.execute('INSERT INTO cycle_identity_outbox VALUES(?,?,?,0)',(b2,json.dumps({'handles':['h2','h3']}),'p'))
            with closing(sqlite3.connect(var/'creator-discovery.sqlite')) as db, db:
                db.executescript('CREATE TABLE discovery_batch(id TEXT,status TEXT); CREATE TABLE discovery_item(batch_id TEXT,handle TEXT,status TEXT,attempt_no INTEGER,retry_at REAL,outcome TEXT);')
                db.executemany('INSERT INTO discovery_batch VALUES(?,?)',[(old,'completed'),(b1,'queued'),(b2,'queued')])
                db.execute("INSERT INTO discovery_item VALUES(?,?,?,1,0,NULL)",(old,'h1','unresolved'))
                db.executemany('INSERT INTO discovery_item VALUES(?,?,?,1,0,NULL)',[(b1,'h1','queued'),(b1,'h2','queued'),(b2,'h2','queued'),(b2,'h3','queued')])
            self.assertEqual(identity_batch.current_batch_scope(folder),b2)


if __name__ == '__main__':
    unittest.main()
