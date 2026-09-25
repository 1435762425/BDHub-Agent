import importlib.util
import sqlite3
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
SPEC=importlib.util.spec_from_file_location('market_catalog_status',ROOT/'scripts/market-catalog-status.py')
MODULE=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class PublishedScreenTests(unittest.TestCase):
 def test_published_counts_are_scoped_and_stale_head_is_not_current(self):
  import tempfile
  with tempfile.TemporaryDirectory() as directory:
   root=Path(directory);(root/'var').mkdir()
   db=sqlite3.connect(root/'var/second-cycle.sqlite')
   db.executescript('CREATE TABLE plan(id TEXT,market TEXT,institution TEXT);CREATE TABLE catalog_head(plan_id TEXT,source TEXT,snapshot_id TEXT);')
   db.execute("INSERT INTO plan VALUES('p-uk','uk','bjn-local-research')")
   db.execute("INSERT INTO catalog_head VALUES('p-uk','live-uk-campaign','snapshot-uk-1')")
   db.commit()
   saved={'runId':'screen-uk-1','snapshot':'snapshot-uk-1',
          'counts':{'states':{'eligible':28,'ineligible':22},'eligiblePids':9,
                    'multiCampaignPids':3,'reasons':{'expired':22}},
          'poolCounts':{'chosen':9,'held':31}}
   with patch.object(MODULE,'ROOT',root),patch.object(MODULE,'recorded_screen',return_value=saved) as read:
    result=MODULE.published_screen('uk')
    self.assertEqual(read.call_args.args[1:],('campaign','uk'))
    self.assertEqual((result['market'],result['offers'],result['distinctPids'],result['eligiblePids']),('uk',50,40,9))
    db.execute("UPDATE catalog_head SET snapshot_id='snapshot-uk-2'")
    db.commit()
    stale=MODULE.published_screen('uk')
    self.assertEqual(stale,{'available':False,'market':'uk','source':'campaign','reason':'screen_stale'})
    foreign=MODULE.published_screen('br')
    self.assertEqual(foreign['reason'],'snapshot_missing')
   db.close()

 def test_category_reference_stays_inside_current_source_lineage(self):
  with closing(sqlite3.connect(':memory:')) as db:
   db.executescript('''CREATE TABLE global_source_head(scope_hash TEXT,run_id TEXT);
    CREATE TABLE global_source_run(id TEXT,scope TEXT,scope_hash TEXT,state TEXT,created REAL,updated REAL,identity_unchanged INTEGER);
    CREATE TABLE global_source_partition(run_id TEXT,page_count INTEGER,state TEXT);
    CREATE TABLE global_source_product(run_id TEXT,pid TEXT);''')
   db.executemany('INSERT INTO global_source_run VALUES(?,?,?,?,?,?,1)',[
    ('current','{"market":"uk"}','institution-a','completed',30,30),
    ('category-a','{"market":"uk","partitionMode":"category_l1_v1"}','institution-a','accepted_partial',10,10),
    ('category-b','{"market":"uk","partitionMode":"category_l1_v1"}','institution-b','completed',20,20)])
   db.execute("INSERT INTO global_source_head VALUES('institution-a','current')")
   db.executemany('INSERT INTO global_source_product VALUES(?,?)',[('category-a','1'),('category-b','2'),('category-b','3')])
   db.execute("INSERT INTO global_source_partition VALUES('category-a',3,'completed')")
   result=MODULE.category_snapshot(SimpleNamespace(db=db),'uk')
   self.assertEqual((result['runId'],result['products'],result['pages']),('category-a',1,3))

 def test_stopped_latest_read_does_not_replace_published_catalog_metrics(self):
  import tempfile,json
  from lib.global_source import SCHEMA
  with tempfile.TemporaryDirectory() as directory:
   root=Path(directory);(root/'var').mkdir();path=root/'var/global-source.sqlite'
   with closing(sqlite3.connect(path)) as db,db:
    db.executescript(SCHEMA)
    scope=json.dumps({'market':'it','account':'acc9'})
    for rid,state,stamp in [('published','completed',10),('last-read','stopped',20)]:
     db.execute('INSERT INTO global_source_run(id,scope,scope_hash,state,created,updated,identity_unchanged) VALUES(?,?,?,?,?,?,1)',(rid,scope,'same',state,stamp,stamp))
    db.execute("INSERT INTO global_source_head VALUES('same','published')")
   with patch.object(MODULE,'ROOT',root),patch.object(MODULE,'market_record',return_value={'capabilities':{'fullManagedCatalog':True}}):
    result=MODULE.full_status('it')
   self.assertEqual(result['id'],'published')
   self.assertEqual(result['state'],'completed')
   self.assertEqual(result['latestRun']['id'],'last-read')
   self.assertEqual(result['latestRun']['state'],'stopped')


if __name__=='__main__':unittest.main()
