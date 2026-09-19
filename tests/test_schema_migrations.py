import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from lib.schema_migrations import apply_all, check_all  # noqa: E402


class SchemaMigrations(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "var").mkdir()
        for name in ("catalog-links.sqlite", "second-cycle.sqlite"):
            with closing(sqlite3.connect(self.root / "var" / name)) as db, db:
                db.execute("CREATE TABLE existing_fact(id TEXT PRIMARY KEY)")
                db.execute("INSERT INTO existing_fact VALUES('kept')")

    def tearDown(self):
        self.temp.cleanup()

    def test_check_is_read_only_and_apply_is_idempotent(self):
        before = check_all(self.root)
        self.assertFalse(before["ready"])
        self.assertEqual([len(state["pending"]) for state in before["databases"]],[1,3])
        # A check must not create its own registry.
        with closing(sqlite3.connect(self.root / "var" / "catalog-links.sqlite")) as db:
            self.assertFalse(db.execute("SELECT 1 FROM sqlite_master WHERE name='agent_schema_migration'").fetchone())
        first = apply_all(self.root, clock=lambda: 123.0)
        second = apply_all(self.root, clock=lambda: 456.0)
        self.assertTrue(first["ready"])
        self.assertTrue(second["ready"])
        self.assertTrue(all(not state["appliedNow"] for state in second["databases"]))
        with closing(sqlite3.connect(self.root / "var" / "catalog-links.sqlite")) as db:
            self.assertEqual(db.execute("SELECT id FROM existing_fact").fetchone()[0], "kept")
            self.assertTrue(db.execute("SELECT 1 FROM sqlite_master WHERE name='catalog_current_binding'").fetchone())
        with closing(sqlite3.connect(self.root / "var" / "second-cycle.sqlite")) as db:
            self.assertTrue(db.execute("SELECT 1 FROM sqlite_master WHERE name='lead_query_head'").fetchone())
            self.assertTrue(db.execute("SELECT 1 FROM sqlite_master WHERE name='source_edge_index'").fetchone())
            self.assertTrue(db.execute("SELECT 1 FROM sqlite_master WHERE name='cycle_bulk_freeze'").fetchone())
            self.assertTrue(db.execute("SELECT 1 FROM sqlite_master WHERE name='cycle_bulk_candidate'").fetchone())

    def test_missing_database_is_never_created_by_check_or_apply(self):
        missing = self.root / "var" / "second-cycle.sqlite"
        missing.unlink()
        state = check_all(self.root)
        self.assertFalse(state["ready"])
        self.assertFalse(missing.exists())
        with self.assertRaisesRegex(ValueError, "migration_database_missing"):
            apply_all(self.root)
        self.assertFalse(missing.exists())

    def test_checksum_mismatch_is_refused(self):
        apply_all(self.root)
        with closing(sqlite3.connect(self.root / "var" / "catalog-links.sqlite")) as db, db:
            db.execute("UPDATE agent_schema_migration SET checksum='wrong'")
        with self.assertRaisesRegex(ValueError, "migration_checksum_mismatch"):
            check_all(self.root)


if __name__ == "__main__":
    unittest.main()
