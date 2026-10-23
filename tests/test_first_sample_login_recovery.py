"""Sample authentication reports join the original supply-account login chain."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'tests'))
from lib import first_outreach_runtime as runtime, login_recovery, sample_operations as samples
from lib.account_identity import current_generation, publish_generation
from lib.schema_migrations import apply_database
from lib.second_cycle import CycleError, CycleStore
from test_account_identity import config


class FirstSampleLoginRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.path = self.root / 'var/second-cycle.sqlite'
        (self.root / 'config').mkdir(parents=True)
        configured = config('project_owned')
        pair = configured['markets'].pop('it')
        pair.update(accounts=['acc1', 'acc2'], roles={'communications': 'acc1', 'supply': 'acc2'})
        configured['markets']['br'] = pair
        (self.root / 'config/market-accounts.json').write_text(json.dumps(configured))
        with CycleStore(self.path) as store:
            plan = store.plan('bjn-local-research', 'br')
            runtime.ensure_schema(store)
            store.db.execute("INSERT INTO first_outreach_control(plan_id,mode,approvals_enabled) VALUES(?,'stopped',1)", (plan,))
        apply_database(self.root, 'second-cycle')
        with CycleStore(self.path) as store:
            for account, role in [('acc1', 'communications'), ('acc2', 'supply')]:
                publish_generation(store, market='br', account=account, role=role, reason='baseline',
                    identity={'browserRef': 'browser:' + account, 'httpRef': 'http:' + account,
                              'imRef': 'im:' + account, 'institutionFingerprint': 'i' * 64},
                    capabilities={'http': {'state': 'verified'}, 'im': {'state': 'verified'}})
            self.original_generation = current_generation(store, 'br', 'acc2')['generationId']

    def tearDown(self):
        self.temp.cleanup()

    def due(self):
        with CycleStore(self.path) as store:
            store.db.execute('UPDATE first_outreach_runtime SET sample_next_at=0')

    def test_auth_failure_joins_one_original_supply_recovery_chain(self):
        spawned = []
        actual_request = login_recovery.request_recovery
        def request(store, root, market, role, **options):
            return actual_request(store, root, market, role, **options, spawn=spawned.append)
        with patch.object(samples, 'sync', side_effect=CycleError('sample_authentication_required')), \
                patch.object(login_recovery, 'request_recovery', side_effect=request) as recover, \
                patch.object(login_recovery, 'request_refresh') as refresh, patch.object(runtime, '_send') as send:
            self.assertEqual(runtime.worker_tick(self.root)['state'], 'stopped')
            self.due()
            runtime.worker_tick(self.root)
        self.assertEqual(recover.call_count, 2)
        self.assertTrue(all(call.args[2:] == ('br', 'supply') and call.kwargs == {'source': 'first-samples'}
                            for call in recover.call_args_list))
        self.assertEqual(spawned, [self.root])
        refresh.assert_not_called()
        send.assert_not_called()
        with CycleStore(self.path, readonly=True) as store:
            rows = store.db.execute('SELECT market,account,role,operation,expected_generation_id,state FROM account_maintenance_intent').fetchall()
            self.assertEqual(len(rows), 1)
            self.assertEqual(tuple(rows[0]), ('br', 'acc2', 'supply', 'refresh', self.original_generation, 'queued'))
            self.assertEqual(current_generation(store, 'br', 'acc2')['generationId'], self.original_generation)
            cfg = runtime.first.control(store)
            self.assertEqual((cfg['mode'], cfg['approvalsEnabled']), ('stopped', True))

    def test_other_sample_errors_never_request_login_recovery(self):
        codes = ('sample_verification_required', 'sample_rate_limited', 'sample_transport_error',
                 'sample_permission_denied', 'sample_read_failed', 'sample_original_identity_changed')
        with patch.object(login_recovery, 'request_recovery') as recover, patch.object(login_recovery, 'request_refresh') as refresh:
            for code in codes:
                self.due()
                with patch.object(samples, 'sync', side_effect=CycleError(code)):
                    runtime.worker_tick(self.root)
        recover.assert_not_called()
        refresh.assert_not_called()
        with CycleStore(self.path, readonly=True) as store:
            self.assertEqual(store.db.execute('SELECT count(*) FROM account_maintenance_intent').fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
