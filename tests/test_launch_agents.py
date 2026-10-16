import importlib.util
from pathlib import Path
import plistlib
import tempfile
import unittest
from unittest.mock import Mock

SPEC = importlib.util.spec_from_file_location(
    'launch_agents', Path(__file__).resolve().parents[1] / 'ops/macos/launch_agents.py')
launch = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(launch)


class LaunchAgentsTest(unittest.TestCase):
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
                root = Path(directory)
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
            root = Path(directory)
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
