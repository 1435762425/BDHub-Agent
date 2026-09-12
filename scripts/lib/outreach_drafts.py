"""A durable two-stage draft experiment. No business delivery tools exist here."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_CEILING
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import threading
import time
import uuid

from lib.profile_refresh import ROOT, VAR, _alive, _iso
from lib import draft_provider

SKILL = ROOT / "apps/agent/skills/it-intent-invitation/v1.md"
NODE = "/opt/homebrew/bin/node"
LEASE_SECONDS = 240
RESERVE = 250_000_000  # nano-CNY, covers both bounded model stages
USAGE_KEYS = ("promptTokens", "cacheHitTokens", "cacheMissTokens", "completionTokens", "reasoningTokens", "totalTokens")
ERRORS = frozenset({"invalid_request", "model_disabled", "policy_invalid", "policy_changed", "budget_exhausted", "trial_limit_reached",
    "request_conflict", "draft_not_found", "context_stale", "context_unavailable", "context_invalid", "skill_mismatch", "provider_unavailable",
    "input_too_large", "draft_validation_failed", "review_validation_failed", "usage_unavailable", "reservation_exceeded", "stage_result_unknown",
    "worker_interrupted", "stale_lease", "internal_error"}) | draft_provider.SAFE_CODES


class OutreachDraftError(ValueError):
    def __init__(self, code, status=400):
        self.code = code if isinstance(code, str) and code in ERRORS else "internal_error"
        self.status = status
        super().__init__(self.code)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _token(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", value):
        raise OutreachDraftError("invalid_request")
    return value


def _money(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]+(?:\.[0-9]{1,12})?", value):
        return None
    try:
        decimal = Decimal(value)
        if decimal > 1_000_000:
            return None
        return int((decimal * 1_000_000_000).to_integral_value(rounding=ROUND_CEILING))
    except InvalidOperation:
        return None


def _amount(value):
    return format(Decimal(value) / 1_000_000_000, ".9f").rstrip("0").rstrip(".") if value else "0.00"


def normalize_context_request(value):
    if not isinstance(value, dict) or set(value) != {"packetId", "style", "instructions"}:
        raise OutreachDraftError("invalid_request")
    packet = _token(value["packetId"])
    if value["style"] not in ("friendly", "direct", "detailed") or not isinstance(value["instructions"], str) or len(value["instructions"]) > 500:
        raise OutreachDraftError("invalid_request")
    return {"packetId": packet, "style": value["style"], "instructions": value["instructions"].strip()}


def compile_context(request):
    try:
        result = subprocess.run([NODE, "--experimental-strip-types", str(ROOT / "apps/web/scripts/draft-context.ts")],
            cwd=ROOT / "apps/web", input=_json(request), text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=15)
        if len(result.stdout.encode()) > 131072:
            raise OutreachDraftError("context_unavailable", 503)
        value = json.loads(result.stdout)
        if not isinstance(value, dict):
            raise ValueError()
        if "error" in value:
            error = value["error"] if isinstance(value["error"], dict) else {}
            raise OutreachDraftError("context_stale" if error.get("status") in (403, 404, 409) else "context_unavailable", 409)
        if result.returncode or set(value) - {"context", "fingerprint", "contextRequest"} or "context" not in value or "fingerprint" not in value:
            raise ValueError()
        return value
    except OutreachDraftError:
        raise
    except Exception:
        raise OutreachDraftError("context_unavailable", 503) from None


def _bounded_text(value, maximum, *, empty=False):
    if not isinstance(value, str) or len(value) > maximum or not empty and not value.strip():
        raise ValueError("invalid_text")
    return value.strip()


def validate_context(context, request, skill_path=SKILL):
    try:
        if not isinstance(context, dict) or context.get("schema") != "bdhub.outreach-draft-context.v1" or context.get("executionBlocked") is not True:
            raise ValueError()
        binding, facts = context["binding"], context["modelFacts"]
        if binding["market"] != "it" or binding["packetId"] != request["packetId"] or binding["policyVersion"] != "it-intent-invitation@1":
            raise ValueError()
        second = binding.get("origin") == "second_outreach"
        if second and (not re.fullmatch(r"second-packet-[0-9a-f-]{36}", request["packetId"]) or binding.get("historicalOwnership") != "unverified" or not re.fullmatch(r"[0-9a-f]{64}", binding.get("sourceFingerprint", ""))):
            raise ValueError()
        if not second and request["packetId"].startswith("second-packet-"):
            raise ValueError()
        if not isinstance(binding["oecId"], str) or not re.fullmatch(r"[0-9]{1,64}", binding["oecId"]):
            raise ValueError()
        _token(binding["registryCreatorId"])
        skill = skill_path.read_bytes()
        if len(skill) > 16000 or hashlib.sha256(skill).hexdigest() != binding["skillSha256"]:
            raise OutreachDraftError("skill_mismatch", 409)
        if set(facts) != {"intent", "language", "recipient", "style", "instructions", "products", "facts", "commercialTerms"} or \
                facts["intent"] != "explore_interest" or facts["language"] != "it" or facts["style"] != request["style"] or facts["instructions"] != request["instructions"] or \
                facts["commercialTerms"] != {"status": "not_verified", "allowedPromises": []}:
            raise ValueError()
        recipient = facts["recipient"]
        if not isinstance(recipient, dict) or set(recipient) != {"handle"} or recipient["handle"] is not None and not re.fullmatch(r"[a-z0-9._]{1,64}", recipient["handle"]):
            raise ValueError()
        if not isinstance(facts["products"], list) or not 1 <= len(facts["products"]) <= 3 or not isinstance(facts["facts"], list) or not 1 <= len(facts["facts"]) <= 12:
            raise ValueError()
        fact_ids = set()
        for fact in facts["facts"]:
            if not isinstance(fact, dict) or set(fact) != {"id", "kind", "value"} or not isinstance(fact["id"], str) or not re.fullmatch(r"recipient|p[1-3]-(?:name|fit)", fact["id"]) or fact["id"] in fact_ids or fact["kind"] not in {"recipient_handle", "product_name", "category_alignment"}:
                raise ValueError()
            _token(fact["id"]);fact_ids.add(fact["id"])
            values = fact["value"] if isinstance(fact["value"], list) else [fact["value"]]
            if len(values) > 16:
                raise ValueError()
            for text in values:
                _bounded_text(text, 300, empty=True)
        products = set()
        for product in facts["products"]:
            if not isinstance(product, dict) or set(product) != {"id", "nameIt", "sharedCategories", "factIds"} or not re.fullmatch(r"p[1-3]", product["id"]) or product["id"] in products:
                raise ValueError()
            products.add(product["id"]);_bounded_text(product["nameIt"], 200)
            if not isinstance(product["sharedCategories"], list) or len(product["sharedCategories"]) > 16:
                raise ValueError()
            for category in product["sharedCategories"]:
                _bounded_text(category, 100)
            if not isinstance(product["factIds"], list) or not set(product["factIds"]) <= fact_ids or not product["factIds"]:
                raise ValueError()
            bound = [fact for fact in facts["facts"] if fact["id"] in product["factIds"]]
            if not any(fact["kind"]=="product_name" and fact["value"]==product["nameIt"] for fact in bound):
                raise ValueError()
            if second:
                # A source lead is sufficient to discuss a named product; it is
                # not evidence of this recipient's sales or category alignment.
                if product["sharedCategories"] or any(fact["kind"] != "product_name" for fact in bound):raise ValueError()
            elif not any(fact["kind"]=="category_alignment" and fact["value"]==product["sharedCategories"] for fact in bound):
                raise ValueError()
        if second and any(fact["kind"] not in {"recipient_handle","product_name"} for fact in facts["facts"]):raise ValueError()
        return skill.decode("utf-8")
    except OutreachDraftError:
        raise
    except Exception:
        raise OutreachDraftError("context_invalid", 400) from None


URL = re.compile(r"https?://|www\.|wa\.me/|t\.me/|mailto:|\b[A-Za-z0-9._+%-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", re.I)


def validate_draft(value, facts):
    try:
        if not isinstance(value, dict) or set(value) != {"textIt", "translationZh", "selectedProductIds", "evidenceRefs", "rationaleZh"}:
            raise ValueError()
        text = _bounded_text(value["textIt"], 900)
        translation = _bounded_text(value["translationZh"], 1600)
        rationale = _bounded_text(value["rationaleZh"], 400)
        if URL.search(text) or URL.search(translation):
            raise ValueError()
        products = {p["id"]: p for p in facts["products"]}
        ids, refs = value["selectedProductIds"], value["evidenceRefs"]
        if not isinstance(ids, list) or not 1 <= len(ids) <= 3 or len(set(ids)) != len(ids) or not set(ids) <= products.keys():
            raise ValueError()
        known_refs = {f["id"]: f for f in facts["facts"]}
        if not isinstance(refs, list) or not 1 <= len(refs) <= 12 or len(set(refs)) != len(refs) or not set(refs) <= known_refs.keys():
            raise ValueError()
        for identity in ids:
            product = products[identity]
            if product["nameIt"].casefold() not in text.casefold() or not any(ref in refs and known_refs[ref]["kind"] == "product_name" for ref in product["factIds"]):
                raise ValueError()
        handles = re.findall(r"(?<![A-Za-z0-9_])@([A-Za-z0-9._]{1,64})", text)
        expected = facts["recipient"]["handle"]
        if any(handle.casefold() != (expected or "").casefold() for handle in handles):
            raise ValueError()
        return {"textIt": text, "translationZh": translation, "selectedProductIds": ids, "evidenceRefs": refs, "rationaleZh": rationale}
    except Exception:
        raise OutreachDraftError("draft_validation_failed", 422) from None


def validate_review(value):
    try:
        if not isinstance(value, dict) or set(value) != {"verdict", "issues", "unsupportedClaims", "italianValid", "translationFaithful"} or value["verdict"] not in ("pass", "needs_review"):
            raise ValueError()
        if type(value["italianValid"]) is not bool or type(value["translationFaithful"]) is not bool:
            raise ValueError()
        for key in ("issues", "unsupportedClaims"):
            if not isinstance(value[key], list) or len(value[key]) > 12:
                raise ValueError()
            value[key] = [_bounded_text(text, 400) for text in value[key]]
        if value["issues"] or value["unsupportedClaims"] or not value["italianValid"] or not value["translationFaithful"]:
            value["verdict"] = "needs_review"
        return value
    except Exception:
        raise OutreachDraftError("review_validation_failed", 422) from None


def _receipt_time(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})", value):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
    except (ValueError, OverflowError):
        return None


def _pricing_version(value):
    if not isinstance(value, str) or not re.fullmatch(r"deepseek-flash-cny-\d{4}-\d{2}-\d{2}", value):
        return None
    try:
        datetime.strptime(value[-10:], "%Y-%m-%d")
        return value
    except ValueError:
        return None


def _public_cost(cost):
    return {key: cost[key] for key in ("estimatedCny", "upperBoundCny", "complete")}


def safe_receipt(value):
    value = value if isinstance(value, dict) else {}
    usage = value.get("usage") if isinstance(value.get("usage"), dict) else {}
    usage = {key: usage.get(key) if type(usage.get(key)) is int and 0 <= usage[key] <= 1_000_000_000 else None for key in USAGE_KEYS}
    cost = value.get("cost") if isinstance(value.get("cost"), dict) else {}
    estimate, upper = _money(cost.get("estimatedCny")), _money(cost.get("upperBoundCny"))
    complete = cost.get("complete") is True and estimate is not None and upper is not None and upper >= estimate
    content = value.get("content")
    window = cost.get("window")
    return {"content": content if isinstance(content, str) and len(content.encode()) <= 32768 else None,
            "model": "deepseek-flash", "responseId": value.get("responseId") if isinstance(value.get("responseId"), str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,200}", value["responseId"]) else None,
            "startedAt": _receipt_time(value.get("startedAt")), "finishedAt": _receipt_time(value.get("finishedAt")),
            "usage": usage, "cost": {"estimatedCny": _amount(estimate) if estimate is not None else None, "upperBoundCny": _amount(upper) if upper is not None else None, "complete": complete,
                "pricingVersion": _pricing_version(cost.get("pricingVersion")), "window": window if isinstance(window, str) and window in {"peak", "off_peak", "cross_window", "unknown"} else "unknown"}}


_SCHEMA = """
CREATE TABLE IF NOT EXISTS outreach_meta(version INTEGER PRIMARY KEY CHECK(version=1));
INSERT OR IGNORE INTO outreach_meta VALUES(1);
CREATE TABLE IF NOT EXISTS outreach_policy(id TEXT NOT NULL,version INTEGER NOT NULL,fingerprint TEXT NOT NULL,max_drafts INTEGER NOT NULL,max_nano INTEGER NOT NULL,PRIMARY KEY(id,version));
CREATE TABLE IF NOT EXISTS outreach_job(
 id TEXT PRIMARY KEY,request_id TEXT NOT NULL UNIQUE,request_hash TEXT NOT NULL,context_request TEXT NOT NULL,context_json TEXT NOT NULL,context_fingerprint TEXT NOT NULL,
 packet_id TEXT NOT NULL,style TEXT NOT NULL,creator_id TEXT NOT NULL,policy_id TEXT NOT NULL,policy_version INTEGER NOT NULL,policy_hash TEXT NOT NULL,
 status TEXT NOT NULL,stage TEXT,created_at TEXT NOT NULL,started_at TEXT,finished_at TEXT,error_code TEXT,
 reserve_nano INTEGER NOT NULL,slot_counted INTEGER NOT NULL DEFAULT 1,lease_owner TEXT,lease_pid INTEGER,lease_until REAL,fence INTEGER NOT NULL DEFAULT 0);
