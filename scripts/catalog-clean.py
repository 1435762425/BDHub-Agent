#!/usr/bin/env python3
"""TapLink health and cleaning: refresh the inventory, classify, and delete under guard.

    python scripts/catalog-clean.py refresh     --report var/...
    python scripts/catalog-clean.py classify    --report var/...
    python scripts/catalog-clean.py status
    python scripts/catalog-clean.py delete --max-deletes 50 --report var/...
    python scripts/catalog-clean.py verify      --report var/...

Design: the health rules and the delete protocol come from the legacy implementation and are
reused verbatim. A card is deleted only when the whole list is platform-confirmed invalid, it is
re-checked immediately before the delete, and the result is confirmed by re-listing. Prior use is
kept as audit evidence but does not keep an already-invalid platform object alive.
"""
import argparse
import json
import sys
import time
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / 'scripts'))
from lib.catalog_clean import CatalogClean, CLEAN_READ_EXTRA, classify_card, decide, used_list_ids  # noqa: E402
from lib.catalog_prepare import TaplinkInventory, list_rows, read_members, scan_lists  # noqa: E402
from lib.cohort_find import SharedPacer  # noqa: E402
from lib.global_source_transport import opportunity_list_deleter_batch, opportunity_reader  # noqa: E402
from lib.second_cycle import digest  # noqa: E402

CAMPAIGN_LIST = '/api/v1/affiliate/partner/campaign/list'
SOURCE = '2'
CAMPAIGN_SCOPE = '0'


def reader_for(on, pace=None):
    def read(path, extra):
        if pace is not None:
            pace()
        r = on._xhr(method='GET', path=path, params=on._params() | extra, payload=None, write=False)
        body = on.require_read(r)
        return body, digest(body)
    return read


def read_campaigns(read):
    """Joined campaigns, so the legacy rule can also catch an ended Campaign."""
    out = {}
    total = None
    for page in range(1, 21):
        body, _ = read(CAMPAIGN_LIST, {'cur_page': page, 'page_size': 100,
                                       'campaign_join_status_category': '1', 'crs_campaign_types': ''})
        data = body.get('data') if isinstance(body, dict) else None
        if not isinstance(data, dict):
            raise ValueError('campaign_list_malformed')
        count = data.get('total_num')
        rows = data.get('campaign')
        if type(count) is not int or count < 0 or not isinstance(rows, list):
            raise ValueError('campaign_list_malformed')
        if total is None:
            total = count
        elif total != count:
            raise ValueError('campaign_list_changed')
        for row in rows:
            out[str(row.get('campaign_id'))] = row
        if len(out) >= total or not rows:
            break
    return out


def step_refresh(report, lanes=1, qps=5):
    """Re-read the account-wide inventory (lists, then members). Read-only."""
    inv = TaplinkInventory(ROOT)
    states = {}
    try:
        with opportunity_reader(report, extra_read_endpoints=CLEAN_READ_EXTRA, wait_seconds=60, account_name='acc9') as transport:
            total, rows = scan_lists(reader_for(transport))
            report['inventoryLists'] = total
            for row in rows:
                inv.save_list(row)
            todo = inv.lists_pending_members()
            report['inventoryPending'] = len(todo)
            from concurrent.futures import ThreadPoolExecutor, as_completed
            lane_transports = []
            try:
                if lanes > 1 and len(todo) > 1:
                    pacer = SharedPacer(qps)
                    lane_transports = [transport] + [transport.fork_lane(pacer.acquire) for _ in range(lanes - 1)]
                    reads = [reader_for(transport, pacer.acquire)] + [reader_for(on, pacer.acquire) for on in lane_transports[1:]]
                    def fetch(index, row):
                        try:
                            return row, read_members(reads[index % lanes], row['list_id'], SOURCE), None
                        except Exception as error:
                            return row, None, error
                    with ThreadPoolExecutor(max_workers=lanes) as executor:
                        for future in as_completed([executor.submit(fetch, i, row) for i, row in enumerate(todo)]):
                            row, members, error = future.result()
                            if error is not None:
                                states['members_unresolved'] = states.get('members_unresolved', 0) + 1
                                continue
                            inv.save_members(row['list_id'], row['name'], members)
                            states['members'] = states.get('members', 0) + 1
                else:
                    base = reader_for(transport)
                    for row in todo:
                        try:
                            members = read_members(base, row['list_id'], SOURCE)
                        except Exception:
                            states['members_unresolved'] = states.get('members_unresolved', 0) + 1
                            continue
                        inv.save_members(row['list_id'], row['name'], members)
                        states['members'] = states.get('members', 0) + 1
            finally:
                for on in lane_transports[1:]:
                    try:
                        on.session.close()
                    except Exception:
                        pass
        report['inventory'] = inv.summary()
        report['refreshStates'] = states
    finally:
        inv.close()
    return states


