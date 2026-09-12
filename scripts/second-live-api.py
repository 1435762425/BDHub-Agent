#!/usr/bin/env python3
"""Show or explicitly start an existing, frozen Italy second-outreach scope."""
import argparse
import json
import sys

sys.dont_write_bytecode = True
from lib.second_live_api import FIELDS, MAX_INPUT, SecondLiveAPI
from lib.second_live_trial import LiveTrialError


def main(argv=None, *, api=None, stdin=None, stdout=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=FIELDS)
    args = parser.parse_args(argv)
    stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
    try:
        raw = stdin.read(MAX_INPUT + 1)
        if len(raw.encode("utf-8")) > MAX_INPUT:
            raise LiveTrialError("invalid_request", 400)
        try:
            value = json.loads(raw)
        except (ValueError, UnicodeError):
            raise LiveTrialError("invalid_request", 400) from None
        result = (api or SecondLiveAPI()).dispatch(args.command, value)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False), file=stdout)
        return 0
    except LiveTrialError as error:
        print(json.dumps({"error": {"code": error.code, "message": error.code, "status": error.status}}), file=stdout)
        return 1
    except Exception:
        print(json.dumps({"error": {"code": "second_live_api_failed", "message": "second_live_api_failed", "status": 500}}), file=stdout)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