CREATE UNIQUE INDEX IF NOT EXISTS outreach_running_creator ON outreach_job(creator_id) WHERE status='running';
CREATE TABLE IF NOT EXISTS outreach_attempt(
 job_id TEXT NOT NULL REFERENCES outreach_job(id),stage TEXT NOT NULL,status TEXT NOT NULL,request_maybe_sent INTEGER NOT NULL,
 receipt_json TEXT,output_json TEXT,started_at TEXT NOT NULL,finished_at TEXT,error_code TEXT,PRIMARY KEY(job_id,stage));
CREATE TABLE IF NOT EXISTS outreach_heartbeat(owner TEXT PRIMARY KEY,pid INTEGER NOT NULL,seen_at REAL NOT NULL);
"""


class OutreachDraftStore:
    def __init__(self, var_dir=VAR, *, now=time.time, context_validator=None, provider_status=None, skill_path=SKILL):
        self.var_dir = Path(var_dir).resolve();self.var_dir.mkdir(parents=True, exist_ok=True)
        self.policy_path = self.var_dir / "outreach-model-policy.json"
        self.path = self.var_dir / "outreach-drafts.sqlite"
        self.now, self.validate_context, self.provider_status, self.skill_path = now, context_validator or compile_context, provider_status or draft_provider.provider_status, Path(skill_path)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(self.path, timeout=10, isolation_level=None, check_same_thread=False);self._db.row_factory = sqlite3.Row
        try:
            tables = {r[0] for r in self._db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if tables and "outreach_meta" not in tables:
                raise OutreachDraftError("internal_error", 500)
            if tables and [r[0] for r in self._db.execute("SELECT version FROM outreach_meta")] != [1]:
                raise OutreachDraftError("internal_error", 500)
            self._db.execute("PRAGMA journal_mode=WAL");self._db.execute("PRAGMA foreign_keys=ON")
            self._db.executescript("BEGIN IMMEDIATE;\n" + _SCHEMA + "\nCOMMIT;");self.path.chmod(0o600)
        except BaseException:
            self._db.close();raise

    def __enter__(self):return self
    def __exit__(self, *_):self.close()
    def close(self):
        with self._lock:self._db.close()

    @contextmanager
    def transaction(self):
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                yield;self._db.execute("COMMIT")
            except BaseException:
                self._db.execute("ROLLBACK");raise

    def policy(self):
        default = {"version": 1, "id": None, "enabled": False, "model": "deepseek-flash", "maxDrafts": 3, "maxCostCny": "1.00"}
        if not self.policy_path.is_file():return default
        try:
            if self.policy_path.stat().st_size > 4096:raise ValueError()
            value = json.loads(self.policy_path.read_text())
            if not isinstance(value, dict) or set(value) != set(default) or type(value["version"]) is not int or value["version"] < 1 or type(value["enabled"]) is not bool or \
                    value["model"] != "deepseek-flash" or type(value["maxDrafts"]) is not int or not 1 <= value["maxDrafts"] <= 3 or _money(value["maxCostCny"]) is None or not 0 < _money(value["maxCostCny"]) <= 1_000_000_000:
                raise ValueError()
            _token(value["id"]);return value
        except Exception:raise OutreachDraftError("policy_invalid", 503) from None

    @staticmethod
    def _policy_hash(policy):return _hash({key: value for key, value in policy.items() if key != "enabled"})

    def _row(self, draft_id):
        row = self._db.execute("SELECT * FROM outreach_job WHERE id=?", (draft_id,)).fetchone()
        if row is None:raise OutreachDraftError("draft_not_found", 404)
        return dict(row)

    def attempts(self, draft_id):
        return [dict(row) for row in self._db.execute("SELECT * FROM outreach_attempt WHERE job_id=? ORDER BY CASE stage WHEN 'draft' THEN 0 ELSE 1 END", (draft_id,))]

    def cost(self, draft_id):
        estimated = upper = 0
        for attempt in self.attempts(draft_id):
            if not attempt["request_maybe_sent"]:continue
            receipt = json.loads(attempt["receipt_json"]) if attempt["receipt_json"] else None
            if not receipt or not receipt["cost"]["complete"]:return {"estimatedCny": None, "upperBoundCny": None, "complete": False}
            estimated += _money(receipt["cost"]["estimatedCny"]);upper += _money(receipt["cost"]["upperBoundCny"])
        return {"estimatedCny": _amount(estimated), "upperBoundCny": _amount(upper), "complete": True}

    def budget(self, policy):
        rows = self._db.execute("SELECT * FROM outreach_job WHERE policy_id=? AND policy_version=?", (policy["id"], policy["version"])).fetchall()
        held = settled = known = slots = 0
        for row in rows:
            held += row["reserve_nano"];slots += row["slot_counted"]
            cost = self.cost(row["id"])
            if not row["reserve_nano"] and cost["complete"]:settled += _money(cost["upperBoundCny"])
            for attempt in self.attempts(row["id"]):
                if attempt["receipt_json"]:
                    amount = _money(json.loads(attempt["receipt_json"])["cost"].get("estimatedCny"))
                    known += amount or 0
        return {"reservedCny": _amount(held), "knownCostCny": _amount(known), "availableCny": _amount(max(0, _money(policy["maxCostCny"]) - held - settled)), "usedDrafts": slots}

    def worker_online(self):
        with self._lock:return any(_alive(r[0]) for r in self._db.execute("SELECT pid FROM outreach_heartbeat WHERE seen_at>=?", (self.now() - 30,)))

    def heartbeat(self, owner):
        with self.transaction():self._db.execute("INSERT INTO outreach_heartbeat VALUES(?,?,?) ON CONFLICT(owner) DO UPDATE SET pid=excluded.pid,seen_at=excluded.seen_at", (owner, os.getpid(), self.now()))

    def status(self):
        policy = self.policy();provider = self.provider_status()
        with self._lock:return {"provider": {"ready": provider.get("ready") is True, "model": "deepseek-flash"}, "policy": policy, "budget": self.budget(policy), "workerOnline": self.worker_online()}

    def _public(self, row):
        return {"id": row["id"], "packetId": row["packet_id"], "status": row["status"], "style": row["style"],
                "createdAt": row["created_at"], "startedAt": row["started_at"], "finishedAt": row["finished_at"], "errorCode": row["error_code"],
                "stage": row["stage"], "reservedCostCny": _amount(row["reserve_nano"]), "knownCostCny": self.cost(row["id"])["estimatedCny"], "executionBlocked": True}

    def lookup_request(self, request_id, request):
        request_id = _token(request_id);request = normalize_context_request(request)
        with self._lock:
            row = self._db.execute("SELECT * FROM outreach_job WHERE request_id=?", (request_id,)).fetchone()
            if row is None:return None
            if row["request_hash"] != _hash(request):raise OutreachDraftError("request_conflict", 409)
            return self._public(row)

    def enqueue(self, request_id, context, fingerprint, context_request):
        request_id = _token(request_id);request = normalize_context_request(context_request)
        previous = self.lookup_request(request_id, request)
        if previous:return previous
        if not isinstance(fingerprint, str) or not re.fullmatch(r"[0-9a-f]{64}", fingerprint):raise OutreachDraftError("invalid_request")
        policy = self.policy()
        if not policy["enabled"]:raise OutreachDraftError("model_disabled", 403)
        if self.provider_status().get("ready") is not True:raise OutreachDraftError("provider_unavailable", 503)
        current = self.validate_context(request)
        if current.get("fingerprint") != fingerprint or _hash(current.get("context")) != _hash(context):raise OutreachDraftError("context_stale", 409)
        validate_context(context, request, self.skill_path)
        with self.transaction():
            previous = self.lookup_request(request_id, request)
            if previous:return previous
            live = self.policy()
            if not live["enabled"]:raise OutreachDraftError("model_disabled", 403)
            policy_hash = self._policy_hash(policy)
            if self._policy_hash(live) != policy_hash:raise OutreachDraftError("policy_changed", 409)
            old = self._db.execute("SELECT fingerprint FROM outreach_policy WHERE id=? AND version=?", (policy["id"], policy["version"])).fetchone()
            if old and old[0] != policy_hash:raise OutreachDraftError("policy_changed", 409)
            self._db.execute("INSERT OR IGNORE INTO outreach_policy VALUES(?,?,?,?,?)", (policy["id"], policy["version"], policy_hash, policy["maxDrafts"], _money(policy["maxCostCny"])))
            budget = self.budget(policy)
            if budget["usedDrafts"] >= policy["maxDrafts"]:raise OutreachDraftError("trial_limit_reached", 429)
            if _money(budget["availableCny"]) < RESERVE:raise OutreachDraftError("budget_exhausted", 429)
            draft_id = "outreach_draft_" + uuid.uuid4().hex
            self._db.execute("""INSERT INTO outreach_job(id,request_id,request_hash,context_request,context_json,context_fingerprint,packet_id,style,creator_id,
                policy_id,policy_version,policy_hash,status,created_at,reserve_nano) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (draft_id, request_id, _hash(request), _json(request), _json(context), fingerprint, request["packetId"], request["style"], context["binding"]["registryCreatorId"],
                 policy["id"], policy["version"], policy_hash, "queued", _iso(self.now()), RESERVE))
            return self._public(self._row(draft_id))

    def list_drafts(self, packet_id):
        packet_id = _token(packet_id)
        with self._lock:return {"drafts": [self._public(r) for r in self._db.execute("SELECT * FROM outreach_job WHERE packet_id=? ORDER BY created_at DESC,id DESC LIMIT 20", (packet_id,))], "workerOnline": self.worker_online()}

    def list_creator_drafts(self, creator_id):
        if not isinstance(creator_id, str) or not re.fullmatch(r"creator_[0-9a-f]{32}", creator_id):
            raise OutreachDraftError("invalid_request")
        with self._lock:
            return {"drafts": [self._public(row) for row in self._db.execute(
                "SELECT * FROM outreach_job WHERE creator_id=? ORDER BY created_at DESC,id DESC LIMIT 20", (creator_id,))],
                "workerOnline": self.worker_online()}

    def detail(self, draft_id):
        with self._lock:
            row = self._row(_token(draft_id));attempts = self.attempts(draft_id);content = review = None;public = []
            for attempt in attempts:
                output = json.loads(attempt["output_json"]) if attempt["output_json"] else None
                if attempt["stage"] == "draft":content = output
                else:review = output
                receipt = json.loads(attempt["receipt_json"]) if attempt["receipt_json"] else safe_receipt(None)
                public.append({"stage": attempt["stage"], "status": attempt["status"], "usage": receipt["usage"], "cost": _public_cost(receipt["cost"])})
            facts=json.loads(row["context_json"])["modelFacts"]
            return {"draft": self._public(row), "content": content, "review": review, "attempts": public, "cost": self.cost(draft_id),
                    "facts": [{key:fact[key] for key in ("id","kind","value")} for fact in facts["facts"]],
                    "products": [{key:product[key] for key in ("id","nameIt")} for product in facts["products"]],
                    "contextRequest": json.loads(row["context_request"]), "contextFingerprint": row["context_fingerprint"], "executionBlocked": True}

    def claim(self, owner):
        with self.transaction():
            running = self._db.execute("SELECT * FROM outreach_job WHERE status='running' ORDER BY started_at,id").fetchall()
            row = next((r for r in running if r["lease_until"] <= self.now() or not _alive(r["lease_pid"])), None)
            if row is None:
                row = self._db.execute("""SELECT j.* FROM outreach_job j WHERE j.status='queued' AND NOT EXISTS
                    (SELECT 1 FROM outreach_job active WHERE active.status='running' AND active.creator_id=j.creator_id) ORDER BY j.created_at,j.id LIMIT 1""").fetchone()
            if row is None:return None
            self._db.execute("UPDATE outreach_job SET status='running',started_at=COALESCE(started_at,?),lease_owner=?,lease_pid=?,lease_until=?,fence=fence+1 WHERE id=?",
                             (_iso(self.now()), owner, os.getpid(), self.now()+LEASE_SECONDS, row["id"]))
            return self._row(row["id"])

    def _owned(self, job, owner):
        current = self._row(job["id"])
        if current["status"] != "running" or current["lease_owner"] != owner or current["fence"] != job["fence"] or current["lease_until"] <= self.now():
            raise OutreachDraftError("stale_lease", 409)
        return current

    def check_policy(self, job):
        policy = self.policy()
        if not policy["enabled"]:raise OutreachDraftError("model_disabled", 403)
        if (policy["id"], policy["version"], self._policy_hash(policy)) != (job["policy_id"], job["policy_version"], job["policy_hash"]):raise OutreachDraftError("policy_changed", 409)
        return policy

    def begin_stage(self, job, owner, stage):
        with self.transaction():
            self._owned(job, owner);policy=self.check_policy(job)
            obligations=0
            for row in self._db.execute("SELECT id,reserve_nano FROM outreach_job WHERE policy_id=? AND policy_version=?", (policy["id"],policy["version"])):
                cost=self.cost(row["id"])
                obligations+=row["reserve_nano"] if row["reserve_nano"] else (_money(cost["upperBoundCny"]) or 0)
            if obligations>_money(policy["maxCostCny"]):raise OutreachDraftError("budget_exhausted",429)
            if self._db.execute("SELECT 1 FROM outreach_attempt WHERE job_id=? AND stage=?", (job["id"], stage)).fetchone():raise OutreachDraftError("stage_result_unknown", 409)
            self._db.execute("INSERT INTO outreach_attempt(job_id,stage,status,request_maybe_sent,started_at) VALUES(?,?,'inflight',1,?)", (job["id"], stage, _iso(self.now())))
            self._db.execute("UPDATE outreach_job SET stage=?,lease_until=? WHERE id=?", (stage,self.now()+LEASE_SECONDS,job["id"]))

    def receipt(self, job, owner, stage, value, *, error=None, sent=True):
        receipt = safe_receipt(value)
        if not sent:
            receipt["cost"].update(estimatedCny="0.00", upperBoundCny="0.00", complete=True)
        with self.transaction():
            self._owned(job, owner)
            self._db.execute("UPDATE outreach_attempt SET status=?,request_maybe_sent=?,receipt_json=?,finished_at=?,error_code=? WHERE job_id=? AND stage=? AND status='inflight'",
                ("result_unknown" if error and sent else "failed" if error else "completed", int(sent), _json(receipt), _iso(self.now()), error, job["id"], stage))
            known_upper = sum((_money(json.loads(r[0])["cost"].get("upperBoundCny")) or 0) for r in self._db.execute("SELECT receipt_json FROM outreach_attempt WHERE job_id=? AND receipt_json IS NOT NULL", (job["id"],)))
            if known_upper > RESERVE:self._db.execute("UPDATE outreach_job SET reserve_nano=? WHERE id=?", (known_upper, job["id"]))

    def save_output(self, job, owner, stage, output):
        with self.transaction():
            self._owned(job, owner)
            self._db.execute("UPDATE outreach_attempt SET output_json=? WHERE job_id=? AND stage=? AND status='completed'", (_json(output),job["id"],stage))

    def finish(self, job, owner, status, error=None):
        with self.transaction():
            try:current = self._owned(job, owner)
            except OutreachDraftError:return self._public(self._row(job["id"]))
            attempts = self.attempts(job["id"])
            sent = any(a["request_maybe_sent"] for a in attempts)
            cost = self.cost(job["id"])
            reserve = current["reserve_nano"] if status=="result_unknown" or not cost["complete"] else 0
            self._db.execute("UPDATE outreach_job SET status=?,finished_at=?,error_code=?,reserve_nano=?,slot_counted=?,lease_owner=NULL,lease_pid=NULL,lease_until=NULL WHERE id=?",
                (status,_iso(self.now()),error if error in ERRORS else None,reserve,int(sent),job["id"]))
            if status=="result_unknown":self._db.execute("UPDATE outreach_attempt SET status='result_unknown',error_code=COALESCE(error_code,?) WHERE job_id=? AND status='inflight'", (error,job["id"]))
            return self._public(self._row(job["id"]))


