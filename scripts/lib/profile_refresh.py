"""Persistent single-creator profile refresh jobs; no messaging capabilities."""
from __future__ import annotations

from contextlib import closing, contextmanager
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import re
import signal
import sqlite3
import subprocess
import threading
import time
import uuid

from lib.creator_identity import CreatorIdentityStore

ROOT = Path(__file__).resolve().parents[2]
VAR = ROOT / "var"
LEGACY_PYTHON = ROOT.parent / "01-BDSystem-V2/.venv/bin/python"
LEASE_SECONDS = 240
SAFE_ERRORS = frozenset({
    "identity_not_found", "identity_mismatch", "unsupported_market", "idempotency_conflict",
    "invalid_request", "job_not_found", "probe_timeout", "probe_failed", "probe_report_missing",
    "probe_report_invalid", "identity_import_failed", "lease_expired_no_report",
    "attempt_exists_without_final", "worker_interrupted", "runtime_unavailable",
    "account_not_startable", "account_not_prepared", "maintenance_due", "shared_backoff",
    "account_maintenance_or_shared_backoff", "identity_changed_before_guard", "verification_required",
    "remote_error", "request_or_signer_error", "probe_initialization_or_validation_error",
    "stale_lease", "profile_identity_mismatch", "profile_market_mismatch", "supplement_identity_mismatch",
    "supplement_market_mismatch", "merged_identity_not_confirmed", "internal_error",
})


class ProfileRefreshError(ValueError):
    def __init__(self, code, status=400):
        self.code = code if code in SAFE_ERRORS else "internal_error"
        self.status = status
        super().__init__(self.code)


