#!/usr/bin/env python3
"""Inspect or explicitly backfill one indexed conversation; no send/model calls."""
import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
from pathlib import Path
import signal
import sqlite3
import sys
import time
from types import SimpleNamespace

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from lib.inbox_history import HistoryBackfill
from lib.second_cycle import CycleError, CycleStore

STOP = False


def stop(*_):
    global STOP
    STOP = True


@contextmanager
def open_store(path, *, readonly):
    # Never create/init schema here. Apply the explicit second_cycle migration first.
    db = sqlite3.connect(path.resolve().as_uri() + ('?mode=ro' if readonly else '?mode=rw'),
                         uri=True, timeout=10, isolation_level=None)
    db.row_factory = sqlite3.Row
    store = SimpleNamespace(db=db, clock=time.time)
    store.tx = lambda: CycleStore.tx(store)
    try:
        yield store
    finally:
        db.close()


@contextmanager
def read_session(market, report, stopped):
    if market == 'it':
        from lib.second_live_runtime import _authenticated
        from lib.italy_im_session import ItalyImReadSession
        with _authenticated(report, stopped=stopped, read_only=True) as (_, _, _, auth, _, available):
            with ItalyImReadSession(auth, report, maintenance_due=available, stopped=stopped) as session:
                yield session
    else:
        from lib.market_im_runtime import authenticated
        with authenticated(ROOT, market, report, read_only=True, stopped=stopped) as runtime:
            yield runtime['session']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--market', required=True, choices=('it', 'br', 'my', 'uk'))
    parser.add_argument('--cid', required=True)
    parser.add_argument('--oec', required=True)
    parser.add_argument('--read', action='store_true', help='Perform bounded platform reads and persist historical messages; default is local status only.')
    parser.add_argument('--max-pages', type=int, default=5)
    parser.add_argument('--page-size', type=int, default=20)
    args = parser.parse_args()
    if not 1 <= args.max_pages <= 100 or not 1 <= args.page_size <= 50:
        parser.error('invalid page budget')
    for value in (args.cid, args.oec):
        if not value.isascii() or not value.isdigit() or len(value) > 19 or not 0 < int(value) <= (1 << 63) - 1:
            parser.error('invalid conversation identity')
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    report = dict(platformWrites=0, realSends=0, automaticReplies=0)
    try:
        with open_store(ROOT / 'var/second-cycle.sqlite', readonly=not args.read) as store:
            runner = HistoryBackfill(store)
            row = store.db.execute("SELECT id FROM plan WHERE market=? AND institution='bjn-local-research'", (args.market,)).fetchone()
            if not row:
                raise CycleError('plan_missing')
            plan = row[0]
            runner._binding(plan, args.cid, args.oec)
            if not args.read:
                report.update(state='status', checkpoint=runner.status(plan, args.cid))
            else:
                lock_key = hashlib.sha256(f'{plan}:{args.cid}'.encode()).hexdigest()[:24]
                lock_path = ROOT / 'var' / f'inbox-history-{lock_key}.lock'
                with lock_path.open('a') as lock:
                    try:
                        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError:
                        raise CycleError('history_busy') from None
                    pause_path = ROOT / 'var' / ('cycle-inbox.pause' if args.market == 'it' else f'market-inbox-{args.market}.pause')
                    def stopped():
                        return STOP or pause_path.exists()
                    if stopped():
                        report.update(state='stopped', pagesRead=0)
                    else:
                        with read_session(args.market, report, stopped) as session:
                            report.update(runner.run(session, plan, args.cid, args.oec,
                                                     max_pages=args.max_pages, page_size=args.page_size, stopped=stopped))
        # Session instrumentation contains no message text; output only useful coverage.
        print(json.dumps({key: value for key, value in report.items() if key not in ('imReads',)}, ensure_ascii=False))
        return 0 if report.get('state') in ('status', 'complete', 'partial', 'stopped') else 1
    except Exception as error:
        print(json.dumps(dict(state='error', errorCode=getattr(error, 'code', None) or
                              (str(error) if isinstance(error, CycleError) else type(error).__name__),
                              platformWrites=0, realSends=0, automaticReplies=0)))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
