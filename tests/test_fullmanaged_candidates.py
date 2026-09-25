"""Cumulative intake, original-intent ownership, cadence, and retained evidence contracts."""
import contextlib,json,sqlite3,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from lib.global_source import GlobalSources,SCHEMA,source_lineage_key
from lib.global_screen import screen_source,promotion_assessment
from lib.global_selection import Selection
from lib.fullmanaged_candidates import candidate_rows,migrate,selection_rows
from lib.operations_policy import full_catalog_collection_mode

PIDS=[str(1000000000000000000+i) for i in range(8)]
NOW=1790300000

def product(pid,sales='500 已售',rating=4.5,selected=False):
    return {'product_id':pid,'sales':sales,'product_rating':rating,'commission_rate':1400,
            'open_collab_rate':1000,'fs_is_selected':selected,'campaign_id':'7'*19,'title':'one'}


class Candidates(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        (self.root/'var').mkdir();(self.root/'config').mkdir()
        (self.root/'config/operations-policy.json').write_text((ROOT/'config/operations-policy.json').read_text())

    def tearDown(self):self.temp.cleanup()

    def publish(self,rid,products,at=NOW,state='completed',market='it'):
        path=self.root/'var'/('global-source.sqlite' if market=='it' else f'global-source-{market}.sqlite')
        s=GlobalSources(path,clock=lambda:at)
        scope={'market':market,'account':'acc6' if market=='it' else 'acc4','institutionFingerprint':'f'*64}
        try:
            s.start_partitioned(rid,scope,[{'category_id':'1','name':'A','is_leaf':True}])
            with s.tx():
                s.db.execute('UPDATE global_source_run SET state=?,identity_unchanged=1 WHERE id=?',(state,rid))
                s.db.execute('INSERT OR REPLACE INTO global_source_head VALUES(?,?)',(source_lineage_key(scope),rid))
                for p in products:
                    s.db.execute('INSERT INTO global_source_product VALUES(?,?,?,?,?,?,?)',(rid,p['product_id'],json.dumps(p),'fp',1,1,at))
                    s.db.execute('INSERT INTO global_source_product_category VALUES(?,?,?,?,?,?,?)',(rid,p['product_id'],'1','A',1,1,at))
            return screen_source(path,rid,root=self.root,clock=lambda:at)
        finally:s.close()

    def test_union_of_generations_preserves_first_qualification_and_single_intent(self):
        a,b,c,d=PIDS[:4]
        first=self.publish('r1',[product(a),product(b),product(d,sales='10 已售')])
        ledger=Selection(self.root)
        try:
            run1=ledger.prepare();before={r['pid']:r for r in candidate_rows(self.root,'it')}
            ledger.update(next(i for i in ledger.items(run1) if i['pid']==b),'confirmed',selectionEvidence=[{'pid':b}])
            second=self.publish('r2',[product(b,sales='1 已售',rating=1),product(c),product(d)],NOW+15*86400)
            run2=ledger.prepare()
            after={r['pid']:r for r in candidate_rows(self.root,'it')}
            self.assertEqual(set(after),{a,b,c,d})
            self.assertEqual(before[a],after[a]);self.assertEqual(before[b],after[b])
            self.assertEqual(second['candidates']['added'],2)
            self.assertEqual(ledger.status(run2),{'pending':3,'confirmed':1})
            ledger.prepare()
            self.assertEqual(ledger.db.execute('SELECT count(*) FROM intake_item').fetchone()[0],4)
            self.assertEqual(next(i for i in ledger.items(run2) if i['pid']==b)['run_id'],run1)
        finally:ledger.db.close()

    def test_unknown_and_failed_items_are_referenced_not_recreated_or_reset(self):
        a,b=PIDS[:2];self.publish('r1',[product(a),product(b)])
        ledger=Selection(self.root)
        try:
            first=ledger.prepare()
            for item in ledger.items(first):
                ledger.update(item,'result_unknown' if item['pid']==a else 'filtered',receipt={'code':0},
                              campaign={'campaign':{'campaign_id':'7'*19}},attemptedAt=NOW)
            original=ledger.items(first)
            self.publish('r2',[product(a),product(b)],NOW+15*86400)
            second=ledger.prepare();self.assertEqual(ledger.items(second),original)
            self.assertEqual(ledger.db.execute('SELECT count(*) FROM intake_item').fetchone()[0],2)
            with self.assertRaisesRegex(ValueError,'selection_already_attempted'):
                ledger.begin(ledger.items(second)[0],{'campaign':{'campaign_id':'8'*19}})
        finally:ledger.db.close()

    def test_admitted_before_enqueue_crash_is_recovered_once(self):
        a=PIDS[0];self.publish('r1',[product(a)])
        ledger=Selection(self.root)
        try:
            ledger.db.execute("CREATE TRIGGER fail_owner BEFORE INSERT ON intake_candidate_owner BEGIN SELECT RAISE(ABORT,'injected'); END")
            with self.assertRaisesRegex(sqlite3.IntegrityError,'injected'):ledger.prepare()
            self.assertEqual(ledger.db.execute('SELECT count(*) FROM intake_item').fetchone()[0],0)
            ledger.db.execute('DROP TRIGGER fail_owner')
            run=ledger.prepare();self.assertEqual(ledger.status(run),{'pending':1})
            self.assertEqual(len(candidate_rows(self.root,'it')),1)
        finally:ledger.db.close()

    def test_partial_source_does_not_admit_or_claim_complete(self):
        result=self.publish('partial',[product(PIDS[0])],state='partial')
        self.assertFalse(result['candidates']['published'])
        self.assertEqual(candidate_rows(self.root,'it'),[])

    def test_zero_rating_is_unverified_and_current_promotion_ignores_sales_rating(self):
        result=self.publish('r1',[product(PIDS[0],rating=0),product(PIDS[1],rating=None)])
        self.assertEqual(result['counts'],{'rejected':1,'eligible':1})
        self.assertTrue(promotion_assessment(product(PIDS[0],sales='0 已售',rating=1))['eligible'])
        self.assertFalse(promotion_assessment(product(PIDS[0])|{'product_status':2})['eligible'])
        self.assertFalse(promotion_assessment(product(PIDS[0])|{'commission_rate':1100})['eligible'])
        self.assertTrue(promotion_assessment(product(PIDS[0])|{'stock':0,'unavailable_type':8})['eligible'])

    def test_same_pid_in_different_market_retains_distinct_evidence(self):
        self.publish('it-run',[product(PIDS[0])])
        self.publish('uk-run',[product(PIDS[0],sales='900 已售')],market='uk')
        self.assertEqual(candidate_rows(self.root,'it')[0]['evidence']['qualification']['units'],500)
        self.assertEqual(candidate_rows(self.root,'uk')[0]['evidence']['qualification']['units'],900)

    def test_snapshot_retention_does_not_erase_self_contained_qualification(self):
        self.publish('r1',[product(PIDS[0])])
        before=candidate_rows(self.root,'it')
        with contextlib.closing(sqlite3.connect(self.root/'var/global-source.sqlite')) as db,db:
            db.execute('DELETE FROM global_source_product WHERE run_id=?',('r1',))
            db.execute('DELETE FROM global_source_product_category WHERE run_id=?',('r1',))
            db.execute('DELETE FROM global_source_screen')
            db.execute('DELETE FROM global_source_screen_run')
        self.assertEqual(candidate_rows(self.root,'it'),before)
        self.assertEqual(before[0]['evidence']['categoryEvidence'][0][:3],['1','A',1])

    def test_repeated_migration_preserves_legacy_intents(self):
        self.publish('r1',[product(PIDS[0])])
        ledger=Selection(self.root)
        try:
            ledger.db.execute("INSERT INTO intake_run VALUES('old','{}','r1',1)")
            ledger.db.execute("INSERT INTO intake_item VALUES('old',?,'filtered','{}',1)",(PIDS[0],));ledger.db.commit()
            before=ledger.db.execute('SELECT * FROM intake_item').fetchall()
            self.assertEqual(migrate(self.root,'it')['owners'],1)
            self.assertEqual(migrate(self.root,'it')['owners'],0)
            self.assertEqual(ledger.db.execute('SELECT * FROM intake_item').fetchall(),before)
            run=ledger.prepare();self.assertEqual(ledger.items(run)[0]['run_id'],'old')
        finally:ledger.db.close()

    def test_archived_screen_intake_evidence_backfills_without_changing_old_outcome(self):
        self.publish('r1',[product(PIDS[0])])
        ledger=Selection(self.root)
        try:
            run=ledger.prepare();item=ledger.items(run)[0]
            ledger.update(item,'skipped_unknown',receipt={'ambiguous':True})
            with contextlib.closing(sqlite3.connect(self.root/'var/global-source.sqlite')) as db,db:
                db.execute('DELETE FROM global_source_candidate')
                db.execute('DELETE FROM global_source_product')
                db.execute('DELETE FROM global_source_screen')
                db.execute('DELETE FROM global_source_screen_run')
            result=migrate(self.root,'it')
            self.assertEqual(result['admitted'],1)
            candidate=candidate_rows(self.root,'it')[0]
            self.assertEqual(candidate['evidence']['legacyIntakeRun'],run)
            self.assertEqual(candidate['evidence']['product'],item['payload']['snapshot'])
            self.assertEqual(ledger.items(run)[0]['state'],'skipped_unknown')
            self.assertEqual(migrate(self.root,'it')['admitted'],0)
        finally:ledger.db.close()

    def test_fifteen_day_boundary_and_stopped_history(self):
        self.publish('r1',[product(PIDS[0])],NOW)
        for m in ('it',):
            self.assertEqual(full_catalog_collection_mode(self.root,m,NOW+15*86400-1)['mode'],'reuse')
            self.assertEqual(full_catalog_collection_mode(self.root,m,NOW+15*86400)['mode'],'category')
        self.publish('stopped',[],NOW+20*86400,state='stopped')
        mode=full_catalog_collection_mode(self.root,'it',NOW+20*86400)
        self.assertEqual(mode['lastCategoryAt'],NOW)
        self.assertEqual(mode['mode'],'category')

if __name__=='__main__':unittest.main()
