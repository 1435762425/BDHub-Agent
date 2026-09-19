"""The sending pool: layers, the creator-level cooldown, and the rolling order."""
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from lib.lead_pool import LAYER_ORDER, LOCKED_COOLDOWN, UNLOCKED_COOLDOWN, pool  # noqa: E402

NOW = 1_800_000_000.0


def fixture(folder, positions, relationships, deliveries=(), cases=()):
    var = Path(folder) / 'var'
    var.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(var / 'second-cycle.sqlite') as conn:
        conn.executescript('''
            CREATE TABLE source_edge(plan_id TEXT,source_id TEXT,payload TEXT);
            CREATE TABLE cycle_identity_resolution(plan_id TEXT,source_id TEXT,creator_id TEXT);
            CREATE TABLE cycle_identity_outcome(plan_id TEXT,source_id TEXT,status TEXT);
            CREATE TABLE relationship(plan_id TEXT,creator_id TEXT,unlocked INTEGER,mode TEXT,rejected INTEGER);
            CREATE TABLE cycle_delivery(id TEXT,plan_id TEXT,creator_id TEXT,pid TEXT,state TEXT);
            CREATE TABLE cycle_delivery_part(delivery_id TEXT,kind TEXT,started REAL);
            CREATE TABLE service_case(plan_id TEXT,creator_id TEXT,state TEXT,updated REAL);''')
        for index, (creator, pid, rank) in enumerate(positions):
            sid = f's{index}'
            conn.execute('INSERT INTO source_edge VALUES(?,?,?)', ('p', sid,
                         '{"sourceKind":"kalodata_http","pid":"%s","sourceRank":%d,"units":10,"sourceHandle":"%s"}'
                         % (pid, rank, creator)))
            conn.execute('INSERT INTO cycle_identity_resolution VALUES(?,?,?)', ('p', sid, creator))
            conn.execute('INSERT INTO cycle_identity_outcome VALUES(?,?,?)', ('p', sid, 'completed'))
        for creator, unlocked, mode, rejected in relationships:
            conn.execute('INSERT INTO relationship VALUES(?,?,?,?,?)', ('p', creator, unlocked, mode, rejected))
        for index, (creator, pid, started) in enumerate(deliveries):
            conn.execute('INSERT INTO cycle_delivery VALUES(?,?,?,?,?)', (f'd{index}', 'p', creator, pid, 'confirmed'))
            conn.execute('INSERT INTO cycle_delivery_part VALUES(?,?,?)', (f'd{index}', 'card', started))
        for creator, state, updated in cases:
            conn.execute('INSERT INTO service_case VALUES(?,?,?,?)', ('p', creator, state, updated))
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

    def test_a_recent_send_puts_every_position_of_that_creator_on_cooldown(self):
        with tempfile.TemporaryDirectory() as folder:
            fixture(folder, [('a', '1' * 19, 1), ('a', '2' * 19, 2), ('b', '3' * 19, 3)],
                    [('a', 0, 'auto', 0), ('b', 0, 'auto', 0)],
                    deliveries=[('a', '9' * 19, NOW - 3600)])
            counts = layers(pool(folder, now=NOW))
            # Both of a's positions wait for a, and b is unaffected.
            self.assertEqual(counts['cooling'], 2)
            self.assertEqual(counts['ready'], 1)

    def test_an_unlocked_creator_comes_off_cooldown_sooner(self):
        with tempfile.TemporaryDirectory() as folder:
            fixture(folder, [('locked', '1' * 19, 1), ('unlocked', '2' * 19, 2)],
                    [('locked', 0, 'auto', 0), ('unlocked', 1, 'auto', 0)],
                    deliveries=[('locked', '9' * 19, NOW - 100000), ('unlocked', '8' * 19, NOW - 100000)])
            counts = layers(pool(folder, now=NOW))
            # 100000s is past the 24h unlocked cooldown but inside the 48h locked one.
            self.assertGreater(UNLOCKED_COOLDOWN, 0)
            self.assertLess(UNLOCKED_COOLDOWN, 100000)
            self.assertGreater(LOCKED_COOLDOWN, 100000)
            self.assertEqual(counts['ready'], 1)
            self.assertEqual(counts['cooling'], 1)
            self.assertEqual(pool(folder, now=NOW)['pools']['ready'][0]['handle'], 'unlocked')

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
            self.assertEqual(state['counts']['unsent'] + state['counts']['sent'],
                             state['counts']['positions'])

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
