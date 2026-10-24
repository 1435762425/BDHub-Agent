"""Video-author lookups stay bounded to current handles, market and unambiguous identity."""
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from lib.creator_identity import CreatorIdentityStore
from lib.creator_identity_lookup import current_handle_owners


class CurrentHandleLookupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = self.root / 'var/creator-identities.sqlite'

    def test_missing_database_does_not_create_state_and_empty_scope_needs_no_database(self):
        self.assertEqual(current_handle_owners(self.root, 'br', []), {})
        with self.assertRaises(sqlite3.OperationalError):
            current_handle_owners(self.root, 'br', ['creator'])
        self.assertFalse((self.root / 'var').exists())

    def test_scope_unicode_market_conflict_and_ambiguous_current_alias(self):
        with CreatorIdentityStore(self.path) as store:
            for market, oec, handle in [('br','1','JÉSSICA'),('it','2','jéssica'),
                                        ('br','3','outside'),('br','4','conflict'),
                                        ('br','5','reused'),('br','6','reused')]:
                store.observe_profile(market, oec, handle, '2026-10-24T00:00:00Z', 'profile:'+oec)
            store._db.execute("UPDATE creator_identity SET handle_conflict=1 WHERE oec_id='4'")
            expected = store.get_by_oec('br', '1')['creatorId']
            before = store._db.total_changes
            self.assertEqual(current_handle_owners(self.root, 'br', ['JÉSSICA','conflict','reused','absent']),
                             {'jéssica': expected})
            self.assertEqual(store._db.total_changes, before)

    def test_existing_store_upgrade_is_additive_idempotent_and_alias_lookup_is_indexed(self):
        with CreatorIdentityStore(self.path) as store:
            original = store.observe_profile('br', '1', 'creator', '2026-10-24T00:00:00Z', 'original')
            store._db.execute('DROP INDEX identity_market_recent')
        for _ in range(2):
            with CreatorIdentityStore(self.path) as store:
                self.assertEqual(store.get_by_oec('br', '1'), original)
                self.assertIn('identity_market_recent', {r[1] for r in store._db.execute('PRAGMA index_list(creator_identity)')})
                plan = store._db.execute('''EXPLAIN QUERY PLAN SELECT DISTINCT i.creator_id,i.current_handle
                    FROM json_each(?) h CROSS JOIN identity_observation e CROSS JOIN creator_identity i
                    WHERE e.market=? AND e.handle=h.value AND e.kind='profile'
                    AND i.creator_id=e.creator_id AND i.market=e.market
                    AND i.handle_conflict=0 AND i.current_handle=e.handle''', ('["creator"]','br'))
                steps = [r[3] for r in plan]
                self.assertTrue(any('INDEX identity_alias_lookup' in step for step in steps))
                self.assertFalse(any('SCAN i ' in step for step in steps))
                self.assertEqual(len(store.history('br', '1')), 1)
        self.assertEqual(current_handle_owners(self.root, 'br', ['creator']), {'creator': original['creatorId']})


if __name__ == '__main__':
    unittest.main()
