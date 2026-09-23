from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from lib.delivery_diagnostics import diagnose


class DeliveryDiagnosticsTests(unittest.TestCase):
    def test_aggregates_confirmed_deliveries_excludes_partial_and_preserves_source(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "var").mkdir()
            path = root / "var/second-cycle.sqlite"
            with closing(sqlite3.connect(path)) as db, db:
                db.executescript("""
                    CREATE TABLE plan(id,market);
                    CREATE TABLE cycle_delivery(id,plan_id,creator_id,state);
                    CREATE TABLE cycle_delivery_part(delivery_id,kind,started,state);
                    CREATE TABLE cycle_delivery_check(delivery_id,kind,checked,payload);
                    INSERT INTO plan VALUES('p','my');
                    INSERT INTO cycle_delivery VALUES('a','p','private-creator-a','confirmed');
                    INSERT INTO cycle_delivery VALUES('b','p','private-creator-b','confirmed');
                    INSERT INTO cycle_delivery VALUES('c','p','private-creator-c','unknown');
                    INSERT INTO cycle_delivery_part VALUES('a','card',120,'confirmed');
                    INSERT INTO cycle_delivery_part VALUES('a','text',125,'confirmed');
                    INSERT INTO cycle_delivery_part VALUES('b','card',180,'confirmed');
                    INSERT INTO cycle_delivery_part VALUES('b','text',185,'confirmed');
                    INSERT INTO cycle_delivery_part VALUES('c','card',200,'confirmed');
                    INSERT INTO cycle_delivery_part VALUES('c','text',205,'unknown');
                """)
                for delivery, kind, checked in [('a', 'card', 122), ('a', 'card', 130), ('a', 'text', 128), ('b', 'text', 184)]:
                    db.execute('INSERT INTO cycle_delivery_check VALUES(?,?,?,?)',
                               (delivery, kind, checked, json.dumps({'status': 'confirmed', 'body': 'secret-body'})))
            log = root / 'var/market-send-worker-my.log'
            record = {'checkedAt': 250, 'result': {'market': 'my', 'requestId': 'r',
                       'timingMs': {'auth': 400, 'card': 600, 'secret': 'never-output'}}}
            log.write_text(json.dumps(record) + '\n' + 'not-json\n')
            (root / 'var/market-send-worker-my.json').write_text(json.dumps(record))
            before = path.read_bytes()
            result = diagnose(root, clock=lambda: 300)
            my = result['markets']['my']
            self.assertEqual(my['throughput']['completedDeliveries'], 2)
            self.assertEqual(my['confirmedComponents'], 5)
            self.assertEqual(my['throughput']['meanPerMinuteAcrossObservedSpan'], 1)
            self.assertEqual(my['missingOrInvalidConfirmedCheckTime'], 3)
            self.assertEqual(my['componentStartToFirstConfirmedReadbackMs']['median'], 2500)
            self.assertEqual(my['recordedStageTimingMs']['auth']['samples'], 1)
            self.assertEqual(my['recordedStageTimingMs']['auth']['median'], 400)
            serialized = json.dumps(result)
            for private in ('private-creator', 'secret-body', 'never-output'):
                self.assertNotIn(private, serialized)
            self.assertEqual(path.read_bytes(), before)
            recent = diagnose(root, since=170, clock=lambda: 300)
            self.assertEqual(recent['markets']['my']['throughput']['completedDeliveries'], 1)

    def test_missing_database_does_not_create_it(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(sqlite3.OperationalError):
                diagnose(folder)
            self.assertFalse((Path(folder) / 'var').exists())


if __name__ == '__main__':
    unittest.main()
