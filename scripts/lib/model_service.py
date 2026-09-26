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


def _schema(db):
    db.execute(SCHEMA)
    # Each open/close transition starts a new epoch; a call only affects the epoch it was admitted in.
    columns = {row[1] for row in db.execute('PRAGMA table_info(agent_model_service)')}
    if 'epoch' not in columns:
        db.execute('ALTER TABLE agent_model_service ADD COLUMN epoch INTEGER NOT NULL DEFAULT 0')
    # Each probe grant gets its own token: a probe whose lease lapsed and was re-granted can no longer
    # decide the breaker, only the probe currently holding the token can (I01).
    if 'probe_token' not in columns:
        db.execute('ALTER TABLE agent_model_service ADD COLUMN probe_token TEXT')


def acquire(store, key):
    """Admit one call and return its permit, or raise ``agent_model_service_paused`` (no attempt spent).

    The permit records the breaker epoch: a result from an older epoch (a call admitted before the
    breaker opened) can neither close a newer pause nor count toward it; only the probe of the
    current open epoch decides whether the service is back."""
    now = store.clock()
    with store.tx():
        _schema(store.db)
        row = _row(store.db, key)
        if row is None or row['state'] == 'closed':
            return {'key': key, 'mode': 'closed', 'epoch': row['epoch'] if row else 0}
        if now < row['next_at'] or now < row['probe_until']:
            raise CycleError('agent_model_service_paused')
        import uuid
        token = uuid.uuid4().hex
        store.db.execute('UPDATE agent_model_service SET probe_until=?,probe_token=?,updated_at=? WHERE service_key=? AND epoch=?',
                         (now + PROBE_LEASE_SECONDS, token, now, key, row['epoch']))
        return {'key': key, 'mode': 'probe', 'epoch': row['epoch'], 'token': token}


def _permit(value):
    # Older callers passed the bare key: treat it as a closed-state permit of epoch 0.
    return value if isinstance(value, dict) else {'key': value, 'mode': 'closed', 'epoch': 0}


def succeeded(store, permit):
    permit = _permit(permit)
    with store.tx():
        _schema(store.db)
        row = _row(store.db, permit['key'])
        if row is None or row['epoch'] != permit['epoch']:
            return  # A call from another epoch says nothing about the current state.
        if row['state'] == 'open' and (permit['mode'] != 'probe' or row['probe_token'] != permit.get('token')):
            return
        store.db.execute("UPDATE agent_model_service SET state='closed',failures=0,opens=0,probe_until=0,probe_token=NULL,last_error=NULL,"
                         "epoch=epoch+?,updated_at=? WHERE service_key=?",
                         (1 if row['state'] == 'open' else 0, store.clock(), permit['key']))


def failed(store, permit, code):
    """Count a service-level failure of the current epoch; local input and invalid output never count."""
    permit = _permit(permit)
    if family(code) not in ('provider_config', 'provider_transient'):
        return
    key, now = permit['key'], store.clock()
    with store.tx():
        _schema(store.db)
        row = _row(store.db, key)
        epoch = row['epoch'] if row else 0
        if epoch != permit['epoch'] or (row and row['state'] == 'open' and (
                permit['mode'] != 'probe' or row['probe_token'] != permit.get('token'))):
            return
        failures = (row['failures'] if row else 0) + 1
        opens = row['opens'] if row else 0
        probing = bool(row and row['state'] == 'open')
        if probing or failures >= OPEN_AFTER:
            pause = min(PAUSE_CAP_SECONDS, PAUSE_SECONDS * 2 ** opens)
            values = ('open', failures, opens + 1, now + pause, 0, code, now, epoch + 1)
        else:
            values = ('closed', failures, opens, 0, 0, code, now, epoch)
        store.db.execute('INSERT INTO agent_model_service(service_key,state,failures,opens,next_at,probe_until,last_error,updated_at,epoch) '
                         'VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(service_key) DO UPDATE SET '
                         'state=excluded.state,failures=excluded.failures,opens=excluded.opens,next_at=excluded.next_at,'
                         'probe_until=excluded.probe_until,last_error=excluded.last_error,updated_at=excluded.updated_at,'
                         'epoch=excluded.epoch,probe_token=NULL', (key, *values))


def paused(db, key, now):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='agent_model_service'").fetchone():
        return None
    row = _row(db, key)
    if not row or row['state'] != 'open':
        return None
    return {'nextAt': row['next_at'], 'lastError': row['last_error'], 'probing': now < row['probe_until']}


def guard(root, call, *, provider='DeepSeek', model=None):
    """Wrap any model call in the shared breaker (translation, product names, materials...).

    The breaker is read and written through its own short-lived connection, so a caller holding a
    read-only store or an open transaction is unaffected. A paused service raises
    ``agent_model_service_paused`` before any request is made."""
    from pathlib import Path
    from lib.second_cycle import CycleStore
    if model is None:
        from lib.draft_provider import MODEL as model
    key, database = service_key(provider, model), Path(root) / 'var/second-cycle.sqlite'

    def guarded(*args, **kwargs):
        with CycleStore(database) as store:
            permit = acquire(store, key)
        try:
            result = call(*args, **kwargs)
        except Exception as error:
            code = getattr(error, 'code', None) or (str(error) if isinstance(error, CycleError) else type(error).__name__)
            with CycleStore(database) as store:
                failed(store, permit, code)
            raise
        with CycleStore(database) as store:
            succeeded(store, permit)
        return result
    return guarded