def _iso(seconds):
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _token(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", value):
        raise ProfileRefreshError("invalid_request")
    return value


def _alive(pid):
    if type(pid) is not int or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


_SCHEMA = """
CREATE TABLE IF NOT EXISTS profile_refresh_meta(version INTEGER PRIMARY KEY CHECK(version=1));
INSERT OR IGNORE INTO profile_refresh_meta VALUES(1);
CREATE TABLE IF NOT EXISTS profile_refresh_job(
    id TEXT PRIMARY KEY, creator_id TEXT NOT NULL, market TEXT NOT NULL, oec_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('queued','running','completed','blocked')),
    created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT, error_code TEXT,
    identity_updated INTEGER NOT NULL DEFAULT 0, request_count INTEGER DEFAULT 0,
    lease_owner TEXT, lease_pid INTEGER, lease_until REAL
);
CREATE UNIQUE INDEX IF NOT EXISTS profile_refresh_active_creator ON profile_refresh_job(creator_id)
WHERE status IN ('queued','running');
CREATE INDEX IF NOT EXISTS profile_refresh_queue ON profile_refresh_job(status,created_at);
CREATE TABLE IF NOT EXISTS profile_refresh_request(
    request_id TEXT PRIMARY KEY, creator_id TEXT NOT NULL,
    job_id TEXT NOT NULL REFERENCES profile_refresh_job(id)
);
CREATE TABLE IF NOT EXISTS profile_refresh_heartbeat(owner TEXT PRIMARY KEY,pid INTEGER NOT NULL,seen_at REAL NOT NULL);
"""


class ProfileRefreshStore:
    def __init__(self, var_dir=VAR, *, now=time.time):
        self.var_dir = Path(var_dir).resolve()
        self.var_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.var_dir / "creator-profile-refresh.sqlite"
        self.identity_path = self.var_dir / "creator-identities.sqlite"
        self.output_root = self.var_dir / "creator-profile-refresh"
        self.now = now
        self._lock = threading.RLock()
        self._db = sqlite3.connect(self.path, timeout=10, isolation_level=None, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        try:
            tables = {row[0] for row in self._db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if tables and "profile_refresh_meta" not in tables:
                raise ProfileRefreshError("internal_error", 500)
            if tables and [row[0] for row in self._db.execute("SELECT version FROM profile_refresh_meta")] != [1]:
                raise ProfileRefreshError("internal_error", 500)
            self._db.execute("PRAGMA foreign_keys=ON")
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.executescript("BEGIN IMMEDIATE;\n" + _SCHEMA + "\nCOMMIT;")
            self.path.chmod(0o600)
        except BaseException:
            self._db.close()
            raise

    def close(self):
        with self._lock:
            self._db.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    @contextmanager
    def _transaction(self):
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                yield
                self._db.execute("COMMIT")
            except BaseException:
                self._db.execute("ROLLBACK")
                raise

    def canonical(self, creator_id):
        creator_id = _token(creator_id)
        if not self.identity_path.is_file():
            raise ProfileRefreshError("identity_not_found", 404)
        with closing(sqlite3.connect(self.identity_path.as_uri() + "?mode=ro", uri=True, timeout=10)) as database:
            database.row_factory = sqlite3.Row
            row = database.execute("SELECT creator_id,market,oec_id,current_handle FROM creator_identity WHERE creator_id=?", (creator_id,)).fetchone()
        if row is None:
            raise ProfileRefreshError("identity_not_found", 404)
        if row["market"] != "it":
            raise ProfileRefreshError("unsupported_market", 409)
        if not isinstance(row["oec_id"], str) or not re.fullmatch(r"[0-9]{1,40}", row["oec_id"]):
            raise ProfileRefreshError("identity_mismatch", 409)
        return dict(row)

    @staticmethod
    def _public(row):
        return {"id": row["id"], "creatorId": row["creator_id"], "market": row["market"], "oecId": row["oec_id"],
                "status": row["status"], "createdAt": row["created_at"], "startedAt": row["started_at"],
                "finishedAt": row["finished_at"], "errorCode": row["error_code"],
                "identityUpdated": bool(row["identity_updated"]), "requestCount": row["request_count"]}

    def _row(self, job_id):
        row = self._db.execute("SELECT * FROM profile_refresh_job WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise ProfileRefreshError("job_not_found", 404)
        return dict(row)

    def status(self, job_id):
        job_id = _token(job_id)
        with self._lock:
            return {**self._public(self._row(job_id)), "workerOnline": self.worker_online()}

    def list_jobs(self, creator_id):
        self.canonical(creator_id)
        with self._lock:
            rows = self._db.execute("SELECT * FROM profile_refresh_job WHERE creator_id=? ORDER BY created_at DESC,id DESC LIMIT 10", (creator_id,)).fetchall()
            return {"jobs": [self._public(row) for row in rows], "workerOnline": self.worker_online()}

    def heartbeat(self, owner):
        with self._transaction():
            self._db.execute("INSERT INTO profile_refresh_heartbeat VALUES(?,?,?) ON CONFLICT(owner) DO UPDATE SET pid=excluded.pid,seen_at=excluded.seen_at",
                             (owner, os.getpid(), self.now()))

    def remove_heartbeat(self, owner):
        with self._transaction():
            self._db.execute("DELETE FROM profile_refresh_heartbeat WHERE owner=?", (owner,))

    def worker_online(self):
        with self._lock:
            return any(_alive(row[0]) for row in self._db.execute(
                "SELECT pid FROM profile_refresh_heartbeat WHERE seen_at>=?", (self.now() - 30,)))

    @contextmanager
    def hold_lease(self, job_id, owner):
        # Hold the queue's write transaction during import so another worker
        # cannot recover this lease between the ownership check and the write.
        with self._transaction():
            row = self._row(job_id)
            if row["status"] != "running" or row["lease_owner"] != owner or row["lease_until"] <= self.now():
                raise ProfileRefreshError("stale_lease", 409)
            self._db.execute("UPDATE profile_refresh_job SET lease_until=? WHERE id=?", (self.now() + LEASE_SECONDS, job_id))
            yield

    def enqueue(self, creator_id, request_id):
        creator_id, request_id = _token(creator_id), _token(request_id)
        with self._transaction():
            prior = self._db.execute("SELECT * FROM profile_refresh_request WHERE request_id=?", (request_id,)).fetchone()
            if prior is not None:
                if prior["creator_id"] != creator_id:
                    raise ProfileRefreshError("idempotency_conflict", 409)
                return self._public(self._row(prior["job_id"]))
            current = self.canonical(creator_id)
            active = self._db.execute("SELECT * FROM profile_refresh_job WHERE creator_id=? AND status IN ('queued','running')", (creator_id,)).fetchone()
            if active is None:
                job_id = "refresh_" + uuid.uuid4().hex
                self._db.execute("INSERT INTO profile_refresh_job(id,creator_id,market,oec_id,status,created_at) VALUES(?,?,?,?,?,?)",
                                 (job_id, creator_id, current["market"], current["oec_id"], "queued", _iso(self.now())))
            else:
                job_id = active["id"]
            self._db.execute("INSERT INTO profile_refresh_request VALUES(?,?,?)", (request_id, creator_id, job_id))
            return self._public(self._row(job_id))

    def claim(self, owner):
        with self._transaction():
            row = self._db.execute("SELECT id FROM profile_refresh_job WHERE status='queued' ORDER BY created_at,id LIMIT 1").fetchone()
            if row is None:
                return None
            self._db.execute("UPDATE profile_refresh_job SET status='running',started_at=?,request_count=NULL,lease_owner=?,lease_pid=?,lease_until=? WHERE id=? AND status='queued'",
                             (_iso(self.now()), owner, os.getpid(), self.now() + LEASE_SECONDS, row["id"]))
            return self._row(row["id"])

    def claim_recovery(self, owner):
        with self._transaction():
            rows = self._db.execute("SELECT * FROM profile_refresh_job WHERE status='running' ORDER BY started_at,id").fetchall()
            for row in rows:
                if row["lease_until"] is not None and row["lease_until"] > self.now() and _alive(row["lease_pid"]):
                    continue
                self._db.execute("UPDATE profile_refresh_job SET lease_owner=?,lease_pid=?,lease_until=? WHERE id=?",
                                 (owner, os.getpid(), self.now() + LEASE_SECONDS, row["id"]))
                return self._row(row["id"])
            return None

    def finish(self, job_id, owner, *, error=None, identity_updated=False, request_count=None):
        if error is not None and error not in SAFE_ERRORS:
            error = "internal_error"
        with self._transaction():
            row = self._row(job_id)
            if row["status"] != "running" or row["lease_owner"] != owner:
                return self._public(row)
            self._db.execute("""UPDATE profile_refresh_job SET status=?,finished_at=?,error_code=?,identity_updated=?,request_count=?,
                lease_owner=NULL,lease_pid=NULL,lease_until=NULL WHERE id=?""",
                ("blocked" if error else "completed", _iso(self.now()), error, int(identity_updated), request_count, job_id))
            return self._public(self._row(job_id))


def _import_reports(store, reports):
    spec = importlib.util.spec_from_file_location("refresh_identity_import", ROOT / "scripts/import-creator-identities.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.import_reports(store, reports)


def _execute_probe(target_file, output):
    if not LEGACY_PYTHON.is_file():
        raise ProfileRefreshError("runtime_unavailable", 503)
    # These paths are constructed from job IDs under our fixed var directory,
    # never from a request payload or an environment-selected script path.
    command = [str(LEGACY_PYTHON), str(ROOT / "scripts/probe-italy-profile.py"),
               "--account", "acc6", "--targets", str(target_file), "--output", str(output)]
    process = subprocess.Popen(command, cwd=ROOT, start_new_session=True, stdin=subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    try:
        try:
            # The reused probe supervises its network child for 180 seconds;
            # this outer bound leaves time for its group/SDK cleanup.
            return process.wait(timeout=190)
        except subprocess.TimeoutExpired as error:
            raise ProfileRefreshError("probe_timeout", 503) from error
    finally:
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()


class ProfileRefreshWorker:
    def __init__(self, store, *, executor=None, importer=None):
        self.store = store
        self.executor = executor or _execute_probe
        self.importer = importer or _import_reports
        self.owner = "worker_" + uuid.uuid4().hex
        self._heartbeat_stop = threading.Event()
        self._heartbeat_thread = None

    def __enter__(self):
        self.store.heartbeat(self.owner)
        def beat():
            while not self._heartbeat_stop.wait(10):
                try:
                    self.store.heartbeat(self.owner)
                except Exception:
                    pass
        self._heartbeat_thread = threading.Thread(target=beat, name="profile-refresh-heartbeat", daemon=True)
        self._heartbeat_thread.start()
        return self

    def __exit__(self, *_):
        self._heartbeat_stop.set()
        if self._heartbeat_thread is not None:
            self._heartbeat_thread.join(timeout=1)
        self.store.remove_heartbeat(self.owner)

    def _paths(self, job):
        if not re.fullmatch(r"refresh_[0-9a-f]{32}", job["id"]):
            raise ProfileRefreshError("invalid_request")
        directory = self.store.output_root / job["id"]
        output, target = directory / "attempt-1", directory / "targets.private.json"
        if any(not path.resolve().is_relative_to(self.store.var_dir) for path in (directory, output, target, output / "report.private.json")):
            raise ProfileRefreshError("invalid_request")
        return directory, output, target

    def _identity(self, job):
        current = self.store.canonical(job["creator_id"])
        if (current["market"], current["oec_id"]) != (job["market"], job["oec_id"]):
            raise ProfileRefreshError("identity_mismatch", 409)
        return current

    @staticmethod
    def _read_final(path):
        if not path.is_file():
            return None
        if path.stat().st_size > 4 * 1024 * 1024:
            raise ProfileRefreshError("probe_report_invalid")
        try:
            report = json.loads(path.read_text())
        except (ValueError, UnicodeError) as error:
            raise ProfileRefreshError("probe_report_invalid") from error
        if not isinstance(report, dict):
            raise ProfileRefreshError("probe_report_invalid")
        if not isinstance(report.get("status"), str):
            raise ProfileRefreshError("probe_report_invalid")
        if not report.get("finishedAt") or report.get("status") not in {"completed", "blocked", "bounded_timeout"}:
            return None
        try:
            finished = datetime.fromisoformat(report["finishedAt"].replace("Z", "+00:00"))
            if finished.tzinfo is None:
                raise ValueError()
        except (TypeError, AttributeError, ValueError) as error:
            raise ProfileRefreshError("probe_report_invalid") from error
        return report

    def _block(self, job, code, *, request_count=None):
        code = code if code in SAFE_ERRORS else "probe_failed"
        try:
            with self.store.hold_lease(job["id"], self.owner):
                self._identity(job)
                with CreatorIdentityStore(self.store.identity_path) as identities:
                    identities.record_profile_failure(job["market"], job["oec_id"], job["started_at"],
                        f"profile-refresh:{job['id']}:failure", "timeout" if code == "probe_timeout" else "unknown")
        except ProfileRefreshError as error:
            if error.code == "stale_lease":
                return self.store.status(job["id"])
        except Exception:
            # A failure to record a diagnostic does not delete/replace identity
            # or turn an interrupted network operation back into queued work.
            pass
        return self.store.finish(job["id"], self.owner, error=code, request_count=request_count)

    def _settle(self, job, report):
        count = report.get("counters", {}).get("request_count") if isinstance(report.get("counters"), dict) else report.get("businessRequests")
        count = count if type(count) is int and count >= 0 else None
        if report.get("status") != "completed":
            reason = "probe_timeout" if report.get("status") == "bounded_timeout" else report.get("reason")
            return self._block(job, reason if isinstance(reason, str) and reason in SAFE_ERRORS else "probe_failed", request_count=count)
        try:
            self._identity(job)
            targets, requests = report.get("targets"), report.get("requests")
            if report.get("schema") != "bdhub.italy-profile-probe.v3" or report.get("market") != job["market"] or \
                    report.get("account") != "acc6" or report.get("identityFileUnchanged") is not True or \
                    type(report.get("oldDatabaseWrites")) is not int or report["oldDatabaseWrites"] != 0 or \
                    type(report.get("realSends")) is not int or report["realSends"] != 0 or \
                    not isinstance(targets, list) or len(targets) != 1 or not isinstance(requests, list) or not requests:
                raise ProfileRefreshError("probe_report_invalid")
            target = targets[0]
            if target.get("targetRef") != job["id"] or target.get("externalId") != job["creator_id"] or \
                    target.get("inputKind") != "known_oec" or target.get("requestedOecId") != job["oec_id"] or \
                    target.get("requestedHandle") is not None or target.get("status") != "completed" or \
                    target.get("oecId") != job["oec_id"] or any(
                        row.get("targetRef") != job["id"] or row.get("stage") != "profile" or
                        row.get("status") != "returned" or row.get("httpStatus") != 200 or
                        row.get("code") != "0" or row.get("verificationRequired") is not False for row in requests):
                raise ProfileRefreshError("probe_report_invalid")
            with self.store.hold_lease(job["id"], self.owner):
                self._identity(job)
                with CreatorIdentityStore(self.store.identity_path) as identities:
                    self.importer(identities, [report])
                    current = identities.get_by_oec(job["market"], job["oec_id"])
                    if current is None or current["creatorId"] != job["creator_id"]:
                        raise ProfileRefreshError("identity_mismatch", 409)
            return self.store.finish(job["id"], self.owner, identity_updated=True, request_count=count)
        except ProfileRefreshError as error:
            return self._block(job, error.code, request_count=count)
        except Exception:
            return self._block(job, "identity_import_failed", request_count=count)

    def run_once(self):
        self.store.heartbeat(self.owner)
        recovery = self.store.claim_recovery(self.owner)
        if recovery is not None:
            try:
                _, output, _ = self._paths(recovery)
                report = self._read_final(output / "report.private.json")
                return self._settle(recovery, report) if report else self._block(recovery, "lease_expired_no_report")
            except ProfileRefreshError as error:
                return self._block(recovery, error.code)
            except Exception:
                return self._block(recovery, "probe_report_invalid")
        job = self.store.claim(self.owner)
        if job is None:
            return None
        try:
            current = self._identity(job)
            directory, output, target_file = self._paths(job)
            report = self._read_final(output / "report.private.json")
            if report is not None:
                return self._settle(job, report)
            if output.exists() or target_file.exists():
                return self._block(job, "attempt_exists_without_final")
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            target = {"ref": job["id"], "oecId": job["oec_id"], "externalId": job["creator_id"]}
            if current["current_handle"]:
                target["handle"] = current["current_handle"]
            with target_file.open("x", encoding="utf-8") as stream:
                target_file.chmod(0o600)
                json.dump({"market": job["market"], "targets": [target]}, stream)
            returncode = self.executor(target_file, output)
            report = self._read_final(output / "report.private.json")
            if report is not None:
                return self._settle(job, report)
            return self._block(job, "probe_failed" if returncode else "probe_report_missing")
        except ProfileRefreshError as error:
            return self._block(job, error.code)
        except (KeyboardInterrupt, SystemExit):
            self._block(job, "worker_interrupted")
            raise
        except Exception:
            return self._block(job, "probe_failed")
