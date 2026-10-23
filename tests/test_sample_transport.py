from contextlib import closing
import sys
from pathlib import Path
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from lib.sample_transport import (CONTRACT_SCHEMA, READ_CONTRACT_SCHEMA, CANARY_CONTRACT_SCHEMA, READ_EVIDENCE, READ_PATHS,
                                  OBSERVED_READ_BODY, LIST, REVIEW, SampleAdapter, normalize_record,
                                  observed_read_contract, review_payload, validate_contract, validate_read_contract, build_canary_contract, validate_canary_contract, extend_canary_contract, validate_status_evidence, observed_status_evidence, read_failure, receipt_accepted, scope_snapshot, derive_status_transition)
from lib.second_cycle import CycleError


def contract():
    return {'schema': CONTRACT_SCHEMA, 'market': 'br',
            'evidence': {k: 'test-observed:' + k for k in ('records', 'creatorIdentity', 'payment', 'review', 'lifecycle')},
            'recordPaths': {'applicationId': 'apply_id', 'oecId': 'creator_info.oec_id',
                            'platformCreatorId': 'creator_info.creator_id', 'pid': 'product_info.product_id',
                            'campaignId': 'campaign_info.campaign_id', 'status': 'sample_status',
                            'paymentRequired': 'requires_partner_payment'},
            'statusMap': {'pending-observed': 'pending', 'approved-observed': 'approved',
                          'seller-observed': 'seller_pending'}, 'paymentFreeValue': False,
            'readBody': {'search_params': [], 'order_params': [], 'page_size': 5, 'cur_page': 1},
            'reviewSuccessCode': 0}


def raw():
    return {'apply_id': '101', 'creator_info': {'oec_id': '201', 'creator_id': '301', 'email': 'do-not-store'},
            'product_info': {'product_id': '401'}, 'campaign_info': {'campaign_id': '501'},
            'sample_status': 'pending-observed', 'requires_partner_payment': False,
            'seller_contact_info': {'phone': 'do-not-store'}}


def read_contract(binding=None):
    import copy
    return {'schema': READ_CONTRACT_SCHEMA, 'market': 'br', 'readScope': 'all_states',
            'evidence': {'records': 'test-observed:br-22', 'creatorIdentity': 'test-observed:creator-id'},
            'recordPaths': copy.deepcopy(READ_PATHS), 'readBody': copy.deepcopy(OBSERVED_READ_BODY),
            'statusCountersPath': 'sample_status_num_map',
            'binding': binding or {'market': 'br', 'account': 'acc2', 'institutionFingerprint': 'test-agency', 'identitySha256': 'test-generation'}}


def seller_policy():
    from lib.sample_cost_policy import POLICY_SCHEMA, USER_CONFIRMATION
    return {'schema': POLICY_SCHEMA, 'market': 'br', 'payer': 'seller', 'agencyPaymentAllowed': False,
            'requestPayment': 'omit', 'authority': 'user', 'authorityEvidence': USER_CONFIRMATION,
            'confirmedAt': '2026-01-01T00:00:00Z', 'revision': 1}


def canary_contract():
    return build_canary_contract(read_contract(), seller_policy(), pending_ui_evidence_ref='observed:br-pending-ui',
                                  request_candidate_ref='docs/contracts/tiktok/business-catalog.md:TK-010')


def status_evidence(binding=None):
    owner = binding or read_contract()['binding']
    observations = []
    for native, state, label in ((40, 'content_pending', 'Content pending'), (100, 'completed', 'Completed')):
        observations.append({'applicationId': '90001', 'platformCreatorId': '90002', 'oecId': '90003', 'pid': '90004', 'campaignId': '90005',
                             'nativeStatus': native, 'state': state, 'uiLabel': label, 'uiEvidenceRef': 'observed:br-ui:' + label,
                             'apiEvidenceRef': 'observed:br-original-list'})
    return {'schema': 'bdhub.br-sample-status-evidence.v1', 'market': 'br', 'binding': owner, 'observations': observations}


