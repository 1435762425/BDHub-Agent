"""Account-affine sample reads and one frozen review request.

The BR review contract is deliberately absent by default. A successful empty list
is evidence for reading that list, never proof of creator IDs, payment or approval.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
import re
import sqlite3
import time
from pathlib import Path

from lib.second_cycle import CycleError, digest

LIST = '/api/v1/affiliate/partner/sample/records/list'
CAMPAIGNS = '/api/v1/affiliate/partner/campaign/list'
PRODUCTS = '/api/v1/affiliate/partner/campaign/product/list'
REVIEW = '/api/v1/affiliate/partner/campaign/sample/budget/review'
# Current BR all-state read, observed with acc2 on 2026-10-23 (22 records).
# The native numeric status is retained without inventing its lifecycle meaning.
OBSERVED_READ_BODY = {'search_params': [],
                      'order_params': [{'order_key': 9, 'order_type': 2}], 'page_size': 20, 'cur_page': 1}
CONTRACT_SCHEMA = 'bdhub.br-sample-contract.v1'
READ_CONTRACT_SCHEMA = 'bdhub.br-sample-read-contract.v1'
READ_PROBE_SCHEMA = 'bdhub.br-sample-read-probe.v1'
CANARY_CONTRACT_SCHEMA = 'bdhub.br-sample-canary-contract.v1'
STATUS_EVIDENCE_SCHEMA = 'bdhub.br-sample-status-evidence.v1'
UI_STAGES = {'To review': 'pending', 'Ready to ship': 'ready_to_ship', 'Shipped': 'shipped',
             'Content pending': 'content_pending', 'Completed': 'completed', 'Canceled': 'cancelled'}
COUNTER_STAGES = {'TO_REVIEW_1': ('pending', 'To review'), 'READY_TO_SHIP_1': ('ready_to_ship', 'Ready to ship'),
                  'SHIPPED': ('shipped', 'Shipped'), 'CONTENT_PENDING': ('content_pending', 'Content pending'),
                  'COMPLETED': ('completed', 'Completed'), 'CANCELED': ('cancelled', 'Canceled')}
UI_FILES = {'Ready to ship': 'sample-ready-to-ship-ui.private.txt', 'Shipped': 'sample-shipped-ui.private.txt',
            'Content pending': 'sample-content-pending-ui.private.txt', 'Completed': 'sample-completed-ui.private.txt',
            'Canceled': 'sample-canceled-ui.private.txt'}
READ_EVIDENCE = 'var/br-sample-allstate-readonly-20261023-complete.private.json'
READ_PATHS = {'applicationId': 'apply_id', 'oecId': 'creator_info.oec_id',
              'platformCreatorId': 'creator_info.id', 'pid': 'product_info.product_id',
              'campaignId': 'campaign_info.campaign_id', 'status': 'sample_status'}


def validate_read_contract(contract, *, binding=None):
    """Validate a read-only observation; no payment or approval semantics exist here."""
    if not isinstance(contract, dict) or contract.get('schema') != READ_CONTRACT_SCHEMA or contract.get('market') != 'br':
        raise CycleError('sample_read_contract_unverified')
    if set(contract) != {'schema', 'market', 'readScope', 'evidence', 'recordPaths', 'readBody', 'statusCountersPath', 'binding'} or \
            contract.get('readScope') != 'all_states' or contract.get('recordPaths') != READ_PATHS or \
            contract.get('readBody') != OBSERVED_READ_BODY or contract.get('statusCountersPath') != 'sample_status_num_map':
        raise CycleError('sample_read_contract_shape_invalid')
    proofs = contract.get('evidence')
    if not isinstance(proofs, dict) or set(proofs) != {'records', 'creatorIdentity'} or \
            any(not isinstance(ref, str) or not ref for ref in proofs.values()):
        raise CycleError('sample_read_contract_evidence_missing')
    owner = contract.get('binding')
    if not isinstance(owner, dict) or owner.get('market') != 'br' or owner.get('account') != 'acc2' or \
            any(not isinstance(owner.get(key), str) or not owner[key] for key in ('institutionFingerprint', 'identitySha256')):
        raise CycleError('sample_read_contract_binding_missing')
    if binding is not None and owner != binding:
        raise CycleError('sample_original_identity_changed')
    return deepcopy(contract)


def observed_read_contract(root, binding):
    """Load only this project's complete BR observation, bound to the same identity."""
    path = Path(root) / READ_EVIDENCE
    try:
        proof = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if proof.get('schema') != 'bdhub.br-sample-field-probe.v1' or proof.get('status') != 'observed' or \
            proof.get('market') != 'br' or proof.get('account') != 'acc2' or proof.get('binding') != binding:
        raise CycleError('sample_read_evidence_binding_mismatch')
    pages = proof.get('pages')
    if proof.get('complete') is not True or type(proof.get('total')) is not int or proof.get('recordCount') != proof['total'] or \
            proof.get('platformWrites') != 0 or proof.get('platformWriteAttempts') != 0 or proof.get('identityFileUnchanged') is not True or \
            not isinstance(pages, list) or not pages or sum(page.get('recordCount', 0) for page in pages) != proof['total']:
        raise CycleError('sample_read_evidence_incomplete')
    request = proof.get('request') or {}
    if request.get('path') != LIST or request.get('method') != 'POST' or request.get('body') != OBSERVED_READ_BODY:
        raise CycleError('sample_read_evidence_shape_invalid')
    counters = pages[0].get('statusCounts')
    if not isinstance(counters, dict) or any(not isinstance(k, str) or not k or type(v) is not int or v < 0
                                            for k, v in counters.items()) or sum(counters.values()) != proof['total']:
        raise CycleError('sample_read_evidence_counters_missing')
    for index, page in enumerate(pages, 1):
        if page.get('page') != index or page.get('http') != 200 or type(page.get('code')) is not int or page['code'] != 0 or \
                page.get('ambiguous') is not False or page.get('verificationRequired') is not False or page.get('systemError3') is not False:
            raise CycleError('sample_read_evidence_incomplete')
        shapes = page.get('fields') or {}
        if page.get('statusCounts') != counters or any(shapes.get('$.data.sample_status_num_map.' + key) != {'number': 1} for key in counters):
            raise CycleError('sample_read_evidence_counters_missing')
        if page['recordCount'] and any(shapes.get('$.data.sample_records[].' + value) != {'string': page['recordCount']}
                                      for key, value in READ_PATHS.items() if key != 'status'):
            raise CycleError('sample_read_evidence_fields_missing')
        if page['recordCount'] and shapes.get('$.data.sample_records[].sample_status') != {'number': page['recordCount']}:
            raise CycleError('sample_read_evidence_fields_missing')
    ref = READ_EVIDENCE + ':sha256:' + hashlib.sha256(path.read_bytes()).hexdigest()
    return validate_read_contract({'schema': READ_CONTRACT_SCHEMA, 'market': 'br', 'readScope': 'all_states',
        'evidence': {'records': ref, 'creatorIdentity': ref}, 'recordPaths': deepcopy(READ_PATHS),
        'readBody': deepcopy(OBSERVED_READ_BODY), 'statusCountersPath': 'sample_status_num_map', 'binding': deepcopy(binding)}, binding=binding)