def step_classify(report):
    """Classify every card and record the decision. Read-only."""
    inv = TaplinkInventory(ROOT)
    clean = CatalogClean(ROOT)
    try:
        clean.open_run()
        campaigns = {}
        if report.get('withCampaigns', True):
            try:
                with opportunity_reader(report, extra_read_endpoints=CLEAN_READ_EXTRA | {(CAMPAIGN_LIST, 'GET')},
                                        wait_seconds=60, account_name='acc9') as transport:
                    campaigns = read_campaigns(reader_for(transport))
                report['campaignsRead'] = len(campaigns)
            except Exception as error:
                report['campaignReadError'] = str(error)[:80]
        run_id = clean.run_id()
        report['cleanRunId'] = run_id
        report['classify'] = clean.plan(run_id, inv, campaigns)
        report['summary'] = clean.summary(run_id)
    finally:
        clean.close()
        inv.close()
    return report


def step_status():
    clean = CatalogClean(ROOT)
    try:
        run_id = clean.run_id()
        row = clean.db.execute('SELECT * FROM catalog_clean_run WHERE id=?', (run_id,)).fetchone()
        if not row:
            return {'available': False, 'executionAllowed': False, 'reason': 'catalog_clean_not_started'}
        items = clean.items(run_id)
        return {'available': True, 'executionAllowed': False, 'runId': run_id,
                'summary': clean.summary(run_id),
                'items': [{k: i[k] for k in ('list_id', 'pid', 'list_name', 'health', 'reason', 'decision', 'used')}
                          for i in items if i['decision'] != 'keep'][:40]}
    finally:
        clean.close()


def step_delete(clean, report, max_deletes=None):
    """Delete frozen, re-checked, still-invalid cards. One platform write per list."""
    run_id = clean.run_id()
    candidates = clean.items(run_id, 'delete_candidate')
    if max_deletes:
        candidates = candidates[:max_deletes]
    report['deleteCandidates'] = len(candidates)
    work = []
    done = {r[0] for r in clean.db.execute("SELECT list_id FROM catalog_clean_intent WHERE run_id=? AND state='verified'", (run_id,))}
    for item in candidates:
        if str(item['list_id']) in done:
            continue  # already verified deleted: never re-check or re-delete it
        try:
            work.append((item, clean.freeze_delete(run_id, item['list_id'])))
        except Exception as error:
            report.setdefault('deleteFrozenErrors', []).append({'list_id': item['list_id'], 'error': str(error)[:60]})
    if not work:
        return {'deleted': [], 'blocked': [], 'kept': []}
    deleted = []
    blocked = []
    kept = []
    inventory = TaplinkInventory(ROOT)
    try:
        with opportunity_list_deleter_batch(report, [w[0]['list_id'] for w in work], wait_seconds=60) as transport:
            read = reader_for(transport)
            for item, intent in work:
                list_id = str(item['list_id'])
                try:
                    # Re-check immediately before the write: a card that just became valid must live.
                    fresh = read_members(read, list_id, SOURCE)
                    verdict = classify_card([{'pid': m.get('product_id'), 'product_status': m.get('product_status') or 0,
                                              'unavailable_type': m.get('unavailable_type'), 'stock': m.get('stock'),
                                              'campaign_id': m.get('campaign_id'),
                                              'is_under_governed': m.get('is_under_governed')} for m in fresh])
                    decision, why = decide(verdict['state'], list_id in used_list_ids(ROOT))
                    if decision != 'delete_candidate':
                        clean.mark(intent['id'], 'unknown', readback={'reason': why, 'health': verdict['state']})
                        kept.append({'list_id': list_id, 'reason': why})
                        continue
                    clean.mark(intent['id'], 'submitted')
                    r = transport._xhr(method='POST', path='/api/v1/affiliate/partner/campaign/product_list/delete',
                                       params=transport._params(), payload={'list_id': list_id}, write=True)
                    report['platformWrites'] = int(report.get('platformWrites') or 0) + 1
                    body = transport.require_read(r)
                    receipt = {'http': r.http_status, 'code': r.code if type(r.code) is int else None,
                               'verification': r.has_turing, 'responseHash': digest(body)}
                    if r.has_turing:
                        clean.mark(intent['id'], 'unknown', receipt=receipt)
                        blocked.append({'list_id': list_id, 'error': 'verification_required_readback_pending'})
                        continue
                    clean.mark(intent['id'], 'receipt_saved', receipt=receipt)
                    deleted.append({'list_id': list_id, 'state': 'submitted'})
                except Exception as error:
                    code = str(error) if isinstance(error, ValueError) else f'{type(error).__name__}:{str(error)[:60]}'
                    try:
                        # No receipt means nothing was submitted: back to the queue, not unknown.
                        current = clean.intent(intent['id'])
                        submitted = current['state'] in ('submitted', 'receipt_saved') or current.get('receipt')
                        clean.mark(intent['id'], 'unknown' if submitted else 'prepared', readback={'error': code})
                    except Exception:
                        pass
                    blocked.append({'list_id': list_id, 'error': code})
            # One re-listing confirms the whole batch: a list that is gone was really deleted.
            try:
                report['verify'] = verify_deleted(clean, read, report)
            except Exception as error:
                report['verifyError'] = str(error)[:80]
    finally:
        inventory.close()
    return {'deleted': deleted, 'blocked': blocked, 'kept': kept}


