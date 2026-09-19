"""Short names for TapLink cards: what counts as ready, and what filling the gap actually does."""
import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from lib.catalog_names import cached_pids, gap, prepare, scope  # noqa: E402


def fixture(folder, products, names):
    var = Path(folder) / 'var'
    var.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(var / 'catalog-links.sqlite')) as conn, conn:
        conn.execute('CREATE TABLE catalog_prepare_item(pid TEXT,state TEXT,listing TEXT)')
        conn.executemany('INSERT INTO catalog_prepare_item VALUES(?,?,?)',
                         [(pid, state, json.dumps({'title': title, 'product_id': pid}))
                          for pid, state, title in products])
        conn.commit()
    with closing(sqlite3.connect(var / 'second-cycle.sqlite')) as conn, conn:
        conn.execute('CREATE TABLE cycle_product_name(id TEXT PRIMARY KEY,pid TEXT,locale TEXT,'
                     'source_title TEXT,payload TEXT,job_id TEXT)')
        conn.executemany('INSERT INTO cycle_product_name VALUES(?,?,?,?,?,?)',
                         [(f'k{index}', pid, 'it-IT', title, json.dumps(payload), 'job')
                          for index, (pid, title, payload) in enumerate(names)])
        conn.commit()


class FakeCall:
    def __init__(self, fail=False):
        self.fail = fail
        self.calls = []

    def __call__(self, messages, *, max_output_tokens=1000):
        self.calls.append(messages)
        if self.fail:
            raise RuntimeError('provider_http_error')
        payload = json.loads(messages[-1]['content'])
        items = [{'ref': row['ref'], 'shortNameIt': f'nome {row["ref"]}', 'mentionIt': f'questo nome {row["ref"]}',
                  'shortNameZh': f'名称{row["ref"]}'} for row in payload['items']]
        return {'content': json.dumps({'items': items}), 'usage': {'total_tokens': 10}, 'cost': 0.01}


