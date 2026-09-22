#!/usr/bin/env python3
"""非全托（Campaign）商品的筛分：看漏斗、或把某次筛分记下来。

    python scripts/campaign-screen.py status [--source campaign|selected]
    python scripts/campaign-screen.py record [--source campaign]

``status`` 只读：拿该渠道最新快照，按已确认规则**重算佣金**再判可推资格，给出
「offer 总数 / 合格 / 不合格原因 / 合格 PID 数 / 需要挑活动的 PID 数」。
``record`` 把这次结果写进 `var/campaign-screen.sqlite`（审计与下游批次号用）。
筛分本身不写平台，也不改任何商品或线索。
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / 'scripts'))
from lib.campaign_screen import SOURCES, build, record, status  # noqa: E402


def _trim(payload, sample):
    """Drop the full offer/pool lists; keep counts, reasons and at most ``sample`` pool rows."""
    if not payload.get('available'):
        return payload
    trimmed = {key: value for key, value in payload.items() if key != 'items'}
    projected = payload.get('pool') or {}
    trimmed['pool'] = {key: value for key, value in projected.items() if key != 'items'}
    rows = [row for row in projected.get('items', []) if row['state'] == 'chosen']
    trimmed['pool']['sample'] = rows[:sample] if sample else []
    return trimmed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['status', 'record'])
    parser.add_argument('--market', default='it')
    parser.add_argument('--source', choices=SOURCES, default='campaign')
    # 页面只要摘要 + 少量样例：3000+ 条 offer 明细不进浏览器。
    parser.add_argument('--sample', type=int, default=0, help='附带前 N 个入池条目（0=不带明细）')
    args = parser.parse_args()
    try:
        if not 0 <= args.sample <= 200:
            raise ValueError('campaign_screen_sample_invalid')
        if args.action == 'status':
            # 走 status()：它除了漏斗还带「上次落库是否还是当前快照」的对照。
            payload = status(ROOT, source=args.source, market=args.market)
            print(json.dumps(_trim(payload, args.sample), ensure_ascii=False))
            return 0
        built = build(ROOT, source=args.source, market=args.market)
        record(ROOT, built, market=args.market)
        print(json.dumps(_trim(built | {'recorded': True}, args.sample), ensure_ascii=False))
        return 0
    except ValueError as error:
        print(json.dumps({'error': str(error)}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