def validate_contract(contract):
    """Require separately observed identity, money and lifecycle contracts."""
    if not isinstance(contract, dict) or contract.get('schema') != CONTRACT_SCHEMA or contract.get('market') != 'br':
        raise CycleError('sample_contract_unverified')
    proofs = contract.get('evidence') or {}
    if any(not isinstance(proofs.get(key), str) or not proofs[key] for key in
           ('records', 'creatorIdentity', 'payment', 'review', 'lifecycle')):
        raise CycleError('sample_contract_evidence_missing')
    policy = contract.get('costPolicy')
    if policy is not None:
        from lib.sample_cost_policy import validate_cost_policy
        validate_cost_policy(policy)
    paths = contract.get('recordPaths') or {}
    required_paths = ('applicationId', 'oecId', 'platformCreatorId', 'pid', 'campaignId', 'status') + (() if policy else ('paymentRequired',))
    if any(not isinstance(paths.get(key), str) or not paths[key] for key in required_paths):
        raise CycleError('sample_contract_fields_missing')
    statuses = contract.get('statusMap')
    if not isinstance(statuses, dict) or not statuses or not all(isinstance(k, str) and v in
            {'pending', 'seller_pending', 'approved', 'ready_to_ship', 'shipped', 'content_pending', 'completed', 'rejected', 'cancelled'} for k, v in statuses.items()):
        raise CycleError('sample_contract_status_missing')
    if 'pending' not in statuses.values() or not {'approved', 'ready_to_ship', 'shipped', 'content_pending', 'completed'}.intersection(statuses.values()):
        raise CycleError('sample_contract_status_missing')
    if policy is None and contract.get('paymentFreeValue') is not False:
        raise CycleError('sample_contract_payment_unverified')
    if contract.get('statusEvidence'):
        observed = validate_status_evidence(contract['statusEvidence'], binding=contract.get('binding'))
        if any(statuses.get(native) != state for native, state in observed.items()):
            raise CycleError('sample_contract_status_evidence_mismatch')
    body = contract.get('readBody')
    if not isinstance(body, dict) or any(k not in {'search_params', 'order_params', 'page_size', 'cur_page'} for k in body):
        raise CycleError('sample_contract_read_shape_invalid')
    if type(body.get('page_size')) is not int or not 1 <= body['page_size'] <= 100 or body.get('cur_page') != 1:
        raise CycleError('sample_contract_read_shape_invalid')
    if contract.get('reviewSuccessCode') != 0 or type(contract.get('reviewSuccessCode')) is not int:
        raise CycleError('sample_contract_review_unverified')
    return deepcopy(contract)


def build_canary_contract(read_contract, cost_policy, *, pending_ui_evidence_ref, request_candidate_ref):
    """Arm the documented one-shot request without claiming review/lifecycle success."""
    from lib.sample_cost_policy import validate_cost_policy
    read = validate_read_contract(read_contract)
    result = deepcopy(read)
    result.update(schema=CANARY_CONTRACT_SCHEMA, statusMap={'10': 'pending'},
                  costPolicy=validate_cost_policy(cost_policy),
                  request={'method': 'POST', 'path': REVIEW, 'fields': ['campaign_id', 'creator_id', 'product_id', 'is_approved'],
                           'requestPayment': 'omit'},
                  evidence=read['evidence'] | {'pendingUi': pending_ui_evidence_ref, 'requestCandidate': request_candidate_ref})
    return validate_canary_contract(result)


