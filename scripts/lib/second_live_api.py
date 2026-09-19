"""Small fixed-scope launcher; HTTP-facing reads never initialize a trial store.

The only execution input is a confirmation of an already frozen snapshot. A
durable intent precedes process creation, so an interrupted launch cannot be
retried implicitly. Tests inject the CLI runner and process factory.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import stat
import subprocess
import threading
import time
import uuid

from lib.second_live_trial import LiveTrialError, LiveTrialStore

ROOT = Path(__file__).resolve().parents[2]
PYTHON = ROOT / ".venv/bin/python"
TRIAL_ID = re.compile(r"second_live_trial_[a-f0-9]{32}\Z")
HASH = re.compile(r"[a-f0-9]{64}\Z")
REQUEST_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,159}\Z")
CODE = re.compile(r"[a-z][a-z0-9_]{0,95}\Z")
MAX_INPUT = 4096
MAX_SCOPE = 65536
MAX_RECORD = 16384
FIELDS = {"show": {"trialId"}, "start": {"trialId", "snapshotHash", "confirmed", "requestId"}}
APPROVAL_FIELDS = {"senderBindingHash", "templateContextFingerprint", "cardBindingSha256", "opportunityFingerprint"}


def _hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _error(code, status=409):
    return LiveTrialError(code, status)


def _safe_code(value):
    return value if isinstance(value, str) and CODE.fullmatch(value) else None


def _iso(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # A PID we cannot inspect must never justify another spawn.


def _check_stat(info, *, directory=False, private=False):
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    if (not expected(info.st_mode) or info.st_uid != os.getuid()
            or info.st_mode & (0o077 if private else 0o022)
            or (not directory and info.st_nlink != 1)):
        raise _error("unsafe_local_file", 503)


def _read_json(path, maximum):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        raise _error("trial_scope_not_found", 404) from None
    except OSError:
        raise _error("unsafe_local_file", 503) from None
    try:
        info = os.fstat(fd)
        _check_stat(info)
        if info.st_size > maximum:
            raise _error("local_file_too_large", 503)
        with os.fdopen(fd, "rb", closefd=False) as stream:
            raw = stream.read(maximum + 1)
        if len(raw) > maximum:
            raise _error("local_file_too_large", 503)
        try:
            return json.loads(raw)
        except (ValueError, UnicodeError):
            raise _error("invalid_local_state", 503) from None
    finally:
        os.close(fd)


def _save(path, value):
    """Publish and fsync an intent before the caller may perform side effects."""
    temporary = path.with_name(path.name + ".tmp-" + uuid.uuid4().hex)
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, sort_keys=True, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)


class _ReadOnlyStore(LiveTrialStore):
    """Reuse read contracts without invoking the writable constructor or DDL."""
    def __init__(self, path, now):
        self.path, self.var_dir, self.now = path, path.parent, now
        self._lock = threading.RLock()
        self.db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True,
            timeout=3, isolation_level=None, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        try:
            self.db.execute("PRAGMA query_only=ON")
            if [row[0] for row in self.db.execute("SELECT version FROM live_trial_meta")] != [1]:
                raise _error("unsupported_schema", 503)
        except BaseException:
            self.db.close()
            raise


def public_trial(trial):
    """Explicitly omit credential provenance, local paths, receipts and SDK data."""
    snapshot = trial["snapshot"]
    items = []
    for item in trial["items"]:
        components = []
        for component in item["components"]:
            card = component.get("card") or {}
            components.append({"componentId": component["componentId"],
                "componentKind": component["componentKind"], "position": component["position"],
                "state": component["state"], "productId": card.get("product_id"),
                "listId": card.get("list_id"),
                "listName": card.get("list_name"), "campaignName": card.get("campaign_name"),
                "confirmed": component["state"] == "confirmed" and bool(component.get("confirmation")),
                "errorCode": _safe_code(component.get("errorCode"))})
        attempts = [{"attemptId": entry["attemptId"], "stage": entry["stage"], "state": entry["state"],
            "startedAt": entry["startedAt"], "finishedAt": entry["finishedAt"],
            "errorCode": _safe_code(entry.get("errorCode"))} for entry in item["attempts"]]
        item_fields = ("itemId", "opportunityId", "creatorId", "oecId", "handle", "draftId", "textIt",
            "translationZh", "textSha256", "state", "partialDelivery", "requiresReconciliation", "unknownStage")
        items.append({**{key: item[key] for key in item_fields}, "errorCode": _safe_code(item.get("errorCode")),
            "components": components, "attempts": attempts})
    fields = ("trialId", "snapshotHash", "expiresAt", "approved", "approvedAt", "paused", "expired", "complete", "blockedByUnknown")
    return {**{key: trial[key] for key in fields},
        **{key: snapshot[key] for key in ("market", "account", "campaignId", "createdAt")}, "items": items}


class SecondLiveAPI:
    def __init__(self, root=ROOT, *, runner=subprocess.run, popen=subprocess.Popen, alive=_alive, now=time.time):
        self.root = Path(root).resolve()
        self.var = self.root / "var"
        self.directory = self.var / "second-live"
        self.runner, self.popen, self.alive, self.now = runner, popen, alive, now

    def _time(self):
        value = self.now()
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            raise _error("clock_invalid", 503)
        return value

    def _paths(self, trial_id):
        if not isinstance(trial_id, str) or not TRIAL_ID.fullmatch(trial_id):
            raise _error("invalid_request", 400)
        for path in (self.root, self.var, self.directory):
            try:
                _check_stat(path.lstat(), directory=True)
            except FileNotFoundError:
                raise _error("trial_scope_not_found", 404) from None
        return (self.directory / (trial_id + ".json"), self.directory / (trial_id + ".launch.json"),
            self.directory / (trial_id + ".launch.lock"))

    def _load(self, trial_id):
        scope_path, _, _ = self._paths(trial_id)
        scope = _read_json(scope_path, MAX_SCOPE)
        if (not isinstance(scope, dict) or scope.get("schema") != "bdhub.second-live-scope.v1"
                or scope.get("trialId") != trial_id or not isinstance(scope.get("snapshotHash"), str)
                or not HASH.fullmatch(scope["snapshotHash"])):
            raise _error("scope_conflict")
        facts = scope.get("approvalFacts")
        if (not isinstance(facts, dict) or set(facts) != APPROVAL_FIELDS
                or any(not isinstance(value, str) or not HASH.fullmatch(value) for value in facts.values())):
            raise _error("scope_conflict")
        card_path = scope.get("cardFactsPath")
        if (not isinstance(card_path, str) or not card_path or len(card_path) > 512 or "\\" in card_path
                or Path(card_path).is_absolute() or ".." in Path(card_path).parts):
            raise _error("scope_conflict")
        path = self.var / "second-live-trials.sqlite"
        try:
            _check_stat(path.lstat())
            for suffix in ("-wal", "-shm"):
                companion = path.with_name(path.name + suffix)
                if os.path.lexists(companion):
                    _check_stat(companion.lstat())
            with _ReadOnlyStore(path, self.now) as store:
                # Bound malformed local snapshots before materializing the read model.
                row = store.db.execute("SELECT length(snapshot_json) FROM live_trial WHERE id=?", (trial_id,)).fetchone()
                if row is None:
                    raise _error("trial_not_found", 404)
                if row[0] > 131072:
                    raise _error("local_file_too_large", 503)
                trial = store.get_trial(trial_id)
        except FileNotFoundError:
            raise _error("trial_not_found", 404) from None
        except sqlite3.Error:
            raise _error("trial_store_unavailable", 503) from None
        snapshot = trial["snapshot"]
        if (scope["snapshotHash"] != trial["snapshotHash"] or _hash(snapshot) != trial["snapshotHash"]
                or _hash(facts) != snapshot.get("sourceFingerprint") or snapshot.get("trialId") != trial_id
                or snapshot.get("market") != "it" or snapshot.get("account") != "acc6"
                or snapshot.get("campaignId") != "italy-second-pilot"
                or not 1 <= len(trial["items"]) <= 3):
            raise _error("scope_conflict")
        return trial

    def _record(self, path, trial):
        if not os.path.lexists(path):
            return None
        value = _read_json(path, MAX_RECORD)
        if (not isinstance(value, dict) or value.get("schema") != "bdhub.second-live-launch.v1"
                or value.get("trialId") != trial["trialId"] or value.get("snapshotHash") != trial["snapshotHash"]
                or not isinstance(value.get("requestId"), str) or not REQUEST_ID.fullmatch(value["requestId"])
                or value.get("phase") not in ("approval_pending", "launch_intent", "launched", "not_started")
                or (value.get("pid") is not None and (type(value["pid"]) is not int or value["pid"] <= 1))):
            raise _error("invalid_launch_state", 503)
        return value

    def _launch(self, record, trial):
        if record is None:
            return {"state": "finished" if trial["complete"] else "not_started", "pid": None, "requestId": None}
        pid = record.get("pid")
        if trial["complete"]:
            state = "finished"
        elif record["phase"] == "not_started":
            state = "not_started"
        elif record["phase"] == "launched" and pid is not None:
            try:
                state = "running" if self.alive(pid) else "start_unknown"
            except Exception:
                state = "start_unknown"
        else:
            state = "start_unknown"
        return {"state": state, "pid": pid, "requestId": record["requestId"]}

    @contextmanager
    def _locked(self, path):
        try:
            fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        except OSError:
            raise _error("unsafe_local_file", 503) from None
        try:
            _check_stat(os.fstat(fd), private=True)
            # A second HTTP request must return promptly; its original request can be polled.
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise _error("launch_busy", 409) from None
            linked = path.lstat()
            held = os.fstat(fd)
            if (linked.st_dev, linked.st_ino) != (held.st_dev, held.st_ino):
                raise _error("unsafe_local_file", 503)
            yield
        finally:
            os.close(fd)

    def _guard(self, trial):
        if trial["complete"]:
            raise _error("trial_complete")
        if trial["expired"]:
            raise _error("trial_expired")
        if trial["paused"]:
            raise _error("trial_paused")
        if trial["blockedByUnknown"] or any(item["state"] == "result_unknown" for item in trial["items"]):
            raise _error("campaign_result_unknown")
        if any(item["requiresReconciliation"] for item in trial["items"]):
            raise _error("readback_required")
        if any(item["attempts"] or item["state"] != "pending" or item.get("leaseOwner") for item in trial["items"]):
            raise _error("trial_already_started")

    def _argv(self, command, trial):
        return [str(PYTHON), str(self.root / "scripts" / "italy-second-live.py"), command,
            "--trial-id", trial["trialId"]]

    def _approve(self, trial, env):
        try:
            result = self.runner(self._argv("approve", trial) + ["--expected-hash", trial["snapshotHash"]],
                cwd=str(self.root), env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, timeout=15, check=False, shell=False)
        except subprocess.TimeoutExpired:
            raise _error("approval_result_unknown", 503) from None
        except OSError:
            raise _error("approval_not_started", 503) from None
        raw = result.stdout
        if not isinstance(raw, (str, bytes)) or len(raw) > MAX_SCOPE:
            raise _error("approval_result_unknown", 503)
        try:
            value = json.loads(raw)
        except (ValueError, UnicodeError):
            raise _error("approval_result_unknown", 503) from None
        if result.returncode != 0:
            code = _safe_code(value.get("error")) if isinstance(value, dict) else None
            raise _error(code or "approval_failed")
        if (not isinstance(value, dict) or value.get("trialId") != trial["trialId"]
                or value.get("snapshotHash") != trial["snapshotHash"] or value.get("approved") is not True):
            raise _error("approval_result_unknown", 503)

    def dispatch(self, command, value):
        if command not in FIELDS or not isinstance(value, dict) or set(value) != FIELDS[command]:
            raise _error("invalid_request", 400)
        trial_id = value["trialId"]
        _, record_path, lock_path = self._paths(trial_id)
        if command == "show":
            trial = self._load(trial_id)
            return {"trial": public_trial(trial), "launch": self._launch(self._record(record_path, trial), trial)}
        if (value["confirmed"] is not True or not isinstance(value["snapshotHash"], str)
                or not HASH.fullmatch(value["snapshotHash"]) or not isinstance(value["requestId"], str)
                or not REQUEST_ID.fullmatch(value["requestId"])):
            raise _error("invalid_request", 400)
        with self._locked(lock_path):
            trial = self._load(trial_id)
            if trial["snapshotHash"] != value["snapshotHash"]:
                raise _error("snapshot_mismatch")
            record = self._record(record_path, trial)
            if record is not None:
                if record["requestId"] != value["requestId"]:
                    raise _error("request_conflict")
                return {"trial": public_trial(trial), "launch": self._launch(record, trial)}
            self._guard(trial)
            record = {"schema": "bdhub.second-live-launch.v1", "trialId": trial_id,
                "snapshotHash": trial["snapshotHash"], "requestId": value["requestId"],
                "phase": "approval_pending", "pid": None, "createdAt": _iso(self._time())}
            _save(record_path, record)
            env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
            try:
                if not trial["approved"]:
                    self._approve(trial, env)
                trial = self._load(trial_id)
                self._guard(trial)
                if not trial["approved"]:
                    raise _error("approval_not_applied", 503)
            except LiveTrialError as error:
                # No process creation has been attempted in this phase.
                if error.code != "approval_result_unknown":
                    record["phase"] = "not_started"
                    _save(record_path, record)
                raise
            record["phase"] = "launch_intent"
            _save(record_path, record)
            log_path = self.directory / (trial_id + ".launch.log")
            try:
                fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            except OSError:
                record["phase"] = "not_started"
                _save(record_path, record)
                raise _error("launch_log_unavailable", 503) from None
            try:
                with os.fdopen(fd, "ab", buffering=0) as log:
                    try:
                        process = self.popen(self._argv("run", trial), cwd=str(self.root), env=env,
                            stdin=subprocess.DEVNULL, stdout=log, stderr=log, shell=False,
                            start_new_session=True, close_fds=True)
                    except OSError:
                        record["phase"] = "not_started"
                        _save(record_path, record)
                        return {"trial": public_trial(trial), "launch": self._launch(record, trial)}
                if type(process.pid) is not int or process.pid <= 1:
                    raise ValueError("invalid_child_pid")
                record.update(phase="launched", pid=process.pid, launchedAt=_iso(self._time()))
                _save(record_path, record)
                return {"trial": public_trial(trial), "launch": {"state": "started", "pid": process.pid,
                    "requestId": record["requestId"]}}
            except BaseException:
                # The persisted launch_intent survives a crash after spawn but before PID publication.
                # Never terminate or replace a potentially sending process to recover the UI call.
                return {"trial": public_trial(trial), "launch": {"state": "start_unknown",
                    "pid": record.get("pid"), "requestId": record["requestId"]}}
