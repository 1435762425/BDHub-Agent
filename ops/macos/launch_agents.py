#!/usr/bin/env python3
"""Render project LaunchAgents or restore an absent scheduler without clearing a stop request."""
import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import plistlib
import sqlite3
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


def _first_work(root):
    """Read original grants and unfinished work without provisioning a checkout."""
    path = root / 'var/second-cycle.sqlite'
    if not path.is_file():
        return 'missing_database'
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=3)) as db:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {'plan', 'first_outreach_control'} <= tables:
            return 'not_configured'
        plan = db.execute("SELECT id FROM plan WHERE market='br' AND institution='bjn-local-research'").fetchone()
        if not plan:
            return 'not_configured'
        control = db.execute('SELECT mode,approvals_enabled FROM first_outreach_control WHERE plan_id=?', plan).fetchone()
        if control and (control[0] in {'canary', 'continuous'} or control[1] == 1):
            return 'authorized'
        if 'first_outreach_job' in tables and db.execute(
                "SELECT 1 FROM first_outreach_job WHERE plan_id=? AND state IN ('queued','running') LIMIT 1", plan).fetchone():
            return 'unfinished_job'
        if 'cycle_delivery' in tables:
            submitted = ["d.state='unknown'"]
            if 'cycle_delivery_part' in tables:
                submitted.append("EXISTS(SELECT 1 FROM cycle_delivery_part p WHERE p.delivery_id=d.id AND p.state IN ('inflight','accepted','unknown'))")
            if 'cycle_conversation_intent' in tables:
                submitted.append("EXISTS(SELECT 1 FROM cycle_conversation_intent i WHERE i.delivery_id=d.id AND i.state IN ('inflight','received'))")
            if db.execute("SELECT 1 FROM cycle_delivery d WHERE d.plan_id=? AND d.state<>'quarantined_unknown' AND (" +
                          ' OR '.join(submitted) + ') LIMIT 1', plan).fetchone():
                return 'original_delivery'
        has_sample_work = 'sample_review_intent' in tables
    if has_sample_work:
        sys.path.insert(0, str(root / 'scripts'))
        from lib.sample_operations import has_followups
        if has_followups(root, 'br'):
            return 'original_samples'
    return 'operator_stopped'


def ensure_first_outreach(root, state=None, start=None, needed=None):
    """Recover only the original first-outreach worker, preserving every grant."""
    root = Path(root).resolve()
    needed = needed or _first_work
    try:
        reason = needed(root)
        if reason in {'missing_database', 'not_configured', 'operator_stopped'}:
            return {'state': reason}
        if state is None or start is None:
            sys.path.insert(0, str(root / 'scripts'))
            from lib.first_outreach_runtime import worker_state, launch_worker
            state, start = worker_state, launch_worker
        current = state(root)
        if current['running']:
            return {'state': 'running', 'pid': current['pid']}
        # A manual pause can arrive during the liveness check.
        reason = needed(root)
        if reason in {'missing_database', 'not_configured', 'operator_stopped'}:
            return {'state': reason}
        return {'state': 'started', 'pid': start(root), 'reason': reason}
    except (OSError, ValueError, sqlite3.Error) as error:
        return {'state': 'unavailable', 'error': type(error).__name__}


def jobs(root, node, environment):
    root = Path(root).resolve()
    env = {'PATH': str(root / 'private-data/tools/bin') + ':/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin',
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
        first = ensure_first_outreach(ROOT)
        # Avoid logging a redundant success every minute.
        if result['state'] != 'running':
            print(json.dumps(result), flush=True)
        if first['state'] not in {'running', 'not_configured', 'operator_stopped'}:
            print(json.dumps({'firstOutreach': first}), flush=True)
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
