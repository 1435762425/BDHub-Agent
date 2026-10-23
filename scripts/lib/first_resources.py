"""First-outreach reads share the existing global, account and market slots.

No business authorization or separate lock ledger is created here. A child PID
can own the original fenced claim while its supervising process waits for it.
"""
from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import sqlite3
import threading
import time
import uuid

from lib.market_accounts import load_config
from lib.operations_policy import load_policy
from lib.second_cycle import CycleError, digest
from lib import workflow_resources as resources

HEARTBEAT_SECONDS = 30
LEASE_SECONDS = 120


class _Store:
    """Open only an existing database, without initializing any schema."""
    def __init__(self, path):
        self.clock = time.time
        self.db = sqlite3.connect(path.resolve().as_uri() + '?mode=rw', uri=True,
                                  timeout=10, isolation_level=None)
        self.db.row_factory = sqlite3.Row

    @contextmanager
    def tx(self):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            yield
            self.db.execute('COMMIT')
        except BaseException:
            self.db.execute('ROLLBACK')
            raise

    def close(self):
        self.db.close()


class PlatformRead:
    def __init__(self, path, store, ticket):
        self.path = path
        self.store = store
        self.ticket = ticket
        self.parent_pid = os.getpid()
        self.worker_pid = self.parent_pid
        self._stop = threading.Event()
        self._errors = []
        self._closed = False
        self._thread = threading.Thread(target=self._heartbeat, name='first-platform-heartbeat', daemon=True)

    def _heartbeat(self):
        # This connection is constructed and used wholly within this thread.
        store = None
        try:
            store = _Store(self.path)
            while not self._stop.wait(HEARTBEAT_SECONDS):
                resources.heartbeat(store, self.ticket['stageRunId'], self.ticket['ownerId'],
                                    self.ticket['fence'], lease_seconds=LEASE_SECONDS)
        except Exception as error:
            self._errors.append(error)
        finally:
            if store is not None:
                store.close()

    def check(self):
        """Before a new network call, require this exact still-live lease."""
        if self._closed:
            raise CycleError('first_resource_scope_closed')
        if self._errors:
            error = self._errors[0]
            if isinstance(error, CycleError):
                raise error
            raise CycleError('first_resource_heartbeat_failed') from None
        return resources.assert_current(self.store, self.ticket['stageRunId'],
                                        self.ticket['ownerId'], self.ticket['fence'])

    def _child_running(self):
        if self.worker_pid == self.parent_pid:
            return False
        row = self.store.db.execute('SELECT heartbeat_at FROM workflow_stage_claim WHERE stage_run_id=? AND owner_id=? AND fence=?',
                                    (self.ticket['stageRunId'], self.ticket['ownerId'], self.ticket['fence'])).fetchone()
        if not row:
            raise CycleError('workflow_stage_fence_stale')
        return resources._pid_alive(self.worker_pid, row[0])

    def adopt(self, pid):
        """Call immediately after Popen, then adopt the parent after child exit.

        A live adopted child cannot be replaced by another PID, including the
        parent. It retains the slots even if the supervisor exits unexpectedly.
        """
        if self._closed or type(pid) is not int or pid <= 0:
            raise CycleError('first_resource_worker_invalid')
        if pid != self.worker_pid and self._child_running():
            raise CycleError('first_resource_child_running')
        # The heartbeat occurs after the new child started, so liveness recovery
        # does not mistake its later start time for reuse of the old worker PID.
        resources.heartbeat(self.store, self.ticket['stageRunId'], self.ticket['ownerId'],
                            self.ticket['fence'], lease_seconds=LEASE_SECONDS)
        resources.adopt_worker(self.store, self.ticket['stageRunId'], self.ticket['ownerId'],
                               self.ticket['fence'], pid)
        resources.assert_owned(self.store, self.ticket['stageRunId'], self.ticket['ownerId'], self.ticket['fence'])
        self.worker_pid = pid
        return pid

    def _finish(self):
        self._stop.set()
        self._thread.join(timeout=15)
        if self._thread.is_alive():
            raise CycleError('first_resource_heartbeat_stuck')
        if self._child_running():
            # Keep the original claim on the child. Recovery releases it only
            # after that child really dies; lease expiry alone cannot steal it.
            raise CycleError('first_resource_child_running')
        if self.worker_pid != self.parent_pid:
            self.adopt(self.parent_pid)
        resources.assert_owned(self.store, self.ticket['stageRunId'], self.ticket['ownerId'], self.ticket['fence'])
        resources.release(self.store, self.ticket['stageRunId'], self.ticket['ownerId'], self.ticket['fence'])
        self._closed = True
        if self._errors:
            error = self._errors[0]
            if isinstance(error, CycleError):
                raise error
            raise CycleError('first_resource_heartbeat_failed') from None


@contextmanager
def platform_read(root, request_id, *, account_name=None):
    """Reserve BR's existing heavy-platform slots for one bounded read operation.

    ``with platform_read(root, request_id) as grant`` yields ``grant.check()``
    and ``grant.adopt(pid)``. Capacity contention is workflow_resource_busy;
    missing migration is workflow_resource_migration_required. Nothing is
    created when the project database or its resource migration is absent.
    """
    if not isinstance(request_id, str) or not resources.OWNER.fullmatch(request_id):
        raise CycleError('first_resource_request_invalid')
    path = Path(root) / 'var/second-cycle.sqlite'
    if not path.is_file():
        raise CycleError('workflow_resource_migration_required')
    store = _Store(path)
    grant = None
    try:
        resources._required(store)
        capacity = load_policy(root)['platformMaxParallelMarkets']
        pair = load_config(root).get('markets', {}).get('br', {})
        account = account_name or pair.get('roles', {}).get('supply')
        if account_name is not None and account_name not in pair.get('accounts', []):
            raise CycleError('workflow_resource_market_unavailable')
        if not isinstance(account, str) or not account:
            raise CycleError('workflow_resource_market_unavailable')
        job = 'first-platform-read-' + digest(request_id)[:32]
        owner = 'first-reader-' + uuid.uuid4().hex
        ticket = resources.claim_auxiliary(store, job, owner,
                                           [('platform:global', capacity), ('supply:' + account, 1), ('workflow:br', 1)],
                                           lease_seconds=LEASE_SECONDS, worker_pid=os.getpid())
        grant = PlatformRead(path, store, ticket)
        try:
            grant._thread.start()
        except BaseException:
            resources.release(store, ticket['stageRunId'], ticket['ownerId'], ticket['fence'])
            raise
        try:
            grant.check()
            yield grant
        finally:
            grant._finish()
    finally:
        store.close()
