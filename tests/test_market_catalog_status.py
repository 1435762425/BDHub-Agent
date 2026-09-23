import importlib.util
import sqlite3
import unittest
from pathlib import Path
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


if __name__=='__main__':unittest.main()
