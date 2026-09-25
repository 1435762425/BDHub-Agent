"""Business contracts for bounded Campaign membership verification; all platform I/O is fake."""
import contextlib
import json
import signal
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from test_campaign_join import Fake, A, B, C, NOW, FAR, outcome
from lib.campaign_join import (SCHEMA, Store, apply, db_path, join_all, migrate, preview, status,
                               verify, verification_timeout, VerificationDeadline)


class CampaignContinuity(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def unknown(self, ids=(A, B)):
        fake = Fake(joinable=ids, answer=lambda *_: outcome(code=None, ambiguous=True))
        join_all(self.root, email='a@b.com', confirm=True, transport=fake, clock=lambda: NOW)
        fake.joinable = []
        return fake

    def test_one_unknown_of_ten_does_not_block_nine_confirmed(self):
        ids = [str(1000000000000000000 + i) for i in range(10)]
        fake = Fake(joinable=ids, answer=lambda cid, _: outcome(ambiguous=True, code=None)
                    if cid == ids[0] else outcome())
        result = join_all(self.root, email='a@b.com', confirm=True, transport=fake, clock=lambda: NOW)
        self.assertEqual(result['counts'], {'result_unknown': 1, 'joined': 9})
        self.assertEqual(len(fake.writes), 10)
        self.assertEqual(json.loads(result['items'][0]['request_json']), fake.writes[0][1])

    def test_unknown_in_first_hundred_does_not_block_next_batch(self):
        ids = [str(1000000000000000000 + i) for i in range(103)]
        fake = Fake(joinable=ids, answer=lambda cid, _: outcome(ambiguous=True, code=None)
                    if cid == ids[0] else outcome())
        result = join_all(self.root, email='a@b.com', confirm=True, transport=fake, clock=lambda: NOW)
        self.assertEqual(len(fake.writes), 103)
        self.assertEqual(result['counts']['joined'], 102)

    def test_four_shared_rounds_persist_and_next_day_never_polls_or_posts_again(self):
        fake = self.unknown()
        with patch.object(fake, '_xhr', wraps=fake._xhr) as reads:
            result = verify(self.root, transport=fake, clock=lambda: NOW, bounded=True)
            self.assertEqual(reads.call_count, 8)  # two complete lists per round, shared by two items
        self.assertEqual(result['stoppedUnknown'], [A, B])
        self.assertEqual({r['verify_attempts'] for r in result['items']}, {4})
        frozen = [r['request_json'] for r in result['items']]
        with patch.object(fake, '_xhr', side_effect=AssertionError('unexpected read')):
            verify(self.root, transport=fake, clock=lambda: NOW + 86400, bounded=True)
        fake.joinable = [{'campaign_id': cid, 'promotion_end_time': FAR} for cid in (A, B)]
        later = join_all(self.root, email='new@b.com', confirm=True, transport=fake, clock=lambda: NOW+86400)
        self.assertEqual(len(fake.writes), 2)
        self.assertEqual([r['request_json'] for r in later['items']], frozen)
        self.assertEqual(later['activeVerification'], [])

    def test_restart_retains_remaining_rounds_and_original_deadline(self):
        fake = self.unknown()
        first = verify(self.root, transport=fake, clock=lambda: NOW)
        self.assertEqual(first['items'][0]['verify_attempts'], 1)
        with patch.object(fake, '_xhr', wraps=fake._xhr) as reads:
            resumed = verify(self.root, transport=fake, clock=lambda: NOW+100, bounded=True)
            self.assertEqual(reads.call_count, 6)
        self.assertEqual({r['verify_deadline'] for r in resumed['items']}, {NOW+300})

    def test_expired_deadline_does_not_initialize_transport(self):
        fake = self.unknown()
        verify(self.root, transport=fake, clock=lambda: NOW)
        with patch.object(fake, '__enter__', side_effect=AssertionError('must not initialize')):
            result = verify(self.root, transport=fake, clock=lambda: NOW+301, bounded=True)
        self.assertEqual(result['stoppedUnknown'], [A, B])
        self.assertEqual(result['items'][0]['verify_attempts'], 1)

    def test_setup_time_and_partial_reads_consume_same_budget_without_negative_settlement(self):
        fake = self.unknown()
        now = [NOW]
        class SlowSetup(Fake):
            def __enter__(inner):
                now[0] += 301
                return inner
        result = verify(self.root, transport=SlowSetup(joinable=[A, B]), clock=lambda: now[0], bounded=True)
        self.assertEqual(result['stoppedUnknown'], [A, B])
        self.assertEqual({r['state'] for r in result['items']}, {'result_unknown'})
        self.assertEqual({r['verify_attempts'] for r in result['items']}, {1})

    def test_real_deadline_interrupts_blocking_setup_and_restores_signal(self):
        self.unknown()
        prior = signal.getsignal(signal.SIGALRM)
        class SlowSetup(Fake):
            def __enter__(inner):
                time.sleep(1)
                return inner
        start = time.monotonic()
        with patch('lib.campaign_join.VERIFY_SECONDS', .05):
            result = verify(self.root, transport=SlowSetup(), bounded=True)
        self.assertLess(time.monotonic()-start, .5)
        self.assertEqual(result['stoppedUnknown'], [A, B])
        self.assertEqual(signal.getsignal(signal.SIGALRM), prior)
        self.assertEqual(signal.getitimer(signal.ITIMER_REAL)[0], 0)

    def test_invalid_partial_duplicate_list_does_not_prove_not_joined(self):
        fake = self.unknown()
        class Partial(Fake):
            def _xhr(inner, **kwargs):
                result = super()._xhr(**kwargs)
                result.payload['data']['total'] = 2
                return result
        result = verify(self.root, transport=Partial(joinable=[A]), clock=lambda: NOW, bounded=True)
        self.assertEqual(result['settled'], 0)
        self.assertEqual(result['stoppedUnknown'], [A, B])
        self.assertTrue(all(r['verify_error'] for r in result['items']))
        with self.assertRaisesRegex(ValueError, 'campaign_list_incomplete'):
            preview(self.root, transport=Fake(joinable=[A,A]), clock=lambda: NOW)

    def test_normal_refresh_can_settle_stopped_child_without_special_recheck(self):
        fake = Fake(joinable=[A], answer=lambda *_: outcome(code=None, ambiguous=True, returned=C))
        join_all(self.root, email='a@b.com', confirm=True, transport=fake, clock=lambda: NOW)
        fake.joinable = []
        verify(self.root, transport=fake, clock=lambda: NOW, bounded=True)
        fake.joined_rows = [{'campaign_id': C}]
        result = preview(self.root, transport=fake, clock=lambda: NOW+86400)
        self.assertEqual(result['unresolved'], [])
        self.assertEqual(result['items'][0]['state'], 'joined')
        self.assertEqual(result['items'][0]['verify_attempts'], 4)
        self.assertEqual(len(fake.writes), 1)

    def test_normal_refresh_child_evidence_settles_even_if_parent_still_joinable(self):
        fake = Fake(joinable=[A], answer=lambda *_: outcome(code=None, ambiguous=True, returned=C))
        join_all(self.root, email='a@b.com', confirm=True, transport=fake, clock=lambda: NOW)
        frozen = status(self.root)['items'][0]['request_json']
        fake.joined_rows = [{'campaign_id': C}]
        result = preview(self.root, transport=fake, clock=lambda: NOW+1)
        self.assertEqual(result['unresolved'], [])
        self.assertEqual(result['eligible'], 0)
        self.assertEqual(result['items'][0]['write_attempted'], 1)
        self.assertEqual(result['items'][0]['joined_campaign_id'], C)
        self.assertEqual(result['items'][0]['request_json'], frozen)

    def test_original_account_conflict_performs_no_platform_read(self):
        fake = self.unknown()
        with patch('lib.campaign_join._account_name', return_value='different-account'):
            with self.assertRaisesRegex(ValueError, 'campaign_join_request_conflict'):
                verify(self.root, transport=fake, clock=lambda: NOW, bounded=True)

    def test_auth_failure_stops_new_posts_but_is_not_explicit_membership_rejection(self):
        fake = Fake(joinable=[A,B], answer=lambda *_: outcome(code=16201010))
        result = join_all(self.root, email='a@b.com', confirm=True, transport=fake, clock=lambda: NOW)
        self.assertTrue(result['accountBlocked'])
        self.assertEqual(len(fake.writes), 1)
        self.assertEqual(result['items'][0]['state'], 'result_unknown')

    def test_status_is_read_only_and_migration_additive_idempotent(self):
        self.assertFalse(status(self.root)['available'])
        self.assertFalse((self.root/'var').exists())
        path = db_path(self.root)
        path.parent.mkdir()
        with contextlib.closing(sqlite3.connect(path)) as db, db:
            db.executescript(SCHEMA)
            db.execute("INSERT INTO campaign_join_job VALUES('campaign-join','it','','needs_verification','apply','','old@b.com','[]',2,1,2)")
            db.execute("INSERT INTO campaign_join_item VALUES('campaign-join',?,'old','result_unknown','original',1,?,2)",(A,C))
        before = path.read_bytes()
        self.assertEqual(status(self.root)['unresolved'], [A])
        self.assertEqual(path.read_bytes(), before)
        with self.assertRaisesRegex(ValueError, 'campaign_join_migration_required'):
            Store(self.root)
        self.assertEqual(len(migrate(self.root)['added']), 5)
        self.assertEqual(migrate(self.root)['added'], [])
        result = status(self.root)
        self.assertEqual(result['items'][0]['reason'], 'original')
        self.assertEqual(result['items'][0]['joined_campaign_id'], C)
        self.assertEqual(result['items'][0]['request_json'], '{}')  # never invent old evidence

    def test_crash_during_read_preserves_attempt_and_deadline(self):
        fake = self.unknown()
        with patch.object(fake, '_xhr', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                verify(self.root, transport=fake, clock=lambda: NOW, bounded=True)
        persisted = status(self.root, clock=lambda: NOW)
        self.assertEqual(persisted['items'][0]['verify_attempts'], 1)
        resumed = verify(self.root, transport=fake, clock=lambda: NOW+10, bounded=True)
        self.assertEqual(resumed['items'][0]['verify_attempts'], 4)
        self.assertEqual(resumed['items'][0]['verify_deadline'], NOW+300)

    def test_market_operation_lock_refuses_concurrent_mutation(self):
        import fcntl
        fake = self.unknown()
        with db_path(self.root).with_suffix('.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(ValueError, 'campaign_join_busy'):
                verify(self.root, transport=fake, clock=lambda: NOW, bounded=True)
        self.assertEqual(status(self.root)['items'][0]['verify_attempts'], 0)

    def test_all_confirmed_exits_after_first_read_round(self):
        fake = self.unknown()
        fake.joined_rows = [{'campaign_id': A},{'campaign_id': B}]
        with patch.object(fake, '_xhr', wraps=fake._xhr) as reads:
            result = verify(self.root, transport=fake, clock=lambda: NOW, bounded=True)
        self.assertEqual(reads.call_count, 2)
        self.assertEqual(result['joinedSettled'], 2)


if __name__ == '__main__':
    unittest.main()
