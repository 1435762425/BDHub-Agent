#!/usr/bin/env python3
"""读非全托（campaign）渠道的 TapLink 卡片清单：逐活动扫列表 + 读成员。

    python scripts/campaign-inventory.py scan [--campaigns id,id] [--max-lists 40]

**只读**：不建、不删、不改任何平台数据。差别在于渠道参数——全托读的是
``source=2, campaign_id=0``（账号级），非全托读的是 **``source=1, campaign_id=<活动>``**，
这正是旧版 ``commerce_transport`` 的做法（``source: 2 if route=='selected' else 1``）。

读到的卡片进 ``var/catalog-links.sqlite`` 的 ``catalog_tap_list`` / ``catalog_tap_member``
（带 ``source='1'``），后续复判与建链都复用同一份清单，不重复扫。
"""
import argparse
import json
import sys
import time
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / 'scripts'))

from lib.catalog_prepare import READ_EXTRA, TaplinkInventory, read_members, scan_lists  # noqa: E402
from lib.campaign_screen import snapshot_for  # noqa: E402
from lib.global_source_transport import opportunity_reader  # noqa: E402
from lib.second_cycle import digest  # noqa: E402

CAMPAIGN_SOURCE = '1'


def joined_campaigns(root, explicit=None):
    """Joined campaign ids, or the whole snapshot when nothing has been joined yet."""
    if explicit:
        return [str(c) for c in explicit]
    db = Path(root) / 'var/campaign-join.sqlite'
    joined = []
    if db.exists():
        with closing(__import__('sqlite3').connect(db.resolve().as_uri() + '?mode=ro', uri=True)) as conn:
            conn.execute('BEGIN')
            joined = [row[0] for row in conn.execute(
                "SELECT campaign_id FROM campaign_join_item WHERE state='joined' ORDER BY campaign_id")]
    if joined:
        return joined
    _, offers = snapshot_for(root, 'campaign')
    return sorted({str(o.get('campaignId')) for o in offers if o.get('campaignId')})


def scan(root=None, *, campaigns=None, max_lists=40, max_pages=50, clock=time.time):
    root = Path(root or ROOT)
    report = {'platformWrites': 0}
    inventory = TaplinkInventory(root)
    result = {'campaigns': [], 'lists': 0, 'members': 0, 'pids': 0, 'platformWrites': 0}
    try:
        with opportunity_reader(report, extra_read_endpoints=READ_EXTRA, wait_seconds=60,
                                account_name='acc9') as transport:
            def read(path, extra):
                outcome = transport._xhr(method='GET', path=path, params=transport._params() | extra,
                                         payload=None, write=False)
                return transport.require_read(outcome), digest(outcome.payload)

            for campaign_id in campaigns:
                entry = {'campaignId': campaign_id, 'listTotal': 0, 'lists': 0,
                         'members': 0, 'pids': 0, 'error': None}
                try:
                    total, rows = scan_lists(read, source=CAMPAIGN_SOURCE, campaign_id=campaign_id,
                                             max_pages=max_pages)
                    entry['listTotal'] = total
                    for row in rows:
                        inventory.save_list(row, source=CAMPAIGN_SOURCE, campaign_id=campaign_id, now=clock())
                    members = 0
                    pids = set()
                    for row in rows:
                        fetched = read_members(read, row['list_id'], source=CAMPAIGN_SOURCE)
                        inventory.save_members(row['list_id'], row.get('name'), fetched, now=clock())
                        members += len(fetched)
                        pids |= {str(m.get('product_id')) for m in fetched if m.get('product_id')}
                    entry.update(lists=len(rows), members=members, pids=len(pids))
                    result['lists'] += len(rows)
                    result['members'] += members
                except Exception as error:                     # noqa: BLE001 - one campaign must not stop the rest
                    entry['error'] = str(error) if isinstance(error, ValueError) else type(error).__name__
                result['campaigns'].append(entry)
                if result['lists'] >= max_lists:
                    break
        # pids 必须按渠道统计：同一张表里还有全托（source='2'）的卡，读全表会把全托的商品
        # 冒充成本次 campaign 扫描的结果（实测差 1,754 vs 10）。
        result['accountPids'] = inventory.db.execute(
            'SELECT count(DISTINCT pid) FROM catalog_tap_member').fetchone()[0]
        result['pids'] = inventory.db.execute(
            "SELECT count(DISTINCT m.pid) FROM catalog_tap_member m"
            " JOIN catalog_tap_list l ON l.list_id=m.list_id WHERE l.source='1'").fetchone()[0]
        result['platformWrites'] = report.get('platformWrites', 0)
        return result
    finally:
        inventory.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['scan'])
    parser.add_argument('--campaigns', help='逗号分隔；默认用已加入的活动')
    parser.add_argument('--max-lists', type=int, default=40)
    args = parser.parse_args()
    explicit = [c.strip() for c in (args.campaigns or '').split(',') if c.strip()] or None
    if not 1 <= args.max_lists <= 2000:
        print(json.dumps({'error': 'campaign_inventory_limit_invalid'}, ensure_ascii=False))
        return 2
    targets = joined_campaigns(ROOT, explicit)
    print(json.dumps({'campaigns': len(targets)}, ensure_ascii=False), file=sys.stderr)
    print(json.dumps(scan(ROOT, campaigns=targets, max_lists=args.max_lists), ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
