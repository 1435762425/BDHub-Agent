"""The sending pool: layers, the creator-level cooldown, and the rolling order."""
import sqlite3
import json
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from lib.lead_pool import LAYER_ORDER, pool  # noqa: E402
from lib.schema_migrations import apply_database  # noqa: E402

NOW = 1_800_000_000.0


def fixture(folder, positions, relationships, deliveries=(), cases=(), pending=()):
    var = Path(folder) / 'var'
    var.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(var / 'second-cycle.sqlite')) as conn, conn:
        conn.executescript('''
            CREATE TABLE source_edge(plan_id TEXT,source_id TEXT,payload TEXT);
            CREATE TABLE cycle_identity_resolution(plan_id TEXT,source_id TEXT,creator_id TEXT);
            CREATE TABLE cycle_identity_outcome(plan_id TEXT,source_id TEXT,status TEXT);
            CREATE TABLE relationship(plan_id TEXT,creator_id TEXT,unlocked INTEGER,mode TEXT,rejected INTEGER,inbox_until REAL);
            CREATE TABLE cycle_delivery(id TEXT,plan_id TEXT,creator_id TEXT,pid TEXT,state TEXT);
            CREATE TABLE cycle_delivery_part(delivery_id TEXT,kind TEXT,started REAL);
            CREATE TABLE service_case(plan_id TEXT,creator_id TEXT,state TEXT,updated REAL);
            CREATE TABLE inbox_pending(plan_id TEXT,creator_id TEXT,revision INTEGER,due_at REAL,state TEXT);''')
        conn.commit()
    apply_database(folder,'second-cycle')
    with closing(sqlite3.connect(var / 'second-cycle.sqlite')) as conn, conn:
        grouped={}
        for index, (creator, pid, rank) in enumerate(positions):
            sid = f's{index}'
            conn.execute('INSERT INTO source_edge VALUES(?,?,?)', ('p', sid,
                         '{"sourceKind":"kalodata_http","pid":"%s","sourceRank":%d,"units":10,"sourceHandle":"%s"}'
                         % (pid, rank, creator)))
            conn.execute('INSERT INTO cycle_identity_resolution VALUES(?,?,?)', ('p', sid, creator))
            conn.execute('INSERT INTO cycle_identity_outcome VALUES(?,?,?)', ('p', sid, 'completed'))
            conn.execute('INSERT INTO source_edge_index('
                         'plan_id,source_id,pid,source_handle,source_rank,units,window_start,window_end,source_kind) '
                         'VALUES(?,?,?,?,?,?,?,?,?)',
                         ('p',sid,pid,creator,rank,10,'2026-09-01','2026-09-14','kalodata_http'))
            grouped.setdefault(pid,[]).append((sid,rank))
        for pid,rows in grouped.items():
            query='q-'+pid
            conn.execute('INSERT INTO lead_query_run VALUES(?,?,?,?,?,?,?,?,?,?)',
                         (query,'p',pid,'2026-09-01','2026-09-14','v2','published',len(rows),'fp',1))
            conn.execute('INSERT INTO lead_query_head VALUES(?,?,?)',('p',pid,query))
            for position,(sid,rank) in enumerate(sorted(rows,key=lambda row:row[1]),start=1):
                conn.execute('INSERT INTO lead_query_selection VALUES(?,?,?,?,?)',(query,sid,rank,10,position))
        for creator, unlocked, mode, rejected in relationships:
            conn.execute('INSERT INTO relationship VALUES(?,?,?,?,?,0)', ('p', creator, unlocked, mode, rejected))
        for index, (creator, pid, started) in enumerate(deliveries):
            conn.execute('INSERT INTO cycle_delivery VALUES(?,?,?,?,?)', (f'd{index}', 'p', creator, pid, 'confirmed'))
            conn.execute('INSERT INTO cycle_delivery_part VALUES(?,?,?)', (f'd{index}', 'card', started))
        for creator, state, updated in cases:
            conn.execute('INSERT INTO service_case VALUES(?,?,?,?)', ('p', creator, state, updated))
        for creator,state,due_at in pending:
            conn.execute('INSERT INTO inbox_pending VALUES(?,?,1,?,?)',('p',creator,due_at,state))
        conn.commit()


def layers(state):
    return state['counts']


