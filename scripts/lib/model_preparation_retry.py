"""Retry metadata for original preparation jobs, never another model intent.

The synchronous DeepSeek adapter has no result-retrieval operation. Subsequent
model attempts are bounded per supply cycle and keep every original unknown
attempt; this policy does not apply to any platform write.
The original tables and frozen inputs stay unchanged for resident callers.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import subprocess
from lib.process_liveness import pid_alive
from lib.second_cycle import CycleError, encoded

TABLES = frozenset({'first_category_job', 'cycle_name_job'})
KNOWN_RESPONSES = frozenset({'provider_output_invalid', 'provider_output_truncated'})
SCHEMA = '''CREATE TABLE IF NOT EXISTS first_model_retry(
 table_name TEXT NOT NULL,job_id TEXT NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,
 next_at REAL NOT NULL DEFAULT 0,outcome TEXT NOT NULL,error TEXT,
 history TEXT NOT NULL DEFAULT '[]',updated REAL NOT NULL,cycle_id TEXT NOT NULL DEFAULT '',
 cycle_attempts INTEGER NOT NULL DEFAULT 0,owner_pid INTEGER,owner_recorded REAL,
 PRIMARY KEY(table_name,job_id));'''
LEGACY_CALL_WINDOW = 120


def ensure_schema(store):
    store.db.execute(SCHEMA)
    columns = {row[1] for row in store.db.execute('PRAGMA table_info(first_model_retry)')}
    for name, kind in (('owner_pid', 'INTEGER'), ('owner_recorded', 'REAL')):
        if name not in columns:
            store.db.execute(f'ALTER TABLE first_model_retry ADD COLUMN {name} {kind}')


def legacy_names_executor_alive(root):
    """Official legacy name callers have no per-job owner; observe their CLI."""
    target = str(Path(root).resolve() / 'scripts/catalog-names.py')
    try:
        result = subprocess.run(['ps', '-axo', 'pid=,args='], capture_output=True, text=True, timeout=3)
        if result.returncode != 0:
            return True
        for line in result.stdout.splitlines():
            try:
                pid, command = line.strip().split(None, 1)
                args = shlex.split(command)
            except ValueError:
                continue
            if int(pid) != os.getpid() and any(arg in {target, 'scripts/catalog-names.py', 'catalog-names.py'} for arg in args) and 'prepare' in args and \
                    '--market' in args and args[args.index('--market') + 1:args.index('--market') + 2] == ['br'] and pid_alive(int(pid)):
                return True
        return False
    except (OSError, subprocess.TimeoutExpired):
        return True


def _save(store, table, job, *, attempts, outcome, cycle_id, cycle_attempts, error=None, next_at=0, history=None,
          owner_pid=None, owner_recorded=None):
    store.db.execute('''INSERT INTO first_model_retry(table_name,job_id,attempts,next_at,outcome,error,history,updated,cycle_id,cycle_attempts,owner_pid,owner_recorded)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(table_name,job_id) DO UPDATE SET attempts=excluded.attempts,next_at=excluded.next_at,
        outcome=excluded.outcome,error=excluded.error,history=excluded.history,updated=excluded.updated,
        cycle_id=excluded.cycle_id,cycle_attempts=excluded.cycle_attempts,owner_pid=excluded.owner_pid,owner_recorded=excluded.owner_recorded''',
                     (table, job, attempts, next_at, outcome, error, encoded(history or []), store.clock(), cycle_id, cycle_attempts, owner_pid, owner_recorded))


def call_original(store, table, job, call, validate, *, cycle_id='initial', active_original=lambda: False):
    """Return ready/isolated/waiting for this exact existing job's response.

    ``call`` receives no new inputs here: the caller has reconstructed them from
    the original job. A response_saved row is validated/replayed without HTTP.
    """
    if table not in TABLES:
        raise CycleError('first_model_job_invalid')
    ensure_schema(store)
    row = store.db.execute(f'SELECT * FROM {table} WHERE id=?', (job,)).fetchone()
    if not row:
        raise CycleError('first_model_job_missing')
    meta = store.db.execute('SELECT * FROM first_model_retry WHERE table_name=? AND job_id=?', (table, job)).fetchone()
    attempts = meta['attempts'] if meta else 0
    history = json.loads(meta['history']) if meta else []
    outcome = meta['outcome'] if meta else None
    cycle_attempts = meta['cycle_attempts'] if meta and meta['cycle_id'] == cycle_id else 0

    def save(**values):
        _save(store, table, job, attempts=attempts, cycle_id=cycle_id,
              cycle_attempts=cycle_attempts, history=history, **values)

    def failed(kind, code, receipt=None):
        delay = min(3600, 30 * 2 ** min(max(attempts - 1, 0), 7))
        exhausted = cycle_attempts >= 3
        next_at = store.clock() + delay if not exhausted else 0
        state = 'quarantined_unknown' if exhausted else 'retry_wait'
        history.append({'attempt': attempts, 'at': store.clock(), 'outcome': kind,
                        'error': code, 'receipt': receipt})
        with store.tx():
            # Only state/response progress changes; id and frozen inputs persist.
            store.db.execute(f'UPDATE {table} SET state=? WHERE id=?', (state, job))
            save(outcome=kind, error=code, next_at=next_at)
        return {'state': 'isolated' if exhausted else 'waiting', 'reason': code,
                'nextRetryAt': next_at or None, 'jobId': job, 'modelCalls': 0}

    # A complete original response is always the first recovery source, even
    # when a later validator fix makes a previously rejected answer usable.
    receipt = None
    if row['response']:
        try:
            receipt = json.loads(row['response'])
            validate(receipt)
        except (KeyError, ValueError, TypeError, CycleError):
            if outcome != 'response_invalid' and isinstance(receipt, dict) and isinstance(receipt.get('content'), str):
                return failed('response_invalid', 'first_model_response_invalid', receipt)
        else:
            save(outcome='confirmed')
            return {'state': 'ready', 'receipt': receipt, 'jobId': job, 'modelCalls': 0}
    if meta and meta['next_at'] > store.clock():
        return {'state': 'waiting', 'reason': meta['error'], 'nextRetryAt': meta['next_at'], 'jobId': job, 'modelCalls': 0}
    if outcome == 'requesting' and meta['owner_pid'] and pid_alive(meta['owner_pid'], meta['owner_recorded']):
        return {'state': 'waiting', 'reason': 'first_model_original_call_active',
                'nextRetryAt': store.clock() + 30, 'jobId': job, 'modelCalls': 0}
    legacy_requesting = row['state'] in {'request_started', 'requesting'} and (not meta or outcome == 'legacy_waiting')
    if legacy_requesting and (not meta or active_original()):
        due = store.clock() + (LEGACY_CALL_WINDOW if not meta else 30)
        save(outcome='legacy_waiting', error='first_model_original_call_window', next_at=due)
        return {'state': 'waiting', 'reason': 'first_model_original_call_window', 'nextRetryAt': due, 'jobId': job, 'modelCalls': 0}
    if not meta and row['state'] not in {'pending', 'recovery_pending'} or outcome in {'requesting', 'legacy_waiting'}:
        # Legacy known-invalid provider answers carry an explicit safe code.
        legacy_code = row['error'] if 'error' in row.keys() else None
        if legacy_code in KNOWN_RESPONSES:
            return failed('response_invalid', legacy_code, receipt)
        if not meta:
            cycle_attempts = 1
        return failed('outcome_unknown', 'first_model_result_query_unavailable', receipt)
    if cycle_attempts >= 3:
        return {'state': 'isolated', 'reason': 'first_model_cycle_budget_exhausted',
                'nextRetryAt': None, 'jobId': job, 'modelCalls': 0}

    attempts += 1
    cycle_attempts += 1
    with store.tx():
        current = store.db.execute('SELECT * FROM first_model_retry WHERE table_name=? AND job_id=?', (table, job)).fetchone()
        latest = store.db.execute(f'SELECT state,response FROM {table} WHERE id=?', (job,)).fetchone()
        fence_fields = ('attempts', 'outcome', 'updated', 'owner_pid', 'owner_recorded')
        if bool(current) != bool(meta) or current and any(current[key] != meta[key] for key in fence_fields) or \
                not latest or latest['state'] != row['state'] or latest['response'] != row['response']:
            return {'state': 'waiting', 'reason': 'first_model_original_progress_changed',
                    'nextRetryAt': store.clock() + 1, 'jobId': job, 'modelCalls': 0}
        store.db.execute(f"UPDATE {table} SET state='requesting',response=NULL WHERE id=?", (job,))
        save(outcome='requesting', owner_pid=os.getpid(), owner_recorded=store.clock())
    try:
        receipt = call()
    except Exception as error:
        code = getattr(error, 'code', None) or (str(error) if isinstance(error, CycleError) else 'provider_failed')
        kind = 'request_not_sent' if getattr(error, 'outcome', None) == 'request_not_sent' or code == 'agent_model_service_paused' else \
               'response_invalid' if code in KNOWN_RESPONSES else 'outcome_unknown'
        if kind == 'request_not_sent':
            cycle_attempts -= 1
        return {**failed(kind, code, getattr(error, 'receipt', None)), 'modelCalls': int(kind != 'request_not_sent')}
    except BaseException:
        failed('outcome_unknown', 'first_model_executor_interrupted')
        raise
    # Crash between this write and the consumer's projection only replays data.
    store.db.execute(f"UPDATE {table} SET state='response_saved',response=? WHERE id=?", (encoded(receipt), job))
    try:
        validate(receipt)
    except (KeyError, ValueError, TypeError, CycleError):
        return {**failed('response_invalid', 'first_model_response_invalid', receipt), 'modelCalls': 1}
    history.append({'attempt': attempts, 'at': store.clock(), 'outcome': 'confirmed',
                    'responseId': receipt.get('responseId') if isinstance(receipt, dict) else None})
    save(outcome='confirmed')
    return {'state': 'ready', 'receipt': receipt, 'jobId': job, 'modelCalls': 1}
