import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from lib.operations_policy import published_plain_source

SCHEMA='''
CREATE TABLE global_source_run(id TEXT PRIMARY KEY,state TEXT,identity_unchanged INTEGER,scope TEXT,terminal_reason TEXT);
CREATE TABLE global_source_head(scope_hash TEXT PRIMARY KEY,run_id TEXT);
CREATE TABLE global_source_product(run_id TEXT,pid TEXT);
'''


class PublishedPlainSource(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);(self.root/'var').mkdir()
        self.path=self.root/'var/global-source.sqlite'
        self.db=sqlite3.connect(self.path);self.db.executescript(SCHEMA)

    def tearDown(self):
        self.db.close();self.tmp.cleanup()

    def run_row(self,run_id,*,state='completed',identity=1,market='it',partition=None,overlay=None,terminal=None):
        scope={'market':market,'account':'acc9'}
        if partition:scope['partitionMode']=partition
        if overlay:scope['coverageOverlay']=overlay
        self.db.execute('INSERT INTO global_source_run VALUES(?,?,?,?,?)',
                        (run_id,state,identity,json.dumps(scope),terminal))

    def head(self,scope_hash,run_id):
        self.db.execute('INSERT INTO global_source_head VALUES(?,?)',(scope_hash,run_id))

    def products(self,run_id,count):
        for index in range(count):
            self.db.execute('INSERT INTO global_source_product VALUES(?,?)',(run_id,f'pid-{index}'))

    def test_head_is_the_run_itself(self):
        self.run_row('run-1');self.head('h1','run-1');self.products('run-1',3);self.db.commit()
        self.assertEqual(published_plain_source(self.root,'it','run-1'),
                         {'sourceRunId':'run-1','headRunId':'run-1','products':3})

    def test_head_is_a_coverage_overlay_refreshing_the_run(self):
        self.run_row('run-1');self.run_row('run-2',overlay={'refreshRunId':'run-1'})
        self.head('h1','run-2');self.products('run-2',2);self.db.commit()
        self.assertEqual(published_plain_source(self.root,'it','run-1'),
                         {'sourceRunId':'run-1','headRunId':'run-2','products':2})

    def test_unrelated_second_head_for_the_same_market_does_not_matter(self):
        self.run_row('run-1');self.run_row('run-9')
        self.head('h1','run-1');self.head('h2','run-9');self.products('run-1',1);self.db.commit()
        self.assertEqual(published_plain_source(self.root,'it','run-1'),
                         {'sourceRunId':'run-1','headRunId':'run-1','products':1})

    def test_head_for_another_run_only_is_none(self):
        self.run_row('run-1');self.run_row('run-9');self.head('h1','run-9');self.db.commit()
        self.assertIsNone(published_plain_source(self.root,'it','run-1'))

    def test_source_not_completed_is_none(self):
        self.run_row('run-1',state='collecting');self.head('h1','run-1');self.db.commit()
        self.assertIsNone(published_plain_source(self.root,'it','run-1'))

    def test_identity_changed_is_none(self):
        self.run_row('run-1',identity=0);self.head('h1','run-1');self.db.commit()
        self.assertIsNone(published_plain_source(self.root,'it','run-1'))

    def test_category_partition_source_is_none(self):
        self.run_row('run-1',partition='category_l1_v1');self.head('h1','run-1');self.db.commit()
        self.assertIsNone(published_plain_source(self.root,'it','run-1'))

    def test_database_missing_is_none(self):
        self.assertIsNone(published_plain_source(self.root/'missing','it','run-1'))

    def test_two_heads_both_referencing_the_run_is_none(self):
        self.run_row('run-1');self.run_row('run-2',overlay={'refreshRunId':'run-1'})
        self.head('h1','run-1');self.head('h2','run-2');self.db.commit()
        self.assertIsNone(published_plain_source(self.root,'it','run-1'))

    def test_market_mismatch_is_none(self):
        self.run_row('run-1',market='br');self.head('h1','run-1');self.db.commit()
        self.assertIsNone(published_plain_source(self.root,'it','run-1'))

    def test_non_it_market_uses_its_own_database(self):
        path=self.root/'var/global-source-br.sqlite'
        db=sqlite3.connect(path);db.executescript(SCHEMA)
        db.execute('INSERT INTO global_source_run VALUES(?,?,?,?,?)',
                   ('run-br','completed',1,json.dumps({'market':'br','account':'acc8'}),None))
        db.execute('INSERT INTO global_source_head VALUES(?,?)',('h1','run-br'))
        db.execute('INSERT INTO global_source_product VALUES(?,?)',('run-br','pid-1'));db.commit();db.close()
        self.assertEqual(published_plain_source(self.root,'br','run-br'),
                         {'sourceRunId':'run-br','headRunId':'run-br','products':1})
        self.assertIsNone(published_plain_source(self.root,'it','run-br'))


if __name__=='__main__':unittest.main()
