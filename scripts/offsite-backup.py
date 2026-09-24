#!/usr/bin/env python3
"""Plan, write, re-check or auto-run the encrypted off-machine copy.

``plan --target <dir>`` is read-only.  ``run --target <dir> --confirm`` snapshots the state databases,
writes one AES-256 encrypted copy under <dir>/BDHub-Agent-offsite/<time>/, verifies it by decrypting
it back and keeps the newest three copies there.  ``verify --target <dir>`` re-reads the newest copy.
``auto`` is the on-mount entry: it only touches volumes that already hold BDHub-Agent-offsite/.  The
key file is created once next to the local backups; keep a second copy in a password manager.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "scripts"))

from lib.offsite_backup import OffsiteError, auto, plan, run, verify  # noqa: E402

NOTICES = {"writing": "正在写入 {volume}，完成前请勿拔出", "written": "{volume} 已写入并校验，可以拔出",
           "checked": "{volume} 上的最新备份校验通过，可以拔出", "failed": "{volume} 备份失败，详情见 var/offsite-backup.log"}


def notify(volume, event):
    text = NOTICES[event].format(volume=volume)
    subprocess.run(["osascript", "-e", f"display notification {json.dumps(text)} with title \"BDHub 异机备份\""],
                   capture_output=True, check=False)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("plan", "run", "verify", "auto"))
    parser.add_argument("--target", type=Path)
    parser.add_argument("--copy")
    parser.add_argument("--confirm", action="store_true")
    parser.add_argument("--notify", action="store_true")
    args = parser.parse_args(argv)
    try:
        if (args.action == "auto") == (args.target is not None):
            raise OffsiteError("offsite_arguments_invalid")
        if args.action == "plan":
            output = plan(ROOT, args.target)
        elif args.action == "verify":
            output = verify(ROOT, args.target, copy=args.copy)
        elif args.action == "auto":
            output = auto(ROOT, notify=notify if args.notify else None)
        elif not args.confirm:
            raise OffsiteError("offsite_confirmation_required")
        else:
            output = run(ROOT, args.target)
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 2 if args.action == "auto" and any("error" in entry for entry in output) else 0
    except (OffsiteError, OSError, ValueError, subprocess.CalledProcessError) as error:
        print(json.dumps({"error": str(error) or type(error).__name__}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
