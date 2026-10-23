import importlib.util
from contextlib import closing
from pathlib import Path
import plistlib
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock

SPEC = importlib.util.spec_from_file_location(
    'launch_agents', Path(__file__).resolve().parents[1] / 'ops/macos/launch_agents.py')
launch = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(launch)


class LaunchAgentsTest(unittest.TestCase):
    def first_database(self, root, mode='stopped', approvals=0):
        (root / 'var').mkdir()
        with closing(sqlite3.connect(root / 'var/second-cycle.sqlite')) as db, db:
            db.executescript('''CREATE TABLE plan(id TEXT,market TEXT,institution TEXT);
             INSERT INTO plan VALUES('br-plan','br','bjn-local-research');
             CREATE TABLE first_outreach_control(plan_id TEXT,mode TEXT,approvals_enabled INTEGER,revision INTEGER);
             CREATE TABLE cycle_delivery(plan_id TEXT,state TEXT,snapshot TEXT);''')
            db.execute('INSERT INTO first_outreach_control VALUES(?,?,?,9)', ('br-plan',mode,approvals))

    def test_first_supervision_never_initializes_missing_state(self):
        with tempfile.TemporaryDirectory() as directory:
            state, start = Mock(), Mock()
            self.assertEqual(launch.ensure_first_outreach(directory,state,start), {'state':'missing_database'})
            state.assert_not_called()
            start.assert_not_called()
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_first_supervision_preserves_manual_pause_and_grant_revision(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            self.first_database(root)
            state, start = Mock(), Mock(return_value=42)
            self.assertEqual(launch.ensure_first_outreach(root,state,start), {'state':'operator_stopped'})
            state.assert_not_called()
            start.assert_not_called()
            with closing(sqlite3.connect(root/'var/second-cycle.sqlite')) as db, db:
                db.execute("UPDATE first_outreach_control SET mode='continuous'")
            def pause(_):
                with closing(sqlite3.connect(root/'var/second-cycle.sqlite')) as db, db:
                    db.execute("UPDATE first_outreach_control SET mode='stopped'")
                return {'running':False}
            self.assertEqual(launch.ensure_first_outreach(root,pause,start), {'state':'operator_stopped'})
            start.assert_not_called()
            with closing(sqlite3.connect(root/'var/second-cycle.sqlite')) as db, db:
                self.assertEqual(db.execute('SELECT mode,approvals_enabled,revision FROM first_outreach_control').fetchone(),('stopped',0,9))

    def test_first_supervision_recovers_only_absent_authorized_worker(self):
        for mode, approvals in [('continuous',0),('canary',0),('stopped',1)]:
            with self.subTest(mode=mode,approvals=approvals), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                self.first_database(root,mode,approvals)
                state=Mock(side_effect=[{'running':False},{'running':True,'pid':42}])
                start=Mock(return_value=42)
                self.assertEqual(launch.ensure_first_outreach(root,state,start),{'state':'started','pid':42,'reason':'authorized'})
                self.assertEqual(launch.ensure_first_outreach(root,state,start),{'state':'running','pid':42})
                start.assert_called_once_with(root)

    def test_first_supervision_recovers_original_readback_without_new_grant(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            self.first_database(root)
            with closing(sqlite3.connect(root/'var/second-cycle.sqlite')) as db, db:
                db.execute('INSERT INTO cycle_delivery VALUES(?,?,?)',('br-plan','ready','{"outreachPurpose":"first"}'))
            state,start=Mock(return_value={'running':False}),Mock(return_value=42)
            self.assertEqual(launch.ensure_first_outreach(root,state,start),{'state':'operator_stopped'})
            start.assert_not_called()
            with closing(sqlite3.connect(root/'var/second-cycle.sqlite')) as db, db:
                db.execute('INSERT INTO cycle_delivery VALUES(?,?,?)',('foreign-plan','unknown','{"outreachPurpose":"first"}'))
            self.assertEqual(launch.ensure_first_outreach(root,state,start),{'state':'operator_stopped'})
            with closing(sqlite3.connect(root/'var/second-cycle.sqlite')) as db, db:
                db.execute('INSERT INTO cycle_delivery VALUES(?,?,?)',('br-plan','unknown','{"outreachPurpose":"second"}'))
            self.assertEqual(launch.ensure_first_outreach(root,state,start),{'state':'started','pid':42,'reason':'original_delivery'})
            with closing(sqlite3.connect(root/'var/second-cycle.sqlite')) as db, db:
                self.assertEqual(db.execute('SELECT mode,approvals_enabled,revision FROM first_outreach_control').fetchone(),('stopped',0,9))

    def test_first_supervision_recovers_submitted_parts_after_pause(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            self.first_database(root)
            with closing(sqlite3.connect(root/'var/second-cycle.sqlite')) as db, db:
                db.executescript('ALTER TABLE cycle_delivery ADD COLUMN id TEXT; CREATE TABLE cycle_delivery_part(delivery_id TEXT,state TEXT);')
                db.execute('INSERT INTO cycle_delivery VALUES(?,?,?,?)',('br-plan','running','{}','original-part'))
                db.execute('INSERT INTO cycle_delivery_part VALUES(?,?)',('original-part','accepted'))
            start=Mock(return_value=42)
            self.assertEqual(launch.ensure_first_outreach(root,Mock(return_value={'running':False}),start),
                             {'state':'started','pid':42,'reason':'original_delivery'})
            start.assert_called_once_with(root)

    def test_missing_database_does_not_start_or_create_state(self):
        with tempfile.TemporaryDirectory() as directory:
            state, start = Mock(), Mock()
            self.assertEqual(launch.ensure_scheduler(directory, state, start)['state'], 'missing_database')
            state.assert_not_called()
            start.assert_not_called()
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_operator_stop_is_preserved_including_during_liveness_check(self):
        for during_check in (False, True):
            with self.subTest(during_check=during_check), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                (root / 'var').mkdir()
                (root / 'var/second-cycle.sqlite').touch()
                stop = root / 'var/operations-scheduler.stop'
                def state(_):
                    stop.write_text('operator request')
                    return {'running': False}
                if not during_check:
                    stop.write_text('operator request')
                start = Mock()
                self.assertEqual(launch.ensure_scheduler(root, state, start)['state'], 'operator_stopped')
                start.assert_not_called()
                self.assertEqual(stop.read_text(), 'operator request')

    def test_repeated_checks_start_only_missing_scheduler(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / 'var').mkdir()
            (root / 'var/second-cycle.sqlite').touch()
            state = Mock(side_effect=[{'running': False}, {'running': True, 'pid': 42}])
            start = Mock(return_value={'pid': 42})
            self.assertEqual(launch.ensure_scheduler(root, state, start), {'state': 'started', 'pid': 42})
            self.assertEqual(launch.ensure_scheduler(root, state, start), {'state': 'running', 'pid': 42})
            start.assert_called_once_with(root)

    def test_launchd_uses_fixed_commands_and_preserves_network_route(self):
        configs = launch.jobs(Path('/tmp/project with spaces'), '/opt/homebrew/bin/node',
                              {'HTTPS_PROXY': 'http://127.0.0.1:7897', 'UNRELATED_SECRET': 'excluded'})
        for config in configs.values():
            self.assertEqual(plistlib.loads(plistlib.dumps(config)), config)
            self.assertEqual(config['EnvironmentVariables']['HTTPS_PROXY'], 'http://127.0.0.1:7897')
            self.assertNotIn('UNRELATED_SECRET', config['EnvironmentVariables'])
            self.assertTrue(config['RunAtLoad'])
        watch = configs['io.bdhub.agent.scheduler-watch']
        self.assertEqual(watch['StartInterval'], 60)
        self.assertNotIn('KeepAlive', watch)
        self.assertEqual(watch['ProgramArguments'][-1], 'ensure-scheduler')


if __name__ == '__main__':
    unittest.main()
