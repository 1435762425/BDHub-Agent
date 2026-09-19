#!/usr/bin/env python3
"""从发送池到正式发送：先看这一批会发给谁、发什么、谁被跳过。

    python scripts/send-batch.py preview --count 500
    python scripts/send-batch.py preview --count 600 --widen
    python scripts/send-batch.py preview --count 500 --window 09:00-24:00
    python scripts/send-batch.py status

`preview` **只读**：不落库、不碰平台。它算的就是"如果现在发这一批，会发给谁、发什么商品、为什么
有人被跳过"。`--widen` 是**显式越界探测**：越过本地 24 小时 500 个新联系的保守闸门，去拿平台自己的
上限信号；越界时每条真实回执都要落账，单达人被拒只标该条、不停整批。

真正执行仍由既有驱动器负责（`bulk-second-send.py` + `cycle_burst.run_cohort`），这里不重写发送。
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / 'scripts'))
from lib.send_batch import capacity, preview, save_and_status, status  # noqa: E402


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
    parser.add_argument('action', choices=['preview', 'status', 'save'])
    parser.add_argument('--count', type=int, default=500)
    parser.add_argument('--widen', action='store_true', help='显式越过本地 24h 500 新联系闸门')
    parser.add_argument('--window', help='发送窗口，形如 09:00-24:00；不填＝不设窗口')
    parser.add_argument('--json', help='配置 JSON（save 用），如 {"count":500,"widen":true}')
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
        state = preview(ROOT, count=args.count, widen=args.widen, window=window)
        print(json.dumps(state, ensure_ascii=False))
        return 0
    except ValueError as error:
        print(json.dumps({'error': str(error)}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
