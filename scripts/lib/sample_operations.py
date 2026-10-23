"""Persistent BR first-outreach sample approval ledger in second-cycle.sqlite.

Only an explicit page control authorizes new reviews. Reads, capability evidence
and code deployment cannot grant that authorization. Unknown intents are read only.
"""
from __future__ import annotations

import fcntl
import json
from pathlib import Path
import uuid

from lib.second_cycle import CycleError, CycleStore, digest, encoded, epoch
from lib.sample_transport import review_payload, sample_session, validate_contract

SCHEMA = '''
CREATE TABLE IF NOT EXISTS sample_capability(market TEXT NOT NULL,account_name TEXT NOT NULL,state TEXT NOT NULL,evidence_ref TEXT NOT NULL,observed REAL NOT NULL,binding TEXT,contract TEXT,PRIMARY KEY(market,account_name));
CREATE TABLE IF NOT EXISTS sample_sync(id TEXT PRIMARY KEY,market TEXT NOT NULL,account_name TEXT NOT NULL,observed REAL NOT NULL,evidence_ref TEXT NOT NULL,record_count INTEGER NOT NULL,complete INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS sample_application(market TEXT NOT NULL,application_id TEXT NOT NULL,account_name TEXT NOT NULL,observed REAL NOT NULL,evidence_ref TEXT NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(market,application_id));
CREATE TABLE IF NOT EXISTS sample_review_intent(id TEXT PRIMARY KEY,market TEXT NOT NULL,application_id TEXT NOT NULL,account_name TEXT NOT NULL,request_ref TEXT NOT NULL UNIQUE,state TEXT NOT NULL,created REAL NOT NULL,started REAL,binding TEXT NOT NULL,application TEXT NOT NULL,payload TEXT NOT NULL,contract TEXT NOT NULL,quota_proof TEXT NOT NULL,offer TEXT NOT NULL,delivery_id TEXT NOT NULL,receipt TEXT,confirmation TEXT,lease_owner TEXT,fence INTEGER NOT NULL DEFAULT 0,lease_until REAL NOT NULL DEFAULT 0,UNIQUE(market,application_id));
CREATE TABLE IF NOT EXISTS sample_review_check(id INTEGER PRIMARY KEY AUTOINCREMENT,intent_id TEXT NOT NULL,observed REAL NOT NULL,payload TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS sample_review_pending ON sample_review_intent(market,state,created);
CREATE TRIGGER IF NOT EXISTS sample_intent_frozen BEFORE UPDATE OF market,application_id,account_name,request_ref,binding,application,payload,contract,quota_proof,offer,delivery_id ON sample_review_intent BEGIN SELECT RAISE(ABORT,'immutable sample intent'); END;
'''
PENDING_INTENTS = ('inflight', 'accepted', 'unknown')
MAX_FACT_AGE = 900


def require_market(market):
    if market != 'br':
        raise CycleError('sample_market_not_supported')


def ensure_schema(store):
    store.db.executescript(SCHEMA)


def authorize(store, market, enabled, event_id, *, root=None, expected_revision=None):
    """Compatibility wrapper around the one first-outreach page authorization."""
    from lib.first_outreach import control, change_control
    require_market(market)
    current = control(store, market)
    return change_control(store, root, market, event_id, 'set-approvals',
                          current['revision'] if expected_revision is None else expected_revision,
                          approvals_enabled=enabled)


def record_capability(store, market, account_name, state, evidence_ref, *, contract=None, binding=None):
    """Store verified canary evidence; no approval authorization is created here."""
    require_market(market)
    if state not in {'unknown', 'readonly', 'verified', 'blocked'} or not isinstance(evidence_ref, str) or not evidence_ref:
        raise CycleError('sample_capability_invalid')
    if state == 'verified':
        contract = validate_contract(contract)
        if not isinstance(binding, dict) or binding.get('market') != market or binding.get('account') != account_name or \
                not binding.get('institutionFingerprint') or not binding.get('identitySha256'):
            raise CycleError('sample_capability_binding_missing')
    ensure_schema(store)
    with store.tx():
        store.db.execute('INSERT INTO sample_capability VALUES(?,?,?,?,?,?,?) ON CONFLICT(market,account_name) DO UPDATE SET state=excluded.state,evidence_ref=excluded.evidence_ref,observed=excluded.observed,binding=excluded.binding,contract=excluded.contract',
                         (market, account_name, state, evidence_ref, store.clock(), encoded(binding) if binding else None, encoded(contract) if contract else None))