def scope_snapshot(binding, records, counters, evidence_ref, *, complete):
    """Only a complete same-reader API scope can witness an enum transition."""
    if complete is not True or not isinstance(records, list) or not isinstance(binding, dict) or any(not isinstance(binding.get(key), str) or not binding[key] for key in ('market', 'account', 'institutionFingerprint', 'identitySha256')) or not isinstance(counters, dict) or any(not isinstance(k, str) or not k or type(v) is not int or v < 0 for k, v in counters.items()) or sum(counters.values()) != len(records) or not isinstance(evidence_ref, str) or not evidence_ref:
        return None
    members = []
    for row in records:
        member = {key: row.get(key) for key in ('applicationId', 'oecId', 'platformCreatorId', 'pid', 'campaignId', 'nativeStatus')}
        if any(not isinstance(member[key], str) or not member[key].isascii() or not member[key].isdigit() or int(member[key]) <= 0 for key in ('applicationId', 'oecId', 'platformCreatorId', 'pid', 'campaignId')) or type(member['nativeStatus']) is not int or member['nativeStatus'] < 0:
            return None
        members.append(member)
    if len({row['applicationId'] for row in members}) != len(members):
        return None
    return {'binding': deepcopy(binding), 'members': sorted(members, key=lambda row: row['applicationId']),
            'platformCounts': deepcopy(counters), 'evidenceRef': evidence_ref}


def derive_status_transition(before, after, known_map, *, target_id=None):
    """Prove one changed application and exactly one named-counter transfer; otherwise learn nothing."""
    if not isinstance(known_map, dict) or not isinstance(before, dict) or not isinstance(after, dict) or before.get('binding') != after.get('binding') or before.get('evidenceRef') == after.get('evidenceRef'):
        return None
    for snapshot in (before, after):
        if scope_snapshot(snapshot.get('binding'), snapshot.get('members', []), snapshot.get('platformCounts'), snapshot.get('evidenceRef'), complete=True) is None:
            return None
    old = {row['applicationId']: row for row in before['members']}
    new = {row['applicationId']: row for row in after['members']}
    if old.keys() != new.keys():
        return None
    changes = []
    for application_id in old:
        if {k:v for k,v in old[application_id].items() if k != 'nativeStatus'} != {k:v for k,v in new[application_id].items() if k != 'nativeStatus'}:
            return None
        if old[application_id]['nativeStatus'] != new[application_id]['nativeStatus']:
            changes.append(application_id)
    if len(changes) != 1 or target_id is not None and changes[0] != target_id:
        return None
    application_id = changes[0]
    original, current = old[application_id], new[application_id]
    previous = known_map.get(str(original['nativeStatus']))
    old_keys = [key for key, (state, _) in COUNTER_STAGES.items() if state == previous]
    if len(old_keys) != 1:
        return None
    counts_before, counts_after = before['platformCounts'], after['platformCounts']
    deltas = {key: counts_after.get(key, 0) - counts_before.get(key, 0) for key in counts_before.keys() | counts_after.keys()}
    changed = {key: delta for key, delta in deltas.items() if delta}
    additions = [key for key, delta in changed.items() if delta == 1 and key in COUNTER_STAGES]
    if len(additions) != 1 or changed != {old_keys[0]: -1, additions[0]: 1}:
        return None
    new_key = additions[0]
    state, label = COUNTER_STAGES[new_key]
    existing = known_map.get(str(current['nativeStatus']))
    if existing is not None and existing != state:
        return None
    # Named totals must also cover every row whose native meaning is already known.
    for snapshot, mapping in ((before, known_map), (after, known_map | {str(current['nativeStatus']): state})):
        for counter, (canonical, _) in COUNTER_STAGES.items():
            known_count = sum(mapping.get(str(row['nativeStatus'])) == canonical for row in snapshot['members'])
            if snapshot['platformCounts'].get(counter, 0) < known_count:
                return None
    return current | {'state': state, 'sourceKind': 'single_application_delta', 'platformCounter': new_key,
                      'apiEvidenceRef': after['evidenceRef'], 'delta': {'before': deepcopy(before), 'after': deepcopy(after),
                       'knownStatusMap': deepcopy(known_map), 'targetId': application_id}, 'platformLabel': label}


