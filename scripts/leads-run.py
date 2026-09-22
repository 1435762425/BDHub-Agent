#!/usr/bin/env python3
"""Run one batch of the lead query queue against Kalodata.

    python scripts/leads-run.py [--limit N] [--max-pages 2]

Takes the top of the queue computed by ``lib.leads_queue`` and asks Kalodata for each product's
creator list. Everything that touches the platform is reused, not reimplemented: the transport and
its endpoint/scope validation come from ``batch_source_runtime.kalodata_provider``, the response
parsing and the daily-quota detection come from ``cycle_kalodata.parse_page``, and the leads land in
the same ``source_edge`` store the rest of the system already reads.

Discipline kept from the existing reader: one page per request, the page receipt is persisted before
anything is imported, the batch runs under the provider's bounded shared read lock, and it stops
immediately on quota exhaustion or an authentication failure, leaving the rest of the queue untouched
for next time.

This is a read. It never sends a message and never writes platform business data.
"""
import argparse
import json
import os
import sqlite3
import sys
import time
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEGACY = ROOT.parent / '01-BDSystem-V2'
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / 'scripts'))

from lib.cycle_kalodata import PATH, parse_page  # noqa: E402
from lib.lead_selection import publish_query  # noqa: E402
from lib.leads_queue import Ledger, config_path, load, plan  # noqa: E402
from lib.second_cycle import CycleError, digest, encoded  # noqa: E402

BROWSER_LOCK = LEGACY / 'data/research/kalodata/.browser.lock'
PAYLOAD_KEYS = ('id', 'handle', 'nickname', 'sale', 'revenue', 'video_revenue', 'live_revenue', 'followers')
STOP_CODES = ('kalodata_daily_quota_exhausted', 'kalodata_auth_required')


def state_path(root,market='it'):
    return Path(root) / ('var/leads-run.json' if market=='it' else f'var/leads-run-{market}.json')


def write_state(root, payload,market='it'):
    path = state_path(root,market)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)


def plan_id(root,market='it'):
    """The market research plan the leads belong to, so the rest of the system can read them."""
    db = Path(root) / 'var/second-cycle.sqlite'
    if not db.exists():
        raise CycleError('plan_store_missing')
    with closing(sqlite3.connect(db.resolve().as_uri() + '?mode=ro', uri=True)) as conn:
        conn.execute('BEGIN')
        row = conn.execute("SELECT id FROM plan WHERE institution='bjn-local-research' AND market=?",(market,)).fetchone()
    if not row:
        raise CycleError('plan_missing')
    return row[0]


def offers(root, pids,market='it'):
    """Campaign binding per product, used only as a stable offer key on the recorded lead."""
    db = Path(root) / 'var/catalog-links.sqlite'
    if not db.exists():
        return {}
    marks = ','.join('?' * len(pids))
    with closing(sqlite3.connect(db.resolve().as_uri() + '?mode=ro', uri=True)) as conn:
        conn.execute('BEGIN')
        rows=conn.execute(f"SELECT pid,campaign_id,catalog_source FROM catalog_current_binding WHERE market=? AND state='active' "
            f"AND pid IN ({marks}) ORDER BY CASE catalog_source WHEN 'selected' THEN 0 ELSE 1 END", [market,*pids])
        result={}
        for pid,campaign,source in rows:result.setdefault(str(pid),str(campaign))
        return result


def store_edges(root, edges,market='it'):
    """Insert leads into the same store the identity and outreach stages already read."""
    if not edges:
        return 0
    db = Path(root) / 'var/second-cycle.sqlite'
    target = plan_id(root,market)
    with closing(sqlite3.connect(db, timeout=30)) as conn:
        conn.execute('BEGIN IMMEDIATE')
        added = 0
        for edge in edges:
            added += conn.execute('INSERT OR IGNORE INTO source_edge(plan_id,source_id,payload) '
                                  'VALUES(?,?,?)', (target, edge['sourceId'], encoded(edge))).rowcount
        conn.commit()
    return added


def claim_for(root, pid, cursor, offer_key, window,market='it'):
    return {'id': 'leads-' + digest(['leads-queue',market,pid,window[0],window[1]])[:28], 'pid': pid,
            'offer_key': offer_key, 'cursor': cursor,
            'window_start': window[0], 'window_end': window[1]}


