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

from lib.legacy_runtime import (configure_vendored_bdhub, project_identity_maintenance_due,
                                project_identity_paths)  # noqa: E402


class VendoredLegacyRuntime(unittest.TestCase):
    def test_protocol_source_is_vendored_while_config_root_remains_explicit(self):
        with tempfile.TemporaryDirectory() as directory:
            legacy = Path(directory) / "legacy-state"
            config = configure_vendored_bdhub(root=ROOT)
            previous = config.ROOT
            try:
                config = configure_vendored_bdhub(root=ROOT, legacy_root=legacy)
                import bdhub
                self.assertTrue(Path(bdhub.__file__).resolve().is_relative_to(ROOT / "vendor"))
                self.assertEqual(config.ROOT, legacy.resolve())
                self.assertFalse(any(Path(entry).resolve() == legacy.resolve() for entry in sys.path if entry))
            finally:
                config.ROOT = previous

    def test_active_python_never_adds_the_legacy_source_tree_to_sys_path(self):
        forbidden = ("sys.path.insert(0,str(LEGACY", "sys.path.insert(0, str(LEGACY",
                     "sys.path.insert(0,str(ROOT.parent/'01-BDSystem-V2'",
                     'sys.path.insert(0, legacy)')
        offenders = []
        for path in (ROOT / "scripts").rglob("*.py"):
            if path.name == "vendor-legacy-bdhub.py":
                continue
            text = path.read_text(encoding="utf-8")
            if any(value in text for value in forbidden):
                offenders.append(str(path.relative_to(ROOT)))
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
            (generation / "headers.json").unlink()
            self.assertIsNone(project_identity_paths(root, "acc6"))


if __name__ == "__main__":
    unittest.main()