def validate_status_evidence(evidence, *, binding=None):
    if not isinstance(evidence, dict) or set(evidence) - {'sourceBinding', 'revalidatedBy'} != {'schema', 'market', 'binding', 'observations'} or evidence.get('schema') != STATUS_EVIDENCE_SCHEMA or evidence.get('market') != 'br' or not isinstance(evidence.get('observations'), list) or not evidence['observations']:
        raise CycleError('sample_status_evidence_invalid')
    if binding is not None and evidence.get('binding') != binding:
        raise CycleError('sample_original_identity_changed')
    if 'sourceBinding' in evidence or 'revalidatedBy' in evidence:
        source, current = evidence.get('sourceBinding'), evidence.get('binding')
        if not isinstance(source, dict) or not isinstance(current, dict) or any(source.get(key) != current.get(key) for key in ('market', 'account', 'institutionFingerprint')) or not isinstance(evidence.get('revalidatedBy'), str) or not evidence['revalidatedBy']:
            raise CycleError('sample_status_evidence_binding_invalid')
    statuses = {}
    for row in evidence['observations']:
        if not isinstance(row, dict) or type(row.get('nativeStatus')) is not int or row['nativeStatus'] < 0 or not isinstance(row.get('apiEvidenceRef'), str) or not row['apiEvidenceRef']:
            raise CycleError('sample_status_evidence_invalid')
        if row.get('sourceKind') == 'single_application_delta':
            delta = row.get('delta') or {}
            proven = derive_status_transition(delta.get('before'), delta.get('after'), delta.get('knownStatusMap', {}), target_id=delta.get('targetId'))
            if proven != row or (delta.get('before') or {}).get('binding') != evidence.get('binding'):
                raise CycleError('sample_status_delta_unproven')
        elif UI_STAGES.get(row.get('uiLabel')) != row.get('state') or not isinstance(row.get('uiEvidenceRef'), str) or not row['uiEvidenceRef']:
            raise CycleError('sample_status_evidence_invalid')
        for key in ('applicationId', 'platformCreatorId', 'oecId', 'pid', 'campaignId'):
            if not isinstance(row.get(key), str) or not row[key].isascii() or not row[key].isdigit() or int(row[key]) <= 0:
                raise CycleError('sample_status_evidence_identity_missing')
        native = str(row['nativeStatus'])
        if native == '10' and row['state'] != 'pending' or native in statuses and statuses[native] != row['state']:
            raise CycleError('sample_status_evidence_conflict')
        statuses[native] = row['state']
    for row in evidence['observations']:
        if row.get('sourceKind') == 'single_application_delta':
            delta = row['delta']
            original = next(item for item in delta['before']['members'] if item['applicationId'] == delta['targetId'])
            native = str(original['nativeStatus'])
            if ({'10': 'pending'} | statuses).get(native) != delta['knownStatusMap'].get(native):
                raise CycleError('sample_status_delta_parent_unproven')
    return statuses


def observed_status_evidence(root, binding):
    """Join archived real BR UI rows to the immutable API observation, never totals alone."""
    root = Path(root)
    read = observed_read_contract(root, binding)
    if read is None:
        return None
    proof = json.loads((root / READ_EVIDENCE).read_text())
    records = []
    for page in proof['pages']:
        values = page.get('businessValues') or {}
        indices = sorted({int(match[1]) for key in values if (match := re.fullmatch(r'\$\.data\.sample_records\[(\d+)\]\.apply_id', key))})
        for index in indices:
            row = {key: values.get(f'$.data.sample_records[{index}].{path}') for key, path in READ_PATHS.items()}
            row['nativeStatus'] = row.pop('status')
            records.append(row)
    observations = []
    identities = root / 'var/creator-identities.sqlite'
    database = sqlite3.connect(identities.resolve().as_uri() + '?mode=ro', uri=True) if identities.is_file() else None
    try:
        for label, filename in UI_FILES.items():
            path = root / 'var/releases/br-first-activation-20261023' / filename
            if not path.is_file():
                continue
            source = path.read_text()
            if 'Brazil' not in source:
                continue
            reference = str(path.relative_to(root)) + ':sha256:' + hashlib.sha256(source.encode()).hexdigest()
            for section in re.split(r'\n\s*\d+\s+row\s*\n', source):
                ids = re.findall(r'\bID\s*:\s*(\d{10,32})', section)
                if len(ids) != 2 or not re.search(r'(?:文本|text)\s+' + re.escape(label) + r'(?:\s|$)', section):
                    continue
                matches = [row for row in records if (row['pid'], row['campaignId']) == tuple(ids)]
                handle = re.search(r'(?:文本|text)\s+@\s*([A-Za-z0-9._]+)', section)
                if not handle or database is None:
                    continue
                if handle and database is not None:
                    exact = []
                    for row in matches:
                        person = database.execute("SELECT current_handle FROM creator_identity WHERE market='br' AND oec_id=? AND handle_conflict=0", (row['oecId'],)).fetchone()
                        if person and str(person[0] or '').lstrip('@').lower() == handle[1].lower():
                            exact.append(row)
                    matches = exact
                if len(matches) != 1:
                    continue
                observations.append(matches[0] | {'state': UI_STAGES[label], 'uiLabel': label,
                    'uiEvidenceRef': reference, 'apiEvidenceRef': read['evidence']['records']})
    finally:
        if database is not None:
            database.close()
    if not observations:
        return None
    evidence = {'schema': STATUS_EVIDENCE_SCHEMA, 'market': 'br', 'binding': deepcopy(binding), 'observations': observations}
    validate_status_evidence(evidence, binding=binding)
    return evidence


