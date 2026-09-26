import sqlite3,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from lib.lead_contribution import between,record  # noqa:E402

class LeadContributionTests(unittest.TestCase):
 def setUp(self):
  self.db=sqlite3.connect(':memory:')
  self.db.executescript("CREATE TABLE plan(id TEXT,market TEXT);INSERT INTO plan VALUES('plan-it','it');"
   "CREATE TABLE source_edge_index(plan_id TEXT,source_id TEXT,pid TEXT,source_handle TEXT);"
   "INSERT INTO source_edge_index VALUES('plan-it','s1','111','Alice');INSERT INTO source_edge_index VALUES('plan-it','s2','222','Bob');")
 def test_new_pairs_refreshes_and_new_creators_are_counted_once_per_publication(self):
  # alice is known for this PID (refresh); bob is known elsewhere (new pair, not a new creator); carol is new.
  record(self.db,publication_id='A:q1',plan_id='plan-it',pid='111',kind='A',handles=['ALICE','bob','carol'],at=100)
  record(self.db,publication_id='A:q1',plan_id='plan-it',pid='111',kind='A',handles=['x','y'],at=101)  # replay: ignored
  row=self.db.execute('SELECT handles,new_pairs,refreshed_pairs,new_creators,market FROM lead_publication_contribution').fetchone()
  self.assertEqual(row,(3,2,1,1,'it'))
  self.assertEqual(between(self.db,'it',90,110),{'publications':1,'newPairs':2,'refreshedPairs':1,'newCreators':1,'aPublications':1,'bPublications':0})
  self.assertIsNone(between(self.db,'it',200,300))
  self.assertIsNone(between(self.db,'br',90,110))
 def test_no_contribution_table_reads_as_not_recorded(self):
  self.assertIsNone(between(sqlite3.connect(':memory:'),'it',0,1))

if __name__=='__main__':unittest.main()
