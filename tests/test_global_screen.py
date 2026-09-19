"""Screen-at-collection: threshold config, per-product decisions, and the collection funnel."""
import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from lib.global_selection import settled_pids  # noqa: E402
from lib.global_screen import (DEFAULTS, Screen, config_path, evaluate, fingerprint, funnel,  # noqa: E402
                               load, preview, sales, save, screen_run_id, screen_source, validate)
from lib.global_selection import assess  # noqa: E402

BASE = validate(DEFAULTS)


def product(pid, *, sales_value='500 已售', rating=4.5, total=1400, public=900, selected=False):
    return {'product_id': pid, 'sales': sales_value, 'product_rating': rating,
            'commission_rate': total, 'open_collab_rate': public,
            'fs_is_selected': selected, 'title': 'prodotto', 'source': 'opportunity_global_only'}


def source_db(folder, run_id, products):
    path = Path(folder) / 'global-source.sqlite'
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript('CREATE TABLE global_source_run(id TEXT PRIMARY KEY,state TEXT,created REAL,updated REAL);'
                           'CREATE TABLE global_source_product(run_id TEXT,pid TEXT,payload TEXT,PRIMARY KEY(run_id,pid));')
        conn.execute('INSERT INTO global_source_run VALUES(?,?,?,?)', (run_id, 'completed', 1.0, 2.0))
        conn.executemany('INSERT INTO global_source_product VALUES(?,?,?)',
                         [(run_id, p['product_id'], json.dumps(p)) for p in products])
        conn.commit()
    return path


class SalesParsing(unittest.TestCase):
    def test_both_platform_formats_are_parsed(self):
        self.assertEqual(sales('2.349 已售'), 2349)
        self.assertEqual(sales('300 已售'), 300)
        self.assertEqual(sales('1,2K 已售'), 1200)
        # An unknown shape must stay unknown rather than be guessed at zero.
        for bad in ['已售', None, 500, '1.2K 已售', '']:
            self.assertIsNone(sales(bad))


class Thresholds(unittest.TestCase):
    def test_each_threshold_applies_at_its_boundary(self):
        self.assertTrue(evaluate(product('1', sales_value='300 已售'), BASE)['eligible'])
        self.assertIn('sales_below_min', evaluate(product('1', sales_value='299 已售'), BASE)['reasons'])
        self.assertTrue(evaluate(product('1', rating=4.0), BASE)['eligible'])
        self.assertIn('rating_below_min', evaluate(product('1', rating=3.9), BASE)['reasons'])
        # Total 14% vs public 12% is exactly the two-point gap and must pass.
        self.assertTrue(evaluate(product('1', total=1400, public=1200), BASE)['eligible'])
        self.assertIn('commission_gap_below_min', evaluate(product('1', total=1399, public=1200), BASE)['reasons'])

    def test_unrated_is_recorded_as_a_fact_not_treated_as_a_bad_rating(self):
        quiet = product('1', rating=0)
        allowed = evaluate(quiet, BASE)
        self.assertTrue(allowed['eligible'])
        self.assertTrue(allowed['unrated'])
        strict = evaluate(quiet, {**BASE, 'allowUnrated': False})
        self.assertEqual(strict['reasons'], ['rating_unrated'])

    def test_missing_fields_are_reported_rather_than_guessed(self):
        result = evaluate({'product_id': '1'}, BASE)
        self.assertEqual(result['reasons'], ['sales_missing', 'commission_missing'])
        self.assertIsNone(result['units'])
        self.assertIsNotNone(evaluate(product('1'), BASE)['gapPoints'])


class ConfigFile(unittest.TestCase):
    def test_saved_thresholds_round_trip_and_are_bounded(self):
        with tempfile.TemporaryDirectory() as folder:
            saved = save(folder, {'minSales': 200, 'minRating': 4.5, 'minCommissionGapPoints': 3})
            self.assertEqual(load(folder), saved)
            self.assertEqual(load(folder)['minSales'], 200)
            for bad in [{'minSales': -1}, {'minSales': '300'}, {'minSales': 300.5},
                        {'minRating': 5.1}, {'minCommissionGapPoints': 101}, {'allowUnrated': 'yes'}]:
                with self.assertRaises(ValueError):
                    validate({**DEFAULTS, **bad})

    def test_a_rejected_threshold_never_touches_the_file(self):
        with tempfile.TemporaryDirectory() as folder:
            save(folder, {'minSales': 250})
            before = config_path(folder).read_text(encoding='utf-8')
            with self.assertRaises(ValueError):
                save(folder, {'minSales': -5})
            self.assertEqual(config_path(folder).read_text(encoding='utf-8'), before)
            self.assertEqual(load(folder)['minSales'], 250)

    def test_a_threshold_change_gets_its_own_screening_record(self):
        self.assertNotEqual(screen_run_id('run-1', validate({'minSales': 300})),
                            screen_run_id('run-1', validate({'minSales': 200})))
        self.assertEqual(screen_run_id('run-1', validate({'minSales': 300})),
                         screen_run_id('run-1', validate({'minSales': 300})))


