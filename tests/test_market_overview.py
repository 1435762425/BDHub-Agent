"""Read-only, market-scoped overview counts, including incomplete legacy state."""
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from lib.market_overview import overview, identity_counts, read_db, handling, inventory, video_counts
from test_cycle_stats import fixture as stats_fixture, NOON


class OverviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / 'config').mkdir()
        shutil.copy(ROOT / 'config/markets.json', self.root / 'config/markets.json')

    def test_missing_database_is_unavailable_and_does_not_create_var(self):
        value = overview(self.root, 'it', now=NOON)
        self.assertFalse(value['available'])
        self.assertFalse(value['activity']['available'])
        self.assertFalse((self.root / 'var').exists())
        for key in ('', 'IT', 'be', '../it'):
            with self.subTest(key=key), self.assertRaises(ValueError):
                overview(self.root, key, now=NOON)

    def test_daily_matches_existing_stats_and_read_never_mutates_the_database(self):
        from lib.cycle_stats import daily
        stats_fixture(self.root)
        path = self.root / 'var/second-cycle.sqlite'
        with closing(sqlite3.connect(path)) as db, db:
            db.execute("ALTER TABLE plan ADD COLUMN state TEXT DEFAULT 'active'")
            db.execute('ALTER TABLE cycle_delivery ADD COLUMN created REAL')
            db.execute("UPDATE cycle_delivery SET created=1")
            db.execute("INSERT INTO service_case VALUES('tech','p','creator','card_result_unknown','open',1)")
            for m in ('br', 'my', 'uk'):
                db.execute("INSERT INTO plan VALUES(?, 'bjn-local-research', ?, 'paused')", ('p-'+m, m))
            db.execute("INSERT INTO service_case VALUES('br-human','p-br','creator','paid_quote','open',1)")
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        expected = daily(self.root, market='it', now=NOON, count=7)
        it = overview(self.root, 'it', now=NOON)
        self.assertTrue(it['available'])
        self.assertEqual(it['activity']['days'], expected['days'])
        self.assertEqual(it['activity']['totals'], expected['totals'])
        values = {m['key']: m['value'] for p in it['panels'] if p['id']=='handling' for m in p['metrics']}
        self.assertEqual(values['humanCases'], 1)
        self.assertEqual(values['legacyTechnicalCases'], 1)
        self.assertFalse(next(p for p in it['panels'] if p['id']=='inventory')['available'])
        for market in ('br', 'my', 'uk'):
            value = overview(self.root, market, now=NOON)
            self.assertTrue(value['available']); self.assertEqual(value['planState'], 'paused')
            self.assertEqual(value['activity']['totals']['creators'], 0)
            self.assertEqual(value['activity']['totals']['replies'], 0)
            self.assertFalse(next(p for p in value['panels'] if p['id']=='video')['available'])
        self.assertEqual(before, hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertEqual(it['platformWrites'], 0)
        self.assertEqual(it['realSends'], 0)

    def test_corrupt_and_missing_schema_do_not_become_business_zero(self):
        (self.root / 'var').mkdir(); path = self.root / 'var/second-cycle.sqlite'
        path.write_text('broken database')
        self.assertFalse(overview(self.root, 'it', now=NOON)['available'])
        path.unlink()
        with closing(sqlite3.connect(path)) as db, db:
            db.execute('CREATE TABLE unrelated(value)')
        self.assertFalse(overview(self.root, 'it', now=NOON)['available'])

    def test_identity_partitions_current_rows_and_handles_without_cross_market_reuse(self):
        (self.root / 'var').mkdir(); path = self.root / 'var/second-cycle.sqlite'
        with closing(sqlite3.connect(path)) as db, db:
            db.executescript('''
                CREATE TABLE lead_query_head(plan_id,pid,query_id);
                CREATE TABLE lead_query_selection(query_id,source_id);
                CREATE TABLE source_edge_index(plan_id,source_id,source_handle,source_kind);
                CREATE TABLE cycle_identity_resolution(plan_id,source_id,creator_id);
                CREATE TABLE cycle_identity_outcome(plan_id,source_id,status);
            ''')
            for i,(handle,status) in enumerate([('resolved',None),('resolved',None),('missing','unresolved'),('queued','queued'),('blocked','blocked'),('new',None),('conflict','completed')]):
                db.execute('INSERT INTO source_edge_index VALUES(?,?,?,?)',('p',str(i),handle,'kalodata_http'))
                db.execute('INSERT INTO lead_query_head VALUES(?,?,?)',('p',str(i),'q'+str(i)))
                db.execute('INSERT INTO lead_query_selection VALUES(?,?)',('q'+str(i),str(i)))
                if status:db.execute('INSERT INTO cycle_identity_outcome VALUES(?,?,?)',('p',str(i),status))
            # A terminal resolution from another PID of the SAME market is reusable.
            db.execute("INSERT INTO source_edge_index VALUES('p','old','resolved','kalodata_http')")
            db.execute("INSERT INTO cycle_identity_resolution VALUES('p','old','creator')")
            db.execute("INSERT INTO source_edge_index VALUES('other','x','new','kalodata_http')")
            db.execute("INSERT INTO cycle_identity_resolution VALUES('other','x','other-creator')")
        with closing(sqlite3.connect(self.root/'var/creator-discovery.sqlite')) as db, db:
            db.executescript('CREATE TABLE discovery_batch(id,market); CREATE TABLE discovery_item(batch_id,handle,status);')
            db.execute("INSERT INTO discovery_batch VALUES('foreign','br')")
            db.execute("INSERT INTO discovery_item VALUES('foreign','new','unresolved')")
        with read_db(path) as db:
            value = identity_counts(self.root, db, 'p', 'it')
            self.assertEqual(value['rows'], {'resolved':2,'notFound':1,'queued':1,'blocked':1,'noRecord':1,'conflict':1})
            self.assertEqual(sum(value['handles'].values()), 6)
            self.assertEqual(sum(value['rows'].values()), 7)
            with self.assertRaises(sqlite3.OperationalError):
                db.execute("DELETE FROM source_edge_index")
        # Historical terminal judgment is reused across current edges, but stays market scoped.
        with closing(sqlite3.connect(self.root/'var/creator-discovery.sqlite')) as db, db:
            db.execute("INSERT INTO discovery_batch VALUES('own','it')")
            db.execute("INSERT INTO discovery_item VALUES('own','new','unresolved')")
        with read_db(path) as db:
            value = identity_counts(self.root, db, 'p', 'it')
            self.assertEqual(value['rows']['notFound'], 2)
            self.assertEqual(value['rows']['noRecord'], 0)

    def test_inventory_preserves_market_scope_and_missing_cumulative_history(self):
        (self.root/'var').mkdir()
        with closing(sqlite3.connect(self.root/'var/catalog-links.sqlite')) as links, links:
            links.execute('CREATE TABLE catalog_current_binding(market,state)')
            links.executemany('INSERT INTO catalog_current_binding VALUES(?,?)', [('it','active'),('it','inactive'),('br','active')])
        with closing(sqlite3.connect(':memory:')) as db, db:
            db.row_factory=sqlite3.Row
            db.executescript('CREATE TABLE catalog(id,plan_id,observed,payload); CREATE TABLE catalog_head(plan_id,snapshot_id);')
            for key,plan,items in [('one','p',[{'pid':'1'},{'pid':'2'}]),('two','p',[{'pid':'2'},{'pid':'3'}]),('foreign','other',[{'pid':'4'}])]:
                db.execute('INSERT INTO catalog VALUES(?,?,?,?)',(key,plan,100,json.dumps(items)))
                db.execute('INSERT INTO catalog_head VALUES(?,?)',(plan,key))
            result=inventory(self.root,db,'p','it')
            metrics={m['key']:m['value'] for m in result['metrics']}
            self.assertEqual(metrics,{'catalogPids':3,'activeBindings':1,'cumulativePids':None})
            self.assertEqual(result['observedAt'],100)

    def test_legacy_video_scope_never_leaks_into_other_markets(self):
        (self.root/'var').mkdir()
        with closing(sqlite3.connect(self.root/'var/creator-identities.sqlite')) as ids, ids:
            ids.execute('CREATE TABLE creator_identity(market,current_handle,handle_conflict)')
            ids.executemany('INSERT INTO creator_identity VALUES(?,?,?)',[('it','known',0),('br','unknown',0)])
        with closing(sqlite3.connect(':memory:')) as db, db:
            db.row_factory=sqlite3.Row
            db.execute('CREATE TABLE video_lead_current(handle)')
            db.executemany('INSERT INTO video_lead_current VALUES(?)',[('known',),('unknown',)])
            result=video_counts(self.root,db,'it')
            self.assertEqual({m['key']:m['value'] for m in result['metrics']},{'rows':2,'known':1,'unknown':1})
            with self.assertRaises(ValueError):video_counts(self.root,db,'br')
            db.execute("ALTER TABLE video_lead_current ADD COLUMN market TEXT DEFAULT 'it'")
            db.execute("INSERT INTO video_lead_current VALUES('unknown','br')")
            result=video_counts(self.root,db,'br')
            self.assertEqual({m['key']:m['value'] for m in result['metrics']},{'rows':1,'known':1,'unknown':0})

    def test_unknowns_and_technical_cases_do_not_disappear_or_change_business_cases(self):
        with closing(sqlite3.connect(':memory:')) as db, db:
            db.row_factory=sqlite3.Row
            db.executescript('''CREATE TABLE cycle_delivery(plan_id,state);
              CREATE TABLE service_reply(plan_id,state); CREATE TABLE service_case(plan_id,reason,state);
              CREATE TABLE account_maintenance_intent(market,state);''')
            db.executemany('INSERT INTO cycle_delivery VALUES(?,?)', [('p','unknown'),('p','quarantined_unknown'),('other','unknown')])
            db.execute("INSERT INTO service_reply VALUES('p','unknown')")
            db.executemany('INSERT INTO service_case VALUES(?,?,?)', [('p','card_result_unknown','open'),('p','paid_quote','open'),('p','card_result_unknown','resolved')])
            db.executemany('INSERT INTO account_maintenance_intent VALUES(?,?)', [('it','needs_human'),('br','needs_human')])
            before=db.total_changes
            value=handling(db,'p','it',NOON)
            metrics={m['key']:m['value'] for m in value['metrics']}
            self.assertEqual(metrics,{'deliveryUnknown':1,'quarantined':1,'replyUnknown':1,'legacyTechnicalCases':1,'humanCases':1,'accountNeedsHuman':1})
            self.assertEqual(db.total_changes,before)


if __name__=='__main__':unittest.main()
