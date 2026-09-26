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

 def test_later_outcomes_credit_each_delivery_to_its_own_frozen_source_only(self):
  from lib.lead_contribution import later_outcomes
  db=sqlite3.connect(':memory:')
  db.executescript("""CREATE TABLE plan(id TEXT,market TEXT,institution TEXT);INSERT INTO plan VALUES('p','it','bjn-local-research');
   CREATE TABLE lead_query_run(query_id TEXT,plan_id TEXT,published_at REAL);CREATE TABLE lead_query_selection(query_id TEXT,source_id TEXT);
   CREATE TABLE source_edge_index(plan_id TEXT,source_id TEXT,source_kind TEXT);CREATE TABLE kalodata_video_run(run_id TEXT,observed_at REAL);
   CREATE TABLE cycle_delivery(id TEXT,plan_id TEXT,creator_id TEXT,state TEXT,source_id TEXT,created REAL);
   CREATE TABLE cycle_delivery_part(delivery_id TEXT,kind TEXT,state TEXT,started REAL);
   CREATE TABLE inbound_turn(plan_id TEXT,creator_id TEXT,occurred_ms INTEGER,observed_at REAL,historical INTEGER);
   INSERT INTO lead_query_run VALUES('q-new','p',100),('q-old','p',10);
   INSERT INTO lead_query_selection VALUES('q-new','a1'),('q-old','a-old');
   INSERT INTO source_edge_index VALUES('p','video:vr1:v9','kalodata_video');INSERT INTO kalodata_video_run VALUES('vr1',100);
   INSERT INTO cycle_delivery VALUES('d1','p','c1','confirmed','a1',110),('d2','p','c2','confirmed','video:vr1:v9',110),
    ('d3','p','c3','confirmed','a-old',110),('d4','p','c4','cancelled','a1',110);
   INSERT INTO cycle_delivery_part VALUES('d1','card','confirmed',120),('d2','card','confirmed',120),('d3','card','confirmed',120);
   INSERT INTO inbound_turn VALUES('p','c1',130000,130,0),('p','c2',115000,115,0),('p','c3',130000,130,0);""")
  out=later_outcomes(db,'it',50,200)
  # c1 replied after its card; c2's message predates the card; d3's source was published before the window.
  self.assertEqual(out['A'],{'publishedSources':1,'deliveries':2,'sentDeliveries':1,'creatorsReached':1,'creatorsReplied':1})
  self.assertEqual(out['B'],{'publishedSources':1,'deliveries':1,'sentDeliveries':1,'creatorsReached':1,'creatorsReplied':0})

if __name__=='__main__':unittest.main()
