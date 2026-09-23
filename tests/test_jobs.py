"""Job registry and schedule intent: every real job is wired and defaults off."""
import json
import sqlite3
import tempfile
import unittest
import sys
from contextlib import closing
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from lib.jobs import JOBS,last_run,load,save,status,validate  # noqa:E402


class ConfigFile(unittest.TestCase):
    def test_every_schedule_defaults_to_off(self):
        with tempfile.TemporaryDirectory() as folder:
            config=load(folder)
            self.assertEqual(len(config['jobs']),len(JOBS))
            self.assertTrue(all(not entry['enabled'] for entry in config['jobs'].values()))

    def test_unknown_job_bad_time_and_bad_weekday_are_refused(self):
        bad=[{'jobs':{'nope':{'enabled':True}}},{'jobs':{'taplink_clean':{'at':'25:00'}}},
             {'jobs':{'taplink_clean':{'at':'3:00'}}},{'jobs':{'taplink_clean':{'enabled':'yes'}}},
             {'jobs':{'taplink_clean':{'weekday':7}}},{'jobs':{'campaign_catalog_update':{'weekday':1}}},
             {'jobs':[]}]
        for value in bad:
            with self.assertRaises(ValueError):validate(value)

    def test_partial_save_keeps_other_jobs_off(self):
        with tempfile.TemporaryDirectory() as folder:
            save(folder,{'jobs':{'campaign_catalog_update':{'enabled':True,'at':'07:05'}}})
            config=load(folder)
            self.assertTrue(config['jobs']['campaign_catalog_update']['enabled'])
            self.assertFalse(config['jobs']['taplink_prepare']['enabled'])

    def test_taplink_prepare_time_round_trips_at_0730(self):
        with tempfile.TemporaryDirectory() as folder:
            saved=save(folder,{'jobs':{'taplink_prepare':{'enabled':True,'at':'07:30'}}})
            self.assertEqual(saved['jobs']['taplink_prepare']['at'],'07:30')
            self.assertEqual(load(folder)['jobs']['taplink_prepare']['at'],'07:30')


class Reporting(unittest.TestCase):
    def test_all_rows_are_real_controls_and_scheduler_is_off(self):
        with tempfile.TemporaryDirectory() as folder:
            state=status(folder);self.assertEqual(state['version'],'jobs-v3')
            self.assertFalse(state['scheduler']['running'])
            self.assertTrue(all(job['manualEndpoint'] in ('/api/workflow','/api/jobs') for job in state['jobs']))
            self.assertTrue(all(job['schedulable'] for job in state['jobs']))
            self.assertEqual({job['id'] for job in state['jobs']},
              {'taplink_clean','full_catalog_update','campaign_catalog_update','taplink_prepare',
               'kalodata_leads','oecid','send_pool_publish','inbox_monitor','agent_reply','continuous_send'})

    def test_missing_ledgers_are_unknown_not_zero(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertTrue(all(value is None for value in last_run(folder,'it').values()))

    def test_workflow_and_monitor_timestamps_are_read_back(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);var=root/'var';var.mkdir()
            with closing(sqlite3.connect(var/'second-cycle.sqlite')) as db,db:
                db.execute('CREATE TABLE plan(id TEXT,market TEXT)')
                db.execute("INSERT INTO plan VALUES('p','it')")
                db.execute('CREATE TABLE workflow_run(run_id TEXT,market TEXT)')
                db.execute("INSERT INTO workflow_run VALUES('r','it')")
                db.execute('CREATE TABLE workflow_stage_run(run_id TEXT,stage TEXT,state TEXT,finished_at REAL)')
                db.execute("INSERT INTO workflow_stage_run VALUES('r','catalog','completed',1789318884)")
                db.execute('CREATE TABLE agent_reply_run(plan_id TEXT,finished_at REAL)')
                db.execute("INSERT INTO agent_reply_run VALUES('p',1789319999)")
                db.execute('CREATE TABLE continuous_send_runtime(plan_id TEXT,last_success_at REAL)')
                db.execute("INSERT INTO continuous_send_runtime VALUES('p',1789320000)")
            (var/'cycle-inbox-status.json').write_text(json.dumps({'checkedAt':1789472255.5}))
            values=last_run(root,'it')
            self.assertEqual(values['full_catalog_update'],1789318884)
            self.assertEqual(values['campaign_catalog_update'],1789318884)
            self.assertEqual(values['inbox_monitor'],1789472255.5)
            self.assertEqual(values['agent_reply'],1789319999)
            self.assertEqual(values['continuous_send'],1789320000)


if __name__=='__main__':unittest.main()
