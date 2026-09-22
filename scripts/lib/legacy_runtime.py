"""Load the frozen BDHub protocol layer from this repository, never from the sibling source tree.

The old repository remains the read-only home of account configuration and runtime identity files
until those credentials are explicitly migrated.  Only ``bdhub`` Python source comes from
``vendor/``.  This keeps protocol code versioned with BDHub-Agent without copying secrets.
"""
from dataclasses import replace
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
import json
import re
import sqlite3
import sys
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[2]
VENDOR_ROOT = ROOT / "vendor"
LEGACY_ROOT = ROOT.parent / "01-BDSystem-V2"
_PROJECT_REF = re.compile(r"^project-(browser|http|im):([a-f0-9]{32})$")
_BEIJING = ZoneInfo("Asia/Shanghai")
_ROLE_SLOT = {"communications": (14, 30), "supply": (14, 40)}


def _project_assignment(root, account):
    try:
        value = json.loads((Path(root) / "config/market-accounts.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return ({"market": "it", "role": "communications"} if account == "acc6" else
                {"market": "it", "role": "supply"} if account == "acc9" else None)
    for market, pair in (value.get("markets") or {}).items():
        for role, name in (pair.get("roles") or {}).items():
            if name == account and role in _ROLE_SLOT:
                return {"market": market, "role": role}
    return None


def _same_path(value, expected):
    try:
        return Path(value).resolve() == Path(expected).resolve()
    except (OSError, TypeError, ValueError):
        return False


def project_identity_paths(root, account):
    """Resolve the latest atomically published project identity without exposing its files."""
    root = Path(root).resolve()
    if not isinstance(account, str) or not re.fullmatch(r"acc[1-9][0-9]*", account):
        return None
    database = root / "var/second-cycle.sqlite"
    if not database.is_file():
        return None
    try:
        with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as connection:
            row = connection.execute(
                "SELECT market,role,browser_ref,http_ref,im_ref FROM account_identity_generation "
                "WHERE account=? AND state='published' "
                "ORDER BY published_at DESC,rowid DESC LIMIT 1", (account,),
            ).fetchone()
    except (OSError, sqlite3.Error):
        return None
    if not row or _project_assignment(root, account) != {"market": row[0], "role": row[1]}:
        return None
    matches = [_PROJECT_REF.fullmatch(str(value or "")) for value in row[2:]]
    if len(matches) != 3 or any(match is None for match in matches):
        return None
    candidate_ids = {match.group(2) for match in matches if match is not None}
    if len(candidate_ids) != 1:
        return None
    candidate_id = candidate_ids.pop()
    generation = root / "var/account-identities" / account / "generations" / candidate_id
    profile = generation / "profile"
    headers = generation / "headers.json"
    sensitive_root = (root / "var/account-identities").resolve()
    try:
        resolved_generation = generation.resolve()
        if not resolved_generation.is_relative_to(sensitive_root):
            return None
        if generation.is_symlink() or profile.is_symlink() or headers.is_symlink():
            return None
    except OSError:
        return None
    if not profile.is_dir() or not headers.is_file():
        return None
    return {"candidateId": candidate_id, "profileDir": profile, "headersJson": headers,
            "market": row[0], "role": row[1]}


def project_identity_maintenance_due(root, account, now=None):
    """Return the 72-hour role slot for a published project generation, or ``None`` for legacy."""
    root = Path(root).resolve()
    if project_identity_paths(root, account) is None:
        return None
    try:
        with closing(sqlite3.connect((root / "var/second-cycle.sqlite").as_uri() + "?mode=ro", uri=True)) as db:
            row = db.execute(
                "SELECT role,published_at FROM account_identity_generation "
                "WHERE account=? AND state='published' "
                "ORDER BY published_at DESC,rowid DESC LIMIT 1", (account,),
            ).fetchone()
    except (OSError, sqlite3.Error):
        return None
    if not row or row[0] not in _ROLE_SLOT or not isinstance(row[1], (int, float)):
        return None
    base = datetime.fromtimestamp(float(row[1]), _BEIJING) + timedelta(hours=72)
    hour, minute = _ROLE_SLOT[row[0]]
    scheduled = base.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if scheduled < base:
        scheduled += timedelta(days=1)
    observed = now if isinstance(now, datetime) else datetime.now(_BEIJING)
    if observed.tzinfo is None:
        raise ValueError("project_identity_maintenance_now_requires_timezone")
    return observed.astimezone(_BEIJING) >= scheduled


def project_account_enabled(root, account):
    """Read the new project's own enable switch; a published account defaults to enabled."""
    root = Path(root).resolve()
    if project_identity_paths(root, account) is None:
        return None
    try:
        with closing(sqlite3.connect((root / "var/second-cycle.sqlite").as_uri() + "?mode=ro", uri=True)) as db:
            paths = project_identity_paths(root, account)
            if paths is None:
                return None
            row = db.execute(
                "SELECT enabled FROM account_runtime_setting WHERE market=? AND account=?",
                (paths["market"], account),
            ).fetchone()
    except (OSError, sqlite3.Error):
        return True
    return bool(row[0]) if row else True


def _install_project_account_overlay(config, root):
    """Make all vendored transports consume a published project identity when one exists."""
    if not hasattr(config, "_bdhub_agent_base_load_accounts"):
        config._bdhub_agent_base_load_accounts = config.load_accounts

        def load_accounts_with_project_identity(cfg=None, path=None):
            accounts = config._bdhub_agent_base_load_accounts(cfg, path=path)
            agent_root = Path(getattr(config, "_bdhub_agent_root"))
            output = []
            for account in accounts:
                paths = project_identity_paths(agent_root, account.name)
                if paths is None:
                    output.append(account)
                    continue
                role = paths["role"]
                market = paths["market"]
                if role not in _ROLE_SLOT:
                    output.append(account)
                    continue
                output.append(replace(account, profile_dir=paths["profileDir"],
                                      headers_json=paths["headersJson"], market=market,
                                      enabled=bool(project_account_enabled(agent_root, account.name)),
                                      listener_pool=False, im_send_pool=True,
                                      collection_pool=role == "communications", report_pool=False,
                                      share_link_pool=False, sample_review_pool=False,
                                      identity_lifecycle_enabled=False, auto_relogin=False))
            return output

        config.load_accounts = load_accounts_with_project_identity
    config._bdhub_agent_root = Path(root).resolve()

    from bdhub import scheduled_relogin
    if not hasattr(scheduled_relogin, "_bdhub_agent_base_maintenance_due"):
        scheduled_relogin._bdhub_agent_base_maintenance_due = scheduled_relogin.maintenance_due

        def maintenance_due_with_project_identity(account, **kwargs):
            agent_root = Path(getattr(config, "_bdhub_agent_root"))
            due = project_identity_maintenance_due(agent_root, account.name, kwargs.get("now"))
            if due is not None:
                return due
            return scheduled_relogin._bdhub_agent_base_maintenance_due(account, **kwargs)

        scheduled_relogin.maintenance_due = maintenance_due_with_project_identity


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
    _install_project_account_overlay(config, root)
    return config