def capability(store, market, account_name):
    row = store.db.execute('SELECT * FROM sample_capability WHERE market=? AND account_name=?', (market, account_name)).fetchone()
    if not row:
        return {'state': 'unknown', 'reason': 'sample_review_contract_unverified', 'account': account_name}
    return dict(row) | {'contract': json.loads(row['contract']) if row['contract'] else None,
                        'binding': json.loads(row['binding']) if row['binding'] else None}


def store_records(store, market, account_name, records, *, observed_at=None, evidence_ref, complete=False):
    require_market(market)
    if type(complete) is not bool or not isinstance(records, list) or len(records) > 10000:
        raise CycleError('sample_sync_invalid')
    at = store.clock() if observed_at is None else epoch(observed_at)
    if at > store.clock() + 30 or not isinstance(evidence_ref, str) or not evidence_ref:
        raise CycleError('sample_sync_invalid')
    ids = [r.get('applicationId') for r in records]
    if any(not isinstance(v, str) or not v.isascii() or not v.isdigit() for v in ids) or len(ids) != len(set(ids)):
        raise CycleError('sample_sync_duplicate_or_invalid_identity')
    ensure_schema(store)
    sid = 'sample-sync-' + digest([market, account_name, at, evidence_ref])[:32]
    with store.tx():
        for row in records:
            prior = store.db.execute('SELECT account_name,observed,payload FROM sample_application WHERE market=? AND application_id=?', (market, row['applicationId'])).fetchone()
            # Account ownership is immutable for an intent, even if another account can see the record.
            if prior and prior['account_name'] != account_name:
                continue
            if prior and prior['observed'] > at:
                continue
            if prior and prior['observed'] == at and prior['payload'] != encoded(row):
                raise CycleError('sample_observation_conflict')
            store.db.execute('INSERT INTO sample_application VALUES(?,?,?,?,?,?) ON CONFLICT(market,application_id) DO UPDATE SET observed=excluded.observed,evidence_ref=excluded.evidence_ref,payload=excluded.payload',
                             (market, row['applicationId'], account_name, at, evidence_ref, encoded(row)))
        store.db.execute('INSERT OR IGNORE INTO sample_sync VALUES(?,?,?,?,?,?,?)', (sid, market, account_name, at, evidence_ref, len(records), int(complete)))
    return {'syncId': sid, 'records': len(records), 'complete': complete, 'platformWrites': 0}


def query_records(store, market='br', limit=100):
    require_market(market)
    if type(limit) is not int or not 1 <= limit <= 500:
        raise CycleError('sample_query_limit_invalid')
    if not _has_table(store.db, 'sample_application'):
        return []
    rows = store.db.execute('SELECT a.*,i.id intent_id,i.state intent_state FROM sample_application a LEFT JOIN sample_review_intent i ON i.market=a.market AND i.application_id=a.application_id WHERE a.market=? ORDER BY a.observed DESC,a.application_id LIMIT ?', (market, limit)).fetchall()
    return [{'application': json.loads(r['payload']), 'account': r['account_name'], 'observedAt': r['observed'],
             'evidenceRef': r['evidence_ref'], 'intentId': r['intent_id'], 'reviewState': r['intent_state']} for r in rows]


def _has_table(db, name):
    return bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone())


def status(root, market='br'):
    """No mkdir, schema installation, authorization or platform access on a status read."""
    require_market(market)
    result = {'market': market, 'enabled': False, 'capability': {'state': 'unknown', 'reason': 'sample_review_contract_unverified'},
              'counts': {}, 'applicationCounts': {}, 'paymentNeedsReview': 0, 'records': [], 'platformWrites': 0}
    path = Path(root) / 'var/second-cycle.sqlite'
    if not path.is_file():
        return result
    with CycleStore(path, readonly=True) as store:
        from lib.first_outreach import control
        try:
            cfg = control(store, market)
            result.update(enabled=cfg['approvalsEnabled'], revision=cfg['revision'])
        except CycleError:
            pass
        if not _has_table(store.db, 'sample_capability'):
            return result
        rows = store.db.execute('SELECT account_name,state,evidence_ref,observed FROM sample_capability WHERE market=?', (market,)).fetchall()
        result['capabilities'] = [dict(r) for r in rows]
        verified = next((r for r in rows if r['state'] == 'verified'), None)
        if verified:
            result['capability'] = {'state': 'verified', 'account': verified['account_name'], 'evidenceRef': verified['evidence_ref']}
        elif rows:
            result['capability'] = {'state': rows[-1]['state'], 'reason': 'sample_review_contract_unverified', 'evidenceRef': rows[-1]['evidence_ref']}
        result['counts'] = {r[0]: r[1] for r in store.db.execute('SELECT state,count(*) FROM sample_review_intent WHERE market=? GROUP BY state', (market,))}
        result['applicationCounts'] = {r[0]: r[1] for r in store.db.execute("SELECT coalesce(json_extract(payload,'$.state'),'unknown'),count(*) FROM sample_application WHERE market=? GROUP BY 1", (market,))}
        result['paymentNeedsReview'] = store.db.execute("SELECT count(*) FROM sample_application WHERE market=? AND json_extract(payload,'$.state')='pending' AND (json_type(payload,'$.paymentRequired') IS NOT 'false')", (market,)).fetchone()[0]
        result['records'] = query_records(store, market)
    return result


