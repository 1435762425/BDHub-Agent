import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from lib.lead_pool import _eligible_pids


class EligibleCacheTests(unittest.TestCase):
    def test_new_catalog_head_invalidates_sender_only_cache(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'state.sqlite'
            db = sqlite3.connect(path)
            try:
                db.executescript('''CREATE TABLE plan(id TEXT,market TEXT);
                  CREATE TABLE catalog(id TEXT,payload TEXT);
                  CREATE TABLE catalog_head(plan_id TEXT,snapshot_id TEXT);''')
                db.execute("INSERT INTO plan VALUES('plan','uk')")
                for key in ('first','second'):
                    db.execute('INSERT INTO catalog VALUES(?,?)', (key, json.dumps([{'pid': key}])))
                db.execute("INSERT INTO catalog_head VALUES('plan','first')")
                tables = {'plan','catalog','catalog_head'}
                with patch('lib.second_cycle.assess_offer', return_value={'eligible': True}) as assess:
                    self.assertEqual(_eligible_pids(db, 1, tables, 'uk', db_path=str(path), cache_seconds=30), {'first'})
                    self.assertEqual(_eligible_pids(db, 1, tables, 'uk', db_path=str(path), cache_seconds=30), {'first'})
                    self.assertEqual(assess.call_count, 1)
                    db.execute("UPDATE catalog_head SET snapshot_id='second' WHERE plan_id='plan'")
                    self.assertEqual(_eligible_pids(db, 1, tables, 'uk', db_path=str(path), cache_seconds=30), {'second'})
                    self.assertEqual(assess.call_count, 2)
            finally:
                db.close()


if __name__ == '__main__': unittest.main()
