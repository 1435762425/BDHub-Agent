from contextlib import ExitStack
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from lib import first_resources
from lib.schema_migrations import apply_database
from lib.second_cycle import CycleError, CycleStore
from lib import workflow_resources as resources


class FirstResourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        with CycleStore(self.root / 'var/second-cycle.sqlite') as store:
            store.plan('bjn-local-research', 'br')
        apply_database(self.root, 'second-cycle')
        self.store = CycleStore(self.root / 'var/second-cycle.sqlite')
        self.stack = ExitStack()
        self.stack.enter_context(patch('lib.first_resources.load_policy', return_value={'platformMaxParallelMarkets': 2}))
        self.stack.enter_context(patch('lib.first_resources.load_config', return_value={'markets': {'br': {'roles': {'supply': 'acc2'}}}}))

    def tearDown(self):
        self.stack.close()
        self.store.close()
        self.temp.cleanup()

    def slots(self):
        return [tuple(row) for row in self.store.db.execute('SELECT resource_key,slot_no FROM workflow_resource_slot ORDER BY resource_key,slot_no')]

    def peer(self, key, account, market, *, resources_override=None):
        return resources.claim_profile_read(self.store, 'refresh_' + key * 32, 'profile-resource-owner-' + key,
                      resources_override or [('platform:global', 2), ('supply:' + account, 1), ('workflow:' + market, 1)])

    def test_exact_shared_keys_and_no_parallel_workflow_stage_or_lock_ledger(self):
        stages = self.store.db.execute('SELECT count(*) FROM workflow_stage_run').fetchone()[0]
        with first_resources.platform_read(self.root, 'first-resource-request-1') as guard:
            self.assertTrue(guard.check())
            self.assertEqual(self.slots(), [('platform:global', 0), ('supply:acc2', 0), ('workflow:br', 0)])
            self.assertEqual(self.store.db.execute('SELECT count(*) FROM workflow_stage_run').fetchone()[0], stages)
        self.assertEqual(self.slots(), [])
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM workflow_stage_claim').fetchone()[0], 0)
        with self.assertRaisesRegex(CycleError, 'scope_closed'):
            guard.check()

    def test_existing_two_market_readers_prevent_first_outreach_becoming_third(self):
        self.peer('a', 'acc8', 'my')
        self.peer('b', 'acc11', 'uk')
        before = self.slots()
        with self.assertRaisesRegex(CycleError, 'workflow_resource_busy'):
            with first_resources.platform_read(self.root, 'first-resource-request-2'):
                self.fail('must not acquire')
        self.assertEqual(self.slots(), before)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM workflow_stage_claim').fetchone()[0], 2)

    def test_account_contention_is_atomic_and_does_not_leak_free_global_slot(self):
        self.peer('a', 'acc2', 'br', resources_override=[('supply:acc2', 1)])
        with self.assertRaisesRegex(CycleError, 'workflow_resource_busy'):
            with first_resources.platform_read(self.root, 'first-resource-request-3'):
                self.fail('must not acquire')
        self.assertEqual(self.slots(), [('supply:acc2', 0)])

    def test_missing_database_or_migration_never_initializes_a_probe_database(self):
        missing = self.root / 'missing'
        with self.assertRaisesRegex(CycleError, 'workflow_resource_migration_required'):
            with first_resources.platform_read(missing, 'first-resource-missing'):
                self.fail('must not acquire')
        self.assertFalse(missing.exists())
        unmigrated = self.root / 'unmigrated'
        with CycleStore(unmigrated / 'var/second-cycle.sqlite') as store:
            tables = {row[0] for row in store.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            with self.assertRaisesRegex(CycleError, 'workflow_resource_migration_required'):
                with first_resources.platform_read(unmigrated, 'first-resource-no-schema'):
                    self.fail('must not acquire')
            self.assertEqual(tables, {row[0] for row in store.db.execute("SELECT name FROM sqlite_master WHERE type='table'")})

    def test_lapsed_but_live_owner_is_not_recovered_and_settlement_uses_owned_fence(self):
        with first_resources.platform_read(self.root, 'first-resource-live') as guard:
            self.store.db.execute('UPDATE workflow_stage_claim SET lease_until=0 WHERE stage_run_id=?', (guard.ticket['stageRunId'],))
            self.assertEqual(resources.recover_expired(self.store, pid_alive=lambda pid: pid == os.getpid()), [])
            self.assertEqual(len(self.slots()), 3)
            with self.assertRaisesRegex(CycleError, 'workflow_resource_busy'):
                self.peer('a', 'acc2', 'br')
        self.assertEqual(self.slots(), [])

    def test_heartbeat_uses_an_independent_thread_owned_sqlite_connection(self):
        event = threading.Event()
        calls = []
        original = resources.heartbeat
        def beat(store, *args, **kwargs):
            calls.append((threading.get_ident(), id(store.db)))
            value = original(store, *args, **kwargs)
            event.set()
            return value
        with patch.object(first_resources, 'HEARTBEAT_SECONDS', .01), patch.object(resources, 'heartbeat', side_effect=beat):
            with first_resources.platform_read(self.root, 'first-resource-heartbeat') as guard:
                self.assertTrue(event.wait(2), 'heartbeat was not delivered')
                self.assertTrue(guard.check())
                self.assertNotEqual(calls[0][0], threading.get_ident())
                self.assertNotEqual(calls[0][1], id(guard.store.db))
                row = self.store.db.execute('SELECT heartbeat_at,created_at FROM workflow_stage_claim WHERE stage_run_id=?', (guard.ticket['stageRunId'],)).fetchone()
                self.assertGreater(row['heartbeat_at'], row['created_at'])
        self.assertEqual(self.slots(), [])

    def test_live_child_keeps_slots_if_parent_leaves_scope_and_recovery_waits_for_death(self):
        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
        try:
            with self.assertRaisesRegex(CycleError, 'first_resource_child_running'):
                with first_resources.platform_read(self.root, 'first-resource-child') as guard:
                    guard.adopt(child.pid)
                    self.assertEqual(self.store.db.execute('SELECT worker_pid FROM workflow_stage_claim WHERE stage_run_id=?', (guard.ticket['stageRunId'],)).fetchone()[0], child.pid)
            self.store.db.execute('UPDATE workflow_stage_claim SET lease_until=0')
            resources.recover_expired(self.store)
            self.assertEqual(len(self.slots()), 3)
        finally:
            child.terminate()
            child.wait(timeout=5)
        resources.recover_expired(self.store)
        self.assertEqual(self.slots(), [])
        with first_resources.platform_read(self.root, 'first-resource-after-child') as replacement:
            self.assertGreater(replacement.ticket['fence'], guard.ticket['fence'])

    def test_exited_child_is_adopted_back_before_normal_release(self):
        child = subprocess.Popen([sys.executable, '-c', 'pass'])
        try:
            with first_resources.platform_read(self.root, 'first-resource-child-done') as guard:
                guard.adopt(child.pid)
                child.wait(timeout=5)
        finally:
            if child.poll() is None:
                child.terminate()
                child.wait(timeout=5)
        self.assertEqual(guard.worker_pid, os.getpid())
        self.assertEqual(self.slots(), [])

    def test_cannot_rebind_to_parent_while_adopted_child_is_still_running(self):
        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
        try:
            with first_resources.platform_read(self.root, 'first-resource-child-rebind') as guard:
                guard.adopt(child.pid)
                with self.assertRaisesRegex(CycleError, 'first_resource_child_running'):
                    guard.adopt(os.getpid())
                child.terminate()
                child.wait(timeout=5)
                guard.adopt(os.getpid())
        finally:
            if child.poll() is None:
                child.terminate()
                child.wait(timeout=5)
        self.assertEqual(self.slots(), [])

    def test_body_failure_releases_only_this_still_owned_claim(self):
        with self.assertRaisesRegex(RuntimeError, 'fixture-failure'):
            with first_resources.platform_read(self.root, 'first-resource-body-error'):
                raise RuntimeError('fixture-failure')
        self.assertEqual(self.slots(), [])

    def test_stale_owner_never_releases_replacement_fence(self):
        replacement = None
        with self.assertRaisesRegex(CycleError, 'workflow_stage_fence_stale'):
            with first_resources.platform_read(self.root, 'first-resource-stale') as guard:
                ticket = guard.ticket
                resources.release(self.store, ticket['stageRunId'], ticket['ownerId'], ticket['fence'])
                replacement = resources.claim_auxiliary(self.store, ticket['stageRunId'].removeprefix('auxiliary:'),
                                  'replacement-resource-owner', [('platform:global', 2)], worker_pid=os.getpid())
                guard.check()
        self.assertTrue(resources.assert_owned(self.store, replacement['stageRunId'], replacement['ownerId'], replacement['fence']))
        self.assertEqual(self.slots(), [('platform:global', 0)])

    def test_auxiliary_claim_does_not_duplicate_or_create_workflow_stages(self):
        before = self.store.db.execute('SELECT count(*) FROM workflow_stage_run').fetchone()[0]
        ticket = resources.claim_auxiliary(self.store, 'first-resource-auxiliary', 'first-auxiliary-owner', [('platform:global', 2)])
        with self.assertRaisesRegex(CycleError, 'workflow_resource_busy'):
            resources.claim_auxiliary(self.store, 'first-resource-auxiliary', 'first-auxiliary-owner', [('platform:global', 2)])
        self.store.db.execute('UPDATE workflow_stage_claim SET lease_until=0')
        resources.recover_expired(self.store, pid_alive=lambda _: False)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM workflow_stage_run').fetchone()[0], before)
        self.assertEqual(self.slots(), [])
        with self.assertRaisesRegex(CycleError, 'workflow_stage_fence_stale'):
            resources.assert_owned(self.store, ticket['stageRunId'], ticket['ownerId'], ticket['fence'])


if __name__ == '__main__':
    unittest.main()
