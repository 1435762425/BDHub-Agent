import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "scripts"))

from lib import offsite_backup  # noqa: E402

DAY = 86400


class OffsiteBackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.root = self.base / "project"
        self.target = self.base / "usb"
        self.target.mkdir()
        (self.root / "var").mkdir(parents=True)
        (self.root / "config").mkdir()
        subprocess.run(["git", "-C", str(self.root), "init", "-q"], check=True)
        (self.root / "README.md").write_text("project\n")
        subprocess.run(["git", "-C", str(self.root), "add", "README.md"], check=True)
        subprocess.run(["git", "-C", str(self.root), "-c", "user.name=t", "-c", "user.email=t@example.com",
                        "commit", "-q", "-m", "init"], check=True)
        (self.root / "README.md").write_text("project\nwork in progress\n")
        (self.root / "config/state-backup.json").write_text(json.dumps(
            {"schemaVersion": "bdhub.state-backup-policy.v1", "databases": ["a.sqlite"], "ignoredSnapshots": []}))
        with closing(sqlite3.connect(self.root / "var/a.sqlite")) as db, db:
            db.execute("CREATE TABLE t(x)")
            db.execute("INSERT INTO t VALUES(1)")
        (self.root / "config/kalodata-identity.json").write_text('{"secret": "local"}')
        home = self.base / "project-backups"
        (home / "retention/20260924T000000Z").mkdir(parents=True)
        (home / "retention/20260924T000000Z/manifest.json").write_text("{}")
        (home / "git").mkdir()
        (home / "git/old.bundle").write_text("superseded")

    def tearDown(self):
        self.temp.cleanup()

    def test_copy_is_encrypted_restorable_and_the_key_stays_local(self):
        receipt = offsite_backup.run(self.root, self.target, now=1_790_000_000)
        destination = Path(receipt["destination"])
        key = Path(receipt["keyPath"])
        self.assertTrue(receipt["keyCreated"])
        self.assertEqual(key.stat().st_mode & 0o777, 0o600)
        self.assertFalse(any(path.name == key.name for path in self.target.rglob("*")))
        self.assertEqual(sorted(path.name for path in destination.iterdir()),
                         ["README.txt", "backup.tar.gz.enc", "manifest.json"])
        self.assertNotIn(b'"secret"', (destination / "backup.tar.gz.enc").read_bytes())
        self.assertEqual(offsite_backup.verify(self.root, self.target)["copy"], destination.name)

        restore = self.base / "restore"
        restore.mkdir()
        plain = subprocess.run(["openssl", "enc", "-d", *offsite_backup.CIPHER, "-pass", f"file:{key}",
                                "-in", str(destination / "backup.tar.gz.enc")], capture_output=True, check=True)
        subprocess.run(["tar", "-xzf", "-", "-C", str(restore)], input=plain.stdout, check=True)
        self.assertEqual((restore / "config/kalodata-identity.json").read_text(), '{"secret": "local"}')
        self.assertTrue((restore / "archives/retention/20260924T000000Z/manifest.json").exists())
        self.assertFalse((restore / "archives/git").exists())
        with closing(sqlite3.connect(restore / "state" / receipt["stateSnapshot"] / "a.sqlite")) as db:
            self.assertEqual(db.execute("SELECT x FROM t").fetchall(), [(1,)])
        clone = self.base / "clone"
        subprocess.run(["git", "clone", "-q", str(restore / "code/code.bundle"), str(clone)], check=True)
        subprocess.run(["git", "-C", str(clone), "apply", str(restore / "code/uncommitted.patch")], check=True)
        self.assertEqual((clone / "README.md").read_text(), "project\nwork in progress\n")

        again = offsite_backup.run(self.root, self.target, now=1_790_000_000 + DAY)
        self.assertFalse(again["keyCreated"])
        self.assertEqual(again["keyFingerprint"], receipt["keyFingerprint"])

    def test_target_keeps_the_newest_copies_and_nothing_else_is_touched(self):
        folder = self.target / offsite_backup.FOLDER
        (folder / "20260101T000000Z").mkdir(parents=True)  # unfinished copy from an interrupted run
        (folder / "notes").mkdir()
        (self.target / "other.iso").write_text("user data")
        for day in range(3):
            offsite_backup.run(self.root, self.target, now=1_790_000_000 + day * DAY, keep=2)
        kept = [path.name for path in offsite_backup.copies(self.target)]
        self.assertEqual(len(kept), 2)
        self.assertEqual(sorted(path.name for path in folder.iterdir()), sorted([*kept, "notes"]))
        self.assertEqual((self.target / "other.iso").read_text(), "user data")

    def test_failed_write_leaves_no_copy_behind(self):
        blocked = self.base / "project-backups/retention/20260924T000000Z/manifest.json"
        blocked.chmod(0)
        try:
            with self.assertRaises(PermissionError):
                offsite_backup.run(self.root, self.target)
        finally:
            blocked.chmod(0o600)
        self.assertEqual(list((self.target / offsite_backup.FOLDER).iterdir()), [])

    def test_damaged_copy_fails_the_check(self):
        receipt = offsite_backup.run(self.root, self.target)
        archive = Path(receipt["destination"]) / offsite_backup.ARCHIVE
        data = bytearray(archive.read_bytes())
        data[len(data) // 2] ^= 0xFF
        archive.write_bytes(bytes(data))
        with self.assertRaisesRegex(ValueError, "offsite_verify_failed"):
            offsite_backup.verify(self.root, self.target)

    def test_auto_only_uses_volumes_that_already_hold_the_folder(self):
        volumes = self.base / "Volumes"
        fresh, stale, foreign = volumes / "FRESH", volumes / "STALE", volumes / "FOREIGN"
        for volume in (fresh, stale, foreign):
            volume.mkdir(parents=True)
        now = 1_790_000_000
        offsite_backup.run(self.root, fresh, now=now - 3600)
        offsite_backup.run(self.root, stale, now=now - 3 * DAY)
        events = []
        results = offsite_backup.auto(self.root, volumes, now=now,
                                      notify=lambda volume, event: events.append((volume, event)))
        self.assertEqual([Path(entry["target"]).name for entry in results], ["FRESH", "STALE"])
        self.assertNotIn("written", results[0])
        self.assertTrue(results[1]["written"])
        self.assertEqual(len(offsite_backup.copies(stale)), 2)
        self.assertEqual(list(foreign.iterdir()), [])
        self.assertEqual(events, [("FRESH", "checked"), ("STALE", "writing"), ("STALE", "written")])

    def test_target_inside_the_project_or_its_backups_is_refused(self):
        for target in (self.root / "var", self.base / "project-backups/retention"):
            with self.subTest(target=target), self.assertRaisesRegex(ValueError, "offsite_target_not_offsite"):
                offsite_backup.plan(self.root, target)

    def test_unlisted_or_missing_database_is_refused(self):
        (self.root / "config/state-backup.json").write_text(json.dumps(
            {"schemaVersion": "bdhub.state-backup-policy.v1", "databases": ["a.sqlite", "b.sqlite"],
             "ignoredSnapshots": []}))
        with self.assertRaisesRegex(ValueError, "offsite_state_inventory_not_ready"):
            offsite_backup.plan(self.root, self.target)


if __name__ == "__main__":
    unittest.main()
