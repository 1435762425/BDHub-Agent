"""Read-only cross-market console: what each market is doing, waiting for and last achieved (H12).

It reads only existing ledgers -- workflow runs/stages, claims and resource slots, the projected
operations read model -- and never starts, retries or claims anything. Every figure keeps its own
time; a market whose ledger cannot be read is reported as unavailable, never as zero.
"""
from __future__ import annotations

import json
import sqlite3

from lib.second_cycle import CycleError

SCHEMA_VERSION = "bdhub.operations-console.v1"
QUIET_STATES = ("skipped", "waiting_upstream")
STAGE_LABELS = {"taplink_clean": "TapLink 清理", "catalog": "货盘", "taplink_prepare": "TapLink 建链",
                "kalodata": "Kalodata 线索", "oecid": "OECID 身份", "send_pool": "发送池"}


def _stage_rows(db, run_id):
    return list(db.execute("SELECT * FROM workflow_stage_run WHERE run_id=? ORDER BY position", (run_id,)))


def _holders(db):
    """resource key -> [{market, stage, stageRunId, since, heartbeatAt}] for every occupied slot."""
    holders = {}
    for row in db.execute("""SELECT sl.resource_key,sl.owner_stage_run_id,c.created_at,c.heartbeat_at,s.stage,r.market
        FROM workflow_resource_slot sl JOIN workflow_stage_claim c ON c.stage_run_id=sl.owner_stage_run_id AND c.fence=sl.fence
        JOIN workflow_stage_run s ON s.stage_run_id=sl.owner_stage_run_id JOIN workflow_run r ON r.run_id=s.run_id"""):
        holders.setdefault(row["resource_key"], []).append(
            {"market": row["market"], "stage": row["stage"], "stageRunId": row["owner_stage_run_id"],
             "since": row["created_at"], "heartbeatAt": row["heartbeat_at"]})
    return holders


def _current(root, db, market, run, holders, policy, accounts):
    """The stage this run is on: running (with its claim) or queued (with what it waits for)."""
    from lib.workflow_dispatch import resources
    for stage in _stage_rows(db, run["run_id"]):
        if stage["state"] == "running":
            claim = db.execute("SELECT created_at,heartbeat_at FROM workflow_stage_claim WHERE stage_run_id=?",
                               (stage["stage_run_id"],)).fetchone()
            return {"stage": stage["stage"], "state": "running", "since": claim["created_at"] if claim else stage["started_at"],
                    "heartbeatAt": claim["heartbeat_at"] if claim else None, "waitingOn": [], "waitingKnown": True, "waitReason": None}
        if stage["state"] == "queued":
            waiting = []
            try:
                needed = resources(root, market, stage["stage"], policy, accounts=accounts)
            except (CycleError, KeyError, TypeError) as error:
                # Unknown requirements are not "nothing to wait for" (I05).
                return {"stage": stage["stage"], "state": "queued", "since": None, "heartbeatAt": None, "waitingOn": [],
                        "waitingKnown": False, "waitReason": str(error)[:80] or type(error).__name__}
            for key, slots in needed:
                taken = [row for row in holders.get(key, []) if row["stageRunId"] != stage["stage_run_id"]]
                if len(taken) >= slots:
                    waiting.append({"resource": key, "heldBy": [{k: row[k] for k in ("market", "stage", "since")} for row in taken]})
            return {"stage": stage["stage"], "state": "queued", "since": None, "heartbeatAt": None, "waitingOn": waiting,
                    "waitingKnown": True, "waitReason": None}
    return None


def _items(counts_json):
    try:
        value = json.loads(counts_json or "{}").get("items")
    except (TypeError, ValueError):
        return None
    return value if type(value) is int else None


def _finished(row):
    return {"market": row["market"], "runId": row["run_id"], "stage": row["stage"], "state": row["state"],
            "startedAt": row["started_at"], "finishedAt": row["finished_at"], "errorCode": row["error_code"],
            "items": _items(row["counts_json"]),
            "writeEvidence": (json.loads(row["counts_json"] or "{}") or {}).get("writeEvidence")}


def _lanes(store, market):
    """Continuous lanes from the projected operations page, with that projection's own time."""
    from lib.market_read_model import read
    try:
        page = read(store, market, "operations")
    except CycleError:
        page = None
    if not page:
        return {"available": False, "observedAt": None, "continuousSend": None, "agentReply": None}
    by_id = {row.get("id"): row for row in page.get("stages") or [] if isinstance(row, dict)}

    def lane(stage_id):
        row = by_id.get(stage_id)
        if not row:
            return None
        metric = row.get("metric") or {}
        return {"state": row.get("state"), "value": metric.get("value"), "label": metric.get("label"),
                "unit": metric.get("unit"), "scope": metric.get("scope"),
                "lastSuccessAt": row.get("lastSuccessAt"), "stopReason": row.get("stopReason")}
    return {"available": True, "observedAt": page.get("observationTime"),
            "continuousSend": lane("continuous_send"), "agentReply": lane("agent_reply")}


