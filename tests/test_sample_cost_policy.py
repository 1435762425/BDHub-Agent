import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from lib.sample_cost_policy import (BASIS_SCHEMA, POLICY_PATH, POLICY_SCHEMA, USER_CONFIRMATION,
                                    load_cost_policy, payment_basis, validate_cost_policy)
from lib.second_cycle import CycleError, digest

NOW = datetime(2026, 10, 23, 12, 40, tzinfo=timezone.utc).timestamp()


def policy():
    return {'schema': POLICY_SCHEMA, 'market': 'br', 'payer': 'seller', 'agencyPaymentAllowed': False,
            'requestPayment': 'omit', 'authority': 'user', 'authorityEvidence': USER_CONFIRMATION,
            'confirmedAt': '2026-10-23T12:30:00Z', 'revision': 1}


class SampleCostPolicyTest(unittest.TestCase):
    def test_valid_user_policy_returns_detached_snapshot(self):
        original = policy()
        result = validate_cost_policy(original, now=NOW)
        self.assertEqual(result, original)
        result['revision'] = 2
        self.assertEqual(original['revision'], 1)

    def test_schema_authority_scope_and_no_agency_payment_are_strict(self):
        invalid = [None, [], {}, policy() | {'schema': 'other'}, policy() | {'market': 'mx'},
                   policy() | {'payer': 'agency'}, policy() | {'authority': 'model'},
                   policy() | {'authorityEvidence': 'historical default'},
                   policy() | {'authorityEvidence': USER_CONFIRMATION + '。'},
                   policy() | {'agencyPaymentAllowed': True}, policy() | {'agencyPaymentAllowed': 0},
                   policy() | {'agencyPaymentAllowed': 'false'}, policy() | {'requestPayment': False},
                   policy() | {'requestPayment': '0'}, policy() | {'revision': True},
                   policy() | {'revision': 1.0}, policy() | {'revision': 0},
                   policy() | {'revision': 2 ** 53}, policy() | {'platformAmount': 0}]
        for value in invalid:
            with self.subTest(value=value), self.assertRaisesRegex(CycleError, 'policy_invalid'):
                validate_cost_policy(value, now=NOW)
        missing = policy()
        del missing['authorityEvidence']
        with self.assertRaisesRegex(CycleError, 'policy_invalid'):
            validate_cost_policy(missing, now=NOW)

    def test_confirmation_requires_valid_explicit_timezone_and_cannot_be_future(self):
        invalid = [None, True, NOW, '2026-10-23', '2026-10-23T12:30:00',
                   '2026-02-30T12:30:00Z', '2026-10-23T12:40:01Z', '2026-10-23T12:30:00+25:00']
        for confirmed in invalid:
            with self.subTest(confirmed=confirmed), self.assertRaisesRegex(CycleError, 'time_invalid'):
                validate_cost_policy(policy() | {'confirmedAt': confirmed}, now=NOW)
        self.assertTrue(validate_cost_policy(policy() | {'confirmedAt': '2026-10-23T20:30:00+08:00'}, now=NOW))
        self.assertTrue(validate_cost_policy(policy() | {'confirmedAt': '2026-10-23T12:40:00Z'}, now=NOW))

    def test_invalid_clock_does_not_validate_policy(self):
        for current in (True, 0, float('nan'), float('inf'), 'now'):
            with self.subTest(current=current), self.assertRaisesRegex(CycleError, 'clock_invalid'):
                validate_cost_policy(policy(), now=current)

    def test_missing_policy_read_creates_nothing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'absent-project'
            self.assertIsNone(load_cost_policy(root, now=NOW))
            self.assertFalse(root.exists())

    def test_private_policy_read_is_exact_and_does_not_rewrite_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / POLICY_PATH
            path.parent.mkdir(parents=True)
            source = json.dumps(policy(), ensure_ascii=False).encode()
            path.write_bytes(source)
            before = path.stat().st_mtime_ns
            self.assertEqual(load_cost_policy(root, now=NOW), policy())
            self.assertEqual(path.read_bytes(), source)
            self.assertEqual(path.stat().st_mtime_ns, before)

    def test_corrupt_duplicate_oversized_or_invalid_policy_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / POLICY_PATH
            path.parent.mkdir(parents=True)
            duplicate = json.dumps(policy())[:-1] + ',"revision":2}'
            for source in (b'not-json', b'\xff', b'x' * 8193, duplicate.encode(), json.dumps(policy() | {'payer': 'agency'}).encode()):
                path.write_bytes(source)
                with self.subTest(source=source[:50]), self.assertRaisesRegex(CycleError, 'policy_invalid'):
                    load_cost_policy(root, now=NOW)

    def test_platform_payment_required_always_blocks_even_with_confirmed_policy(self):
        for value in (None, policy(), policy() | {'payer': 'agency'}):
            with self.subTest(value=value), self.assertRaisesRegex(CycleError, 'payment_requires_handling'):
                payment_basis({'paymentRequired': True}, value, now=NOW)

    def test_platform_explicit_false_uses_fact_without_claiming_user_policy(self):
        result = payment_basis({'paymentRequired': False})
        self.assertEqual(result['source'], 'platform_fact')
        self.assertIs(result['snapshot']['paymentRequired'], False)
        self.assertIsNone(result['snapshot']['policy'])
        self.assertEqual(result['snapshot']['requestPayment'], 'omit')
        self.assertEqual(result['fingerprint'], digest(result['snapshot']))
        self.assertEqual(payment_basis({'paymentRequired': False}, policy(), now=NOW), result)

    def test_unknown_cost_requires_user_policy_and_preserves_unknown_platform_fact(self):
        application = {'applicationId': '10001', 'paymentRequired': None, 'campaign': {'sample_budget': {}}}
        original = copy.deepcopy(application)
        with self.assertRaisesRegex(CycleError, 'payment_requires_handling'):
            payment_basis(application)
        result = payment_basis(application, policy(), now=NOW)
        self.assertEqual(application, original)
        self.assertIsNone(result['snapshot']['paymentRequired'])
        self.assertEqual(result['snapshot']['schema'], BASIS_SCHEMA)
        self.assertEqual(result['source'], 'user_policy')
        self.assertEqual(result['snapshot']['policy'], policy())
        self.assertEqual(result['fingerprint'], digest(result['snapshot']))
        self.assertEqual(payment_basis({}, policy(), now=NOW)['source'], 'user_policy')

    def test_nonboolean_payment_values_and_empty_budget_cannot_be_free_facts(self):
        for value in (0, 1, '0', 'false', {}, []):
            with self.subTest(value=value), self.assertRaisesRegex(CycleError, 'payment_requires_handling'):
                payment_basis({'paymentRequired': value}, policy(), now=NOW)
        with self.assertRaisesRegex(CycleError, 'payment_requires_handling'):
            payment_basis({'sample_budget': {}})

    def test_policy_fingerprint_is_stable_detached_and_changes_with_revision(self):
        original = policy()
        first = payment_basis({'paymentRequired': None}, original, now=NOW)
        reordered = dict(reversed(list(original.items())))
        self.assertEqual(payment_basis({}, reordered, now=NOW)['fingerprint'], first['fingerprint'])
        original['revision'] = 2
        self.assertEqual(first['snapshot']['policy']['revision'], 1)
        self.assertNotEqual(payment_basis({}, original, now=NOW)['fingerprint'], first['fingerprint'])

    def test_invalid_or_future_policy_cannot_resolve_unknown_cost(self):
        for value in (policy() | {'authority': 'model'}, policy() | {'confirmedAt': '2026-10-23T12:40:01Z'}):
            with self.subTest(value=value), self.assertRaises(CycleError):
                payment_basis({'paymentRequired': None}, value, now=NOW)


if __name__ == '__main__':
    unittest.main()
