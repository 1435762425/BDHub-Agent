#!/usr/bin/env python3
"""Consume one user-confirmed frozen send batch.

The worker never discovers or substitutes recipients.  It only advances rows already persisted in
``cycle_bulk_candidate`` and stops at the first unknown result.  Starting this process is the only
step in this file that can reach the platform, so the API launches it only after explicit user
confirmation.
"""
import argparse
import fcntl
import json
import signal
import sqlite3
import sys
import time
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / 'scripts'))

from lib.cycle_burst import run_cohort  # noqa: E402
from lib.send_batch import window_state  # noqa: E402
from lib.second_cycle import CycleError, CycleStore  # noqa: E402

STOP = False
TERMINAL_ITEMS = {'confirmed', 'contacted_inquiry', 'material_stale', 'recipient_limit',
                  'needs_review'}


def stop(*_):
    global STOP
    STOP = True


def frozen_state(database, batch_id):
    with closing(sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True, timeout=5)) as db:
        db.row_factory = sqlite3.Row
        row = db.execute('SELECT state,authorization_json FROM cycle_bulk_freeze WHERE batch_id=?',
                         (batch_id,)).fetchone()
        return dict(row) if row else None


def should_stop(database, batch_id):
    row = frozen_state(database, batch_id)
    return STOP or not row or row['state'] not in ('starting', 'running')


def publish_runtime(store, batch_id, phase):
    store.db.execute('INSERT INTO cycle_bulk_runtime(batch_id,pid,seen,phase) VALUES(?,?,?,?) '
                     'ON CONFLICT(batch_id) DO UPDATE SET pid=excluded.pid,seen=excluded.seen,phase=excluded.phase',
                     (batch_id, __import__('os').getpid(), time.time(), phase))


def settle(store, batch_id):
    counts = dict(store.db.execute('SELECT state,count(*) FROM cycle_bulk_item WHERE batch_id=? GROUP BY state',
                                   (batch_id,)))
    total = sum(counts.values())
    terminal = sum(counts.get(state, 0) for state in TERMINAL_ITEMS)
    if total and terminal == total:
        exceptions = terminal - counts.get('confirmed', 0) - counts.get('contacted_inquiry', 0)
        state = 'completed_with_exceptions' if exceptions else 'completed'
        store.db.execute('UPDATE cycle_bulk SET state=? WHERE id=?', (state, batch_id))
        store.db.execute('UPDATE cycle_bulk_freeze SET state=? WHERE batch_id=?', (state, batch_id))
        publish_runtime(store, batch_id, state)
        return state
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--batch-id', required=True)
    parser.add_argument('--lanes', type=int, choices=(1, 2, 4), default=2)
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    database = ROOT / 'var/second-cycle.sqlite'
    lock_dir = ROOT / 'var/bulk-second'
    lock_dir.mkdir(parents=True, exist_ok=True)
    with (lock_dir / 'worker.lock').open('a') as lock, CycleStore(database) as store:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # The parent publishes ``running`` just after spawning.  Give that tiny hand-off a bounded wait.
        for _ in range(50):
            row = frozen_state(database, args.batch_id)
            if row and row['state'] == 'running':
                break
            if not row or row['state'] not in ('starting', 'running'):
                raise CycleError('frozen_batch_not_running')
            time.sleep(0.1)
        else:
            raise CycleError('frozen_batch_start_timeout')
        while not should_stop(database, args.batch_id):
            row = frozen_state(database, args.batch_id)
            authorization = json.loads(row['authorization_json'])
            window = authorization.get('sendWindow')
            if not window_state(window, time.time())['open']:
                publish_runtime(store, args.batch_id, 'waiting_window')
                if args.once:
                    break
                time.sleep(5)
                continue
            unknown = store.db.execute("SELECT 1 FROM cycle_delivery d JOIN cycle_bulk_item i "
                                       "ON i.delivery_id=d.id WHERE i.batch_id=? AND d.state='unknown'",
                                       (args.batch_id,)).fetchone()
            if unknown:
                store.db.execute("UPDATE cycle_bulk SET state='waiting_reconciliation' WHERE id=?",
                                 (args.batch_id,))
                store.db.execute("UPDATE cycle_bulk_freeze SET state='waiting_reconciliation' WHERE batch_id=?",
                                 (args.batch_id,))
                publish_runtime(store, args.batch_id, 'waiting_reconciliation')
                break
            if settle(store, args.batch_id):
                break
            publish_runtime(store, args.batch_id, 'cohort')
            result = run_cohort(args.batch_id, lanes=args.lanes,
                                stopped=lambda: should_stop(database, args.batch_id))
            if store.db.execute("SELECT 1 FROM sqlite_master WHERE name='outbound_episode'").fetchone():
                from lib.reply_events import backfill
                backfill(store)
            print(json.dumps(result, ensure_ascii=False), flush=True)
            current = frozen_state(database, args.batch_id)
            if not current or current['state'] == 'waiting_reconciliation':
                break
            if args.once:
                break
            time.sleep(1)
        current = frozen_state(database, args.batch_id)
        if current and current['state'] == 'stop_requested':
            store.db.execute("UPDATE cycle_bulk SET state='stopped' WHERE id=?", (args.batch_id,))
            store.db.execute("UPDATE cycle_bulk_freeze SET state='stopped' WHERE batch_id=?",
                             (args.batch_id,))
            publish_runtime(store, args.batch_id, 'stopped')
        settle(store, args.batch_id)
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (CycleError, BlockingIOError) as error:
        print(json.dumps({'error': str(error)}, ensure_ascii=False), flush=True)
        raise SystemExit(2)
