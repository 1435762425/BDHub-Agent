import sys
from pathlib import Path
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from lib.sample_transport import (CONTRACT_SCHEMA, LIST, REVIEW, SampleAdapter, normalize_record,
                                  review_payload, validate_contract)
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
        self.assertEqual(transport.calls[0]['payload']['search_params'][0]['value'], '2')

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
        for adapter, changed in ((SampleAdapter(Transport(), {}, frozen=payload), payload),
                                  (SampleAdapter(Transport(), {}, contract=contract(), frozen=payload), payload | {'creator_id': '201'})):
            with self.assertRaisesRegex(CycleError, 'outside_intent'):
                adapter.approve_once(changed)
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