def extend_canary_contract(contract, evidence):
    current = validate_canary_contract(contract)
    statuses = validate_status_evidence(evidence, binding=current['binding'])
    if 'statusEvidence' in current:
        combined = {digest(row): row for row in current['statusEvidence']['observations'] + evidence['observations']}
        evidence = evidence | {'observations': list(combined.values())}
        statuses = validate_status_evidence(evidence, binding=current['binding'])
    current.update(statusEvidence=deepcopy(evidence), statusMap={'10': 'pending'} | statuses)
    return validate_canary_contract(current)


def validate_canary_contract(contract, *, binding=None):
    if not isinstance(contract, dict) or set(contract) - {'statusEvidence'} != {'schema', 'market', 'readScope', 'evidence', 'recordPaths', 'readBody', 'statusCountersPath', 'binding', 'statusMap', 'costPolicy', 'request'} or contract.get('schema') != CANARY_CONTRACT_SCHEMA:
        raise CycleError('sample_canary_contract_invalid')
    statuses = {'10': 'pending'}
    if 'statusEvidence' in contract:
        statuses.update(validate_status_evidence(contract['statusEvidence'], binding=contract.get('binding')))
    if contract.get('statusMap') != statuses or contract.get('request') != {
            'method': 'POST', 'path': REVIEW, 'fields': ['campaign_id', 'creator_id', 'product_id', 'is_approved'], 'requestPayment': 'omit'}:
        raise CycleError('sample_canary_unobserved_semantics')
    evidence = contract.get('evidence')
    if not isinstance(evidence, dict) or set(evidence) != {'records', 'creatorIdentity', 'pendingUi', 'requestCandidate'} or any(not isinstance(ref, str) or not ref for ref in evidence.values()):
        raise CycleError('sample_canary_evidence_missing')
    read = {key: deepcopy(value) for key, value in contract.items() if key not in {'statusMap', 'costPolicy', 'request', 'statusEvidence'}}
    read['schema'] = READ_CONTRACT_SCHEMA
    read['evidence'] = {key: evidence[key] for key in ('records', 'creatorIdentity')}
    validate_read_contract(read, binding=binding)
    from lib.sample_cost_policy import validate_cost_policy
    validate_cost_policy(contract['costPolicy'])
    return deepcopy(contract)


def validate_execution_contract(contract, *, binding=None):
    if isinstance(contract, dict) and contract.get('schema') == CANARY_CONTRACT_SCHEMA:
        return validate_canary_contract(contract, binding=binding)
    result = validate_contract(contract)
    if binding is not None and result.get('binding') is not None and result['binding'] != binding:
        raise CycleError('sample_original_identity_changed')
    return result


def validate_any_contract(contract, *, binding=None):
    return validate_read_contract(contract, binding=binding) if isinstance(contract, dict) and contract.get('schema') == READ_CONTRACT_SCHEMA else validate_execution_contract(contract, binding=binding)


def record_contract_fingerprint(contract):
    # A frozen user cost policy is execution authority, not a different platform row schema.
    value = deepcopy(contract)
    if value and value.get('schema') == CONTRACT_SCHEMA:
        value.pop('costPolicy', None)
    return digest(value)


def require_current_cost_policy(root, contract):
    from lib.sample_cost_policy import load_cost_policy, validate_cost_policy
    frozen = contract.get('costPolicy') if contract else None
    if frozen is not None:
        validate_cost_policy(frozen)
    current = load_cost_policy(root) if root is not None else None
    if current != frozen:
        raise CycleError('sample_cost_policy_changed')
    return frozen


def _path(row, path):
    value = row
    for key in path.split('.'):
        if not isinstance(value, dict) or key not in value:
            return None
        value = value[key]
    return value


def normalize_record(raw, *, contract=None):
    """Retain only business facts; seller/creator contact information never enters this ledger."""
    if not isinstance(raw, dict):
        raise CycleError('sample_record_invalid')
    paths = (contract or {}).get('recordPaths') or {
        'applicationId': 'apply_id', 'oecId': 'creator_info.oec_id', 'pid': 'product_info.product_id',
        'campaignId': 'campaign_info.campaign_id', 'status': 'sample_status'}
    def ident(key):
        value = _path(raw, paths.get(key, ''))
        # IDs remain strings; numeric IDs cannot safely pass through browser JSON.
        return value if isinstance(value, str) and value.isascii() and value.isdigit() and int(value) > 0 else None
    application = ident('applicationId')
    if not application:
        raise CycleError('sample_application_id_missing')
    status = _path(raw, paths.get('status', ''))
    writable = bool(contract and contract.get('schema') == CONTRACT_SCHEMA)
    canary = bool(contract and contract.get('schema') == CANARY_CONTRACT_SCHEMA)
    payment = _path(raw, paths.get('paymentRequired', '')) if writable else None
    return {'applicationId': application, 'oecId': ident('oecId'), 'pid': ident('pid'),
            'campaignId': ident('campaignId'), 'platformCreatorId': ident('platformCreatorId') if contract else None,
            'state': (contract or {}).get('statusMap', {}).get(str(status), 'unknown') if writable or canary else 'unknown',
            'nativeStatus': status if type(status) in (str, int) else None,
            'paymentRequired': payment if type(payment) is bool else None,
            'isPosted': raw.get('is_posted') if type(raw.get('is_posted')) is bool else None,
            'videoCount': raw.get('video_count') if type(raw.get('video_count')) is int and raw['video_count'] >= 0 else None,
            'liveCount': raw.get('room_count') if type(raw.get('room_count')) is int and raw['room_count'] >= 0 else None,
            'contractFingerprint': record_contract_fingerprint(contract) if contract else None}


