#!/usr/bin/env python3
"""Inventory, create, verify or stage-restore BDHub-Agent SQLite state backups."""
import argparse
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "scripts"))

from lib.state_backup import create_backup, inventory, restore_backup, retention_plan, verify_backup  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("inventory", "create", "verify", "restore", "retention-plan"))
    parser.add_argument("--backup")
    parser.add_argument("--output")
    parser.add_argument("--label")
    parser.add_argument("--target-var")
    parser.add_argument("--confirmed", action="store_true")
    parser.add_argument("--backup-root")
    parser.add_argument("--recent-days", type=int)
    parser.add_argument("--daily-days", type=int)
    parser.add_argument("--weekly-days", type=int)
    parser.add_argument("--minimum-verified", type=int)
    parser.add_argument("--protect", action="append", default=[])
    args = parser.parse_args(argv)
    try:
        retention_args = (args.backup_root, args.recent_days, args.daily_days,
                          args.weekly_days, args.minimum_verified)
        if args.action != "retention-plan" and (any(value is not None for value in retention_args) or args.protect):
            raise ValueError("state_backup_arguments_invalid")
        if args.action == "retention-plan":
            if any((args.backup, args.output, args.label, args.target_var, args.confirmed)):
                raise ValueError("state_backup_arguments_invalid")
            options = {key: value for key, value in vars(args).items()
                       if key in {"recent_days", "daily_days", "weekly_days", "minimum_verified"}
                       and value is not None}
            result = retention_plan(args.backup_root or ROOT / "var/backups/state",
                                    protect=args.protect, **options)
            code = 0
        elif args.action == "inventory":
            if any((args.backup, args.output, args.label, args.target_var, args.confirmed)):
                raise ValueError("state_backup_arguments_invalid")
            result = inventory(ROOT)
            code = 0 if result["ready"] else 2
        elif args.action == "create":
            if args.backup or args.target_var or args.confirmed:
                raise ValueError("state_backup_arguments_invalid")
            result = create_backup(ROOT, output=args.output, label=args.label)
            code = 0
        elif args.action == "verify":
            if not args.backup or any((args.output, args.label, args.target_var, args.confirmed)):
                raise ValueError("state_backup_arguments_invalid")
            result = verify_backup(args.backup)
            code = 0
        else:
            if not args.backup or not args.target_var or args.output or args.label:
                raise ValueError("state_backup_arguments_invalid")
            result = restore_backup(args.backup, args.target_var, confirmed=args.confirmed)
            code = 0
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return code
    except (OSError, ValueError, sqlite3.Error) as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
