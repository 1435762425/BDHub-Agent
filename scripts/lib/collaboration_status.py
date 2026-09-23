"""Append-only creator collaboration status with a deterministic current projection."""
from __future__ import annotations

import json
import re

from lib.second_cycle import CycleError, digest, encoded


STATUSES = ("normal", "collaborated", "paid", "rejected")
SOURCES = {"manual", "auto"}
REQUEST_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{7,119}")


def _required(store):
    present = {row[0] for row in store.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not {"creator_collaboration_event", "creator_collaboration_current"} <= present:
        raise CycleError("collaboration_schema_migration_required")


def _plan(store, market="it"):
    row = store.db.execute(
        "SELECT id FROM plan WHERE institution='bjn-local-research' AND market=? AND state='active'", (market,)
    ).fetchone()
    if not row:
        raise CycleError("plan_paused")
    return row[0]


def current(store, creator_id, plan_id=None, market="it"):
    _required(store)
    plan_id = plan_id or _plan(store, market)
    row = store.db.execute(
        "SELECT * FROM creator_collaboration_current WHERE plan_id=? AND creator_id=?",
        (plan_id, creator_id),
    ).fetchone()
    if row:
        return {
            "status": row["status"], "source": row["source"], "revision": row["revision"],
            "updatedAt": row["updated_at"],
        }
    relationship = store.db.execute(
        "SELECT rejected FROM relationship WHERE plan_id=? AND creator_id=?", (plan_id, creator_id),
    ).fetchone()
    if not relationship:
        raise CycleError("relationship_missing")
    return {"status": "rejected" if relationship["rejected"] else "normal",
            "source": "manual" if relationship["rejected"] else "auto", "revision": 0, "updatedAt": 0}


def backfill_preview(store):
    _required(store)
    plan_id = _plan(store)
    rows = list(store.db.execute("SELECT creator_id,rejected FROM relationship WHERE plan_id=?", (plan_id,)))
    showcase = {row[0] for row in store.db.execute(
        "SELECT DISTINCT r.creator_id FROM inbox_event e JOIN relationship r "
        "ON r.plan_id=e.plan_id AND r.oec=e.oec WHERE e.plan_id=? AND e.kind='showcaseNotifications' "
        "AND e.historical=0", (plan_id,),
    )} if store.db.execute("SELECT 1 FROM sqlite_master WHERE name='inbox_event'").fetchone() else set()
    existing = {row[0] for row in store.db.execute(
        "SELECT creator_id FROM creator_collaboration_current WHERE plan_id=?", (plan_id,),
    )}
    counts = {status: 0 for status in STATUSES}
    pending = 0
    for row in rows:
        status = "rejected" if row["rejected"] else "collaborated" if row["creator_id"] in showcase else "normal"
        counts[status] += 1
        pending += row["creator_id"] not in existing
    return {"planId": plan_id, "total": len(rows), "pending": pending, "counts": counts,
            "paidInferred": 0, "platformWrites": 0, "realSends": 0}


def apply_backfill(store):
    """Apply only locally provable defaults; never infer paid collaboration from message text."""
    preview = backfill_preview(store)
    plan_id = preview["planId"]
    showcase = {row[0] for row in store.db.execute(
        "SELECT DISTINCT r.creator_id FROM inbox_event e JOIN relationship r "
        "ON r.plan_id=e.plan_id AND r.oec=e.oec WHERE e.plan_id=? AND e.kind='showcaseNotifications' "
        "AND e.historical=0", (plan_id,),
    )} if store.db.execute("SELECT 1 FROM sqlite_master WHERE name='inbox_event'").fetchone() else set()
    applied = 0
    with store.tx():
        for row in store.db.execute("SELECT creator_id,rejected FROM relationship WHERE plan_id=?", (plan_id,)):
            exists = store.db.execute(
                "SELECT 1 FROM creator_collaboration_current WHERE plan_id=? AND creator_id=?",
                (plan_id, row["creator_id"]),
            ).fetchone()
            if exists:
                continue
            status = "rejected" if row["rejected"] else "collaborated" if row["creator_id"] in showcase else "normal"
            source = "manual" if row["rejected"] else "auto"
            _insert(store, plan_id, row["creator_id"], source, "normal", status,
                    {"reason": "migration_backfill_v1"}, request_id=None, revision=1)
            applied += 1
    return backfill_preview(store) | {"applied": applied}


def _insert(store, plan_id, creator_id, source, old_status, new_status, evidence, *, request_id, revision):
    now = store.clock()
    plan=store.db.execute("SELECT market FROM plan WHERE id=?",(plan_id,)).fetchone()
    if not plan:raise CycleError("plan_missing")
    market=plan[0]
    event_id = "collaboration-" + digest([plan_id, creator_id, revision, source, new_status, evidence])[:28]
    store.db.execute(
        "INSERT INTO creator_collaboration_event VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (event_id, request_id, plan_id, market, creator_id, source, old_status, new_status,
         encoded(evidence), revision, now),
    )
    store.db.execute(
        """INSERT INTO creator_collaboration_current VALUES(?,?,?,?,?,?,?)
        ON CONFLICT(plan_id,creator_id) DO UPDATE SET status=excluded.status,source=excluded.source,
          revision=excluded.revision,updated_at=excluded.updated_at""",
        (plan_id, market, creator_id, new_status, source, revision, now),
    )
    return event_id


