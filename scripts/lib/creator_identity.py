"""Durable creator identity facts, independent of matching and messaging stores.

OEC identifies a creator within a market. Handles are observations, never routing
keys. A pending handle lead is separate until a successful exact Find resolves
its current identity; an external source ID still remains an unproven reference.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import threading
import uuid


class IdentityConflict(ValueError):
    """The same immutable evidence key was submitted with different facts."""


_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_SCHEMA = """
CREATE TABLE IF NOT EXISTS identity_store_meta(version INTEGER PRIMARY KEY CHECK(version = 1));
INSERT OR IGNORE INTO identity_store_meta VALUES(1);
CREATE TABLE IF NOT EXISTS creator_identity(
    creator_id TEXT PRIMARY KEY, market TEXT NOT NULL, oec_id TEXT NOT NULL,
    current_handle TEXT, handle_observed_at TEXT, handle_observed_us INTEGER,
    handle_conflict INTEGER NOT NULL DEFAULT 0,
    last_observed_at TEXT NOT NULL, last_observed_us INTEGER NOT NULL,
    created_at TEXT NOT NULL, UNIQUE(market, oec_id)
);
CREATE TABLE IF NOT EXISTS identity_observation(
    event_id TEXT PRIMARY KEY, creator_id TEXT REFERENCES creator_identity(creator_id),
    market TEXT NOT NULL, oec_id TEXT NOT NULL, kind TEXT NOT NULL,
    handle TEXT, outcome TEXT NOT NULL, observed_at TEXT NOT NULL, observed_us INTEGER NOT NULL,
    evidence_ref TEXT NOT NULL, fingerprint TEXT NOT NULL, payload_json TEXT NOT NULL,
    UNIQUE(market, oec_id, evidence_ref, observed_at)
);
CREATE INDEX IF NOT EXISTS identity_alias_lookup ON identity_observation(market, handle, observed_us);
CREATE INDEX IF NOT EXISTS identity_history_lookup ON identity_observation(market, oec_id, observed_us);
CREATE TRIGGER IF NOT EXISTS identity_observation_no_update BEFORE UPDATE ON identity_observation
BEGIN SELECT RAISE(ABORT, 'identity observations are append-only'); END;
CREATE TRIGGER IF NOT EXISTS identity_observation_no_delete BEFORE DELETE ON identity_observation
BEGIN SELECT RAISE(ABORT, 'identity observations are append-only'); END;
CREATE TABLE IF NOT EXISTS handle_lead(
    lead_id TEXT PRIMARY KEY, market TEXT NOT NULL, handle TEXT NOT NULL,
    observed_at TEXT NOT NULL, evidence_ref TEXT NOT NULL, fingerprint TEXT NOT NULL,
    external_source TEXT, external_id TEXT, payload_json TEXT NOT NULL,
    UNIQUE(market, handle, evidence_ref, observed_at)
);
CREATE TABLE IF NOT EXISTS handle_lead_resolution(
    lead_id TEXT PRIMARY KEY REFERENCES handle_lead(lead_id),
    creator_id TEXT NOT NULL REFERENCES creator_identity(creator_id),
    observed_at TEXT NOT NULL, evidence_ref TEXT NOT NULL, fingerprint TEXT NOT NULL,
    exact_find_json TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS handle_lead_no_update BEFORE UPDATE ON handle_lead
BEGIN SELECT RAISE(ABORT, 'handle leads are append-only'); END;
CREATE TRIGGER IF NOT EXISTS handle_lead_no_delete BEFORE DELETE ON handle_lead
BEGIN SELECT RAISE(ABORT, 'handle leads are append-only'); END;
CREATE TRIGGER IF NOT EXISTS handle_resolution_no_update BEFORE UPDATE ON handle_lead_resolution
BEGIN SELECT RAISE(ABORT, 'handle resolutions are append-only'); END;
CREATE TRIGGER IF NOT EXISTS handle_resolution_no_delete BEFORE DELETE ON handle_lead_resolution
BEGIN SELECT RAISE(ABORT, 'handle resolutions are append-only'); END;
"""


def _market(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-z]{2}", value):
        raise ValueError("market must be a lowercase two-letter code")
    return value


def _oec(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{1,64}", value):
        raise ValueError("oec_id must be an exact ASCII decimal string")
    return value


def _handle(value, *, optional=False):
    if value is None and optional:
        return None
    if not isinstance(value, str):
        raise ValueError("handle must be a string")
    normalized = value.strip().removeprefix("@").lower()
    if not re.fullmatch(r"[a-z0-9._]{1,64}", normalized):
        raise ValueError("invalid handle")
    return normalized


def _text(value, name, *, optional=False):
    if value is None and optional:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > 2048 or any(ord(c) < 32 for c in value):
        raise ValueError(f"invalid {name}")
    return value


def _timestamp(value):
    if not isinstance(value, str):
        raise ValueError("observed_at must be an ISO timestamp with timezone")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError()
        utc = parsed.astimezone(timezone.utc)
    except (ValueError, OverflowError) as error:
        raise ValueError("observed_at must be an ISO timestamp with timezone") from error
    delta = utc - _EPOCH
    micros = (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds
    return utc.isoformat(timespec="microseconds").replace("+00:00", "Z"), micros


def _payload(value):
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise ValueError("payload must be a JSON object")
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as error:
        raise ValueError("payload must be a finite JSON object") from error


def _fingerprint(value):
    return hashlib.sha256(_payload(value).encode("utf-8")).hexdigest()


def _id(prefix):
    return prefix + uuid.uuid4().hex


class CreatorIdentityStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(self.path, timeout=10, isolation_level=None, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        try:
            tables = {r[0] for r in self._db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if tables and "identity_store_meta" not in tables:
                raise ValueError("database is not a creator identity store")
            if tables and [r[0] for r in self._db.execute("SELECT version FROM identity_store_meta")] != [1]:
                raise ValueError("unsupported identity store version")
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

    @staticmethod
    def _identity(row):
        if row is None:
            return None
        return {"creatorId": row["creator_id"], "market": row["market"], "oecId": row["oec_id"],
                "currentHandle": row["current_handle"], "currentHandleVerifiedAt": row["handle_observed_at"],
                "handleConflict": bool(row["handle_conflict"]), "lastObservedAt": row["last_observed_at"],
                "createdAt": row["created_at"]}

    def _get(self, market, oec_id):
        return self._db.execute("SELECT * FROM creator_identity WHERE market=? AND oec_id=?", (market, oec_id)).fetchone()

    def get_by_oec(self, market, oec_id):
        market, oec_id = _market(market), _oec(oec_id)
        with self._lock:
            return self._identity(self._get(market, oec_id))

    def refresh_target(self, market, oec_id, ref):
        """Build an OEC-only refresh route; optional handle is audit context."""
        ref = _text(ref, "ref")
        current = self.get_by_oec(market, oec_id)
        if current is None:
            raise LookupError("canonical creator not found for market and OEC")
        target = {"ref": ref, "oecId": current["oecId"], "externalId": current["creatorId"]}
        if current["currentHandle"] is not None:
            target["handle"] = current["currentHandle"]
        return target

    def stats(self):
        with self._lock:
            row = self._db.execute("""SELECT
                (SELECT COUNT(*) FROM creator_identity) AS identities,
                (SELECT COUNT(*) FROM identity_observation) AS observations,
                (SELECT COUNT(*) FROM handle_lead) AS leads,
                (SELECT COUNT(*) FROM handle_lead_resolution) AS resolved
                """).fetchone()
            return {"identities": row["identities"], "observations": row["observations"],
                    "leads": row["leads"], "pendingLeads": row["leads"] - row["resolved"],
                    "resolvedLeads": row["resolved"]}

    def _observe(self, market, oec_id, handle, observed_at, observed_us, evidence_ref, payload_json):
        fingerprint = _fingerprint({"kind": "profile", "handle": handle, "payload": json.loads(payload_json)})
        previous = self._db.execute(
            "SELECT fingerprint FROM identity_observation WHERE market=? AND oec_id=? AND evidence_ref=? AND observed_at=?",
            (market, oec_id, evidence_ref, observed_at)).fetchone()
        if previous is not None:
            if previous["fingerprint"] != fingerprint:
                raise IdentityConflict("observation evidence already exists with different facts")
            return self._identity(self._get(market, oec_id))
        row = self._get(market, oec_id)
        if row is None:
            self._db.execute(
                "INSERT INTO creator_identity(creator_id,market,oec_id,last_observed_at,last_observed_us,created_at) VALUES(?,?,?,?,?,?)",
                (_id("creator_"), market, oec_id, observed_at, observed_us, datetime.now(timezone.utc).isoformat()))
            row = self._get(market, oec_id)
        self._db.execute(
            "INSERT INTO identity_observation VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (_id("obs_"), row["creator_id"], market, oec_id, "profile", handle, "observed", observed_at,
             observed_us, evidence_ref, fingerprint, payload_json))
        if observed_us > row["last_observed_us"]:
            self._db.execute("UPDATE creator_identity SET last_observed_at=?,last_observed_us=? WHERE creator_id=?",
                             (observed_at, observed_us, row["creator_id"]))
        if handle is not None:
            if row["handle_observed_us"] is None or observed_us > row["handle_observed_us"]:
                self._db.execute(
                    "UPDATE creator_identity SET current_handle=?,handle_observed_at=?,handle_observed_us=?,handle_conflict=0 WHERE creator_id=?",
                    (handle, observed_at, observed_us, row["creator_id"]))
            elif observed_us == row["handle_observed_us"] and handle != row["current_handle"]:
                # Two contradictory facts at the same timestamp are retained;
                # arrival order must not silently overwrite a current name.
                self._db.execute("UPDATE creator_identity SET handle_conflict=1 WHERE creator_id=?", (row["creator_id"],))
        return self._identity(self._get(market, oec_id))

    def observe_profile(self, market, oec_id, handle, observed_at, evidence_ref, payload=None):
        """Record a successful OEC Profile/Find fact, never a failed response.

        evidence_ref must identify this source observation, not just a provider.
        Reuse with the same timestamp and different facts raises IdentityConflict.
        """
        market, oec_id, handle = _market(market), _oec(oec_id), _handle(handle, optional=True)
        observed_at, observed_us = _timestamp(observed_at)
        evidence_ref, payload_json = _text(evidence_ref, "evidence_ref"), _payload(payload)
        with self._transaction():
            return self._observe(market, oec_id, handle, observed_at, observed_us, evidence_ref, payload_json)

    def record_profile_failure(self, market, oec_id, observed_at, evidence_ref, outcome):
        """Retain failure evidence without creating, deleting or refreshing identity."""
        market, oec_id = _market(market), _oec(oec_id)
        observed_at, observed_us = _timestamp(observed_at)
        evidence_ref = _text(evidence_ref, "evidence_ref")
        if outcome not in {"unknown", "timeout", "not_found", "blocked", "error"}:
            raise ValueError("invalid failure outcome")
        fingerprint = _fingerprint({"kind": "failure", "outcome": outcome})
        with self._transaction():
            previous = self._db.execute(
                "SELECT fingerprint FROM identity_observation WHERE market=? AND oec_id=? AND evidence_ref=? AND observed_at=?",
                (market, oec_id, evidence_ref, observed_at)).fetchone()
            row = self._get(market, oec_id)
            if previous is not None:
                if previous["fingerprint"] != fingerprint:
                    raise IdentityConflict("observation evidence already exists with different facts")
            else:
                self._db.execute("INSERT INTO identity_observation VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (
                    _id("obs_"), row["creator_id"] if row else None, market, oec_id, "failure", None, outcome,
                    observed_at, observed_us, evidence_ref, fingerprint, "{}"))
            return self._identity(row)

    def history(self, market, oec_id):
        market, oec_id = _market(market), _oec(oec_id)
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM identity_observation WHERE market=? AND oec_id=? ORDER BY observed_us,event_id",
                (market, oec_id)).fetchall()
            return [{"eventId": row["event_id"], "creatorId": row["creator_id"], "kind": row["kind"],
                     "handle": row["handle"], "outcome": row["outcome"], "observedAt": row["observed_at"],
                     "evidenceRef": row["evidence_ref"], "fingerprint": row["fingerprint"],
                     "payload": json.loads(row["payload_json"])} for row in rows]

    def find_handle_candidates(self, market, handle):
        """Return historical candidates; never use this result as a refresh route."""
        market, handle = _market(market), _handle(handle)
        with self._lock:
            rows = self._db.execute("""
                SELECT i.*, MIN(e.observed_at) AS alias_first, MAX(e.observed_at) AS alias_last
                FROM identity_observation e JOIN creator_identity i ON i.creator_id=e.creator_id
                WHERE e.market=? AND e.handle=? AND e.kind='profile'
                GROUP BY i.creator_id ORDER BY alias_last DESC,i.creator_id
                """, (market, handle)).fetchall()
            return [{**self._identity(row), "alias": handle, "aliasFirstObservedAt": row["alias_first"],
                     "aliasLastObservedAt": row["alias_last"], "isCurrentHandle": row["current_handle"] == handle,
                     "routingAuthoritative": False} for row in rows]

    def record_handle_lead(self, market, handle, observed_at, evidence_ref, *, external_source=None, external_id=None, payload=None):
        market, handle = _market(market), _handle(handle)
        observed_at, _ = _timestamp(observed_at)
        evidence_ref = _text(evidence_ref, "evidence_ref")
        external_source = _text(external_source, "external_source", optional=True)
        external_id = _text(external_id, "external_id", optional=True)
        if (external_source is None) != (external_id is None):
            raise ValueError("external_source and external_id must be supplied together")
        payload_json = _payload(payload)
        fingerprint = _fingerprint({"externalSource": external_source, "externalId": external_id, "payload": json.loads(payload_json)})
        with self._transaction():
            previous = self._db.execute(
                "SELECT * FROM handle_lead WHERE market=? AND handle=? AND evidence_ref=? AND observed_at=?",
                (market, handle, evidence_ref, observed_at)).fetchone()
            if previous is not None:
                if previous["fingerprint"] != fingerprint:
                    raise IdentityConflict("lead evidence already exists with different facts")
                return self._lead(previous["lead_id"])
            lead_id = _id("lead_")
            self._db.execute("INSERT INTO handle_lead VALUES(?,?,?,?,?,?,?,?,?)", (
                lead_id, market, handle, observed_at, evidence_ref, fingerprint, external_source, external_id, payload_json))
            return self._lead(lead_id)

    def _lead(self, lead_id):
        row = self._db.execute("""SELECT l.*, r.creator_id, r.observed_at AS resolved_at
            FROM handle_lead l LEFT JOIN handle_lead_resolution r USING(lead_id) WHERE l.lead_id=?""", (lead_id,)).fetchone()
        if row is None:
            return None
        return {"leadId": row["lead_id"], "market": row["market"], "handle": row["handle"],
                "observedAt": row["observed_at"], "evidenceRef": row["evidence_ref"],
                "status": "resolved" if row["creator_id"] else "pending", "creatorId": row["creator_id"],
                "resolvedAt": row["resolved_at"], "externalSource": row["external_source"], "externalId": row["external_id"],
                "historicalCrossSourceIdentityProven": False}

    def get_handle_lead(self, lead_id):
        with self._lock:
            return self._lead(_text(lead_id, "lead_id"))

    def resolve_handle_lead(self, lead_id, oec_id, observed_at, evidence_ref, *, exact_find):
        """Resolve a current lead using an adapter's successful exact Find proof.

        exact_find keys: market, queriedHandle, returnedHandle, oecId,
        httpStatus=200, code=0, verificationRequired=False. This validates the
        adapter contract; the caller must supply actual observed platform facts.
        """
        lead_id, oec_id = _text(lead_id, "lead_id"), _oec(oec_id)
        observed_at, observed_us = _timestamp(observed_at)
        evidence_ref = _text(evidence_ref, "evidence_ref")
        if not isinstance(exact_find, dict) or set(exact_find) != {
            "market", "queriedHandle", "returnedHandle", "oecId", "httpStatus", "code", "verificationRequired"
        }:
            raise ValueError("complete exact Find evidence is required")
        if type(exact_find["httpStatus"]) is not int or exact_find["httpStatus"] != 200 or \
                type(exact_find["code"]) is not int or exact_find["code"] != 0 or exact_find["verificationRequired"] is not False:
            raise ValueError("Find did not confirm a successful unchallenged result")
        if _oec(exact_find["oecId"]) != oec_id:
            raise ValueError("Find OEC does not match requested identity")
        query, returned = _handle(exact_find["queriedHandle"]), _handle(exact_find["returnedHandle"])
        market = _market(exact_find["market"])
        normalized = {**exact_find, "queriedHandle": query, "returnedHandle": returned}
        fingerprint = _fingerprint({"observedAt": observed_at, "evidenceRef": evidence_ref, "exactFind": normalized})
        with self._transaction():
            lead = self._lead(lead_id)
            if lead is None:
                raise ValueError("unknown handle lead")
            if lead["market"] != market or lead["handle"] != query or query != returned:
                raise ValueError("Find did not return this lead's exact handle and market")
            if observed_us < _timestamp(lead["observedAt"])[1]:
                raise ValueError("Find evidence predates the handle lead")
            prior = self._db.execute("SELECT * FROM handle_lead_resolution WHERE lead_id=?", (lead_id,)).fetchone()
            if prior is not None:
                if prior["fingerprint"] != fingerprint:
                    raise IdentityConflict("resolved lead cannot be reassigned; record a new observation")
                return {"lead": lead, "identity": self._identity(self._get(market, oec_id))}
            identity = self._observe(market, oec_id, returned, observed_at, observed_us, evidence_ref, _payload({"exactFind": normalized}))
            self._db.execute("INSERT INTO handle_lead_resolution VALUES(?,?,?,?,?,?)", (
                lead_id, identity["creatorId"], observed_at, evidence_ref, fingerprint, _payload(normalized)))
            return {"lead": self._lead(lead_id), "identity": identity}
