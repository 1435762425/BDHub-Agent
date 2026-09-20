#!/usr/bin/env python3
"""CLI boundary for continuous send controls. Only start launches the real worker."""
import argparse
import json
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1];sys.dont_write_bytecode=True;sys.path.insert(0,str(ROOT/'scripts'))
from lib.continuous_send import launch_worker,mutate_control,status  # noqa:E402
from lib.second_cycle import CycleError,CycleStore  # noqa:E402

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('action',choices=('status','save','start','stop','reconcile'));parser.add_argument('--json');args=parser.parse_args()
    try:
        body=json.loads(args.json or '{}')
        with CycleStore(ROOT/'var/second-cycle.sqlite') as store:
            if args.action not in ('status','reconcile'):
                changes=body.get('changes') if args.action=='save' else None
                result=mutate_control(store,ROOT,action=args.action,request_id=body.get('requestId'),
                  expected_revision=body.get('expectedRevision'),changes=changes)
            else:result=None
        worker_pid=launch_worker(ROOT) if args.action=='reconcile' or args.action=='start' and not result.get('duplicate') else None
        with CycleStore(ROOT/'var/second-cycle.sqlite') as store:
            output=status(ROOT,store)
            if worker_pid:output['workerPid']=worker_pid
        print(json.dumps(output,ensure_ascii=False));return 0
    except (CycleError,ValueError,TypeError,json.JSONDecodeError) as error:
        print(json.dumps({'error':str(error)},ensure_ascii=False));return 2

if __name__=='__main__':raise SystemExit(main())
