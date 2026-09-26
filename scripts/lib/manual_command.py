"""Durable lifecycle of one manual send command, so "not found" is never read as "not sent".

A command is registered (``received``) before any blocking step. Creating its send intent moves it to
``handed_off`` in the same transaction as the intent row; from then on ``service_reply`` is the truth.
Only ``terminal_not_submitted`` releases the page: it is written atomically, and a late original
request that reaches the intent step afterwards is refused by the same compare-and-set.
"""
import time

from lib.second_cycle import CycleError

SCHEMA = '''CREATE TABLE IF NOT EXISTS manual_command(plan_id TEXT NOT NULL,request_id TEXT NOT NULL,cid TEXT NOT NULL,
payload_hash TEXT NOT NULL,state TEXT NOT NULL,created_at REAL NOT NULL,updated_at REAL NOT NULL,PRIMARY KEY(plan_id,request_id));'''
# A command still ``received`` this long after registration has no live executor: the CLI and its
# bridge give up after 120 s, so closing it cannot race a request that may still submit.
STALE_SECONDS = 300
TERMINAL = 'terminal_not_submitted'


def ensure(db):
    db.executescript(SCHEMA)


def claim(store, plan, request_id, cid, payload_hash):
    """Register the command before any blocking work; a closed or conflicting command is refused."""
    ensure(store.db)
    now = store.clock()
    with store.tx():
        row = store.db.execute('SELECT * FROM manual_command WHERE plan_id=? AND request_id=?', (plan, request_id)).fetchone()
        if row is None:
            store.db.execute("INSERT INTO manual_command VALUES(?,?,?,?,'received',?,?)",
                             (plan, request_id, cid, payload_hash, now, now))
            return 'received'
        if row['state'] == TERMINAL:
            raise CycleError('manual_command_closed')
        if row['cid'] != cid or row['payload_hash'] != payload_hash:
            raise CycleError('manual_reply_request_conflict')
        return row['state']


def hand_off(db, plan, request_id):
    """Inside the intent transaction: a closed command may never create its intent."""
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='manual_command'").fetchone():
        return
    row = db.execute('SELECT state FROM manual_command WHERE plan_id=? AND request_id=?', (plan, request_id)).fetchone()
    if row is None:
        return  # Callers that never registered (older paths, tests) keep the service_reply contract.
    if row['state'] == TERMINAL:
        raise CycleError('manual_command_closed')
    db.execute("UPDATE manual_command SET state='handed_off',updated_at=? WHERE plan_id=? AND request_id=? "
               "AND state<>?", (time.time(), plan, request_id, TERMINAL))


def close_unsubmitted(store, plan, request_id, cid, *, require_stale):
    """Close a command that never created an intent; returns the resulting state.

    ``require_stale`` is used by reconciliation: a command registered by a request that may still be
    running stays open until it is older than STALE_SECONDS. An unknown command is tombstoned so that
    its original request, if it ever arrives, is refused.
    """
    ensure(store.db)
    now = store.clock()
    with store.tx():
        row = store.db.execute('SELECT * FROM manual_command WHERE plan_id=? AND request_id=?', (plan, request_id)).fetchone()
        if row is None:
            store.db.execute('INSERT INTO manual_command VALUES(?,?,?,?,?,?,?)',
                             (plan, request_id, cid, 'tombstone', TERMINAL, now, now))
            return TERMINAL
        if row['state'] == 'received' and (not require_stale or now - row['updated_at'] >= STALE_SECONDS):
            store.db.execute("UPDATE manual_command SET state=?,updated_at=? WHERE plan_id=? AND request_id=? AND state='received'",
                             (TERMINAL, now, plan, request_id))
            return TERMINAL
        return row['state']
