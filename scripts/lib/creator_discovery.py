"""Explicit handle discovery batches, separate from OEC-only profile refresh."""
from __future__ import annotations

from contextlib import contextmanager, closing
from datetime import datetime
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
from urllib.parse import urlsplit
import uuid

from lib.creator_identity import CreatorIdentityStore
from lib.profile_refresh import ROOT, VAR, SAFE_ERRORS, LEASE_SECONDS, _alive, _execute_probe, _iso

ERRORS = SAFE_ERRORS | {"input_limit_exceeded", "preview_mismatch", "empty_batch", "batch_not_found", "batch_not_resumable"}


class CreatorDiscoveryError(ValueError):
    def __init__(self, code, status=400):
        self.code = code if isinstance(code, str) and code in ERRORS else "internal_error"
        self.status = status
        super().__init__(self.code)


def _token(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", value):
        raise CreatorDiscoveryError("invalid_request")
    return value


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def preview(market, source_label, text):
    """Parse only. No stores, identities, lookups or network are consulted."""
    if market != "it":
        raise CreatorDiscoveryError("unsupported_market", 409)
    if not isinstance(source_label, str) or not 1 <= len(source_label.strip()) <= 120 or any(ord(c) < 32 for c in source_label):
        raise CreatorDiscoveryError("invalid_request")
    if not isinstance(text, str):
        raise CreatorDiscoveryError("invalid_request")
    if len(text.encode("utf-8")) > 65536:
        raise CreatorDiscoveryError("input_limit_exceeded")
    label = source_label.strip()
    tokens = [token.strip() for token in re.split(r"[,\r\n]+", text) if token.strip()]
    if len(tokens) > 500:
        raise CreatorDiscoveryError("input_limit_exceeded")
    items, seen = [], {}
    counts = {"total": len(tokens), "valid": 0, "duplicate": 0, "invalid": 0}
    for index, raw in enumerate(tokens, 1):
        candidate, reason = raw, None
        if raw.isascii() and raw.isdigit() and len(raw) >= 15:
            reason = "looks_like_id"
        if "://" in raw:
            try:
                url = urlsplit(raw)
                match = re.fullmatch(r"/@([A-Za-z0-9._]{1,64})/?", url.path)
                if url.scheme != "https" or url.netloc.lower() not in {"tiktok.com", "www.tiktok.com"} or not match:
                    reason = "unsupported_url"
                else:
                    candidate = match[1]
            except ValueError:
                reason = "unsupported_url"
        handle = candidate.removeprefix("@").lower() if reason is None else None
        if reason is None and not re.fullmatch(r"[a-z0-9._]{1,64}", handle):
            reason, handle = "invalid_handle", None
        duplicate_of = seen.get(handle) if handle else None
        status = "invalid" if reason else ("duplicate" if duplicate_of is not None else "valid")
        if status == "duplicate":
            reason = "duplicate_handle"
        if status == "valid":
            seen[handle] = index
        counts[status] += 1
        items.append({"index": index, "raw": raw, "handle": handle, "status": status, "reason": reason, "duplicateOf": duplicate_of})
    return {"market": market, "sourceLabel": label, "previewHash": _hash({"market": market, "sourceLabel": label, "text": text}),
            "counts": counts, "items": items, "canSubmit": counts["valid"] > 0}


_SCHEMA = """
CREATE TABLE IF NOT EXISTS creator_discovery_meta(version INTEGER PRIMARY KEY CHECK(version=1));
INSERT OR IGNORE INTO creator_discovery_meta VALUES(1);
CREATE TABLE IF NOT EXISTS discovery_batch(
 id TEXT PRIMARY KEY,market TEXT NOT NULL,source_label TEXT NOT NULL,input_hash TEXT NOT NULL,input_json TEXT NOT NULL,
 status TEXT NOT NULL,created_at TEXT NOT NULL,started_at TEXT,finished_at TEXT,error_code TEXT);
CREATE TABLE IF NOT EXISTS discovery_item(
 id TEXT PRIMARY KEY,batch_id TEXT NOT NULL REFERENCES discovery_batch(id),row_index INTEGER NOT NULL,raw TEXT NOT NULL,
 handle TEXT,status TEXT NOT NULL,reason TEXT,duplicate_of INTEGER,creator_id TEXT,oec_id TEXT,outcome TEXT,
 started_at TEXT,finished_at TEXT,request_count INTEGER DEFAULT 0,lease_owner TEXT,lease_pid INTEGER,lease_until REAL,
 UNIQUE(batch_id,row_index));
DROP INDEX IF EXISTS discovery_one_active_item;
CREATE INDEX IF NOT EXISTS discovery_running_items ON discovery_item(status,lease_owner);
CREATE TABLE IF NOT EXISTS discovery_cohort(id TEXT PRIMARY KEY,owner TEXT NOT NULL,payload TEXT NOT NULL,state TEXT NOT NULL,created REAL NOT NULL);
CREATE INDEX IF NOT EXISTS discovery_item_queue ON discovery_item(batch_id,status,row_index);
CREATE TABLE IF NOT EXISTS discovery_request(request_id TEXT PRIMARY KEY,fingerprint TEXT NOT NULL,batch_id TEXT NOT NULL REFERENCES discovery_batch(id));
CREATE TABLE IF NOT EXISTS discovery_heartbeat(owner TEXT PRIMARY KEY,pid INTEGER NOT NULL,seen_at REAL NOT NULL);
"""


class CreatorDiscoveryStore:
    def __init__(self, var_dir=VAR, *, now=time.time):
        self.var_dir = Path(var_dir).resolve()
        self.var_dir.mkdir(parents=True, exist_ok=True)
        self.identity_path = self.var_dir / "creator-identities.sqlite"
        self.output_root = self.var_dir / "creator-discovery"
        self.now, self._lock = now, threading.RLock()
        path = self.var_dir / "creator-discovery.sqlite"
        self._db = sqlite3.connect(path, timeout=10, isolation_level=None, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        try:
            tables = {r[0] for r in self._db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if tables and "creator_discovery_meta" not in tables:
                raise CreatorDiscoveryError("internal_error", 500)
            if tables and [r[0] for r in self._db.execute("SELECT version FROM creator_discovery_meta")] != [1]:
                raise CreatorDiscoveryError("internal_error", 500)
            self._db.execute("PRAGMA foreign_keys=ON")
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.executescript("BEGIN IMMEDIATE;\n" + _SCHEMA + "\nCOMMIT;")
            columns={r[1] for r in self._db.execute('PRAGMA table_info(discovery_item)')}
            if 'attempt_no' not in columns:self._db.execute('ALTER TABLE discovery_item ADD COLUMN attempt_no INTEGER NOT NULL DEFAULT 1')
            if 'retry_at' not in columns:self._db.execute('ALTER TABLE discovery_item ADD COLUMN retry_at REAL NOT NULL DEFAULT 0')
            path.chmod(0o600)
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
    def transaction(self):
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                yield
                self._db.execute("COMMIT")
            except BaseException:
                self._db.execute("ROLLBACK")
                raise

    def _batch(self, batch_id):
        row = self._db.execute("SELECT * FROM discovery_batch WHERE id=?", (batch_id,)).fetchone()
        if row is None:
            raise CreatorDiscoveryError("batch_not_found", 404)
        return dict(row)

    def _summary(self, batch_id):
        batch = self._batch(batch_id)
        counts = {key: 0 for key in ("total", "queued", "running", "completed", "unresolved", "blocked", "invalid", "duplicate", "created", "existing", "identityOnly")}
        for row in self._db.execute("SELECT status,outcome,COUNT(*) AS n FROM discovery_item WHERE batch_id=? GROUP BY status,outcome", (batch_id,)):
            counts["total"] += row["n"]
            counts[row["status"]] += row["n"]
            if row["outcome"]:
                counts["identityOnly" if row["outcome"] == "identity_only" else row["outcome"]] += row["n"]
        return {"id": batch_id, "market": batch["market"], "sourceLabel": batch["source_label"], "status": batch["status"],
                "createdAt": batch["created_at"], "startedAt": batch["started_at"], "finishedAt": batch["finished_at"],
                "errorCode": batch["error_code"], "counts": counts, "workerOnline": self.worker_online()}

    def detail(self, batch_id):
        batch_id = _token(batch_id)
        with self._lock:
            summary = self._summary(batch_id)
            items = [{"id": r["id"], "index": r["row_index"], "handle": r["handle"], "status": r["status"], "reason": r["reason"],
                      "duplicateOf": r["duplicate_of"], "creatorId": r["creator_id"], "oecId": r["oec_id"], "outcome": r["outcome"],
                      "startedAt": r["started_at"], "finishedAt": r["finished_at"], "requestCount": r["request_count"]}
                     for r in self._db.execute("SELECT * FROM discovery_item WHERE batch_id=? ORDER BY row_index", (batch_id,))]
            return {"batch": summary, "items": items}

    def list_batches(self):
        with self._lock:
            return {"batches": [self._summary(r[0]) for r in self._db.execute("SELECT id FROM discovery_batch ORDER BY created_at DESC,id DESC LIMIT 20").fetchall()],
                    "workerOnline": self.worker_online()}

    def _replay(self, request_id, fingerprint):
        row = self._db.execute("SELECT * FROM discovery_request WHERE request_id=?", (request_id,)).fetchone()
        if row is not None and row["fingerprint"] != fingerprint:
            raise CreatorDiscoveryError("idempotency_conflict", 409)
        return row["batch_id"] if row else None

    def submit(self, market, source_label, text, preview_hash, request_id):
        request_id = _token(request_id)
        parsed = preview(market, source_label, text)
        if preview_hash != parsed["previewHash"]:
            raise CreatorDiscoveryError("preview_mismatch", 409)
        if not parsed["canSubmit"]:
            raise CreatorDiscoveryError("empty_batch")
        fingerprint = _hash({"command": "submit", "previewHash": preview_hash})
        with self.transaction():
            previous = self._replay(request_id, fingerprint)
            if previous:
                return self._summary(previous)
            batch_id = "discovery_" + uuid.uuid4().hex
            self._db.execute("INSERT INTO discovery_batch(id,market,source_label,input_hash,input_json,status,created_at) VALUES(?,?,?,?,?,?,?)",
                (batch_id, market, parsed["sourceLabel"], preview_hash, _json({"market": market, "sourceLabel": parsed["sourceLabel"], "text": text}), "queued", _iso(self.now())))
            for item in parsed["items"]:
                self._db.execute("INSERT INTO discovery_item(id,batch_id,row_index,raw,handle,status,reason,duplicate_of) VALUES(?,?,?,?,?,?,?,?)",
                    ("discovery_item_" + uuid.uuid4().hex, batch_id, item["index"], item["raw"], item["handle"],
                     "queued" if item["status"] == "valid" else item["status"], item["reason"], item["duplicateOf"]))
            self._db.execute("INSERT INTO discovery_request VALUES(?,?,?)", (request_id, fingerprint, batch_id))
            return self._summary(batch_id)

    def control(self, batch_id, action, request_id):
        batch_id, request_id = _token(batch_id), _token(request_id)
        if not isinstance(action, str) or action not in {"pause", "resume"}:
            raise CreatorDiscoveryError("invalid_request")
        fingerprint = _hash({"command": "control", "batchId": batch_id, "action": action})
        with self.transaction():
            prior = self._replay(request_id, fingerprint)
            if prior:
                return self._summary(prior)
            current = self._summary(batch_id)
            if action == "pause":
                if current["status"] not in {"queued", "running", "paused"}:
                    raise CreatorDiscoveryError("batch_not_resumable", 409)
                self._db.execute("UPDATE discovery_batch SET status='paused' WHERE id=?", (batch_id,))
            else:
                if current["status"] not in {"paused", "blocked"} or current["counts"]["queued"] == 0:
                    raise CreatorDiscoveryError("batch_not_resumable", 409)
                self._db.execute("UPDATE discovery_batch SET status=?,error_code=NULL,finished_at=NULL WHERE id=?",
                                 ("running" if current["counts"]["running"] else "queued", batch_id))
            self._db.execute("INSERT INTO discovery_request VALUES(?,?,?)", (request_id, fingerprint, batch_id))
            return self._summary(batch_id)

    def heartbeat(self, owner):
        with self.transaction():
            self._db.execute("INSERT INTO discovery_heartbeat VALUES(?,?,?) ON CONFLICT(owner) DO UPDATE SET pid=excluded.pid,seen_at=excluded.seen_at", (owner, os.getpid(), self.now()))

    def worker_online(self):
        with self._lock:
            return any(_alive(r[0]) for r in self._db.execute("SELECT pid FROM discovery_heartbeat WHERE seen_at>=?", (self.now() - 30,)))

    def _next_items(self,limit,batch_ids=None,distinct=False):
        # Unknown handles grow the creator pool first; this only changes
        # queue order, never substitutes cached identity for exact Find.
        known=[]; identity_path=self.var_dir/'creator-identities.sqlite'
        if identity_path.exists():
            with closing(sqlite3.connect(identity_path.resolve().as_uri()+'?mode=ro',uri=True)) as identities:
                if identities.execute("SELECT 1 FROM sqlite_master WHERE name='creator_identity'").fetchone():
                    known=[r[0] for r in identities.execute("SELECT current_handle FROM creator_identity WHERE market='it' AND current_handle IS NOT NULL AND handle_conflict=0")]
        if distinct:
            return self._db.execute("""WITH candidates AS (
              SELECT i.*,b.created_at queue_created,CASE WHEN b.source_label='Kalodata 二发线索身份解析' THEN CASE WHEN i.handle IN (SELECT value FROM json_each(?)) THEN 2 WHEN EXISTS(SELECT 1 FROM discovery_item old WHERE old.handle=i.handle AND old.id<>i.id AND old.status IN ('completed','unresolved')) THEN 1 ELSE 0 END ELSE 0 END queue_priority
              FROM discovery_item i JOIN discovery_batch b ON b.id=i.batch_id WHERE i.status='queued' AND b.status IN ('queued','running') AND i.retry_at<=? AND (? IS NULL OR b.id IN (SELECT value FROM json_each(?)))
            ), unique_handles AS (SELECT *,row_number() OVER(PARTITION BY handle ORDER BY queue_priority,queue_created,batch_id,row_index) occurrence FROM candidates)
            SELECT * FROM unique_handles WHERE occurrence=1 ORDER BY queue_priority,queue_created,batch_id,row_index LIMIT ?""",(_json(known),self.now(),_json(batch_ids) if batch_ids is not None else None,_json(batch_ids) if batch_ids is not None else None,limit)).fetchall()
        return self._db.execute("""SELECT i.* FROM discovery_item i JOIN discovery_batch b ON b.id=i.batch_id
            WHERE i.status='queued' AND b.status IN ('queued','running') AND i.retry_at<=? AND (? IS NULL OR b.id IN (SELECT value FROM json_each(?)))
            ORDER BY CASE WHEN b.source_label='Kalodata 二发线索身份解析' THEN CASE WHEN i.handle IN (SELECT value FROM json_each(?)) THEN 2 WHEN EXISTS(SELECT 1 FROM discovery_item old WHERE old.handle=i.handle AND old.id<>i.id AND old.status IN ('completed','unresolved')) THEN 1 ELSE 0 END ELSE 0 END,b.created_at,b.id,i.row_index LIMIT ?""",(self.now(),_json(batch_ids) if batch_ids is not None else None,_json(batch_ids) if batch_ids is not None else None,_json(known),limit)).fetchall()

    def claim(self, owner, *, recovery=False):
        with self.transaction():
            running = self._db.execute("SELECT * FROM discovery_item WHERE status='running' LIMIT 1").fetchone()
            if recovery:
                if running is None or (running["lease_until"] > self.now() and _alive(running["lease_pid"])):
                    return None
                row = running
            else:
                if running:
                    return None
                candidates=self._next_items(1)
                row=candidates[0] if candidates else None
                if row is None:
                    return None
                self._db.execute("UPDATE discovery_batch SET status='running',started_at=COALESCE(started_at,?) WHERE id=?", (_iso(self.now()), row["batch_id"]))
            self._db.execute("UPDATE discovery_item SET status='running',started_at=COALESCE(started_at,?),request_count=NULL,lease_owner=?,lease_pid=?,lease_until=? WHERE id=?",
                             (_iso(self.now()), owner, os.getpid(), self.now() + LEASE_SECONDS, row["id"]))
            return dict(self._db.execute("SELECT * FROM discovery_item WHERE id=?", (row["id"],)).fetchone())

    def claim_cohort(self,owner,batch_ids,limit=20):
        if type(limit) is not int or not 1<=limit<=20:raise CreatorDiscoveryError('invalid_request')
        with self.transaction():
            if self._db.execute("SELECT 1 FROM discovery_item WHERE status='running'").fetchone():return None
            candidates=self._next_items(limit,batch_ids,distinct=True);items=[];handles=set()
            for row in candidates:
                if row['handle'] in handles:continue
                handles.add(row['handle']);items.append(dict(row))
                if len(items)==limit:break
            if not items:return None
            for item in items:
                self._db.execute("UPDATE discovery_item SET status='running',started_at=COALESCE(started_at,?),request_count=NULL,lease_owner=?,lease_pid=?,lease_until=? WHERE id=?",(_iso(self.now()),owner,os.getpid(),self.now()+LEASE_SECONDS,item['id']))
                self._db.execute("UPDATE discovery_batch SET status='running',started_at=COALESCE(started_at,?) WHERE id=?",(_iso(self.now()),item['batch_id']))
            cid='discovery_cohort_'+uuid.uuid4().hex
            self._db.execute("INSERT INTO discovery_cohort VALUES(?,?,?,'running',?)",(cid,owner,_json(items),self.now()))
            return {'id':cid,'items':items}

    def recover_cohort(self,owner):
        with self.transaction():
            for row in self._db.execute("SELECT * FROM discovery_cohort WHERE state='running' ORDER BY created").fetchall():
                items=json.loads(row['payload']);active=[]
                for item in items:
                    live=self._db.execute('SELECT * FROM discovery_item WHERE id=?',(item['id'],)).fetchone()
                    if live['status']=='running':active.append(live)
                if any(x['lease_until']>self.now() and _alive(x['lease_pid']) for x in active):return None
                for item in active:self._db.execute('UPDATE discovery_item SET lease_owner=?,lease_pid=?,lease_until=? WHERE id=?',(owner,os.getpid(),self.now()+LEASE_SECONDS,item['id']))
                self._db.execute('UPDATE discovery_cohort SET owner=? WHERE id=?',(owner,row['id']))
                return {'id':row['id'],'items':items,'recovering':True}
            return None

    @contextmanager
    def hold_lease(self, item, owner):
        with self.transaction():
            row = self._db.execute("SELECT * FROM discovery_item WHERE id=?", (item["id"],)).fetchone()
            if row is None or row["status"] != "running" or row["lease_owner"] != owner or row["lease_until"] <= self.now():
                raise CreatorDiscoveryError("stale_lease", 409)
            self._db.execute("UPDATE discovery_item SET lease_until=? WHERE id=?", (self.now() + LEASE_SECONDS, item["id"]))
            yield

    def defer_busy(self,item,owner):
        with self.transaction():
            current=self._db.execute('SELECT * FROM discovery_item WHERE id=?',(item['id'],)).fetchone()
            if current['status']!='running' or current['lease_owner']!=owner:raise CreatorDiscoveryError('stale_lease')
            self._db.execute("UPDATE discovery_item SET status='queued',reason=NULL,request_count=0,attempt_no=attempt_no+1,retry_at=?,lease_owner=NULL,lease_pid=NULL,lease_until=NULL WHERE id=?",(self.now()+30,item['id']))
            self._db.execute("UPDATE discovery_batch SET status='queued',error_code=NULL WHERE id=? AND status<>'paused'",(item['batch_id'],))
        return self.detail(item['batch_id'])

    def finish(self, item, owner, status, *, reason=None, creator_id=None, oec_id=None, outcome=None, request_count=None):
        with self.transaction():
            current = self._db.execute("SELECT status,lease_owner FROM discovery_item WHERE id=?", (item["id"],)).fetchone()
            if current["status"] != "running" or current["lease_owner"] != owner:
                return self.detail(item["batch_id"])
            self._db.execute("""UPDATE discovery_item SET status=?,reason=?,creator_id=?,oec_id=?,outcome=?,request_count=?,finished_at=?,
                lease_owner=NULL,lease_pid=NULL,lease_until=NULL WHERE id=?""",
                (status, reason, creator_id, oec_id, outcome, request_count, _iso(self.now()), item["id"]))
            batch = self._summary(item["batch_id"])
            if status == "blocked":
                paused = batch["status"] == "paused"
                self._db.execute("UPDATE discovery_batch SET status=?,error_code=?,finished_at=? WHERE id=?",
                    ("paused" if paused else "blocked", reason, None if paused else _iso(self.now()), item["batch_id"]))
            elif not batch["counts"]["queued"] and not batch["counts"]["running"]:
                failed = self._db.execute("SELECT reason FROM discovery_item WHERE batch_id=? AND status='blocked' ORDER BY row_index LIMIT 1", (item["batch_id"],)).fetchone()
                self._db.execute("UPDATE discovery_batch SET status=?,error_code=?,finished_at=? WHERE id=?",
                    ("blocked" if failed else "completed", failed[0] if failed else None, _iso(self.now()), item["batch_id"]))
            elif batch["status"] != "paused":
                self._db.execute("UPDATE discovery_batch SET status='running' WHERE id=?", (item["batch_id"],))
            return self.detail(item["batch_id"])


class _AtomicIdentities(CreatorIdentityStore):
    """Reuse registry contracts within one outer transaction for classification."""
    @contextmanager
    def _transaction(self):
        with self._lock:
            if not self._db.in_transaction:
                with super()._transaction():
                    yield
            else:
                name = "discovery_" + uuid.uuid4().hex
                self._db.execute("SAVEPOINT " + name)
                try:
                    yield
                    self._db.execute("RELEASE " + name)
                except BaseException:
                    self._db.execute("ROLLBACK TO " + name)
                    self._db.execute("RELEASE " + name)
                    raise


class CreatorDiscoveryWorker:
    def __init__(self, store, *, executor=None):
        self.store, self.executor = store, executor or _execute_probe
        self.owner = "discovery_worker_" + uuid.uuid4().hex
        self._stop = threading.Event()
        self._thread = None

    def __enter__(self):
        self.store.heartbeat(self.owner)
        def beat():
            while not self._stop.wait(10):
                try:
                    self.store.heartbeat(self.owner)
                except Exception:
                    pass
        self._thread = threading.Thread(target=beat, daemon=True, name="creator-discovery-heartbeat")
        self._thread.start()
        return self

    def __exit__(self, *_):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1)
        with self.store.transaction():
            self.store._db.execute("DELETE FROM discovery_heartbeat WHERE owner=?", (self.owner,))

    def _identity_only_batch(self,item):
        """Only actual active cycle handoffs use the shortened critical path."""
        path=self.store.var_dir/'second-cycle.sqlite'
        if not path.exists():return False
        with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=2)) as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name='cycle_identity_outbox'").fetchone():return False
            return db.execute("SELECT 1 FROM cycle_identity_outbox o JOIN plan p ON p.id=o.plan_id WHERE o.batch_id=? AND p.state='active' AND p.market='it'",(item['batch_id'],)).fetchone() is not None

    def _paths(self, item):
        if not re.fullmatch(r"discovery_item_[0-9a-f]{32}", item["id"]):
            raise CreatorDiscoveryError("invalid_request")
        directory = self.store.output_root / item["batch_id"] / item["id"]
        number=item.get("attempt_no",1)
        paths = directory, directory / f"attempt-{number}", directory / ("targets.private.json" if number==1 else f"targets-{number}.private.json")
        if any(not path.resolve().is_relative_to(self.store.var_dir) for path in (*paths, paths[1] / "report.private.json")):
            raise CreatorDiscoveryError("invalid_request")
        return paths

    @staticmethod
    def _final(path):
        if not path.is_file():
            return None
        if path.stat().st_size > 4 * 1024 * 1024:
            raise CreatorDiscoveryError("probe_report_invalid")
        value = json.loads(path.read_text())
        if not isinstance(value, dict) or not isinstance(value.get("status"), str):
            raise CreatorDiscoveryError("probe_report_invalid")
        if value.get("status") not in {"completed", "blocked", "bounded_timeout"} or not value.get("finishedAt"):
            return None
        stamp = datetime.fromisoformat(value["finishedAt"].replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            raise CreatorDiscoveryError("probe_report_invalid")
        return value

    def _blocked(self, item, code, **values):
        return self.store.finish(item, self.owner, "blocked", reason=code if isinstance(code, str) and code in ERRORS else "probe_failed", **values)

    @staticmethod
    def _pending(identities, handle):
        return [r[0] for r in identities._db.execute("""SELECT l.lead_id FROM handle_lead l
            LEFT JOIN handle_lead_resolution r USING(lead_id) WHERE l.market='it' AND l.handle=? AND r.lead_id IS NULL
            ORDER BY l.observed_at,l.lead_id""", (handle,))]

    def _lead(self, identities, item):
        leads = self._pending(identities, item["handle"])
        if leads:
            return leads
        batch = self.store._batch(item["batch_id"])
        lead = identities.record_handle_lead("it", item["handle"], batch["created_at"], f"creator-discovery:{item['id']}:lead",
            external_source="discovery_batch", external_id=item["id"], payload={"sourceLabel": batch["source_label"], "batchId": batch["id"]})
        # Replaying an already-resolved event must not create a new pending lead.
        return [lead["leadId"]] if lead["status"] == "pending" else []

    def _settle(self, item, report):
        count = report.get("counters", {}).get("request_count") if isinstance(report.get("counters"), dict) else None
        count = count if type(count) is int and count >= 0 else None
        try:
            if report.get("schema") != "bdhub.italy-profile-probe.v3" or report.get("market") != "it" or report.get("account") != "acc6" or \
                    type(report.get("oldDatabaseWrites")) is not int or report["oldDatabaseWrites"] != 0 or \
                    type(report.get("realSends")) is not int or report["realSends"] != 0:
                raise CreatorDiscoveryError("probe_report_invalid")
            targets, requests = report.get("targets"), report.get("requests")
            if not isinstance(targets, list) or not isinstance(requests, list) or len(targets) > 1:
                raise CreatorDiscoveryError("probe_report_invalid")
            if not targets and not requests and report.get('errorType')=='BlockingIOError' and report.get('reason')=='probe_initialization_or_validation_error':
                return self.store.defer_busy(item,self.owner)
            if not targets:
                return self._blocked(item, report.get("reason"), request_count=count)
            target = targets[0]
            if target.get("targetRef") != item["id"] or target.get("externalId") != item["id"] or target.get("inputKind") != "handle_discovery" or \
                    target.get("requestedHandle") != item["handle"] or any(r.get("targetRef") != item["id"] for r in requests):
                raise CreatorDiscoveryError("probe_report_invalid")
            receipt = next((r for r in requests if r.get("stage") == "find"), None)
            receipt_ok = receipt is not None and receipt.get("status") == "returned" and receipt.get("httpStatus") == 200 and receipt.get("code") == "0" and receipt.get("verificationRequired") is False
            no_exact = report.get("status") == "completed" and target.get("status") == "unresolved" and target.get("reason") == "no_exact_handle" and receipt_ok
            spec = importlib.util.spec_from_file_location("discovery_import_contract", ROOT / "scripts/import-creator-identities.py")
            importer = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(importer)
            if no_exact:
                with self.store.hold_lease(item, self.owner), _AtomicIdentities(self.store.identity_path) as identities, identities._transaction():
                    self._lead(identities, item)
                return self.store.finish(item, self.owner, "unresolved", reason="no_exact_handle", request_count=count)
            found = importer.evidence.normalized(target["find"]) if isinstance(target.get("find"), dict) else None
            if not receipt_ok or found is None:
                return self._blocked(item, report.get("reason"), request_count=count)
            found_identity = found["identity"]
            if target.get("currentHandleResolved") is not True or found_identity["handle"] != item["handle"] or found_identity["market"] not in (None, "it") or \
                    not isinstance(found_identity["oecId"], str) or not re.fullmatch(r"[0-9]{1,40}", found_identity["oecId"]) or found_identity["oecId"] != target.get("oecId"):
                raise CreatorDiscoveryError("probe_report_invalid")
            identity_only = report.get("identityOnly") is True and report.get("status")=="completed" and target.get("status")=="identity_verified"
            if identity_only and (target.get("profiles") or target.get("profileCollection")!="not_requested" or any(r.get("stage")!="find" or r.get("status")!="returned" or r.get("httpStatus")!=200 or r.get("code")!="0" or r.get("verificationRequired") is not False for r in requests)):
                raise CreatorDiscoveryError("probe_report_invalid")
            complete = report.get("status") == "completed" and target.get("status") == "completed"
            completion_error = None
            if complete:
                try:
                    importer.evidence.build_documents([report])
                    if not target.get("profiles") or not any(r.get("stage") == "profile" for r in requests) or report.get("identityFileUnchanged") is not True or any(r.get("stage") not in {"find", "profile"} or r.get("status") != "returned" or r.get("httpStatus") != 200 or r.get("code") != "0" or r.get("verificationRequired") is not False for r in requests):
                        raise ValueError("profile_receipt_invalid")
                except (ValueError, TypeError, KeyError, AttributeError):
                    complete, completion_error = False, "probe_report_invalid"
            if not complete:
                # A partial identity needs explicit IT Find evidence and an
                # unchanged account snapshot. Conflicting Profile identity is
                # not a routine missing-field response and must not enter IT.
                if found_identity["market"] != "it" or report.get("identityFileUnchanged") is not True or report.get("reason") in {
                    "profile_identity_mismatch", "profile_market_mismatch", "supplement_identity_mismatch", "supplement_market_mismatch"
                }:
                    raise CreatorDiscoveryError("probe_report_invalid")
                for observation in target.get("profiles", []):
                    observed_identity = importer.evidence.normalized(observation["summary"])["identity"]
                    if observed_identity["oecId"] != found_identity["oecId"] or observed_identity["market"] not in (None, "it"):
                        raise CreatorDiscoveryError("probe_report_invalid")
            oec_id, observed_at = found_identity["oecId"], report["finishedAt"]
            reference = f"creator-discovery:{item['id']}:{_hash(report)}"
            with self.store.hold_lease(item, self.owner), _AtomicIdentities(self.store.identity_path) as identities, identities._transaction():
                previous = identities.get_by_oec("it", oec_id)
                # All lead resolutions and the canonical upsert share this one
                # SQLite write transaction; another worker cannot miscount a creation.
                for lead_id in self._lead(identities, item):
                    identities.resolve_handle_lead(lead_id, oec_id, observed_at, reference + ":find", exact_find={
                        "market": "it", "queriedHandle": item["handle"], "returnedHandle": item["handle"], "oecId": oec_id,
                        "httpStatus": 200, "code": 0, "verificationRequired": False})
                current = identities.observe_profile("it", oec_id, found_identity["handle"], observed_at, reference + ":find-profile", payload=found)
                if complete:
                    merged = importer.evidence.normalized(target["merged"])
                    current = identities.observe_profile("it", oec_id, merged["identity"]["handle"], observed_at, reference + ":profile", payload=merged)
                elif not identity_only:
                    identities.record_profile_failure("it", oec_id, observed_at, reference + ":profile-failure", "timeout" if report.get("status") == "bounded_timeout" else "unknown")
                # Persist classification while this transaction still owns the
                # first-creation decision. A recovery reuses this event marker.
                marker = identities.history("it", oec_id)
                prior_marker = next((event for event in marker if event["evidenceRef"] == reference + ":discovery-result"), None)
                created = prior_marker["payload"]["created"] if prior_marker else previous is None
                identities.observe_profile("it", oec_id, None, observed_at, reference + ":discovery-result", payload={"created": created, "scope": "identity_only" if identity_only else "current_discovery_only", "profileCollection": "not_requested" if identity_only else "attempted"})
            values = {"creator_id": current["creatorId"], "oec_id": oec_id, "request_count": count}
            if identity_only:
                return self.store.finish(item,self.owner,"completed",outcome="identity_only",**values)
            if complete:
                return self.store.finish(item, self.owner, "completed", outcome="created" if created else "existing", **values)
            return self._blocked(item, completion_error or ("probe_timeout" if report.get("status") == "bounded_timeout" else report.get("reason")), outcome="identity_only", **values)
        except CreatorDiscoveryError as error:
            if error.code == "stale_lease":
                return self.store.detail(item["batch_id"])
            return self._blocked(item, error.code, request_count=count)
        except Exception:
            return self._blocked(item, "identity_import_failed", request_count=count)

    def run_once(self):
        self.store.heartbeat(self.owner)
        item = self.store.claim(self.owner, recovery=True)
        recovering = item is not None
        item = item or self.store.claim(self.owner)
        if item is None:
            return None
        try:
            directory, output, target_file = self._paths(item)
            report = self._final(output / "report.private.json")
            if report:
                return self._settle(item, report)
            if recovering:
                return self._blocked(item, "lease_expired_no_report")
            if output.exists() or target_file.exists():
                return self._blocked(item, "attempt_exists_without_final")
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            with target_file.open("x", encoding="utf-8") as stream:
                target_file.chmod(0o600)
                json.dump({"market": "it", "identityOnly":self._identity_only_batch(item), "targets": [{"ref": item["id"], "handle": item["handle"], "externalId": item["id"]}]}, stream)
            self.executor(target_file, output)
            report = self._final(output / "report.private.json")
            return self._settle(item, report) if report else self._blocked(item, "probe_report_missing")
        except CreatorDiscoveryError as error:
            return self._blocked(item, error.code)
        except (KeyboardInterrupt, SystemExit):
            self._blocked(item, "worker_interrupted")
            raise
        except Exception as error:
            return self._blocked(item, getattr(error, "code", "probe_failed"))
