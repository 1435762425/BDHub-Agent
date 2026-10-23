"""BR page controls, durable local jobs and the one first-outreach worker.

Status is strictly read only. A page mutation records its grant/job before a
worker can be launched; restarting the worker consumes the same job and stages.
"""
from __future__ import annotations

from contextlib import closing
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import sqlite3
import subprocess
import time
import uuid

from lib import first_outreach as first
from lib import sample_operations as samples
from lib.process_liveness import record_alive
from lib.second_cycle import CycleError, CycleStore, digest, encoded

ACTIONS = {'prepare', 'sync', 'start', 'continue', 'stop', 'set-approvals', 'reconcile'}
JOBS = {'prepare', 'sync', 'reconcile'}
SCHEMA = '''
CREATE TABLE IF NOT EXISTS first_outreach_job(
 request_id TEXT PRIMARY KEY,plan_id TEXT NOT NULL,action TEXT NOT NULL,payload TEXT NOT NULL,
 result_revision INTEGER NOT NULL,state TEXT NOT NULL,stage INTEGER NOT NULL DEFAULT 0,
 created REAL NOT NULL,started REAL,updated REAL NOT NULL,finished REAL,error TEXT);
CREATE INDEX IF NOT EXISTS first_outreach_job_pending ON first_outreach_job(plan_id,state,created);
CREATE TABLE IF NOT EXISTS first_outreach_runtime(
 plan_id TEXT PRIMARY KEY,state TEXT NOT NULL,reason TEXT,updated REAL NOT NULL,
 pid INTEGER,started_at REAL,current_request TEXT,last_sample_sync REAL NOT NULL DEFAULT 0);
'''


def validate_request(action, market, body):
    fields = {'action', 'market', 'requestId', 'expectedRevision'}
    if action == 'set-approvals':
        fields.add('approvalsEnabled')
    if market != 'br' or action not in ACTIONS or not isinstance(body, dict) or set(body) != fields or \
            body.get('action') != action or body.get('market') != market or \
            not isinstance(body.get('requestId'), str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]{7,119}', body['requestId']) or \
            type(body.get('expectedRevision')) is not int or body['expectedRevision'] < 0 or \
            action == 'set-approvals' and type(body['approvalsEnabled']) is not bool:
        raise CycleError('invalid_first_outreach_request')
    return dict(body)


def ensure_schema(store):
    first.ensure_schema(store)
    store.db.executescript(SCHEMA)


def _table(store, name):
    return bool(store.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone())


def _code(error):
    code = getattr(error, 'code', None)
    if not code and isinstance(error, (CycleError, ValueError)):
        code = str(error)
    if isinstance(error, sqlite3.OperationalError) and any(v in str(error).lower() for v in ('locked', 'busy')):
        return 'local_store_busy'
    return code if isinstance(code, str) and re.fullmatch(r'[A-Za-z0-9_:-]{1,100}', code) else type(error).__name__


def _path(root):
    path = Path(root) / 'var/second-cycle.sqlite'
    if not path.is_file():
        raise CycleError('first_outreach_state_unavailable')
    return path


def _cfg(root):
    with CycleStore(_path(root), readonly=True) as store:
        return first.control(store, 'br')


def worker_state(root):
    try:
        value = json.loads((Path(root) / 'var/first-outreach-worker-br.json').read_text())
    except (OSError, ValueError, TypeError):
        value = {}
    return {'running': record_alive(value), 'pid': value.get('pid'), 'startedAt': value.get('startedAt')}


def _record_worker(root, value):
    path = Path(root) / 'var/first-outreach-worker-br.json'
    tmp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    tmp.write_text(encoded(value) + '\n')
    tmp.replace(path)


