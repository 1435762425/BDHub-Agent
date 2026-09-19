#!/usr/bin/env python3
"""收信监控与按天统计：这一页要读的全部数字，一次读完。

    python scripts/inbox-monitor.py status [--days 14]
    python scripts/inbox-monitor.py detail --date 2026-09-15 [--offset 0 --limit 50]
    python scripts/inbox-monitor.py save --json '{"limit":6,"interval":60}'

``status`` 只读，给"监控与回复"卡片用：

* ``job``      —— 监控作业现在在不在跑、为什么这一轮没读到东西（`live_guard_busy` 最常见：
  补身份/发送正占着同一把 ACC6 live 锁，它在退避重试，不是坏了）。
* ``today``    —— 北京时间的今天：发出多少、触达多少达人、收到多少回复、多少加橱窗。
* ``openCases``—— 现在还有几件待人工。
* ``days``     —— 最近若干天的同一份口径，日历页直接用。

监控进程本身仍是 ``scripts/poll-cycle-inbox.py``（只读，不发送）；这里不重复实现任何轮询，
只把它的状态和已有的账本一起读出来。
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / 'scripts'))
from lib.cycle_stats import daily, day_detail  # noqa: E402
from lib.job_run import load_config, save_config, status as job_status  # noqa: E402


def status(root, days=14):
    job = job_status(root, 'inbox')['inbox']
    stats = daily(root, count=days)
    return {'available': True,
            'config': job['config'],
            'configInvalid': job['configInvalid'],
            'run': job['run'],
            'today': stats['days'][-1] if stats['days'] else None,
            'openCases': stats['openCases'],
            'timezone': stats['timezone'],
            'totals': stats['totals'],
            'days': stats['days']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['status', 'detail', 'save'])
    parser.add_argument('--json', help='config JSON, inline or @path')
    parser.add_argument('--days', type=int, default=14)
    parser.add_argument('--date')
    parser.add_argument('--offset', type=int, default=0)
    parser.add_argument('--limit', type=int, default=50)
    args = parser.parse_args()
    try:
        if args.action == 'status':
            print(json.dumps(status(ROOT, args.days), ensure_ascii=False))
            return 0
        if args.action == 'detail':
            print(json.dumps(day_detail(ROOT, args.date, offset=args.offset, limit=args.limit),
                             ensure_ascii=False))
            return 0
        raw = {}
        if args.json:
            raw = (json.loads(Path(args.json[1:]).read_text(encoding='utf-8'))
                   if args.json.startswith('@') else json.loads(args.json))
        saved = save_config(ROOT, 'inbox', {**load_config(ROOT, 'inbox'), **raw})
        print(json.dumps(status(ROOT) | {'config': saved, 'saved': True}, ensure_ascii=False))
        return 0
    except ValueError as error:
        print(json.dumps({'error': str(error)}, ensure_ascii=False))
        return 2
    except json.JSONDecodeError:
        print(json.dumps({'error': 'inbox_monitor_json_invalid'}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
