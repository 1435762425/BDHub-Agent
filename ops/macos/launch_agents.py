#!/usr/bin/env python3
"""Render project LaunchAgents or restore an absent scheduler without clearing a stop request."""
import argparse
import json
import os
from pathlib import Path
import plistlib
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.dont_write_bytecode = True


def ensure_scheduler(root, state=None, start=None):
    root = Path(root)
    # Check before importing the runtime: an unprovisioned checkout must never initialize a DB.
    if not (root / 'var/second-cycle.sqlite').is_file():
        return {'state': 'missing_database'}
    if (root / 'var/operations-scheduler.stop').exists():
        return {'state': 'operator_stopped'}
    if state is None or start is None:
        sys.path.insert(0, str(root / 'scripts'))
        from lib.operations_scheduler import scheduler_state, start_scheduler
        state, start = scheduler_state, start_scheduler
    current = state(root)
    if current['running']:
        return {'state': 'running', 'pid': current['pid']}
    # A stop may arrive while process liveness is being checked.
    if (root / 'var/operations-scheduler.stop').exists():
        return {'state': 'operator_stopped'}
    try:
        current = start(root)
    except ValueError as error:
        if str(error) != 'scheduler_already_running':
            raise
        return {'state': 'running'}
    return {'state': 'started', 'pid': current['pid']}


def jobs(root, node, environment):
    root = Path(root).resolve()
    env = {'PATH': '/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin',
           'PYTHONDONTWRITEBYTECODE': '1', 'NEXT_TELEMETRY_DISABLED': '1'}
    # launchd does not inherit the interactive shell's network route. Render locally only;
    # generated plists are machine configuration and must not be committed.
    for key in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY',
                'http_proxy', 'https_proxy', 'all_proxy', 'no_proxy'):
        if environment.get(key):
            env[key] = environment[key]
    common = {'RunAtLoad': True, 'EnvironmentVariables': env}
    return {
        'io.bdhub.agent.web': {
            **common, 'Label': 'io.bdhub.agent.web', 'KeepAlive': True, 'ThrottleInterval': 30,
            'WorkingDirectory': str(root / 'apps/web'),
            'ProgramArguments': [str(Path(node).absolute()), 'node_modules/next/dist/bin/next',
                                 'start', '--hostname', '127.0.0.1', '--port', '5198'],
            'StandardOutPath': str(root / 'var/web.log'),
            'StandardErrorPath': str(root / 'var/web-error.log'),
        },
        'io.bdhub.agent.scheduler-watch': {
            **common, 'Label': 'io.bdhub.agent.scheduler-watch', 'StartInterval': 60,
            'WorkingDirectory': str(root),
            'ProgramArguments': [str(root / '.venv/bin/python'),
                                 str(root / 'ops/macos/launch_agents.py'), 'ensure-scheduler'],
            'StandardOutPath': str(root / 'var/scheduler-watch.log'),
            'StandardErrorPath': str(root / 'var/scheduler-watch-error.log'),
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('render', 'ensure-scheduler'))
    parser.add_argument('--node')
    parser.add_argument('--output-dir', type=Path)
    args = parser.parse_args()
    if args.action == 'ensure-scheduler':
        result = ensure_scheduler(ROOT)
        # Avoid logging a redundant success every minute.
        if result['state'] != 'running':
            print(json.dumps(result), flush=True)
        return
    if not args.node or not args.output_dir:
        parser.error('render requires --node and --output-dir')
    for path in (Path(args.node), ROOT / '.venv/bin/python',
                 ROOT / 'apps/web/.next/BUILD_ID', ROOT / 'var/second-cycle.sqlite'):
        if not path.is_file():
            parser.error(f'missing runtime prerequisite: {path}')
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for label, config in jobs(ROOT, args.node, os.environ).items():
        path = args.output_dir / f'{label}.plist'
        with path.open('wb') as handle:
            plistlib.dump(config, handle)
        path.chmod(0o600)
        print(path)


if __name__ == '__main__':
    main()
