"""Job registry and schedule intent: defaults stay off, and no verdict is invented."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from lib.jobs import JOBS, config_path, last_run, load, save, status, validate  # noqa: E402


class ConfigFile(unittest.TestCase):
    def test_every_schedule_defaults_to_off(self):
        with tempfile.TemporaryDirectory() as folder:
            config = load(folder)
            self.assertEqual(len(config['jobs']), len(JOBS))
            self.assertTrue(all(not entry['enabled'] for entry in config['jobs'].values()))

    def test_an_unknown_job_or_a_bad_time_is_refused(self):
        for bad in [{'jobs': {'nope': {'enabled': True}}},
                    {'jobs': {'catalog_collect': {'at': '25:00'}}},
                    {'jobs': {'catalog_collect': {'at': '3:00'}}},
                    {'jobs': {'catalog_collect': {'enabled': 'yes'}}},
                    {'jobs': []}]:
            with self.assertRaises(ValueError):
                validate(bad)

    def test_a_saved_schedule_round_trips(self):
        with tempfile.TemporaryDirectory() as folder:
            saved = save(folder, {'jobs': {'catalog_collect': {'enabled': True, 'at': '03:00'}}})
            self.assertTrue(saved['jobs']['catalog_collect']['enabled'])
            self.assertEqual(load(folder)['jobs']['catalog_collect']['at'], '03:00')
            self.assertFalse(load(folder)['jobs']['link_prepare']['enabled'])

    def test_a_partial_save_keeps_the_other_jobs_at_their_defaults(self):
        with tempfile.TemporaryDirectory() as folder:
            save(folder, {'jobs': {'creator_leads': {'enabled': True}}})
            config = load(folder)
            self.assertTrue(config['jobs']['creator_leads']['enabled'])
            self.assertEqual(config['jobs']['creator_leads']['at'], '04:00')
            self.assertFalse(config['jobs']['catalog_screen']['enabled'])


class Reporting(unittest.TestCase):
    def test_status_never_claims_the_scheduler_is_ready(self):
        with tempfile.TemporaryDirectory() as folder:
            state = status(folder)
            self.assertFalse(state['schedulerReady'])
            self.assertTrue(all(job['enabled'] is False for job in state['jobs']))

    def test_only_jobs_with_a_verified_endpoint_offer_a_manual_trigger(self):
        with tempfile.TemporaryDirectory() as folder:
            by_id = {job['id']: job for job in status(folder)['jobs']}
            self.assertEqual(by_id['catalog_collect']['manualEndpoint'], '/api/global-source')
            self.assertEqual(by_id['catalog_screen']['manualEndpoint'], '/api/catalog-screen')
            # 收信监控的启停在「监控与回复」卡片上（/api/inbox）；/ops 这张表只记定时意向，
            # 所以它按设计保持 unwired，免得同一个作业有两个入口。
            self.assertEqual(by_id['inbox_monitor']['manual'], 'unwired')
            self.assertIsNone(by_id['inbox_monitor']['manualEndpoint'])
            for job_id in ('creator_leads', 'creator_profile_refresh', 'link_prepare'):
                self.assertEqual(by_id[job_id]['manual'], 'unwired')
                self.assertIsNone(by_id[job_id]['manualEndpoint'])

    def test_a_missing_database_reports_no_last_run_instead_of_a_zero(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(set(last_run(folder)),
                             {'catalog_collect', 'catalog_screen', 'link_prepare', 'inbox_monitor'})
            self.assertTrue(all(value is None for value in last_run(folder).values()))
            self.assertTrue(all(job['lastRunAt'] is None for job in status(folder)['jobs']))

    def test_the_monitors_own_last_checked_time_is_reported(self):
        """收信监控把"最近核验"写在自己的状态文件里；那是真实记录，不能用 0 冒充。"""
        with tempfile.TemporaryDirectory() as folder:
            var = Path(folder) / 'var'
            var.mkdir(parents=True, exist_ok=True)
            (var / 'cycle-inbox-status.json').write_text(
                json.dumps({'checkedAt': 1789472255.5, 'state': 'tracking'}), encoding='utf-8')
            self.assertEqual(last_run(folder)['inbox_monitor'], 1789472255.5)
            # 写坏的文件只是"没有记录"，不是 0，也不能让整页读不出来。
            (var / 'cycle-inbox-status.json').write_text('{"checkedAt": ', encoding='utf-8')
            self.assertIsNone(last_run(folder)['inbox_monitor'])
            (var / 'cycle-inbox-status.json').write_text(json.dumps({'checkedAt': None}), encoding='utf-8')
            self.assertIsNone(last_run(folder)['inbox_monitor'])

    def test_a_real_database_timestamp_is_reported(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as folder:
            var = Path(folder) / 'var'
            var.mkdir(parents=True)
            with sqlite3.connect(var / 'global-source.sqlite') as conn:
                conn.execute('CREATE TABLE global_source_run(id TEXT, updated REAL)')
                conn.execute("INSERT INTO global_source_run VALUES('r',1789318884.0)")
                conn.commit()
            self.assertEqual(status(folder)['jobs'][0]['lastRunAt'], 1789318884.0)


if __name__ == '__main__':
    unittest.main()
