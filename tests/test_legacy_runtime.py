from pathlib import Path
from contextlib import closing
from datetime import datetime
import sqlite3
import sys
import tempfile
import unittest
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from lib.legacy_runtime import (configure_vendored_bdhub, private_root, project_account_enabled,
                                project_identity_maintenance_due, project_identity_paths)  # noqa: E402


class VendoredLegacyRuntime(unittest.TestCase):
    def test_protocol_source_is_vendored_and_config_root_is_the_project_private_root(self):
        config = configure_vendored_bdhub(root=ROOT)
        import bdhub
        self.assertTrue(Path(bdhub.__file__).resolve().is_relative_to(ROOT / "vendor"))
        self.assertEqual(config.ROOT, (ROOT / "private-data").resolve())

    def test_private_root_redirected_outside_the_project_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "project"; outside = Path(directory) / "elsewhere"
            root.mkdir(); outside.mkdir()
            (root / "private-data").symlink_to(outside)
            with self.assertRaisesRegex(RuntimeError, "private_root_outside_project"):
                private_root(root)

    def test_runtime_code_never_references_the_original_project(self):
        # Offline tools that exist to read the original source tree are the only exceptions.
        allowed = {"vendor-legacy-bdhub.py", "audit-tiktok-interface-sources.py", "audit-tiktok-web-bundle.py"}
        offenders = [str(path.relative_to(ROOT)) for path in (ROOT / "scripts").rglob("*.py")
                     if path.name not in allowed and "01-BDSystem-V2" in path.read_text(encoding="utf-8")]
        offenders += [str(path.relative_to(ROOT)) for path in (ROOT / "apps/web/src").rglob("*.ts*")
                      if "01-BDSystem-V2" in path.read_text(encoding="utf-8")]
        self.assertEqual(offenders, [])

    def test_project_identity_paths_only_resolve_a_complete_published_generation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "var").mkdir()
            candidate = "a" * 32
            generation = root / "var/account-identities/acc6/generations" / candidate
            (generation / "profile").mkdir(parents=True)
            (generation / "headers.json").write_text("{}")
            with closing(sqlite3.connect(root / "var/second-cycle.sqlite")) as database:
                database.execute("CREATE TABLE account_identity_generation(market,account,role,state,published_at,browser_ref,http_ref,im_ref)")
                database.execute("CREATE TABLE account_runtime_setting(market,account,enabled)")
                published = datetime(2026, 9, 20, 1, 0, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp()
                database.execute("INSERT INTO account_identity_generation VALUES(?,?,?,?,?,?,?,?)",
                    ("it", "acc6", "communications", "published", published, f"project-browser:{candidate}",
                     f"project-http:{candidate}", f"project-im:{candidate}"))
                database.commit()
            paths = project_identity_paths(root, "acc6")
            self.assertEqual(paths["candidateId"], candidate)
            self.assertEqual(paths["profileDir"].resolve(), (generation / "profile").resolve())
            before = datetime(2026, 9, 23, 14, 29, tzinfo=ZoneInfo("Asia/Shanghai"))
            after = datetime(2026, 9, 23, 14, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
            self.assertFalse(project_identity_maintenance_due(root, "acc6", before))
            self.assertTrue(project_identity_maintenance_due(root, "acc6", after))
            self.assertTrue(project_account_enabled(root, "acc6"))
            with closing(sqlite3.connect(root / "var/second-cycle.sqlite")) as database:
                database.execute("INSERT INTO account_runtime_setting VALUES('it','acc6',0)")
                database.commit()
            self.assertFalse(project_account_enabled(root, "acc6"))
            (generation / "headers.json").unlink()
            self.assertIsNone(project_identity_paths(root, "acc6"))


if __name__ == "__main__":
    unittest.main()