def run(root, *, market='it', limit=None, max_pages=2, provider_factory=None, clock=time.time, on_progress=None,
        browser_lock=None):
    """Execute the top of the queue. Returns a report; never raises for a platform refusal."""
    root = Path(root)
    config = load(root)
    planned = plan(root,market=market, config=config, batch_size=limit)
    items = planned['items']
    end = date.today() - timedelta(days=2)
    window = (str(end - timedelta(days=13)), str(end))
    try:
        from lib.market_registry import market as market_row
        currency=market_row(root,market)['currency']
    except (FileNotFoundError,ValueError):currency='EUR' if market=='it' else None
    region='GB' if market=='uk' else market.upper()
    report = {'startedAt': clock(), 'batchSize': planned['batchSize'], 'dueQueue': planned['dueQueue'],
              'targets': len(items), 'done': 0, 'leads': 0, 'rawPositive': 0, 'networkRequests': 0, 'stopped': None,
              'errors': [], 'skipped': {}, 'platformWrites': 0,'market':market,'kalodataRegion':region,'currency':currency}
    if not items:
        report['stopped'] = 'queue_empty'
        return report
    if provider_factory is None:
        from lib.batch_source_runtime import kalodata_provider
        provider_factory = lambda value: kalodata_provider(value,market)
    ledger = Ledger(root,market)
    binds = offers(root, [row['pid'] for row in items],market)
    lock_path = Path(browser_lock) if browser_lock else BROWSER_LOCK
    if not lock_path.exists():
        report['stopped'] = 'browser_lock_missing'
        ledger.close()
        return report
    try:
        # Do NOT take the browser lock here. ``kalodata_provider`` already takes the exact same
        # shared read lock used by the new-project provider, and flock is owned by the open file
        # description -- a second fd in the *same* process is refused too. Taking it here as well
        # made every real batch stop instantly with ``browser_lock_busy`` while the tests passed,
        # because the test factory never locks. One owner, one lock.
        with provider_factory(root) as provider:
            for row in items:
                pid = row['pid']
                offer_key = 'campaign:' + binds.get(pid, 'unknown')
                cursor, done = '', False
                try:
                    gathered=[];fingerprints=[]
                    while not done:
                        saved = ledger.db.execute('SELECT payload FROM leads_page WHERE pid=? AND cursor=?',
                                                  (pid, cursor)).fetchone()
                        if saved:
                            receipt = json.loads(saved[0])
                        else:
                            claim = claim_for(root, pid, cursor, offer_key, window,market)
                            payload = {'startDate': window[0], 'endDate': window[1], 'authority': True,
                                       'pageSize': 50, 'pageNo': int(cursor or '1'),
                                       'sort': [{'field': 'revenue', 'type': 'DESC'}], 'id': pid}
                            report['networkRequests'] += 1
                            body = provider.request(PATH, payload)
                            receipt = parse_page(body, claim, clock(), max_pages=max_pages,market=market,currency=currency)
                            # The receipt is durable before anything is imported.
                            with ledger.db:
                                ledger.db.execute('INSERT OR REPLACE INTO leads_page VALUES(?,?,?)',
                                                  (pid, cursor, encoded(receipt)))
                        gathered.extend(receipt['edges']);fingerprints.append(receipt['rowsFingerprint'])
                        for reason, count in (receipt.get('skipped') or {}).items():
                            report['skipped'][reason] = report['skipped'].get(reason, 0) + count
                        done = receipt['done']
                        cursor = receipt['nextCursor']
                    target=plan_id(root,market);query=claim_for(root,pid,'',offer_key,window,market)['id']
                    published=publish_query(root,plan_id=target,query_id=query,pid=pid,edges=gathered,
                                            receipt_fingerprints=fingerprints,
                                            policy_version=config['version'],limit=config['leadsPerPid'],
                                            window_start=window[0],window_end=window[1],at=clock())
                    report['rawPositive']+=published['rawPositive'];report['leads']+=published['selected']
                    ledger.succeeded(pid, window_end=window[1], leads=published['selected'],note='队列查询', at=clock())
                    report['done'] += 1
                except CycleError as error:
                    code = str(error)
                    # No queried_at on a failure: the product keeps its place at the head of
                    # the queue instead of being locked out for a whole refresh cycle.
                    ledger.failed(pid, code, at=clock())
                    if code in STOP_CODES:
                        report['stopped'] = code
                        break
                    report['errors'].append({'pid': pid, 'code': code})
                except Exception as error:  # noqa: BLE001 - one bad product must not stop the batch
                    import traceback
                    diagnostic=root/('var/leads-run-errors.log' if market=='it' else f'var/leads-run-errors-{market}.log')
                    with diagnostic.open('a',encoding='utf-8') as stream:stream.write(f"{time.time()} {type(error).__name__}\n{traceback.format_exc()}\n")
                    diagnostic.chmod(0o600)
                    ledger.failed(pid, type(error).__name__, at=clock())
                    report['errors'].append({'pid': pid, 'code': type(error).__name__})
                if on_progress:
                    on_progress(report)
    except BlockingIOError:
        # The provider's flock refused: another Kalodata reader owns the browser right now. Nothing
        # was requested for this batch, so nothing was spent and the queue is untouched.
        report['stopped'] = 'browser_lock_busy'
    except FileNotFoundError:
        report['stopped'] = 'browser_lock_missing'
    finally:
        ledger.close()
    report['finishedAt'] = clock()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--market',default='it')
    parser.add_argument('--limit', type=int, help='override the configured batch ceiling')
    parser.add_argument('--max-pages', type=int, default=2)
    args = parser.parse_args()
    try:
        from lib.market_registry import enabled_market_keys
        if args.market not in enabled_market_keys(ROOT):
            raise ValueError('leads_run_market_invalid')
        if args.limit is not None and not 1 <= args.limit <= 5000:
            raise ValueError('leads_run_limit_invalid')
        if not 1 <= args.max_pages <= 20:
            raise ValueError('leads_run_pages_invalid')

        def progress(report):
            write_state(ROOT, report | {'running': True},args.market)

        progress({'startedAt': time.time(), 'targets': 0, 'done': 0, 'leads': 0,
                  'networkRequests': 0, 'stopped': None, 'errors': [], 'platformWrites': 0,
                  'batchSize': args.limit or load(ROOT)['batchSize'], 'dueQueue': 0})
        report = run(ROOT,market=args.market, limit=args.limit, max_pages=args.max_pages, on_progress=progress)
        write_state(ROOT, report | {'running': False},args.market)
        print(json.dumps(report, ensure_ascii=False))
        return 0
    except (CycleError, ValueError) as error:
        write_state(ROOT, {'running': False, 'stopped': str(error), 'done': 0, 'leads': 0},args.market)
        print(json.dumps({'error': str(error)}, ensure_ascii=False))
        return 2
    except Exception as error:  # noqa: BLE001 - the page must never be left claiming "running"
        write_state(ROOT, {'running': False, 'stopped': type(error).__name__, 'done': 0, 'leads': 0},args.market)
        print(json.dumps({'error': type(error).__name__}, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
