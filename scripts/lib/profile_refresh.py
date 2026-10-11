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
from lib.creator_core_profile import CORE_FIELDS, core_read_route, core_summary, find_core_observations, _observed_at
from lib.profile_completion import merge_profile_summaries

from lib.im_session_owner import identity_account as _identity_account

ROOT = Path(__file__).resolve().parents[2]
VAR = ROOT / "var"
PROJECT_PYTHON = ROOT / ".venv/bin/python"
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
    "identity_policy_unreadable", "published_identity_policy_invalid",
    "market_mismatch", "current_handle_required", "account_busy", "resources_busy", "resource_lease_lost",
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
    def __init__(self, var_dir=VAR, *, now=time.time, root=ROOT):
        self.var_dir = Path(var_dir).resolve()
        self.root = Path(root).resolve()
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
            # Additive upgrade; old IT requests and attempts remain valid.
            with self._transaction():
                columns = {r[1] for r in self._db.execute("PRAGMA table_info(profile_refresh_job)")}
                for name, declaration in (("account", "TEXT"), ("requested_handle", "TEXT"), ("read_route", "TEXT"),
                                          ("attempt_no", "INTEGER NOT NULL DEFAULT 1"), ("retry_at", "REAL NOT NULL DEFAULT 0")):
                    if name not in columns:
                        self._db.execute(f"ALTER TABLE profile_refresh_job ADD COLUMN {name} {declaration}")
                if "market" not in {r[1] for r in self._db.execute("PRAGMA table_info(profile_refresh_heartbeat)")}:
                    self._db.execute("ALTER TABLE profile_refresh_heartbeat ADD COLUMN market TEXT")
                if "accepting" not in {r[1] for r in self._db.execute("PRAGMA table_info(profile_refresh_heartbeat)")}:
                    self._db.execute("ALTER TABLE profile_refresh_heartbeat ADD COLUMN accepting INTEGER NOT NULL DEFAULT 1")
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

    def canonical(self, creator_id, expected_market=None):
        creator_id = _token(creator_id)
        if not self.identity_path.is_file():
            raise ProfileRefreshError("identity_not_found", 404)
        with closing(sqlite3.connect(self.identity_path.as_uri() + "?mode=ro", uri=True, timeout=10)) as database:
            database.row_factory = sqlite3.Row
            row = database.execute("SELECT creator_id,market,oec_id,current_handle FROM creator_identity WHERE creator_id=?", (creator_id,)).fetchone()
        if row is None:
            raise ProfileRefreshError("identity_not_found", 404)
        if row["market"] not in {"it", "br", "my", "th", "uk", "de", "fr"}:
            raise ProfileRefreshError("unsupported_market", 409)
        if expected_market is not None and row["market"] != expected_market:
            raise ProfileRefreshError("market_mismatch", 409)
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

    def status(self, job_id, expected_market=None):
        job_id = _token(job_id)
        with self._lock:
            row = self._row(job_id)
            if expected_market is not None and row["market"] != expected_market:
                raise ProfileRefreshError("market_mismatch", 409)
            return {**self._public(row), "workerOnline": self.worker_online(row["market"])}

    def list_jobs(self, creator_id, expected_market=None):
        current = self.canonical(creator_id, expected_market)
        with self._lock:
            rows = self._db.execute("SELECT * FROM profile_refresh_job WHERE creator_id=? ORDER BY created_at DESC,id DESC LIMIT 10", (creator_id,)).fetchall()
            return {"jobs": [self._public(row) for row in rows], "workerOnline": self.worker_online(current["market"])}

    def heartbeat(self, owner, market=None):
        with self._transaction():
            self._db.execute("INSERT INTO profile_refresh_heartbeat(owner,pid,seen_at,market) VALUES(?,?,?,?) ON CONFLICT(owner) DO UPDATE SET pid=excluded.pid,seen_at=excluded.seen_at,market=excluded.market",
                             (owner, os.getpid(), self.now(), market))

    def remove_heartbeat(self, owner):
        with self._transaction():
            self._db.execute("DELETE FROM profile_refresh_heartbeat WHERE owner=?", (owner,))

    def worker_online(self, market=None):
        with self._lock:
            return any(_alive(row[0]) for row in self._db.execute(
                "SELECT pid FROM profile_refresh_heartbeat WHERE accepting=1 AND seen_at>=? AND (? IS NULL OR market IS NULL OR market=?)",
                (self.now() - 30, market, market)))

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

    def enqueue(self, creator_id, request_id, expected_market=None):
        creator_id, request_id = _token(creator_id), _token(request_id)
        with self._transaction():
            prior = self._db.execute("SELECT * FROM profile_refresh_request WHERE request_id=?", (request_id,)).fetchone()
            if prior is not None:
                if prior["creator_id"] != creator_id:
                    raise ProfileRefreshError("idempotency_conflict", 409)
                if expected_market is not None and self._row(prior["job_id"])["market"] != expected_market:
                    raise ProfileRefreshError("market_mismatch", 409)
                return self._public(self._row(prior["job_id"]))
            current = self.canonical(creator_id, expected_market)
            active = self._db.execute("SELECT * FROM profile_refresh_job WHERE creator_id=? AND status IN ('queued','running')", (creator_id,)).fetchone()
            if active is None:
                job_id = "refresh_" + uuid.uuid4().hex
                route = core_read_route(current["market"])
                account = _identity_account(self.root, current["market"])
                if route == "find" and not current["current_handle"]:
                    raise ProfileRefreshError("current_handle_required", 409)
                self._db.execute("INSERT INTO profile_refresh_job(id,creator_id,market,oec_id,status,created_at,account,requested_handle,read_route) VALUES(?,?,?,?,?,?,?,?,?)",
                                 (job_id, creator_id, current["market"], current["oec_id"], "queued", _iso(self.now()), account,
                                  current["current_handle"], route))
            else:
                job_id = active["id"]
            self._db.execute("INSERT INTO profile_refresh_request VALUES(?,?,?)", (request_id, creator_id, job_id))
            return self._public(self._row(job_id))

    def claim(self, owner, market=None):
        with self._transaction():
            row = self._db.execute("SELECT id FROM profile_refresh_job WHERE status='queued' AND retry_at<=? AND (? IS NULL OR market=?) ORDER BY created_at,id LIMIT 1",
                                   (self.now(), market, market)).fetchone()
            if row is None:
                return None
            self._db.execute("UPDATE profile_refresh_job SET status='running',started_at=?,request_count=NULL,lease_owner=?,lease_pid=?,lease_until=? WHERE id=? AND status='queued'",
                             (_iso(self.now()), owner, os.getpid(), self.now() + LEASE_SECONDS, row["id"]))
            return self._row(row["id"])

    def claim_recovery(self, owner, market=None):
        with self._transaction():
            rows = self._db.execute("SELECT * FROM profile_refresh_job WHERE status='running' AND (? IS NULL OR market=?) ORDER BY started_at,id", (market, market)).fetchall()
            for row in rows:
                if row["lease_until"] is not None and row["lease_until"] > self.now() and _alive(row["lease_pid"]):
                    continue
                self._db.execute("UPDATE profile_refresh_job SET lease_owner=?,lease_pid=?,lease_until=? WHERE id=?",
                                 (owner, os.getpid(), self.now() + LEASE_SECONDS, row["id"]))
                return self._row(row["id"])
            return None

    def has_pending(self, market=None):
        with self._lock:
            return bool(self._db.execute("SELECT 1 FROM profile_refresh_job WHERE status IN ('queued','running') AND (? IS NULL OR market=?) LIMIT 1", (market, market)).fetchone())

    def begin_drain(self, owner, market):
        # Enqueue commits under this same transaction lock. Once a worker stops
        # accepting, the next enqueue launches a successor instead of trusting
        # a PID that is already exiting.
        with self._transaction():
            if self._db.execute("SELECT 1 FROM profile_refresh_job WHERE status IN ('queued','running') AND market=? LIMIT 1", (market,)).fetchone():
                return False
            self._db.execute("UPDATE profile_refresh_heartbeat SET accepting=0 WHERE owner=?", (owner,))
            return True

    def due_candidates(self, market, *, max_age_hours=168, limit=3):
        """A bounded refresh scope; unauthorized fields wait for a permission change."""
        if market not in {"it", "br", "my", "th", "uk", "de", "fr"} or \
                type(max_age_hours) is not int or not 1 <= max_age_hours <= 720 or type(limit) is not int or not 1 <= limit <= 50:
            raise ProfileRefreshError("invalid_request")
        with closing(sqlite3.connect(self.identity_path.as_uri() + "?mode=ro", uri=True)) as database:
            database.row_factory = sqlite3.Row
            database.execute("BEGIN")
            creators = [dict(r) for r in database.execute("SELECT creator_id,oec_id,current_handle FROM creator_identity WHERE market=?", (market,))]
            latest = {}
            for row in database.execute("""SELECT creator_id,observed_us,payload_json FROM identity_observation
                WHERE market=? AND kind='profile' AND outcome='observed' AND json_valid(payload_json)
                  AND json_type(payload_json,'$.fields')='object'
                ORDER BY observed_us DESC,CASE WHEN evidence_ref LIKE '%:profile' THEN 2 WHEN evidence_ref LIKE '%:find-profile' THEN 1 ELSE 0 END DESC,rowid DESC""", (market,)):
                latest.setdefault(row["creator_id"], (row["observed_us"] / 1_000_000, json.loads(row["payload_json"])["fields"]))
        bucket = int(self.now() // (max_age_hours * 3600))
        with self._lock:
            active = {r[0] for r in self._db.execute("SELECT creator_id FROM profile_refresh_job WHERE market=? AND status IN ('queued','running')", (market,))}
            used = {r[0] for r in self._db.execute("SELECT request_id FROM profile_refresh_request WHERE request_id LIKE ?", (f"profile-due:{market}:%:{bucket}",))}
        skipped, candidates = {}, []
        for creator in creators:
            observed, fields = latest.get(creator["creator_id"], (None, {}))
            reason = None
            request = f"profile-due:{market}:{creator['oec_id']}:{bucket}"
            if creator["creator_id"] in active or request in used:
                reason = "already_queued_or_attempted"
            elif any(isinstance(fields.get(name), dict) and fields[name].get("status") == "unauthorized" for name in CORE_FIELDS):
                reason = "permission_required"
            elif core_read_route(market) == "find" and not creator["current_handle"]:
                reason = "current_handle_required"
            elif observed is not None and observed + max_age_hours * 3600 > self.now():
                reason = "fresh"
            if reason:
                skipped[reason] = skipped.get(reason, 0) + 1
            else:
                candidates.append({"creatorId": creator["creator_id"], "requestId": request, "lastProfileObservedAt": _iso(observed) if observed else None})
        candidates.sort(key=lambda r: (r["lastProfileObservedAt"] or "", r["creatorId"]))
        return {"market": market, "maxAgeHours": max_age_hours, "limit": limit, "dueCreators": len(candidates),
                "items": candidates[:limit], "skipped": skipped, "platformRequests": 0}

    def enqueue_due(self, market, *, max_age_hours=168, limit=3):
        preview = self.due_candidates(market, max_age_hours=max_age_hours, limit=limit)
        jobs = [self.enqueue(row["creatorId"], row["requestId"], market) for row in preview["items"]]
        return {**preview, "jobs": jobs}

    def defer(self, job_id, owner, code, *, attempted=False):
        """Only before a platform request, return a busy read to its original queue."""
        with self._transaction():
            row = self._row(job_id)
            if row["status"] != "running" or row["lease_owner"] != owner or row["lease_until"] <= self.now():
                return self._public(row)
            delay = 30 if code == "account_busy" else 5
            self._db.execute("UPDATE profile_refresh_job SET status='queued',started_at=NULL,error_code=?,retry_at=?,attempt_no=attempt_no+?,lease_owner=NULL,lease_pid=NULL,lease_until=NULL WHERE id=?",
                             (code, self.now() + delay, int(attempted), job_id))
            return self._public(self._row(job_id))

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


def _execute_probe(target_file, output, *, root=ROOT):
    root = Path(root)
    python = PROJECT_PYTHON if root == ROOT else root / ".venv/bin/python"
    if not python.is_file():
        raise ProfileRefreshError("runtime_unavailable", 503)
    frozen = json.loads(target_file.read_text())
    market = frozen.get("market", "it")
    account = frozen.get("account") or _identity_account(root, market)
    from lib.market_accounts import load_config
    if account not in load_config(root)["markets"][market]["accounts"]:
        raise ProfileRefreshError("account_not_prepared", 409)
    # These paths are constructed from job IDs under our fixed var directory,
    # never from a request payload or an environment-selected script path.
    command = [str(python), str(root / "scripts/probe-italy-profile.py"), "--runtime-root", str(root),
               "--market", market, "--account", account, "--targets", str(target_file), "--output", str(output)]
    process = subprocess.Popen(command, cwd=root, start_new_session=True, stdin=subprocess.DEVNULL,
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


@contextmanager
def profile_read_resources(root, job, owner):
    """Share the original global/account slots; the existing profile mutex is still required."""
    from lib.second_cycle import CycleStore, CycleError
    from lib.workflow_resources import claim_profile_read, assert_current, release
    from lib.operations_policy import load_policy
    path = Path(root) / "var/second-cycle.sqlite"
    if not path.is_file():  # Standalone offline stores have no operations workflow.
        yield lambda: None
        return
    try:
        capacity = load_policy(root)["platformMaxParallelMarkets"]
        with CycleStore(path) as cycle:
            token = claim_profile_read(cycle, job["id"], owner,
                [("platform:global", capacity), ("supply:" + job["account"], 1), ("workflow:" + job["market"], 1)])
            def check():
                try:
                    assert_current(cycle, token["stageRunId"], owner, token["fence"])
                except CycleError as error:
                    raise ProfileRefreshError("resource_lease_lost", 409) from error
            try:
                yield check
            finally:
                try:
                    release(cycle, token["stageRunId"], owner, token["fence"])
                except CycleError:
                    pass  # Recovery already released this exact fence.
    except CycleError as error:
        if str(error) in {"workflow_resource_busy", "workflow_stage_already_claimed"}:
            raise ProfileRefreshError("resources_busy", 409) from error
        raise ProfileRefreshError("resource_lease_lost", 409) from error


class ProfileRefreshWorker:
    def __init__(self, store, *, executor=None, importer=None, market=None, resource_guard=None):
        self.store = store
        self.executor = executor or (lambda target, output: _execute_probe(target, output, root=store.root))
        self.importer = importer or _import_reports
        self.market = market
        self.resource_guard = resource_guard or profile_read_resources
        self._resource_check = None
        self.owner = "worker_" + uuid.uuid4().hex
        self._heartbeat_stop = threading.Event()
        self._heartbeat_thread = None

    def __enter__(self):
        self.store.heartbeat(self.owner, self.market)
        def beat():
            while not self._heartbeat_stop.wait(10):
                try:
                    self.store.heartbeat(self.owner, self.market)
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
        attempt = job.get("attempt_no", 1)
        if type(attempt) is not int or attempt < 1:
            raise ProfileRefreshError("invalid_request")
        output, target = directory / f"attempt-{attempt}", directory / "targets.private.json"
        if any(not path.resolve().is_relative_to(self.store.var_dir) for path in (directory, output, target, output / "report.private.json")):
            raise ProfileRefreshError("invalid_request")
        return directory, output, target

    def _identity(self, job):
        current = self.store.canonical(job["creator_id"])
        if (current["market"], current["oec_id"]) != (job["market"], job["oec_id"]):
            raise ProfileRefreshError("identity_mismatch", 409)
        return current

    def _input(self, job, current):
        route = job.get("read_route") or "profile"
        if route not in {"find", "profile"}:
            raise ProfileRefreshError("invalid_request")
        account = job.get("account") or _identity_account(self.store.root, job["market"])
        target = {"ref": job["id"], "externalId": job["creator_id"]}
        if route == "find":
            handle = job.get("requested_handle")
            from lib.creator_identity import valid_handle
            if not valid_handle(handle, maximum=100):
                raise ProfileRefreshError("current_handle_required", 409)
            target["handle"] = handle
        else:
            target["oecId"] = job["oec_id"]
            audit = job.get("requested_handle") if job.get("account") is not None else current["current_handle"]
            if audit:
                target["handle"] = audit
        value = {"market": job["market"], "account": account, "identityOnly": route == "find",
                 "targets": [target], "ownerWaitSeconds": 15}
        if route == "profile":
            value["profileTypeSets"] = [[2]]
        return value

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
            # A busy canonical mutex proves no request was submitted. Keep the
            # same job/input, and preserve each zero-request attempt separately.
            if report.get("requests") == [] and report.get("reason") == "probe_initialization_or_validation_error" and (
                    report.get("errorType") == "BlockingIOError" or report.get("errorMessage") == "account_in_use") and \
                    report.get("market") == job["market"] and report.get("account") == job.get("account") and \
                    type(report.get("oldDatabaseWrites")) is int and report["oldDatabaseWrites"] == 0 and \
                    type(report.get("realSends")) is int and report["realSends"] == 0:
                return self.store.defer(job["id"], self.owner, "account_busy", attempted=True)
            reason = "probe_timeout" if report.get("status") == "bounded_timeout" else report.get("reason")
            return self._block(job, reason if isinstance(reason, str) and reason in SAFE_ERRORS else "probe_failed", request_count=count)
        try:
            self._identity(job)
            targets, requests = report.get("targets"), report.get("requests")
            schema = "bdhub.italy-profile-probe.v3" if job["market"] == "it" else "bdhub.market-profile-probe.v1"
            account = job.get("account") or ("acc6" if job["market"] == "it" else _identity_account(self.store.root, job["market"]))
            if report.get("schema") != schema or report.get("market") != job["market"] or \
                    report.get("account") != account or report.get("identityFileUnchanged") is not True or \
                    type(report.get("oldDatabaseWrites")) is not int or report["oldDatabaseWrites"] != 0 or \
                    type(report.get("realSends")) is not int or report["realSends"] != 0 or \
                    not isinstance(targets, list) or len(targets) != 1 or not isinstance(requests, list) or not requests:
                raise ProfileRefreshError("probe_report_invalid")
            target = targets[0]
            route = job.get("read_route") or "profile"
            core = None
            if route == "find":
                if target.get("inputKind") != "handle_discovery" or target.get("requestedOecId") is not None or \
                        target.get("requestedHandle") != job["requested_handle"] or len(requests) != 1 or requests[0].get("stage") != "find":
                    raise ProfileRefreshError("probe_report_invalid")
                rows = find_core_observations(report, [{"ref": job["id"], "handle": job["requested_handle"], "sourceId": job["creator_id"]}],
                    market=job["market"], account=account, expected_oecs={job["id"]: job["oec_id"]})
                if len(rows) != 1:
                    raise ProfileRefreshError("identity_mismatch", 409)
                core = rows[0]
            elif target.get("targetRef") != job["id"] or target.get("externalId") != job["creator_id"] or \
                    target.get("inputKind") != "known_oec" or target.get("requestedOecId") != job["oec_id"] or \
                    target.get("requestedHandle") is not None or target.get("status") != "completed" or \
                    target.get("oecId") != job["oec_id"] or any(
                        row.get("targetRef") != job["id"] or row.get("stage") != "profile" or
                        row.get("status") != "returned" or row.get("httpStatus") != 200 or
                        row.get("code") != "0" or row.get("verificationRequired") is not False for row in requests):
                raise ProfileRefreshError("probe_report_invalid")
            if route == "profile" and job["market"] != "it":
                summary = target.get("merged") or {}
                profiles = target.get("profiles")
                if not isinstance(profiles, list) or len(profiles) != len(requests) or any(
                        not isinstance(p, dict) or p.get("profileTypes") != request.get("profileTypes")
                        for p, request in zip(profiles, requests)) or merge_profile_summaries([p["summary"] for p in profiles]) != summary:
                    raise ProfileRefreshError("probe_report_invalid")
                payload = core_summary(summary, market=job["market"], oec_id=job["oec_id"], handle=summary.get("identity", {}).get("handle"))
                payload["profileSource"] = {"provider": "tiktok", "method": "profile", "account": account, "windowStart": None,
                                            "salesPerformanceEnd": payload["fields"]["sales_performance_end_time"]}
                core = {"handle": payload["identity"]["handle"], "observedAt": _observed_at(report["finishedAt"]),
                        "evidenceRef": f"profile-refresh:{job['id']}:profile", "payload": payload}
            with self.store.hold_lease(job["id"], self.owner):
                self._identity(job)
                if self._resource_check is not None:
                    self._resource_check()
                with CreatorIdentityStore(self.store.identity_path) as identities:
                    if core is None:
                        self.importer(identities, [report])
                    else:
                        identities.observe_known_profile(job["creator_id"], job["market"], job["oec_id"], core["handle"],
                                                         core["observedAt"], core["evidenceRef"], core["payload"])
                    current = identities.get_by_oec(job["market"], job["oec_id"])
                    if current is None or current["creatorId"] != job["creator_id"]:
                        raise ProfileRefreshError("identity_mismatch", 409)
            return self.store.finish(job["id"], self.owner, identity_updated=True, request_count=count)
        except ProfileRefreshError as error:
            return self._block(job, error.code, request_count=count)
        except ValueError as error:
            return self._block(job, "identity_mismatch" if str(error) == "core_profile_identity_mismatch" else "probe_report_invalid", request_count=count)
        except Exception:
            return self._block(job, "identity_import_failed", request_count=count)

    def run_once(self):
        self.store.heartbeat(self.owner, self.market)
        recovery = self.store.claim_recovery(self.owner, self.market)
        if recovery is not None:
            try:
                _, output, _ = self._paths(recovery)
                report = self._read_final(output / "report.private.json")
                return self._settle(recovery, report) if report else self._block(recovery, "lease_expired_no_report")
            except ProfileRefreshError as error:
                return self._block(recovery, error.code)
            except Exception:
                return self._block(recovery, "probe_report_invalid")
        job = self.store.claim(self.owner, self.market)
        if job is None:
            return None
        try:
            current = self._identity(job)
            directory, output, target_file = self._paths(job)
            report = self._read_final(output / "report.private.json")
            if report is not None:
                return self._settle(job, report)
            if output.exists() or (target_file.exists() and job.get("attempt_no", 1) == 1):
                return self._block(job, "attempt_exists_without_final")
            frozen = self._input(job, current)
            with self.resource_guard(self.store.root, {**job, "account": frozen["account"]}, self.owner) as check:
                self._resource_check = check
                directory.mkdir(parents=True, exist_ok=True, mode=0o700)
                if target_file.exists():
                    if json.loads(target_file.read_text()) != frozen:
                        raise ProfileRefreshError("identity_mismatch", 409)
                else:
                    with target_file.open("x", encoding="utf-8") as stream:
                        target_file.chmod(0o600)
                        json.dump(frozen, stream)
                returncode = self.executor(target_file, output)
                report = self._read_final(output / "report.private.json")
                if report is not None:
                    return self._settle(job, report)
                return self._block(job, "probe_failed" if returncode else "probe_report_missing")
        except ProfileRefreshError as error:
            if error.code == "resources_busy":
                return self.store.defer(job["id"], self.owner, error.code)
            return self._block(job, error.code)
        except (KeyboardInterrupt, SystemExit):
            self._block(job, "worker_interrupted")
            raise
        except Exception:
            return self._block(job, "probe_failed")
        finally:
            self._resource_check = None
