"""Durable control plane for the automated Italy operating workflow.

This module deliberately contains no platform transport.  It owns the versioned switches, the
immutable run scope, stage transitions, generations and checkpoints.  Stage executors live at the
CLI boundary and must explicitly report their terminal evidence back through this ledger.
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from lib.second_cycle import CycleError, digest, encoded


BEIJING = ZoneInfo("Asia/Shanghai")
MARKETS = {"it"}
TRIGGERS = {"manual", "schedule", "recovery"}
STAGES = (
    "taplink_clean",
    "catalog",
    "taplink_prepare",
    "kalodata",
    "oecid",
    "send_pool",
)
STAGE_TERMINAL = {"completed", "quota_exhausted", "needs_human", "failed", "stopped", "skipped"}
STAGE_SUCCESS = {"completed", "quota_exhausted", "skipped"}
RUN_ACTIVE = {"queued", "running", "stop_requested"}
REQUEST_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{7,119}")


def _required(store):
    required = {
        "market_automation_setting", "market_automation_request", "workflow_run",
        "workflow_stage_run", "workflow_generation", "workflow_checkpoint",
    }
    present = {row[0] for row in store.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not required <= present:
        raise CycleError("workflow_schema_migration_required")


def _market(value):
    if value not in MARKETS:
        raise CycleError("workflow_market_invalid")
    return value


def _request_id(value):
    if not isinstance(value, str) or not REQUEST_ID.fullmatch(value):
        raise CycleError("workflow_request_invalid")
    return value


def setting(store, market="it"):
    _required(store)
    market = _market(market)
    row = store.db.execute("SELECT * FROM market_automation_setting WHERE market=?", (market,)).fetchone()
    if not row:
        return {
            "market": market,
            "automaticOperationsEnabled": False,
            "fullCatalogWeeklyEnabled": False,
            "continuousSendEnabled": False,
            "revision": 0,
            "updatedAt": 0,
        }
    return {
        "market": market,
        "automaticOperationsEnabled": bool(row["automatic_operations_enabled"]),
        "fullCatalogWeeklyEnabled": bool(row["full_catalog_weekly_enabled"]),
        "continuousSendEnabled": bool(row["continuous_send_enabled"]),
        "revision": row["revision"],
        "updatedAt": row["updated_at"],
    }


def save_setting(store, market, request_id, expected_revision, changes):
    """Save only the three home switches.  Replays are idempotent and never start a worker."""
    _required(store)
    market = _market(market)
    request_id = _request_id(request_id)
    if type(expected_revision) is not int or expected_revision < 0 or not isinstance(changes, dict):
        raise CycleError("workflow_setting_invalid")
    allowed = {
        "automaticOperationsEnabled", "fullCatalogWeeklyEnabled", "continuousSendEnabled",
    }
    if not changes or set(changes) - allowed or any(type(value) is not bool for value in changes.values()):
        raise CycleError("workflow_setting_invalid")
    payload = encoded({"market": market, "changes": changes})
    with store.tx():
        prior = store.db.execute(
            "SELECT payload_json,result_revision FROM market_automation_request WHERE request_id=?",
            (request_id,),
        ).fetchone()
        if prior:
            if prior["payload_json"] != payload:
                raise CycleError("workflow_request_conflict")
            return setting(store, market) | {"duplicate": True}
        current = setting(store, market)
        if current["revision"] != expected_revision:
            raise CycleError("workflow_revision_conflict")
        updated = {**current, **changes}
        revision = expected_revision + 1
        now = store.clock()
        store.db.execute(
            """INSERT INTO market_automation_setting VALUES(?,?,?,?,?,?)
            ON CONFLICT(market) DO UPDATE SET
              automatic_operations_enabled=excluded.automatic_operations_enabled,
              full_catalog_weekly_enabled=excluded.full_catalog_weekly_enabled,
              continuous_send_enabled=excluded.continuous_send_enabled,
              revision=excluded.revision,updated_at=excluded.updated_at""",
            (market, int(updated["automaticOperationsEnabled"]), int(updated["fullCatalogWeeklyEnabled"]),
             int(updated["continuousSendEnabled"]), revision, now),
        )
        store.db.execute(
            "INSERT INTO market_automation_request VALUES(?,?,?,?,?,?)",
            (request_id, market, expected_revision, payload, revision, now),
        )
        if "continuousSendEnabled" in changes and store.db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='continuous_send_control'"
        ).fetchone():
            plan = store.db.execute(
                "SELECT id FROM plan WHERE institution='bjn-local-research' AND market=?", (market,),
            ).fetchone()
            if plan:
                control = store.db.execute(
                    "SELECT revision FROM continuous_send_control WHERE plan_id=?", (plan[0],),
                ).fetchone()
                control_revision = (control[0] if control else 0) + 1
                store.db.execute(
                    """INSERT INTO continuous_send_control VALUES(?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(plan_id) DO UPDATE SET
                      automatic_enabled=excluded.automatic_enabled,
                      stop_requested=CASE WHEN excluded.automatic_enabled=1 THEN 0 ELSE continuous_send_control.stop_requested END,
                      revision=excluded.revision,updated_at=excluded.updated_at""",
                    (plan[0], int(changes["continuousSendEnabled"]), 0, 0, "16:30", "24:00",
                     "standard", control_revision, now),
                )
    return setting(store, market) | {"duplicate": False}


def applicable_sources(store, market="it", scheduled_at=None):
    current = setting(store, market)
    stamp = store.clock() if scheduled_at is None else float(scheduled_at)
    monday = datetime.fromtimestamp(stamp, BEIJING).weekday() == 0
    sources = ["campaign"]
    if monday and current["fullCatalogWeeklyEnabled"]:
        sources.insert(0, "selected")
    return sources


def create_run(store, *, market="it", trigger_source="manual", scheduled_at=None, request_id=None,
               only_stage=None, sources=None):
    """Create one immutable run scope.  A manual run is allowed while the schedule switch is off."""
    _required(store)
    market = _market(market)
    if trigger_source not in TRIGGERS:
        raise CycleError("workflow_trigger_invalid")
    if request_id is not None:
        request_id = _request_id(request_id)
    stamp = store.clock() if scheduled_at is None else float(scheduled_at)
    current = setting(store, market)
    sources = list(sources) if sources is not None else applicable_sources(store, market, stamp)
    if not sources or any(source not in ('selected','campaign') for source in sources) or len(set(sources))!=len(sources):
        raise CycleError('workflow_sources_invalid')
    if only_stage is not None and only_stage not in STAGES:raise CycleError('workflow_stage_invalid')
    if trigger_source == "schedule" and not current["automaticOperationsEnabled"]:
        raise CycleError("workflow_automation_disabled")
    key = request_id or f"{market}:{trigger_source}:{int(stamp)}"
    run_id = "workflow-" + digest([key, market, trigger_source, stamp, sources, current["revision"],only_stage])[:28]
    with store.tx():
        existing = store.db.execute("SELECT * FROM workflow_run WHERE run_id=?", (run_id,)).fetchone()
        if existing:
            return run_payload(store, run_id) | {"duplicate": True}
        active = store.db.execute(
            "SELECT run_id FROM workflow_run WHERE market=? AND state IN ('queued','running','stop_requested') "
            "ORDER BY started_at DESC LIMIT 1", (market,),
        ).fetchone()
        if active:
            raise CycleError("workflow_run_active")
        store.db.execute(
            "INSERT INTO workflow_run VALUES(?,?,?,?,?,?,'queued',?,NULL,NULL,NULL)",
            (run_id, market, trigger_source, stamp, encoded(sources), current["revision"], store.clock()),
        )
        for position, stage in enumerate(STAGES):
            state = ('skipped' if only_stage is not None and stage!=only_stage else
                     "skipped" if stage == "taplink_clean" and not (
                         datetime.fromtimestamp(stamp, BEIJING).weekday() == 0
                     ) else "waiting_upstream")
            finished = store.clock() if state == "skipped" else None
            store.db.execute(
                "INSERT INTO workflow_stage_run(stage_run_id,run_id,stage,position,state,finished_at) "
                "VALUES(?,?,?,?,?,?)",
                ("stage-" + digest([run_id, stage])[:28], run_id, stage, position, state, finished),
            )
        _release_next(store, run_id)
    return run_payload(store, run_id) | {"duplicate": False}


def request_stop(store, run_id, expected_state="running"):
    _required(store)
    with store.tx():
        row = store.db.execute("SELECT state FROM workflow_run WHERE run_id=?", (run_id,)).fetchone()
        if not row:
            raise CycleError("workflow_run_missing")
        if row["state"] == "stop_requested":
            return run_payload(store, run_id) | {"duplicate": True}
        if row["state"] != expected_state or row["state"] not in RUN_ACTIVE:
            raise CycleError("workflow_not_stoppable")
        now = store.clock()
        store.db.execute(
            "UPDATE workflow_run SET state='stop_requested',stop_requested_at=? WHERE run_id=?",
            (now, run_id),
        )
    return run_payload(store, run_id) | {"duplicate": False}


def start_stage(store, run_id, stage, *, input_generation_id=None):
    _required(store)
    if stage not in STAGES:
        raise CycleError("workflow_stage_invalid")
    with store.tx():
        run = store.db.execute("SELECT state FROM workflow_run WHERE run_id=?", (run_id,)).fetchone()
        row = store.db.execute(
            "SELECT * FROM workflow_stage_run WHERE run_id=? AND stage=?", (run_id, stage),
        ).fetchone()
        if not run or not row:
            raise CycleError("workflow_run_missing")
        if run["state"] == "stop_requested":
            raise CycleError("workflow_stop_requested")
        if row["state"] == "running":
            return stage_payload(row) | {"duplicate": True}
        if row["state"] != "queued":
            raise CycleError("workflow_stage_not_queued")
        now = store.clock()
        store.db.execute("UPDATE workflow_run SET state='running' WHERE run_id=?", (run_id,))
        store.db.execute(
            "UPDATE workflow_stage_run SET state='running',input_generation_id=?,started_at=?,error_code=NULL "
            "WHERE run_id=? AND stage=?",
            (input_generation_id, now, run_id, stage),
        )
    return stage_state(store, run_id, stage) | {"duplicate": False}


def update_checkpoint(store, run_id, stage, checkpoint_key, value, counts=None):
    _required(store)
    if stage not in STAGES or not isinstance(checkpoint_key, str) or not checkpoint_key or len(checkpoint_key) > 80:
        raise CycleError("workflow_checkpoint_invalid")
    with store.tx():
        row = store.db.execute(
            "SELECT state FROM workflow_stage_run WHERE run_id=? AND stage=?", (run_id, stage),
        ).fetchone()
        if not row or row["state"] != "running":
            raise CycleError("workflow_stage_not_running")
        now = store.clock()
        store.db.execute(
            """INSERT INTO workflow_checkpoint VALUES(?,?,?,?,?)
            ON CONFLICT(run_id,stage,checkpoint_key) DO UPDATE SET
              value_json=excluded.value_json,updated_at=excluded.updated_at""",
            (run_id, stage, checkpoint_key, encoded(value), now),
        )
        store.db.execute(
            "UPDATE workflow_stage_run SET checkpoint_json=?,counts_json=COALESCE(?,counts_json) "
            "WHERE run_id=? AND stage=?",
            (encoded({"key": checkpoint_key, "value": value}), encoded(counts) if counts is not None else None,
             run_id, stage),
        )


def finish_stage(store, run_id, stage, *, state, item_count=0, scope=None, payload=None,
                 complete=True, platform_writes=0, error_code=None):
    """Finish one stage and atomically publish its generation when its barrier is satisfied."""
    _required(store)
    if stage not in STAGES or state not in STAGE_TERMINAL:
        raise CycleError("workflow_stage_state_invalid")
    if type(item_count) is not int or item_count < 0 or type(platform_writes) is not int or platform_writes < 0:
        raise CycleError("workflow_stage_result_invalid")
    if stage in {"catalog", "taplink_prepare", "oecid", "send_pool"} and state == "completed" and not complete:
        raise CycleError("workflow_generation_incomplete")
    if stage == "kalodata" and state not in {"completed", "quota_exhausted", "failed", "needs_human", "stopped"}:
        raise CycleError("workflow_stage_state_invalid")
    with store.tx():
        run = store.db.execute("SELECT * FROM workflow_run WHERE run_id=?", (run_id,)).fetchone()
        row = store.db.execute(
            "SELECT * FROM workflow_stage_run WHERE run_id=? AND stage=?", (run_id, stage),
        ).fetchone()
        if not run or not row:
            raise CycleError("workflow_run_missing")
        if row["state"] in STAGE_TERMINAL:
            return stage_payload(row) | {"duplicate": True}
        if row["state"] != "running":
            raise CycleError("workflow_stage_not_running")
        now = store.clock()
        generation_id = None
        publishable = state in {"completed", "quota_exhausted"}
        if publishable:
            facts = {
                "runId": run_id, "stage": stage, "market": run["market"], "scope": scope or {},
                "payload": payload or {}, "itemCount": item_count, "complete": bool(complete),
            }
            generation_id = "generation-" + digest(facts)[:28]
            store.db.execute(
                "INSERT OR IGNORE INTO workflow_generation VALUES(?,?,?,?,?,'published',?,?,?,?)",
                (generation_id, run_id, stage, run["market"], digest(scope or {}), item_count,
                 encoded(facts), now, now),
            )
        store.db.execute(
            "UPDATE workflow_stage_run SET state=?,output_generation_id=?,platform_writes=?,finished_at=?,"
            "error_code=? WHERE run_id=? AND stage=?",
            (state, generation_id, platform_writes, now, error_code, run_id, stage),
        )
        if state in STAGE_SUCCESS:
            _release_next(store, run_id)
        else:
            run_state = "stopped" if state == "stopped" else "needs_human" if state == "needs_human" else "failed"
            store.db.execute(
                "UPDATE workflow_run SET state=?,finished_at=?,error_code=? WHERE run_id=?",
                (run_state, now, error_code, run_id),
            )
        _settle_run(store, run_id)
    return stage_state(store, run_id, stage) | {"duplicate": False}


def _release_next(store, run_id):
    rows = list(store.db.execute(
        "SELECT stage,state,position FROM workflow_stage_run WHERE run_id=? ORDER BY position", (run_id,),
    ))
    for index, row in enumerate(rows):
        if row["state"] in STAGE_SUCCESS:
            continue
        if row["state"] == "waiting_upstream" and all(prior["state"] in STAGE_SUCCESS for prior in rows[:index]):
            store.db.execute(
                "UPDATE workflow_stage_run SET state='queued' WHERE run_id=? AND stage=?",
                (run_id, row["stage"]),
            )
        break


def _settle_run(store, run_id):
    rows = list(store.db.execute("SELECT state FROM workflow_stage_run WHERE run_id=?", (run_id,)))
    if rows and all(row["state"] in STAGE_SUCCESS for row in rows):
        now = store.clock()
        store.db.execute(
            "UPDATE workflow_run SET state='completed',finished_at=?,error_code=NULL WHERE run_id=?",
            (now, run_id),
        )


def stage_payload(row):
    return {
        "stageRunId": row["stage_run_id"], "stage": row["stage"], "position": row["position"],
        "state": row["state"], "inputGenerationId": row["input_generation_id"],
        "outputGenerationId": row["output_generation_id"],
        "checkpoint": json.loads(row["checkpoint_json"] or "{}"),
        "counts": json.loads(row["counts_json"] or "{}"),
        "platformWrites": row["platform_writes"], "startedAt": row["started_at"],
        "finishedAt": row["finished_at"], "errorCode": row["error_code"],
    }


def stage_state(store, run_id, stage):
    row = store.db.execute(
        "SELECT * FROM workflow_stage_run WHERE run_id=? AND stage=?", (run_id, stage),
    ).fetchone()
    if not row:
        raise CycleError("workflow_stage_missing")
    return stage_payload(row)


def run_payload(store, run_id):
    row = store.db.execute("SELECT * FROM workflow_run WHERE run_id=?", (run_id,)).fetchone()
    if not row:
        raise CycleError("workflow_run_missing")
    stages = [stage_payload(stage) for stage in store.db.execute(
        "SELECT * FROM workflow_stage_run WHERE run_id=? ORDER BY position", (run_id,),
    )]
    return {
        "runId": row["run_id"], "market": row["market"], "triggerSource": row["trigger_source"],
        "scheduledAt": row["scheduled_at"], "applicableSources": json.loads(row["applicable_sources_json"]),
        "configRevision": row["config_revision"], "state": row["state"],
        "startedAt": row["started_at"], "finishedAt": row["finished_at"],
        "stopRequestedAt": row["stop_requested_at"], "errorCode": row["error_code"], "stages": stages,
    }


def status(store, market="it", limit=10):
    _required(store)
    market = _market(market)
    if type(limit) is not int or not 1 <= limit <= 50:
        raise CycleError("workflow_query_invalid")
    rows = list(store.db.execute(
        "SELECT run_id FROM workflow_run WHERE market=? ORDER BY started_at DESC LIMIT ?", (market, limit),
    ))
    current = run_payload(store, rows[0]["run_id"]) if rows else None
    history = [run_payload(store, row["run_id"]) for row in rows]
    return {
        "schemaVersion": "bdhub.operations-workflow.v1", "market": market,
        "setting": setting(store, market), "current": current, "history": history,
        "platformWrites": sum(
            stage["platformWrites"] for run in history for stage in run["stages"]
        ),
    }
