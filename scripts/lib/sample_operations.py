"""Persistent BR first-outreach sample approval ledger in second-cycle.sqlite.

Only an explicit page control authorizes new reviews. Reads, capability evidence
and code deployment cannot grant that authorization. Unknown intents are read only.
"""
from __future__ import annotations

from copy import deepcopy
import fcntl
import json
from pathlib import Path
import uuid

from lib.second_cycle import CycleError, CycleStore, digest, encoded, epoch
from lib.sample_transport import (CONTRACT_SCHEMA, READ_CONTRACT_SCHEMA, CANARY_CONTRACT_SCHEMA, READ_PATHS, OBSERVED_READ_BODY, build_canary_contract,
                                 observed_status_evidence, extend_canary_contract, validate_status_evidence, receipt_accepted, scope_snapshot, derive_status_transition, STATUS_EVIDENCE_SCHEMA,
                                 review_payload, sample_session, validate_contract, validate_read_contract,
                                 validate_canary_contract, validate_execution_contract, validate_any_contract,
                                 require_current_cost_policy, record_contract_fingerprint)
from lib.sample_cost_policy import load_cost_policy, payment_basis

SCHEMA = '''
CREATE TABLE IF NOT EXISTS sample_capability(market TEXT NOT NULL,account_name TEXT NOT NULL,state TEXT NOT NULL,evidence_ref TEXT NOT NULL,observed REAL NOT NULL,binding TEXT,contract TEXT,PRIMARY KEY(market,account_name));
CREATE TABLE IF NOT EXISTS sample_sync(id TEXT PRIMARY KEY,market TEXT NOT NULL,account_name TEXT NOT NULL,observed REAL NOT NULL,evidence_ref TEXT NOT NULL,record_count INTEGER NOT NULL,complete INTEGER NOT NULL,binding_fingerprint TEXT,contract_fingerprint TEXT,read_scope TEXT,platform_counts TEXT);
CREATE TABLE IF NOT EXISTS sample_sync_member(sync_id TEXT NOT NULL,application_id TEXT NOT NULL,PRIMARY KEY(sync_id,application_id));
CREATE TABLE IF NOT EXISTS sample_application(market TEXT NOT NULL,application_id TEXT NOT NULL,account_name TEXT NOT NULL,observed REAL NOT NULL,evidence_ref TEXT NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(market,application_id));
CREATE TABLE IF NOT EXISTS sample_review_intent(id TEXT PRIMARY KEY,market TEXT NOT NULL,application_id TEXT NOT NULL,account_name TEXT NOT NULL,request_ref TEXT NOT NULL UNIQUE,state TEXT NOT NULL,created REAL NOT NULL,started REAL,binding TEXT NOT NULL,application TEXT NOT NULL,payload TEXT NOT NULL,contract TEXT NOT NULL,quota_proof TEXT NOT NULL,offer TEXT NOT NULL,delivery_id TEXT NOT NULL,receipt TEXT,confirmation TEXT,lease_owner TEXT,fence INTEGER NOT NULL DEFAULT 0,lease_until REAL NOT NULL DEFAULT 0,UNIQUE(market,application_id));
CREATE TABLE IF NOT EXISTS sample_reconcile_attempt(intent_id TEXT NOT NULL,attempt_number INTEGER NOT NULL,started REAL NOT NULL,outcome TEXT,PRIMARY KEY(intent_id,attempt_number));
CREATE TABLE IF NOT EXISTS sample_review_check(id INTEGER PRIMARY KEY AUTOINCREMENT,intent_id TEXT NOT NULL,observed REAL NOT NULL,payload TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS sample_review_pending ON sample_review_intent(market,state,created);
CREATE TRIGGER IF NOT EXISTS sample_intent_frozen BEFORE UPDATE OF market,application_id,account_name,request_ref,binding,application,payload,contract,quota_proof,offer,delivery_id ON sample_review_intent BEGIN SELECT RAISE(ABORT,'immutable sample intent'); END;
'''
PENDING_INTENTS = ('inflight', 'accepted', 'unknown', 'quarantined_unknown')
MIN_RECONCILE_INTERVAL = 60
MAX_UNKNOWN_CHECKS = 3
MAX_FACT_AGE = 900
APPROVAL_STAGES = frozenset({'approved', 'ready_to_ship', 'shipped', 'content_pending', 'completed'})


def require_market(market):
    if market != 'br':
        raise CycleError('sample_market_not_supported')


def ensure_schema(store):
    store.db.executescript(SCHEMA)
    columns = {row['name'] for row in store.db.execute('PRAGMA table_info(sample_sync)')}
    for column in ('binding_fingerprint', 'contract_fingerprint', 'read_scope', 'platform_counts'):
        if column not in columns:
            store.db.execute(f'ALTER TABLE sample_sync ADD COLUMN {column} TEXT')


def authorize(store, market, enabled, event_id, *, root=None, expected_revision=None):
    """Compatibility wrapper around the one first-outreach page authorization."""
    from lib.first_outreach import control, change_control
    require_market(market)
    current = control(store, market)
    return change_control(store, root, market, event_id, 'set-approvals',
                          current['revision'] if expected_revision is None else expected_revision,
                          approvals_enabled=enabled)


def record_capability(store, market, account_name, state, evidence_ref, *, contract=None, binding=None):
    """Store the account-bound protocol phase; never create approval authorization."""
    require_market(market)
    if state not in {'unknown', 'readonly', 'canary', 'verified', 'blocked'} or not isinstance(evidence_ref, str) or not evidence_ref:
        raise CycleError('sample_capability_invalid')
    if state in {'verified', 'readonly', 'canary'}:
        validator = {'verified': validate_contract, 'readonly': validate_read_contract, 'canary': validate_canary_contract}[state]
        contract = validator(contract) if state == 'verified' else validator(contract, binding=binding)
        if not isinstance(binding, dict) or binding.get('market') != market or binding.get('account') != account_name or \
                not binding.get('institutionFingerprint') or not binding.get('identitySha256'):
            raise CycleError('sample_capability_binding_missing')
    ensure_schema(store)
    with store.tx():
        store.db.execute('INSERT INTO sample_capability VALUES(?,?,?,?,?,?,?) ON CONFLICT(market,account_name) DO UPDATE SET state=excluded.state,evidence_ref=excluded.evidence_ref,observed=excluded.observed,binding=excluded.binding,contract=excluded.contract',
                         (market, account_name, state, evidence_ref, store.clock(), encoded(binding) if binding else None, encoded(contract) if contract else None))