class Screening(unittest.TestCase):
    def test_one_decision_is_recorded_per_collected_product(self):
        with tempfile.TemporaryDirectory() as folder:
            products = [product('1', selected=True), product('2'),
                        product('3', sales_value='10 已售'), product('4', rating=3.0)]
            source_db(folder, 'run-1', products)
            path = Path(folder) / 'global-source.sqlite'
            record = screen_source(path, 'run-1', root=folder, config=BASE)
            self.assertEqual(record['counts'], {'eligible': 2, 'rejected': 2})
            self.assertEqual(record['reasons'], {'sales_below_min': 1, 'rating_below_min': 1})
            numbers = funnel(path, record['runId'])
            self.assertEqual(numbers['collected'], 4)
            self.assertEqual(numbers['eligible'], 2)
            self.assertEqual(numbers['selectedEligible'], 1)
            self.assertEqual(numbers['unselectedEligible'], 1)
            self.assertEqual(numbers['unknownSelectedFlag'], 0)

    def test_only_eligible_unselected_products_are_offered_for_intake(self):
        with tempfile.TemporaryDirectory() as folder:
            source_db(folder, 'run-1', [product('1', selected=True), product('2'),
                                        product('3', sales_value='10 已售')])
            path = Path(folder) / 'global-source.sqlite'
            record = screen_source(path, 'run-1', root=folder, config=BASE)
            ledger = Screen(path)
            try:
                self.assertEqual(ledger.unselected_eligible(record['runId']), ['2'])
            finally:
                ledger.close()

    def test_rescreening_the_same_run_replaces_its_own_record_only(self):
        with tempfile.TemporaryDirectory() as folder:
            source_db(folder, 'run-1', [product('1', sales_value='250 已售')])
            path = Path(folder) / 'global-source.sqlite'
            strict = screen_source(path, 'run-1', root=folder, config=BASE)
            loose = screen_source(path, 'run-1', root=folder, config=validate({**DEFAULTS, 'minSales': 200}))
            self.assertNotEqual(strict['runId'], loose['runId'])
            self.assertEqual(funnel(path, strict['runId'])['eligible'], 0)
            self.assertEqual(funnel(path, loose['runId'])['eligible'], 1)

    def test_preview_reports_what_a_change_would_add_without_writing(self):
        with tempfile.TemporaryDirectory() as folder:
            source_db(folder, 'run-1', [product('1', sales_value='250 已售'), product('2'), product('3')])
            path = Path(folder) / 'global-source.sqlite'
            before = Screen(path)
            try:
                # Nothing has been screened yet: preview must not create a screening record.
                self.assertIsNone(before.latest_run())
            finally:
                before.close()
            result = preview(path, 'run-1', validate({**DEFAULTS, 'minSales': 200}), active=BASE)
            after = Screen(path)
            try:
                self.assertIsNone(after.latest_run())
            finally:
                after.close()
            self.assertEqual(result['addedVersusActive'], 1)
            self.assertEqual(result['removedVersusActive'], 0)
            self.assertEqual(result['eligible'], 3)
            self.assertEqual(result['unselectedEligible'], 3)
            self.assertEqual(result['reasons'], {})

    def test_the_intake_filter_uses_the_same_configurable_thresholds(self):
        quiet = product('1', sales_value='250 已售')
        self.assertFalse(assess(quiet)['eligible'])
        self.assertTrue(assess(quiet, validate({**DEFAULTS, 'minSales': 200}))['eligible'])


class LiveSelectionState(unittest.TestCase):
    """The funnel must read the pool as it is now, not as the collection snapshot saw it.

    The snapshot keeps saying "not selected" for every product we selected afterwards, so a page
    that trusts it alone keeps reporting work that is already done.
    """

    def test_a_product_confirmed_into_the_pool_counts_as_selected(self):
        with tempfile.TemporaryDirectory() as folder:
            source_db(folder, 'run-1', [product('1', selected=False), product('2', selected=True)])
            path = Path(folder) / 'global-source.sqlite'
            record = screen_source(path, 'run-1', root=folder, config=BASE)
            stale = funnel(path, record['runId'])
            self.assertEqual(stale['unselectedEligible'], 1)
            self.assertEqual(stale['selectedEligible'], 1)
            live = funnel(path, record['runId'], {'1'})
            self.assertEqual(live['selectedEligible'], 2)
            self.assertEqual(live['unselectedEligible'], 0)

    def test_a_product_nobody_confirmed_still_counts_as_waiting(self):
        with tempfile.TemporaryDirectory() as folder:
            source_db(folder, 'run-1', [product('1', selected=False)])
            path = Path(folder) / 'global-source.sqlite'
            record = screen_source(path, 'run-1', root=folder, config=BASE)
            self.assertEqual(funnel(path, record['runId'], set())['unselectedEligible'], 1)

    def test_only_proof_of_pool_membership_settles_a_product(self):
        import sqlite3 as sql
        with tempfile.TemporaryDirectory() as folder:
            var = Path(folder) / 'var'
            var.mkdir(parents=True, exist_ok=True)
            with sql.connect(var / 'global-selection.sqlite') as conn:
                conn.execute('CREATE TABLE intake_item(run_id TEXT,pid TEXT,state TEXT)')
                conn.executemany('INSERT INTO intake_item VALUES(?,?,?)',
                                 [('r', 'a', 'confirmed'), ('r', 'b', 'already_selected'),
                                  ('r', 'c', 'pending'), ('r', 'd', 'filtered'),
                                  ('r', 'e', 'needs_review'), ('r', 'f', 'result_unknown')])
                conn.commit()
            # Pending, filtered, needs-review and unknown are not proof of being in the pool.
            self.assertEqual(settled_pids(folder), {'a', 'b'})

    def test_a_missing_selection_ledger_settles_nothing(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(settled_pids(folder), set())


if __name__ == '__main__':
    unittest.main()
