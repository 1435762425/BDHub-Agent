from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from lib.legacy_runtime import configure_vendored_bdhub  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