def verify_deleted(clean, read, report):
    """Confirm deletions by re-listing the account inventory once."""
    run_id = clean.run_id()
    _, rows = scan_lists(read)
    present = {str(r['list_id']) for r in rows}
    states = {}
    for intent in clean.pending_deletes(run_id):
        if intent['state'] not in ('receipt_saved', 'submitted', 'unknown'):
            continue
        if intent['list_id'] not in present:
            clean.mark(intent['id'], 'verified', readback={'reason': '平台回查确认已删除'})
            states['verified'] = states.get('verified', 0) + 1
        else:
            # The POST may have reached the platform. A complete post-batch inventory that still
            # contains the list is a known non-deletion/needs-review result, never permission to
            # submit the DELETE again.
            clean.mark(intent['id'], 'failed_known',
                       readback={'reason': '批后完整回读仍存在，不自动重复删除'})
            states['failed_known'] = states.get('failed_known', 0) + 1
    report['verifyStates'] = states
    return states


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['refresh', 'classify', 'status', 'delete', 'verify'])
    parser.add_argument('--report', type=Path)
    parser.add_argument('--lanes', type=int, default=1, choices=[1, 3, 6, 9])
    parser.add_argument('--qps', type=int, default=5, choices=[3, 5, 8, 12])
    parser.add_argument('--max-deletes', type=int, default=0)
    parser.add_argument('--no-campaigns', action='store_true')
    args = parser.parse_args()

    if args.action == 'status':
        print(json.dumps(step_status(), ensure_ascii=False))
        return 0

    if not args.report:
        parser.error('--report required')
    output = args.report.resolve()
    if not output.is_relative_to(ROOT / 'var') or output.exists():
        parser.error('new report under var required')

    report = {'action': args.action, 'realSends': 0, 'platformWrites': 0, 'startedAt': time.time()}
    if args.action == 'refresh':
        report['refresh'] = step_refresh(report, lanes=args.lanes, qps=args.qps)
    elif args.action == 'classify':
        report['withCampaigns'] = not args.no_campaigns
        step_classify(report)
    elif args.action == 'delete':
        clean = CatalogClean(ROOT)
        try:
            report['delete'] = step_delete(clean, report, max_deletes=args.max_deletes or None)
            report['summary'] = clean.summary(clean.run_id())
        finally:
            clean.close()
    elif args.action == 'verify':
        clean = CatalogClean(ROOT)
        try:
            with opportunity_reader(report, extra_read_endpoints=CLEAN_READ_EXTRA, wait_seconds=60, account_name='acc9') as transport:
                report['verify'] = verify_deleted(clean, reader_for(transport), report)
            report['summary'] = clean.summary(clean.run_id())
        finally:
            clean.close()
    report['elapsedSeconds'] = round(time.time() - report['startedAt'], 2)
    report['state'] = 'completed'
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'action': args.action, 'state': report['state'],
                      'platformWrites': report.get('platformWrites'),
                      'summary': report.get('summary')}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