def review_payload(application, policy=None):
    """Only the observed platform creator ID is allowed; never substitute OEC."""
    values = [application.get(key) for key in ('campaignId', 'platformCreatorId', 'pid')]
    if any(not isinstance(v, str) or not v.isascii() or not v.isdigit() or int(v) <= 0 for v in values):
        raise CycleError('sample_review_identity_unverified')
    from lib.sample_cost_policy import payment_basis
    payment_basis(application, policy)
    return dict(zip(('campaign_id', 'creator_id', 'product_id'), values)) | {'is_approved': True}


def receipt_accepted(receipt):
    return isinstance(receipt, dict) and receipt.get('status') == 'accepted' and receipt.get('http') == 200 and \
           type(receipt.get('code')) is int and receipt['code'] == 0 and \
           all(receipt.get(key) is None or receipt.get(key) is False for key in ('ambiguous', 'verificationRequired', 'systemError3'))


def read_failure(result):
    if result.has_turing or result.system_error_3 and result.code == 10000:
        return 'sample_verification_required'
    if result.http_status == 429:
        return 'sample_rate_limited'
    if result.http_status == 401 or result.code == 16201010:
        return 'sample_authentication_required'
    if result.http_status == 403:
        return 'sample_permission_denied'
    if result.http_status in {0, 500, 502, 503, 504}:
        return 'sample_network_unavailable'
    return 'sample_remote_read_failed'


