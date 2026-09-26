"""Model-call error families and a durable service-level breaker (§9.17).

Per-input budgets stay in agent_reply_decision_v2 (at most 3 real calls per input). This module only
decides whether the shared model service may be called at all: repeated service-level failures pause
every caller, calls not made while paused cost no one an attempt, and after the pause exactly one
caller holds a probe lease. Engineering values below are not business-confirmed parameters.
"""
from lib.second_cycle import CycleError, digest

SCHEMA = ('CREATE TABLE IF NOT EXISTS agent_model_service(service_key TEXT PRIMARY KEY,state TEXT NOT NULL,'
          'failures INTEGER NOT NULL,opens INTEGER NOT NULL,next_at REAL NOT NULL,probe_until REAL NOT NULL,'
          'last_error TEXT,updated_at REAL NOT NULL)')
OPEN_AFTER = 3
PAUSE_SECONDS = 300
PAUSE_CAP_SECONDS = 3600
PROBE_LEASE_SECONDS = 120

LOCAL_INPUT = frozenset({'provider_input_invalid', 'provider_limit_invalid'})
SERVICE_CONFIG = frozenset({'provider_not_configured', 'provider_config_invalid', 'provider_config_unavailable',
                            'provider_runtime_unavailable', 'provider_deadline_unavailable', 'provider_clock_invalid'})
SERVICE_TRANSIENT = frozenset({'provider_timeout', 'provider_network_error', 'provider_http_error',
                               'provider_redirect_rejected'})


def family(code):
    """local_input_invalid | provider_config | provider_transient | invalid_output."""
    if code in LOCAL_INPUT:
        return 'local_input_invalid'
    if code in SERVICE_CONFIG:
        return 'provider_config'
    if code in SERVICE_TRANSIENT:
        return 'provider_transient'
    return 'invalid_output'


def service_key(provider, model):
    return 'model-service-' + digest([provider, model])[:24]


def _row(db, key):
    return db.execute('SELECT * FROM agent_model_service WHERE service_key=?', (key,)).fetchone()


def acquire(store, key):
    """Allow one call, or raise ``agent_model_service_paused`` without spending any attempt."""
    now = store.clock()
    with store.tx():
        store.db.execute(SCHEMA)
        row = _row(store.db, key)
        if row is None or row['state'] == 'closed':
            return 'closed'
        if now < row['next_at'] or now < row['probe_until']:
            raise CycleError('agent_model_service_paused')
        store.db.execute('UPDATE agent_model_service SET probe_until=?,updated_at=? WHERE service_key=?',
                         (now + PROBE_LEASE_SECONDS, now, key))
        return 'probe'


def succeeded(store, key):
    with store.tx():
        store.db.execute(SCHEMA)
        store.db.execute("UPDATE agent_model_service SET state='closed',failures=0,opens=0,probe_until=0,last_error=NULL,"
                         "updated_at=? WHERE service_key=?", (store.clock(), key))


def failed(store, key, code):
    """Count a service-level failure; local input and invalid output do not concern the service."""
    if family(code) not in ('provider_config', 'provider_transient'):
        return
    now = store.clock()
    with store.tx():
        store.db.execute(SCHEMA)
        row = _row(store.db, key)
        failures = (row['failures'] if row else 0) + 1
        opens = row['opens'] if row else 0
        probing = bool(row and row['state'] == 'open')
        if probing or failures >= OPEN_AFTER:
            pause = min(PAUSE_CAP_SECONDS, PAUSE_SECONDS * 2 ** opens)
            values = ('open', failures, opens + 1, now + pause, 0, code, now)
        else:
            values = ('closed', failures, opens, 0, 0, code, now)
        store.db.execute('INSERT INTO agent_model_service VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(service_key) DO UPDATE SET '
                         'state=excluded.state,failures=excluded.failures,opens=excluded.opens,next_at=excluded.next_at,'
                         'probe_until=excluded.probe_until,last_error=excluded.last_error,updated_at=excluded.updated_at',
                         (key, *values))


def paused(db, key, now):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='agent_model_service'").fetchone():
        return None
    row = _row(db, key)
    if not row or row['state'] != 'open':
        return None
    return {'nextAt': row['next_at'], 'lastError': row['last_error'], 'probing': now < row['probe_until']}
