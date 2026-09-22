#!/usr/bin/env python3
"""非全托（Campaign）一键刷新：全量采集 + 按当前门槛重新筛分入池。

    python scripts/campaign-collect.py --report var/xxx.json [--progress var/xxx.json] [--stop var/xxx.stop]

为什么要有这个驱动器：`sync-cycle-catalog.py --source campaign` 是**可续跑的分页状态机**，
每次最多跑 150 个请求就把进度落盘、退出。这里只是反复调它直到这一轮读完，再把进度发布出去，
让页面能看到"读到第几个活动、拿了多少 offer"——不然 60+ 个活动的读取在页面上就是一段黑箱。

**只读平台**（`platformWrites: 0`）：采集是读活动与商品，不建链、不改任何平台数据。
采集完成后会自动按**当前**门槛重新筛分并落库，所以"刷新"之后池子就是新的——
加入新活动 → 刷新 → 池子变大 → 待备链商品增加，这条链才接得上。

**每轮新建一个运行文件**：不续跑上一轮。账号刚加入新活动时活动清单已经变了，
续跑旧状态会在"总数对不上"处报错，而且旧状态里没有新活动的商品。
"""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / 'scripts'))

MAX_REQUESTS_PER_PASS = 150      # sync-cycle-catalog.py 自己的上限


def publish(path, payload):
    """原子发布当前步骤：页面读的是这个文件，半截写入会被读成"没有进度"。"""
    if path is None:
        return
    temporary = path.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(payload, ensure_ascii=False) + '\n', encoding='utf-8')
    temporary.replace(path)


def run_sync(run_file, max_requests, market):
    """一次分页状态机的推进。返回它的 JSON 摘要（脚本自己会落盘进度）。"""
    child = subprocess.run(
        [sys.executable, str(ROOT / 'scripts/sync-cycle-catalog.py'), '--market', market, '--source', 'campaign',
         '--run', str(run_file), '--max-requests', str(max_requests)],
        cwd=ROOT, capture_output=True, text=True, timeout=3000)
    out = (child.stdout or '').strip().splitlines()
    if not out:
        raise ValueError(f'campaign_collect_sync_failed:{child.returncode}:{(child.stderr or "").strip()[-200:]}')
    return json.loads(out[-1])


def record_screen(market):
    """采集完按**当前**门槛重新筛分并落库：不筛分的话池子还是旧的，刷新等于没用。"""
    child = subprocess.run([sys.executable, str(ROOT / 'scripts/campaign-screen.py'), 'record',
                            '--market', market, '--source', 'campaign'],
                           cwd=ROOT, capture_output=True, text=True, timeout=600)
    out = (child.stdout or '').strip().splitlines()
    if not out:
        return {'recorded': False, 'error': f'campaign_screen_failed:{child.returncode}'}
    payload = json.loads(out[-1])
    if isinstance(payload, dict) and payload.get('error'):
        return {'recorded': False, 'error': payload['error']}
    return {'recorded': True, 'offers': payload.get('offers'), 'eligiblePids': payload.get('eligiblePids'),
            'poolCounts': (payload.get('pool') or {}).get('counts')}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--market', default='it')
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--progress', type=Path)
    parser.add_argument('--stop', type=Path, help='停止请求文件；在两次推进之间安全退出')
    parser.add_argument('--max-requests', type=int, default=MAX_REQUESTS_PER_PASS)
    parser.add_argument('--passes', type=int, default=40)
    parser.add_argument('--screen', action='store_true', help='采集完成后按当前门槛重新筛分入池')
    args = parser.parse_args()

    out = args.report.resolve()
    if not out.is_relative_to(ROOT / 'var') or out.exists():
        parser.error('new report under var required')
    if not 1 <= args.max_requests <= MAX_REQUESTS_PER_PASS or not 1 <= args.passes <= 200:
        parser.error('limits out of range')
    progress = args.progress.resolve() if args.progress else None
    if progress is not None and not progress.is_relative_to(ROOT / 'var'):
        parser.error('progress must live under var')
    stop = args.stop.resolve() if args.stop else None

    started_at = time.time()
    stamp = time.strftime('%Y%m%d-%H%M%S')
    run_file = ROOT / f'var/cycle-catalog-{args.market}-campaign-{stamp}.json'

    def note(status, round_no, requests=0, offers=0, error=None):
        publish(progress, {'startedAt': started_at, 'updatedAt': time.time(), 'status': status,
                           'round': round_no, 'rounds': args.passes, 'requests': requests,
                           'offers': offers, 'runFile': str(run_file.relative_to(ROOT)),
                           'error': error})

    steps = []
    status = 'paused'
    error = None
    note('running', 0)
    for index in range(args.passes):
        if stop is not None and stop.exists():
            status = 'stopped'
            break
        try:
            summary = run_sync(run_file, args.max_requests, args.market)
        except Exception as failure:                                  # noqa: BLE001 - 如实上报
            status = 'blocked'
            error = str(failure)[:200]
            steps.append({'round': index + 1, 'error': error})
            note(status, index + 1, error=error)
            break
        steps.append(summary)
        status = str(summary.get('status') or 'paused')
        note(status, index + 1, int(summary.get('requests') or 0), int(summary.get('offers') or 0),
             summary.get('error'))
        if status == 'completed':
            break
        if status in ('blocked',):
            error = summary.get('error')
            break
        if summary.get('error'):
            # 被账号占用/维护挡下：留着断点，下次点"刷新"会新建一轮，不假装成功。
            error = summary.get('error')
            break

    screening = None
    if status == 'completed' and args.screen:
        note('screening', len(steps), _last(steps, 'requests'), _last(steps, 'offers'))
        screening = record_screen(args.market)
        if not screening.get('recorded'):
            error = screening.get('error')

    final = {'status': status, 'market': args.market, 'source': 'campaign', 'platformWrites': 0,
             'runFile': str(run_file.relative_to(ROOT)), 'steps': steps,
             'requests': _last(steps, 'requests'), 'offers': _last(steps, 'offers'),
             'screening': screening, 'error': error,
             'elapsedSeconds': round(time.time() - started_at, 1)}
    out.write_text(json.dumps(final, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    note('done' if status == 'completed' else status, len(steps), _last(steps, 'requests'),
         _last(steps, 'offers'), error)
    print(json.dumps({'status': status, 'offers': _last(steps, 'offers'),
                      'requests': _last(steps, 'requests'), 'screening': screening,
                      'platformWrites': 0, 'error': error}, ensure_ascii=False))
    return 0


def _last(steps, key):
    for step in reversed(steps):
        if isinstance(step.get(key), int):
            return step[key]
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
