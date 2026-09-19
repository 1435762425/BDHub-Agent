"""Load the frozen BDHub protocol layer from this repository, never from the sibling source tree.

The old repository remains the read-only home of account configuration and runtime identity files
until those credentials are explicitly migrated.  Only ``bdhub`` Python source comes from
``vendor/``.  This keeps protocol code versioned with BDHub-Agent without copying secrets.
"""
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
VENDOR_ROOT = ROOT / "vendor"
LEGACY_ROOT = ROOT.parent / "01-BDSystem-V2"


def _same_path(value, expected):
    try:
        return Path(value).resolve() == Path(expected).resolve()
    except (OSError, TypeError, ValueError):
        return False


def configure_vendored_bdhub(*, root=ROOT, legacy_root=LEGACY_ROOT):
    """Return vendored ``bdhub.config`` with its default config root pointed at legacy read-only data."""
    root = Path(root).resolve()
    vendor = root / "vendor"
    legacy = Path(legacy_root).resolve()
    if not (vendor / "bdhub/__init__.py").is_file():
        raise RuntimeError("vendored_bdhub_missing")
    # Never let an inherited cwd/PYTHONPATH win over the frozen source.  The legacy path may remain
    # a data/config root, but it must not remain an import root.
    sys.path[:] = [entry for entry in sys.path if not _same_path(entry, legacy)]
    if not any(_same_path(entry, vendor) for entry in sys.path):
        sys.path.insert(0, str(vendor))
    loaded = sys.modules.get("bdhub")
    if loaded is not None:
        source = Path(getattr(loaded, "__file__", "")).resolve()
        if not source.is_relative_to(vendor):
            raise RuntimeError("nonvendored_bdhub_loaded")
    from bdhub import config
    import bdhub
    if not Path(bdhub.__file__).resolve().is_relative_to(vendor):
        raise RuntimeError("nonvendored_bdhub_loaded")
    # Vendored functions that call config.load() without an explicit path continue reading the
    # existing account configuration.  No file is copied or modified.
    config.ROOT = legacy
    return config
