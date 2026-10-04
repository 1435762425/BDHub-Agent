#!/usr/bin/env python3
"""Daily business tables from local ledgers, and an independently scheduled email delivery."""
import argparse
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / 'scripts'))
from lib.business_daily import collect, render
from lib.business_daily_mail import capture, tick, launchagent
from lib.cycle_stats import BEIJING


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('preview', 'capture', 'tick', 'install-schedule'))
    parser.add_argument('--root', type=Path, default=ROOT, help='Project root, useful for isolated read-only validation')
    parser.add_argument('--date', help='Completed Beijing day, default yesterday')
    parser.add_argument('--output', type=Path, help='Preview export folder')
    args = parser.parse_args()
    root = args.root.resolve()
    now = time.time()
    day = args.date or (datetime.fromtimestamp(now, BEIJING).date() - timedelta(days=1)).isoformat()
    try:
        if args.action == 'install-schedule':
            configuration_path = root / 'private-data/business-daily-mail.json'
            if not configuration_path.is_file():
                raise ValueError('mail_configuration_missing')
            from lib.business_daily_mail import configuration
            configuration(root)
            folder = root / 'var/business-daily'
            folder.mkdir(parents=True, exist_ok=True, mode=0o700)
            label = 'io.bdhub.agent.business-daily'
            plist = folder / (label + '.plist')
            payload = launchagent(root, root / '.venv/bin/python')
            plist.write_bytes(plistlib.dumps(payload)); os.chmod(plist, 0o600)
            agents = Path.home() / 'Library/LaunchAgents'
            agents.mkdir(parents=True, exist_ok=True)
            alias = agents / plist.name
            if alias.is_symlink():
                if alias.resolve() != plist.resolve():
                    raise ValueError('mail_schedule_belongs_to_other_project')
            elif alias.exists():
                raise ValueError('mail_schedule_already_exists')
            else:
                alias.symlink_to(plist)
            domain = f'gui/{os.getuid()}'
            present = subprocess.run(['launchctl', 'print', domain + '/' + label], capture_output=True).returncode == 0
            if not present:
                subprocess.run(['launchctl', 'bootstrap', domain, str(alias)], check=True, capture_output=True)
            result = {'state': 'schedule_installed', 'sendTime': configuration(root)['sendTime'],
                      'timezone': 'Asia/Shanghai', 'emailSent': False}
        elif args.action == 'tick':
            result = tick(root, now=now)
        elif args.action == 'capture':
            from lib.business_daily_mail import locked
            with locked(root):
                value = capture(root, day, now=now)
            result = {'state': 'captured', 'date': day, 'inventorySampledAt': value['inventorySampledAt'],
                      'platformWrites': 0, 'emailSent': False}
        else:
            value = collect(root, day, now=now)
            subject, plain, html = render(value)
            if args.output:
                args.output.mkdir(parents=True, exist_ok=True)
                for name, body in (('email.txt', plain), ('email.html', html),
                                   ('report.json', json.dumps(value, ensure_ascii=False, indent=2))):
                    (args.output / name).write_text(body, encoding='utf-8')
            result = value
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except BlockingIOError:
        print(json.dumps({'state': 'runner_already_active', 'emailSent': False}))
        return 0
    except (ValueError, OSError, KeyError, TypeError, json.JSONDecodeError, subprocess.SubprocessError):
        print(json.dumps({'state': 'report_unavailable', 'emailSent': False}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