def _account(root, market):
    from lib.market_accounts import load_config
    return load_config(root)['markets'][market]['roles']['supply']


def _enabled(store, market):
    from lib.first_outreach import control
    try:
        cfg = control(store, market)
        return cfg['approvalsEnabled'] and store._plan(cfg['planId'])['state'] == 'active'
    except CycleError:
        return False


def _assess(store, root, market, application):
    from lib.first_outreach import assess_application
    return assess_application(store, root, market, application)


def _pages(adapter, *, max_pages=20):
    records = []
    total = None
    refs = []
    seen = set()
    for page in range(1, max_pages + 1):
        data = adapter.read_page(page)
        if total is not None and total != data['total']:
            raise CycleError('sample_sync_total_changed')
        total = data['total']
        refs.append(data['evidenceRef'])
        for row in data['records']:
            if row['applicationId'] in seen:
                raise CycleError('sample_sync_duplicate_record')
            records.append(row)
            seen.add(row['applicationId'])
        if len(records) == total:
            return records, True, 'sample-pages:' + digest(refs)
        if len(records) > total or len(data['records']) < data['pageSize']:
            raise CycleError('sample_sync_incomplete')
    return records, False, 'sample-pages:' + digest(refs)


def sync(root, market='br', *, transport_factory=None, stopped=lambda: False):
    require_market(market)
    root = Path(root)
    factory = transport_factory or sample_session
    account = _account(root, market)
    report = {'market': market, 'platformWrites': 0}
    with CycleStore(root / 'var/second-cycle.sqlite') as store:
        ensure_schema(store)
        cap = capability(store, market, account)
        contract = cap.get('contract') if cap['state'] == 'verified' else None
        with factory(root, market, account, report, contract=contract, stopped=stopped) as adapter:
            rows, complete, ref = _pages(adapter)
        result = store_records(store, market, account, rows, evidence_ref=ref, complete=complete)
        # A read proves the read endpoint only. Never promote review capability here.
        if cap['state'] == 'unknown':
            record_capability(store, market, account, 'readonly', ref)
        return report | result | {'reviewCapability': cap['state']}


def _intent(store, id):
    row = store.db.execute('SELECT * FROM sample_review_intent WHERE id=?', (id,)).fetchone()
    if not row:
        raise CycleError('sample_intent_missing')
    return dict(row)


def _cancel_ready(store, market, application_id, reason):
    with store.tx():
        row = store.db.execute("SELECT id FROM sample_review_intent WHERE market=? AND application_id=? AND state='ready'", (market, application_id)).fetchone()
        if row:
            store.db.execute("UPDATE sample_review_intent SET state='cancelled' WHERE id=? AND state='ready'", (row['id'],))
            store.db.execute('INSERT INTO sample_review_check(intent_id,observed,payload) VALUES(?,?,?)',
                             (row['id'], store.clock(), encoded({'status': 'cancelled_before_dispatch', 'reason': reason, 'platformWrites': 0})))


def _settle_unsubmitted(store, market, records, *, complete):
    current = {row['applicationId']: row for row in records}
    ready = store.db.execute("SELECT application_id,application FROM sample_review_intent WHERE market=? AND state='ready'", (market,)).fetchall()
    for row in ready:
        actual = current.get(row['application_id'])
        if actual is not None and actual != json.loads(row['application']) or actual is None and complete:
            _cancel_ready(store, market, row['application_id'], 'application_changed_before_dispatch')