class OutreachDraftWorker:
    def __init__(self, store, *, provider=None):
        self.store, self.provider = store, provider or draft_provider.call_model
        self.owner = "draft_worker_" + uuid.uuid4().hex;self._stop = threading.Event();self._thread = None

    def __enter__(self):
        self.store.heartbeat(self.owner)
        def beat():
            while not self._stop.wait(10):
                try:self.store.heartbeat(self.owner)
                except Exception:pass
        self._thread = threading.Thread(target=beat,daemon=True,name="outreach-draft-heartbeat");self._thread.start();return self

    def __exit__(self, *_):
        self._stop.set()
        if self._thread:self._thread.join(timeout=1)
        with self.store.transaction():self.store._db.execute("DELETE FROM outreach_heartbeat WHERE owner=?", (self.owner,))

    def _verify(self, job):
        request=json.loads(job["context_request"]);current=self.store.validate_context(request)
        if current.get("fingerprint")!=job["context_fingerprint"] or _hash(current.get("context"))!=_hash(json.loads(job["context_json"])):raise OutreachDraftError("context_stale",409)
        return validate_context(current["context"],request,self.store.skill_path)

    def _messages(self, job, stage, skill, draft=None):
        facts=json.loads(job["context_json"])["modelFacts"]
        if stage=="draft":
            instruction="You are the writer. Return exactly one JSON object with textIt, translationZh, selectedProductIds, evidenceRefs, rationaleZh. No other keys."
            data={"modelFacts":facts}
        else:
            instruction="You are the independent reviewer. Check the entire text and translation against these facts; do not trust the writer's citations alone. Return exactly one JSON object with verdict, issues, unsupportedClaims, italianValid, translationFaithful. Do not rewrite the draft."
            data={"modelFacts":facts,"draft":draft}
        messages=[{"role":"system","content":skill+"\n\n"+instruction},{"role":"user","content":_json(data)}]
        if len(_json(messages).encode())>24000:raise OutreachDraftError("input_too_large",413)
        return messages

    def _stage(self, job, stage, draft=None):
        existing=next((a for a in self.store.attempts(job["id"]) if a["stage"]==stage),None)
        if existing is None:
            skill=self._verify(job);self.store.check_policy(job)
            if self.store.provider_status().get("ready") is not True:raise OutreachDraftError("provider_unavailable",503)
            messages=self._messages(job,stage,skill,draft);self.store.begin_stage(job,self.owner,stage)
            try:
                receipt=self.provider(messages,max_output_tokens=1200,timeout=60)
            except draft_provider.DraftProviderError as error:
                sent=error.outcome!="request_not_sent"
                self.store.receipt(job,self.owner,stage,error.receipt,error=error.code,sent=sent)
                return None,self.store.finish(job,self.owner,"result_unknown" if sent else "failed",error.code)
            except Exception:
                self.store.receipt(job,self.owner,stage,None,error="stage_result_unknown",sent=True)
                return None,self.store.finish(job,self.owner,"result_unknown","stage_result_unknown")
            self.store.receipt(job,self.owner,stage,receipt)
            existing=next(a for a in self.store.attempts(job["id"]) if a["stage"]==stage)
        if existing["status"]!="completed" or not existing["receipt_json"]:
            if existing["status"]=="failed" and not existing["request_maybe_sent"]:
                return None,self.store.finish(job,self.owner,"failed",existing["error_code"])
            return None,self.store.finish(job,self.owner,"result_unknown","stage_result_unknown")
        receipt=json.loads(existing["receipt_json"])
        try:
            output=json.loads(receipt["content"])
            output=validate_draft(output,json.loads(job["context_json"])["modelFacts"]) if stage=="draft" else validate_review(output)
        except OutreachDraftError:raise
        except Exception:raise OutreachDraftError("draft_validation_failed" if stage=="draft" else "review_validation_failed",422) from None
        self.store.save_output(job,self.owner,stage,output)
        if not receipt["cost"]["complete"]:return None,self.store.finish(job,self.owner,"result_unknown","usage_unavailable")
        cost=self.store.cost(job["id"])
        if not cost["complete"]:return None,self.store.finish(job,self.owner,"result_unknown","usage_unavailable")
        if _money(cost["upperBoundCny"])>RESERVE:return None,self.store.finish(job,self.owner,"result_unknown","reservation_exceeded")
        return output,None

    def run_once(self):
        self.store.heartbeat(self.owner);job=self.store.claim(self.owner)
        if job is None:return None
        try:
            # An in-flight stage without a durable response is never requested again.
            if any(a["status"] in {"inflight","result_unknown"} for a in self.store.attempts(job["id"])):
                return self.store.finish(job,self.owner,"result_unknown","stage_result_unknown")
            draft,terminal=self._stage(job,"draft")
            if terminal:return terminal
            review,terminal=self._stage(job,"review",draft)
            if terminal:return terminal
            self._verify(job)
            return self.store.finish(job,self.owner,"drafted" if review["verdict"]=="pass" else "needs_review")
        except OutreachDraftError as error:
            if error.code=="stale_lease":return self.store.detail(job["id"])["draft"]
            return self.store.finish(job,self.owner,"stale" if error.code in {"context_stale","skill_mismatch"} else "failed",error.code)
        except (KeyboardInterrupt,SystemExit):
            unknown=any(a["status"]=="inflight" for a in self.store.attempts(job["id"]))
            self.store.finish(job,self.owner,"result_unknown" if unknown else "failed","worker_interrupted");raise
        except Exception:
            unknown=any(a["status"]=="inflight" for a in self.store.attempts(job["id"]))
            return self.store.finish(job,self.owner,"result_unknown" if unknown else "failed","internal_error")
