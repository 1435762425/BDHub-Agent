#!/usr/bin/env python3
"""Resolve the handles waiting for an OECID, one validated round at a time.

    python scripts/identity-batch.py --limit 2000 --cohort-size 20 [--progress var/job-identity-progress.json]

Sending needs the platform identity (the OECID), so every Kalodata lead whose handle has not been
resolved yet is a lead that cannot enter the sending pool. This driver walks that backlog.

It does not reimplement any of the identity work: each round runs the already-validated
``creator-profile-refresh.py worker --once``, which does one profile refresh, one Find cohort through
the published 12 QPS / 9-lane policy, and the freeze/dispatch/reconcile that turns resolved finds
into sending-pool positions. This driver only decides *how many rounds*, publishes the step so the
page can show movement, and stops on the conditions that must never be papered over.

``--limit`` is a ceiling on handles attempted, not a target: when the backlog is smaller the run is
simply shorter, and it is never padded with retries of leads already judged unresolved.
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

from lib.identity_queue import counts  # noqa: E402

WORKER = ROOT / 'scripts/creator-profile-refresh.py'
# 1 is deliberately absent. At ``--cohort-size 1`` the validated worker skips the cohort runner
# entirely and takes its single-item discovery path, which reports no target count -- the progress
# bar would then read "claimed 0" while a handle was in fact judged. 10 and 20 both go through
# ``run_cohort``, which reports exactly what it claimed.
COHORT_SIZES = (10, 20)
# Two rounds in a row that claim nothing means the queue is empty (or stuck); either way the driver
# must end rather than spin forever on a daily budget.
IDLE_ROUNDS = 2
MAX_ROUNDS = 500
ROUND_TIMEOUT = 900
# 这个库被别人持有时整段读不了：实测（2026-09-15）常驻准备作业单次占用 23.9/42.6/56.8/74.3 秒。
# 一轮几十秒的补号批次不该因为别人提交一次就整批停下——那是纯粹的时间问题，不是失败。所以这几种
# 码会重试（不计入轮次、不动队列），但重试必须有上限，连续用完就如实停下并说明原因。
RETRYABLE_CODES = frozenset({'identity_policy_unreadable'})
RETRIES = 3
RETRY_WAIT_SECONDS = 5.0


def python_bin(root=ROOT):
    return Path(root) / '.venv/bin/python'


def publish(path, payload):
    """Replace the running step atomically; the page reads it while the batch works."""
    if path is None:
        return
    temporary = path.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(payload, ensure_ascii=False) + '\n', encoding='utf-8')
    temporary.replace(path)


def one_round(root, cohort_size, *, timeout=ROUND_TIMEOUT):
    """Run exactly one validated round and report what it did."""
    # `--skip-judged`：达人身份一位查一次。判过的 handle（找到或找不到）不再重复问平台——补 OECID
    # 只补真正没判过的达人，不会把同一位达人的其它线索反复重查。
    command = [str(python_bin(root)), str(root / 'scripts/creator-profile-refresh.py'), 'worker',
               '--once', '--cohort-size', str(cohort_size), '--interval', '1', '--skip-judged']
    child = subprocess.run(command, cwd=str(root), capture_output=True, text=True, timeout=timeout)
    lines = [line for line in (child.stdout or '').strip().splitlines() if line.strip()]
    payload = None
    if lines:
        try:
            payload = json.loads(lines[-1])
        except ValueError:
            payload = None
    if child.returncode != 0:
        error = payload.get('error') if isinstance(payload, dict) and isinstance(payload.get('error'), dict) else {}
        # The stderr traceback is what actually explains a nameless crash; the envelope's message is
        # the fallback when the worker could not write one.
        detail = (child.stderr or '').strip()[-1200:] or str(error.get('message') or '') or None
        return {'ok': False, 'code': error.get('code') or f'round_exit_{child.returncode}',
                'detail': detail}
    if not isinstance(payload, dict):
        return {'ok': False, 'code': 'round_output_invalid'}
    cohort = payload.get('cohort') or {}
    # 池位回填（cycleIdentity）失败时这一轮**看起来是成功的**：查到的身份不会写成池位，而
    # `reconcile_cycle` 的兜底 except 会把原因咽掉。所以把它带出来，让页面上留得下这句话。
    cycle = payload.get('cycleIdentity')
    note = None
    if isinstance(cycle, dict) and (cycle.get('status') == 'blocked' or cycle.get('error')):
        note = f"池位回填未成功：{cycle.get('error') or cycle.get('status')}（这一轮的判定结果没有写进发送池）"
    return {'ok': True, 'code': None, 'targets': int(cohort.get('targets') or 0),
            'cohortId': cohort.get('id'), 'seconds': cohort.get('seconds'), 'note': note}


def run(root=None, *, limit=2000, cohort_size=20, progress=None, rounds=None, clock=time.time,
        round_runner=None, measured=None, stop=None, pause=None):
    """Consume the identity backlog. Returns a report; a refusal is a stop reason, never an exception.

    ``stop`` is a file the operator's stop button creates. It is checked **between** rounds, never
    inside one: a round is the unit of platform work, and abandoning it halfway would leave the
    cohort half-settled for no gain. So the button ends the batch; it does not interrupt a query.
    """
    root = Path(root or ROOT)
    if type(limit) is not int or not 1 <= limit <= 200000:
        raise ValueError('identity_limit_invalid')
    if cohort_size not in COHORT_SIZES:
        raise ValueError('identity_cohort_invalid')
    stop_path = Path(stop) if stop else None
    read = measured or (lambda: counts(root))
    step = round_runner or (lambda size: one_round(root, size))
    wait = pause or time.sleep
    started = clock()
    before = read() or {}
    pending_start = int(before.get('pendingCreators') or 0)
    resolved_start = int(before.get('resolvedCreators') or 0)
    unresolved_start = int(before.get('unresolvedCreators') or 0)
    report = {'startedAt': started, 'finishedAt': None, 'limit': limit, 'cohortSize': cohort_size,
              'pendingAtStart': pending_start, 'pending': pending_start, 'claimed': 0, 'rounds': 0,
              'found': 0, 'notFound': 0, 'stopReason': None, 'lastTargets': 0, 'retry': 0,
              'errors': [], 'platformWrites': 0}
    publish(progress, report | {'updatedAt': clock()})
    if pending_start == 0:
        report['stopReason'] = 'nothing_pending'
        report['finishedAt'] = clock()
        publish(progress, report | {'updatedAt': report['finishedAt']})
        return report
    idle = 0
    ceiling = MAX_ROUNDS if rounds is None else rounds
    while report['rounds'] < ceiling:
        if stop_path is not None and stop_path.exists():
            report['stopReason'] = 'stopped_by_operator'
            break
        # 每轮**开始时**先发布一次：一轮要几十秒，只在轮末发布的话进度条会长时间纹丝不动，
        # 操作者看到的就是"点了没反应"。这里给出"第几轮进行中＋这一轮何时开始"。
        report['roundRunning'] = report['rounds'] + 1
        report['roundStartedAt'] = clock()
        publish(progress, report | {'updatedAt': clock()})
        try:
            outcome = step(cohort_size)
        except subprocess.TimeoutExpired:
            outcome = {'ok': False, 'code': 'round_timeout'}
        except Exception as error:  # noqa: BLE001 - a broken round must end the run, not crash it
            outcome = {'ok': False, 'code': type(error).__name__}
        if not outcome.get('ok') and outcome.get('code') in RETRYABLE_CODES and report['retry'] < RETRIES:
            # 别人正持有那个库。这一轮还没碰到平台，重试不会重复任何真实动作——所以它**不计入轮次**，
            # 只是把等待写进进度，让操作者看到"它在等"，而不是"它卡住了"。
            report['retry'] += 1
            report['roundRunning'] = 0
            report['roundStartedAt'] = None
            publish(progress, report | {'updatedAt': clock()})
            wait(RETRY_WAIT_SECONDS)
            continue
        report['rounds'] += 1
        if not outcome.get('ok'):
            report['stopReason'] = outcome.get('code') or 'round_failed'
            if outcome.get('detail'):
                report['errors'].append({'round': report['rounds'], 'detail': outcome['detail']})
            # 失败退出也要把"进行中"清掉：否则进度文件里留着"第 2 轮进行中"，而进程已经停了。
            report['roundRunning'] = 0
            report['roundStartedAt'] = None
            break
        report['retry'] = 0
        report['lastTargets'] = int(outcome.get('targets') or 0)
        if outcome.get('note'):
            report['errors'].append({'round': report['rounds'], 'detail': str(outcome['note'])})
        report['claimed'] += report['lastTargets']
        now = read() or {}
        # The live backlog is reported, not used as the measure of this run's work: another worker
        # keeps importing new Kalodata leads, so the queue can grow while the backfill drains it.
        report['pending'] = int(now.get('pendingCreators') or 0)
        report['found'] = max(0, int(now.get('resolvedCreators') or 0) - resolved_start)
        report['notFound'] = max(0, int(now.get('unresolvedCreators') or 0) - unresolved_start)
        report['roundRunning'] = 0
        report['roundStartedAt'] = None
        publish(progress, report | {'updatedAt': clock()})
        if report['pending'] == 0:
            report['stopReason'] = 'backlog_clear'
            break
        if report['claimed'] >= limit:
            report['stopReason'] = 'limit_reached'
            break
        # A round that claimed nothing did not necessarily fail: the queue may only hold blocked
        # items. Two in a row is the honest end of this run.
        idle = idle + 1 if report['lastTargets'] == 0 else 0
        if idle >= IDLE_ROUNDS:
            report['stopReason'] = 'queue_stalled'
            break
    else:
        report['stopReason'] = 'round_limit'
    report['finishedAt'] = clock()
    publish(progress, report | {'updatedAt': report['finishedAt']})
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--limit', type=int, default=2000)
    parser.add_argument('--cohort-size', type=int, default=20, choices=COHORT_SIZES)
    parser.add_argument('--progress', type=Path)
    parser.add_argument('--stop', type=Path, help='stop file the launcher creates to end the batch')
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    progress = args.progress.resolve() if args.progress else None
    if progress is not None and not progress.is_relative_to(ROOT / 'var'):
        parser.error('progress must live under var')
    if args.stop is not None and not args.stop.resolve().is_relative_to(ROOT / 'var'):
        parser.error('stop must live under var')
    if args.report is not None and not args.report.resolve().is_relative_to(ROOT / 'var'):
        parser.error('report must live under var')
    try:
        report = run(ROOT, limit=args.limit, cohort_size=args.cohort_size, progress=progress,
                     stop=args.stop)
    except ValueError as error:
        print(json.dumps({'error': str(error)}, ensure_ascii=False))
        return 2
    if args.report is not None:
        args.report.resolve().write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n',
                                         encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