def _identity(application):
    return tuple(application.get(k) for k in ('applicationId', 'oecId', 'platformCreatorId', 'pid', 'campaignId'))


def _prepare(store, root, market, account, application, binding, *, contract, quota_proof, assessor=_assess):
    """Atomic sample reservation and duplicate intent protection, using current domain rules."""
    id = 'sample-review-' + digest([market, application['applicationId']])[:32]
    with store.tx():
        old = store.db.execute('SELECT id FROM sample_review_intent WHERE market=? AND application_id=?', (market, application['applicationId'])).fetchone()
        prior = _intent(store, old[0]) if old else None
        if prior and prior['state'] != 'ready':
            return prior, False
        if prior and (json.loads(prior['application']) != application or json.loads(prior['binding']) != binding or json.loads(prior['contract']) != contract):
            store.db.execute("UPDATE sample_review_intent SET state='cancelled' WHERE id=? AND state='ready'", (prior['id'],))
            return prior, False
        if not _enabled(store, market):
            raise CycleError('sample_approval_paused')
        if application.get('state') != 'pending':
            raise CycleError('sample_application_not_pending')
        if application.get('paymentRequired') is not False:
            raise CycleError('sample_payment_requires_handling')
        payload = review_payload(application)
        assessed = assessor(store, root, market, application)
        if not assessed.get('eligible'):
            raise CycleError(assessed.get('reason') or 'sample_application_ineligible')
        offer = assessed['offer']
        _check_application_duplicates(store, market, application)
        _quota_capacity(store, market, application, quota_proof, exclude_id=id)
        if prior:
            if digest(json.loads(prior['offer'])) != digest(offer) or prior['delivery_id'] != assessed['deliveryId']:
                store.db.execute("UPDATE sample_review_intent SET state='cancelled' WHERE id=? AND state='ready'", (prior['id'],))
                return prior, False
            return prior, True
        store.db.execute('INSERT INTO sample_review_intent(id,market,application_id,account_name,request_ref,state,created,binding,application,payload,contract,quota_proof,offer,delivery_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                         (id, market, application['applicationId'], account, str(uuid.uuid4()), 'ready', store.clock(), encoded(binding), encoded(application), encoded(payload), encoded(contract), encoded(quota_proof), encoded(offer), assessed['deliveryId']))
    return _intent(store, id), True


def _check_application_duplicates(store, market, application):
    # A new application ID is not permission to sample the same triple again.
    for row in store.db.execute("SELECT application_id,payload FROM sample_application WHERE market=? AND application_id<>?", (market, application['applicationId'])):
        observed = json.loads(row['payload'])
        if (observed.get('oecId'), observed.get('pid'), observed.get('campaignId')) == (application.get('oecId'), application.get('pid'), application.get('campaignId')) and observed.get('state') in {'pending', 'seller_pending', 'approved', 'shipped', 'completed'}:
            raise CycleError('sample_duplicate_application')


def _quota_capacity(store, market, application, proof, *, exclude_id):
    if not isinstance(proof, dict) or (proof.get('pid'), proof.get('campaignId')) != (application.get('pid'), application.get('campaignId')):
        raise CycleError('sample_quota_identity_mismatch')
    try:
        at = epoch(proof.get('observedAt'))
    except (ValueError, TypeError, AttributeError, OverflowError):
        raise CycleError('sample_quota_unverified') from None
    quota = proof.get('quota')
    if proof.get('supported') is not True or proof.get('available') is not True or type(quota) is not int or quota <= 0 or \
            at > store.clock() + 30 or store.clock() - at > MAX_FACT_AGE or not proof.get('evidenceRef'):
        raise CycleError('sample_quota_unverified')
    used = 0
    for row in store.db.execute("SELECT id,state,started,application FROM sample_review_intent WHERE market=? AND state IN ('ready','inflight','accepted','unknown','seller_pending','approved')", (market,)):
        previous = json.loads(row['application'])
        if row['id'] != exclude_id and (previous.get('oecId'), previous.get('pid'), previous.get('campaignId')) == (application.get('oecId'), application.get('pid'), application.get('campaignId')):
            raise CycleError('sample_duplicate_intent')
        if row['id'] != exclude_id and (previous.get('pid'), previous.get('campaignId')) == (application['pid'], application['campaignId']) and \
                (row['state'] in ('ready',) + PENDING_INTENTS or (row['started'] or 0) >= at):
            used += 1
    if used >= quota:
        raise CycleError('sample_quota_reserved')


def _begin(store, intent, application, *, quota_proof=None, root=None, assessor=None, stopped=lambda: False):
    with store.tx():
        current = _intent(store, intent['id'])
        if current['state'] != 'ready':
            raise CycleError('sample_intent_already_attempted')
        if stopped() or not _enabled(store, intent['market']):
            raise CycleError('sample_approval_paused')
        if application != json.loads(current['application']):
            raise CycleError('sample_application_changed')
        if assessor is not None:
            assessed = assessor(store, root, intent['market'], application)
            if not assessed.get('eligible') or assessed.get('deliveryId') != current['delivery_id'] or digest(assessed.get('offer')) != digest(json.loads(current['offer'])):
                raise CycleError('sample_facts_changed')
        _check_application_duplicates(store, intent['market'], application)
        proof = quota_proof if quota_proof is not None else json.loads(current['quota_proof'])
        _quota_capacity(store, intent['market'], application, proof, exclude_id=current['id'])
        owner = str(uuid.uuid4())
        store.db.execute('INSERT INTO sample_review_check(intent_id,observed,payload) VALUES(?,?,?)',
                         (current['id'], store.clock(), encoded({'status': 'quota_revalidated', 'quota': proof})))
        store.db.execute("UPDATE sample_review_intent SET state='inflight',started=?,lease_owner=?,fence=fence+1,lease_until=? WHERE id=? AND state='ready'", (store.clock(), owner, store.clock() + 90, current['id']))
    return _intent(store, intent['id'])


def _confirm_from_records(store, intent, rows, evidence_ref):
    original = json.loads(intent['application'])
    matches = [r for r in rows if r.get('applicationId') == original['applicationId']]
    outcome = 'unknown'
    if len(matches) == 1 and _identity(matches[0]) == _identity(original):
        stage = matches[0].get('state')
        if stage in {'approved', 'shipped', 'completed'}:
            outcome = 'approved'
        elif stage == 'seller_pending':
            outcome = 'seller_pending'
        elif stage in {'rejected', 'cancelled'}:
            outcome = 'closed_externally'
    proof = {'status': outcome, 'evidenceRef': evidence_ref, 'applicationId': original['applicationId']}
    with store.tx():
        store.db.execute('INSERT INTO sample_review_check(intent_id,observed,payload) VALUES(?,?,?)', (intent['id'], store.clock(), encoded(proof)))
        store.db.execute('UPDATE sample_review_intent SET state=?,confirmation=?,lease_owner=NULL,lease_until=0 WHERE id=?', (outcome, encoded(proof), intent['id']))
    return outcome


def reconcile(store, root, intent, *, transport_factory=None, stopped=lambda: False):
    """Original account, binding and application only; absence/pending never causes a replay."""
    factory = transport_factory or sample_session
    contract = validate_contract(json.loads(intent['contract']))
    report = {'platformWrites': 0}
    with factory(root, intent['market'], intent['account_name'], report, contract=contract,
                 expected_binding=json.loads(intent['binding']), stopped=stopped) as adapter:
        rows, _, evidence = _pages(adapter)
    store_records(store, intent['market'], intent['account_name'], rows, evidence_ref=evidence, complete=False)
    state = _confirm_from_records(store, intent, rows, evidence)
    return report | {'state': state, 'intentId': intent['id'], 'platformWrites': 0}


def run_one(root, market='br', *, transport_factory=None, stopped=lambda: False, assessor=_assess):
    require_market(market)
    root = Path(root)
    factory = transport_factory or sample_session
    (root / 'var').mkdir(parents=True, exist_ok=True)
    with (root / f'var/sample-review-{market}.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {'state': 'busy', 'platformWrites': 0}
        with CycleStore(root / 'var/second-cycle.sqlite') as store:
            ensure_schema(store)
            # Unknown results have priority over pause and do not require a new write authorization.
            pending = store.db.execute("SELECT * FROM sample_review_intent WHERE market=? AND state IN ('inflight','accepted','unknown') ORDER BY created LIMIT 1", (market,)).fetchone()
            if not pending:
                pending = store.db.execute("SELECT i.* FROM sample_review_intent i WHERE i.market=? AND i.state='seller_pending' AND coalesce((SELECT max(c.observed) FROM sample_review_check c WHERE c.intent_id=i.id),0)<? ORDER BY i.created LIMIT 1", (market, store.clock() - 60)).fetchone()
            if pending:
                return reconcile(store, root, dict(pending), transport_factory=factory, stopped=stopped)
            if stopped() or not _enabled(store, market):
                return {'state': 'paused', 'platformWrites': 0}
            account = _account(root, market)
            cap = capability(store, market, account)
            if cap['state'] != 'verified':
                return {'state': 'capability_unverified', 'reason': 'sample_review_contract_unverified', 'platformWrites': 0}
            validate_contract(cap['contract'])
            report = {'platformWrites': 0}
            # Read application facts and reserve the exact intent before opening a write scope.
            with factory(root, market, account, report, contract=cap['contract'], expected_binding=cap['binding'], stopped=stopped) as reader:
                rows, complete, evidence = _pages(reader)
                store_records(store, market, account, rows, evidence_ref=evidence, complete=complete)
                _settle_unsubmitted(store, market, rows, complete=complete)
                selected = None
                waiting = []
                for application in rows:
                    if application.get('state') != 'pending':
                        continue
                    try:
                        if application.get('paymentRequired') is not False:
                            raise CycleError('sample_payment_requires_handling')
                        assessed = assessor(store, root, market, application)
                        if not assessed.get('eligible'):
                            raise CycleError(assessed.get('reason') or 'sample_application_ineligible')
                        quota_proof = reader.refresh_sample_quota(assessed['offer'])
                        selected, created = _prepare(store, root, market, account, application, reader.binding, contract=cap['contract'], quota_proof=quota_proof, assessor=assessor)
                    except (CycleError, ValueError) as error:
                        _cancel_ready(store, market, application['applicationId'], str(error))
                        waiting.append({'applicationId': application['applicationId'], 'reason': str(error)})
                        continue
                    if not created:
                        selected = None
                        continue
                    break
            if selected is None:
                return report | {'state': 'idle', 'waiting': waiting}
            # Reacquire the same account with a frozen write scope. The second read
            # closes the gap between the readonly session and the write session.
            with factory(root, market, account, report, contract=cap['contract'], frozen=json.loads(selected['payload']),
                         expected_binding=json.loads(selected['binding']), stopped=lambda: stopped() or not _enabled(store, market)) as writer:
                fresh, _, evidence = _pages(writer)
                store_records(store, market, account, fresh, evidence_ref=evidence, complete=False)
                original = json.loads(selected['application'])
                matches = [r for r in fresh if r.get('applicationId') == original['applicationId']]
                if len(matches) != 1 or matches[0] != original:
                    with store.tx():
                        store.db.execute("UPDATE sample_review_intent SET state='cancelled' WHERE id=? AND state='ready'", (selected['id'],))
                    return report | {'state': 'application_changed', 'intentId': selected['id']}
                assessed = assessor(store, root, market, original)
                if not assessed.get('eligible') or assessed.get('deliveryId') != selected['delivery_id'] or digest(assessed.get('offer')) != digest(json.loads(selected['offer'])):
                    with store.tx():
                        store.db.execute("UPDATE sample_review_intent SET state='cancelled' WHERE id=? AND state='ready'", (selected['id'],))
                    return report | {'state': 'facts_changed', 'intentId': selected['id']}
                quota_proof = writer.refresh_sample_quota(assessed['offer'])
                selected = _begin(store, selected, matches[0], quota_proof=quota_proof, root=root, assessor=assessor, stopped=stopped)
                try:
                    receipt = writer.approve_once(json.loads(selected['payload']))
                    state = {'accepted': 'accepted', 'rejected': 'rejected'}.get(receipt.get('status'), 'unknown')
                    with store.tx():
                        store.db.execute('UPDATE sample_review_intent SET state=?,receipt=?,lease_owner=NULL,lease_until=0 WHERE id=?', (state, encoded(receipt), selected['id']))
                    if state == 'rejected':
                        return report | {'state': 'rejected', 'intentId': selected['id']}
                    fresh, _, evidence = _pages(writer)
                    store_records(store, market, account, fresh, evidence_ref=evidence, complete=False)
                    state = _confirm_from_records(store, selected, fresh, evidence)
                    return report | {'state': state, 'intentId': selected['id']}
                except BaseException:
                    with store.tx():
                        store.db.execute("UPDATE sample_review_intent SET state='unknown',lease_owner=NULL,lease_until=0 WHERE id=? AND state IN ('inflight','accepted')", (selected['id'],))
                    raise