class SampleAdapter:
    """Protocol independent of transport construction, useful for narrow contract tests."""
    def __init__(self, transport, report, *, contract=None, frozen=None, stopped=lambda: False, sleep=time.sleep, probe_binding=None):
        self.transport = transport
        self.report = report
        if frozen is not None:
            validate_execution_contract(contract)
        self.contract = validate_any_contract(contract) if contract is not None else None
        if frozen is not None and self.contract.get('schema') == CANARY_CONTRACT_SCHEMA and (set(frozen) != set(self.contract['request']['fields']) or frozen.get('is_approved') is not True or any(not isinstance(frozen.get(key), str) or not frozen[key].isascii() or not frozen[key].isdigit() for key in ('campaign_id', 'creator_id', 'product_id'))):
            raise CycleError('sample_canary_payload_invalid')
        self.frozen = deepcopy(frozen)
        self.stopped = stopped
        self.used = False
        self.sleep = sleep
        self.probe_binding = probe_binding

    def _read(self, *, method, path, params, payload):
        for attempt in range(2):
            if self.stopped():
                raise CycleError('sample_stopped')
            try:
                current_params = dict(params)
                if 'fp' in current_params and getattr(self.transport, 'fp', None):
                    current_params['fp'] = self.transport.fp
                result = self.transport._xhr(method=method, path=path, params=current_params, payload=payload, write=False)
            except ValueError as error:
                if str(error).startswith('commerce_verification'):
                    raise CycleError('sample_verification_required') from error
                raise
            if not (result.http_status != 200 or type(result.code) is not int or result.code != 0 or result.ambiguous or result.has_turing or result.system_error_3):
                return self.transport.require_read(result)
            code = read_failure(result)
            if code != 'sample_network_unavailable' or attempt == 1:
                raise CycleError(code)
            self.report['readRetries'] = self.report.get('readRetries', 0) + 1
            self.sleep(.5)
        raise CycleError('sample_remote_read_failed')

    def read_page(self, page=1):
        if self.stopped():
            raise CycleError('sample_stopped')
        if type(page) is not int or not 1 <= page <= 100:
            raise CycleError('sample_page_invalid')
        body = deepcopy(self.contract['readBody'] if self.contract else OBSERVED_READ_BODY)
        body['cur_page'] = page
        payload = self._read(method='POST', path=LIST, params=self.transport._params(), payload=body)
        data = payload.get('data')
        if not isinstance(data, dict):
            raise CycleError('sample_records_response_invalid')
        rows = data.get('sample_records')
        total = data.get('total_num', data.get('total'))
        if type(total) is not int or total < 0 or not isinstance(rows, list) and not (total == 0 and rows is None):
            raise CycleError('sample_records_response_invalid')
        rows = [] if rows is None else rows
        if len(rows) > body['page_size'] or len(rows) > total:
            raise CycleError('sample_records_response_invalid')
        counters = data.get('sample_status_num_map')
        if counters is not None and (not isinstance(counters, dict) or any(not isinstance(k, str) or not k or
                type(v) is not int or v < 0 for k, v in counters.items())):
            raise CycleError('sample_records_response_invalid')
        if (self.probe_binding or self.contract and self.contract.get('schema') in {READ_CONTRACT_SCHEMA, CANARY_CONTRACT_SCHEMA}) and \
                (counters is None or sum(counters.values()) != total):
            raise CycleError('sample_records_counters_incomplete')
        self.report['readRequests'] = self.report.get('readRequests', 0) + 1
        candidate = self.contract or ({'schema': READ_PROBE_SCHEMA, 'recordPaths': READ_PATHS, 'binding': self.probe_binding} if self.probe_binding else None)
        return {'records': [normalize_record(r, contract=candidate) for r in rows], 'total': total,
                'page': page, 'pageSize': body['page_size'], 'evidenceRef': 'sample-list:' + digest(payload),
                'platformCounts': deepcopy(counters), 'readScope': (self.contract or {}).get('readScope',
                    'all_states' if (self.contract or self.probe_binding) and body.get('search_params') == [] else None)}

    def refresh_sample_quota(self, offer):
        """Read the exact joined Campaign/PID; incomplete pages never prove absence."""
        from lib.cycle_catalog import sample_facts, timestamp
        from lib.product_stock_policy import unavailable_allowed
        from lib.second_cycle import epoch
        refs = []
        def find(path, params, key, id_key, wanted, max_pages):
            total = None
            seen = set()
            for page in range(1, max_pages + 1):
                if self.stopped():
                    raise CycleError('sample_stopped')
                payload = self._read(method='GET', path=path,
                    params=self.transport._params() | params | {'cur_page': page, 'page_size': 100}, payload=None)
                self.report['readRequests'] = self.report.get('readRequests', 0) + 1
                refs.append(digest(payload))
                data = payload.get('data')
                current = data.get('total_num') if isinstance(data, dict) else None
                if isinstance(current, str) and current.isascii() and current.isdigit():
                    current = int(current)
                rows = data.get(key, []) if isinstance(data, dict) else None
                if type(current) is not int or current < 0 or total is not None and current != total or not isinstance(rows, list) or len(rows) > 100:
                    raise CycleError('sample_offer_pagination_incomplete')
                total = current
                ids = [str(r.get(id_key) or '') for r in rows if isinstance(r, dict)]
                if len(ids) != len(rows) or any(not value.isascii() or not value.isdigit() for value in ids) or len(ids) != len(set(ids)) or seen.intersection(ids):
                    raise CycleError('sample_offer_pagination_incomplete')
                seen.update(ids)
                if len(seen) > total or len(rows) < 100 and len(seen) != total:
                    raise CycleError('sample_offer_pagination_incomplete')
                matches = [r for r in rows if str(r.get(id_key)) == wanted]
                if len(matches) == 1:
                    return matches[0]
                if len(seen) == total:
                    raise CycleError('sample_current_offer_missing')
            raise CycleError('sample_offer_page_limit')
        campaign = find(CAMPAIGNS, {'campaign_join_status_category': '1', 'crs_campaign_types': ''},
                        'campaign', 'campaign_id', offer['campaignId'], 5)
        product = find(PRODUCTS, {'campaign_id': offer['campaignId'],
                       'marked': '0' if str(campaign.get('crs_campaign_type')) == '7' else 'false'},
                       'campaign_product', 'product_id', offer['pid'], 20)
        at = time.time()
        end = timestamp(campaign.get('promotion_end_time'))
        start = timestamp(campaign.get('promotion_start_time'))
        available = str(product.get('product_status')) == '2' and product.get('is_under_governed') is not True and \
                    unavailable_allowed(product.get('unavailable_type'), {}) and end is not None and epoch(end) > at and \
                    (start is None or epoch(start) <= at)
        evidence = 'sample-offer:' + digest(refs)
        return sample_facts(product, evidence, at) | {'pid': offer['pid'], 'campaignId': offer['campaignId'], 'available': available}

    def approve_once(self, payload):
        if self.stopped():
            raise CycleError('sample_stopped')
        if not self.contract or self.contract.get('schema') not in {CONTRACT_SCHEMA, CANARY_CONTRACT_SCHEMA} or self.frozen is None or payload != self.frozen or self.used:
            raise CycleError('sample_review_outside_intent')
        # Claim the attempt before transport entry: network failure is never replayable.
        self.used = True
        self.report['platformWriteAttempts'] = self.report.get('platformWriteAttempts', 0) + 1
        result = self.transport._xhr(method='POST', path=REVIEW, params=self.transport._params(), payload=deepcopy(payload), write=True)
        if result.http_status:
            self.report['platformWrites'] = self.report.get('platformWrites', 0) + 1
        receipt = {'http': result.http_status, 'code': result.code if type(result.code) is int else None,
                   'evidenceRef': 'sample-review:' + digest(result.payload), 'ambiguous': bool(result.ambiguous),
                   'verificationRequired': bool(result.has_turing), 'systemError3': bool(result.system_error_3)}
        if result.ambiguous or result.http_status != 200 or result.has_turing or result.system_error_3 or type(result.code) is not int:
            return receipt | {'status': 'unknown'}
        if self.contract.get('schema') == CANARY_CONTRACT_SCHEMA:
            return receipt | {'status': 'accepted' if result.code == 0 else 'unknown'}
        if result.code != self.contract['reviewSuccessCode']:
            return receipt | {'status': 'rejected'}
        # A success response is an agency decision receipt. Only a lifecycle read
        # can confirm platform approval or show seller confirmation is still due.
        return receipt | {'status': 'accepted'}


