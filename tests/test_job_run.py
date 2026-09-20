"""Catalogue job launcher: what it refuses, what it records, and how it reports liveness."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from lib.job_run import (JOBS, command_for, load_config, progress_path, read_progress, request_stop,  # noqa: E402
                         save_config, start, state, status, stop_path, validate)


class FakeChild:
    # The liveness guard asks the real process table, so the fake must claim a live pid.
    def __init__(self, pid=os.getpid()):
        self.pid = pid


class Spawn:
    """Records what would have been launched instead of launching it."""

    def __init__(self):
        self.calls = []

    def __call__(self, command, **kwargs):
        self.calls.append((command, kwargs))
        return FakeChild()


class Config(unittest.TestCase):
    def test_defaults_are_conservative(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(load_config(folder, 'links'), {'readLimit': 15, 'creates': 0, 'lanes': 9, 'qps': 12})
            # 600 is the script's own ceiling, so one click covers the whole batch.
            self.assertEqual(load_config(folder, 'selection'), {'limit': 600})

    def test_creating_links_is_opt_in_and_bounded(self):
        for bad in [{'creates': -1}, {'creates': 201}, {'readLimit': 0}, {'readLimit': 201},
                    {'creates': 'yes'}, {'creates': 1, 'typo': 2},
                    {'lanes': 2}, {'qps': 7}, {'lanes': 9, 'qps': 99}]:
            with self.assertRaises(ValueError):
                validate('links', bad)
        self.assertEqual(validate('links', {'creates': 0}), {'readLimit': 15, 'creates': 0, 'lanes': 9, 'qps': 12})
        self.assertEqual(validate('links', {'creates': 5}), {'readLimit': 15, 'creates': 5, 'lanes': 9, 'qps': 12})

    def test_selection_limit_is_bounded(self):
        for bad in [{'limit': 0}, {'limit': 601}, {'limit': 1.5}]:
            with self.assertRaises(ValueError):
                validate('selection', bad)
        self.assertEqual(validate('selection', {'limit': 600}), {'limit': 600})

    def test_an_unknown_job_is_refused(self):
        with self.assertRaises(ValueError):
            validate('nope', {})
        with self.assertRaises(ValueError):
            validate('selection', {'limitt': 5})

    def test_a_stale_config_file_is_reported_not_fatal(self):
        # A rule can tighten after a file was written. That must not take every job panel down.
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'config/identity-run.json'
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({'batchSize': 3, 'cohortSize': 1}), encoding='utf-8')
            payload = status(folder)
            self.assertEqual(sorted(payload), sorted(JOBS))
            self.assertEqual(payload['identity']['config'], {'batchSize': 2000, 'cohortSize': 50})
            self.assertTrue(payload['identity']['configInvalid'])
            self.assertFalse(payload['links']['configInvalid'])
            # The stored file is left exactly as it was; only the next save rewrites it.
            self.assertEqual(json.loads(path.read_text(encoding='utf-8')), {'batchSize': 3, 'cohortSize': 1})

    def test_a_run_record_keeps_its_own_config(self):
        with tempfile.TemporaryDirectory() as folder:
            var = Path(folder) / 'var'
            var.mkdir(parents=True, exist_ok=True)
            (var / 'job-identity.json').write_text(json.dumps(
                {'name': 'identity', 'pid': os.getpid(), 'startedAt': 1.0,
                 'config': {'batchSize': 3, 'cohortSize': 1}}), encoding='utf-8')
            # The run's own record is history: it is reported as written, not re-validated.
            self.assertEqual(state(folder, 'identity')['config'], {'batchSize': 3, 'cohortSize': 1})

    def test_a_rejected_value_never_touches_the_file(self):
        with tempfile.TemporaryDirectory() as folder:
            save_config(folder, 'links', {'creates': 3})
            before = (Path(folder) / 'config/link-prepare-run.json').read_text(encoding='utf-8')
            with self.assertRaises(ValueError):
                save_config(folder, 'links', {'creates': 999})
            self.assertEqual((Path(folder) / 'config/link-prepare-run.json').read_text(encoding='utf-8'), before)


class Launch(unittest.TestCase):
    def test_a_read_only_link_run_says_so(self):
        with tempfile.TemporaryDirectory() as folder:
            spawn = Spawn()
            record = start(folder, 'links', {'creates': 0}, spawn=spawn)
            self.assertFalse(record['platformWrites'])
            self.assertTrue(record['running'])
            command = spawn.calls[0][0]
            self.assertIn('--creates', command)
            self.assertEqual(command[command.index('--creates') + 1], '0')

    def test_creating_links_is_flagged_as_a_platform_write(self):
        with tempfile.TemporaryDirectory() as folder:
            spawn = Spawn()
            record = start(folder, 'links', {'creates': 5}, spawn=spawn)
            self.assertTrue(record['platformWrites'])
            self.assertIn('--seed', spawn.calls[0][0])

    def test_agent_reply_worker_is_explicitly_flagged_as_platform_writing(self):
        with tempfile.TemporaryDirectory() as folder:
            spawn=Spawn();record=start(folder,'agentReply',{'interval':60},spawn=spawn)
            self.assertTrue(record['platformWrites'])
            command=spawn.calls[0][0]
            self.assertTrue(command[1].endswith('scripts/run-agent-replies.py'))
            self.assertIn('--worker',command)

    def test_a_second_copy_of_the_same_job_is_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            spawn = Spawn()
            start(folder, 'selection', spawn=spawn)
            with self.assertRaises(ValueError):
                start(folder, 'selection', spawn=spawn)
            self.assertEqual(len(spawn.calls), 1)

    def test_the_two_jobs_do_not_block_each_other(self):
        with tempfile.TemporaryDirectory() as folder:
            spawn = Spawn()
            start(folder, 'selection', spawn=spawn)
            start(folder, 'links', spawn=spawn)
            self.assertEqual(len(spawn.calls), 2)

    def test_a_finished_process_is_not_reported_as_running(self):
        with tempfile.TemporaryDirectory() as folder:
            var = Path(folder) / 'var'
            var.mkdir(parents=True, exist_ok=True)
            (var / 'job-selection.json').write_text(
                json.dumps({'name': 'selection', 'pid': 999999999, 'startedAt': 1.0}), encoding='utf-8')
            self.assertFalse(state(folder, 'selection')['running'])

    def test_a_live_process_is_reported_as_running(self):
        with tempfile.TemporaryDirectory() as folder:
            var = Path(folder) / 'var'
            var.mkdir(parents=True, exist_ok=True)
            (var / 'job-selection.json').write_text(
                json.dumps({'name': 'selection', 'pid': os.getpid(), 'startedAt': 1.0}), encoding='utf-8')
            self.assertTrue(state(folder, 'selection')['running'])

    def test_status_covers_both_jobs_even_before_anything_ran(self):
        with tempfile.TemporaryDirectory() as folder:
            payload = status(folder)
            self.assertEqual(sorted(payload), sorted(JOBS))
            self.assertIsNone(payload['selection']['run'])

    def test_commands_call_the_verified_scripts(self):
        with tempfile.TemporaryDirectory() as folder:
            selection = command_for(folder, 'selection', {'limit': 50}, '20260914-120000')
            self.assertTrue(selection[1].endswith('scripts/select-global-products.py'))
            self.assertEqual(selection[2:], ['execute', '--limit', '50'])
            links = command_for(folder, 'links', {'readLimit': 20, 'creates': 3, 'lanes': 9, 'qps': 12}, '20260914-120000')
            self.assertTrue(links[1].endswith('scripts/catalog-link-batch.py'))
            # Same-account lanes must reach the driver, or creation silently runs on one lane.
            self.assertEqual(links[links.index('--lanes') + 1], '9')
            self.assertEqual(links[links.index('--qps') + 1], '12')
            self.assertIn('20260914-120000', ' '.join(links))
            # The driver is told where to publish its running step, and it must stay under var.
            self.assertEqual(links[links.index('--progress') + 1], str(progress_path(folder, 'links')))
            identity = command_for(folder, 'identity', {'batchSize': 40, 'cohortSize': 10}, '20260914-120000')
            self.assertTrue(identity[1].endswith('scripts/identity-batch.py'))
            self.assertEqual(identity[identity.index('--limit') + 1], '40')
            self.assertEqual(identity[identity.index('--cohort-size') + 1], '10')
            self.assertEqual(identity[identity.index('--progress') + 1], str(progress_path(folder, 'identity')))
            # The stop button is a file the driver reads; the launcher must hand it the same path.
            self.assertEqual(identity[identity.index('--stop') + 1], str(stop_path(folder, 'identity')))


class IdentityConfig(unittest.TestCase):
    def test_only_the_validated_cohort_sizes_are_accepted(self):
        self.assertEqual(validate('identity', {}), {'batchSize': 2000, 'cohortSize': 50})
        self.assertEqual(validate('identity', {'batchSize': 50, 'cohortSize': 10}),
                         {'batchSize': 50, 'cohortSize': 10})
        self.assertEqual(validate('identity', {'batchSize': 500, 'cohortSize': 50}),
                         {'batchSize': 500, 'cohortSize': 50})
        # 1 is refused: the worker would leave the cohort path and report no target count.
        for bad in ({'cohortSize': 1}, {'cohortSize': 2}, {'cohortSize': 51}, {'cohortSize': '20'}, {'batchSize': 0}, {'batchSize': 200001},
                    {'batchSize': 10, 'typo': 1}):
            with self.assertRaises(ValueError):
                validate('identity', bad)


class Progress(unittest.TestCase):
    """A long batch has to publish how far it has got, or the page looks hung between passes."""

    def test_a_published_step_is_read_back(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / 'var').mkdir(parents=True, exist_ok=True)
            progress_path(folder, 'links').write_text(json.dumps({
                'startedAt': 100.0, 'updatedAt': 160.0, 'phase': 'read', 'step': 4,
                'pass': 3, 'passes': 80, 'created': 0, 'total': 3450,
                'states': {'pending': 2216, 'ready': 1098, 'reuse': 1191, 'review': 1161}
            }), encoding='utf-8')
            step = read_progress(folder, 'links')
            self.assertEqual(step['phase'], 'read')
            self.assertEqual(step['pass'], 3)
            self.assertEqual(step['states']['pending'], 2216)

    def test_a_job_without_a_published_step_reports_none(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / 'var').mkdir(parents=True, exist_ok=True)
            self.assertIsNone(read_progress(folder, 'links'))
            (Path(folder) / 'var/job-selection.json').write_text(json.dumps(
                {'name': 'selection', 'pid': os.getpid(), 'startedAt': 1.0}), encoding='utf-8')
            self.assertIsNone(state(folder, 'selection')['progress'])

    def test_the_identity_step_keeps_its_own_fields(self):
        # Two jobs publish genuinely different steps; one normalizer must not flatten the other.
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / 'var').mkdir(parents=True, exist_ok=True)
            progress_path(folder, 'identity').write_text(json.dumps({
                'startedAt': 100.0, 'updatedAt': 160.0, 'rounds': 7, 'limit': 300, 'cohortSize': 20,
                'pendingAtStart': 1035, 'pending': 900, 'claimed': 140, 'lastTargets': 20,
                'found': 110, 'notFound': 25, 'stopReason': None}), encoding='utf-8')
            step = read_progress(folder, 'identity')
            self.assertEqual(step['rounds'], 7)
            self.assertEqual(step['pendingAtStart'], 1035)
            self.assertEqual(step['found'], 110)
            self.assertIsNone(step['stopReason'])
            self.assertEqual(step['cohortSize'], 20)
            # 老进度文件没有 errors：缺字段是历史，不是坏包。
            self.assertEqual(step['errors'], [])

    def test_the_inbox_job_runs_the_monitor_that_already_exists(self):
        """收信监控不是新写的轮询器：作业面板直接跑那个早就在跑的脚本，并沿用它的状态文件。"""
        with tempfile.TemporaryDirectory() as folder:
            command = command_for(folder, 'inbox', {'limit': 6, 'interval': 60}, '20260915-190000')
            self.assertTrue(command[1].endswith('scripts/poll-cycle-inbox.py'))
            self.assertIn('--worker', command)
            self.assertEqual(command[command.index('--limit') + 1], '6')
            self.assertEqual(command[command.index('--interval') + 1], '60')
            self.assertEqual(command[command.index('--stop') + 1], str(stop_path(folder, 'inbox')))
            # 脚本本来就在写这份状态（页面也在读），再写一份就成了两个真相。
            self.assertEqual(progress_path(folder, 'inbox').name, 'cycle-inbox-status.json')

    def test_the_inbox_step_reports_why_a_round_read_nothing(self):
        with tempfile.TemporaryDirectory() as folder:
            var = Path(folder) / 'var'
            var.mkdir(parents=True, exist_ok=True)
            (var / 'cycle-inbox-status.json').write_text(json.dumps({
                'processed': 0, 'added': 0, 'historical': 0, 'liveReplies': 0, 'indexedTargets': 555,
                'serviceDecisions': 0, 'errorCode': 'live_guard_busy', 'checkedAt': 1789472118.5,
                'status': {'conversations': 555, 'events': 1352, 'historicalEvents': 316, 'gaps': 0,
                           'pendingContent': 25, 'lastCheckedAt': 1789472058.3,
                           'automaticRepliesEnabled': False}}), encoding='utf-8')
            step = read_progress(folder, 'inbox')
            self.assertEqual(step['errorCode'], 'live_guard_busy')
            self.assertEqual(step['indexedTargets'], 555)
            self.assertEqual(step['conversations'], 555)
            self.assertEqual(step['pendingContent'], 25)
            self.assertFalse(step['automaticRepliesEnabled'])

    def test_the_identity_step_carries_the_reason_the_last_round_died_of(self):        # `internal_error` 单独出现时操作者无从下手：驱动抓到的 traceback 必须一路送到页面。
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / 'var').mkdir(parents=True, exist_ok=True)
            progress_path(folder, 'identity').write_text(json.dumps({
                'startedAt': 100.0, 'updatedAt': 160.0, 'rounds': 2, 'limit': 300, 'cohortSize': 20,
                'pendingAtStart': 1035, 'pending': 900, 'claimed': 140, 'lastTargets': 20,
                'found': 110, 'notFound': 25, 'stopReason': 'internal_error',
                'errors': [{'round': 1, 'detail': 'earlier'}, {'round': 2, 'detail': 'line\n' * 900 + 'boom'}]}),
                encoding='utf-8')
            step = read_progress(folder, 'identity')
            self.assertEqual(step['stopReason'], 'internal_error')
            # 只留最近两条，并且截断——页面不该被一段日志灌满。
            self.assertEqual([row['round'] for row in step['errors']], [1, 2])
            self.assertLessEqual(len(step['errors'][1]['detail']), 1200)
            self.assertTrue(step['errors'][1]['detail'].endswith('boom'))
            # 重试计数也要到页面：否则"在等锁"和"卡死"看起来一模一样。
            self.assertEqual(step['retry'], 0)
            (Path(folder) / 'var/job-identity-progress.json').write_text(json.dumps({
                'startedAt': 100.0, 'updatedAt': 160.0, 'rounds': 0, 'limit': 300, 'cohortSize': 20,
                'pendingAtStart': 1035, 'pending': 1035, 'claimed': 0, 'lastTargets': 0,
                'found': 0, 'notFound': 0, 'stopReason': None, 'retry': 2,
                'roundRunning': 0, 'roundStartedAt': None}), encoding='utf-8')
            self.assertEqual(read_progress(folder, 'identity')['retry'], 2)

    def test_a_torn_step_file_is_ignored_rather_than_reported(self):
        with tempfile.TemporaryDirectory() as folder:
            var = Path(folder) / 'var'
            var.mkdir(parents=True, exist_ok=True)
            (var / 'job-links-progress.json').write_text('{"phase": "read", "step":', encoding='utf-8')
            (var / 'job-selection.json').write_text(json.dumps(
                {'name': 'selection', 'pid': os.getpid(), 'startedAt': 1.0}), encoding='utf-8')
            self.assertIsNone(read_progress(folder, 'selection'))

    def test_a_stop_request_is_recorded_and_shown_while_the_job_runs(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / 'var').mkdir(parents=True, exist_ok=True)
            (Path(folder) / 'var/job-identity.json').write_text(json.dumps(
                {'name': 'identity', 'pid': os.getpid(), 'startedAt': 1.0}), encoding='utf-8')
            self.assertFalse(state(folder, 'identity')['stopping'])
            request_stop(folder, 'identity')
            self.assertTrue(stop_path(folder, 'identity').exists())
            self.assertTrue(state(folder, 'identity')['stopping'])

    def test_stopping_something_that_is_not_running_is_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / 'var').mkdir(parents=True, exist_ok=True)
            with self.assertRaises(ValueError):
                request_stop(folder, 'identity')
            with self.assertRaises(ValueError):
                request_stop(folder, 'nope')
            self.assertFalse(stop_path(folder, 'identity').exists())

    def test_starting_a_job_clears_the_previous_runs_step(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / 'var').mkdir(parents=True, exist_ok=True)
            progress_path(folder, 'links').write_text(json.dumps({'phase': 'done', 'step': 9}), encoding='utf-8')
            stop_path(folder, 'links').write_text('{}', encoding='utf-8')
            record = start(folder, 'links', {'creates': 0}, spawn=Spawn())
            self.assertTrue(record['running'])
            self.assertFalse(record['stopping'])
            # Showing the old finished bar while the new batch starts would claim it already ended.
            self.assertFalse(progress_path(folder, 'links').exists())
            # And a leftover stop request would end the new batch before its first round.
            self.assertFalse(stop_path(folder, 'links').exists())
            self.assertIsNone(state(folder, 'links')['progress'])
            self.assertFalse(state(folder, 'links')['stopping'])


if __name__ == '__main__':
    unittest.main()
