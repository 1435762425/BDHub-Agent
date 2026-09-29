"""Read-only evidence for a controlled restart: baseline, drained and post-restart checks.

It never stops, starts, claims, retries, resolves or sends anything, and opens every ledger read-only.
It only writes its own evidence under var/releases/<release-id>/. A check it cannot read is a failure,
never a pass, and nothing is made consistent by clearing unknowns, gaps or request references.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from contextlib import closing
from pathlib import Path

# Settings, authorizations and their receipts: a restart must leave them exactly as they were.
CONTROL_TABLES = ("market_automation_setting", "continuous_send_control", "agent_reply_setting", "service_reply_config",
                  "control_event", "account_runtime_setting", "continuous_send_control_request")
RELEASE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{2,79}")
MARKETS = ("it", "uk", "br", "my")


def _db(root):
    path = Path(root) / "var/second-cycle.sqlite"
    if not path.exists():
        raise FileNotFoundError("second_cycle_missing")
    return closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True))


def control_digests(db):
    result = {}
    for table in CONTROL_TABLES:
        digest = hashlib.sha256()
        for row in db.execute(f"SELECT * FROM {table} ORDER BY 1"):
            digest.update(json.dumps(row, default=str).encode())
        result[table] = digest.hexdigest()[:16]
    return result


def request_refs(db, *, upto=None):
    """Each delivery part's request reference, bound to its delivery and kind.

    Equal counts and uniqueness can hide a replaced value; the digest over (delivery, kind, ref)
    for the rows that existed at the baseline cannot."""
    limit = upto if upto is not None else db.execute("SELECT coalesce(max(rowid),0) FROM cycle_delivery_part").fetchone()[0]
    digest = hashlib.sha256()
    count = 0
    for delivery, kind, ref in db.execute("SELECT delivery_id,kind,request_ref FROM cycle_delivery_part WHERE rowid<=? "
                                          "ORDER BY rowid", (limit,)):
        digest.update(f"{delivery}|{kind}|{ref}\n".encode())
        count += 1
    total, unique = db.execute("SELECT count(*),count(DISTINCT request_ref) FROM cycle_delivery_part").fetchone()
    return {"maxRowid": limit, "baselineRows": count, "baselineDigest": digest.hexdigest()[:24],
            "total": total, "unique": unique}


def inflight(db):
    """Work already handed to the platform whose outcome is not settled yet, including opening a conversation."""
    return {
        "deliveryParts": db.execute("SELECT count(*) FROM cycle_delivery_part WHERE state IN ('inflight','accepted')").fetchone()[0],
        "serviceReplies": db.execute("SELECT count(*) FROM service_reply WHERE state IN ('inflight','accepted')").fetchone()[0],
        "conversationIntents": db.execute(
            "SELECT count(*) FROM cycle_conversation_intent WHERE state='inflight' AND delivery_id IN "
            "(SELECT id FROM cycle_delivery WHERE state IN ('ready','running'))").fetchone()[0],
    }


def processes(root):
    """Registered resident processes that are alive, with the code each one loaded."""
    from lib.runtime_release import code_id, head_sha, loaded, runtime_dirty
    head = head_sha(root)
    head_code = code_id(root, head) if head else None
    rows = []
    for row in loaded(root):  # live registrations only
        rows.append({"role": row.get("role"), "pid": row.get("pid"), "sha": row.get("sha"),
                     "current": bool(head) and code_id(root, row.get("sha")) == head_code and not row.get("runtimeDirty")})
    return {"head": head, "runtimeDirty": runtime_dirty(root) if head else "unknown", "alive": rows}


def _check(name, ok, detail):
    return {"name": name, "ok": bool(ok), "detail": detail}


def preflight(root):
    with _db(root) as db:
        return {"phase": "preflight", "at": time.time(), "controls": control_digests(db), "requestRefs": request_refs(db),
                "inflight": inflight(db), "unknownDeliveries": db.execute(
                    "SELECT count(*) FROM cycle_delivery WHERE state='unknown'").fetchone()[0],
                "processes": processes(root)}


def drained(root, *, workers_stopped=False):
    from lib.operations_scheduler import scheduler_state
    with _db(root) as db:
        claims = db.execute("SELECT count(*) FROM workflow_stage_claim").fetchone()[0]
        pending = inflight(db)
    alive = processes(root)["alive"]
    checks = [_check("scheduler_stopped", not scheduler_state(root).get("running"), None),
              _check("no_stage_claims", claims == 0, claims),
              _check("nothing_inflight", not any(pending.values()), pending)]
    if workers_stopped:
        checks.append(_check("no_resident_process", not alive, [row["role"] for row in alive]))
    return {"phase": "drained", "at": time.time(), "checks": checks, "ok": all(row["ok"] for row in checks)}


def session_states(root, *, wait_seconds=0, clock=time.monotonic, sleep=time.sleep):
    """IM session owners' states; a session rotating (``starting``) is given up to ``wait_seconds`` to be ready."""
    deadline = clock() + wait_seconds
    while True:
        states = {}
        for market in MARKETS:
            try:
                states[market] = json.loads((Path(root) / f"var/im-session-{market}.json").read_text()).get("state")
            except (OSError, ValueError):
                states[market] = None
        if all(state == "ready" for state in states.values()) or clock() >= deadline:
            return states
        sleep(3)


def postflight(root, baseline, *, wait_seconds=0):
    with _db(root) as db:
        controls = control_digests(db)
        refs = request_refs(db, upto=baseline["requestRefs"]["maxRowid"])
        integrity = db.execute("PRAGMA quick_check").fetchone()[0]
    running = processes(root)
    alive = running["alive"]
    ready = session_states(root, wait_seconds=wait_seconds)
    changed = sorted(table for table, value in controls.items() if baseline["controls"].get(table) != value)
    checks = [_check("controls_unchanged", not changed, changed),
              _check("request_refs_kept", refs["baselineDigest"] == baseline["requestRefs"]["baselineDigest"]
                     and refs["baselineRows"] == baseline["requestRefs"]["baselineRows"],
                     {"baselineRows": refs["baselineRows"]}),
              _check("request_refs_unique", refs["total"] == refs["unique"], {"total": refs["total"], "unique": refs["unique"]}),
              _check("quick_check", integrity == "ok", integrity),
              _check("no_uncommitted_runtime_code", running["runtimeDirty"] is None, running["runtimeDirty"]),
              _check("processes_on_head", bool(alive) and all(row["current"] for row in alive),
                     [row["role"] for row in alive if not row["current"]]),
              _check("same_process_roles", sorted({row["role"] for row in alive}) ==
                     sorted({row["role"] for row in baseline["processes"]["alive"]}),
                     sorted({row["role"] for row in alive})),
              _check("im_sessions_ready", all(state == "ready" for state in ready.values()), ready)]
    return {"phase": "postflight", "at": time.time(), "head": running["head"], "checks": checks,
            "ok": all(row["ok"] for row in checks)}


def evidence_path(root, release_id, phase):
    if not isinstance(release_id, str) or not RELEASE_ID.fullmatch(release_id):
        raise ValueError("release_id_invalid")
    return Path(root) / "var/releases" / release_id / f"restart-{phase}.json"