class Layers(unittest.TestCase):
    def test_a_clear_creator_is_ready_and_sorted_by_lead_rank(self):
        with tempfile.TemporaryDirectory() as folder:
            fixture(folder, [('a', '1' * 19, 9), ('b', '2' * 19, 1),
                             ('c', '3' * 19, 5)],
                    [('a', 0, 'auto', 0), ('b', 0, 'auto', 0), ('c', 0, 'auto', 0)])
            state = pool(folder, now=NOW)
            self.assertEqual(layers(state)['ready'], 3)
            self.assertEqual([r['handle'] for r in state['pools']['ready']], ['b', 'c', 'a'])

    def test_only_the_best_position_per_creator_takes_a_slot(self):
        with tempfile.TemporaryDirectory() as folder:
            fixture(folder, [('a', '1' * 19, 7), ('a', '2' * 19, 2), ('a', '3' * 19, 5)],
                    [('a', 0, 'auto', 0)])
            state = pool(folder, now=NOW)
            counts = layers(state)
            self.assertEqual(counts['ready'], 1)
            self.assertEqual(counts['readyCreators'], 1)
            self.assertEqual(counts['queued'], 2)
            # The strongest lead is the one that would go out.
            self.assertEqual(state['pools']['ready'][0]['rank'], 2)

    def test_equal_source_rank_uses_higher_sales_then_pid(self):
        with tempfile.TemporaryDirectory() as folder:
            low='1'*19;high='2'*19
            fixture(folder,[('a',low,1),('a',high,1)],[('a',0,'auto',0)])
            with closing(sqlite3.connect(Path(folder)/'var/second-cycle.sqlite')) as db, db:
                db.execute("UPDATE source_edge_index SET units=5 WHERE pid=?",(low,))
                db.execute("UPDATE source_edge_index SET units=20 WHERE pid=?",(high,))
            state=pool(folder,now=NOW)
            self.assertEqual(state['pools']['ready'][0]['pid'],high)
            self.assertEqual(state['pools']['queued'][0]['pid'],low)

    def test_same_creator_a_positions_use_numeric_gmv_before_source_rank(self):
        with tempfile.TemporaryDirectory() as folder:
            low='1'*19;high='2'*19
            fixture(folder,[('a',low,1),('a',high,3)],[('a',0,'auto',0)])
            with closing(sqlite3.connect(Path(folder)/'var/second-cycle.sqlite')) as db,db:
                db.execute("UPDATE source_edge_index SET revenue_value='20',revenue_currency='EUR' WHERE pid=?",(low,))
                db.execute("UPDATE source_edge_index SET revenue_value='500',revenue_currency='EUR' WHERE pid=?",(high,))
            state=pool(folder,now=NOW)
            self.assertEqual((state['pools']['ready'][0]['pid'],state['pools']['ready'][0]['gmv']),(high,'500.0'))
            self.assertEqual(state['pools']['queued'][0]['pid'],low)

    def test_a_precedes_b_and_b_uses_highest_video_views(self):
        with tempfile.TemporaryDirectory() as folder:
            a_pid='1'*19;b_pid='2'*19;c_pid='3'*19
            fixture(folder,[('a',a_pid,9)],[('a',0,'auto',0),('b',0,'auto',0)])
            var=Path(folder)/'var'
            with closing(sqlite3.connect(var/'creator-identities.sqlite')) as db,db:
                db.execute('CREATE TABLE creator_identity(creator_id TEXT,market TEXT,current_handle TEXT,handle_conflict INTEGER)')
                db.executemany('INSERT INTO creator_identity VALUES(?,?,?,0)',
                               [('a','it','a.video'),('b','it','b.video')])
            with closing(sqlite3.connect(var/'second-cycle.sqlite')) as db,db:
                db.executemany('INSERT INTO video_lead_current VALUES(?,?,?,?,?,?,?,?,?,?)',[
                  ('g',b_pid,'ka','a.video','r','va',150000,'2026-09-18',0,NOW),
                  ('g',c_pid,'kb','b.video','r','vb',26000,'2026-09-18',0,NOW)])
            state=pool(folder,now=NOW)
            self.assertEqual([(row['creatorId'],row['sourceClass']) for row in state['pools']['ready']],
                             [('a','A'),('b','B')])
            self.assertEqual((state['pools']['queued'][0]['pid'],state['pools']['queued'][0]['videoViews']),
                             (b_pid,150000))

    def test_a_recent_send_puts_every_position_of_that_creator_on_cooldown(self):
        with tempfile.TemporaryDirectory() as folder:
            fixture(folder, [('a', '1' * 19, 1), ('a', '2' * 19, 2), ('b', '3' * 19, 3)],
                    [('a', 0, 'auto', 0), ('b', 0, 'auto', 0)],
                    deliveries=[('a', '9' * 19, NOW - 3600)])
            counts = layers(pool(folder, now=NOW))
            # Both of a's positions wait for a, and b is unaffected.
            self.assertEqual(counts['cooling'], 2)
            self.assertEqual(counts['ready'], 1)

    def test_every_relationship_uses_72_hours_for_all_product_positions(self):
        for unlocked in (0, 1):
            for elapsed, expected_ready in ((259199, 0), (259200, 1), (259201, 1)):
                with self.subTest(unlocked=unlocked, elapsed=elapsed), tempfile.TemporaryDirectory() as folder:
                    fixture(folder, [('a', '1' * 19, 1), ('a', '2' * 19, 2)],
                            [('a', unlocked, 'auto', 0)],
                            deliveries=[('a', '9' * 19, NOW - elapsed)])
                    result = pool(folder, now=NOW)
                    self.assertEqual(layers(result)['ready'], expected_ready)
                    self.assertEqual(layers(result)['cooling'], 0 if expected_ready else 2)
                    self.assertEqual(result['cooldown'], {'unlocked': 259200, 'locked': 259200})

    def test_cooldown_begins_at_the_last_component_of_previous_outreach(self):
        with tempfile.TemporaryDirectory() as folder:
            fixture(folder, [('a', '1' * 19, 1)], [('a', 1, 'auto', 0)],
                    deliveries=[('a', '9' * 19, NOW - 259201)])
            with closing(sqlite3.connect(Path(folder) / 'var/second-cycle.sqlite')) as db, db:
                db.execute('INSERT INTO cycle_delivery_part VALUES(?,?,?)', ('d0', 'text', NOW - 259199))
            self.assertEqual(layers(pool(folder, now=NOW))['cooling'], 1)

    def test_observed_institution_outbound_cools_all_product_positions(self):
        with tempfile.TemporaryDirectory() as folder:
            fixture(folder, [('a', '1' * 19, 1), ('a', '2' * 19, 2)], [('a', 1, 'auto', 0)])
            with closing(sqlite3.connect(Path(folder) / 'var/second-cycle.sqlite')) as db, db:
                db.execute('ALTER TABLE relationship ADD COLUMN oec TEXT')
                db.execute("UPDATE relationship SET oec='123'")
                db.execute('CREATE TABLE inbox_event(plan_id TEXT,cid TEXT,message_id TEXT,oec TEXT,kind TEXT,occurred_ms INTEGER)')
                db.execute('INSERT INTO inbox_event VALUES(?,?,?,?,?,?)',
                           ('p', '88', '10', '123', 'ourMessages', int((NOW - 259199) * 1000)))
            self.assertEqual(layers(pool(folder, now=NOW))['cooling'], 2)
            self.assertEqual(layers(pool(folder, now=NOW+1))['ready'], 1)

    def test_an_open_case_waits_for_a_reply_rather_than_for_time(self):
        with tempfile.TemporaryDirectory() as folder:
            fixture(folder, [('a', '1' * 19, 1)], [('a', 0, 'human', 0)],
                    cases=[('a', 'open', NOW - 60)])
            counts = layers(pool(folder, now=NOW))
            self.assertEqual(counts['awaitingReply'], 1)
            self.assertEqual(counts['cooling'], 0)
            self.assertEqual(counts['ready'], 0)

    def test_a_resolved_case_returns_the_creator_to_the_pool(self):
        with tempfile.TemporaryDirectory() as folder:
            fixture(folder, [('a', '1' * 19, 1)], [('a', 0, 'auto', 0)],
                    cases=[('a', 'resolved', NOW - 60)])
            self.assertEqual(layers(pool(folder, now=NOW))['ready'], 1)

    def test_persistent_pending_blocks_after_the_short_freeze_expires(self):
        with tempfile.TemporaryDirectory() as folder:
            fixture(folder,[('a','1'*19,1)],[('a',1,'auto',0)],
                    pending=[('a','awaiting_classification',NOW-60)])
            state=pool(folder,now=NOW)
            self.assertEqual(state['counts']['awaitingReply'],1)
            self.assertEqual(state['counts']['ready'],0)
        with tempfile.TemporaryDirectory() as folder:
            fixture(folder,[('a','1'*19,1)],[('a',1,'auto',0)],
                    pending=[('a','resolved_no_reply',NOW-60)])
            self.assertEqual(pool(folder,now=NOW)['counts']['ready'],1)

    def test_a_rejection_is_a_permanent_exclusion_not_a_wait(self):
        with tempfile.TemporaryDirectory() as folder:
            fixture(folder, [('a', '1' * 19, 1)], [('a', 0, 'auto', 1)])
            counts = layers(pool(folder, now=NOW))
            self.assertEqual(counts['excluded'], 1)
            self.assertEqual(counts['awaitingReply'], 0)
            self.assertEqual(counts['ready'], 0)

    def test_a_sent_position_is_kept_rather_than_removed(self):
        with tempfile.TemporaryDirectory() as folder:
            fixture(folder, [('a', '1' * 19, 1), ('a', '2' * 19, 2)], [('a', 0, 'auto', 0)],
                    deliveries=[('a', '1' * 19, NOW - 10 * 86400)])
            counts = layers(pool(folder, now=NOW))
            # The sent position stays in the pool as history; the other one is now the slot.
            self.assertEqual(counts['sent'], 1)
            self.assertEqual(counts['ready'], 1)
            self.assertEqual(pool(folder, now=NOW)['pools']['ready'][0]['pid'], '2' * 19)


