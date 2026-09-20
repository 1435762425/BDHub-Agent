#!/usr/bin/env python3
"""从发送池到正式发送：先看这一批会发给谁、发什么、谁被跳过。

    python scripts/send-batch.py preview --count 500
    python scripts/send-batch.py preview --count 600 --widen
    python scripts/send-batch.py preview --count 500 --window 09:00-24:00
    python scripts/send-batch.py status
    python scripts/send-batch.py freeze --request-id <uuid> --expected-preview-hash <sha256>
    python scripts/send-batch.py start --batch-id <id> --expected-revision 1 --confirmed
    python scripts/send-batch.py stop --batch-id <id> --expected-revision 2
    python scripts/send-batch.py reconcile --batch-id <id> --delivery-id <id> --expected-revision 2 --confirmed

`preview` **只读**：不落库、不碰平台。它算的就是"如果现在发这一批，会发给谁、发什么商品、为什么
有人被跳过"。`--widen` 是**显式越界探测**：越过本地 24 小时 500 个新联系的保守闸门，去拿平台自己的
上限信号；越界时每条真实回执都要落账，单达人被拒只标该条、不停整批。

真正执行由 `send-batch-worker.py` + `cycle_burst.run_cohort` 负责；旧 `bulk-second-send.py` 已退役，
不能用历史 `cycle_bulk` 授权恢复发送。
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / 'scripts'))
from lib.second_cycle import CycleError  # noqa: E402
from lib.cycle_materials import TEMPLATES  # noqa: E402
from lib.send_batch import (freeze_batch, mark_batch_running, mark_batch_start_failed, preview,  # noqa: E402
                            reconciliation_target, save_and_status, settle_reconciliation,
                            start_batch, status, stop_batch)


def parse_window(raw):
    if not raw:
        return None
    text = str(raw)
    if '-' not in text:
        raise ValueError('invalid_send_window')
    start, end = text.split('-', 1)
    return (start.strip(), end.strip())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['preview', 'status', 'save', 'freeze', 'start', 'stop', 'reconcile'])
    parser.add_argument('--count', type=int, default=500)
    parser.add_argument('--widen', action='store_true', help='显式越过本地 24h 500 新联系闸门')
    parser.add_argument('--window', help='发送窗口，形如 09:00-24:00；不填＝不设窗口')
    parser.add_argument('--template', choices=tuple(TEMPLATES), default='standard', help='发送话术模板')
    parser.add_argument('--json', help='配置 JSON（save 用），如 {"count":500,"widen":true}')
    parser.add_argument('--request-id')
    parser.add_argument('--expected-preview-hash')
    parser.add_argument('--batch-id')
    parser.add_argument('--delivery-id')
    parser.add_argument('--expected-revision', type=int)
    parser.add_argument('--confirmed', action='store_true')
    args = parser.parse_args()
    try:
        window = parse_window(args.window)
        if args.action == 'status':
            print(json.dumps(status(ROOT), ensure_ascii=False))
            return 0
        if args.action == 'save':
            raw = (json.loads(Path(args.json[1:]).read_text(encoding='utf-8'))
                   if args.json and args.json.startswith('@') else json.loads(args.json or '{}'))
            print(json.dumps(save_and_status(ROOT, raw), ensure_ascii=False))
            return 0
        if args.action == 'freeze':
            batch = freeze_batch(ROOT, args.request_id, args.expected_preview_hash)
            print(json.dumps(status(ROOT) | {'batch': batch}, ensure_ascii=False))
            return 0
        if args.action == 'start':
            batch = start_batch(ROOT, args.batch_id, args.expected_revision, confirmed=args.confirmed)
            if batch.get('duplicate') and batch['state'] in ('starting', 'running'):
                print(json.dumps(status(ROOT) | {'batch': batch}, ensure_ascii=False))
                return 0
            log_dir = ROOT / 'var/bulk-second'
            log_dir.mkdir(parents=True, exist_ok=True)
            log = (log_dir / f"{args.batch_id}.worker.log").open('a', encoding='utf-8')
            try:
                process = subprocess.Popen(
                    [sys.executable, str(ROOT / 'scripts/send-batch-worker.py'),
                     '--batch-id', args.batch_id, '--lanes', '2'],
                    cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                    start_new_session=True, close_fds=True)
                batch = mark_batch_running(ROOT, args.batch_id) | {'workerPid': process.pid}
            except BaseException:
                mark_batch_start_failed(ROOT, args.batch_id)
                raise CycleError('send_worker_launch_failed') from None
            finally:
                log.close()
            print(json.dumps(status(ROOT) | {'batch': batch}, ensure_ascii=False))
            return 0
        if args.action == 'stop':
            batch = stop_batch(ROOT, args.batch_id, args.expected_revision)
            print(json.dumps(status(ROOT) | {'batch': batch}, ensure_ascii=False))
            return 0
        if args.action == 'reconcile':
            if args.confirmed is not True:
                raise CycleError('reconciliation_confirmation_required')
            target = reconciliation_target(ROOT, args.batch_id, args.delivery_id, args.expected_revision)
            result = subprocess.run(
                [sys.executable, str(ROOT / 'scripts/cycle-send.py'), 'run',
                 '--delivery-id', target['deliveryId'], '--approved-hash', target['snapshotHash'],
                 '--verify-only'], cwd=ROOT, capture_output=True, text=True, timeout=90)
            try:
                verification = json.loads((result.stdout or '').strip().splitlines()[-1])
            except (IndexError, ValueError):
                raise CycleError('reconciliation_result_unavailable') from None
            if result.returncode or verification.get('verificationOnly') is not True:
                raise CycleError('reconciliation_failed')
            batch = settle_reconciliation(ROOT, args.batch_id, args.delivery_id, args.expected_revision)
            print(json.dumps(status(ROOT) | {'batch': batch, 'reconciliation': verification}, ensure_ascii=False))
            return 0
        state = preview(ROOT, count=args.count, widen=args.widen, window=window, template=args.template)
        print(json.dumps(state, ensure_ascii=False))
        return 0
    except (CycleError, ValueError) as error:
        print(json.dumps({'error': str(error)}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