@contextmanager
def sample_session(root, market, account_name, report, *, contract=None, frozen=None,
                   expected_binding=None, stopped=lambda: False, wait_seconds=5, expected_institution_fingerprint=None):
    """Share the original platform slots before acquiring this account's lease."""
    if market != 'br':
        raise CycleError('sample_market_not_supported')
    import uuid
    from lib.first_resources import platform_read
    with platform_read(root, 'first-sample-' + uuid.uuid4().hex, account_name=account_name) as grant:
        def scoped_stopped():
            grant.check()
            return stopped()
        with _account_sample_session(root, market, account_name, report, contract=contract, frozen=frozen,
                                     expected_binding=expected_binding, stopped=scoped_stopped,
                                     wait_seconds=wait_seconds, expected_institution_fingerprint=expected_institution_fingerprint) as adapter:
            yield adapter


@contextmanager
def _account_sample_session(root, market, account_name, report, *, contract=None, frozen=None,
                            expected_binding=None, stopped=lambda: False, wait_seconds=5, expected_institution_fingerprint=None):
    """Use project-owned identity, existing ProfileLease and fixed endpoint scopes."""
    if market != 'br':
        raise CycleError('sample_market_not_supported')
    if type(wait_seconds) not in (int, float) or not 0 <= wait_seconds <= 15:
        raise CycleError('sample_guard_wait_invalid')
    from lib.market_accounts import catalog_read_account
    root = Path(root)
    if catalog_read_account(root, account_name, market) != account_name:
        raise CycleError('sample_account_unverified')
    if frozen is not None:
        contract = validate_execution_contract(contract)
    from lib.legacy_runtime import configure_vendored_bdhub
    configure_vendored_bdhub(root=root)
    from bdhub import scheduled_relogin
    from bdhub.enrich.profile_lease import ProfileBusyError, ProfileLease
    from bdhub.hub.markets import identity_for
    from bdhub.send.taplink.transport import account_for
    from bdhub.research.commerce_transport import CommerceTransport
    cfg, account = account_for(market, account_name, check_maintenance=False)
    identity = identity_for(market, account=account, cfg=cfg).require_product_search()
    if not identity.partner_id_is_own:
        raise CycleError('sample_identity_not_own')
    headers_path = Path(account.headers_json)
    before = hashlib.sha256(headers_path.read_bytes()).hexdigest()
    binding = {'market': market, 'account': account_name,
               'institutionFingerprint': digest(str(identity.im_market_partner_id)), 'identitySha256': before}
    if expected_institution_fingerprint is not None and binding['institutionFingerprint'] != expected_institution_fingerprint:
        raise CycleError('sample_reader_institution_changed')
    if expected_binding is not None and binding != expected_binding:
        raise CycleError('sample_original_identity_changed')
    if contract is not None:
        contract = validate_any_contract(contract, binding=binding)
    elif contract is None:
        try:
            contract = observed_read_contract(root, binding)
        except CycleError as error:
            if str(error) != 'sample_read_evidence_binding_mismatch':
                raise
            # A normal identity rotation may re-probe reads. It grants no write scope.
            contract = None
    def check():
        if frozen is not None:
            require_current_cost_policy(root, contract)
        if stopped():
            raise CycleError('sample_stopped')
        if scheduled_relogin.maintenance_due(account, initialize=False, ignore_retry_throttle=True):
            raise CycleError('sample_account_maintenance_due')
        if hashlib.sha256(headers_path.read_bytes()).hexdigest() != before:
            raise CycleError('sample_identity_changed')
    class Scoped(CommerceTransport):
        READ_ENDPOINTS = frozenset({(LIST, 'POST'), (CAMPAIGNS, 'GET'), (PRODUCTS, 'GET')})
        WRITE_ENDPOINTS = frozenset({(REVIEW, 'POST')}) if frozen is not None else frozenset()
        def _xhr(self, **kwargs):
            check()
            if kwargs.get('write') and kwargs.get('payload') != frozen:
                raise CycleError('sample_review_outside_intent')
            return super()._xhr(**kwargs)
    lease = ProfileLease(account.profile_dir, account=account_name, market=market, operation='agent-sample-review' if frozen else 'agent-sample-read')
    deadline = time.monotonic() + wait_seconds
    while True:
        try:
            owner = lease.acquire()
            break
        except ProfileBusyError:
            if stopped() or time.monotonic() >= deadline:
                raise CycleError('sample_account_in_use') from None
            time.sleep(.1)
    transport = None
    try:
        check()
        transport = Scoped(identity, account, allow_write=frozen is not None)
        transport.session.trust_env = True
        report.update(binding=binding, platformWriteAttempts=0, platformWrites=0)
        adapter = SampleAdapter(transport, report, contract=contract, frozen=frozen, stopped=stopped, probe_binding=binding if contract is None else None)
        adapter.binding = binding
        yield adapter
    finally:
        if transport is not None:
            transport.session.close()
        lease.release(owner.token)
        report['identityFileUnchanged'] = hashlib.sha256(headers_path.read_bytes()).hexdigest() == before
        report['verificationAttempts'] = getattr(transport, 'verification_attempts', 0)
        report['verificationSuccesses'] = getattr(transport, 'verification_successes', 0)