def _human_queue(root, store, market):
    """Creators in the conversation page's human queue, by the same classifier (I02); None if unreadable."""
    from lib.conversation_workbench import list_conversations
    try:
        counts = list_conversations(root, store, "human", limit=1, market=market)["counts"]
    except (CycleError, KeyError, TypeError, ValueError, sqlite3.Error):
        return None
    return {"human": counts.get("human"), "technical": counts.get("technical")}


def console(root, store, *, markets=None, recent=15):
    from lib.market_accounts import load_config
    from lib.market_registry import operational_market_keys
    from lib.operations_policy import load_policy
    from lib.operations_scheduler import scheduler_state
    from lib.operations_workflow import setting
    db = store.db
    markets = list(markets or operational_market_keys(root))
    try:
        accounts = load_config(root)["markets"]
    except (OSError, ValueError, KeyError):
        accounts = {}
    try:
        policy = load_policy(root)
    except (OSError, ValueError, CycleError):
        policy = {"kalodataMaxParallelMarkets": 2}
    holders = _holders(db)
    rows = []
    for market in markets:
        try:
            run = db.execute("SELECT * FROM workflow_run WHERE market=? AND state IN ('queued','running','stop_requested') "
                             "ORDER BY started_at DESC LIMIT 1", (market,)).fetchone()
            last = db.execute(f"""SELECT s.*,r.market FROM workflow_stage_run s JOIN workflow_run r ON r.run_id=s.run_id
                WHERE r.market=? AND s.finished_at IS NOT NULL AND s.state NOT IN {QUIET_STATES}
                ORDER BY s.finished_at DESC LIMIT 1""", (market,)).fetchone()
            attention = db.execute("""SELECT run_id,state,error_code,finished_at FROM workflow_run WHERE market=?
                ORDER BY started_at DESC LIMIT 1""", (market,)).fetchone()
            plan = db.execute("SELECT id FROM plan WHERE market=? AND institution='bjn-local-research'", (market,)).fetchone()
            human = db.execute("SELECT count(*) FROM service_case WHERE plan_id=? AND state='open'",
                               (plan[0],)).fetchone()[0] if plan and db.execute(
                "SELECT 1 FROM sqlite_master WHERE name='service_case'").fetchone() else None
            current_setting = setting(store, market)
            rows.append({
                "market": market, "available": True,
                "setting": {key: current_setting[key] for key in
                            ("automaticOperationsEnabled", "continuousSendEnabled", "fullCatalogWeeklyEnabled")},
                "run": {"runId": run["run_id"], "state": run["state"], "startedAt": run["started_at"]} if run else None,
                "current": _current(root, db, market, run, holders, policy, accounts) if run else None,
                "lastFinished": _finished(last) if last else None,
                "needsReview": {"runId": attention["run_id"], "state": attention["state"], "errorCode": attention["error_code"],
                                "at": attention["finished_at"]}
                               if attention and attention["state"] in ("needs_human", "failed") else None,
                # Open service cases are counted apart from the human queue: they are different things.
                "openHumanCases": human,
                "humanQueue": _human_queue(root, store, market),
                "lanes": _lanes(store, market)})
        except (CycleError, KeyError, TypeError, ValueError) as error:
            rows.append({"market": market, "available": False, "error": str(error)[:120]})
    recent_rows = [_finished(row) for row in db.execute(f"""SELECT s.*,r.market FROM workflow_stage_run s
        JOIN workflow_run r ON r.run_id=s.run_id WHERE s.finished_at IS NOT NULL AND s.state NOT IN {QUIET_STATES}
        ORDER BY s.finished_at DESC LIMIT ?""", (recent,))]
    occupied = [{"resource": key, **{k: row[k] for k in ("market", "stage", "since", "heartbeatAt")}}
                for key, values in sorted(holders.items()) for row in values]
    scheduler = scheduler_state(root)
    return {"schemaVersion": SCHEMA_VERSION, "checkedAt": store.clock(), "markets": rows, "resources": occupied,
            "recent": recent_rows, "scheduler": {"running": bool(scheduler.get("running")), "checkedAt": scheduler.get("checkedAt")},
            "stageLabels": STAGE_LABELS, "readOnly": True, "platformWrites": 0}
