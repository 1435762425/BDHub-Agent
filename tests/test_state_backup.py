import json
from contextlib import closing
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from lib.state_backup import create_backup, inventory, restore_backup, verify_backup  # noqa: E402


class StateBackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "config").mkdir()
        (self.root / "var").mkdir()
        policy = {"schemaVersion": "bdhub.state-backup-policy.v1",
                  "databases": ["a.sqlite", "b.sqlite"],
                  "ignoredSnapshots": ["*-before-*.sqlite"]}
        (self.root / "config/state-backup.json").write_text(json.dumps(policy), encoding="utf-8")
        for name in policy["databases"]:
            with closing(sqlite3.connect(self.root / "var" / name)) as db, db:
                db.execute("CREATE TABLE fact(id INTEGER PRIMARY KEY,value TEXT)")
                db.execute("INSERT INTO fact(value) VALUES(?)", ("original-" + name,))
        with closing(sqlite3.connect(self.root / "var/a-before-change.sqlite")) as db, db:
            db.execute("CREATE TABLE ignored(id)")

    def tearDown(self):
        self.temp.cleanup()

    def test_create_captures_wal_and_excludes_secrets_sidecars_and_old_snapshots(self):
        secret = "never-copy-this-key"
        (self.root / "config/typesafe.json").write_text(json.dumps({"apiKey": secret}), encoding="utf-8")
        live = sqlite3.connect(self.root / "var/a.sqlite")
        try:
            live.execute("PRAGMA journal_mode=WAL")
            live.execute("INSERT INTO fact(value) VALUES('committed-in-wal')")
            live.commit()
            result = create_backup(self.root, output=self.root / "portable", clock=lambda: 123.0)
        finally:
            live.close()
        self.assertTrue(result["valid"])
        self.assertEqual(result["databases"], 2)
        self.assertEqual({path.name for path in (self.root / "portable").iterdir()},
                         {"manifest.json", "a.sqlite", "b.sqlite"})
        with closing(sqlite3.connect(self.root / "portable/a.sqlite")) as db:
            self.assertEqual([row[0] for row in db.execute("SELECT value FROM fact ORDER BY id")],
                             ["original-a.sqlite", "committed-in-wal"])
        self.assertNotIn(secret, (self.root / "portable/manifest.json").read_text(encoding="utf-8"))

    def test_unregistered_database_refuses_backup(self):
        with closing(sqlite3.connect(self.root / "var/unregistered.sqlite")) as db, db:
            db.execute("CREATE TABLE fact(id)")
        state = inventory(self.root)
        self.assertFalse(state["ready"])
        self.assertEqual(state["unexpected"], ["unregistered.sqlite"])
        with self.assertRaisesRegex(ValueError, "state_backup_inventory_not_ready"):
            create_backup(self.root, output=self.root / "refused")
        self.assertFalse((self.root / "refused").exists())

    def test_modified_snapshot_fails_checksum_verification(self):
        create_backup(self.root, output=self.root / "portable")
        with (self.root / "portable/a.sqlite").open("ab") as target:
            target.write(b"tampered")
        with self.assertRaisesRegex(ValueError, "state_backup_file_invalid|state_backup_checksum_mismatch"):
            verify_backup(self.root / "portable")

    def test_restore_requires_confirmation_and_an_empty_target(self):
        create_backup(self.root, output=self.root / "portable")
        with self.assertRaisesRegex(ValueError, "state_restore_confirmation_required"):
            restore_backup(self.root / "portable", self.root / "restored")
        occupied = self.root / "occupied"
        occupied.mkdir()
        (occupied / "keep").write_text("keep", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "state_restore_target_not_empty"):
            restore_backup(self.root / "portable", occupied, confirmed=True)
        result = restore_backup(self.root / "portable", self.root / "restored",
                                confirmed=True, clock=lambda: 456.0)
        self.assertTrue(result["restored"])
        self.assertEqual(result["databases"], 2)
        self.assertTrue((self.root / "restored/.bdhub-restore.json").exists())
        with closing(sqlite3.connect(self.root / "restored/b.sqlite")) as db:
            self.assertEqual(db.execute("SELECT value FROM fact").fetchone()[0], "original-b.sqlite")

    def test_manifest_cannot_escape_the_backup_directory(self):
        create_backup(self.root, output=self.root / "portable")
        manifest_path = self.root / "portable/manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["databases"][0]["name"] = "../outside.sqlite"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "state_backup_database_name_invalid"):
            verify_backup(self.root / "portable")


if __name__ == "__main__":
    unittest.main()
