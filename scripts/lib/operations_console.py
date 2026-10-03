"""Read-only cross-market console: what each market is doing, waiting for and last achieved (H12).

It reads only existing ledgers -- workflow runs/stages, claims and resource slots, and continuous
workers' own status -- and never starts, retries or claims anything. Every figure keeps its own
time; a market whose ledger cannot be read is reported as unavailable, never as zero.
"""
from __future__ import annotations

import json
import math
import sqlite3
from pathlib import Path

from lib.second_cycle import CycleError

SCHEMA_VERSION = "bdhub.operations-console.v1"
QUIET_STATES = ("skipped", "waiting_upstream")
STAGE_LABELS = {"taplink_clean": "TapLink 清理", "catalog": "货盘", "taplink_prepare": "TapLink 建链",
                "kalodata": "Kalodata 线索", "oecid": "OECID 身份", "send_pool": "发送池"}
# Senders may wait 300 s, then spend up to 180 s checking an original intent. A live PID alone
# does not prove the last state is current; give those normal cycles room before calling it stale.
LANE_MAX_AGE_SECONDS = 600


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


def _status_record(path):
    """Read existing worker evidence only; do not call status helpers that initialize stores."""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _stamp(value):
    return value if type(value) in (int, float) and math.isfinite(value) and value > 0 else None


def _lane(record, now, *, enabled=None, sample=None, control_at=None):
    """A process observation and its own sample time, independent of the console read time."""
    from lib.process_liveness import record_alive
    sample = record if sample is None else sample
    observed = _stamp(sample.get("checkedAt")) or _stamp(sample.get("startedAt"))
    running = record_alive(record) if type(record.get("pid")) is int and record["pid"] > 0 else None
    if record.get("running") is False:
        running = False
    state = sample.get("state")
    state = state if isinstance(state, str) and 0 < len(state) <= 40 else None
    reason = None
    if observed is None:
        reason = "status_missing"
    elif not -60 <= now - observed <= LANE_MAX_AGE_SECONDS:
        reason = "status_stale"
    elif _stamp(record.get("startedAt")) and observed < record["startedAt"]:
        reason = "previous_worker_sample"
    elif running is not True:
        reason = "worker_not_running" if running is False else "worker_unknown"
    elif not state:
        reason = "status_missing"
    elif _stamp(control_at) and observed < control_at:
        reason = "control_changed"
    fresh = reason is None
    stop_reason = next((sample[key][:160] for key in ("stopReason", "reason", "error")
                        if isinstance(sample.get(key), str) and sample[key]), None)
    return {"state": state if fresh else "unverified", "reportedState": state,
            "observedAt": observed, "fresh": fresh, "running": running, "enabled": enabled,
            "statusReason": reason, "value": None, "label": None, "unit": None, "scope": None,
            "lastSuccessAt": _stamp(sample.get("lastSuccessAt")),
            "stopReason": stop_reason}


def _lanes(root, store, market, automation):
    """Read both independently authorized workers even while the supply workflow is disabled.

    The IT sender's sample is in continuous_send_runtime; its launcher JSON identifies the process.
    Other senders and all AI workers publish their own status JSON. These are the existing sources,
    not a second persistent projection. Every query here is SELECT, on the caller's read-only store.
    """
    root = Path(root)
    now = store.clock()
    plan = store.db.execute("SELECT id,state FROM plan WHERE market=? AND institution='bjn-local-research'",
                            (market,)).fetchone()
    sender = _status_record(root / "var" / ("continuous-send-worker.json" if market == "it" else
                                             f"market-send-worker-{market}.json"))
    agent = _status_record(root / "var" / ("agent-reply-status.json" if market == "it" else
                                            f"agent-reply-status-{market}.json"))
    send_enabled = agent_enabled = None
    send_control_at = agent_control_at = None
    sample = sender
    if plan:
        try:
            cfg = store.db.execute("SELECT * FROM continuous_send_control WHERE plan_id=?", (plan[0],)).fetchone()
            send_enabled = plan["state"] == "active" and not (cfg and cfg["stop_requested"]) and bool(
                cfg and (cfg["run_requested"] or cfg["automatic_enabled"]) or
                market != "it" and automation["continuousSendEnabled"])
            send_control_at = cfg["updated_at"] if cfg else None
            if market == "it":
                # _runtime is a SELECT-only reader; status() without a supplied store would open a writable DB.
                from lib.continuous_send import _runtime
                runtime = _runtime(store, plan[0])
                sample = runtime | {"checkedAt": runtime["seenAt"], "startedAt": None}
        except (sqlite3.Error, KeyError, TypeError):
            sample = {}
        try:
            # agent_setting only reads the existing table and supplies disabled defaults; it does not migrate.
            from lib.template_library import agent_setting
            cfg = agent_setting(store, plan[0])
            agent_enabled = plan["state"] == "active" and cfg["enabled"]
            agent_control_at = cfg.get("updatedAt")
        except (sqlite3.Error, KeyError, TypeError, ValueError):
            agent = {}
    else:
        sender = agent = sample = {}
    send_lane = _lane(sender, now, enabled=send_enabled, sample=sample, control_at=send_control_at)
    agent_lane = _lane(agent, now, enabled=agent_enabled, control_at=agent_control_at)
    stamps = [row["observedAt"] for row in (send_lane, agent_lane) if row["observedAt"] is not None]
    return {"available": send_lane["fresh"] or agent_lane["fresh"], "observedAt": min(stamps) if stamps else None,
            "continuousSend": send_lane, "agentReply": agent_lane}


def _selection_paused(root, db, market):
    """The newest catalog pass paused its full-managed selection and PIDs are still waiting; None otherwise."""
    from lib.operations_scheduler import SELECTION_BACKLOG_RETRY_SECONDS, selection_backlog
    row = db.execute("SELECT payload_json,created_at FROM workflow_generation WHERE market=? AND stage='catalog' "
                     "ORDER BY created_at DESC LIMIT 1", (market,)).fetchone()
    try:
        paused = ((json.loads(row["payload_json"] or "{}").get("scope") or {}).get("selectionPaused")) if row else None
    except (TypeError, ValueError):
        return None
    if not isinstance(paused, dict):
        return None
    backlog = selection_backlog(root, market)
    if backlog == 0:
        return None
    return {"reason": str(paused.get("reason") or "")[:80], "pending": backlog,
            "pausedAt": row["created_at"], "retryAt": row["created_at"] + SELECTION_BACKLOG_RETRY_SECONDS}


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
        # Unknown capacity: waits that depend on it read "reason to be checked" (I05), never a guessed default.
        policy = None
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
                "selectionPaused": _selection_paused(root, db, market),
                "lanes": _lanes(root, store, market, current_setting)})
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
