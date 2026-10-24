"""Current Agent health is independent of the last batch outcome; all observation stays read-only."""
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from lib.schema_migrations import apply_database
from lib.second_cycle import CycleStore
from lib.template_library import DEFAULT_AGENT_SETTING, save_agent_setting

NOW = 1_790_000_000.0


class OperationsHomeRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / 'var').mkdir()
        with CycleStore(self.root / 'var/second-cycle.sqlite', clock=lambda: NOW) as store:
            self.plans = {market: store.plan('bjn-local-research', market) for market in ('it', 'br')}
        apply_database(self.root, 'second-cycle', clock=lambda: NOW)
        with CycleStore(self.root / 'var/second-cycle.sqlite', clock=lambda: NOW) as store:
            for plan in self.plans.values():
                save_agent_setting(store, plan, 0, {key: value for key, value in
                    (DEFAULT_AGENT_SETTING | {'enabled': True}).items() if key not in ('revision', 'updatedAt')})
                store.db.execute('UPDATE agent_reply_setting SET updated_at=? WHERE plan_id=?', (NOW - 100, plan))
                store.db.execute("INSERT INTO agent_reply_run VALUES(?,?,'completed',?,?,2,0,2,0,2,0,NULL)",
                                 (f'success-{plan}', plan, NOW - 300, NOW - 200))
                store.db.execute("INSERT INTO agent_reply_run VALUES(?,?,'failed',?,?,1,0,0,0,0,0,'model_unavailable')",
                                 (f'failure-{plan}', plan, NOW - 60, NOW - 30))
        spec = importlib.util.spec_from_file_location('operations_home_runtime_cli', ROOT / 'scripts/operations-home.py')
        self.cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.cli)

    def tearDown(self):
        self.tmp.cleanup()

    def sample(self, market='br', **changes):
        name = 'agent-reply-status.json' if market == 'it' else f'agent-reply-status-{market}.json'
        record = {'pid': 101, 'startedAt': NOW - 500, 'checkedAt': NOW - 5, 'state': 'outside_reply_window'} | changes
        (self.root / 'var' / name).write_text(json.dumps(record))

    def observe(self, market='br', alive=True):
        before = {p.name: p.read_bytes() for p in (self.root / 'var').glob('*.json')}
        with CycleStore(self.root / 'var/second-cycle.sqlite', clock=lambda: NOW, readonly=True) as store:
            store.db.execute('PRAGMA query_only=ON')
            with patch('lib.process_liveness.record_alive', return_value=alive):
                value = self.cli._agent_stage(self.root, store, market, self.plans[market], {})
        self.assertEqual(before, {p.name: p.read_bytes() for p in (self.root / 'var').glob('*.json')})
        return value

    def test_recovered_worker_does_not_inherit_failed_batch_in_either_market(self):
        for market in ('it', 'br'):
            with self.subTest(market=market):
                self.sample(market)
                stage = self.observe(market)
                self.assertEqual((stage['state'], stage['runtime']['fresh']), ('outside_reply_window', True))
                self.assertIsNone(stage['stopReason'])
                self.assertEqual((stage['lastRun']['state'], stage['lastRun']['errorCode']), ('failed', 'model_unavailable'))
                self.assertEqual(stage['lastSuccessAt'], NOW - 200)
                self.assertEqual(stage['metric']['scope'], 'last_run')

    def test_stale_missing_or_dead_worker_never_becomes_a_normal_wait(self):
        for sample, alive, reason in (({'checkedAt': NOW - 601, 'startedAt': NOW - 1000}, True, 'status_stale'),
                                      ({}, False, 'worker_not_running')):
            with self.subTest(reason=reason):
                self.sample(**sample)
                stage = self.observe(alive=alive)
                self.assertEqual((stage['state'], stage['runtime']['fresh'], stage['stopReason']), ('unverified', False, reason))
        (self.root / 'var/agent-reply-status-br.json').write_text('{broken')
        stage = self.observe()
        self.assertEqual((stage['state'], stage['stopReason']), ('unverified', 'status_missing'))

    def test_current_failure_is_kept_as_a_current_technical_failure(self):
        self.sample(state='failed', error='provider_failed')
        stage = self.observe()
        self.assertEqual((stage['state'], stage['stopReason']), ('failed', 'provider_failed'))
        self.assertTrue(stage['runtime']['fresh'])

    def test_explicit_off_control_is_current_without_rewriting_history_or_settings(self):
        self.sample(checkedAt=NOW - 1000, startedAt=NOW - 1500, state='failed')
        with CycleStore(self.root / 'var/second-cycle.sqlite', clock=lambda: NOW) as store:
            store.db.execute('UPDATE agent_reply_setting SET enabled=0,updated_at=? WHERE plan_id=?',
                             (NOW - 10, self.plans['br']))
        stage = self.observe(alive=False)
        self.assertEqual((stage['state'], stage['runtime']['fresh'], stage['runtime']['statusSource']), ('disabled', True, 'control'))
        self.assertEqual(stage['lastRun']['state'], 'failed')
        self.assertIsNone(stage['stopReason'])

    def test_live_idle_without_a_batch_keeps_missing_history_and_metric_unknown(self):
        with CycleStore(self.root / 'var/second-cycle.sqlite', clock=lambda: NOW) as store:
            store.db.execute('DELETE FROM agent_reply_run WHERE plan_id=?', (self.plans['br'],))
        self.sample(state='idle')
        stage = self.observe()
        self.assertEqual(stage['state'], 'idle')
        self.assertIsNone(stage['lastRun'])
        self.assertIsNone(stage['metric']['value'])
        self.assertEqual(stage['metric']['availability'], 'not_recorded')


if __name__ == '__main__':
    unittest.main()
