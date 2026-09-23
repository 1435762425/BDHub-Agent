import tempfile
import unittest
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from lib.collaboration_status import apply_backfill, backfill_preview, observe_showcase, set_manual  # noqa:E402
from lib.schema_migrations import apply_database  # noqa:E402
from lib.second_cycle import CycleError, CycleStore  # noqa:E402


NOW = 1_800_000_000.0


class CollaborationStatusTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "var").mkdir()
        with CycleStore(self.root / "var/second-cycle.sqlite", lambda: NOW) as store:
            self.plan = store.plan("bjn-local-research", "it")
        apply_database(self.root, "second-cycle", clock=lambda: NOW)
        self.store = CycleStore(self.root / "var/second-cycle.sqlite", lambda: NOW)
        self.store.db.execute("INSERT INTO relationship VALUES(?,?,?,'auto',0,1,0,0)",
                              (self.plan, "creator-a", "101"))
        self.store.db.execute("INSERT INTO relationship VALUES(?,?,?,'auto',1,1,0,0)",
                              (self.plan, "creator-b", "102"))
        self.store.db.execute("CREATE TABLE IF NOT EXISTS inbox_event(plan_id,oec,kind,historical)")
        self.store.db.execute("INSERT INTO inbox_event VALUES(?,?,?,0)",
                              (self.plan, "101", "showcaseNotifications"))

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_backfill_is_local_and_never_guesses_paid(self):
        preview = backfill_preview(self.store)
        self.assertEqual(preview["counts"], {"normal": 0, "collaborated": 1, "paid": 0, "rejected": 1})
        applied = apply_backfill(self.store)
        self.assertEqual(applied["applied"], 2)
        self.assertEqual(apply_backfill(self.store)["applied"], 0)

    def test_manual_status_wins_over_later_showcase(self):
        apply_backfill(self.store)
        changed = set_manual(self.store, "creator-a", "paid", "collaboration-request-0001", 1, 1)
        self.assertEqual(changed["status"], "paid")
        observed = observe_showcase(self.store, "creator-a", {"messageId": "m2"})
        self.assertFalse(observed["changed"])
        self.assertEqual(observed["status"], "paid")
        with self.assertRaisesRegex(CycleError, "relationship_control_changed"):
            set_manual(self.store, "creator-a", "normal", "collaboration-request-0002", 1, 1)

    def test_showcase_observation_stays_in_its_market_plan(self):
        br = self.store.plan("bjn-local-research", "br")
        self.store.db.execute("INSERT INTO relationship VALUES(?,?,?,'auto',0,1,0,0)",
                              (br, "creator-br", "201"))
        observed = observe_showcase(self.store, "creator-br", {"messageId": "br-m1"}, plan_id=br)
        self.assertTrue(observed["changed"])
        self.assertEqual(observed["status"], "collaborated")
        self.assertEqual(self.store.db.execute(
            "SELECT count(*) FROM creator_collaboration_current WHERE plan_id=? AND creator_id='creator-br'",
            (br,)).fetchone()[0], 1)
        self.assertEqual(self.store.db.execute(
            "SELECT count(*) FROM creator_collaboration_current WHERE plan_id=? AND creator_id='creator-br'",
            (self.plan,)).fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
