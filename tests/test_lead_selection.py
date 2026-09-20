import json,sqlite3,sys,tempfile,unittest
from contextlib import closing
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from lib.lead_selection import backfill_receipts,publish_query,select_top_leads  # noqa: E402
from lib.schema_migrations import apply_database  # noqa: E402

def edge(index,*,creator=None,units=None,pid='1'):
 return {'sourceId':f's{index}','pid':pid,'sourceHandle':creator or f'user{index}',
         'kalodataCreatorId':creator or f'k{index}','sourceRank':index,'units':units or 100-index,
         'windowStart':'2026-09-01','windowEnd':'2026-09-14','sourceKind':'kalodata_http'}

class LeadSelectionTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);(self.root/'var').mkdir()
  with closing(sqlite3.connect(self.root/'var/second-cycle.sqlite')) as db,db:
   db.execute('CREATE TABLE source_edge(plan_id TEXT,source_id TEXT,payload TEXT,PRIMARY KEY(plan_id,source_id))')
   db.execute('CREATE TABLE plan(id TEXT,institution TEXT,market TEXT)')
   db.execute("INSERT INTO plan VALUES('p','bjn-local-research','it')")
  apply_database(self.root,'second-cycle')
 def tearDown(self):self.temp.cleanup()
 def test_top_twenty_is_ranked_positive_and_deduplicated_by_creator(self):
  rows=[edge(i) for i in range(1,26)]+[edge(30,creator='k1',units=999)]
  selected=select_top_leads(rows)
  self.assertEqual(len(selected),20);self.assertEqual([r['sourceRank'] for r in selected],list(range(1,21)))
  bad=edge(1)|{'units':0}
  with self.assertRaisesRegex(ValueError,'lead_edge_invalid'):select_top_leads([bad])
 def test_publish_preserves_all_evidence_but_heads_only_twenty(self):
  rows=[edge(i) for i in range(1,26)]
  result=publish_query(self.root,plan_id='p',query_id='q',pid='1',edges=rows,
                       receipt_fingerprints=['page-1'],at=10)
  self.assertEqual((result['rawPositive'],result['selected']),(25,20))
  with closing(sqlite3.connect(self.root/'var/second-cycle.sqlite')) as db,db:
   self.assertEqual(db.execute('SELECT count(*) FROM source_edge').fetchone()[0],25)
   self.assertEqual(db.execute('SELECT count(*) FROM source_edge_index').fetchone()[0],25)
   self.assertEqual(db.execute('SELECT count(*) FROM lead_query_selection').fetchone()[0],20)
   self.assertEqual(db.execute('SELECT query_id FROM lead_query_head').fetchone()[0],'q')
  self.assertTrue(publish_query(self.root,plan_id='p',query_id='q',pid='1',edges=rows,
                                receipt_fingerprints=['page-1'],at=20)['cached'])
 def test_changed_receipt_cannot_rewrite_one_query_generation(self):
  publish_query(self.root,plan_id='p',query_id='q',pid='1',edges=[edge(1)],receipt_fingerprints=['a'])
  with self.assertRaisesRegex(ValueError,'lead_publication_conflict'):
   publish_query(self.root,plan_id='p',query_id='q',pid='1',edges=[edge(2)],receipt_fingerprints=['b'])
 def test_empty_success_publishes_a_zero_selection_head_with_explicit_window(self):
  result=publish_query(self.root,plan_id='p',query_id='empty',pid='1',edges=[],
                       receipt_fingerprints=['empty-page'],window_start='2026-09-01',window_end='2026-09-14',at=10)
  self.assertEqual((result['rawPositive'],result['selected']),(0,0))
  with closing(sqlite3.connect(self.root/'var/second-cycle.sqlite')) as db:
   self.assertEqual(db.execute("SELECT query_id FROM lead_query_head WHERE pid='1'").fetchone()[0],'empty')
 def test_backfill_checks_then_publishes_existing_receipts_without_platform_writes(self):
  with closing(sqlite3.connect(self.root/'var/kalodata-leads.sqlite')) as db,db:
   db.executescript('CREATE TABLE leads_page(pid TEXT,cursor TEXT,payload TEXT);'
                    'CREATE TABLE leads_query(pid TEXT,window_end TEXT);')
   receipt={'edges':[edge(i) for i in range(1,26)],'rowsFingerprint':'page-1'}
   db.execute('INSERT INTO leads_page VALUES(?,?,?)',('1','',json.dumps(receipt)))
   db.execute('INSERT INTO leads_query VALUES(?,?)',('1','2026-09-14'))
  checked=backfill_receipts(self.root,apply=False,at=10)
  self.assertEqual((checked['pids'],checked['selected'],checked['platformWrites']),(1,20,0))
  with closing(sqlite3.connect(self.root/'var/second-cycle.sqlite')) as db,db:
   self.assertEqual(db.execute('SELECT count(*) FROM lead_query_head').fetchone()[0],0)
  applied=backfill_receipts(self.root,apply=True,at=10)
  self.assertEqual((applied['pids'],applied['selected'],applied['platformWrites']),(1,20,0))
  with closing(sqlite3.connect(self.root/'var/second-cycle.sqlite')) as db,db:
   self.assertEqual(db.execute('SELECT count(*) FROM lead_query_selection').fetchone()[0],20)

if __name__=='__main__':unittest.main()
