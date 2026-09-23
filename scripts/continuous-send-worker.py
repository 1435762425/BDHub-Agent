#!/usr/bin/env python3
"""Consume the current send pool continuously; settings and the Beijing window are authoritative."""
import argparse
import fcntl
import json
import signal
import sys
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1];sys.dont_write_bytecode=True;sys.path.insert(0,str(ROOT/'scripts'))
from lib.continuous_send import control,execute_once,publish_runtime  # noqa:E402
from lib.second_cycle import CycleError,CycleStore  # noqa:E402
from lib.second_live_runtime import SecondLiveRuntimeError  # noqa:E402

STOP=False
def stop(*_):
    global STOP;STOP=True

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--once',action='store_true');args=parser.parse_args()
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    lock_path=ROOT/'var/continuous-send.lock';lock_path.parent.mkdir(parents=True,exist_ok=True)
    with lock_path.open('a') as lock,CycleStore(ROOT/'var/second-cycle.sqlite') as store:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);plan=store.db.execute("SELECT id FROM plan WHERE institution='bjn-local-research' AND market='it'").fetchone()[0]
        while not STOP:
            cfg=control(store,ROOT)
            if cfg['stopRequested'] or not (cfg['runRequested'] or cfg['automaticEnabled']):break
            try:state=execute_once(ROOT,store)
            except SecondLiveRuntimeError as error:
                if error.code!='live_guard_busy':raise
                state=publish_runtime(store,plan,'paused',stop_reason='live_guard_busy')
                print(json.dumps(state,ensure_ascii=False),flush=True)
                if args.once:break
                time.sleep(3)
                continue
            print(json.dumps(state,ensure_ascii=False),flush=True)
            if state['state'] in ('waiting_reconciliation','paused') and state.get('stopReason') not in ('send_pool_empty','recipient_limit'):break
            if args.once:break
            time.sleep(5 if state['state']=='waiting_window' else 1)
        current=control(store,ROOT)
        if STOP or current['stopRequested'] or not (current['runRequested'] or current['automaticEnabled']):
            publish_runtime(store,plan,'stopped',stop_reason='stop_requested' if STOP or current['stopRequested'] else 'disabled')
    return 0

if __name__=='__main__':
    try:raise SystemExit(main())
    except (CycleError,BlockingIOError) as error:
        print(json.dumps({'error':str(error)},ensure_ascii=False));raise SystemExit(2)