def arm_canary(store, root, market, account_name, *, pending_ui_evidence_ref, request_candidate_ref):
    """Register one-shot capability; existing page approval authorization stays separate."""
    require_market(market)
    ensure_schema(store)
    current = capability(store, market, account_name)
    if current['state'] == 'canary':
        validate_canary_contract(current['contract'], binding=current['binding'])
        require_current_cost_policy(root, current['contract'])
        return current | {'duplicate': True}
    if current['state'] != 'readonly':
        raise CycleError('sample_canary_requires_read_contract')
    policy = load_cost_policy(root)
    contract = build_canary_contract(current['contract'], policy, pending_ui_evidence_ref=pending_ui_evidence_ref,
                                     request_candidate_ref=request_candidate_ref)
    record_capability(store, market, account_name, 'canary', pending_ui_evidence_ref,
                      contract=contract, binding=current['binding'])
    return capability(store, market, account_name) | {'duplicate': False}


def promote_canary(root, market, account_name, verified_contract, *, transport_factory=None, stopped=lambda: False):
    """Promote only after an accepted canary and a real exact terminal lifecycle read.

    The supplied full contract must cite newly obtained review/lifecycle evidence;
    the bootstrap contract itself never invents those missing semantics.
    """
    require_market(market)
    verified = validate_contract(verified_contract)
    root = Path(root)
    factory = transport_factory or sample_session
    with (root / f'var/sample-review-{market}.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with CycleStore(root / 'var/second-cycle.sqlite') as store:
            ensure_schema(store)
            current = capability(store, market, account_name)
            if current['state'] != 'canary':
                raise CycleError('sample_canary_not_armed')
            validate_canary_contract(current['contract'], binding=current['binding'])
            _require_lifecycle_extension(current['contract'], verified)
            require_current_cost_policy(root, current['contract'])
            started = _started_canary(store, market, current['binding'])
            if not started:
                raise CycleError('sample_canary_not_started')
            intent = _intent(store, started['id'])
            receipt = json.loads(intent['receipt']) if intent['receipt'] else {}
            if not receipt_accepted(receipt) or not receipt.get('evidenceRef'):
                raise CycleError('sample_canary_review_unconfirmed')
            verified['costPolicy'] = deepcopy(current['contract']['costPolicy'])
            report = {'platformWrites': 0}
            with factory(root, market, account_name, report, contract=verified,
                         expected_binding=json.loads(intent['binding']), stopped=stopped) as reader:
                records, _, evidence = _pages(reader)
            original = json.loads(intent['application'])
            matches = [row for row in records if _identity(row) == _identity(original)]
            if len(matches) != 1 or matches[0].get('nativeStatus') == original.get('nativeStatus') or matches[0].get('state') not in {'approved', 'shipped', 'completed'}:
                return report | {'state': 'waiting_canary_verification', 'reason': 'sample_canary_lifecycle_unconfirmed', 'intentId': intent['id']}
            _confirm_from_records(store, intent, matches, evidence, lifecycle_contract=verified)
            record_capability(store, market, account_name, 'verified', evidence,
                              contract=verified, binding=current['binding'])
            return report | {'state': 'verified', 'intentId': intent['id']}


def capability(store, market, account_name):
    row = store.db.execute('SELECT * FROM sample_capability WHERE market=? AND account_name=?', (market, account_name)).fetchone()
    if not row:
        return {'state': 'unknown', 'reason': 'sample_review_contract_unverified', 'account': account_name}
    return dict(row) | {'contract': json.loads(row['contract']) if row['contract'] else None,
                        'binding': json.loads(row['binding']) if row['binding'] else None}


def store_records(store, market, account_name, records, *, observed_at=None, evidence_ref, complete=False, binding=None, contract=None,
                  read_scope=None, platform_counts=None):
    require_market(market)
    if type(complete) is not bool or not isinstance(records, list) or len(records) > 10000:
        raise CycleError('sample_sync_invalid')
    at = store.clock() if observed_at is None else epoch(observed_at)
    if at > store.clock() + 30 or not isinstance(evidence_ref, str) or not evidence_ref:
        raise CycleError('sample_sync_invalid')
    if read_scope not in {None, 'all_states'} or platform_counts is not None and (
            not isinstance(platform_counts, dict) or any(not isinstance(k, str) or not k or type(v) is not int or v < 0
                                                       for k, v in platform_counts.items())):
        raise CycleError('sample_sync_invalid')
    if platform_counts is not None and (read_scope != 'all_states' or not complete or sum(platform_counts.values()) != len(records)):
        raise CycleError('sample_sync_counts_incomplete')
    ids = [r.get('applicationId') for r in records]
    if any(not isinstance(v, str) or not v.isascii() or not v.isdigit() for v in ids) or len(ids) != len(set(ids)):
        raise CycleError('sample_sync_duplicate_or_invalid_identity')
    ensure_schema(store)
    sid = 'sample-sync-' + digest([market, account_name, at, evidence_ref])[:32]
    with store.tx():
        binding_fingerprint = contract_fingerprint = None
        if complete and (binding is not None or contract is not None):
            current = capability(store, market, account_name)
            if current['state'] not in {'verified', 'readonly', 'canary'} or current.get('binding') != binding or current.get('contract') != contract or at < current['observed']:
                raise CycleError('sample_sync_provenance_mismatch')
            if current['state'] in {'readonly', 'canary'}:
                validate_any_contract(contract, binding=binding)
                if read_scope != contract['readScope']:
                    raise CycleError('sample_sync_provenance_mismatch')
            else:
                validate_contract(contract)
            # Sync provenance binds the full authority, including costPolicy.
            # Application row fingerprints describe the platform schema separately.
            binding_fingerprint, contract_fingerprint = digest(binding), digest(contract)
            if current['state'] in {'readonly', 'canary'} and any(row.get('contractFingerprint') != record_contract_fingerprint(contract) or
                    any(not isinstance(row.get(key), str) or not row[key].isascii() or not row[key].isdigit() or int(row[key]) <= 0
                        for key in ('applicationId', 'platformCreatorId', 'oecId', 'pid', 'campaignId')) for row in records):
                raise CycleError('sample_sync_provenance_mismatch')
        elif read_scope is not None or platform_counts is not None:
            raise CycleError('sample_sync_provenance_mismatch')
        for row in records:
            prior = store.db.execute('SELECT account_name,observed,payload FROM sample_application WHERE market=? AND application_id=?', (market, row['applicationId'])).fetchone()
            # Account ownership is immutable for an intent, even if another account can see the record.
            if prior and prior['account_name'] != account_name:
                if binding_fingerprint is not None:
                    raise CycleError('sample_sync_provenance_mismatch')
                continue
            if prior and prior['observed'] > at:
                continue
            if prior and prior['observed'] == at and prior['payload'] != encoded(row):
                raise CycleError('sample_observation_conflict')
            store.db.execute('INSERT INTO sample_application VALUES(?,?,?,?,?,?) ON CONFLICT(market,application_id) DO UPDATE SET observed=excluded.observed,evidence_ref=excluded.evidence_ref,payload=excluded.payload',
                             (market, row['applicationId'], account_name, at, evidence_ref, encoded(row)))
        store.db.execute('INSERT OR IGNORE INTO sample_sync(id,market,account_name,observed,evidence_ref,record_count,complete,binding_fingerprint,contract_fingerprint,read_scope,platform_counts) VALUES(?,?,?,?,?,?,?,?,?,?,?)', (sid, market, account_name, at, evidence_ref, len(records), int(complete), binding_fingerprint, contract_fingerprint, read_scope, encoded(platform_counts) if platform_counts is not None else None))
        if complete:
            store.db.executemany('INSERT OR IGNORE INTO sample_sync_member VALUES(?,?)', [(sid, application_id) for application_id in ids])
    _passive_confirm(store, market, records, evidence_ref, binding=binding)
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


def read_snapshot(store, market='br', *, account_name=None):
    """Latest all-state snapshot whose immutable membership matches its source proof.

    This checks ledger provenance only. Callers claiming live coverage must also
    check the current identity files and observation age without a platform write.
    """
    require_market(market)
    if not _has_table(store.db, 'sample_sync') or not _has_table(store.db, 'sample_capability'):
        return None
    columns = {row['name'] for row in store.db.execute('PRAGMA table_info(sample_sync)')}
    if not {'read_scope', 'platform_counts', 'binding_fingerprint', 'contract_fingerprint'} <= columns:
        return None
    suffix = ' AND account_name=?' if account_name else ''
    rows = store.db.execute("SELECT * FROM sample_sync WHERE market=? AND complete=1 AND read_scope='all_states'" + suffix + ' ORDER BY observed DESC,id DESC',
                            (market, account_name) if account_name else (market,)).fetchall()
    for row in rows:
        current = capability(store, market, row['account_name'])
        if current['state'] not in {'readonly', 'verified', 'canary'} or row['observed'] < current['observed'] or \
                row['binding_fingerprint'] != digest(current.get('binding')) or row['contract_fingerprint'] != digest(current.get('contract')):
            continue
        try:
            if current['state'] in {'readonly', 'canary'}:
                validate_any_contract(current['contract'], binding=current['binding'])
            else:
                validate_contract(current['contract'])
            counters = json.loads(row['platform_counts']) if row['platform_counts'] else None
        except (CycleError, ValueError, TypeError):
            continue
        members = store.db.execute('SELECT count(*) FROM sample_sync_member WHERE sync_id=?', (row['id'],)).fetchone()[0]
        if members != row['record_count'] or counters is None or not isinstance(counters, dict) or \
                any(not isinstance(k, str) or not k or type(v) is not int or v < 0 for k, v in counters.items()) or sum(counters.values()) != members:
            continue
        return {'complete': True, 'total': row['record_count'], 'observedAt': row['observed'], 'evidenceRef': row['evidence_ref'],
                'account': row['account_name'], 'readScope': row['read_scope'], 'syncId': row['id'],
                'bindingFingerprint': row['binding_fingerprint'], 'contractFingerprint': row['contract_fingerprint'],
                'platformCounts': counters}
    return None


def tracking(store, market='br', limit=100):
    """Read the original intent's observed fulfilment/publication facts, without new actions."""
    require_market(market)
    if not _has_table(store.db, 'sample_review_intent'):
        return []
    result = []
    for row in store.db.execute("SELECT * FROM sample_review_intent WHERE market=? AND started IS NOT NULL ORDER BY created DESC LIMIT ?", (market, limit)):
        observation = store.db.execute("SELECT observed,payload FROM sample_review_check WHERE intent_id=? AND json_extract(payload,'$.kind')='lifecycle' ORDER BY id DESC LIMIT 1", (row['id'],)).fetchone()
        facts = json.loads(observation['payload']) if observation else {}
        result.append({'intentId': row['id'], 'applicationId': row['application_id'], 'account': row['account_name'],
                       'reviewState': row['state'], 'platformStage': facts.get('platformStage', 'unknown'), 'nativeStatus': facts.get('nativeStatus'),
                       'receiptConfirmed': facts.get('receiptConfirmed'), 'possibleExternalCompletion': row['state'] == 'goal_observed',
                       'isPosted': facts.get('isPosted'), 'videoCount': facts.get('videoCount'), 'liveCount': facts.get('liveCount'),
                       'observedAt': observation['observed'] if observation else None, 'evidenceRef': facts.get('evidenceRef')})
    return result


def has_followups(root, market='br'):
    require_market(market)
    path = Path(root) / 'var/second-cycle.sqlite'
    if not path.is_file():
        return False
    with CycleStore(path, readonly=True) as store:
        if not _has_table(store.db, 'sample_review_intent'):
            return False
        for row in store.db.execute("SELECT id,state FROM sample_review_intent WHERE market=? AND state IN ('inflight','accepted','unknown','quarantined_unknown','seller_pending','approved','goal_observed')", (market,)):
            if row['state'] not in {'approved', 'goal_observed'}:
                return True
            last = store.db.execute("SELECT payload FROM sample_review_check WHERE intent_id=? AND json_extract(payload,'$.kind')='lifecycle' ORDER BY id DESC LIMIT 1", (row['id'],)).fetchone()
            if not last or json.loads(last[0]).get('platformStage') not in {'completed', 'cancelled', 'rejected'}:
                return True
    return False


def status(root, market='br'):
    """No mkdir, schema installation, authorization or platform access on a status read."""
    require_market(market)
    result = {'market': market, 'enabled': False, 'capability': {'state': 'unknown', 'reason': 'sample_review_contract_unverified'},
              'counts': {}, 'applicationCounts': {}, 'paymentNeedsReview': 0, 'records': [], 'platformWrites': 0,
              'platformCounts': {}, 'readCoverage': {'complete': False}, 'tracking': [], 'lifecycleCounts': {}}
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
        result['tracking'] = tracking(store, market)
        for item in result['tracking']:
            stage = item['platformStage']
            result['lifecycleCounts'][stage] = result['lifecycleCounts'].get(stage, 0) + 1
        snapshot = read_snapshot(store, market)
        if snapshot:
            result['platformCounts'] = snapshot['platformCounts']
            result['readCoverage'] = {k: v for k, v in snapshot.items() if k != 'platformCounts'}
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


def _pages(adapter, *, max_pages=20, summary=None):
    records = []
    total = None
    refs = []
    seen = set()
    counters = None
    scope = None
    for page in range(1, max_pages + 1):
        data = adapter.read_page(page)
        if total is not None and total != data['total']:
            raise CycleError('sample_sync_total_changed')
        total = data['total']
        if page == 1:
            counters, scope = data.get('platformCounts'), data.get('readScope')
        elif counters != data.get('platformCounts') or scope != data.get('readScope'):
            raise CycleError('sample_sync_counters_changed')
        refs.append(data['evidenceRef'])
        for row in data['records']:
            if row['applicationId'] in seen:
                raise CycleError('sample_sync_duplicate_record')
            records.append(row)
            seen.add(row['applicationId'])
        if len(records) == total:
            if summary is not None:
                summary.update(readScope=scope, platformCounts=counters)
            return records, True, 'sample-pages:' + digest(refs)
        if len(records) > total or len(data['records']) < data['pageSize']:
            raise CycleError('sample_sync_incomplete')
    return records, False, 'sample-pages:' + digest(refs)


def _refresh_status_evidence(store, root, market, account, cap):
    if cap['state'] != 'canary' or cap['contract'].get('statusEvidence'):
        return cap
    try:
        proof = observed_status_evidence(root, cap['binding'])
    except CycleError as error:
        if str(error) == 'sample_read_evidence_binding_mismatch':
            return cap
        raise
    if proof is None:
        return cap
    updated = extend_canary_contract(cap['contract'], proof)
    if updated != cap['contract']:
        record_capability(store, market, account, 'canary', cap['evidence_ref'], contract=updated, binding=cap['binding'])
        return capability(store, market, account)
    return cap


def _reader_contract(store, intent):
    frozen = json.loads(intent['contract'])
    current = capability(store, intent['market'], intent['account_name'])
    if current['state'] in {'canary', 'verified'} and current.get('binding') == json.loads(intent['binding']):
        try:
            _require_lifecycle_extension(frozen, current['contract'])
            return current['contract']
        except CycleError:
            pass
    return frozen


def _auto_promote(store, root, market, account):
    cap = capability(store, market, account)
    if cap['state'] != 'canary' or not cap['contract'].get('statusEvidence'):
        return False
    require_current_cost_policy(root, cap['contract'])
    started = _started_canary(store, market, cap['binding'])
    if not started:
        return False
    intent = _intent(store, started['id'])
    receipt = json.loads(intent['receipt']) if intent['receipt'] else {}
    confirmation = json.loads(intent['confirmation']) if intent['confirmation'] else {}
    if intent['state'] != 'approved' or not receipt_accepted(receipt) or not receipt.get('evidenceRef') or confirmation.get('platformStage') not in APPROVAL_STAGES or not confirmation.get('evidenceRef'):
        return False
    frozen = json.loads(intent['contract'])
    verified = deepcopy(cap['contract'])
    verified.update(schema=CONTRACT_SCHEMA, reviewSuccessCode=0)
    verified['evidence'].update(payment='user-policy:' + digest(frozen['costPolicy']), review=receipt['evidenceRef'], lifecycle=confirmation['evidenceRef'])
    validate_contract(verified)
    _require_lifecycle_extension(frozen, verified)
    record_capability(store, market, account, 'verified', confirmation['evidenceRef'], contract=verified, binding=cap['binding'])
    return True


def _record_scope(store, market, account, binding, records, summary, evidence_ref, *, complete, anchor_id=None):
    current = scope_snapshot(binding, records, summary.get('platformCounts'), evidence_ref, complete=complete)
    if current is None:
        return capability(store, market, account)['contract']
    cap = capability(store, market, account)
    if cap['state'] not in {'canary', 'verified'} or cap.get('binding') != binding:
        return cap.get('contract')
    previous = store.db.execute("SELECT c.intent_id,c.payload FROM sample_review_check c JOIN sample_review_intent i ON i.id=c.intent_id WHERE i.market=? AND i.account_name=? AND i.binding=? AND json_extract(c.payload,'$.kind')='scope_snapshot' ORDER BY c.id DESC LIMIT 1",
                                (market, account, encoded(binding))).fetchone()
    before = json.loads(previous['payload'])['scope'] if previous else None
    if before is not None:
        observation = derive_status_transition(before, current, cap['contract']['statusMap'])
        if observation and str(observation['nativeStatus']) not in cap['contract']['statusMap']:
            target = store.db.execute('SELECT id,contract FROM sample_review_intent WHERE market=? AND account_name=? AND binding=? AND application_id=? AND started IS NOT NULL',
                                     (market, account, encoded(binding), observation['applicationId'])).fetchone()
            if target:
                proof = deepcopy(cap['contract'].get('statusEvidence') or {'schema': STATUS_EVIDENCE_SCHEMA, 'market': market, 'binding': binding, 'observations': []})
                proof['observations'].append(observation)
                statuses = validate_status_evidence(proof, binding=binding)
                updated = extend_canary_contract(cap['contract'], proof) if cap['state'] == 'canary' else deepcopy(cap['contract'])
                if cap['state'] == 'verified':
                    updated.update(statusEvidence=proof, statusMap=updated['statusMap'] | statuses)
                    validate_contract(updated)
                _require_lifecycle_extension(json.loads(target['contract']), updated)
                record_capability(store, market, account, cap['state'], 'sample-status-delta:' + digest(observation), contract=updated, binding=binding)
                store.db.execute('INSERT INTO sample_review_check(intent_id,observed,payload) VALUES(?,?,?)',
                                 (target['id'], store.clock(), encoded({'kind': 'status_mapping', 'observation': observation})))
                cap = capability(store, market, account)
    anchor = anchor_id or (previous['intent_id'] if previous else None)
    if anchor:
        semantic = lambda scope: {key: value for key, value in scope.items() if key != 'evidenceRef'}
        if before is None or semantic(before) != semantic(current):
            store.db.execute('INSERT INTO sample_review_check(intent_id,observed,payload) VALUES(?,?,?)',
                             (anchor, store.clock(), encoded({'kind': 'scope_snapshot', 'scope': current})))
    return cap['contract']


def _remap_records(records, contract):
    return [row | {'state': contract.get('statusMap', {}).get(str(row.get('nativeStatus')), 'unknown'),
                   'contractFingerprint': record_contract_fingerprint(contract)} for row in records]


def _rebind_reader(store, root, market, account, cap, factory, report, stopped):
    summary = {}
    with factory(root, market, account, report, contract=None, expected_binding=None, stopped=stopped,
                 expected_institution_fingerprint=(cap.get('binding') or {}).get('institutionFingerprint')) as adapter:
        rows, complete, reference = _pages(adapter, summary=summary)
        binding = adapter.binding
    old = cap.get('binding') or {}
    if not complete or summary.get('readScope') != 'all_states' or summary.get('platformCounts') is None or any(binding.get(key) != old.get(key) for key in ('market', 'account', 'institutionFingerprint')):
        raise CycleError('sample_reader_rebind_unverified')
    if any(any(not isinstance(row.get(key), str) or not row[key].isascii() or not row[key].isdigit() or int(row[key]) <= 0
               for key in ('applicationId', 'platformCreatorId', 'oecId', 'pid', 'campaignId')) or type(row.get('nativeStatus')) is not int for row in rows):
        raise CycleError('sample_reader_rebind_fields_unverified')
    read = validate_read_contract({'schema': READ_CONTRACT_SCHEMA, 'market': market, 'readScope': 'all_states',
        'recordPaths': deepcopy(READ_PATHS), 'readBody': deepcopy(OBSERVED_READ_BODY), 'statusCountersPath': 'sample_status_num_map',
        'evidence': {'records': reference, 'creatorIdentity': reference}, 'binding': binding}, binding=binding)
    contract, state = read, 'readonly'
    prior = cap.get('contract') or {}
    policy = load_cost_policy(root)
    if cap['state'] in {'canary', 'verified'} and policy is not None and prior.get('statusMap', {}).get('10') == 'pending' and all(prior.get('recordPaths', {}).get(key) == value for key, value in READ_PATHS.items()):
        contract = build_canary_contract(read, policy,
            pending_ui_evidence_ref=(prior.get('evidence') or {}).get('pendingUi') or 'prior-verified-contract:' + digest(prior),
            request_candidate_ref=(prior.get('evidence') or {}).get('requestCandidate') or prior['evidence']['review'])
        if prior.get('statusEvidence'):
            evidence = deepcopy(prior['statusEvidence'])
            evidence.update(sourceBinding=evidence.get('sourceBinding', evidence['binding']), binding=binding, revalidatedBy=reference)
            contract = extend_canary_contract(contract, evidence)
        state = 'canary'
    record_capability(store, market, account, state, reference, contract=contract, binding=binding)
    rows = [row | {'state': contract.get('statusMap', {}).get(str(row['nativeStatus']), 'unknown'),
                    'contractFingerprint': record_contract_fingerprint(contract)} for row in rows]
    result = store_records(store, market, account, rows, evidence_ref=reference, complete=True, binding=binding,
                           contract=contract, read_scope='all_states', platform_counts=summary['platformCounts'])
    return report | result | {'reviewCapability': state, 'readerRebound': True, 'promoted': False, 'platformCounts': summary['platformCounts']}


def sync(root, market='br', *, transport_factory=None, stopped=lambda: False):
    require_market(market)
    root = Path(root)
    factory = transport_factory or sample_session
    account = _account(root, market)
    report = {'market': market, 'platformWrites': 0}
    with CycleStore(root / 'var/second-cycle.sqlite') as store:
        ensure_schema(store)
        cap = _refresh_status_evidence(store, root, market, account, capability(store, market, account))
        contract = cap.get('contract') if cap['state'] in {'verified', 'readonly', 'canary'} else None
        summary = {}
        try:
            with factory(root, market, account, report, contract=contract, expected_binding=cap.get('binding') if contract else None, stopped=stopped) as adapter:
                rows, complete, ref = _pages(adapter, summary=summary)
                actual_contract = getattr(adapter, 'contract', contract)
                binding = adapter.binding if actual_contract else None
                if contract and (binding != cap.get('binding') or actual_contract != contract):
                    raise CycleError('sample_sync_provenance_mismatch')
        except CycleError as error:
            if str(error) == 'sample_original_identity_changed' and cap.get('binding'):
                return _rebind_reader(store, root, market, account, cap, factory, report, stopped)
            raise
        if actual_contract and actual_contract.get('schema') == READ_CONTRACT_SCHEMA:
            validate_read_contract(actual_contract, binding=binding)
            if cap['state'] != 'verified' and (cap.get('contract') != actual_contract or cap.get('binding') != binding):
                record_capability(store, market, account, 'readonly', ref, contract=actual_contract, binding=binding)
            contract = actual_contract
        elif actual_contract:
            validate_any_contract(actual_contract, binding=binding)
        if actual_contract and cap['state'] in {'canary', 'verified'}:
            learned_contract = _record_scope(store, market, account, binding, rows, summary, ref, complete=complete)
            if learned_contract != actual_contract:
                actual_contract = learned_contract
                rows = _remap_records(rows, actual_contract)
        result = store_records(store, market, account, rows, evidence_ref=ref, complete=complete, binding=binding, contract=actual_contract,
                               read_scope=summary.get('readScope'),
                               platform_counts=summary.get('platformCounts') if summary.get('readScope') == 'all_states' else None)
        # List success alone cannot promote. A persisted original POST receipt plus
        # the exact application's proven positive lifecycle observation can.
        promoted = _auto_promote(store, root, market, account)
        if promoted:
            verified = capability(store, market, account)['contract']
            rows = [row | {'contractFingerprint': record_contract_fingerprint(verified)} for row in rows]
            result = store_records(store, market, account, rows, evidence_ref=ref + ':promoted', complete=complete,
                binding=binding, contract=verified, read_scope=summary.get('readScope'),
                platform_counts=summary.get('platformCounts') if summary.get('readScope') == 'all_states' else None)
        return report | result | {'reviewCapability': capability(store, market, account)['state'],
                                  'promoted': promoted, 'platformCounts': summary.get('platformCounts') or {}}


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


def _started_canary(store, market, binding, *, exclude_id=None, unsettled=False):
    # A normal relogin cannot reset an unresolved canary's account/institution slot.
    if unsettled:
        owner = "account_name=? AND json_extract(binding,'$.institutionFingerprint')=?"
        args = (binding.get('account'), binding.get('institutionFingerprint'))
        condition = " AND state NOT IN ('goal_observed','closed_externally','rejected','cancelled')"
    else:
        owner, args, condition = 'binding=?', (encoded(binding),), ''
    return store.db.execute("SELECT id,state FROM sample_review_intent WHERE market=? AND " + owner +
                            " AND started IS NOT NULL AND json_extract(contract,'$.schema')=? AND id<>?" + condition +
                            " ORDER BY created DESC,id DESC LIMIT 1",
                            (market, *args, CANARY_CONTRACT_SCHEMA, exclude_id or '')).fetchone()


def _require_canary_slot(store, market, contract, binding, *, exclude_id=None):
    if contract.get('schema') == CANARY_CONTRACT_SCHEMA and _started_canary(store, market, binding, exclude_id=exclude_id, unsettled=True):
        raise CycleError('sample_canary_already_started')


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
        validate_execution_contract(contract, binding=binding)
        policy = require_current_cost_policy(root, contract)
        payment_basis(application, policy, now=store.clock())
        _require_canary_slot(store, market, contract, binding, exclude_id=id)
        payload = review_payload(application, policy)
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
        if (observed.get('oecId'), observed.get('pid'), observed.get('campaignId')) == (application.get('oecId'), application.get('pid'), application.get('campaignId')) and observed.get('state') in {'unknown', 'pending', 'seller_pending', 'approved', 'ready_to_ship', 'shipped', 'content_pending', 'completed'}:
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
    for row in store.db.execute("SELECT id,state,started,application FROM sample_review_intent WHERE market=? AND state IN ('ready','inflight','accepted','unknown','quarantined_unknown','seller_pending','approved','goal_observed')", (market,)):
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
        if current['state'] != 'ready' or current['started'] is not None:
            raise CycleError('sample_intent_already_attempted')
        if stopped() or not _enabled(store, intent['market']):
            raise CycleError('sample_approval_paused')
        if application != json.loads(current['application']):
            raise CycleError('sample_application_changed')
        contract = json.loads(current['contract'])
        policy = require_current_cost_policy(root, contract)
        payment_basis(application, policy, now=store.clock())
        _require_canary_slot(store, intent['market'], contract, json.loads(current['binding']), exclude_id=current['id'])
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


def _require_lifecycle_extension(frozen, verified):
    validate_execution_contract(verified)
    if frozen.get('schema') == CANARY_CONTRACT_SCHEMA and (
            any(verified['recordPaths'].get(key) != value for key, value in frozen['recordPaths'].items()) or
            any(verified['statusMap'].get(key) != value for key, value in frozen['statusMap'].items())):
        raise CycleError('sample_canary_lifecycle_contract_mismatch')


def _confirm_from_records(store, intent, rows, evidence_ref, *, lifecycle_contract=None):
    original = json.loads(intent['application'])
    matches = [r for r in rows if r.get('applicationId') == original['applicationId']]
    outcome = 'unknown'
    frozen_contract = json.loads(intent['contract'])
    lifecycle_known = frozen_contract.get('schema') != CANARY_CONTRACT_SCHEMA or bool(frozen_contract.get('statusEvidence'))
    if lifecycle_contract is None:
        current = _reader_contract(store, intent)
        if current != frozen_contract:
            lifecycle_contract = current
    if lifecycle_contract is not None:
        _require_lifecycle_extension(frozen_contract, lifecycle_contract)
        lifecycle_known = lifecycle_contract.get('schema') != CANARY_CONTRACT_SCHEMA or bool(lifecycle_contract.get('statusEvidence'))
    if lifecycle_known and len(matches) == 1 and _identity(matches[0]) == _identity(original):
        stage = matches[0].get('state')
        if stage in APPROVAL_STAGES:
            receipt = json.loads(intent['receipt']) if intent.get('receipt') else {}
            acknowledged = receipt_accepted(receipt)
            outcome = 'approved' if acknowledged else 'goal_observed'
        elif stage == 'seller_pending':
            outcome = 'seller_pending'
        elif stage in {'rejected', 'cancelled'}:
            outcome = 'closed_externally'
    if outcome == 'unknown' and intent['state'] in {'approved', 'goal_observed', 'quarantined_unknown'}:
        outcome = intent['state']
    actual = matches[0] if len(matches) == 1 and _identity(matches[0]) == _identity(original) else {}
    receipt = json.loads(intent['receipt']) if intent.get('receipt') else {}
    receipt_confirmed = receipt_accepted(receipt)
    proof = {'kind': 'lifecycle', 'status': outcome, 'evidenceRef': evidence_ref, 'applicationId': original['applicationId'],
             'platformStage': actual.get('state', 'unknown'), 'nativeStatus': actual.get('nativeStatus'),
             'receiptConfirmed': receipt_confirmed, 'possibleExternalCompletion': outcome == 'goal_observed',
             'isPosted': actual.get('isPosted'), 'videoCount': actual.get('videoCount'), 'liveCount': actual.get('liveCount')}
    with store.tx():
        previous = store.db.execute("SELECT payload FROM sample_review_check WHERE intent_id=? AND json_extract(payload,'$.kind')='lifecycle' ORDER BY id DESC LIMIT 1", (intent['id'],)).fetchone()
        before = json.loads(previous[0]) if previous else {}
        if {k:v for k,v in before.items() if k != 'evidenceRef'} != {k:v for k,v in proof.items() if k != 'evidenceRef'}:
            store.db.execute('INSERT INTO sample_review_check(intent_id,observed,payload) VALUES(?,?,?)', (intent['id'], store.clock(), encoded(proof)))
        store.db.execute('UPDATE sample_review_intent SET state=?,confirmation=?,lease_owner=NULL,lease_until=0 WHERE id=?', (outcome, encoded(proof), intent['id']))
    return outcome


def _passive_confirm(store, market, records, evidence_ref, *, binding=None):
    """A normal exact lifecycle observation may settle a quarantined original intent."""
    current = {row['applicationId']: row for row in records}
    for row in store.db.execute("SELECT * FROM sample_review_intent WHERE market=? AND state IN ('inflight','accepted','unknown','quarantined_unknown','seller_pending','approved','goal_observed')", (market,)).fetchall():
        actual = current.get(row['application_id'])
        if binding != json.loads(row['binding']):
            continue
        reader_contract = _reader_contract(store, dict(row))
        if actual and _identity(actual) == _identity(json.loads(row['application'])) and (actual.get('state') in APPROVAL_STAGES | {'seller_pending', 'rejected', 'cancelled'} or row['state'] in {'approved', 'goal_observed'}) and actual.get('contractFingerprint') == record_contract_fingerprint(reader_contract):
            _confirm_from_records(store, dict(row), [actual], evidence_ref, lifecycle_contract=reader_contract)


def _quarantine(store, intent, reason):
    with store.tx():
        store.db.execute("UPDATE sample_review_intent SET state='quarantined_unknown',lease_owner=NULL,lease_until=0 WHERE id=? AND state IN ('inflight','accepted','unknown')", (intent['id'],))
        store.db.execute('INSERT INTO sample_review_check(intent_id,observed,payload) VALUES(?,?,?)',
                         (intent['id'], store.clock(), encoded({'status': 'quarantined_unknown', 'reason': reason, 'platformWrites': 0})))
    return {'state': 'quarantined_unknown', 'reason': reason, 'intentId': intent['id'], 'platformWrites': 0}


def reconcile(store, root, intent, *, transport_factory=None, stopped=lambda: False):
    """Original intent only; durable finite unknown checks never replay a review."""
    factory = transport_factory or sample_session
    current = _intent(store, intent['id'])
    if current['state'] == 'quarantined_unknown':
        return {'state': 'quarantined_unknown', 'intentId': intent['id'], 'platformWrites': 0}
    is_unknown = current['state'] in ('inflight', 'accepted', 'unknown')
    attempt = None
    if is_unknown:
        with store.tx():
            row = store.db.execute('SELECT count(*) count,max(started) last_started FROM sample_reconcile_attempt WHERE intent_id=?', (intent['id'],)).fetchone()
            count, last_started = row['count'], row['last_started']
            if last_started is None:
                previous_read = store.db.execute('SELECT max(observed) FROM sample_review_check WHERE intent_id=?', (intent['id'],)).fetchone()[0]
                last_started = max(previous_read or 0, current['started'] or current['created'])
            if count < MAX_UNKNOWN_CHECKS and (last_started is None or store.clock() - last_started >= MIN_RECONCILE_INTERVAL):
                attempt = count + 1
                # The attempt is spent before auth or any platform read; failures consume it too.
                store.db.execute('INSERT INTO sample_reconcile_attempt VALUES(?,?,?,NULL)', (intent['id'], attempt, store.clock()))
        if count >= MAX_UNKNOWN_CHECKS:
            return _quarantine(store, intent, 'sample_reconciliation_budget_exhausted')
        if attempt is None:
            return {'state': 'waiting_reconciliation', 'reason': 'sample_reconciliation_cooldown',
                    'intentId': intent['id'], 'nextCheckAt': last_started + MIN_RECONCILE_INTERVAL, 'platformWrites': 0}
    report = {'platformWrites': 0}
    try:
        contract = validate_execution_contract(_reader_contract(store, intent))
        with factory(root, intent['market'], intent['account_name'], report, contract=contract,
                     expected_binding=json.loads(intent['binding']), stopped=stopped) as adapter:
            summary = {}
            rows, complete, evidence = _pages(adapter, summary=summary)
        learned_contract = _record_scope(store, intent['market'], intent['account_name'], json.loads(intent['binding']), rows, summary, evidence, complete=complete, anchor_id=intent['id'])
        if learned_contract and learned_contract != contract and capability(store, intent['market'], intent['account_name']).get('binding') == json.loads(intent['binding']):
            rows = _remap_records(rows, learned_contract)
        store_records(store, intent['market'], intent['account_name'], rows, evidence_ref=evidence, complete=False, binding=json.loads(intent['binding']))
        state = _confirm_from_records(store, _intent(store, intent['id']), rows, evidence)
        if attempt is not None:
            store.db.execute('UPDATE sample_reconcile_attempt SET outcome=? WHERE intent_id=? AND attempt_number=?', (state, intent['id'], attempt))
        if state == 'unknown' and attempt == MAX_UNKNOWN_CHECKS:
            return report | _quarantine(store, intent, 'sample_reconciliation_budget_exhausted')
        _auto_promote(store, root, intent['market'], intent['account_name'])
        return report | {'state': state, 'intentId': intent['id'], 'platformWrites': 0}
    except Exception as error:
        if str(error) in {'sample_original_identity_changed', 'sample_identity_changed'}:
            if attempt is not None:
                store.db.execute('UPDATE sample_reconcile_attempt SET outcome=? WHERE intent_id=? AND attempt_number=?', ('original_identity_unavailable', intent['id'], attempt))
            return report | _quarantine(store, intent, 'sample_original_identity_changed') | {'state': 'waiting_original_identity'}
        if attempt is not None:
            with store.tx():
                store.db.execute('UPDATE sample_reconcile_attempt SET outcome=? WHERE intent_id=? AND attempt_number=?', ('read_failed:' + type(error).__name__, intent['id'], attempt))
                store.db.execute("UPDATE sample_review_intent SET state='unknown',lease_owner=NULL,lease_until=0 WHERE id=? AND state IN ('inflight','accepted','unknown')", (intent['id'],))
            if attempt == MAX_UNKNOWN_CHECKS:
                return report | _quarantine(store, intent, 'sample_reconciliation_budget_exhausted')
        else:
            # Seller waiting is ordinary lifecycle polling, with its own 60-second clock.
            store.db.execute('INSERT INTO sample_review_check(intent_id,observed,payload) VALUES(?,?,?)',
                             (intent['id'], store.clock(), encoded({'status': 'seller_pending', 'reason': 'sample_read_failed'})))
        return report | {'state': 'unknown' if is_unknown else 'seller_pending', 'reason': 'sample_read_failed',
                         'intentId': intent['id'], 'platformWrites': 0}


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
            if cap['state'] not in {'verified', 'canary'}:
                return {'state': 'capability_unverified', 'reason': 'sample_review_contract_unverified', 'platformWrites': 0}
            contract = validate_execution_contract(cap['contract'], binding=cap['binding'])
            if cap['state'] == 'canary':
                require_current_cost_policy(root, contract)
                if _started_canary(store, market, cap['binding'], unsettled=True):
                    return {'state': 'waiting_canary_verification', 'reason': 'sample_canary_already_started', 'platformWrites': 0}
            else:
                policy = load_cost_policy(root)
                if policy is not None:
                    contract['costPolicy'] = policy
                require_current_cost_policy(root, contract)
            report = {'platformWrites': 0}
            # Read application facts and reserve the exact intent before opening a write scope.
            with factory(root, market, account, report, contract=contract, expected_binding=cap['binding'], stopped=stopped) as reader:
                rows, complete, evidence = _pages(reader)
                if not complete:
                    return report | {'state': 'waiting_sample_sync', 'reason': 'sample_sync_incomplete'}
                store_records(store, market, account, rows, evidence_ref=evidence, complete=complete)
                _settle_unsubmitted(store, market, rows, complete=complete)
                selected = None
                waiting = []
                for application in rows:
                    if application.get('state') != 'pending':
                        continue
                    try:
                        payment_basis(application, contract.get('costPolicy'), now=store.clock())
                        assessed = assessor(store, root, market, application)
                        if not assessed.get('eligible'):
                            raise CycleError(assessed.get('reason') or 'sample_application_ineligible')
                        quota_proof = reader.refresh_sample_quota(assessed['offer'])
                        selected, created = _prepare(store, root, market, account, application, reader.binding, contract=contract, quota_proof=quota_proof, assessor=assessor)
                    except (CycleError, ValueError) as error:
                        _cancel_ready(store, market, application['applicationId'], str(error))
                        waiting.append({'applicationId': application['applicationId'], 'reason': str(error)})
                        continue
                    if not created:
                        selected = None
                        continue
                    break
            if selected is None:
                return report | {'state': 'waiting_eligible_application' if cap['state'] == 'canary' else 'idle', 'reason': 'sample_no_eligible_first_application' if cap['state'] == 'canary' else None, 'waiting': waiting}
            # Reacquire the same account with a frozen write scope. The second read
            # closes the gap between the readonly session and the write session.
            with factory(root, market, account, report, contract=json.loads(selected['contract']), frozen=json.loads(selected['payload']),
                         expected_binding=json.loads(selected['binding']), stopped=lambda: stopped() or not _enabled(store, market)) as writer:
                before_summary = {}
                fresh, before_complete, evidence = _pages(writer, summary=before_summary)
                if not before_complete:
                    return report | {'state': 'waiting_sample_sync', 'reason': 'sample_sync_incomplete', 'intentId': selected['id']}
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
                _record_scope(store, market, account, json.loads(selected['binding']), fresh, before_summary, evidence, complete=before_complete, anchor_id=selected['id'])
                selected = _begin(store, selected, matches[0], quota_proof=quota_proof, root=root, assessor=assessor, stopped=stopped)
                try:
                    receipt = writer.approve_once(json.loads(selected['payload']))
                    state = {'accepted': 'accepted', 'rejected': 'rejected'}.get(receipt.get('status'), 'unknown')
                    with store.tx():
                        store.db.execute('UPDATE sample_review_intent SET state=?,receipt=?,lease_owner=NULL,lease_until=0 WHERE id=?', (state, encoded(receipt), selected['id']))
                    if state == 'rejected':
                        return report | {'state': 'rejected', 'intentId': selected['id']}
                    after_summary = {}
                    fresh, after_complete, evidence = _pages(writer, summary=after_summary)
                    learned_contract = _record_scope(store, market, account, json.loads(selected['binding']), fresh, after_summary, evidence, complete=after_complete, anchor_id=selected['id'])
                    if learned_contract and learned_contract != json.loads(selected['contract']):
                        fresh = _remap_records(fresh, learned_contract)
                    store_records(store, market, account, fresh, evidence_ref=evidence, complete=False)
                    state = _confirm_from_records(store, _intent(store, selected['id']), fresh, evidence)
                    _auto_promote(store, root, market, account)
                    return report | {'state': state, 'intentId': selected['id']}
                except BaseException:
                    with store.tx():
                        store.db.execute("UPDATE sample_review_intent SET state='unknown',lease_owner=NULL,lease_until=0 WHERE id=? AND state IN ('inflight','accepted')", (selected['id'],))
                    raise