def launch_worker(root):
    root = Path(root)
    _path(root)
    with (root / 'var/first-outreach-launch-br.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        current = worker_state(root)
        if current['running']:
            return current['pid']
        with (root / 'var/first-outreach-br.log').open('a') as log:
            child = subprocess.Popen([str(root / '.venv/bin/python'), str(root / 'scripts/first-outreach.py'), 'worker', '--market', 'br'],
                                     cwd=root, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                     start_new_session=True, env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'})
        _record_worker(root, {'pid': child.pid, 'startedAt': time.time(), 'state': 'starting'})
        return child.pid


def _enqueue(store, body):
    plan = first.control(store)['planId']
    payload = encoded(body)
    with store.tx():
        prior = store.db.execute('SELECT * FROM first_outreach_job WHERE request_id=?', (body['requestId'],)).fetchone()
        if prior:
            if prior['payload'] != payload or prior['plan_id'] != plan:
                raise CycleError('first_outreach_request_conflict')
            return dict(prior)
        if store.db.execute('SELECT 1 FROM first_outreach_request WHERE request_id=?', (body['requestId'],)).fetchone():
            raise CycleError('first_outreach_request_conflict')
        cfg = first.control(store)
        if cfg['revision'] != body['expectedRevision']:
            raise CycleError('first_outreach_revision_conflict')
        if store.db.execute("SELECT 1 FROM first_outreach_job WHERE plan_id=? AND state IN ('queued','running')", (plan,)).fetchone():
            raise CycleError('first_outreach_job_pending')
        revision, now = cfg['revision'] + 1, store.clock()
        store.db.execute('''INSERT INTO first_outreach_control(plan_id,mode,approvals_enabled,revision,updated_at)
         VALUES(?,?,?,?,?) ON CONFLICT(plan_id) DO UPDATE SET revision=excluded.revision,updated_at=excluded.updated_at''',
                         (plan, cfg['mode'], int(cfg['approvalsEnabled']), revision, now))
        store.db.execute('INSERT INTO first_outreach_request VALUES(?,?,?,?,?)',
                         (body['requestId'], plan, encoded(['br', body['action'], None]), revision, now))
        store.db.execute('INSERT INTO first_outreach_job(request_id,plan_id,action,payload,result_revision,state,created,updated) VALUES(?,?,?,?,?,?,?,?)',
                         (body['requestId'], plan, body['action'], payload, revision, 'queued', now, now))
        return dict(store.db.execute('SELECT * FROM first_outreach_job WHERE request_id=?', (body['requestId'],)).fetchone())


def mutate(root, action, market, body, *, launcher=None):
    body = validate_request(action, market, body)
    with CycleStore(_path(root)) as store:
        ensure_schema(store)
        if action in JOBS:
            result = _enqueue(store, body)
            launch = result['state'] in {'queued', 'running'}
        else:
            if action in {'start', 'continue'} and not store.db.execute('SELECT 1 FROM first_outreach_request WHERE request_id=?', (body['requestId'],)).fetchone():
                if store.db.execute("SELECT 1 FROM first_outreach_job WHERE state IN ('queued','running') AND plan_id=?", (first.control(store)['planId'],)).fetchone():
                    raise CycleError('first_outreach_job_pending')
                if not _send_capability(root, store):
                    raise CycleError('first_outreach_send_capability_unverified')
            result = first.change_control(store, root, market, body['requestId'], action, body['expectedRevision'],
                                          approvals_enabled=body.get('approvalsEnabled'))
            launch = action in {'start', 'continue'} or action == 'set-approvals' and body['approvalsEnabled']
    if launch:
        (launcher or launch_worker)(root)
    return status(root)


def _application_rows(store, sync_id=None):
    if not _table(store, 'sample_application'):
        return []
    if not _table(store, 'sample_review_intent'):
        raise CycleError('first_outreach_sample_state_unavailable')
    scope = ' AND EXISTS(SELECT 1 FROM sample_sync_member m WHERE m.sync_id=? AND m.application_id=a.application_id)' if sync_id else ''
    return store.db.execute('''SELECT a.application_id,a.payload,a.observed,i.state review_state
     FROM sample_application a LEFT JOIN sample_review_intent i ON i.market=a.market AND i.application_id=a.application_id
     WHERE a.market='br' ''' + scope + ' ORDER BY a.observed DESC,a.application_id LIMIT 100', (sync_id,) if sync_id else ()).fetchall()


def _application_counts(store, sync_id=None):
    if not _table(store, 'sample_application'):
        return 0, 0, 0
    pending = manual = assessable = 0
    scope = ' AND EXISTS(SELECT 1 FROM sample_sync_member m WHERE m.sync_id=? AND m.application_id=a.application_id)' if sync_id else ''
    for state, review, payment, count in store.db.execute('''SELECT json_extract(a.payload,'$.state'),i.state,
     CASE WHEN json_type(a.payload,'$.paymentRequired')='false' THEN 0 ELSE 1 END,count(*)
     FROM sample_application a LEFT JOIN sample_review_intent i ON i.market=a.market AND i.application_id=a.application_id
     WHERE a.market='br' ''' + scope + ' GROUP BY 1,2,3', (sync_id,) if sync_id else ()):
        if state not in {'pending', 'unknown'} or review in {'approved', 'seller_pending', 'closed_externally'}:
            continue
        pending += count
        if review in {'inflight', 'accepted', 'unknown', 'quarantined_unknown'}:
            continue
        if state == 'unknown' or payment or review == 'rejected':
            manual += count
        else:
            assessable += count
    return pending, manual, assessable


def _application_reason(store, root, application, review_state):
    if review_state in {'inflight', 'accepted', 'unknown', 'quarantined_unknown'}:
        return 'sample_review_result_unknown'
    if application.get('state') not in {'pending', 'unknown'} or review_state in {'approved', 'seller_pending', 'closed_externally'}:
        return None
    if application.get('state') == 'unknown':
        return 'sample_application_state_unverified'
    if application.get('paymentRequired') is not False:
        return 'sample_payment_requires_handling'
    if review_state == 'rejected':
        return 'sample_review_rejected'
    assessed = first.assess_application(store, root, 'br', application)
    return None if assessed.get('eligible') else assessed.get('reason') or 'sample_application_requires_review'


def _identity_names(root, oec_ids):
    path = Path(root) / 'var/creator-identities.sqlite'
    keys = sorted({key for key in oec_ids if isinstance(key, str) and key})
    if not path.is_file() or not keys:
        return {}
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as db:
        return {str(row[0]): row[1] for row in db.execute("SELECT oec_id,current_handle FROM creator_identity WHERE market='br' AND handle_conflict=0 AND current_handle IS NOT NULL AND oec_id IN (" + ','.join('?' for _ in keys) + ')', keys)}


def _category_blockers(store, plan):
    from lib.first_catalog import offers
    missing = [offer for _, offer in offers(store, plan)
               if not offer.get('categoryId') and first.assess_first_offer({**offer, 'categoryId': 'pending'}, store.clock())['eligible']]
    if not missing:
        return []
    if _table(store, 'first_category_job'):
        states = {row[0] for row in store.db.execute("SELECT state FROM first_category_job WHERE market='br'")}
        if states & {'failed', 'unknown', 'requesting'}:
            return ['first_category_model_unavailable']
    return ['first_category_taxonomy_unavailable' if not _table(store, 'first_product_category') else 'first_category_incomplete']


def _send_capability(root, store):
    from lib.account_identity import current_generation
    from lib.market_accounts import load_config
    try:
        account = load_config(root)['markets']['br']['roles']['communications']
        generation = current_generation(store, 'br', account)
    except (OSError, ValueError, KeyError):
        return False
    return (generation or {}).get('capabilities', {}).get('message_send', {}).get('state') == 'verified'


def _sample_binding_current(root, account, binding):
    """Check the active project identity without opening a platform session."""
    from lib.legacy_runtime import project_identity_paths
    if not isinstance(binding, dict) or binding.get('market') != 'br' or binding.get('account') != account or not binding.get('institutionFingerprint'):
        return False
    paths = project_identity_paths(root, account)
    if not paths or paths.get('market') != 'br':
        return False
    try:
        return hashlib.sha256(Path(paths['headersJson']).read_bytes()).hexdigest() == binding.get('identitySha256')
    except (OSError, KeyError, TypeError):
        return False


def _coverage_snapshot(root, store):
    """A historical filtered empty read cannot prove the current pending scope."""
    if not _table(store, 'sample_sync') or not _table(store, 'sample_capability') or not _table(store, 'sample_sync_member'):
        return None
    columns = {row[1] for row in store.db.execute('PRAGMA table_info(sample_sync)')}
    if not {'binding_fingerprint', 'contract_fingerprint'} <= columns:
        return None
    try:
        account = samples._account(root, 'br')
        cap = samples.capability(store, 'br', account)
        if cap['state'] != 'verified':
            return None
        contract = samples.validate_contract(cap.get('contract'))
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if not _sample_binding_current(root, account, cap.get('binding')):
        return None
    row = store.db.execute("""SELECT id,observed,record_count,binding_fingerprint,contract_fingerprint FROM sample_sync
     WHERE market='br' AND account_name=? AND complete=1 ORDER BY observed DESC,rowid DESC LIMIT 1""", (account,)).fetchone()
    if not row or not cap.get('observed') or not cap['observed'] <= row['observed'] <= store.clock() + 30 or store.clock() - row['observed'] > samples.MAX_FACT_AGE:
        return None
    if row['binding_fingerprint'] != digest(cap['binding']) or row['contract_fingerprint'] != digest(contract):
        return None
    members = store.db.execute("""SELECT count(*) FROM sample_sync_member m JOIN sample_application a
     ON a.application_id=m.application_id AND a.market='br' AND a.account_name=? WHERE m.sync_id=?""", (account, row['id'])).fetchone()[0]
    return row if members == row['record_count'] else None


def _application_coverage(root, store):
    return 'complete' if _coverage_snapshot(root, store) is not None else 'unverified'


def status(root, market='br'):
    if market != 'br':
        raise CycleError('first_outreach_market_unsupported')
    path = _path(root)
    with CycleStore(path, readonly=True) as store:
        cfg = first.control(store, market)
        base, sample = first.status(root, market), samples.status(root, market)
        snapshot = _coverage_snapshot(root, store)
        sync_id = snapshot['id'] if snapshot is not None else None
        counts = {key: base['counts'][key] for key in ('products', 'matchedCreators', 'sent', 'pendingApplications', 'approved', 'needsReview', 'unknown')}
        send_unknown = base['counts']['unknown']
        pending_count, manual_count, assessable_count = _application_counts(store, sync_id)
        counts.update(pendingApplications=pending_count, approved=sample['counts'].get('approved', 0), needsReview=manual_count)
        counts['unknown'] += sum(sample['counts'].get(key, 0) for key in ('inflight', 'accepted', 'unknown', 'quarantined_unknown'))
        applications, assessed_count = [], 0
        parsed = [(row, json.loads(row['payload'])) for row in _application_rows(store, sync_id)]
        names = _identity_names(root, [app.get('oecId') for _, app in parsed])
        from lib.first_catalog import offers
        product_names = {(offer['pid'], offer.get('campaignId')): offer.get('title') or offer['pid'] for _, offer in offers(store, cfg['planId'])}
        for row, app in parsed:
            reason = _application_reason(store, root, app, row['review_state'])
            pending = app.get('state') in {'pending', 'unknown'} and row['review_state'] not in {'approved', 'seller_pending', 'closed_externally'}
            assessable = pending and app.get('state') == 'pending' and app.get('paymentRequired') is False and row['review_state'] not in {'inflight', 'accepted', 'unknown', 'quarantined_unknown', 'rejected'}
            assessed_count += int(assessable)
            counts['needsReview'] += int(assessable and reason is not None)
            pid = app.get('pid') or '商品身份待核对'
            applications.append({'id': row['application_id'], 'handle': names.get(app.get('oecId')) or '身份待核对',
                                 'pid': pid, 'productName': product_names.get((app.get('pid'), app.get('campaignId'))) or str(pid),
                                 'state': row['review_state'] or app.get('state') or 'unknown', 'reason': reason})
        category_wait = _category_blockers(store, cfg['planId'])
        # Unclassified products stay out of the pool; they do not hold ready
        # products and their fully matched creators behind an unrelated item.
        blockers = list(dict.fromkeys(base['readiness']['blockers'] + (category_wait if not base['counts']['products'] else [])))
        if not _send_capability(root, store):
            blockers.append('first_outreach_send_capability_unverified')
        if send_unknown and 'first_outreach_result_unknown' not in blockers:
            blockers.append('first_outreach_result_unknown')
        runtime = dict(base['runtime'])
        if _table(store, 'first_outreach_runtime'):
            row = store.db.execute('SELECT * FROM first_outreach_runtime WHERE plan_id=?', (cfg['planId'],)).fetchone()
            if row:
                runtime = {'state': row['state'], 'reason': row['reason'], 'updatedAt': row['updated']}
        queued = None
        if _table(store, 'first_outreach_job'):
            queued = store.db.execute("SELECT * FROM first_outreach_job WHERE plan_id=? AND state IN ('queued','running') ORDER BY created LIMIT 1", (cfg['planId'],)).fetchone()
            latest = store.db.execute("SELECT * FROM first_outreach_job WHERE plan_id=? ORDER BY created DESC LIMIT 1", (cfg['planId'],)).fetchone()
            if not queued and latest and latest['state'] == 'error':
                runtime = {'state': 'attention', 'reason': latest['error'], 'updatedAt': latest['updated']}
        if queued:
            waiting = runtime['state'] in {'waiting_account', 'waiting_capacity'}
            runtime = {'state': runtime['state'] if waiting else {'prepare': 'preparing', 'sync': 'syncing', 'reconcile': 'waiting_reconciliation'}[queued['action']], 'reason': runtime['reason'] if waiting else None, 'updatedAt': queued['updated']}
        elif send_unknown:
            runtime['state'], runtime['reason'] = 'waiting_reconciliation', 'first_outreach_result_unknown'
        elif cfg['mode'] == 'stopped' and runtime['state'] in {'sending', 'continuous', 'canary', 'starting'}:
            runtime['state'], runtime['reason'] = ('canary_confirmed' if cfg.get('canaryDeliveryId') else 'stopped'), None
        if (queued or cfg['mode'] != 'stopped' or cfg['approvalsEnabled']) and not worker_state(root)['running']:
            runtime['reason'] = 'first_outreach_worker_unavailable'
        if assessed_count < assessable_count and not queued and not send_unknown and runtime['state'] != 'attention' and runtime['reason'] != 'first_outreach_worker_unavailable':
            runtime['reason'] = 'sample_application_assessment_incomplete'
        if not runtime['reason'] and cfg['approvalsEnabled'] and sample['capability']['state'] != 'verified':
            runtime['reason'] = sample['capability'].get('reason') or 'sample_review_contract_unverified'
        if not runtime['reason'] and category_wait:
            runtime['reason'] = category_wait[0]
        generation = base.get('generation') or {}
        return {'schemaVersion': 'bdhub.first-outreach.v1', 'market': market,
                'control': {key: cfg[key] for key in ('revision', 'mode', 'approvalsEnabled', 'updatedAt')},
                'readiness': {'ready': not blockers, 'blockers': blockers}, 'counts': counts,
                'applicationCoverage': 'complete' if snapshot is not None else 'unverified',
                'candidates': [{key: row[key] for key in ('creatorId', 'handle', 'pid', 'productName', 'categoryName', 'gmv', 'message')} for row in base['candidates']],
                'applications': applications, 'runtime': runtime, 'platformWrites': 0,
                'sourceUpdatedAt': generation.get('completedAt')}


def _publish(root, state, reason=None, request=None, *, sample_sync=False, started_at=None):
    with CycleStore(_path(root)) as store:
        ensure_schema(store)
        plan, now = first.control(store)['planId'], store.clock()
        store.db.execute('''INSERT INTO first_outreach_runtime VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(plan_id) DO UPDATE SET
         state=excluded.state,reason=excluded.reason,updated=excluded.updated,pid=excluded.pid,
         started_at=COALESCE(excluded.started_at,first_outreach_runtime.started_at),current_request=excluded.current_request,
         last_sample_sync=CASE WHEN ? THEN excluded.last_sample_sync ELSE first_outreach_runtime.last_sample_sync END''',
                         (plan, state, reason, now, os.getpid(), started_at, request, now if sample_sync else 0, int(sample_sync)))


def _claim(root):
    with CycleStore(_path(root)) as store:
        ensure_schema(store)
        with store.tx():
            row = store.db.execute("SELECT * FROM first_outreach_job WHERE state IN ('queued','running') ORDER BY created LIMIT 1").fetchone()
            if not row:
                return None
            now = store.clock()
            store.db.execute("UPDATE first_outreach_job SET state='running',started=COALESCE(started,?),updated=? WHERE request_id=?", (now, now, row['request_id']))
            return dict(store.db.execute('SELECT * FROM first_outreach_job WHERE request_id=?', (row['request_id'],)).fetchone())


def _job_update(root, job, *, stage=None, state=None, error=None):
    with CycleStore(_path(root)) as store:
        store.db.execute('''UPDATE first_outreach_job SET stage=COALESCE(?,stage),state=COALESCE(?,state),
         updated=?,finished=CASE WHEN ? IN ('complete','error') THEN ? ELSE finished END,error=? WHERE request_id=?''',
                         (stage, state, store.clock(), state, store.clock(), error, job['request_id']))


def _job_stopped(root, job, stopped):
    if stopped():
        return True
    if job['action'] == 'reconcile':
        return False
    with CycleStore(_path(root), readonly=True) as store:
        rows = store.db.execute('SELECT payload FROM first_outreach_request WHERE plan_id=? AND result_revision>?', (job['plan_id'], job['result_revision']))
        return any(json.loads(row[0])[1] == 'stop' for row in rows)


def _enrich(root, stopped):
    from lib.first_catalog import enrich
    return enrich(root, 'br', stopped=stopped)


def _refresh(root, request, stopped):
    from lib.first_catalog import refresh
    return refresh(root, 'br', request, stopped=stopped)


def _send(root, request, *, reconcile_only=False):
    from lib.market_send_canary import run
    return run(root, 'br', request, canary=False, page_control=True, purpose='first', reconcile_only=reconcile_only)


def _pending_samples(root, *, include_seller=False):
    with CycleStore(_path(root), readonly=True) as store:
        if not _table(store, 'sample_review_intent'):
            return []
        return [dict(row) for row in store.db.execute("SELECT * FROM sample_review_intent WHERE market='br' AND (state IN ('inflight','accepted','unknown') OR ? AND state='seller_pending') ORDER BY created", (int(include_seller),))]


def _reconcile_samples(root, stopped):
    for intent in _pending_samples(root, include_seller=True):
        if stopped():
            return {'state': 'paused'}
        with CycleStore(_path(root)) as store:
            samples.reconcile(store, root, intent, stopped=stopped)
    return {'state': 'complete'}


def _send_unknown(root):
    with CycleStore(_path(root), readonly=True) as store:
        cfg = first.control(store)
        return first._unknown(store, cfg['planId'])


def process_job(root, job, *, stopped=lambda: False):
    cancel = lambda: _job_stopped(root, job, stopped)
    stages = {'prepare': ('enrich', 'prepare'), 'sync': ('refresh', 'samples', 'enrich', 'prepare'), 'reconcile': ('send_reconcile', 'sample_reconcile')}[job['action']]
    for index in range(job['stage'], len(stages)):
        if cancel():
            if stopped():
                return {'state': 'paused'}
            _job_update(root, job, state='error', error='first_outreach_job_stopped')
            return {'state': 'stopped'}
        name = stages[index]
        _publish(root, 'preparing' if job['action'] == 'prepare' else 'syncing' if job['action'] == 'sync' else 'waiting_reconciliation', request=job['request_id'])
        if name == 'refresh':
            result = _refresh(root, job['request_id'], cancel)
        elif name == 'samples':
            result = samples.sync(root, 'br', stopped=cancel)
            _publish(root, 'syncing', request=job['request_id'], sample_sync=True)
        elif name == 'enrich':
            result = _enrich(root, cancel)
        elif name == 'prepare':
            result = first.prepare(root, 'br', should_stop=cancel)
        elif name == 'send_reconcile':
            result = _send(root, job['request_id'], reconcile_only=True) if _send_unknown(root) else {'state': 'complete'}
        else:
            result = _reconcile_samples(root, cancel)
        if result.get('state') in {'paused', 'waiting'}:
            if result.get('state') == 'waiting':
                raise CycleError(result.get('reason') or 'first_outreach_job_not_ready')
            if result.get('reason'):
                waiting = _wait_state(result['reason'])
                _publish(root, waiting if waiting != 'attention' else 'syncing' if job['action'] == 'sync' else 'preparing', result['reason'], job['request_id'])
            return result
        _job_update(root, job, stage=index + 1)
    _job_update(root, job, state='complete')
    _publish(root, 'stopped' if _cfg(root)['mode'] == 'stopped' else 'ready')
    return {'state': 'complete'}


def _wait_state(code):
    if code in {'outside_send_window'}:
        return 'waiting_window'
    if code in {'market_send_candidate_missing', 'first_outreach_candidates_missing'}:
        return 'waiting_candidates'
    if 'unknown' in code:
        return 'waiting_reconciliation'
    if code in {'new_contact_capacity_reached', 'recipient_limit'}:
        return 'waiting_capacity'
    if code in {'ProfileBusyError', 'live_guard_busy', 'delivery_executor_busy', 'local_store_busy', 'account_busy', 'first_catalog_account_busy',
                'workflow_resource_busy', 'workflow_stage_already_claimed', 'sample_account_in_use', 'first_resource_child_running'}:
        return 'waiting_account'
    return 'attention'


def worker_tick(root, *, stopped=lambda: False):
    job = _claim(root)
    if job:
        try:
            return process_job(root, job, stopped=stopped)
        except Exception as error:
            code = _code(error)
            if _wait_state(code) in {'waiting_account', 'waiting_capacity'}:
                _publish(root, _wait_state(code), code, job['request_id'])
                return {'state': _wait_state(code), 'reason': code}
            _job_update(root, job, state='error', error=code)
            _publish(root, 'attention', code, job['request_id'])
            return {'state': 'attention', 'reason': code}
    cfg = _cfg(root)
    result = {'state': 'stopped'}
    send_unknown = _send_unknown(root)
    if not stopped() and (cfg['mode'] != 'stopped' or send_unknown):
        try:
            _publish(root, 'waiting_reconciliation' if send_unknown else 'sending')
            result = _send(root, 'first-worker-' + uuid.uuid4().hex, reconcile_only=bool(send_unknown))
            after = _cfg(root)
            state = 'canary_confirmed' if cfg['mode'] == 'canary' and after['mode'] == 'stopped' and after.get('canaryDeliveryId') else result.get('state', 'idle')
            _publish(root, state, result.get('stopReason') or result.get('reason'))
            result = {**result, 'state': state}
        except Exception as error:
            code = _code(error)
            result = {'state': _wait_state(code), 'reason': code}
            _publish(root, result['state'], code)
    if not stopped() and _pending_samples(root):
        try:
            _reconcile_samples(root, stopped)
        except Exception as error:
            _publish(root, 'waiting_reconciliation', _code(error))
        return result
    cfg = _cfg(root)
    if not stopped() and cfg['approvalsEnabled']:
        with CycleStore(_path(root), readonly=True) as store:
            row = store.db.execute('SELECT last_sample_sync FROM first_outreach_runtime WHERE plan_id=?', (cfg['planId'],)).fetchone()
            due = not row or time.time() - row[0] >= 60
        if due:
            approval_stopped = lambda: stopped() or not _cfg(root)['approvalsEnabled']
            try:
                samples.sync(root, 'br', stopped=approval_stopped)
                _publish(root, result['state'], result.get('reason'), sample_sync=True)
                samples.run_one(root, 'br', stopped=approval_stopped)
            except Exception as error:
                _publish(root, 'attention', _code(error), sample_sync=True)
    return result


def worker(root, *, once=False, stopped=None, interval=5):
    root = Path(root)
    _path(root)
    shutdown = [False]
    def stop(*_):
        shutdown[0] = True
    check = lambda: shutdown[0] or bool(stopped and stopped())
    if stopped is None:
        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
    with (root / 'var/first-outreach-br.lock').open('a') as lock:
        released = False
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {'state': 'busy', 'platformWrites': 0}
        from lib.runtime_release import register
        register(root, 'first-outreach-br')
        started = time.time()
        _publish(root, 'starting', started_at=started)
        try:
            while not check():
                _record_worker(root, {'pid': os.getpid(), 'startedAt': started, 'checkedAt': time.time()})
                result = worker_tick(root, stopped=check)
                if once:
                    break
                # Share the launch mutex for the final idle check. A newly registered
                # request either keeps this worker alive or launches after its OS lock
                # was released; the tiny process-exit window cannot strand the job.
                with (root / 'var/first-outreach-launch-br.lock').open('a') as launch_lock:
                    fcntl.flock(launch_lock, fcntl.LOCK_EX)
                    cfg = _cfg(root)
                    with CycleStore(_path(root), readonly=True) as store:
                        queued = store.db.execute("SELECT 1 FROM first_outreach_job WHERE state IN ('queued','running') LIMIT 1").fetchone()
                    if not queued and cfg['mode'] == 'stopped' and not cfg['approvalsEnabled'] and not _send_unknown(root) and not _pending_samples(root):
                        fcntl.flock(lock, fcntl.LOCK_UN)
                        _record_worker(root, {'pid': None, 'startedAt': started, 'checkedAt': time.time(), 'state': 'stopped'})
                        released = True
                        break
                delay = 0.5 if result.get('realSends') else 60 if result.get('state') == 'waiting_reconciliation' else interval
                until = time.monotonic() + delay
                while not check() and time.monotonic() < until:
                    time.sleep(min(0.25, max(0, until - time.monotonic())))
        finally:
            if not released:
                with (root / 'var/first-outreach-launch-br.lock').open('a') as launch_lock:
                    fcntl.flock(launch_lock, fcntl.LOCK_EX)
                    fcntl.flock(lock, fcntl.LOCK_UN)
                    _record_worker(root, {'pid': None, 'startedAt': started, 'checkedAt': time.time(), 'state': 'stopped'})
        return {'state': 'stopped'}
