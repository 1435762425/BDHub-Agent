#!/usr/bin/env python3
"""Local draft status/queue API. Model execution requires an enabled trial policy."""
import argparse
import json
import signal
import sys
import time
from lib.outreach_drafts import OutreachDraftError, OutreachDraftStore, OutreachDraftWorker

FIELDS={"status":set(),"enqueue":{"requestId","context","fingerprint","contextRequest"},"list":{"packetId"},"creator_history":{"creatorId"},"detail":{"draftId"},"lookup_request":{"requestId","packetId","style","instructions"}}

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("command",choices=[*FIELDS,"worker"])
    parser.add_argument("--once",action="store_true");parser.add_argument("--interval",type=float,default=5);args=parser.parse_args()
    try:
        value=None
        if args.command!="worker":
            raw=sys.stdin.read(131073)
            if len(raw.encode())>131072:raise OutreachDraftError("invalid_request")
            try:value=json.loads(raw)
            except ValueError:raise OutreachDraftError("invalid_request") from None
            if not isinstance(value,dict) or set(value)!=FIELDS[args.command]:raise OutreachDraftError("invalid_request")
        with OutreachDraftStore() as store:
            if args.command=="status":result=store.status()
            elif args.command=="enqueue":result=store.enqueue(value["requestId"],value["context"],value["fingerprint"],value["contextRequest"])
            elif args.command=="lookup_request":result=store.lookup_request(value["requestId"],{k:v for k,v in value.items() if k!="requestId"})
            elif args.command=="list":result=store.list_drafts(value["packetId"])
            elif args.command=="creator_history":result=store.list_creator_drafts(value["creatorId"])
            elif args.command=="detail":result=store.detail(value["draftId"])
            else:
                if not 0.1<=args.interval<=60:raise OutreachDraftError("invalid_request")
                def stop(*_):raise KeyboardInterrupt()
                signal.signal(signal.SIGTERM,stop)
                with OutreachDraftWorker(store) as worker:
                    while True:
                        result=worker.run_once()
                        if result is not None or args.once:print(json.dumps(result or {"status":"idle"}),flush=True)
                        if args.once:return 0
                        time.sleep(args.interval)
            print(json.dumps(result));return 0
    except OutreachDraftError as error:
        print(json.dumps({"error":{"code":error.code,"message":error.code,"status":error.status}}));return 1
    except KeyboardInterrupt:return 130
    except Exception:
        print(json.dumps({"error":{"code":"internal_error","message":"Draft service error","status":500}}));return 1

if __name__=="__main__":raise SystemExit(main())
