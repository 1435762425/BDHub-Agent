#!/usr/bin/env python3
"""Run the automated-operations scheduler. All switches remain persisted and default off."""
import argparse
import json
import signal
import sys
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1];sys.dont_write_bytecode=True;sys.path.insert(0,str(ROOT/'scripts'))
from lib.operations_scheduler import stop_path,tick  # noqa:E402

STOP=False
def stop(*_):
    global STOP;STOP=True

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--worker',action='store_true');parser.add_argument('--once',action='store_true');args=parser.parse_args()
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    while not STOP and not stop_path(ROOT).exists():
        state=tick(ROOT,refill=args.worker and not args.once,stopped=lambda:STOP);print(json.dumps(state,ensure_ascii=False),flush=True)
        if args.once or not args.worker:break
        for _ in range(10):
            if STOP or stop_path(ROOT).exists():break
            time.sleep(1)
    return 0

if __name__=='__main__':raise SystemExit(main())