def set_manual(store, creator_id, new_status, request_id, expected_status_revision,
               expected_control_revision, *, plan_id=None, market="it"):
    _required(store)
    if new_status not in STATUSES or not isinstance(request_id, str) or not REQUEST_ID.fullmatch(request_id):
        raise CycleError("collaboration_request_invalid")
    if type(expected_status_revision) is not int or expected_status_revision < 0 or \
       type(expected_control_revision) is not int or expected_control_revision < 1:
        raise CycleError("collaboration_request_invalid")
    plan_id = plan_id or _plan(store, market)
    with store.tx():
        prior = store.db.execute(
            "SELECT * FROM creator_collaboration_event WHERE request_id=?", (request_id,),
        ).fetchone()
        if prior:
            if prior["creator_id"] != creator_id or prior["new_status"] != new_status:
                raise CycleError("collaboration_request_conflict")
            return current(store, creator_id, plan_id) | {"duplicate": True}
        relationship = store.db.execute(
            "SELECT * FROM relationship WHERE plan_id=? AND creator_id=?", (plan_id, creator_id),
        ).fetchone()
        if not relationship or relationship["revision"] != expected_control_revision:
            raise CycleError("relationship_control_changed")
        old = current(store, creator_id, plan_id)
        if old["revision"] != expected_status_revision:
            raise CycleError("collaboration_revision_conflict")
        revision = expected_status_revision + 1
        _insert(store, plan_id, creator_id, "manual", old["status"], new_status,
                {"reason": "operator_selection", "relationshipRevision": expected_control_revision},
                request_id=request_id, revision=revision)
        store.db.execute(
            "UPDATE relationship SET rejected=?,revision=revision+1 WHERE plan_id=? AND creator_id=?",
            (int(new_status == "rejected"), plan_id, creator_id),
        )
    return current(store, creator_id, plan_id) | {
        "duplicate": False, "relationshipRevision": expected_control_revision + 1,
        "platformWrites": 0, "realSends": 0,
    }


def observe_showcase(store, creator_id, evidence, *, plan_id=None):
    """Upgrade the system default only; a prior manual choice always wins."""
    _required(store)
    plan_id = plan_id or _plan(store)
    with store.tx():
        old = current(store, creator_id, plan_id)
        if old["source"] == "manual" or old["status"] != "normal":
            return old | {"changed": False}
        revision = old["revision"] + 1
        _insert(store, plan_id, creator_id, "auto", old["status"], "collaborated",
                {"reason": "showcase_notification", "evidence": evidence}, request_id=None,
                revision=revision)
    return current(store, creator_id, plan_id) | {"changed": True}