def br_raw(index=0, status=10):
    return {'apply_id': str(10001 + index), 'creator_info': {'id': str(20001 + index), 'oec_id': str(30001 + index),
            'email': 'do-not-store'}, 'product_info': {'product_id': str(40001 + index), 'sample_quota': 3},
            'campaign_info': {'campaign_id': '50001', 'sample_budget': {}}, 'sample_status': status,
            'seller_contact_info': {'phone': 'do-not-store'}}


class Transport:
    def __init__(self, payload=None, **result):
        self.calls = []
        self.result = SimpleNamespace(http_status=200, code=0, ambiguous=False, has_turing=False,
                                      system_error_3=False, payload=payload or {'code': 0, 'data': {}}, **result)
    def _params(self):
        return {'partner_id': 'test'}
    def _xhr(self, **request):
        self.calls.append(request)
        return self.result
    def require_read(self, result):
        return result.payload


class SampleTransportTest(unittest.TestCase):
    def test_unknown_contract_does_not_invent_creator_payment_or_status(self):
        row = normalize_record(raw())
        self.assertIsNone(row['platformCreatorId'])
        self.assertIsNone(row['paymentRequired'])
        self.assertEqual(row['state'], 'unknown')
        self.assertNotIn('do-not-store', str(row))

    def test_verified_contract_uses_platform_id_not_oec(self):
        row = normalize_record(raw(), contract=contract())
        self.assertEqual(review_payload(row), {'campaign_id': '501', 'creator_id': '301',
                                               'product_id': '401', 'is_approved': True})

    def test_missing_platform_id_cannot_fall_back_to_oec(self):
        row = normalize_record(raw(), contract=contract())
        row['platformCreatorId'] = None
        with self.assertRaisesRegex(CycleError, 'identity_unverified'):
            review_payload(row)

    def test_payment_unknown_and_paid_both_wait(self):
        row = normalize_record(raw(), contract=contract())
        for payment in (None, True, 0, '0'):
            row['paymentRequired'] = payment
            with self.assertRaisesRegex(CycleError, 'payment_requires_handling'):
                review_payload(row)

    def test_contract_requires_all_real_evidence(self):
        for key in contract()['evidence']:
            c = contract()
            del c['evidence'][key]
            with self.assertRaisesRegex(CycleError, 'evidence_missing'):
                validate_contract(c)

    def test_unknown_status_remains_unknown(self):
        r = raw()
        r['sample_status'] = 100
        self.assertEqual(normalize_record(r, contract=contract())['state'], 'unknown')

    def test_empty_historical_br_response_is_readable(self):
        transport = Transport({'code': 0, 'data': {'total': 0}})
        row = SampleAdapter(transport, {}).read_page()
        self.assertEqual(row['records'], [])
        self.assertEqual(transport.calls[0]['path'], LIST)
        self.assertFalse(transport.calls[0]['write'])
        self.assertEqual(transport.calls[0]['payload'], OBSERVED_READ_BODY)

    def test_read_contract_keeps_native_status_and_distinct_platform_identity(self):
        row = normalize_record(br_raw(), contract=read_contract())
        self.assertEqual((row['platformCreatorId'], row['oecId']), ('20001', '30001'))
        self.assertEqual(row['nativeStatus'], 10)
        self.assertEqual(row['state'], 'unknown')
        self.assertIsNone(row['paymentRequired'])
        self.assertNotIn('do-not-store', str(row))

    def test_empty_budget_and_injected_boolean_cannot_prove_payment_free_for_read_contract(self):
        value = br_raw() | {'paymentRequired': False, 'requires_partner_payment': False}
        row = normalize_record(value, contract=read_contract())
        self.assertIsNone(row['paymentRequired'])
        with self.assertRaisesRegex(CycleError, 'payment_requires_handling'):
            review_payload(row)

    def test_read_contract_never_opens_an_approval_request(self):
        transport = Transport()
        with self.assertRaisesRegex(CycleError, 'contract_unverified'):
            SampleAdapter(transport, {}, contract=read_contract(), frozen={'is_approved': True})
        self.assertFalse(transport.calls)
        adapter = SampleAdapter(transport, {}, contract=read_contract())
        with self.assertRaisesRegex(CycleError, 'outside_intent'):
            adapter.approve_once({'is_approved': True})
        with self.assertRaisesRegex(CycleError, 'contract_unverified'):
            validate_contract(read_contract())
        with self.assertRaisesRegex(CycleError, 'read_contract_unverified'):
            validate_read_contract(contract())

    def test_read_contract_binding_scope_and_identity_paths_cannot_drift(self):
        import copy
        value = read_contract()
        with self.assertRaisesRegex(CycleError, 'identity_changed'):
            validate_read_contract(value, binding=value['binding'] | {'identitySha256': 'new-generation'})
        for change in ({'readScope': 'pending'}, {'recordPaths': value['recordPaths'] | {'platformCreatorId': 'creator_info.oec_id'}},
                       {'paymentFreeValue': False}, {'readBody': value['readBody'] | {'search_params': [{'value': '10'}]}}):
            with self.assertRaisesRegex(CycleError, 'shape_invalid'):
                validate_read_contract(copy.deepcopy(value) | change)

    def test_all_state_br_shape_retains_platform_named_counters_without_guessing_enums(self):
        counts = {'TO_REVIEW_1': 6, 'CANCELED': 9, 'COMPLETED': 6, 'CONTENT_PENDING': 1}
        rows = [br_raw(i, status) for i, status in enumerate([10] * 6 + [40] + [52] * 8 + [56] + [100] * 6)]
        transport = Transport({'code': 0, 'data': {'total': 22, 'sample_records': rows[:20], 'sample_status_num_map': counts}})
        adapter = SampleAdapter(transport, {}, contract=read_contract())
        first = adapter.read_page()
        self.assertEqual(first['platformCounts'], counts)
        self.assertEqual(first['readScope'], 'all_states')
        self.assertTrue(all(r['state'] == 'unknown' and r['paymentRequired'] is None for r in first['records']))
        transport.result.payload['data']['sample_records'] = rows[20:]
        second = adapter.read_page(2)
        self.assertEqual((len(first['records']), len(second['records']), second['total']), (20, 2, 22))
        self.assertEqual(transport.calls[-1]['payload']['cur_page'], 2)

    def test_read_contract_requires_complete_nonnegative_integer_status_counters(self):
        for counts in (None, {'TO_REVIEW_1': True}, {'TO_REVIEW_1': -1}, {'TO_REVIEW_1': 5}):
            data = {'total': 6, 'sample_records': [br_raw()], 'sample_status_num_map': counts}
            with self.assertRaisesRegex(CycleError, 'response_invalid|counters_incomplete'):
                SampleAdapter(Transport({'data': data}), {}, contract=read_contract()).read_page()

    def test_project_read_evidence_requires_original_account_identity_and_complete_pages(self):
        import copy
        import json
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / READ_EVIDENCE
            path.parent.mkdir(parents=True)
            binding = read_contract()['binding']
            shapes = {'$.data.sample_records[].' + value: {'number' if key == 'status' else 'string': 1}
                      for key, value in READ_PATHS.items()}
            shapes['$.data.sample_status_num_map.TO_REVIEW_1'] = {'number': 1}
            evidence = {'schema': 'bdhub.br-sample-field-probe.v1', 'market': 'br', 'account': 'acc2',
                'binding': binding, 'status': 'observed', 'complete': True, 'total': 1, 'recordCount': 1,
                'platformWrites': 0, 'platformWriteAttempts': 0, 'identityFileUnchanged': True,
                'request': {'path': LIST, 'method': 'POST', 'body': OBSERVED_READ_BODY},
                'pages': [{'page': 1, 'http': 200, 'code': 0, 'recordCount': 1, 'ambiguous': False,
                    'verificationRequired': False, 'systemError3': False, 'fields': shapes, 'statusCounts': {'TO_REVIEW_1': 1}}]}
            path.write_text(json.dumps(evidence))
            self.assertEqual(observed_read_contract(root, binding)['binding'], binding)
            with self.assertRaisesRegex(CycleError, 'binding_mismatch'):
                observed_read_contract(root, binding | {'identitySha256': 'changed'})
            incomplete = copy.deepcopy(evidence)
            incomplete['complete'] = False
            path.write_text(json.dumps(incomplete))
            with self.assertRaisesRegex(CycleError, 'incomplete'):
                observed_read_contract(root, binding)

    def test_incomplete_or_unbounded_read_refused(self):
        for payload in ({'data': {}}, {'data': {'total': 1}}, {'data': {'total': -1}},
                        {'data': {'total': 0, 'sample_records': [raw()]}}):
            with self.assertRaisesRegex(CycleError, 'response_invalid'):
                SampleAdapter(Transport(payload), {}).read_page()

    def test_refresh_quota_reads_exact_campaign_and_product_with_real_fields(self):
        import time
        transport = Transport()
        payloads = [
            {'data': {'total_num': 1, 'campaign': [{'campaign_id': '501', 'crs_campaign_type': 7,
                'promotion_end_time': str(int(time.time()) + 1000)}]}},
            {'data': {'total_num': 1, 'campaign_product': [{'product_id': '401', 'product_status': 2, 'sample_quota': 3}]}},
        ]
        def read(**request):
            transport.calls.append(request)
            transport.result.payload = payloads.pop(0)
            return transport.result
        transport._xhr = read
        proof = SampleAdapter(transport, {}).refresh_sample_quota({'pid': '401', 'campaignId': '501'})
        self.assertEqual(proof['quota'], 3)
        self.assertTrue(proof['available'])
        self.assertTrue(proof['supported'])
        self.assertEqual(transport.calls[1]['params']['campaign_id'], '501')
        self.assertEqual(transport.calls[1]['params']['marked'], '0')
        self.assertTrue(all(not call['write'] for call in transport.calls))

    def test_incomplete_offer_page_cannot_claim_product_absence_or_quota(self):
        transport = Transport({'data': {'total_num': 101, 'campaign': []}})
        with self.assertRaisesRegex(CycleError, 'pagination_incomplete'):
            SampleAdapter(transport, {}).refresh_sample_quota({'pid': '401', 'campaignId': '501'})
        self.assertEqual(len(transport.calls), 1)

    def test_real_status_evidence_extends_only_observed_phases_and_publication_facts(self):
        value = extend_canary_contract(canary_contract(), status_evidence())
        self.assertEqual(value['statusMap'], {'10': 'pending', '40': 'content_pending', '100': 'completed'})
        self.assertNotIn('review', value['evidence'])
        self.assertNotIn('lifecycle', value['evidence'])
        source = br_raw(status=40) | {'is_posted': False, 'video_count': 0, 'room_count': 0}
        record = normalize_record(source, contract=value)
        self.assertEqual((record['state'], record['isPosted'], record['videoCount'], record['liveCount']), ('content_pending', False, 0, 0))
        self.assertEqual(normalize_record(br_raw(status=52), contract=value)['state'], 'unknown')
        forged = status_evidence()
        forged['observations'][0]['nativeStatus'] = 10
        with self.assertRaisesRegex(CycleError, 'evidence_conflict'):
            extend_canary_contract(canary_contract(), forged)

    def test_rebinding_probe_observes_identity_without_granting_write_or_guessing_stage(self):
        transport = Transport({'code': 0, 'data': {'total': 1, 'sample_records': [br_raw()], 'sample_status_num_map': {'TO_REVIEW_1': 1}}})
        binding = read_contract()['binding'] | {'identitySha256': 'new-login'}
        adapter = SampleAdapter(transport, {}, probe_binding=binding)
        page = adapter.read_page()
        self.assertIsNone(adapter.contract)
        self.assertEqual(page['readScope'], 'all_states')
        self.assertEqual(page['records'][0]['platformCreatorId'], '20001')
        self.assertEqual(page['records'][0]['state'], 'unknown')
        self.assertIsNone(page['records'][0]['paymentRequired'])
        with self.assertRaisesRegex(CycleError, 'outside_intent'):
            adapter.approve_once({'is_approved': True})
        self.assertTrue(all(not request['write'] for request in transport.calls))

    def test_archived_ui_and_immutable_api_rows_require_exact_creator_disambiguation(self):
        import json, sqlite3, tempfile
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binding = read_contract()['binding']
            rows = []
            for index, native in enumerate((100, 40)):
                rows.append({'applicationId': str(1000000000000000001 + index), 'platformCreatorId': str(2000000000000000001 + index),
                    'oecId': str(3000000000000000001 + index), 'pid': '4000000000000000001', 'campaignId': '5000000000000000001', 'status': native})
            values = {f'$.data.sample_records[{index}].{path}': row[key] for index, row in enumerate(rows) for key, path in READ_PATHS.items()}
            fields = {'$.data.sample_records[].' + path: {'number' if key == 'status' else 'string': 2} for key, path in READ_PATHS.items()}
            fields.update({'$.data.sample_status_num_map.COMPLETED': {'number': 1}, '$.data.sample_status_num_map.CONTENT_PENDING': {'number': 1}})
            proof = {'schema': 'bdhub.br-sample-field-probe.v1', 'market': 'br', 'account': 'acc2', 'binding': binding,
                'status': 'observed', 'complete': True, 'total': 2, 'recordCount': 2, 'platformWrites': 0, 'platformWriteAttempts': 0,
                'identityFileUnchanged': True, 'request': {'path': LIST, 'method': 'POST', 'body': OBSERVED_READ_BODY},
                'pages': [{'page': 1, 'http': 200, 'code': 0, 'recordCount': 2, 'ambiguous': False, 'verificationRequired': False,
                          'systemError3': False, 'fields': fields, 'statusCounts': {'COMPLETED': 1, 'CONTENT_PENDING': 1}, 'businessValues': values}]}
            path = root / READ_EVIDENCE
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(proof))
            folder = root / 'var/releases/br-first-activation-20261023'
            folder.mkdir(parents=True)
            for label, name, handle in [('Completed', 'sample-completed-ui.private.txt', 'done_creator'),
                                        ('Content pending', 'sample-content-pending-ui.private.txt', 'waiting_creator')]:
                (folder / name).write_text('Brazil\n1 row\n2 text ID : 4000000000000000001\n3 text ID: 5000000000000000001\n4 文本 ' + label + '\n5 text @ ' + handle + '\n')
            # Same PID/Campaign occurs twice: labels alone cannot assign either state.
            self.assertIsNone(observed_status_evidence(root, binding))
            with closing(sqlite3.connect(root / 'var/creator-identities.sqlite')) as db, db:
                db.execute('CREATE TABLE creator_identity(market TEXT,oec_id TEXT,current_handle TEXT,handle_conflict INTEGER)')
                db.executemany('INSERT INTO creator_identity VALUES(?,?,?,0)', [('br', rows[0]['oecId'], 'done_creator'), ('br', rows[1]['oecId'], 'waiting_creator')])
            result = observed_status_evidence(root, binding)
            self.assertEqual(validate_status_evidence(result, binding=binding), {'40': 'content_pending', '100': 'completed'})
            self.assertEqual({row['applicationId'] for row in result['observations']}, {row['applicationId'] for row in rows})
            # A unique PID/Campaign still needs the same creator; another creator's
            # Completed row cannot redefine an unrelated cancellation enum.
            one = dict(proof)
            one.update(total=1, recordCount=1)
            single_fields = {'$.data.sample_records[].' + value: {'number' if key == 'status' else 'string': 1} for key, value in READ_PATHS.items()}
            single_fields['$.data.sample_status_num_map.CANCELED'] = {'number': 1}
            single_values = {f'$.data.sample_records[0].{value}': (52 if key == 'status' else rows[0][key]) for key, value in READ_PATHS.items()}
            one['pages'] = [dict(proof['pages'][0], recordCount=1, fields=single_fields, statusCounts={'CANCELED': 1}, businessValues=single_values)]
            path.write_text(json.dumps(one))
            (folder / 'sample-content-pending-ui.private.txt').unlink()
            (folder / 'sample-completed-ui.private.txt').write_text('Brazil\n1 row\n2 text ID : 4000000000000000001\n3 text ID: 5000000000000000001\n4 文本 Completed\n5 text @ different_creator\n')
            self.assertIsNone(observed_status_evidence(root, binding))

    def test_sample_read_failures_are_typed_and_only_transient_reads_retry(self):
        examples = [(200, 10000, True, 'sample_verification_required'),
                    (401, 0, False, 'sample_authentication_required'),
                    (403, 0, False, 'sample_permission_denied'), (429, 0, False, 'sample_rate_limited')]
        for http, code, challenge, expected in examples:
            transport = Transport()
            transport.result.http_status, transport.result.code, transport.result.has_turing = http, code, challenge
            with self.assertRaisesRegex(CycleError, expected):
                SampleAdapter(transport, {}, sleep=lambda _: None).read_page()
            self.assertEqual(len(transport.calls), 1)
        transport = Transport()
        transport.result.http_status = 0
        sleeps = []
        with self.assertRaisesRegex(CycleError, 'sample_network_unavailable'):
            SampleAdapter(transport, {}, sleep=sleeps.append).read_page()
        self.assertEqual((len(transport.calls), sleeps), (2, [.5]))

    def test_strict_single_application_delta_proves_only_one_named_transfer(self):
        import copy
        binding = read_contract()['binding']
        target = normalize_record(br_raw(), contract=canary_contract())
        other = normalize_record(br_raw(1, status=100), contract=canary_contract())
        mapping = {'10': 'pending', '100': 'completed'}
        before = scope_snapshot(binding, [target, other], {'TO_REVIEW_1': 1, 'COMPLETED': 1}, 'observed:before', complete=True)
        after = scope_snapshot(binding, [target | {'nativeStatus': 20}, other], {'TO_REVIEW_1': 0, 'READY_TO_SHIP_1': 1, 'COMPLETED': 1}, 'observed:after', complete=True)
        proven = derive_status_transition(before, after, mapping, target_id=target['applicationId'])
        self.assertEqual((proven['nativeStatus'], proven['state'], proven['sourceKind']), (20, 'ready_to_ship', 'single_application_delta'))
        evidence = status_evidence()
        evidence['observations'].append(proven)
        self.assertEqual(extend_canary_contract(canary_contract(), evidence)['statusMap']['20'], 'ready_to_ship')
        witnessed = extend_canary_contract(canary_contract(), evidence)
        verified = contract() | {'recordPaths': dict(READ_PATHS), 'costPolicy': seller_policy(),
            'binding': binding, 'statusMap': witnessed['statusMap'], 'statusEvidence': witnessed['statusEvidence']}
        validate_contract(verified)
        with self.assertRaisesRegex(CycleError, 'status_evidence_mismatch'):
            validate_contract(verified | {'statusMap': verified['statusMap'] | {'20': 'shipped'}})
        variants = []
        changed = copy.deepcopy(after)
        changed['binding']['identitySha256'] = 'different-session'
        variants.append(changed)
        changed = copy.deepcopy(after)
        changed['members'][0]['oecId'] = '999999'
        variants.append(changed)
        changed = copy.deepcopy(after)
        changed['members'].append(target | {'applicationId': '999999', 'nativeStatus': 10})
        changed['platformCounts']['TO_REVIEW_1'] = 1
        variants.append(changed)
        changed = copy.deepcopy(after)
        changed['members'][1]['nativeStatus'] = 40
        changed['platformCounts'] = {'READY_TO_SHIP_1': 1, 'CONTENT_PENDING': 1}
        variants.append(changed)
        changed = copy.deepcopy(after)
        changed['platformCounts']['READY_TO_SHIP_1'] = 2
        variants.append(changed)
        changed = copy.deepcopy(after)
        changed['platformCounts'] = {'TO_REVIEW_1': 1, 'SHIPPED': 1}
        variants.append(changed)
        for changed in variants:
            with self.subTest(changed=changed):
                self.assertIsNone(derive_status_transition(before, changed, mapping, target_id=target['applicationId']))
        self.assertIsNone(derive_status_transition(before, after, mapping | {'20': 'shipped'}))
        self.assertIsNone(derive_status_transition(before, after | {'evidenceRef': before['evidenceRef']}, mapping))
        self.assertIsNone(derive_status_transition(before, after, mapping, target_id=other['applicationId']))
        self.assertIsNone(scope_snapshot(binding, [target], {'TO_REVIEW_1': 1}, 'partial', complete=False))
        wrong_before = copy.deepcopy(before)
        wrong_before['platformCounts'] = {'TO_REVIEW_1': 2, 'COMPLETED': 0}
        wrong_after = copy.deepcopy(after)
        wrong_after['platformCounts'] = {'TO_REVIEW_1': 1, 'READY_TO_SHIP_1': 1}
        self.assertIsNone(derive_status_transition(wrong_before, wrong_after, mapping))

    def test_receipt_permission_requires_an_unambiguous_accepted_response(self):
        accepted = {'status': 'accepted', 'http': 200, 'code': 0, 'ambiguous': False, 'verificationRequired': False, 'systemError3': False}
        self.assertTrue(receipt_accepted(accepted))
        for altered in (accepted | {'status': 'unknown'}, accepted | {'ambiguous': True}, accepted | {'verificationRequired': True},
                        accepted | {'systemError3': True}, accepted | {'status': 'accepted', 'code': True}):
            self.assertFalse(receipt_accepted(altered))

    def test_commerce_verification_failure_remains_a_challenge_without_plain_replay(self):
        transport = Transport()
        def challenge(**kwargs):
            transport.calls.append(kwargs)
            raise ValueError('commerce_verification_required')
        transport._xhr = challenge
        with self.assertRaisesRegex(CycleError, 'sample_verification_required'):
            SampleAdapter(transport, {}, sleep=lambda _: None).read_page()
        self.assertEqual(len(transport.calls), 1)

    def test_canary_contract_proves_only_pending_and_keeps_review_lifecycle_unverified(self):
        value = canary_contract()
        self.assertEqual(value['statusMap'], {'10': 'pending'})
        self.assertNotIn('review', value['evidence'])
        self.assertNotIn('lifecycle', value['evidence'])
        with self.assertRaises(CycleError):
            validate_contract(value)
        row = normalize_record(br_raw(), contract=value)
        self.assertEqual(row['state'], 'pending')
        self.assertIsNone(row['paymentRequired'])
        self.assertEqual(normalize_record(br_raw(status=99), contract=value)['state'], 'unknown')
        self.assertNotIn('payment', review_payload(row, value['costPolicy']))

    def test_canary_rejects_unproven_approved_status_and_forged_success_evidence(self):
        value = canary_contract()
        for changed in (value | {'statusMap': {'10': 'pending', '99': 'approved'}},
                        value | {'evidence': value['evidence'] | {'review': 'not-real'}},
                        value | {'request': value['request'] | {'requestPayment': 0}}):
            with self.assertRaises(CycleError):
                validate_canary_contract(changed)

    def test_verified_user_cost_authority_does_not_require_a_fabricated_payment_field(self):
        value = contract() | {'costPolicy': seller_policy(), 'recordPaths': dict(READ_PATHS), 'statusMap': {'10': 'pending', '100': 'completed'}}
        value.pop('paymentFreeValue')
        validate_contract(value)
        row = normalize_record(br_raw(), contract=value)
        self.assertIsNone(row['paymentRequired'])
        self.assertEqual(review_payload(row, value['costPolicy'])['is_approved'], True)
        self.assertNotIn('payment', review_payload(row, value['costPolicy']))

    def test_canary_nonzero_business_code_is_unknown_and_never_replayed(self):
        value = canary_contract()
        payload = review_payload(normalize_record(br_raw(), contract=value), value['costPolicy'])
        transport = Transport()
        transport.result.code = 12345
        adapter = SampleAdapter(transport, {}, contract=value, frozen=payload)
        self.assertEqual(adapter.approve_once(payload)['status'], 'unknown')
        with self.assertRaises(CycleError):
            adapter.approve_once(payload)
        self.assertEqual(len(transport.calls), 1)

    def test_canary_cannot_add_any_payment_to_frozen_payload(self):
        value = canary_contract()
        payload = review_payload(normalize_record(br_raw(), contract=value), value['costPolicy'])
        for changed in (payload | {'payment': 0}, payload | {'is_approved': False}):
            with self.assertRaisesRegex(CycleError, 'canary_payload_invalid'):
                SampleAdapter(Transport(), {}, contract=value, frozen=changed)

    def test_success_response_is_accepted_never_platform_approved(self):
        row = normalize_record(raw(), contract=contract())
        payload = review_payload(row)
        report = {}
        transport = Transport()
        adapter = SampleAdapter(transport, report, contract=contract(), frozen=payload)
        self.assertEqual(adapter.approve_once(payload)['status'], 'accepted')
        self.assertEqual(report['platformWriteAttempts'], 1)
        self.assertEqual(report['platformWrites'], 1)
        self.assertEqual(transport.calls[0]['path'], REVIEW)
        with self.assertRaisesRegex(CycleError, 'outside_intent'):
            adapter.approve_once(payload)
        self.assertEqual(len(transport.calls), 1)

    def test_changed_scope_or_no_contract_cannot_write(self):
        payload = review_payload(normalize_record(raw(), contract=contract()))
        transport = Transport()
        with self.assertRaisesRegex(CycleError, 'contract_unverified'):
            SampleAdapter(transport, {}, frozen=payload)
        self.assertEqual(transport.calls, [])
        adapter = SampleAdapter(Transport(), {}, contract=contract(), frozen=payload)
        with self.assertRaisesRegex(CycleError, 'outside_intent'):
            adapter.approve_once(payload | {'creator_id': '201'})
        self.assertEqual(adapter.transport.calls, [])

    def test_ambiguous_write_cannot_be_replayed(self):
        payload = review_payload(normalize_record(raw(), contract=contract()))
        transport = Transport()
        transport.result.http_status = 0
        transport.result.ambiguous = True
        adapter = SampleAdapter(transport, {}, contract=contract(), frozen=payload)
        self.assertEqual(adapter.approve_once(payload)['status'], 'unknown')
        with self.assertRaises(CycleError):
            adapter.approve_once(payload)
        self.assertEqual(len(transport.calls), 1)

    def test_stopped_before_send_means_zero_requests(self):
        payload = review_payload(normalize_record(raw(), contract=contract()))
        adapter = SampleAdapter(Transport(), {}, contract=contract(), frozen=payload, stopped=lambda: True)
        with self.assertRaisesRegex(CycleError, 'stopped'):
            adapter.approve_once(payload)
        self.assertFalse(adapter.transport.calls)


if __name__ == '__main__':
    unittest.main()
