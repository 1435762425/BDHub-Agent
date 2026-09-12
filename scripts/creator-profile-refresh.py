#!/usr/bin/env python3
"""Enqueue/status a fixed OEC profile refresh, or run the local refresh worker."""
import argparse
import json
import signal
import sys
import time

from lib.profile_refresh import ProfileRefreshError, ProfileRefreshStore, ProfileRefreshWorker


def read_request(fields):
    raw = sys.stdin.read(4097)
    if len(raw) > 4096:
        raise ProfileRefreshError("invalid_request")
    try:
        value = json.loads(raw)
    except ValueError as error:
        raise ProfileRefreshError("invalid_request") from error
    if not isinstance(value, dict) or set(value) != set(fields):
        raise ProfileRefreshError("invalid_request")
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("enqueue")
    commands.add_parser("status")
    commands.add_parser("list")
    worker = commands.add_parser("worker")
    worker.add_argument("--once", action="store_true")
    worker.add_argument("--interval", type=float, default=5)
    args = parser.parse_args()
    try:
        with ProfileRefreshStore() as store:
            if args.command == "enqueue":
                value = read_request(("creatorId", "requestId"))
                print(json.dumps(store.enqueue(value["creatorId"], value["requestId"])))
            elif args.command == "status":
                value = read_request(("jobId",))
                print(json.dumps(store.status(value["jobId"])))
            elif args.command == "list":
                value = read_request(("creatorId",))
                print(json.dumps(store.list_jobs(value["creatorId"])))
            else:
                if not 0.1 <= args.interval <= 60:
                    raise ProfileRefreshError("invalid_request")
                def stop(*_):
                    raise KeyboardInterrupt()
                signal.signal(signal.SIGTERM, stop)
                with ProfileRefreshWorker(store) as runner:
                    while True:
                        result = runner.run_once()
                        if result is not None or args.once:
                            print(json.dumps(result or {"status": "idle"}), flush=True)
                        if args.once:
                            break
                        time.sleep(args.interval)
        return 0
    except ProfileRefreshError as error:
        print(json.dumps({"error": {"code": error.code, "message": error.code, "status": error.status}}))
        return 1
    except KeyboardInterrupt:
        return 130
    except Exception:
        print(json.dumps({"error": {"code": "internal_error", "message": "Profile refresh service error", "status": 500}}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
