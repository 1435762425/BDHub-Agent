import json
from contextlib import closing
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from lib import state_backup  # noqa: E402
from lib.restore_drill import run_drill  # noqa: E402
from lib.state_backup import create_backup  # noqa: E402

NOW = 1_790_000_000.0


class RestoreDrillTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "config").mkdir()
        (self.root / "var").mkdir()
        # Snapshot order follows the policy: the parent ledger is copied before the child.
        policy = {"schemaVersion": "bdhub.state-backup-policy.v1",
                  "databases": ["creator-identities.sqlite", "second-cycle.sqlite"], "ignoredSnapshots": []}
        (self.root / "config/state-backup.json").write_text(json.dumps(policy), encoding="utf-8")
        self.sql("creator-identities.sqlite",
                 "CREATE TABLE creator_identity(creator_id TEXT PRIMARY KEY,market TEXT,oec_id TEXT,created_at TEXT);"
                 "INSERT INTO creator_identity VALUES('creator_a','it','1','2026-09-01T00:00:00+00:00');")
        self.sql("second-cycle.sqlite", """
            CREATE TABLE plan(id TEXT PRIMARY KEY,market TEXT);
            CREATE TABLE relationship(plan_id TEXT,creator_id TEXT);
            CREATE TABLE cycle_delivery(id TEXT PRIMARY KEY,plan_id TEXT,creator_id TEXT,created REAL);
            CREATE TABLE cycle_delivery_part(delivery_id TEXT,state TEXT);
            CREATE TABLE agent_schema_migration(version INTEGER,name TEXT,checksum TEXT,applied_at REAL);
            CREATE TABLE account_identity_generation(generation_id TEXT,market TEXT,account TEXT,state TEXT,published_at REAL);
            INSERT INTO plan VALUES('plan-it','it');
            INSERT INTO relationship VALUES('plan-it','creator_a');
            INSERT INTO cycle_delivery VALUES('delivery-a','plan-it','creator_a',1.0);
            INSERT INTO cycle_delivery_part VALUES('delivery-a','unknown');
            INSERT INTO agent_schema_migration VALUES(26,'current','x',1.0);
            INSERT INTO account_identity_generation VALUES('generation-1','it','acc6','published',1.0);
        """)
        self.accounts = {"it": "acc6"}

    def tearDown(self):
        self.temp.cleanup()

    def sql(self, name, script):
        with closing(sqlite3.connect(self.root / "var" / name)) as db, db:
            db.executescript(script)

    def backup(self, *, between=None):
        """Create a backup; ``between`` runs after the first database is copied (business keeps moving)."""
        original = state_backup._snapshot_database
        copied = []

        def snapshot(source, destination):
            result = original(source, destination)
            copied.append(Path(source).name)
            if between and len(copied) == 1:
                between()
            return result

        with mock.patch.object(state_backup, "_snapshot_database", snapshot):
            create_backup(self.root, output=self.root / "backup", clock=lambda: NOW)
        return self.root / "backup"

    def drill(self, backup, **options):
        return run_drill(backup, accounts=self.accounts, clock=lambda: NOW, **options)

    def test_consistent_group_is_restorable_and_the_isolated_copy_is_removed(self):
        with mock.patch("tempfile.mkdtemp", return_value=str(self.root / "drill")):
            report = self.drill(self.backup())
        self.assertEqual((report["state"], report["blockers"]), ("restorable", []))
        self.assertEqual(report["platformWrites"], 0)
        self.assertEqual(report["isolation"]["processesStarted"], 0)
        self.assertFalse(report["group"]["crossDatabaseAtomic"])
        self.assertEqual(report["identityDependencies"][0]["identityFiles"], "external_required")
        self.assertFalse((self.root / "drill").exists())

    def test_a_child_written_after_its_parent_was_copied_blocks_the_restore(self):
        def advance():
            # A new creator and its delivery are written after creator-identities was copied.
            import time
            later = time.time() + 1
            self.sql("creator-identities.sqlite", "INSERT INTO creator_identity VALUES('creator_b','it','2','x');")
            self.sql("second-cycle.sqlite", f"INSERT INTO relationship VALUES('plan-it','creator_b');"
                                            f"INSERT INTO cycle_delivery VALUES('delivery-b','plan-it','creator_b',{later});")
        report = self.drill(self.backup(between=advance))
        self.assertEqual(report["state"], "blocked")
        self.assertIn("reference:delivery_creator_identity:future_or_undated_missing", report["blockers"])
        self.assertIn("reference:relationship_creator_identity:future_or_undated_missing", report["blockers"])
        delivery = next(row for row in report["references"] if row["id"] == "delivery_creator_identity")
        self.assertEqual(delivery["sample"], ["creator_b"])

    def test_an_orphan_older_than_the_parent_copy_is_reported_not_blocking(self):
        self.sql("second-cycle.sqlite", "INSERT INTO cycle_delivery VALUES('delivery-old','plan-it','creator_gone',2.0);")
        report = self.drill(self.backup())
        self.assertEqual(report["state"], "restorable")
        self.assertIn("historical_orphans:delivery_creator_identity:1", report["warnings"])

    def test_partial_restore_without_the_parent_database_is_blocked(self):
        backup = self.backup()
        self.assertEqual(self.drill(backup, databases=["nope.sqlite"])["blockers"], ["database_not_in_backup:nope.sqlite"])
        report = self.drill(backup, databases=["second-cycle.sqlite"])
        self.assertEqual(report["state"], "blocked")
        self.assertIn("reference:delivery_creator_identity:dependency_database_missing", report["blockers"])
        self.assertTrue(report["group"]["partial"])

    def test_damaged_or_incomplete_backups_are_never_restorable(self):
        backup = self.backup()
        path = backup / "second-cycle.sqlite"
        data = bytearray(path.read_bytes())
        data[-1] ^= 0xFF
        path.write_bytes(bytes(data))
        damaged = self.drill(backup)
        self.assertEqual(damaged["state"], "blocked")
        self.assertTrue(damaged["blockers"][0].startswith("backup_invalid:"))
        self.assertEqual(self.drill(self.root / "missing")["state"], "blocked")

    def test_a_schema_newer_than_this_code_blocks(self):
        self.sql("second-cycle.sqlite", "INSERT INTO agent_schema_migration VALUES(99,'future','x',1.0);")
        report = self.drill(self.backup())
        self.assertIn("schema_newer_than_code:second-cycle.sqlite", report["blockers"])

    def test_unresolved_intent_without_a_published_identity_blocks(self):
        self.sql("second-cycle.sqlite", "DELETE FROM account_identity_generation;")
        report = self.drill(self.backup())
        self.assertIn("identity_generation_missing:it", report["blockers"])
        self.assertEqual(report["identityDependencies"][0]["unresolved"], {"cycle_delivery_part": 1})


if __name__ == "__main__":
    unittest.main()