class Readiness(unittest.TestCase):
    def test_only_usable_cached_names_count_as_ready(self):
        with tempfile.TemporaryDirectory() as folder:
            fixture(folder, [('1' * 19, 'missing', 't'), ('2' * 19, 'missing', 't')],
                    [('1' * 19, 't', {'shortNameIt': 'nome buono'}),
                     ('2' * 19, 't', {'shortNameIt': ''})])
            self.assertEqual(cached_pids(folder), {'1' * 19})

    def test_the_cache_is_read_by_product_not_by_title(self):
        with tempfile.TemporaryDirectory() as folder:
            # The title the model saw differs from the title the catalogue will use.
            fixture(folder, [('1' * 19, 'missing', 'Cuscino morbido per il collo')],
                    [('1' * 19, 'cuscino per il collo morbido', {'shortNameIt': 'cuscino cervicale'})])
            self.assertEqual(cached_pids(folder), {'1' * 19})
            self.assertEqual(gap(folder)['ready'], 1)

    def test_only_products_still_waiting_for_a_link_need_a_name(self):
        with tempfile.TemporaryDirectory() as folder:
            fixture(folder, [('1' * 19, 'missing', 'a'), ('2' * 19, 'reuse', 'b'),
                             ('3' * 19, 'missing', 'c'), ('4' * 19, 'ready', 'd'),
                             ('5' * 19, 'review', 'e')], [])
            # reuse/ready already have a card whose name is frozen, and review still needs a human
            # decision, so generating a name for any of them would be wasted work.
            self.assertEqual(sorted(scope(folder)), ['1' * 19, '3' * 19])

    def test_the_gap_counts_ready_against_missing(self):
        with tempfile.TemporaryDirectory() as folder:
            fixture(folder, [('1' * 19, 'missing', 'a'), ('2' * 19, 'missing', 'b')],
                    [('1' * 19, 'a', {'shortNameIt': 'nome'})])
            state = gap(folder)
            self.assertEqual(state['scope'], 2)
            self.assertEqual(state['ready'], 1)
            self.assertEqual(state['missing'], 1)
            self.assertEqual(state['missingSample'][0]['pid'], '2' * 19)

    def test_a_missing_catalogue_or_cache_is_not_an_error(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(scope(folder), {})
            self.assertEqual(cached_pids(folder), set())
            self.assertEqual(gap(folder)['scope'], 0)


class Filling(unittest.TestCase):
    def test_names_are_generated_for_missing_products_only(self):
        with tempfile.TemporaryDirectory() as folder:
            fixture(folder, [('1' * 19, 'missing', 'a'), ('2' * 19, 'missing', 'b')],
                    [('1' * 19, 'a', {'shortNameIt': 'già pronto'})])
            call = FakeCall()
            report = prepare(folder, 10, call=call)
            self.assertEqual(report['requested'], 1)
            self.assertEqual(report['prepared'], 1)
            self.assertEqual(len(call.calls), 1)
            self.assertEqual(gap(folder)['missing'], 0)

    def test_the_provider_batch_cap_is_respected(self):
        with tempfile.TemporaryDirectory() as folder:
            products = [(f'{index}' * 19, 'missing', f't{index}') for index in range(12)]
            fixture(folder, products, [])
            call = FakeCall()
            report = prepare(folder, 12, call=call)
            self.assertEqual(report['prepared'], 12)
            # 12 products at a batch cap of 5 needs three calls.
            self.assertEqual(len(call.calls), 3)
            self.assertTrue(all(len(json.loads(c[-1]['content'])['items']) <= 5 for c in call.calls))

    def test_the_limit_bounds_one_run(self):
        with tempfile.TemporaryDirectory() as folder:
            products = [(f'{index}' * 19, 'missing', f't{index}') for index in range(9)]
            fixture(folder, products, [])
            call = FakeCall()
            self.assertEqual(prepare(folder, 4, call=call)['prepared'], 4)
            self.assertEqual(gap(folder)['missing'], 5)

    def test_a_failed_call_is_reported_and_never_retried(self):
        with tempfile.TemporaryDirectory() as folder:
            fixture(folder, [('1' * 19, 'missing', 'a')], [])
            call = FakeCall(fail=True)
            report = prepare(folder, 5, call=call)
            self.assertEqual(report['prepared'], 0)
            self.assertEqual(len(call.calls), 1)
            # A provider failure surfaces as the existing unresolved-names code.
            self.assertEqual(report['errors'][0]['code'], 'names_request_unresolved')
            self.assertEqual(gap(folder)['missing'], 1)

    def test_nothing_missing_is_a_clean_no_op(self):
        with tempfile.TemporaryDirectory() as folder:
            fixture(folder, [('1' * 19, 'missing', 'a')], [('1' * 19, 'a', {'shortNameIt': 'nome'})])
            call = FakeCall()
            report = prepare(folder, 5, call=call)
            self.assertEqual(report['stopped'], 'nothing_missing')
            self.assertEqual(call.calls, [])

    def test_a_silly_limit_is_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            fixture(folder, [], [])
            for bad in (0, -1, 5001, 'ten'):
                with self.assertRaises(ValueError):
                    prepare(folder, bad, call=FakeCall())



class UnusableCacheRows(unittest.TestCase):
    """A stored row that no reader would accept must never block regeneration."""

    def test_a_name_over_the_reader_limit_is_still_stored_and_usable(self):
        # The writer validates up to 60 characters and render_full fits the card afterwards, so a
        # 34-character name is a real name and must be counted as ready.
        with tempfile.TemporaryDirectory() as folder:
            long_name = 'Scarpe da ginnastica con luci LED'
            self.assertGreater(len(long_name), 30)
            fixture(folder, [('1' * 19, 'missing', 't')],
                    [('1' * 19, 't', {'shortNameIt': long_name})])
            self.assertEqual(cached_pids(folder), {'1' * 19})

    def test_an_empty_or_oversized_name_is_not_ready(self):
        with tempfile.TemporaryDirectory() as folder:
            fixture(folder, [('1' * 19, 'missing', 'a'), ('2' * 19, 'missing', 'b'),
                             ('3' * 19, 'missing', 'c')],
                    [('1' * 19, 'a', {'shortNameIt': ''}),
                     ('2' * 19, 'b', {'shortNameIt': 'x' * 61}),
                     ('3' * 19, 'c', {'other': 1})])
            self.assertEqual(cached_pids(folder), set())



class BatchFailureHandling(unittest.TestCase):
    """One unusable batch must not end the run; only repeated failures should."""

    def test_a_failing_batch_is_skipped_and_the_run_continues(self):
        with tempfile.TemporaryDirectory() as folder:
            products = [(f'{index}' * 19, 'missing', f't{index}') for index in range(12)]
            fixture(folder, products, [])

            calls = {'n': 0}

            def call_count(counter):
                return counter['n']

            def flaky(messages, *, max_output_tokens=1000):
                calls['n'] += 1
                if calls['n'] == 2:  # the middle batch of three
                    raise RuntimeError('provider_output_invalid')
                payload = json.loads(messages[-1]['content'])
                items = [{'ref': row['ref'], 'shortNameIt': f'nome {row["ref"]}',
                          'mentionIt': f'questo nome {row["ref"]}', 'shortNameZh': f'名{row["ref"]}'}
                         for row in payload['items']]
                return {'content': json.dumps({'items': items}), 'usage': {}, 'cost': 0.01}

            report = prepare(folder, 12, call=flaky)
            self.assertEqual(len(report['errors']), 1)
            # 12 products are three batches of 5, 5 and 2; losing the middle one leaves 7.
            self.assertEqual(report['prepared'], 7)
            self.assertEqual(call_count(calls), 3)
            self.assertIsNone(report.get('stopped'))

    def test_repeated_failures_stop_before_burning_the_whole_budget(self):
        with tempfile.TemporaryDirectory() as folder:
            products = [(f'{index}' * 19, 'missing', f't{index}') for index in range(40)]
            fixture(folder, products, [])
            calls = {'n': 0}

            def always(messages, *, max_output_tokens=1000):
                calls['n'] += 1
                raise RuntimeError('provider_http_error')

            report = prepare(folder, 40, call=always)
            self.assertEqual(report['stopped'], 'repeated_batch_failure')
            # Three strikes, not forty.
            self.assertEqual(calls['n'], 3)
            self.assertEqual(len(report['errors']), 3)

    def test_all_missing_covers_whatever_is_left(self):
        with tempfile.TemporaryDirectory() as folder:
            products = [(f'{index}' * 19, 'missing', f't{index}') for index in range(7)]
            fixture(folder, products, [])
            call = FakeCall()
            report = prepare(folder, 2, call=call, all_missing=True)
            self.assertEqual(report['prepared'], 7)
            self.assertEqual(gap(folder)['missing'], 0)


if __name__ == '__main__':
    unittest.main()


class SplitFallback(unittest.TestCase):
    """One impossible product must not take its whole batch down with it."""

    def test_a_batch_that_only_fails_whole_is_retried_per_product(self):
        with tempfile.TemporaryDirectory() as folder:
            products = [(f'{index}' * 19, 'missing', f't{index}') for index in range(3)]
            fixture(folder, products, [])
            seen = {'batches': []}

            def picky(messages, *, max_output_tokens=1000):
                payload = json.loads(messages[-1]['content'])
                size = len(payload['items'])
                seen['batches'].append(size)
                # Any multi-item request produces one over-long mention, so it fails validation;
                # single-item requests come back clean.
                items = []
                for index, row in enumerate(payload['items']):
                    mention = ('questo set di strumenti per la pulizia del viso'
                               if size > 1 and index == 0 else f'questo nome {row["ref"]}')
                    items.append({'ref': row['ref'], 'shortNameIt': f'nome {row["ref"]}',
                                  'mentionIt': mention, 'shortNameZh': f'名{row["ref"]}'})
                return {'content': json.dumps({'items': items}), 'usage': {}, 'cost': 0.01}

            report = prepare(folder, 3, call=picky)
            self.assertEqual(report['prepared'], 3)
            self.assertEqual(report['errors'], [])
            self.assertEqual(gap(folder)['missing'], 0)
            # The batch was asked once, then each product on its own.
            self.assertIn(3, seen['batches'])
            self.assertIn(1, seen['batches'])
