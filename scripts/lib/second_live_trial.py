"""Frozen Italy second-outreach trials and fenced delivery intents.

This module has no transport, credential, policy-file, or model integration.
An adapter may perform an external operation only when begin_attempt returns
``dispatchAllowed=True``. Receipt candidates still require positive readback.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

MARKET = "it"
ACCOUNT = "acc6"
CAMPAIGN = "italy-second-pilot"
EXPIRY_SECONDS = 30 * 60
STAGES = ("create_conversation", "send_message", "send_card")
TERMINAL = ("confirmed", "failed_not_sent", "partial_delivery")
ITEM_FIELDS = {"opportunityId", "creatorId", "oecId", "handle", "draftId", "textIt", "translationZh", "contextFingerprint"}
TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$")
HASH = re.compile(r"^[0-9a-f]{64}$")


class LiveTrialError(ValueError):
    def __init__(self, code: str, status: int = 409):
        self.code, self.status = code, status
        super().__init__(code)


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(value):
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _text_hash(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _token(value):
    if not isinstance(value, str) or not TOKEN.fullmatch(value):
        raise LiveTrialError("invalid_input", 400)
    return value


def _fingerprint(value):
    if not isinstance(value, str) or not HASH.fullmatch(value):
        raise LiveTrialError("invalid_input", 400)
    return value


def _text(value, maximum=10000):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum or any(ord(char) < 32 and char not in "\n\r\t" for char in value):
        raise LiveTrialError("invalid_input", 400)
    return value


def _exact(value, fields):
    if not isinstance(value, dict) or set(value) != set(fields):
        raise LiveTrialError("invalid_input", 400)
    return value


def _iso(milliseconds):
    return datetime.fromtimestamp(milliseconds / 1000, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _input_item(value):
    _exact(value, ITEM_FIELDS | ({"cards"} if isinstance(value, dict) and "cards" in value else set()))
    if not isinstance(value["creatorId"], str) or not re.fullmatch(r"creator_[0-9a-f]{32}", value["creatorId"]):
        raise LiveTrialError("invalid_input", 400)
    if not isinstance(value["oecId"], str) or not re.fullmatch(r"[1-9][0-9]{0,63}", value["oecId"]):
        raise LiveTrialError("invalid_input", 400)
    if value["handle"] is not None and (not isinstance(value["handle"], str) or not re.fullmatch(r"@?[A-Za-z0-9._]{1,64}", value["handle"])):
        raise LiveTrialError("invalid_input", 400)
    result = {"opportunityId": _token(value["opportunityId"]), "creatorId": value["creatorId"], "oecId": value["oecId"],
            "handle": value["handle"], "draftId": _token(value["draftId"]), "textIt": _text(value["textIt"]),
            "translationZh": _text(value["translationZh"]), "contextFingerprint": _fingerprint(value["contextFingerprint"])}
    if "cards" in value:
        from lib.italy_im_delivery import ItalyVerifiedProductCard
        try:
            if not isinstance(value["cards"], list) or not 1 <= len(value["cards"]) <= 4:
                raise ValueError()
            cards = [asdict(ItalyVerifiedProductCard(**card)) for card in value["cards"]]
            if len({(card["product_id"], card["list_id"], card["campaign_id"]) for card in cards}) != len(cards):
                raise ValueError()
            result["cards"] = cards
        except Exception:
            raise LiveTrialError("invalid_cards", 400) from None
    return result


class LiveTrialStore:
    """Single-file store. Tests must pass an isolated temporary var_dir."""

    def __init__(self, var_dir, now=time.time):
        self.var_dir, self.now = Path(var_dir).resolve(), now
        self.var_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.var_dir / "second-live-trials.sqlite"
        self._lock = threading.RLock()
        self.db = sqlite3.connect(self.path, timeout=10, isolation_level=None, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA journal_mode=WAL")
        tables = {row[0] for row in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if tables and "live_trial_meta" not in tables:
            self.close()
            raise LiveTrialError("unsupported_schema", 503)
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS live_trial_meta(version INTEGER NOT NULL);
            INSERT INTO live_trial_meta SELECT 1 WHERE NOT EXISTS(SELECT 1 FROM live_trial_meta);
            CREATE TABLE IF NOT EXISTS live_trial(
                id TEXT PRIMARY KEY, request_id TEXT NOT NULL UNIQUE, request_hash TEXT NOT NULL,
                snapshot_json TEXT NOT NULL, snapshot_hash TEXT NOT NULL, created_at INTEGER NOT NULL,
                expires_at INTEGER NOT NULL, approved_at INTEGER, paused_at INTEGER);
            CREATE TABLE IF NOT EXISTS live_trial_item(
                id TEXT PRIMARY KEY, trial_id TEXT NOT NULL REFERENCES live_trial(id), position INTEGER NOT NULL,
                item_json TEXT NOT NULL, oec_id TEXT NOT NULL, state TEXT NOT NULL,
                conversation_id TEXT, receipt_json TEXT, confirmed_json TEXT, error_code TEXT,
                requires_reconciliation INTEGER NOT NULL DEFAULT 0, unknown_stage TEXT,
                lease_owner TEXT, lease_until INTEGER, fence INTEGER NOT NULL DEFAULT 0,
                UNIQUE(trial_id,position), UNIQUE(trial_id,oec_id));
            CREATE TABLE IF NOT EXISTS live_trial_attempt(
                id TEXT PRIMARY KEY, item_id TEXT NOT NULL REFERENCES live_trial_item(id), stage TEXT NOT NULL,
                request_ref TEXT NOT NULL UNIQUE, fence INTEGER NOT NULL, state TEXT NOT NULL,
                started_at INTEGER NOT NULL, finished_at INTEGER, result_json TEXT, error_code TEXT,
                UNIQUE(item_id,stage));
            CREATE TABLE IF NOT EXISTS live_campaign_recipient(
                campaign_id TEXT NOT NULL, oec_id TEXT NOT NULL, item_id TEXT NOT NULL UNIQUE REFERENCES live_trial_item(id),
                state TEXT NOT NULL, PRIMARY KEY(campaign_id,oec_id));
            CREATE TABLE IF NOT EXISTS live_trial_component(
                id TEXT PRIMARY KEY, item_id TEXT NOT NULL REFERENCES live_trial_item(id),
                position INTEGER NOT NULL, kind TEXT NOT NULL, stage TEXT NOT NULL,
                frozen_json TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending',
                attempt_id TEXT UNIQUE REFERENCES live_trial_attempt(id), receipt_json TEXT,
                confirmed_json TEXT, error_code TEXT,
                UNIQUE(item_id,position), UNIQUE(item_id,stage));
            CREATE TABLE IF NOT EXISTS live_trial_event(
                id INTEGER PRIMARY KEY AUTOINCREMENT, trial_id TEXT NOT NULL REFERENCES live_trial(id),
                item_id TEXT, kind TEXT NOT NULL, at INTEGER NOT NULL, data TEXT NOT NULL);
            CREATE TRIGGER IF NOT EXISTS live_snapshot_immutable BEFORE UPDATE OF
                id,request_id,request_hash,snapshot_json,snapshot_hash,created_at,expires_at ON live_trial
                BEGIN SELECT RAISE(ABORT,'immutable_trial_snapshot'); END;
            CREATE TRIGGER IF NOT EXISTS live_item_immutable BEFORE UPDATE OF
                id,trial_id,position,item_json,oec_id ON live_trial_item
                BEGIN SELECT RAISE(ABORT,'immutable_trial_item'); END;
            CREATE TRIGGER IF NOT EXISTS live_event_immutable BEFORE UPDATE ON live_trial_event
                BEGIN SELECT RAISE(ABORT,'immutable_trial_event'); END;
            CREATE TRIGGER IF NOT EXISTS live_event_no_delete BEFORE DELETE ON live_trial_event
                BEGIN SELECT RAISE(ABORT,'immutable_trial_event'); END;
            CREATE TRIGGER IF NOT EXISTS live_component_immutable BEFORE UPDATE OF
                id,item_id,position,kind,stage,frozen_json ON live_trial_component
                BEGIN SELECT RAISE(ABORT,'immutable_trial_component'); END;
        """)
        if [row[0] for row in self.db.execute("SELECT version FROM live_trial_meta")] != [1]:
            self.close()
            raise LiveTrialError("unsupported_schema", 503)

    def close(self):
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def _now(self):
        value = self.now()
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            raise LiveTrialError("clock_invalid", 503)
        return int(value * 1000)

    @contextmanager
    def _transaction(self, write=True):
        with self._lock:
            self.db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            try:
                yield
                self.db.execute("COMMIT")
            except BaseException:
                self.db.execute("ROLLBACK")
                raise

    def _trial(self, trial_id):
        row = self.db.execute("SELECT * FROM live_trial WHERE id=?", (_token(trial_id),)).fetchone()
        if row is None:
            raise LiveTrialError("trial_not_found", 404)
        return row

    def _item(self, item_id):
        row = self.db.execute("SELECT * FROM live_trial_item WHERE id=?", (_token(item_id),)).fetchone()
        if row is None:
            raise LiveTrialError("item_not_found", 404)
        return row

    def _event(self, trial_id, item_id, kind, data):
        self.db.execute("INSERT INTO live_trial_event(trial_id,item_id,kind,at,data) VALUES(?,?,?,?,?)",
                        (trial_id, item_id, kind, self._now(), _canonical(data)))

    def _guard(self, item, state):
        guard = self.db.execute("SELECT * FROM live_campaign_recipient WHERE campaign_id=? AND oec_id=?", (CAMPAIGN, item["oec_id"])).fetchone()
        if guard is None or guard["item_id"] != item["id"]:
            raise LiveTrialError("recipient_not_reserved")
        self.db.execute("UPDATE live_campaign_recipient SET state=? WHERE item_id=?", (state, item["id"]))

    def _release_not_sent(self, item):
        if self.db.execute("SELECT 1 FROM live_trial_component WHERE item_id=? AND state IN ('confirmed','accepted_candidate','inflight','result_unknown')", (item["id"],)).fetchone():
            return
        self.db.execute("DELETE FROM live_campaign_recipient WHERE item_id=? AND state NOT IN ('confirmed','result_unknown')", (item["id"],))

    def _components(self, item_id):
        return list(self.db.execute("SELECT * FROM live_trial_component WHERE item_id=? ORDER BY position", (item_id,)))

    def _component(self, item_id, component_id):
        row = self.db.execute("SELECT * FROM live_trial_component WHERE item_id=? AND id=?", (item_id, _token(component_id))).fetchone()
        if row is None:
            raise LiveTrialError("component_mismatch")
        return row

    def _partial(self, item_id):
        return self.db.execute("SELECT 1 FROM live_trial_component WHERE item_id=? AND kind='card' AND state='confirmed'", (item_id,)).fetchone() is not None

    def _public_component(self, component):
        frozen = json.loads(component["frozen_json"])
        attempt = self.db.execute("SELECT request_ref FROM live_trial_attempt WHERE id=?", (component["attempt_id"],)).fetchone() if component["attempt_id"] else None
        return {"componentId": component["id"], "componentKind": component["kind"], "position": component["position"],
                **frozen, "state": component["state"], "attemptId": component["attempt_id"],
                "requestRef": attempt["request_ref"] if attempt else None,
                "sendReceipt": json.loads(component["receipt_json"]) if component["receipt_json"] else None,
                "confirmation": json.loads(component["confirmed_json"]) if component["confirmed_json"] else None,
                "errorCode": component["error_code"]}

    def _blockers(self):
        # An expired in-flight lease blocks dispatch even before recovery writes it as unknown.
        return self.db.execute("""SELECT DISTINCT i.id,i.trial_id FROM live_trial_item i
            JOIN live_campaign_recipient g ON g.item_id=i.id WHERE g.campaign_id=? AND
            (i.state='result_unknown' OR EXISTS(SELECT 1 FROM live_trial_attempt a WHERE a.item_id=i.id
            AND a.state='inflight' AND i.lease_until<=?)) ORDER BY i.trial_id,i.position""", (CAMPAIGN, self._now())).fetchall()

    def _lease(self, item_id, owner, fence):
        item = self._item(item_id)
        _token(owner)
        if type(fence) is not int or fence < 1 or item["lease_owner"] != owner or item["fence"] != fence or item["lease_until"] is None or item["lease_until"] <= self._now():
            raise LiveTrialError("stale_lease")
        return item

    def _dispatch_allowed(self, item):
        trial = self._trial(item["trial_id"])
        if trial["approved_at"] is None:
            raise LiveTrialError("approval_required", 403)
        if trial["expires_at"] <= self._now():
            raise LiveTrialError("trial_expired")
        if trial["paused_at"] is not None:
            raise LiveTrialError("trial_paused")
        if self._blockers():
            raise LiveTrialError("campaign_result_unknown")
        guard = self.db.execute("SELECT item_id FROM live_campaign_recipient WHERE campaign_id=? AND oec_id=?", (CAMPAIGN, item["oec_id"])).fetchone()
        if guard is None or guard["item_id"] != item["id"]:
            raise LiveTrialError("recipient_not_reserved")
        if item["requires_reconciliation"]:
            raise LiveTrialError("readback_required")

    def _public_item(self, item):
        frozen = json.loads(item["item_json"])
        attempts = [self._public_attempt(row, False) for row in self.db.execute("SELECT * FROM live_trial_attempt WHERE item_id=? ORDER BY started_at,id", (item["id"],))]
        components = [self._public_component(row) for row in self._components(item["id"])]
        return {**frozen, "state": item["state"], "conversationId": item["conversation_id"],
                "sendReceipt": json.loads(item["receipt_json"]) if item["receipt_json"] else None,
                "confirmation": json.loads(item["confirmed_json"]) if item["confirmed_json"] else None,
                "errorCode": item["error_code"], "requiresReconciliation": bool(item["requires_reconciliation"]),
                "unknownStage": item["unknown_stage"], "leaseOwner": item["lease_owner"], "fence": item["fence"],
                "leaseUntil": _iso(item["lease_until"]) if item["lease_until"] is not None else None,
                "attempts": attempts, "components": components, "partialDelivery": self._partial(item["id"]) and item["state"] != "confirmed"}

    def _public_attempt(self, attempt, dispatch_allowed):
        component = self.db.execute("SELECT * FROM live_trial_component WHERE attempt_id=?", (attempt["id"],)).fetchone()
        scope = {}
        if component is not None:
            scope = {"componentId": component["id"], "componentKind": component["kind"]}
            if component["kind"] == "card":
                card = json.loads(component["frozen_json"])["card"]
                scope.update(productId=card["product_id"], listId=card["list_id"], campaignId=card["campaign_id"], bindingSha256=card["binding_sha256"])
        elif attempt["stage"] == "send_message":
            scope = {"componentKind": "text"}
        return {"attemptId": attempt["id"], "itemId": attempt["item_id"], "stage": "send_card" if attempt["stage"].startswith("send_card:") else attempt["stage"],
                **scope,
                "requestRef": attempt["request_ref"], "state": attempt["state"], "fence": attempt["fence"],
                "dispatchAllowed": dispatch_allowed, "startedAt": _iso(attempt["started_at"]),
                "finishedAt": _iso(attempt["finished_at"]) if attempt["finished_at"] is not None else None,
                "result": json.loads(attempt["result_json"]) if attempt["result_json"] else None, "errorCode": attempt["error_code"]}

    def _public_trial(self, trial):
        items = [self._public_item(row) for row in self.db.execute("SELECT * FROM live_trial_item WHERE trial_id=? ORDER BY position", (trial["id"],))]
        blockers = [{"trialId": row["trial_id"], "itemId": row["id"]} for row in self._blockers()]
        return {"trialId": trial["id"], "snapshot": json.loads(trial["snapshot_json"]), "snapshotHash": trial["snapshot_hash"],
                "approved": trial["approved_at"] is not None, "approvedAt": _iso(trial["approved_at"]) if trial["approved_at"] is not None else None,
                "paused": trial["paused_at"] is not None, "expired": trial["expires_at"] <= self._now(),
                "expiresAt": _iso(trial["expires_at"]), "items": items, "blockedByUnknown": blockers,
                "complete": all(item["state"] in TERMINAL for item in items),
                "events": [{"id": row["id"], "itemId": row["item_id"], "kind": row["kind"], "at": _iso(row["at"]), "data": json.loads(row["data"])}
                           for row in self.db.execute("SELECT * FROM live_trial_event WHERE trial_id=? ORDER BY id", (trial["id"],))]}

    def get_trial(self, trial_id: str) -> dict:
        with self._transaction(write=False):
            return self._public_trial(self._trial(trial_id))

    def create_trial(self, request_id: str, items: list[dict], source_fingerprint: str) -> dict:
        request_id, source_fingerprint = _token(request_id), _fingerprint(source_fingerprint)
        if not isinstance(items, list) or not 1 <= len(items) <= 3:
            raise LiveTrialError("invalid_input", 400)
        original = [_input_item(item) for item in items]
        if len({item["oecId"] for item in original}) != len(original) or len({item["creatorId"] for item in original}) != len(original):
            raise LiveTrialError("duplicate_recipient", 400)
        request_hash = _hash({"items": original, "sourceFingerprint": source_fingerprint})
        with self._transaction():
            existing = self.db.execute("SELECT * FROM live_trial WHERE request_id=?", (request_id,)).fetchone()
            if existing is not None:
                if existing["request_hash"] != request_hash:
                    raise LiveTrialError("request_conflict")
                return self._public_trial(existing)
            created = self._now()
            expires = created + EXPIRY_SECONDS * 1000
            trial_id = "second_live_trial_" + uuid.uuid4().hex
            frozen = [{**item, "itemId": "second_live_item_" + uuid.uuid4().hex, "textSha256": _text_hash(item["textIt"])} for item in original]
            snapshot = {"trialId": trial_id, "requestId": request_id, "market": MARKET, "account": ACCOUNT, "campaignId": CAMPAIGN,
                        "sourceFingerprint": source_fingerprint, "createdAt": _iso(created), "expiresAt": _iso(expires), "items": frozen}
            snapshot_hash = _hash(snapshot)
            self.db.execute("INSERT INTO live_trial VALUES(?,?,?,?,?,?,?,NULL,NULL)", (trial_id, request_id, request_hash, _canonical(snapshot), snapshot_hash, created, expires))
            for position, item in enumerate(frozen):
                self.db.execute("INSERT INTO live_trial_item(id,trial_id,position,item_json,oec_id,state) VALUES(?,?,?,?,?,'pending')", (item["itemId"], trial_id, position, _canonical(item), item["oecId"]))
                if "cards" in item:
                    from lib.italy_im_delivery import CARD_CONTENT
                    plans = [("card", "send_card:" + str(index), {"card": card, "textSha256": _text_hash(CARD_CONTENT)}) for index, card in enumerate(item["cards"])]
                    plans.append(("text", "send_message", {"textSha256": item["textSha256"]}))
                    for index, (kind, stage, content) in enumerate(plans):
                        component_id = item["itemId"] + ":" + ("card:" + str(index) if kind == "card" else "text")
                        self.db.execute("INSERT INTO live_trial_component(id,item_id,position,kind,stage,frozen_json) VALUES(?,?,?,?,?,?)",
                                        (component_id, item["itemId"], index, kind, stage, _canonical(content)))
            self._event(trial_id, None, "frozen", {"snapshotHash": snapshot_hash})
            return self._public_trial(self._trial(trial_id))

    def approve(self, trial_id: str, expected_hash: str) -> dict:
        _fingerprint(expected_hash)
        with self._transaction():
            trial = self._trial(trial_id)
            if trial["snapshot_hash"] != expected_hash:
                raise LiveTrialError("snapshot_mismatch")
            if trial["approved_at"] is not None:
                return self._public_trial(trial)
            if trial["expires_at"] <= self._now():
                raise LiveTrialError("trial_expired")
            if trial["paused_at"] is not None:
                raise LiveTrialError("trial_paused")
            rows = list(self.db.execute("SELECT * FROM live_trial_item WHERE trial_id=? ORDER BY position", (trial_id,)))
            for item in rows:
                guard = self.db.execute("SELECT item_id FROM live_campaign_recipient WHERE campaign_id=? AND oec_id=?", (CAMPAIGN, item["oec_id"])).fetchone()
                if guard is not None and guard["item_id"] != item["id"]:
                    raise LiveTrialError("campaign_recipient_conflict")
            for item in rows:
                self.db.execute("INSERT INTO live_campaign_recipient VALUES(?,?,?,?)", (CAMPAIGN, item["oec_id"], item["id"], "pending"))
            self.db.execute("UPDATE live_trial SET approved_at=? WHERE id=?", (self._now(), trial_id))
            self._event(trial_id, None, "approved", {"snapshotHash": expected_hash})
            return self._public_trial(self._trial(trial_id))

    def _unknown(self, item, attempt, code):
        if attempt is not None and attempt["state"] == "inflight":
            self.db.execute("UPDATE live_trial_attempt SET state='result_unknown',finished_at=?,error_code=? WHERE id=?", (self._now(), code, attempt["id"]))
        stage = attempt["stage"] if attempt is not None else item["unknown_stage"]
        if attempt is not None:
            self.db.execute("UPDATE live_trial_component SET state='result_unknown',error_code=? WHERE attempt_id=? AND state!='confirmed'", (code, attempt["id"]))
        self.db.execute("UPDATE live_trial_item SET state='result_unknown',unknown_stage=?,requires_reconciliation=1,error_code=? WHERE id=?", (stage, code, item["id"]))
        self._guard(item, "result_unknown")
        self._event(item["trial_id"], item["id"], "result_unknown", {"stage": stage, "code": code})

    def _recover_expired(self):
        rows = list(self.db.execute("SELECT * FROM live_trial_item WHERE lease_owner IS NOT NULL AND lease_until<=? AND state NOT IN ('confirmed','failed_not_sent','partial_delivery')", (self._now(),)))
        for item in rows:
            attempt = self.db.execute("SELECT * FROM live_trial_attempt WHERE item_id=? AND state='inflight'", (item["id"],)).fetchone()
            if attempt is not None:
                self._unknown(item, attempt, "worker_lease_expired")
            elif item["state"] in ("conversation_ready", "accepted_candidate"):
                self.db.execute("UPDATE live_trial_item SET requires_reconciliation=1 WHERE id=?", (item["id"],))
                self._event(item["trial_id"], item["id"], "readback_required", {"state": item["state"]})
            self.db.execute("UPDATE live_trial_item SET lease_owner=NULL,lease_until=NULL WHERE id=?", (item["id"],))

    def claim(self, trial_id: str, owner: str, lease_seconds: int = 90) -> dict | None:
        _token(owner)
        if type(lease_seconds) is not int or not 1 <= lease_seconds <= 900:
            raise LiveTrialError("invalid_input", 400)
        with self._transaction():
            trial = self._trial(trial_id)
            self._recover_expired()
            rows = list(self.db.execute("SELECT * FROM live_trial_item WHERE trial_id=? AND state NOT IN ('confirmed','failed_not_sent','partial_delivery') ORDER BY position", (trial_id,)))
            if not rows or trial["approved_at"] is None:
                return None
            item = next((row for row in rows if row["state"] == "result_unknown"), rows[0])
            read_only = item["state"] in ("result_unknown", "accepted_candidate") or bool(item["requires_reconciliation"])
            if not read_only and (trial["paused_at"] is not None or trial["expires_at"] <= self._now() or self._blockers()):
                return None
            if item["lease_owner"] is not None and item["lease_until"] > self._now() and item["lease_owner"] != owner:
                return None
            if item["lease_owner"] != owner or item["lease_until"] is None or item["lease_until"] <= self._now():
                self.db.execute("UPDATE live_trial_item SET lease_owner=?,lease_until=?,fence=fence+1 WHERE id=?", (owner, self._now() + lease_seconds * 1000, item["id"]))
                self._event(trial_id, item["id"], "claimed", {"owner": owner, "readOnly": read_only})
                item = self._item(item["id"])
            action = "verify_message" if item["state"] == "accepted_candidate" or item["unknown_stage"] == "send_message" else "verify_conversation" if read_only else "create_conversation" if item["state"] == "pending" else "send_message" if item["state"] == "conversation_ready" else "await_result"
            components = self._components(item["id"])
            component = next((row for row in components if row["state"] != "confirmed"), None)
            if component is not None and item["conversation_id"]:
                if component["state"] in ("accepted_candidate", "result_unknown"):
                    action = "verify_card" if component["kind"] == "card" else "verify_message"
                elif component["state"] == "inflight":
                    action = "await_result"
                elif not read_only and item["state"] == "conversation_ready":
                    action = "send_card" if component["kind"] == "card" else "send_message"
            return {"trialId": trial_id, "itemId": item["id"], "owner": owner, "fence": item["fence"], "leaseUntil": _iso(item["lease_until"]),
                    "nextAction": action, "readOnly": read_only, "snapshotHash": trial["snapshot_hash"], "item": self._public_item(item),
                    "component": self._public_component(component) if component is not None else None}

    def begin_attempt(self, item_id: str, owner: str, fence: int, stage: str, request_ref: str, *, component_id: str | None = None) -> dict:
        if stage not in STAGES:
            raise LiveTrialError("invalid_input", 400)
        _token(request_ref)
        with self._transaction():
            item = self._item(item_id)
            self._dispatch_allowed(item)  # Approval and expiry are checked again here, not only in claim().
            item = self._lease(item_id, owner, fence)
            if stage != "create_conversation" and self._components(item_id):
                return self._begin_component(item, stage, request_ref, component_id)
            if stage == "send_card" or component_id is not None:
                raise LiveTrialError("component_mismatch")
            existing = self.db.execute("SELECT * FROM live_trial_attempt WHERE item_id=? AND stage=?", (item_id, stage)).fetchone()
            if existing is not None:
                if existing["request_ref"] != request_ref:
                    raise LiveTrialError("request_conflict")
                return self._public_attempt(existing, False)
            expected = "pending" if stage == "create_conversation" else "conversation_ready"
            if item["state"] != expected or stage == "send_message" and not item["conversation_id"]:
                raise LiveTrialError("invalid_stage")
            if self.db.execute("SELECT 1 FROM live_trial_attempt WHERE request_ref=?", (request_ref,)).fetchone():
                raise LiveTrialError("request_conflict")
            attempt_id = "second_live_attempt_" + uuid.uuid4().hex
            self.db.execute("INSERT INTO live_trial_attempt(id,item_id,stage,request_ref,fence,state,started_at) VALUES(?,?,?,?,?,'inflight',?)", (attempt_id, item_id, stage, request_ref, fence, self._now()))
            state = "creating_conversation" if stage == "create_conversation" else "submitting"
            self.db.execute("UPDATE live_trial_item SET state=?,error_code=NULL WHERE id=?", (state, item_id))
            self._guard(item, state)
            self._event(item["trial_id"], item_id, "attempt_started", {"attemptId": attempt_id, "stage": stage, "requestRef": request_ref})
            return self._public_attempt(self.db.execute("SELECT * FROM live_trial_attempt WHERE id=?", (attempt_id,)).fetchone(), True)

    def _begin_component(self, item, stage, request_ref, component_id):
        components = self._components(item["id"])
        if stage == "send_card" and component_id is None:
            raise LiveTrialError("component_required", 400)
        component = self._component(item["id"], component_id) if component_id is not None else next(row for row in components if row["kind"] == "text")
        if component["kind"] != ("card" if stage == "send_card" else "text"):
            raise LiveTrialError("component_mismatch")
        existing = self.db.execute("SELECT * FROM live_trial_attempt WHERE item_id=? AND stage=?", (item["id"], component["stage"])).fetchone()
        if existing is not None:
            if existing["request_ref"] != request_ref:
                raise LiveTrialError("request_conflict")
            return self._public_attempt(existing, False)
        if any(row["position"] < component["position"] and row["state"] != "confirmed" for row in components):
            raise LiveTrialError("component_dependency_unconfirmed")
        if item["state"] != "conversation_ready" or not item["conversation_id"] or component["state"] != "pending":
            raise LiveTrialError("invalid_stage")
        if self.db.execute("SELECT 1 FROM live_trial_attempt WHERE request_ref=?", (request_ref,)).fetchone():
            raise LiveTrialError("request_conflict")
        attempt_id = "second_live_attempt_" + uuid.uuid4().hex
        self.db.execute("INSERT INTO live_trial_attempt(id,item_id,stage,request_ref,fence,state,started_at) VALUES(?,?,?,?,?,'inflight',?)",
                        (attempt_id, item["id"], component["stage"], request_ref, item["fence"], self._now()))
        self.db.execute("UPDATE live_trial_component SET state='inflight',attempt_id=?,error_code=NULL WHERE id=?", (attempt_id, component["id"]))
        self.db.execute("UPDATE live_trial_item SET state='submitting',receipt_json=NULL,error_code=NULL WHERE id=?", (item["id"],))
        self._guard(item, "submitting")
        self._event(item["trial_id"], item["id"], "attempt_started", {"attemptId": attempt_id, "stage": stage, "componentId": component["id"], "requestRef": request_ref})
        return self._public_attempt(self.db.execute("SELECT * FROM live_trial_attempt WHERE id=?", (attempt_id,)).fetchone(), True)

    def _attempt(self, item, attempt_id, stage=None):
        row = self.db.execute("SELECT * FROM live_trial_attempt WHERE id=? AND item_id=?", (_token(attempt_id), item["id"])).fetchone()
        if row is None or stage is not None and row["stage"] != stage:
            raise LiveTrialError("attempt_mismatch")
        if row["fence"] != item["fence"]:
            raise LiveTrialError("stale_attempt")
        return row

    def record_conversation(self, item_id: str, owner: str, fence: int, attempt_id: str, conversation_id: str, evidence_ref: str, *, verified: bool = True) -> dict:
        """Persist create response before optional CID/OEC verification can fail.

        A candidate CID is recoverable evidence only; confirm() must supply the
        positive conversation readback before any message component can start.
        """
        if type(verified) is not bool:
            raise LiveTrialError("invalid_input", 400)
        result = {"conversationId": _token(conversation_id), "evidenceRef": _text(evidence_ref, 512)}
        if not verified:result["verified"] = False
        with self._transaction():
            item = self._lease(item_id, owner, fence)
            attempt = self._attempt(item, attempt_id, "create_conversation")
            if attempt["result_json"] is not None:
                if attempt["result_json"] != _canonical(result):
                    raise LiveTrialError("receipt_conflict")
                return self._public_item(item)
            if item["state"] != "creating_conversation" or attempt["state"] != "inflight":
                raise LiveTrialError("invalid_stage")
            self.db.execute("UPDATE live_trial_attempt SET state='completed',finished_at=?,result_json=? WHERE id=?", (self._now(), _canonical(result), attempt_id))
            self.db.execute("UPDATE live_trial_item SET state='conversation_ready',conversation_id=?,requires_reconciliation=?,error_code=NULL WHERE id=?", (conversation_id, int(not verified), item_id))
            self._guard(item, "conversation_ready")
            if verified and self._trial(item["trial_id"])["paused_at"] is not None:
                self.db.execute("UPDATE live_trial_item SET state='failed_not_sent',error_code='paused_before_send' WHERE id=?", (item_id,))
                self._guard(item, "failed_not_sent")
                self._release_not_sent(item)
            self._event(item["trial_id"], item_id, "conversation_recorded", result)
            return self._public_item(self._item(item_id))

    def mark_verification_unresolved(self, item_id: str, owner: str, fence: int, code: str) -> dict:
        """A current reader may record uncertainty about a prior fenced attempt.

        Preserve the original writer's fence, receipt and result. This grants no
        dispatch or retry right and deliberately works while paused or expired.
        """
        if not isinstance(code, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", code):
            raise LiveTrialError("invalid_input", 400)
        with self._transaction():
            item = self._lease(item_id, owner, fence)
            if item["state"] in TERMINAL or not (item["requires_reconciliation"] or item["state"] in ("accepted_candidate", "result_unknown")):
                raise LiveTrialError("verification_not_pending")
            component = next((row for row in self._components(item_id) if row["state"] != "confirmed"), None)
            if component is not None and component["state"] in ("accepted_candidate", "result_unknown"):
                attempt = self.db.execute("SELECT * FROM live_trial_attempt WHERE id=? AND item_id=?", (component["attempt_id"], item_id)).fetchone()
            else:
                stage = "send_message" if item["state"] == "accepted_candidate" or item["unknown_stage"] == "send_message" else "create_conversation"
                attempt = self.db.execute("SELECT * FROM live_trial_attempt WHERE item_id=? AND stage=?", (item_id, stage)).fetchone()
            if attempt is None:
                raise LiveTrialError("attempt_mismatch")
            if item["state"] == "result_unknown" and item["unknown_stage"] == attempt["stage"] and item["error_code"] == code:
                return self._public_item(item)
            self._unknown(item, attempt, code)
            return self._public_item(self._item(item_id))

    def record_send_receipt(self, item_id: str, owner: str, fence: int, attempt_id: str, receipt: dict) -> dict:
        _exact(receipt, {"conversationId", "requestRef", "messageId", "accepted", "evidenceRef"})
        if receipt["accepted"] is not True:
            raise LiveTrialError("invalid_receipt", 400)
        result = {"conversationId": _token(receipt["conversationId"]), "requestRef": _token(receipt["requestRef"]),
                  "messageId": _token(receipt["messageId"]) if receipt["messageId"] is not None else None,
                  "accepted": True, "evidenceRef": _text(receipt["evidenceRef"], 512)}
        with self._transaction():
            item = self._lease(item_id, owner, fence)
            attempt = self._attempt(item, attempt_id)
            component = self.db.execute("SELECT * FROM live_trial_component WHERE attempt_id=?", (attempt_id,)).fetchone()
            if component is not None:
                return self._record_component(item, attempt, component, result)
            if attempt["stage"] != "send_message":
                raise LiveTrialError("attempt_mismatch")
            if result["conversationId"] != item["conversation_id"] or result["requestRef"] != attempt["request_ref"]:
                raise LiveTrialError("receipt_mismatch")
            if attempt["result_json"] is not None:
                if attempt["result_json"] != _canonical(result):
                    raise LiveTrialError("receipt_conflict")
                return self._public_item(item)
            if item["state"] != "submitting" or attempt["state"] != "inflight":
                raise LiveTrialError("invalid_stage")
            self.db.execute("UPDATE live_trial_attempt SET state='completed',finished_at=?,result_json=? WHERE id=?", (self._now(), _canonical(result), attempt_id))
            self.db.execute("UPDATE live_trial_item SET state='accepted_candidate',receipt_json=?,error_code=NULL WHERE id=?", (_canonical(result), item_id))
            self._guard(item, "accepted_candidate")
            self._event(item["trial_id"], item_id, "receipt_candidate", result)
            return self._public_item(self._item(item_id))

    def _record_component(self, item, attempt, component, result):
        if result["conversationId"] != item["conversation_id"] or result["requestRef"] != attempt["request_ref"]:
            raise LiveTrialError("receipt_mismatch")
        if attempt["result_json"] is not None:
            if attempt["result_json"] != _canonical(result):
                raise LiveTrialError("receipt_conflict")
            return self._public_item(item)
        if item["state"] != "submitting" or attempt["state"] != "inflight" or component["state"] != "inflight":
            raise LiveTrialError("invalid_stage")
        self.db.execute("UPDATE live_trial_attempt SET state='completed',finished_at=?,result_json=? WHERE id=?", (self._now(), _canonical(result), attempt["id"]))
        self.db.execute("UPDATE live_trial_component SET state='accepted_candidate',receipt_json=?,error_code=NULL WHERE id=?", (_canonical(result), component["id"]))
        self.db.execute("UPDATE live_trial_item SET state='accepted_candidate',receipt_json=?,error_code=NULL WHERE id=?", (_canonical(result), item["id"]))
        self._guard(item, "accepted_candidate")
        self._event(item["trial_id"], item["id"], "receipt_candidate", {"componentId": component["id"], **result})
        return self._public_item(self._item(item["id"]))

    def confirm(self, item_id: str, owner: str, fence: int, evidence: dict) -> dict:
        if not isinstance(evidence, dict) or evidence.get("kind") not in ("conversation_confirmed", "message_confirmed", "card_confirmed"):
            raise LiveTrialError("invalid_evidence", 400)
        fields = {"kind", "conversationId", "recipientOecId", "evidenceRef"}
        if evidence["kind"] in ("message_confirmed", "card_confirmed"):
            fields |= {"requestRef", "messageId", "textSha256"}
        if evidence["kind"] == "card_confirmed":
            fields |= {"componentId", "productId", "listId", "campaignId", "bindingSha256"}
        _exact(evidence, fields)
        _token(evidence["conversationId"])
        _text(evidence["evidenceRef"], 512)
        with self._transaction():
            item = self._lease(item_id, owner, fence)
            if evidence["recipientOecId"] != item["oec_id"] or item["conversation_id"] and evidence["conversationId"] != item["conversation_id"]:
                raise LiveTrialError("evidence_mismatch")
            if evidence["kind"] != "conversation_confirmed" and self._components(item_id):
                return self._confirm_component(item, evidence)
            if evidence["kind"] == "card_confirmed":
                raise LiveTrialError("component_mismatch")
            if evidence["kind"] == "conversation_confirmed":
                if item["state"] == "result_unknown" and item["unknown_stage"] != "create_conversation" or item["state"] not in ("result_unknown", "conversation_ready"):
                    raise LiveTrialError("invalid_stage")
                if self.db.execute("SELECT 1 FROM live_trial_attempt WHERE item_id=? AND stage='send_message'", (item_id,)).fetchone():
                    raise LiveTrialError("invalid_stage")
                attempt = self.db.execute("SELECT * FROM live_trial_attempt WHERE item_id=? AND stage='create_conversation'", (item_id,)).fetchone()
                if attempt is None:
                    raise LiveTrialError("attempt_mismatch")
                self.db.execute("UPDATE live_trial_attempt SET state='completed',finished_at=COALESCE(finished_at,?) WHERE id=?", (self._now(), attempt["id"]))
                self.db.execute("UPDATE live_trial_item SET state='conversation_ready',conversation_id=?,requires_reconciliation=0,unknown_stage=NULL,error_code=NULL WHERE id=?", (evidence["conversationId"], item_id))
                self._guard(item, "conversation_ready")
                trial = self._trial(item["trial_id"])
                if trial["paused_at"] is not None:
                    state = "partial_delivery" if self._partial(item_id) else "failed_not_sent"
                    self.db.execute("UPDATE live_trial_item SET state=?,error_code=? WHERE id=?", (state, "paused_after_card" if state == "partial_delivery" else "paused_before_send", item_id))
                    self._guard(item, state)
                    self._release_not_sent(item)
            else:
                for key in ("messageId", "requestRef"):
                    _token(evidence[key])
                _fingerprint(evidence["textSha256"])
                frozen = json.loads(item["item_json"])
                attempt = self.db.execute("SELECT * FROM live_trial_attempt WHERE item_id=? AND stage='send_message'", (item_id,)).fetchone()
                if item["state"] not in ("accepted_candidate", "result_unknown", "confirmed") or attempt is None or item["conversation_id"] != evidence["conversationId"] or attempt["request_ref"] != evidence["requestRef"] or frozen["textSha256"] != evidence["textSha256"]:
                    raise LiveTrialError("evidence_mismatch")
                receipt = json.loads(item["receipt_json"]) if item["receipt_json"] else None
                if receipt and receipt["messageId"] is not None and receipt["messageId"] != evidence["messageId"]:
                    raise LiveTrialError("evidence_mismatch")
                if item["confirmed_json"] is not None:
                    if item["confirmed_json"] != _canonical(evidence):
                        raise LiveTrialError("evidence_conflict")
                    return self._public_item(item)
                self.db.execute("UPDATE live_trial_attempt SET state='completed',finished_at=COALESCE(finished_at,?) WHERE id=?", (self._now(), attempt["id"]))
                self.db.execute("UPDATE live_trial_item SET state='confirmed',confirmed_json=?,requires_reconciliation=0,unknown_stage=NULL,error_code=NULL WHERE id=?", (_canonical(evidence), item_id))
                self._guard(item, "confirmed")
            self._event(item["trial_id"], item_id, "readback_confirmed", evidence)
            return self._public_item(self._item(item_id))

    def _confirm_component(self, item, evidence):
        components = self._components(item["id"])
        component = self._component(item["id"], evidence["componentId"]) if evidence["kind"] == "card_confirmed" else next(row for row in components if row["kind"] == "text")
        if component["kind"] != ("card" if evidence["kind"] == "card_confirmed" else "text"):
            raise LiveTrialError("component_mismatch")
        for key in ("messageId", "requestRef"):_token(evidence[key])
        _fingerprint(evidence["textSha256"])
        frozen = json.loads(component["frozen_json"])
        attempt = self.db.execute("SELECT * FROM live_trial_attempt WHERE id=?", (component["attempt_id"],)).fetchone()
        if component["state"] not in ("accepted_candidate", "result_unknown", "confirmed") or attempt is None or item["conversation_id"] != evidence["conversationId"] or attempt["request_ref"] != evidence["requestRef"] or frozen["textSha256"] != evidence["textSha256"]:
            raise LiveTrialError("evidence_mismatch")
        if any(row["position"] < component["position"] and row["state"] != "confirmed" for row in components):
            raise LiveTrialError("component_dependency_unconfirmed")
        if component["kind"] == "card":
            card = frozen["card"]
            expected = {"productId": card["product_id"], "listId": card["list_id"], "campaignId": card["campaign_id"], "bindingSha256": card["binding_sha256"]}
            if any(evidence[key] != value for key, value in expected.items()):
                raise LiveTrialError("evidence_mismatch")
        receipt = json.loads(component["receipt_json"]) if component["receipt_json"] else None
        if receipt and receipt["messageId"] is not None and receipt["messageId"] != evidence["messageId"]:
            raise LiveTrialError("evidence_mismatch")
        if component["confirmed_json"] is not None:
            if component["confirmed_json"] != _canonical(evidence):
                raise LiveTrialError("evidence_conflict")
            return self._public_item(item)
        self.db.execute("UPDATE live_trial_component SET state='confirmed',confirmed_json=?,error_code=NULL WHERE id=?", (_canonical(evidence), component["id"]))
        self.db.execute("UPDATE live_trial_attempt SET state='completed',finished_at=COALESCE(finished_at,?) WHERE id=?", (self._now(), attempt["id"]))
        if component["kind"] == "text":
            state, code = "confirmed", None
            self.db.execute("UPDATE live_trial_item SET confirmed_json=? WHERE id=?", (_canonical(evidence), item["id"]))
        else:
            trial = self._trial(item["trial_id"])
            state = "partial_delivery" if trial["paused_at"] is not None or trial["expires_at"] <= self._now() else "conversation_ready"
            code = "paused_after_card" if trial["paused_at"] is not None else "expired_after_card" if trial["expires_at"] <= self._now() else None
            self.db.execute("UPDATE live_trial_item SET receipt_json=NULL WHERE id=?", (item["id"],))
        self.db.execute("UPDATE live_trial_item SET state=?,requires_reconciliation=0,unknown_stage=NULL,error_code=? WHERE id=?", (state, code, item["id"]))
        self._guard(item, state)
        self._event(item["trial_id"], item["id"], "readback_confirmed", evidence)
        return self._public_item(self._item(item["id"]))

    def error(self, item_id: str, owner: str, fence: int, attempt_id: str, code: str, definitely_not_sent: bool = False, evidence_ref: str | None = None) -> dict:
        if not isinstance(code, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", code) or type(definitely_not_sent) is not bool:
            raise LiveTrialError("invalid_input", 400)
        if evidence_ref is not None:
            _text(evidence_ref, 512)
        with self._transaction():
            item = self._lease(item_id, owner, fence)
            attempt = self._attempt(item, attempt_id)
            component = self.db.execute("SELECT * FROM live_trial_component WHERE attempt_id=?", (attempt_id,)).fetchone()
            if component is not None:
                return self._error_component(item, attempt, component, code, definitely_not_sent, evidence_ref)
            if attempt["stage"] == "create_conversation" and self.db.execute("SELECT 1 FROM live_trial_component WHERE item_id=? AND attempt_id IS NOT NULL", (item_id,)).fetchone():
                raise LiveTrialError("attempt_mismatch")
            if item["state"] in TERMINAL:
                raise LiveTrialError("invalid_stage")
            expected_stage = "create_conversation" if item["state"] in ("creating_conversation", "conversation_ready") else "send_message" if item["state"] in ("submitting", "accepted_candidate") else item["unknown_stage"]
            if attempt["stage"] != expected_stage:
                raise LiveTrialError("attempt_mismatch")
            if definitely_not_sent:
                if evidence_ref is None or attempt["state"] != "inflight" or item["state"] not in ("creating_conversation", "submitting") or item["receipt_json"] is not None:
                    raise LiveTrialError("not_sent_proof_required")
                self.db.execute("UPDATE live_trial_attempt SET state='failed_not_sent',finished_at=?,error_code=?,result_json=? WHERE id=?", (self._now(), code, _canonical({"evidenceRef": evidence_ref}), attempt_id))
                self.db.execute("UPDATE live_trial_item SET state='failed_not_sent',error_code=? WHERE id=?", (code, item_id))
                self._guard(item, "failed_not_sent")
                self._release_not_sent(item)
                self._event(item["trial_id"], item_id, "failed_not_sent", {"code": code, "evidenceRef": evidence_ref})
            else:
                if attempt["state"] not in ("inflight", "completed", "result_unknown"):
                    raise LiveTrialError("invalid_stage")
                self._unknown(item, attempt, code)
            return self._public_item(self._item(item_id))

    def _error_component(self, item, attempt, component, code, definitely_not_sent, evidence_ref):
        if item["state"] in TERMINAL or component["state"] == "confirmed":
            raise LiveTrialError("invalid_stage")
        if component["state"] not in ("inflight", "accepted_candidate", "result_unknown"):
            raise LiveTrialError("attempt_mismatch")
        if definitely_not_sent:
            if evidence_ref is None or attempt["state"] != "inflight" or item["state"] != "submitting" or component["state"] != "inflight" or component["receipt_json"] is not None:
                raise LiveTrialError("not_sent_proof_required")
            self.db.execute("UPDATE live_trial_attempt SET state='failed_not_sent',finished_at=?,error_code=?,result_json=? WHERE id=?", (self._now(), code, _canonical({"evidenceRef": evidence_ref}), attempt["id"]))
            self.db.execute("UPDATE live_trial_component SET state='failed_not_sent',error_code=? WHERE id=?", (code, component["id"]))
            state = "partial_delivery" if self._partial(item["id"]) else "failed_not_sent"
            self.db.execute("UPDATE live_trial_item SET state=?,error_code=? WHERE id=?", (state, code, item["id"]))
            self._guard(item, state)
            if state == "failed_not_sent":self._release_not_sent(item)
            self._event(item["trial_id"], item["id"], state, {"componentId": component["id"], "code": code, "evidenceRef": evidence_ref})
        else:
            if attempt["state"] not in ("inflight", "completed", "result_unknown"):
                raise LiveTrialError("invalid_stage")
            self._unknown(item, attempt, code)
        return self._public_item(self._item(item["id"]))

    def pause(self, trial_id: str, expected_hash: str) -> dict:
        _fingerprint(expected_hash)
        with self._transaction():
            trial = self._trial(trial_id)
            if trial["snapshot_hash"] != expected_hash:
                raise LiveTrialError("snapshot_mismatch")
            if trial["paused_at"] is not None:
                return self._public_trial(trial)
            self.db.execute("UPDATE live_trial SET paused_at=? WHERE id=?", (self._now(), trial_id))
            cancelled = []
            partial = []
            for item in self.db.execute("SELECT * FROM live_trial_item WHERE trial_id=? AND state IN ('pending','conversation_ready') AND requires_reconciliation=0 ORDER BY position", (trial_id,)).fetchall():
                state = "partial_delivery" if self._partial(item["id"]) else "failed_not_sent"
                self.db.execute("UPDATE live_trial_item SET state=?,error_code=?,lease_owner=NULL,lease_until=NULL WHERE id=?", (state, "paused_after_card" if state == "partial_delivery" else "paused_before_send", item["id"]))
                if state == "partial_delivery":
                    self._guard(item, state);partial.append(item["id"])
                else:
                    self._release_not_sent(item);cancelled.append(item["id"])
            self._event(trial_id, None, "paused", {"cancelledBeforeSend": cancelled, "partialDelivery": partial})
            return self._public_trial(self._trial(trial_id))
