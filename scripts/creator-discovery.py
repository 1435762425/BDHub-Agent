#!/usr/bin/env python3
"""Preview or explicitly submit a local Italy handle discovery batch."""
import argparse
import json
import signal
import sys
import time

from lib.creator_discovery import CreatorDiscoveryError, CreatorDiscoveryStore, CreatorDiscoveryWorker, preview


FIELDS = {"preview": {"market", "sourceLabel", "text"}, "submit": {"market", "sourceLabel", "text", "previewHash", "requestId"},
          "list": set(), "detail": {"batchId"}, "control": {"batchId", "action", "requestId"}}


def read_request(command):
    raw = sys.stdin.read(131073)
    if len(raw.encode("utf-8")) > 131072:
        raise CreatorDiscoveryError("input_limit_exceeded")
    try:
        value = json.loads(raw)
    except ValueError as error:
        raise CreatorDiscoveryError("invalid_request") from error
    if not isinstance(value, dict) or set(value) != FIELDS[command]:
        raise CreatorDiscoveryError("invalid_request")
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=[*FIELDS, "worker"])
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--interval", type=float, default=5)
    args = parser.parse_args()
    try:
        value = read_request(args.command) if args.command != "worker" else None
        if args.command == "preview":
            result = preview(value["market"], value["sourceLabel"], value["text"])
        else:
            with CreatorDiscoveryStore() as store:
                if args.command == "submit":
                    result = store.submit(value["market"], value["sourceLabel"], value["text"], value["previewHash"], value["requestId"])
                elif args.command == "list":
                    result = store.list_batches()
                elif args.command == "detail":
                    result = store.detail(value["batchId"])
                elif args.command == "control":
                    result = store.control(value["batchId"], value["action"], value["requestId"])
                else:
                    if not 0.1 <= args.interval <= 60:
                        raise CreatorDiscoveryError("invalid_request")
                    def stop(*_):
                        raise KeyboardInterrupt()
                    signal.signal(signal.SIGTERM, stop)
                    with CreatorDiscoveryWorker(store) as worker:
                        while True:
                            result = worker.run_once()
                            if result is not None or args.once:
                                print(json.dumps(result or {"status": "idle"}), flush=True)
                            if args.once:
                                return 0
                            time.sleep(args.interval)
        print(json.dumps(result))
        return 0
    except CreatorDiscoveryError as error:
        print(json.dumps({"error": {"code": error.code, "message": error.code, "status": error.status}}))
        return 1
    except KeyboardInterrupt:
        return 130
    except Exception:
        print(json.dumps({"error": {"code": "internal_error", "message": "Creator discovery service error", "status": 500}}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
