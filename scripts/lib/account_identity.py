"""Project-owned account identity generations and the global maintenance queue.

The current account secrets still live under the read-only legacy authority.  This module may
observe and version those identities locally, but it will not mutate that authority.  A production
maintenance adapter must explicitly declare a project-owned credential authority before refresh or
re-login can execute.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from lib.market_accounts import load_config
from lib.second_cycle import CycleError, digest, encoded


BEIJING = ZoneInfo("Asia/Shanghai")
REQUEST_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{7,119}")
OPERATIONS = {"refresh", "relogin"}
ROLE_ORDER = {"communications": 0, "supply": 1}
ROLE_SLOT = {"communications": (14, 30), "supply": (14, 40)}
INTERVAL_SECONDS = 72 * 3600
TERMINAL = {"completed", "failed_known", "needs_human", "cancelled"}


def _required(store):
    required = {
        "account_runtime_setting", "account_identity_generation", "account_maintenance_intent",
        "account_capability_observation",
    }
    present = {row[0] for row in store.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not required <= present:
        raise CycleError("account_identity_schema_migration_required")


def _request(value):
    if not isinstance(value, str) or not REQUEST_ID.fullmatch(value):
        raise CycleError("account_request_invalid")
    return value


def assignments(root):
    config = load_config(root)
    rows = []
    for market, pair in config["markets"].items():
        for role in ("communications", "supply"):
            rows.append({"market": market, "account": pair["roles"][role], "role": role,
                         "credentialAuthority": pair["credentialAuthority"]})
    return rows


def account_setting(store, market, account, role):
    _required(store)
    row = store.db.execute(
        "SELECT * FROM account_runtime_setting WHERE market=? AND account=?", (market, account),
    ).fetchone()
    if not row:
        return {"market": market, "account": account, "role": role, "enabled": True,
                "revision": 0, "updatedAt": 0}
    if row["role"] != role:
        raise CycleError("account_role_conflict")
    return {"market": market, "account": account, "role": role, "enabled": bool(row["enabled"]),
            "revision": row["revision"], "updatedAt": row["updated_at"]}


def set_enabled(store, root, market, account, enabled, request_id, expected_revision):
    _required(store)
    request_id = _request(request_id)
    if type(enabled) is not bool or type(expected_revision) is not int or expected_revision < 0:
        raise CycleError("account_setting_invalid")
    match = next((row for row in assignments(root)
                  if row["market"] == market and row["account"] == account), None)
    if not match:
        raise CycleError("account_assignment_missing")
    payload = encoded({"enabled": enabled, "expectedRevision": expected_revision})
    intent_id = "account-setting-" + digest([request_id, market, account])[:24]
    with store.tx():
        prior = store.db.execute("SELECT * FROM account_maintenance_intent WHERE request_id=?",
                                 (request_id,)).fetchone()
        if prior:
            if prior["intent_id"] != intent_id or prior["checkpoint_json"] != payload:
                raise CycleError("account_request_conflict")
            return account_setting(store, market, account, match["role"]) | {"duplicate": True}
        current = account_setting(store, market, account, match["role"])
        if current["revision"] != expected_revision:
            raise CycleError("account_revision_conflict")
        now = store.clock()
        store.db.execute(
            """INSERT INTO account_runtime_setting VALUES(?,?,?,?,?,?)
            ON CONFLICT(market,account) DO UPDATE SET enabled=excluded.enabled,
              revision=excluded.revision,updated_at=excluded.updated_at""",
            (market, account, match["role"], int(enabled), expected_revision + 1, now),
        )
        store.db.execute(
            "INSERT INTO account_maintenance_intent VALUES(?,?,?,?,?,?,'completed',NULL,NULL,?,?,?,?,?,?)",
            (intent_id, request_id, market, account, match["role"], "enable" if enabled else "disable",
             now, now, now, payload, None, now),
        )
    return account_setting(store, market, account, match["role"]) | {"duplicate": False}


def current_generation(store, market, account):
    _required(store)
    row = store.db.execute(
        "SELECT * FROM account_identity_generation WHERE market=? AND account=? AND state='published' "
        "ORDER BY published_at DESC,rowid DESC LIMIT 1", (market, account),
    ).fetchone()
    return generation_payload(store, row) if row else None


def generation_payload(store, row):
    if not row:
        return None
    capabilities = {item["capability"]: {"state": item["state"], "evidenceRef": item["evidence_ref"],
                                         "observedAt": item["observed_at"]}
                    for item in store.db.execute(
                        "SELECT * FROM account_capability_observation WHERE generation_id=? ORDER BY capability",
                        (row["generation_id"],))}
    return {"generationId": row["generation_id"], "market": row["market"], "account": row["account"],
            "role": row["role"], "reason": row["reason"], "state": row["state"],
            "browserRef": row["browser_ref"], "httpRef": row["http_ref"], "imRef": row["im_ref"],
            "institutionFingerprint": row["institution_fingerprint"], "capabilities": capabilities,
            "createdAt": row["created_at"], "publishedAt": row["published_at"],
            "errorCode": row["error_code"]}


def publish_generation(store, *, market, account, role, reason, identity, capabilities, now=None):
    """Atomically publish browser/HTTP/IM references only after their joint validation succeeds."""
    _required(store)
    if role not in ROLE_ORDER or reason not in {"baseline", "refresh", "relogin"}:
        raise CycleError("identity_generation_invalid")
    if not isinstance(identity, dict) or set(identity) != {
        "browserRef", "httpRef", "imRef", "institutionFingerprint",
    } or any(not isinstance(value, str) or not value for value in identity.values()):
        raise CycleError("identity_generation_invalid")
    if not isinstance(capabilities, dict) or not capabilities:
        raise CycleError("identity_capabilities_invalid")
    for capability, observation in capabilities.items():
        if not isinstance(capability, str) or not capability or not isinstance(observation, dict) or \
           observation.get("state") not in {"verified", "not_tested", "blocked", "failed"}:
            raise CycleError("identity_capabilities_invalid")
    stamp = store.clock() if now is None else float(now)
    facts = {"market": market, "account": account, "role": role, "reason": reason,
             "identity": identity, "capabilities": capabilities, "at": stamp}
    generation_id = "identity-generation-" + digest(facts)[:24]
    # A generation may contain not-yet-tested writes, but it cannot publish a known failed identity.
    publishable = all(row["state"] not in {"blocked", "failed"} for row in capabilities.values())
    with store.tx():
        store.db.execute(
            "INSERT OR IGNORE INTO account_identity_generation VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (generation_id, market, account, role, reason, identity["browserRef"], identity["httpRef"],
             identity["imRef"], identity["institutionFingerprint"], encoded(capabilities),
             "published" if publishable else "failed", stamp, stamp if publishable else None,
             None if publishable else "capability_validation_failed"),
        )
        for capability, observation in capabilities.items():
            store.db.execute(
                "INSERT OR IGNORE INTO account_capability_observation VALUES(?,?,?,?,?,?)",
                ("capability-" + digest([generation_id, capability])[:24], generation_id, capability,
                 observation["state"], observation.get("evidenceRef"), stamp),
            )
    if not publishable:
        raise CycleError("identity_capability_validation_failed")
    row = store.db.execute("SELECT * FROM account_identity_generation WHERE generation_id=?",
                           (generation_id,)).fetchone()
    return generation_payload(store, row)


def bootstrap_preview(store, root, evidence):
    """Build a secret-free baseline proposal from already-verified read-only evidence."""
    output = []
    by_account = (evidence or {}).get("accounts") or {}
    for assignment in assignments(root):
        row = by_account.get(assignment["account"]) or {}
        capabilities = {key: {"state": value, "evidenceRef": "account-pair-readonly-v2"}
                        for key, value in (row.get("capabilities") or {}).items()}
        output.append({**assignment, "identity": {
            "browserRef": "legacy-readonly-profile:" + digest([assignment["account"], "browser"])[:16],
            "httpRef": "legacy-readonly-http:" + digest([assignment["account"], "http"])[:16],
            "imRef": "legacy-readonly-im:" + digest([assignment["account"], "im"])[:16],
            "institutionFingerprint": digest([assignment["market"], "bjn-local-research"]),
        }, "capabilities": capabilities})
    return {"rows": output, "platformWrites": 0, "credentialWrites": 0}


def apply_bootstrap(store, root, evidence):
    proposal = bootstrap_preview(store, root, evidence)
    applied = 0
    for row in proposal["rows"]:
        if current_generation(store, row["market"], row["account"]):
            continue
        publish_generation(store, market=row["market"], account=row["account"], role=row["role"],
                           reason="baseline", identity=row["identity"], capabilities=row["capabilities"])
        applied += 1
    return proposal | {"applied": applied}


def next_due(last_success, role, now):
    if role not in ROLE_SLOT:
        raise CycleError("account_role_invalid")
    local = datetime.fromtimestamp(float(now), BEIJING)
    base = datetime.fromtimestamp(float(last_success), BEIJING) + timedelta(seconds=INTERVAL_SECONDS) \
        if last_success is not None else local
    hour, minute = ROLE_SLOT[role]
    slot = base.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if slot < base:
        slot += timedelta(days=1)
    return slot.timestamp()


def request_maintenance(store, root, *, market, account, operation, request_id, scheduled_at=None):
    _required(store)
    request_id = _request(request_id)
    if operation not in OPERATIONS:
        raise CycleError("account_operation_invalid")
    assignment = next((row for row in assignments(root)
                       if row["market"] == market and row["account"] == account), None)
    if not assignment:
        raise CycleError("account_assignment_missing")
    stamp = store.clock() if scheduled_at is None else float(scheduled_at)
    intent_id = "account-maintenance-" + digest([request_id, market, account, operation])[:24]
    with store.tx():
        prior = store.db.execute("SELECT * FROM account_maintenance_intent WHERE request_id=?",
                                 (request_id,)).fetchone()
        if prior:
            if prior["intent_id"] != intent_id:
                raise CycleError("account_request_conflict")
            return intent_payload(prior) | {"duplicate": True}
        active = store.db.execute(
            "SELECT 1 FROM account_maintenance_intent WHERE account=? AND state IN ('queued','draining','running')",
            (account,),
        ).fetchone()
        if active:
            raise CycleError("account_maintenance_active")
        current = current_generation(store, market, account)
        store.db.execute(
            "INSERT INTO account_maintenance_intent VALUES(?,?,?,?,?,?,'queued',?,NULL,?,NULL,NULL,'{}',NULL,?)",
            (intent_id, request_id, market, account, assignment["role"], operation,
             current["generationId"] if current else None, stamp, store.clock()),
        )
    return intent_payload(store.db.execute("SELECT * FROM account_maintenance_intent WHERE intent_id=?",
                                           (intent_id,)).fetchone()) | {"duplicate": False}


def intent_payload(row):
    return {"intentId": row["intent_id"], "requestId": row["request_id"], "market": row["market"],
            "account": row["account"], "role": row["role"], "operation": row["operation"],
            "state": row["state"], "expectedGenerationId": row["expected_generation_id"],
            "resultGenerationId": row["result_generation_id"], "scheduledAt": row["scheduled_at"],
            "startedAt": row["started_at"], "finishedAt": row["finished_at"],
            "checkpoint": json.loads(row["checkpoint_json"] or "{}"),
            "errorCode": row["error_code"], "createdAt": row["created_at"]}


def claim_next(store, *, now=None, active_writes=False):
    """Claim globally: communications wins when both roles are due; only one intent may run."""
    _required(store)
    stamp = store.clock() if now is None else float(now)
    if active_writes:
        return None
    with store.tx():
        if store.db.execute(
            "SELECT 1 FROM account_maintenance_intent WHERE state IN ('draining','running')"
        ).fetchone():
            return None
        rows = list(store.db.execute(
            "SELECT * FROM account_maintenance_intent WHERE state='queued' AND scheduled_at<=?",
            (stamp,),
        ))
        rows.sort(key=lambda row: (ROLE_ORDER.get(row["role"], 99), row["scheduled_at"], row["created_at"]))
        if not rows:
            return None
        row = rows[0]
        store.db.execute(
            "UPDATE account_maintenance_intent SET state='draining',started_at=? WHERE intent_id=?",
            (stamp, row["intent_id"]),
        )
    return intent_payload(store.db.execute("SELECT * FROM account_maintenance_intent WHERE intent_id=?",
                                           (row["intent_id"],)).fetchone())


def execute_claimed(store, root, intent_id, adapter=None):
    """Run refresh then optional re-login.  No adapter means the legacy authority stays untouched."""
    _required(store)
    row = store.db.execute("SELECT * FROM account_maintenance_intent WHERE intent_id=?", (intent_id,)).fetchone()
    if not row or row["state"] != "draining":
        raise CycleError("account_intent_not_claimed")
    assignment = next(item for item in assignments(root)
                      if item["market"] == row["market"] and item["account"] == row["account"])
    if assignment["credentialAuthority"] != "project_owned" or adapter is None:
        with store.tx():
            store.db.execute(
                "UPDATE account_maintenance_intent SET state='needs_human',finished_at=?,error_code=? "
                "WHERE intent_id=?", (store.clock(), "project_identity_authority_required", intent_id),
            )
        return intent_payload(store.db.execute("SELECT * FROM account_maintenance_intent WHERE intent_id=?",
                                               (intent_id,)).fetchone())
    previous = current_generation(store, row["market"], row["account"])
    with store.tx():
        store.db.execute("UPDATE account_maintenance_intent SET state='running' WHERE intent_id=?", (intent_id,))
    try:
        adapter.drain(row["account"])
        result = adapter.refresh(row["account"])
        reason = "refresh"
        if not result.get("ok"):
            result = adapter.relogin(row["account"])
            reason = "relogin"
        if not result.get("ok"):
            raise CycleError("account_maintenance_failed")
        generated = publish_generation(store, market=row["market"], account=row["account"], role=row["role"],
                                       reason=reason, identity=result["identity"],
                                       capabilities=adapter.validate(row["account"], result))
        adapter.reconnect_inbox(row["account"], previous, generated)
        with store.tx():
            store.db.execute(
                "UPDATE account_maintenance_intent SET state='completed',result_generation_id=?,finished_at=?,"
                "checkpoint_json=? WHERE intent_id=?",
                (generated["generationId"], store.clock(), encoded({"inboxReconnected": True}), intent_id),
            )
    except BaseException as error:
        code = str(error) if isinstance(error, CycleError) else type(error).__name__
        with store.tx():
            store.db.execute(
                "UPDATE account_maintenance_intent SET state='failed_known',finished_at=?,error_code=? "
                "WHERE intent_id=?", (store.clock(), code, intent_id),
            )
    return intent_payload(store.db.execute("SELECT * FROM account_maintenance_intent WHERE intent_id=?",
                                           (intent_id,)).fetchone())


def status(store, root):
    _required(store)
    now = store.clock()
    rows = []
    for assignment in assignments(root):
        current = current_generation(store, assignment["market"], assignment["account"])
        local = account_setting(store, assignment["market"], assignment["account"], assignment["role"])
        pending = store.db.execute(
            "SELECT * FROM account_maintenance_intent WHERE market=? AND account=? "
            "ORDER BY created_at DESC LIMIT 1", (assignment["market"], assignment["account"]),
        ).fetchone()
        rows.append({**assignment, "setting": local, "generation": current,
                     "maintenance": intent_payload(pending) if pending else None,
                     "nextMaintenanceAt": next_due(current["publishedAt"] if current else None,
                                                   assignment["role"], now)})
    queue = [intent_payload(row) for row in store.db.execute(
        "SELECT * FROM account_maintenance_intent WHERE state IN ('queued','draining','running','needs_human') "
        "ORDER BY CASE role WHEN 'communications' THEN 0 ELSE 1 END,scheduled_at,created_at"
    )]
    return {"schemaVersion": "bdhub.account-identity.v1", "accounts": rows, "queue": queue,
            "intervalHours": 72, "globalConcurrency": 1, "platformWrites": 0, "realSends": 0}
