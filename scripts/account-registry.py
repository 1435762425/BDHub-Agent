#!/usr/bin/env python3
"""Project account registry CLI. ``add``/``update`` read their JSON from stdin so secrets never
appear in argv; output never contains a username or password."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "scripts"))
from lib.account_registry import AccountRegistryError, add, listing, update_credentials  # noqa: E402

FIELDS = {"add": {"account", "market", "partnerId", "username", "password"},
          "update": {"account", "username", "password"}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("list", "add", "update"))
    args = parser.parse_args()
    try:
        if args.action == "list":
            result = listing(ROOT)
        else:
            body = json.loads(sys.stdin.read(8192) or "{}")
            if not isinstance(body, dict) or set(body) - FIELDS[args.action] or "account" not in body:
                raise AccountRegistryError("account_request_invalid")
            if args.action == "add":
                result = add(ROOT, account=body["account"], market=body.get("market"),
                             partner_id=body.get("partnerId"), username=body.get("username"),
                             password=body.get("password"))
            else:
                result = update_credentials(ROOT, account=body["account"], username=body.get("username"),
                                            password=body.get("password"))
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (AccountRegistryError, json.JSONDecodeError) as error:
        print(json.dumps({"error": str(error) if isinstance(error, AccountRegistryError) else "account_request_invalid"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
