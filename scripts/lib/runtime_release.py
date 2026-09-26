"""Which code each resident process actually loaded, recorded once at its start.

The working tree HEAD shows what the next process will load, not what a long-running worker runs.
Each resident process registers the commit (and a digest of uncommitted tracked changes) it started
from; alerts compare live registrations with the current HEAD instead of guessing from the disk.
"""
import hashlib
import json
import os
import subprocess
import time
from pathlib import Path


def _git(root, *args):
    return subprocess.run(['git', *args], cwd=root, capture_output=True, text=True, timeout=10, check=True).stdout


def head_sha(root):
    try:return _git(root, 'rev-parse', 'HEAD').strip() or None
    except (OSError, subprocess.SubprocessError):return None


RUNTIME_TREES = ('scripts', 'vendor')


def code_id(root, sha):
    """Identity of the code a commit runs: the git trees processes load from, not the commit itself.

    A documentation-only commit changes HEAD but not this value, so it is not a version difference."""
    if not sha:
        return None
    try:
        trees = _git(root, 'rev-parse', *[f'{sha}:{path}' for path in RUNTIME_TREES]).split()
    except (OSError, subprocess.SubprocessError):
        return None
    return hashlib.sha256(' '.join(trees).encode()).hexdigest()[:16] if len(trees) == len(RUNTIME_TREES) else None


def current_release(root):
    try:
        sha = _git(root, 'rev-parse', 'HEAD').strip()
        diff = _git(root, 'diff', 'HEAD')
    except (OSError, subprocess.SubprocessError):
        return {'sha': None, 'dirty': None, 'contentDigest': None}
    return {'sha': sha or None, 'dirty': bool(diff),
            'contentDigest': hashlib.sha256(diff.encode()).hexdigest()[:16] if diff else None}


def directory(root):
    return Path(root) / 'var/runtime-loaded'


def register(root, role):
    """Record this process's loaded release; call once at process start, never per heartbeat."""
    if not role.replace('-', '').isalnum():
        raise ValueError('runtime_role_invalid')
    value = {'role': role, 'pid': os.getpid(), 'startedAt': time.time(), **current_release(root)}
    target = directory(root) / f'{role}.json'
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix('.tmp')
    temporary.write_text(json.dumps(value) + '\n', encoding='utf-8')
    temporary.replace(target)
    return value


def loaded(root):
    """Live registrations only (a dead pid's record is history, not a running version)."""
    from lib.process_liveness import pid_alive
    rows = []
    for path in sorted(directory(root).glob('*.json')):
        try:
            value = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        if isinstance(value, dict) and pid_alive(value.get('pid')):
            rows.append(value)
    return rows
