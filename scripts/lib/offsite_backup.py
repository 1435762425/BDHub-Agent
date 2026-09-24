"""Encrypted off-machine copy of the code, a fresh state snapshot and the local-only config.

Each run snapshots every policy database with SQLite's online backup API into a temporary
directory, streams one tar.gz through AES-256 (openssl, PBKDF2) straight onto the target volume,
flushes it to the device and decrypts it back to compare the plaintext hash and member list before
it writes the manifest that marks the copy complete.  The target keeps the newest KEEP_COPIES
complete copies inside its own folder; nothing else on the volume is touched.  The key file stays
on this machine next to the other local backups; keep a second copy in a password manager, because
the copies cannot be restored without it.  Account browser identities are never copied (a re-login
regenerates them).
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import tarfile
import tempfile
import time
import zlib

from lib.state_backup import create_backup, inventory

CIPHER = ("-aes-256-cbc", "-pbkdf2", "-iter", "200000")
SECRET_CONFIGS = ("config/kalodata-identity.json", "config/campaign-join.json", "config/typesafe.json")
FOLDER = "BDHub-Agent-offsite"
ARCHIVE = "backup.tar.gz.enc"
MANIFEST = "manifest.json"
KEEP_COPIES = 3
AUTO_MIN_AGE_HOURS = 20
STAGING_RESERVE_BYTES = 1 << 30
STAMP = re.compile(r"\d{8}T\d{6}Z")


class OffsiteError(ValueError):
    pass


def backups_home(root):
    root = Path(root).resolve()
    return root.parent / f"{root.name}-backups"


def key_path(root):
    return backups_home(root) / "offsite" / "offsite.key"


def ensure_key(root):
    """Create the random key file once (0600).  Returns (path, created)."""
    path = key_path(root)
    if path.exists():
        return path, False
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as out:
        out.write(secrets.token_hex(32) + "\n")
    return path, True


def _fingerprint(key):
    return hashlib.sha256(Path(key).read_bytes()).hexdigest()[:16]


@contextmanager
def _exclusive(root):
    path = backups_home(root) / "offsite" / ".lock"
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with open(path, "a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise OffsiteError("offsite_busy") from error
        yield


def _check_target(root, target):
    root = Path(root).resolve()
    target = Path(target).resolve()
    if not target.is_dir():
        raise OffsiteError("offsite_target_missing")
    for inside in (root, backups_home(root)):
        if target == inside or inside in target.parents:
            raise OffsiteError("offsite_target_not_offsite")
    return target


def _archives(root):
    home = backups_home(root)
    # Local git bundles are superseded by the fresh bundle inside each copy; the key never leaves.
    return sorted(path for path in home.iterdir() if path.is_dir() and path.name not in ("git", "offsite")) \
        if home.is_dir() else []


def _size(path):
    if path.is_file():
        return path.stat().st_size
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def copies(target):
    """Complete copies on the target, newest first; the manifest is written last, so it marks completion."""
    folder = Path(target) / FOLDER
    if not folder.is_dir():
        return []
    return sorted((path for path in folder.iterdir()
                   if path.is_dir() and STAMP.fullmatch(path.name) and (path / MANIFEST).is_file()),
                  key=lambda path: path.name, reverse=True)


def plan(root, target):
    root = Path(root).resolve()
    target = _check_target(root, target)
    state = inventory(root)
    if not state["ready"]:
        raise OffsiteError("offsite_state_inventory_not_ready")
    database_bytes = sum((root / "var" / name).stat().st_size for name in state["configured"])
    archives = _archives(root)
    configs = [root / name for name in SECRET_CONFIGS if (root / name).exists()]
    needed = database_bytes + sum(_size(path) for path in [*archives, *configs, root / ".git"])
    return {"target": str(target), "databases": len(state["configured"]), "databaseBytes": database_bytes,
            "archives": [path.name for path in archives], "configs": [str(path.relative_to(root)) for path in configs],
            "estimatedBytes": needed, "targetFreeBytes": shutil.disk_usage(target).free,
            "stagingFreeBytes": shutil.disk_usage(tempfile.gettempdir()).free,
            "copies": [path.name for path in copies(target)], "keyExists": key_path(root).exists()}


class _HashingWriter:
    def __init__(self, raw):
        self.raw, self.hash, self.bytes = raw, hashlib.sha256(), 0

    def write(self, data):
        self.hash.update(data)
        self.bytes += len(data)
        return self.raw.write(data)

    def flush(self):
        self.raw.flush()


class _HashingReader:
    def __init__(self, raw):
        self.raw, self.hash = raw, hashlib.sha256()

    def read(self, size=-1):
        data = self.raw.read(size)
        self.hash.update(data)
        return data


def _git(root, *args, runner=subprocess.run):
    return runner(["git", "-C", str(root), *args], capture_output=True, check=True)


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _flush_to_device(handle):
    handle.flush()
    os.fsync(handle.fileno())
    if hasattr(fcntl, "F_FULLFSYNC"):
        try:
            fcntl.fcntl(handle.fileno(), fcntl.F_FULLFSYNC)
        except OSError:
            pass  # not every file system forwards a cache flush; fsync above still ran


def _write_synced(path, text):
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
        _flush_to_device(handle)


def _decrypt(key, encrypted):
    """Decrypt and read the whole tar stream; returns (plaintext sha256, member names)."""
    with subprocess.Popen(["openssl", "enc", "-d", *CIPHER, "-pass", f"file:{key}", "-in", str(encrypted)],
                          stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as process:
        reader = _HashingReader(process.stdout)
        try:
            with tarfile.open(fileobj=reader, mode="r|gz") as tar:
                members = [member.name for member in tar]
            while reader.read(1 << 20):
                pass
        except (tarfile.TarError, zlib.error, EOFError, OSError) as error:
            raise OffsiteError("offsite_verify_failed") from error
    if process.returncode != 0:
        raise OffsiteError("offsite_verify_failed")
    return reader.hash.hexdigest(), members


def _write_copy(root, key, destination, current, now, stamp):
    encrypted = destination / ARCHIVE
    with tempfile.TemporaryDirectory(prefix="bdhub-offsite-") as temporary:
        staging = Path(temporary)
        snapshot = f"{stamp}-offsite"
        state = create_backup(root, output=staging / snapshot)
        bundle = staging / "code.bundle"
        _git(root, "bundle", "create", str(bundle), "--all")
        _git(root, "bundle", "verify", str(bundle))
        (staging / "uncommitted.patch").write_bytes(_git(root, "diff", "--binary").stdout)
        sources = [(bundle, "code/code.bundle"), (staging / "uncommitted.patch", "code/uncommitted.patch"),
                   (staging / snapshot, f"state/{snapshot}"),
                   *[(backups_home(root) / name, f"archives/{name}") for name in current["archives"]],
                   *[(root / name, name) for name in current["configs"]]]
        with open(encrypted, "wb") as out:
            with subprocess.Popen(["openssl", "enc", *CIPHER, "-salt", "-pass", f"file:{key}"],
                                  stdin=subprocess.PIPE, stdout=out) as process:
                writer = _HashingWriter(process.stdin)
                with tarfile.open(fileobj=writer, mode="w|gz", compresslevel=6) as tar:
                    for path, name in sources:
                        tar.add(path, arcname=name)
                process.stdin.close()
            if process.returncode != 0:
                raise OffsiteError("offsite_encrypt_failed")
            _flush_to_device(out)
    plain_sha256, members = _decrypt(key, encrypted)
    if plain_sha256 != writer.hash.hexdigest() or not {name for _, name in sources} <= set(members):
        raise OffsiteError("offsite_verify_failed")
    fingerprint = _fingerprint(key)
    manifest = {"schemaVersion": "bdhub.offsite-backup.v1", "createdAt": now,
                "createdAtIso": datetime.fromtimestamp(now, timezone.utc).isoformat(), "sourceRoot": str(root),
                "sourceCommit": _git(root, "rev-parse", "HEAD").stdout.decode().strip(),
                "stateSnapshot": snapshot, "databases": state["databases"], "databaseBytes": state["totalBytes"],
                "archives": current["archives"], "configs": current["configs"], "members": len(members),
                "plainBytes": writer.bytes, "plainSha256": plain_sha256, "encryptedBytes": encrypted.stat().st_size,
                "encryptedSha256": _sha256(encrypted), "cipher": " ".join(CIPHER), "keyFingerprint": fingerprint}
    _write_synced(destination / "README.txt",
                  "BDHub-Agent encrypted backup.  Put the key text (the password-manager copy of offsite.key)\n"
                  "into a file named offsite.key, then unpack into an empty directory:\n"
                  f"  openssl enc -d {' '.join(CIPHER)} -pass file:offsite.key -in {ARCHIVE} | tar -xzf - -C <dir>\n"
                  "Code:   git clone <dir>/code/code.bundle <repo>; git -C <repo> apply <dir>/code/uncommitted.patch\n"
                  "State:  scripts/state-backup.py restore --backup <dir>/state/<snapshot> --target-var <repo>/var"
                  " --confirmed\n"
                  "Config: copy <dir>/config/*.json into <repo>/config/.  Archives: scripts/state-retention.py restore.\n"
                  f"Key fingerprint (first 16 hex of sha256 of the key file): {fingerprint}\n")
    # The manifest goes last: its presence is what marks the copy complete.
    _write_synced(destination / MANIFEST, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    return manifest


def _prune(target, keep):
    """Drop complete copies beyond the newest ``keep`` and unfinished ones; leave anything not named like ours."""
    folder = Path(target) / FOLDER
    kept = {path.name for path in copies(target)[:keep]}
    removed = []
    for path in sorted(folder.iterdir()):
        if path.is_dir() and not path.is_symlink() and STAMP.fullmatch(path.name) and path.name not in kept:
            shutil.rmtree(path)
            removed.append(path.name)
    return removed


def run(root, target, *, now=None, keep=KEEP_COPIES):
    """Write one encrypted copy, verify it by decrypting it back, then keep the newest ``keep`` copies."""
    root = Path(root).resolve()
    with _exclusive(root):
        current = plan(root, target)
        if current["targetFreeBytes"] < current["estimatedBytes"]:
            raise OffsiteError("offsite_target_full")
        if current["stagingFreeBytes"] < current["databaseBytes"] + STAGING_RESERVE_BYTES:
            raise OffsiteError("offsite_staging_full")
        key, created = ensure_key(root)
        now = time.time() if now is None else now
        stamp = datetime.fromtimestamp(now, timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        destination = Path(current["target"]) / FOLDER / stamp
        destination.mkdir(parents=True, exist_ok=False)
        try:
            manifest = _write_copy(root, key, destination, current, now, stamp)
        except BaseException:
            shutil.rmtree(destination, ignore_errors=True)
            raise
        removed = _prune(current["target"], keep)
    return {"destination": str(destination), "keyPath": str(key), "keyCreated": created, "removedCopies": removed,
            **{name: manifest[name] for name in ("keyFingerprint", "stateSnapshot", "databases", "members",
                                                 "plainBytes", "encryptedBytes")}}


def verify(root, target, *, copy=None):
    """Re-read one copy (default: the newest) from the target and check it against its manifest."""
    root = Path(root).resolve()
    target = _check_target(root, target)
    chosen = next((path for path in copies(target) if copy is None or path.name == copy), None)
    if chosen is None:
        raise OffsiteError("offsite_copy_missing")
    manifest = json.loads((chosen / MANIFEST).read_text(encoding="utf-8"))
    key = key_path(root)
    if not key.exists() or _fingerprint(key) != manifest["keyFingerprint"]:
        raise OffsiteError("offsite_key_mismatch")
    encrypted = chosen / ARCHIVE
    if not encrypted.is_file() or _sha256(encrypted) != manifest["encryptedSha256"]:
        raise OffsiteError("offsite_verify_failed")
    plain_sha256, members = _decrypt(key, encrypted)
    if plain_sha256 != manifest["plainSha256"] or len(members) != manifest["members"]:
        raise OffsiteError("offsite_verify_failed")
    return {"copy": chosen.name, "createdAt": manifest["createdAt"], "members": len(members), "verified": True}


def auto(root, volumes=Path("/Volumes"), *, now=None, min_age_hours=AUTO_MIN_AGE_HOURS, notify=None):
    """On-mount entry point.  Only volumes that already hold our folder are used, so the first copy on a
    new disk is always a manual ``run``.  The newest copy is re-read from the device, and a new copy is
    written when that one is older than ``min_age_hours`` or fails the check.  ``notify(volume, event)``
    receives writing/written/checked/failed."""
    now = time.time() if now is None else now
    results = []
    for volume in sorted(Path(volumes).iterdir()):
        try:
            if not (volume / FOLDER).is_dir():
                continue
        except OSError:
            continue
        entry: dict = {"target": str(volume)}
        try:
            stale = True
            if copies(volume):
                try:
                    entry["verified"] = verify(root, volume)
                    stale = now - entry["verified"]["createdAt"] >= min_age_hours * 3600
                except (OSError, ValueError, KeyError, TypeError) as error:
                    entry["verifyError"] = str(error) or type(error).__name__
            if stale:
                if notify:
                    notify(volume.name, "writing")
                entry["written"] = run(root, volume, now=now)
        except (OSError, ValueError, subprocess.CalledProcessError) as error:
            entry["error"] = str(error) or type(error).__name__
        if notify:
            notify(volume.name, "failed" if "error" in entry else "written" if "written" in entry else "checked")
        results.append(entry)
    return results
