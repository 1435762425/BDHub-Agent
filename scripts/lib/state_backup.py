"""Consistent, portable backups for the BDHub-Agent-owned SQLite ledgers.

Each database is copied with SQLite's online backup API, so WAL contents are captured without
copying ``-wal`` or ``-shm`` sidecars.  The resulting directory contains only immutable database
snapshots plus a checksummed manifest.  Restore never overwrites an existing state directory.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
from fnmatch import fnmatch
import hashlib
import json
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import time
import uuid


POLICY_SCHEMA = "bdhub.state-backup-policy.v1"
MANIFEST_SCHEMA = "bdhub.state-backup.v1"
RESTORE_SCHEMA = "bdhub.state-restore.v1"
DATABASE_NAME = re.compile(r"[a-z0-9][a-z0-9._-]{0,119}\.sqlite")
LABEL = re.compile(r"[a-z0-9][a-z0-9-]{0,31}")


def _encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_bytes(value):
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _database_name(value):
    if not isinstance(value, str) or not DATABASE_NAME.fullmatch(value) or Path(value).name != value:
        raise ValueError("state_backup_database_name_invalid")
    return value


def load_policy(root):
    path = Path(root) / "config/state-backup.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError("state_backup_policy_unavailable") from error
    if not isinstance(raw, dict) or set(raw) != {"schemaVersion", "databases", "ignoredSnapshots"}:
        raise ValueError("state_backup_policy_invalid")
    if raw["schemaVersion"] != POLICY_SCHEMA or not isinstance(raw["databases"], list) or not raw["databases"]:
        raise ValueError("state_backup_policy_invalid")
    databases = tuple(_database_name(value) for value in raw["databases"])
    if len(databases) != len(set(databases)):
        raise ValueError("state_backup_policy_invalid")
    patterns = raw["ignoredSnapshots"]
    if not isinstance(patterns, list) or any(not isinstance(pattern, str) or not pattern.endswith(".sqlite")
                                             or "/" in pattern or "\\" in pattern or ".." in pattern
                                             for pattern in patterns):
        raise ValueError("state_backup_policy_invalid")
    normalized = {"schemaVersion": POLICY_SCHEMA, "databases": list(databases),
                  "ignoredSnapshots": list(patterns)}
    return normalized | {"sha256": _sha256_bytes(_encoded(normalized).encode("utf-8"))}


def inventory(root):
    root = Path(root)
    policy = load_policy(root)
    var = root / "var"
    configured = set(policy["databases"])
    files = {path.name: path for path in var.glob("*.sqlite")}
    ignored = sorted(name for name in files
                     if any(fnmatch(name, pattern) for pattern in policy["ignoredSnapshots"]))
    unexpected = sorted(set(files) - configured - set(ignored))
    missing = sorted(configured - set(files))
    unsafe = sorted(name for name, path in files.items() if name in configured
                    and (path.is_symlink() or not path.is_file()))
    return {"schema": POLICY_SCHEMA, "policySha256": policy["sha256"],
            "configured": list(policy["databases"]), "ignoredSnapshots": ignored,
            "missing": missing, "unexpected": unexpected, "unsafe": unsafe,
            "ready": not missing and not unexpected and not unsafe}


def _inspect_database(path):
    path = Path(path)
    uri = path.resolve().as_uri() + "?mode=ro&immutable=1"
    with closing(sqlite3.connect(uri, uri=True, timeout=30)) as db:
        quick = [row[0] for row in db.execute("PRAGMA quick_check")]
        if quick != ["ok"]:
            raise ValueError("state_backup_integrity_failed")
        page_count = db.execute("PRAGMA page_count").fetchone()[0]
        page_size = db.execute("PRAGMA page_size").fetchone()[0]
        schema_version = db.execute("PRAGMA schema_version").fetchone()[0]
        user_version = db.execute("PRAGMA user_version").fetchone()[0]
    return {"quickCheck": "ok", "pageCount": page_count, "pageSize": page_size,
            "schemaVersion": schema_version, "userVersion": user_version}


def _snapshot_database(source, destination):
    source = Path(source)
    destination = Path(destination)
    started = time.time()
    with closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True, timeout=30)) as reader:
        reader.execute("PRAGMA query_only=ON")
        with closing(sqlite3.connect(destination, timeout=30)) as writer:
            reader.backup(writer, pages=2048, sleep=0.01)
    destination.chmod(0o600)
    inspected = _inspect_database(destination)
    return {"name": destination.name, "size": destination.stat().st_size,
            "sha256": _sha256_file(destination), **inspected,
            "snapshotStartedAt": started, "snapshotFinishedAt": time.time()}


def _git_state(root):
    try:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, check=True,
                                capture_output=True, text=True, timeout=10).stdout.strip()
        dirty = bool(subprocess.run(["git", "status", "--porcelain=v1"], cwd=root, check=True,
                                    capture_output=True, text=True, timeout=10).stdout.strip())
        return {"commit": commit, "dirty": dirty}
    except (OSError, subprocess.SubprocessError):
        return {"commit": None, "dirty": None}


def create_backup(root, *, output=None, label=None, clock=time.time):
    root = Path(root).resolve()
    state = inventory(root)
    if not state["ready"]:
        raise ValueError("state_backup_inventory_not_ready")
    if label is not None and (not isinstance(label, str) or not LABEL.fullmatch(label)):
        raise ValueError("state_backup_label_invalid")
    stamp = float(clock())
    timestamp = datetime.fromtimestamp(stamp, timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    name = timestamp + ("-" + label if label else "")
    raw_destination = Path(output) if output is not None else root / "var/backups/state" / name
    if raw_destination.is_symlink():
        raise ValueError("state_backup_destination_invalid")
    destination = raw_destination.resolve()
    if destination.exists() or destination.is_symlink():
        raise ValueError("state_backup_destination_exists")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.parent / ("." + destination.name + ".tmp-" + uuid.uuid4().hex)
    temporary.mkdir(mode=0o700)
    try:
        databases = []
        for database in state["configured"]:
            databases.append(_snapshot_database(root / "var" / database, temporary / database))
        manifest = {
            "schema": MANIFEST_SCHEMA,
            "createdAt": stamp,
            "createdAtIso": datetime.fromtimestamp(stamp, timezone.utc).isoformat(),
            "source": {"project": "BDHub-Agent", **_git_state(root)},
            "policySha256": state["policySha256"],
            "databases": databases,
            "excluded": {
                "sqliteSidecars": ["*-wal", "*-shm"],
                "sensitiveConfig": ["config/typesafe.json", "config/kalodata-identity.json",
                                    "config/campaign-join.json", "vendor/config.yaml"],
                "operationalArtifacts": ["var/*.json", "var/*.log", "var/*.pid", "outputs/**"]
            },
            "restoreContract": {"emptyTargetOnly": True, "overwritesExistingState": False,
                                "credentialsIncluded": False,
                                "crossDatabaseAtomic": False}
        }
        manifest_path = temporary / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                                 encoding="utf-8")
        manifest_path.chmod(0o600)
        temporary.replace(destination)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return verify_backup(destination)


def _load_manifest(backup):
    backup = Path(backup)
    if backup.is_symlink() or not backup.is_dir():
        raise ValueError("state_backup_path_invalid")
    path = backup / "manifest.json"
    if path.is_symlink() or not path.is_file():
        raise ValueError("state_backup_manifest_invalid")
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError("state_backup_manifest_invalid") from error
    required = {"schema", "createdAt", "createdAtIso", "source", "policySha256", "databases",
                "excluded", "restoreContract"}
    if not isinstance(manifest, dict) or set(manifest) != required or manifest.get("schema") != MANIFEST_SCHEMA:
        raise ValueError("state_backup_manifest_invalid")
    if not isinstance(manifest["databases"], list) or not manifest["databases"]:
        raise ValueError("state_backup_manifest_invalid")
    source = manifest["source"]
    contract = manifest["restoreContract"]
    excluded = manifest["excluded"]
    commit = source.get("commit") if isinstance(source, dict) else False
    dirty = source.get("dirty") if isinstance(source, dict) else False
    if not isinstance(manifest["createdAt"], (int, float)) or isinstance(manifest["createdAt"], bool) \
            or not isinstance(manifest["createdAtIso"], str) \
            or not isinstance(manifest["policySha256"], str) \
            or not re.fullmatch(r"[0-9a-f]{64}", manifest["policySha256"]) \
            or not isinstance(source, dict) or set(source) != {"project", "commit", "dirty"} \
            or source["project"] != "BDHub-Agent" \
            or (commit is not None and (not isinstance(commit, str)
                                        or not re.fullmatch(r"[0-9a-f]{40,64}", commit))) \
            or (dirty is not None and type(dirty) is not bool) \
            or not isinstance(excluded, dict) or not isinstance(contract, dict) \
            or contract != {"emptyTargetOnly": True, "overwritesExistingState": False,
                            "credentialsIncluded": False, "crossDatabaseAtomic": False}:
        raise ValueError("state_backup_manifest_invalid")
    return manifest


def verify_backup(backup):
    raw_backup = Path(backup)
    if raw_backup.is_symlink():
        raise ValueError("state_backup_path_invalid")
    backup = raw_backup.resolve()
    manifest = _load_manifest(backup)
    names = []
    total = 0
    required_entry = {"name", "size", "sha256", "quickCheck", "pageCount", "pageSize",
                      "schemaVersion", "userVersion", "snapshotStartedAt", "snapshotFinishedAt"}
    for entry in manifest["databases"]:
        if not isinstance(entry, dict) or set(entry) != required_entry:
            raise ValueError("state_backup_manifest_invalid")
        name = _database_name(entry.get("name"))
        if name in names:
            raise ValueError("state_backup_manifest_invalid")
        path = backup / name
        if path.is_symlink() or not path.is_file() or type(entry.get("size")) is not int \
                or entry["size"] < 0 or path.stat().st_size != entry["size"]:
            raise ValueError("state_backup_file_invalid")
        if not isinstance(entry.get("sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]) \
                or _sha256_file(path) != entry["sha256"]:
            raise ValueError("state_backup_checksum_mismatch")
        if any(type(entry.get(key)) is not int or entry[key] < 0
               for key in ("pageCount", "pageSize", "schemaVersion", "userVersion")) \
                or any(not isinstance(entry.get(key), (int, float)) or isinstance(entry[key], bool)
                       for key in ("snapshotStartedAt", "snapshotFinishedAt")) \
                or entry.get("quickCheck") != "ok":
            raise ValueError("state_backup_manifest_invalid")
        inspected = _inspect_database(path)
        if any(inspected[key] != entry[key] for key in inspected):
            raise ValueError("state_backup_manifest_mismatch")
        names.append(name)
        total += entry["size"]
    actual = {path.name for path in backup.iterdir()}
    if actual != {"manifest.json", *names} or any(path.is_symlink() for path in backup.iterdir()):
        raise ValueError("state_backup_unexpected_file")
    return {"schema": MANIFEST_SCHEMA, "backup": str(backup), "valid": True,
            "createdAt": manifest["createdAt"], "gitCommit": manifest["source"].get("commit"),
            "gitDirty": manifest["source"].get("dirty"), "databases": len(names),
            "totalBytes": total, "credentialsIncluded": False}


def restore_backup(backup, target_var, *, confirmed=False, clock=time.time):
    if confirmed is not True:
        raise ValueError("state_restore_confirmation_required")
    raw_backup = Path(backup)
    raw_target = Path(target_var)
    if raw_backup.is_symlink() or raw_target.is_symlink():
        raise ValueError("state_restore_target_invalid")
    backup = raw_backup.resolve()
    verification = verify_backup(backup)
    manifest = _load_manifest(backup)
    target = raw_target.resolve()
    if target == backup or backup in target.parents:
        raise ValueError("state_restore_target_invalid")
    if target.exists() and (not target.is_dir() or any(target.iterdir())):
        raise ValueError("state_restore_target_not_empty")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.parent / ("." + target.name + ".restore-" + uuid.uuid4().hex)
    temporary.mkdir(mode=0o700)
    try:
        for entry in manifest["databases"]:
            source = backup / entry["name"]
            destination = temporary / entry["name"]
            shutil.copyfile(source, destination)
            destination.chmod(0o600)
            if _sha256_file(destination) != entry["sha256"]:
                raise ValueError("state_restore_checksum_mismatch")
            _inspect_database(destination)
        receipt = {"schema": RESTORE_SCHEMA, "restoredAt": float(clock()),
                   "sourceManifestSha256": _sha256_file(backup / "manifest.json"),
                   "sourceCreatedAt": manifest["createdAt"], "databases": len(manifest["databases"]),
                   "credentialsIncluded": False}
        receipt_path = temporary / ".bdhub-restore.json"
        receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                                encoding="utf-8")
        receipt_path.chmod(0o600)
        if target.exists():
            target.rmdir()
        temporary.replace(target)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return {"schema": RESTORE_SCHEMA, "targetVar": str(target), "restored": True,
            "databases": verification["databases"], "totalBytes": verification["totalBytes"],
            "credentialsIncluded": False}
