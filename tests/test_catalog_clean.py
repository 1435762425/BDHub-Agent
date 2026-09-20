"""TapLink cleaning: the reused legacy rule, the decision table, and the delete guards."""
import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from lib.catalog_clean import CatalogClean, classify_card, decide, used_list_ids  # noqa: E402


def member(pid='1729480061238089885', status='2', unavailable=None, governed=None, stock='25', campaign='7685262119046498070'):
    return {'pid': pid, 'product_status': status, 'unavailable_type': unavailable,
            'governed': governed, 'stock': stock, 'member_campaign_id': campaign}


class HealthRule(unittest.TestCase):
    """The rule itself is the legacy one; these pin the behaviour the cleaner relies on."""

    def test_a_healthy_card_stays_valid(self):
        self.assertEqual(classify_card([member()])['state'], 'valid')

    def test_governed_delisted_and_closed_are_invalid(self):
        for unavailable in ('4', '6', '7', '3'):
            self.assertEqual(classify_card([member(unavailable=unavailable, governed=1)])['state'], 'invalid', unavailable)
        self.assertEqual(classify_card([member(governed=1)])['state'], 'invalid')

    def test_temporary_stock_absence_is_kept_not_deleted(self):
        self.assertEqual(classify_card([member(unavailable='8', stock='0')])['state'], 'valid')
        self.assertEqual(classify_card([member(stock='0')])['state'], 'valid')

    def test_an_ended_campaign_invalidates_the_card(self):
        campaigns = {'7685262119046498070': {'start_time': 1, 'end_time': 2}}
        self.assertEqual(classify_card([member()], campaigns)['state'], 'invalid')

    def test_an_unreadable_status_is_unknown_never_a_deletion(self):
        self.assertEqual(classify_card([member(status=None)])['state'], 'unknown')

    def test_a_mixed_list_keeps_everything(self):
        verdict = classify_card([member(pid='1' * 19), member(pid='2' * 19, unavailable='6', governed=1)])
        self.assertEqual(verdict['state'], 'mixed')


class DecisionTable(unittest.TestCase):
    def test_platform_confirmed_invalid_is_a_delete_candidate_even_when_used(self):
        self.assertEqual(decide('invalid', False)[0], 'delete_candidate')
        self.assertEqual(decide('invalid', True)[0], 'delete_candidate')
        self.assertEqual(decide('mixed', False)[0], 'keep')
        self.assertEqual(decide('unknown', False)[0], 'review')
        self.assertEqual(decide('valid', False)[0], 'keep')


class DeleteGuards(unittest.TestCase):
    def _clean(self, root):
        (Path(root) / 'var').mkdir(parents=True, exist_ok=True)
        return CatalogClean(root)

    def test_only_candidates_freeze_and_the_intent_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            clean = self._clean(tmp)
            try:
                run = clean.open_run()
                clean.save_item(run, '1' * 19, health='valid', reason='ok', decision='keep')
                clean.save_item(run, '2' * 19, health='invalid', reason='治理', decision='delete_candidate')
                clean.save_item(run, '3' * 19, health='invalid', reason='治理', decision='delete_candidate', used=True)
                with self.assertRaisesRegex(ValueError, 'catalog_clean_not_eligible'):
                    clean.freeze_delete(run, '1' * 19)
                first = clean.freeze_delete(run, '2' * 19)
                again = clean.freeze_delete(run, '2' * 19)
                self.assertEqual(first['id'], again['id'])
                self.assertEqual(first['state'], 'prepared')
                self.assertEqual(json.loads(first['payload']), {'list_id': '2' * 19})
                used = clean.freeze_delete(run, '3' * 19)
                self.assertEqual(used['state'], 'prepared')
                self.assertEqual(len(clean.pending_deletes(run)), 2)
            finally:
                clean.close()

    def test_a_missing_item_cannot_be_frozen(self):
        with tempfile.TemporaryDirectory() as tmp:
            clean = self._clean(tmp)
            try:
                with self.assertRaisesRegex(ValueError, 'catalog_clean_item_missing'):
                    clean.freeze_delete(clean.open_run(), '9' * 19)
            finally:
                clean.close()


class UsedLinks(unittest.TestCase):
    def test_used_list_ids_come_from_the_frozen_delivery_snapshots(self):
        with tempfile.TemporaryDirectory() as tmp:
            var = Path(tmp) / 'var'
            var.mkdir(parents=True)
            with closing(sqlite3.connect(var / 'second-cycle.sqlite')) as db,db:
                db.execute('CREATE TABLE cycle_delivery(id TEXT PRIMARY KEY,snapshot TEXT)')
                db.execute('INSERT INTO cycle_delivery VALUES(?,?)', ('d1', json.dumps({'card': {'listId': '8650745929916717846'}})))
                db.execute('INSERT INTO cycle_delivery VALUES(?,?)', ('d2', json.dumps({'card': {}})))
            self.assertEqual(used_list_ids(tmp), {'8650745929916717846'})
            self.assertEqual(used_list_ids(str(Path(tmp) / 'nope')), set())


if __name__ == '__main__':
    unittest.main()