class Partition(unittest.TestCase):
    def test_every_position_lands_in_exactly_one_layer(self):
        with tempfile.TemporaryDirectory() as folder:
            fixture(folder, [('a', '1' * 19, 1), ('a', '2' * 19, 2), ('b', '3' * 19, 3),
                             ('c', '4' * 19, 4), ('d', '5' * 19, 5), ('e', '6' * 19, 6)],
                    [('a', 0, 'auto', 0), ('b', 0, 'auto', 0), ('c', 0, 'human', 0),
                     ('d', 0, 'auto', 1), ('e', 0, 'auto', 0)],
                    deliveries=[('b', '9' * 19, NOW - 60), ('e', '3' * 19, NOW - 10 * 86400)],
                    cases=[('c', 'open', NOW)])
            state = pool(folder, now=NOW)
            self.assertEqual(sum(state['layers'].values()), state['counts']['positions'])
            self.assertEqual(set(state['layers']), set(LAYER_ORDER))
            self.assertEqual(state['counts']['unsent'] + state['history']['currentPositions'],
                             state['counts']['positions'])
            self.assertEqual(state['business']['sendable']+state['business']['waiting']+
                             state['business']['inactive'],state['business']['total'])
            self.assertEqual(state['business']['total']+state['history']['currentPositions'],state['counts']['positions'])

    def test_product_invalidation_affects_only_that_pid_and_projects_inactive(self):
        with tempfile.TemporaryDirectory() as folder:
            active='1'*19;inactive='2'*19
            fixture(folder,[('a',active,1),('a',inactive,2),('b',inactive,1)],
                    [('a',0,'auto',0),('b',0,'auto',0)])
            path=Path(folder)/'var/second-cycle.sqlite'
            with closing(sqlite3.connect(path)) as db, db:
                db.executescript('CREATE TABLE plan(id TEXT,institution TEXT,market TEXT);'
                                 'CREATE TABLE catalog(id TEXT PRIMARY KEY,plan_id TEXT,source TEXT,observed REAL,state TEXT,payload TEXT);'
                                 'CREATE TABLE catalog_head(plan_id TEXT,source TEXT,snapshot_id TEXT);')
                offer={'pid':active,'campaignId':'9','catalogSource':'selected','creatorPercent':'13',
                       'publicPercent':'10','totalPercent':'15','endAt':'2099-01-01T00:00:00+00:00',
                       'available':True,'managementType':'full_managed','managementEvidenceRef':'fixture'}
                db.execute("INSERT INTO plan VALUES('p','bjn-local-research','it')")
                db.execute("INSERT INTO catalog VALUES('cat','p','live-it-selected',1,'complete',?)",(json.dumps([offer]),))
                db.execute("INSERT INTO catalog_head VALUES('p','live-it-selected','cat')")
            state=pool(folder,now=NOW)
            self.assertEqual(state['business'],{'sendable':1,'waiting':0,'inactive':2,'total':3})
            self.assertEqual(state['layers']['product_inactive'],2)
            self.assertEqual({row['creatorId'] for row in state['pools']['product_inactive']},{'a','b'})

    def test_a_missing_database_is_reported_not_raised(self):
        with tempfile.TemporaryDirectory() as folder:
            state = pool(folder, now=NOW)
            self.assertFalse(state['available'])

    def test_the_limit_caps_rows_not_counts(self):
        with tempfile.TemporaryDirectory() as folder:
            fixture(folder, [(f'c{i}', f'{i}' * 19, i) for i in range(1, 9)],
                    [(f'c{i}', 0, 'auto', 0) for i in range(1, 9)])
            state = pool(folder, now=NOW, limit=3)
            self.assertEqual(len(state['pools']['ready']), 3)
            self.assertEqual(state['counts']['ready'], 8)


if __name__ == '__main__':
    unittest.main()
