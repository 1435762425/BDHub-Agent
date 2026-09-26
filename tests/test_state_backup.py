import json
from contextlib import closing
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from lib.state_backup import create_backup, inventory, restore_backup, retention_plan, verify_backup  # noqa: E402


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

    def test_backup_completes_while_another_process_keeps_writing(self):
        import threading, time
        path = self.root / "var/a.sqlite"
        with closing(sqlite3.connect(path)) as db:
            db.execute("PRAGMA journal_mode=WAL")
            with db:
                db.executemany("INSERT INTO fact(value) VALUES(?)", [("x" * 2000,)] * 6000)
        stop = threading.Event()
        def writer():
            with closing(sqlite3.connect(path, timeout=30)) as db:
                while not stop.is_set():
                    with db:
                        db.execute("INSERT INTO fact(value) VALUES('live')")
                    time.sleep(0.002)
        thread = threading.Thread(target=writer)
        thread.start()
        try:
            before = time.time()
            result = create_backup(self.root, output=self.root / "live", clock=lambda: 124.0)
            elapsed = time.time() - before
        finally:
            stop.set()
            thread.join()
        self.assertTrue(result["valid"])
        self.assertLess(elapsed, 30)
        with closing(sqlite3.connect(self.root / "live/a.sqlite")) as db:
            self.assertEqual(db.execute("PRAGMA quick_check").fetchone()[0], "ok")
            self.assertGreaterEqual(db.execute("SELECT count(*) FROM fact").fetchone()[0], 6001)

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

    def test_retention_preserves_verified_recent_baseline_and_unknown_without_writes(self):
        backups = self.root / "backups"
        now = 1800000000.0
        for label, age in [("recent", 1), ("newest", 0), ("old-one", 100),
                           ("old-two", 101), ("old-drop", 102), ("v1-baseline", 103),
                           ("before-migration", 104), ("damaged", 105)]:
            create_backup(self.root, output=backups / label, clock=lambda age=age: now - age * 86400)
        (backups / "damaged/a.sqlite").write_bytes(b"invalid")
        (backups / "unknown").mkdir()
        (backups / "unknown/keep.txt").write_text("keep")
        (backups / "link").symlink_to(backups / "old-drop", target_is_directory=True)
        before = {str(p): (p.stat().st_size, p.stat().st_mtime_ns)
                  for p in backups.rglob("*") if p.is_file()}
        result = retention_plan(backups, clock=lambda: now)
        entries = {x["name"]: x for x in result["entries"]}
        self.assertEqual(entries["newest"]["decision"], "keep")
        self.assertIn("recent_recovery_point", entries["recent"]["reasons"])
        self.assertEqual(entries["old-one"]["decision"], "keep")
        self.assertEqual(entries["old-drop"]["decision"], "candidate")
        self.assertEqual(entries["old-drop"]["verification"], "verified")
        for name in ("v1-baseline", "before-migration", "damaged", "unknown", "link"):
            self.assertEqual(entries[name]["decision"], "keep")
        self.assertFalse(result["deletionPerformed"])
        self.assertFalse(result["offsiteCopyVerified"])
        self.assertEqual(before, {str(p): (p.stat().st_size, p.stat().st_mtime_ns)
                                 for p in backups.rglob("*") if p.is_file()})

    def test_retention_keeps_daily_weekly_points_and_custom_protection(self):
        backups = self.root / "backups"
        now = 1800000000.0
        for label, age in [("daily", 10), ("daily-duplicate", 10.001),
                           ("weekly", 45), ("custom", 150), ("latest", 1)]:
            create_backup(self.root, output=backups / label, clock=lambda age=age: now - age * 86400)
        result = retention_plan(backups, minimum_verified=1, protect=["custom"], clock=lambda: now)
        entries = {x["name"]: x for x in result["entries"]}
        self.assertIn("daily_recovery_point", entries["daily"]["reasons"])
        self.assertEqual(entries["daily-duplicate"]["decision"], "candidate")
        self.assertIn("weekly_recovery_point", entries["weekly"]["reasons"])
        self.assertIn("protected_label", entries["custom"]["reasons"])

    def test_retention_keeps_latest_for_each_database_inventory(self):
        backups = self.root / "backups"
        create_backup(self.root, output=backups / "old-inventory", clock=lambda: 1)
        policy_path = self.root / "config/state-backup.json"
        policy = json.loads(policy_path.read_text())
        policy["databases"].append("c.sqlite")
        policy_path.write_text(json.dumps(policy))
        with closing(sqlite3.connect(self.root / "var/c.sqlite")) as db, db:
            db.execute("CREATE TABLE fact(id)")
        create_backup(self.root, output=backups / "new-inventory", clock=lambda: 2)
        result = retention_plan(backups, minimum_verified=1, clock=lambda: 1800000000)
        self.assertEqual(result["candidateCount"], 0)
        for entry in result["entries"]:
            self.assertIn("latest_verified_inventory_snapshot", entry["reasons"])

    def test_retention_rejects_invalid_policy_and_symlink_root(self):
        backups = self.root / "backups"
        backups.mkdir()
        link = self.root / "link"
        link.symlink_to(backups, target_is_directory=True)
        for kwargs in ({"recent_days": -1}, {"daily_days": 3}, {"minimum_verified": 0}):
            with self.assertRaises(ValueError):
                retention_plan(backups, **kwargs)
        with self.assertRaises(ValueError):
            retention_plan(link)

    def test_retention_invalid_newest_cannot_displace_latest_verified_snapshot(self):
        backups = self.root / "backups"
        for name, stamp in [("old-valid", 1), ("new-broken", 2)]:
            create_backup(self.root, output=backups / name, clock=lambda stamp=stamp: stamp)
        (backups / "new-broken/a.sqlite").write_bytes(b"invalid")
        result = retention_plan(backups, minimum_verified=1, clock=lambda: 1800000000)
        entries = {x['name']: x for x in result['entries']}
        self.assertIn('latest_verified_inventory_snapshot', entries['old-valid']['reasons'])
        self.assertEqual(entries['new-broken']['verification'], 'failed')
        self.assertEqual(result['candidateCount'], 0)


if __name__ == "__main__":
    unittest.main()
