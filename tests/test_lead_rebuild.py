from contextlib import closing
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))

from lib.lead_rebuild import reset_current  # noqa:E402


class LeadRebuildTests(unittest.TestCase):
 def test_only_current_projection_and_receipt_cache_are_cleared(self):
  with tempfile.TemporaryDirectory() as folder:
   root=Path(folder);(root/'var').mkdir()
   with closing(sqlite3.connect(root/'var/kalodata-leads.sqlite')) as db,db:
    db.executescript('CREATE TABLE leads_query(pid);CREATE TABLE leads_page(pid);CREATE TABLE leads_attempt(pid);'
                     'CREATE TABLE leads_page_scope(query_id TEXT);'
                     'CREATE TABLE leads_page_legacy_history(pid TEXT);'
                     "INSERT INTO leads_query VALUES('p');INSERT INTO leads_page VALUES('p');"
                     "INSERT INTO leads_attempt VALUES('p');"
                     "INSERT INTO leads_page_scope VALUES('scope');"
                     "INSERT INTO leads_page_legacy_history VALUES('p');")
   with closing(sqlite3.connect(root/'var/second-cycle.sqlite')) as db,db:
    db.executescript('CREATE TABLE lead_query_head(pid);CREATE TABLE lead_query_run(id);'
      'CREATE TABLE lead_query_selection(id);CREATE TABLE source_edge_index(id);'
      'CREATE TABLE cycle_delivery(creator_id,pid,state);'
      "INSERT INTO lead_query_head VALUES('p');INSERT INTO lead_query_run VALUES('r');"
      "INSERT INTO lead_query_selection VALUES('s');INSERT INTO source_edge_index VALUES('e');"
      "INSERT INTO cycle_delivery VALUES('c','p','confirmed');")
   self.assertEqual(reset_current(root)['mode'],'preview')
   result=reset_current(root,confirmed=True)
   self.assertEqual(result['after']['currentHeads'],0);self.assertEqual(result['after']['leadPages'],0)
   self.assertEqual(result['after']['scopedPages'],0)
   with closing(sqlite3.connect(root/'var/kalodata-leads.sqlite')) as db:
    self.assertEqual(db.execute('SELECT count(*) FROM leads_page_legacy_history').fetchone()[0],1)
   self.assertEqual((result['after']['historicalRuns'],result['after']['historicalSelections'],
                     result['after']['sourceEdges'],result['after']['sentPairs']),(1,1,1,1))


if __name__=='__main__':unittest.main()
