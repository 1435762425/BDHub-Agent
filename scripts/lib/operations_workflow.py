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
from pathlib import Path
from zoneinfo import ZoneInfo

from lib.market_registry import enabled_market_keys, supports
from lib.second_cycle import CycleError, digest, encoded


BEIJING = ZoneInfo("Asia/Shanghai")
ROOT = Path(__file__).resolve().parents[2]
MARKETS = frozenset(enabled_market_keys(ROOT))
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
# Stages whose children can POST to the platform (link delete/create, selection, Campaign join).
WRITE_CAPABLE_STAGES = {"taplink_clean", "catalog", "taplink_prepare"}
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


RECEIPT_SQL = ("CREATE TABLE IF NOT EXISTS market_automation_receipt(request_id TEXT PRIMARY KEY,market TEXT NOT NULL,"
               "payload_hash TEXT NOT NULL,committed_revision INTEGER NOT NULL,committed_at REAL NOT NULL,setting_json TEXT NOT NULL)")


def _setting_receipt(store, market, request_id, payload):
    """The original outcome of an already committed request, or None for a new request."""
    prior = store.db.execute(
        "SELECT market,payload_json,result_revision,created_at FROM market_automation_request WHERE request_id=?",
        (request_id,),
    ).fetchone()
    if not prior:
        return None
    if prior["payload_json"] != payload or prior["market"] != market:
        raise CycleError("workflow_request_conflict")
    receipt = store.db.execute(
        "SELECT setting_json FROM market_automation_receipt WHERE request_id=?", (request_id,),
    ).fetchone() if store.db.execute(
        "SELECT 1 FROM sqlite_master WHERE name='market_automation_receipt'").fetchone() else None
    if receipt:
        return json.loads(receipt[0]) | {"duplicate": True, "originalAvailable": True}
    # Committed before receipts were kept: the revision is known, the committed values are not.
    current = setting(store, market)
    return current | {"revision": prior["result_revision"], "updatedAt": prior["created_at"],
                      "duplicate": True, "originalAvailable": False}


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
    # A replay returns its own immutable receipt; enable-time preconditions only guard new writes.
    replay = _setting_receipt(store, market, request_id, payload)
    if replay is not None:
        return replay
    if changes.get("fullCatalogWeeklyEnabled") and not supports(ROOT, market, "fullManagedCatalog"):
        raise CycleError("full_catalog_not_supported")
    if changes.get("continuousSendEnabled") is True:
        from lib.template_library import require_send_template_approval
        require_send_template_approval(store, ROOT, market)
    if market != 'it' and (changes.get('automaticOperationsEnabled') or changes.get('continuousSendEnabled')):
        from lib.account_identity import current_generation
        from lib.market_accounts import load_config
        pair=load_config(ROOT)['markets'][market];verified=set()
        for account in pair['accounts']:
            generation=current_generation(store,market,account)
            verified.update(name for name,row in (generation or {}).get('capabilities',{}).items()
                            if row.get('state')=='verified')
        required={'campaign','campaign_join','catalog_read','inbox_read','message_send','oecid_find','taplink'}
        if supports(ROOT,market,'fullManagedCatalog'):required.add('product_select')
        if not required<=verified:raise CycleError('market_automation_capabilities_pending')
    with store.tx():
        store.db.execute(RECEIPT_SQL)
        replay = _setting_receipt(store, market, request_id, payload)
        if replay is not None:
            return replay
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
        # The receipt is what this request committed, read inside its own transaction.
        committed = setting(store, market)
        store.db.execute("INSERT INTO market_automation_receipt VALUES(?,?,?,?,?,?)",
                         (request_id, market, digest(payload), committed["revision"], now, encoded(committed)))
    return committed | {"duplicate": False, "originalAvailable": True}


def maintenance_weekday():
    """The saved weekly TapLink maintenance day (0=Monday); the scheduler and pages read the same value."""
    from lib.jobs import load
    return load(ROOT)["jobs"]["taplink_clean"].get("weekday", 0)


def applicable_sources(store, market="it", scheduled_at=None):
    current = setting(store, market)
    stamp = store.clock() if scheduled_at is None else float(scheduled_at)
    maintenance_day = datetime.fromtimestamp(stamp, BEIJING).weekday() == maintenance_weekday()
    sources = ["campaign"]
    if maintenance_day and current["fullCatalogWeeklyEnabled"] and supports(ROOT, market, "fullManagedCatalog"):
        sources.insert(0, "selected")
    return sources


def create_run(store, *, market="it", trigger_source="manual", scheduled_at=None, request_id=None,
               only_stage=None, sources=None, from_stage=None):
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
    if 'selected' in sources and not supports(ROOT, market, 'fullManagedCatalog'):
        raise CycleError('full_catalog_not_supported')
    if only_stage is not None and only_stage not in STAGES:raise CycleError('workflow_stage_invalid')
    if trigger_source == "schedule" and not current["automaticOperationsEnabled"]:
        raise CycleError("workflow_automation_disabled")
    if from_stage is not None and (from_stage not in STAGES or only_stage is not None):raise CycleError('workflow_stage_invalid')
    key = request_id or f"{market}:{trigger_source}:{int(stamp)}"
    run_id = "workflow-" + digest([key, market, trigger_source, stamp, sources, current["revision"],only_stage]+([from_stage] if from_stage is not None else []))[:28]
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
        weekday = maintenance_weekday()
        for position, stage in enumerate(STAGES):
            state = ('skipped' if from_stage is not None and position<STAGES.index(from_stage) else
                     'skipped' if only_stage is not None and stage!=only_stage else
                     # Weekly link maintenance: full inventory for full-managed markets, a read-only
                     # binding check for the others (see SubprocessStageExecutor.execute).
                     "skipped" if stage == "taplink_clean" and
                     datetime.fromtimestamp(stamp, BEIJING).weekday() != weekday else "waiting_upstream")
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


def retry_failed_stage(store, run_id, *, now=None, delay=3600, max_retries=3):
    """Resume one failed scheduled stage from its original upstream generation."""
    _required(store)
    stamp=store.clock() if now is None else float(now)
    with store.tx():
        run=store.db.execute('SELECT * FROM workflow_run WHERE run_id=?',(run_id,)).fetchone()
        if not run or run['state']!='failed' or run['trigger_source']!='schedule':
            return {'state':'not_applicable'}
        stage=store.db.execute("SELECT * FROM workflow_stage_run WHERE run_id=? AND state='failed' ORDER BY position LIMIT 1",
                               (run_id,)).fetchone()
        evidence=json.loads(stage['counts_json'] or '{}').get('writeEvidence') if stage else None
        # Write-capable stages retry only on a recorded zero-write proof; a legacy numeric 0
        # or a lost/unknown child result is never read as zero.
        if not stage or stage['platform_writes'] or not stage['finished_at'] or (
                stage['stage'] in WRITE_CAPABLE_STAGES and evidence!='zero'):
            store.db.execute("UPDATE workflow_run SET state='needs_human',error_code='workflow_retry_requires_review' WHERE run_id=?",
                             (run_id,))
            return {'state':'needs_human','reason':'workflow_retry_requires_review'}
        code=str(stage['error_code'] or '')
        if code=='global_catalog_not_published' or any(value in code.lower() for value in ('unknown','unresolved','ambiguous')):
            store.db.execute("UPDATE workflow_run SET state='needs_human',error_code='workflow_retry_requires_review' WHERE run_id=?",
                             (run_id,))
            return {'state':'needs_human','reason':'workflow_retry_requires_review'}
        prior=store.db.execute("SELECT value_json FROM workflow_checkpoint WHERE run_id=? AND stage=? AND checkpoint_key='auto_retry'",
                               (run_id,stage['stage'])).fetchone()
        attempts=int(json.loads(prior[0])['attempts']) if prior else 0
        if attempts>=max_retries:
            store.db.execute("UPDATE workflow_run SET state='needs_human',error_code='workflow_retry_exhausted' WHERE run_id=?",
                             (run_id,))
            return {'state':'exhausted','attempts':attempts}
        due=float(stage['finished_at'])+delay
        if stamp<due:return {'state':'waiting','nextAt':due,'attempts':attempts}
        if store.db.execute('SELECT 1 FROM workflow_stage_claim WHERE stage_run_id=?',(stage['stage_run_id'],)).fetchone():
            store.db.execute("UPDATE workflow_run SET state='needs_human',error_code='workflow_retry_claim_active' WHERE run_id=?",
                             (run_id,))
            return {'state':'needs_human','reason':'workflow_retry_claim_active'}
        if store.db.execute("SELECT 1 FROM workflow_run WHERE market=? AND run_id!=? AND state IN ('queued','running','stop_requested')",
                            (run['market'],run_id)).fetchone():
            return {'state':'waiting','nextAt':stamp+60,'attempts':attempts}
        evidence={'attempts':attempts+1,'previousError':code,'previousFinishedAt':stage['finished_at'],
                  'previousPlatformWrites':stage['platform_writes']}
        store.db.execute("INSERT INTO workflow_checkpoint VALUES(?,?,?,?,?) ON CONFLICT(run_id,stage,checkpoint_key) "
                         "DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at",
                         (run_id,stage['stage'],'auto_retry',encoded(evidence),stamp))
        store.db.execute("UPDATE workflow_stage_run SET state='queued',started_at=NULL,finished_at=NULL,error_code=NULL "
                         "WHERE stage_run_id=?",(stage['stage_run_id'],))
        store.db.execute("UPDATE workflow_run SET state='running',finished_at=NULL,error_code=NULL WHERE run_id=?",
                         (run_id,))
        return {'state':'resumed','attempts':attempts+1,'stage':stage['stage']}


def resume_short_names(store, market, run_id, request_id, *, root=ROOT):
    """Requeue the original IT TapLink stage after its pre-write name gap is filled."""
    _required(store)
    market = _market(market)
    request_id = _request_id(request_id)
    if not isinstance(run_id, str):
        raise CycleError("workflow_recovery_scope_invalid")
    checkpoint_key = "recovery:catalog_short_names_incomplete"
    with store.tx():
        run = store.db.execute("SELECT * FROM workflow_run WHERE run_id=?", (run_id,)).fetchone()
        if not run:
            raise CycleError("workflow_run_missing")
        if run["market"] != market:
            raise CycleError("workflow_market_mismatch")
        if market != "it":
            raise CycleError("workflow_recovery_scope_invalid")
        prior = store.db.execute(
            "SELECT value_json FROM workflow_checkpoint WHERE run_id=? AND stage='taplink_prepare' AND checkpoint_key=?",
            (run_id, checkpoint_key),
        ).fetchone()
        if prior:
            if json.loads(prior["value_json"])["requestId"] != request_id:
                raise CycleError("workflow_recovery_already_requested")
            return run_payload(store, run_id) | {"duplicate": True}
        stages = list(store.db.execute(
            "SELECT * FROM workflow_stage_run WHERE run_id=? ORDER BY position", (run_id,),
        ))
        by_stage = {row["stage"]: row for row in stages}
        current = by_stage.get("taplink_prepare")
        catalog = by_stage.get("catalog")
        if (run["state"] != "needs_human" or run["error_code"] != "catalog_short_names_incomplete"
                or run["stop_requested_at"] is not None
                or json.loads(run["applicable_sources_json"]) != ["campaign"]
                or not current or current["state"] != "needs_human"
                or current["error_code"] != "catalog_short_names_incomplete"
                or current["platform_writes"] != 0 or current["output_generation_id"] is not None
                or not catalog or catalog["state"] != "completed"
                or not catalog["output_generation_id"]
                or current["input_generation_id"] != catalog["output_generation_id"]
                or any(row["state"] not in STAGE_SUCCESS for row in stages[:current["position"]])
                or any(row["state"] != "waiting_upstream" for row in stages[current["position"] + 1:])):
            raise CycleError("workflow_recovery_state_invalid")
        if store.db.execute("SELECT 1 FROM workflow_stage_claim WHERE stage_run_id=?",
                            (current["stage_run_id"],)).fetchone():
            raise CycleError("workflow_recovery_claim_active")
        if store.db.execute(
            "SELECT 1 FROM workflow_run WHERE market=? AND run_id!=? AND state IN ('queued','running','stop_requested')",
            (market, run_id),
        ).fetchone():
            raise CycleError("workflow_run_active")
        from lib.catalog_names import gap
        names = gap(root, market)
        if names["missing"] != 0 or names["invalidSourceTitles"] != 0:
            raise CycleError("catalog_short_names_incomplete")
        now = store.clock()
        evidence = {"requestId": request_id, "previousErrorCode": current["error_code"],
                    "previousStartedAt": current["started_at"], "previousFinishedAt": current["finished_at"],
                    "previousPlatformWrites": current["platform_writes"], "shortNames": {
                        "scope": names["scope"], "ready": names["ready"], "missing": names["missing"]}}
        store.db.execute(
            "INSERT INTO workflow_checkpoint VALUES(?,?,?,?,?)",
            (run_id, "taplink_prepare", checkpoint_key, encoded(evidence), now),
        )
        store.db.execute(
            "UPDATE workflow_stage_run SET state='queued',started_at=NULL,finished_at=NULL,error_code=NULL "
            "WHERE stage_run_id=?", (current["stage_run_id"],),
        )
        store.db.execute(
            "UPDATE workflow_run SET state='running',finished_at=NULL,error_code=NULL WHERE run_id=?",
            (run_id,),
        )
    return run_payload(store, run_id) | {"duplicate": False}


def resume_kalodata_preflight(store, market, run_id, request_id, *, root=ROOT):
    """Retry the original IT Kalodata stage after an argv failure before its CLI started."""
    _required(store)
    market = _market(market)
    request_id = _request_id(request_id)
    if not isinstance(run_id, str):
        raise CycleError("workflow_recovery_scope_invalid")
    checkpoint_key = "recovery:kalodata_sales_market_arg"
    with store.tx():
        run = store.db.execute("SELECT * FROM workflow_run WHERE run_id=?", (run_id,)).fetchone()
        if not run:
            raise CycleError("workflow_run_missing")
        if run["market"] != market:
            raise CycleError("workflow_market_mismatch")
        if market != "it":
            raise CycleError("workflow_recovery_scope_invalid")
        prior = store.db.execute(
            "SELECT value_json FROM workflow_checkpoint WHERE run_id=? AND stage='kalodata' AND checkpoint_key=?",
            (run_id, checkpoint_key),
        ).fetchone()
        if prior:
            if json.loads(prior["value_json"])["requestId"] != request_id:
                raise CycleError("workflow_recovery_already_requested")
            return run_payload(store, run_id) | {"duplicate": True}
        stages = list(store.db.execute(
            "SELECT * FROM workflow_stage_run WHERE run_id=? ORDER BY position", (run_id,),
        ))
        by_stage = {row["stage"]: row for row in stages}
        current = by_stage.get("kalodata")
        upstream = by_stage.get("taplink_prepare")
        if (run["state"] != "failed" or run["error_code"] != "kalodata-sales_failed"
                or run["stop_requested_at"] is not None
                or json.loads(run["applicable_sources_json"]) != ["campaign"]
                or not current or current["state"] != "failed"
                or current["error_code"] != "kalodata-sales_failed"
                or current["platform_writes"] != 0 or current["output_generation_id"] is not None
                or json.loads(current["counts_json"] or "{}").get("items") != 0
                or current["started_at"] is None or current["finished_at"] is None
                or not 0 <= current["finished_at"] - current["started_at"] <= 5
                or not upstream or upstream["state"] != "completed"
                or not upstream["output_generation_id"]
                or current["input_generation_id"] != upstream["output_generation_id"]
                or any(row["state"] not in STAGE_SUCCESS for row in stages[:current["position"]])
                or any(row["state"] != "waiting_upstream" for row in stages[current["position"] + 1:])):
            raise CycleError("workflow_recovery_state_invalid")
        status_file = Path(root) / "var/leads-run.json"
        if status_file.exists() and status_file.stat().st_mtime >= current["started_at"]:
            raise CycleError("workflow_recovery_external_read_possible")
        if store.db.execute("SELECT 1 FROM workflow_stage_claim WHERE stage_run_id=?",
                            (current["stage_run_id"],)).fetchone():
            raise CycleError("workflow_recovery_claim_active")
        if store.db.execute(
            "SELECT 1 FROM workflow_run WHERE market=? AND run_id!=? AND state IN ('queued','running','stop_requested')",
            (market, run_id),
        ).fetchone():
            raise CycleError("workflow_run_active")
        now = store.clock()
        evidence = {"requestId": request_id, "previousErrorCode": current["error_code"],
                    "previousStartedAt": current["started_at"], "previousFinishedAt": current["finished_at"],
                    "previousPlatformWrites": 0, "leadsRunStatusMtime": status_file.stat().st_mtime if status_file.exists() else None}
        store.db.execute("INSERT INTO workflow_checkpoint VALUES(?,?,?,?,?)",
                         (run_id, "kalodata", checkpoint_key, encoded(evidence), now))
        store.db.execute(
            "UPDATE workflow_stage_run SET state='queued',started_at=NULL,finished_at=NULL,error_code=NULL "
            "WHERE stage_run_id=?", (current["stage_run_id"],),
        )
        store.db.execute(
            "UPDATE workflow_run SET state='running',finished_at=NULL,error_code=NULL WHERE run_id=?",
            (run_id,),
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
                 complete=True, platform_writes=0, error_code=None, claim_ticket=None, write_evidence=None,
                 identity_account=None):
    """Finish one stage and atomically publish its generation when its barrier is satisfied."""
    _required(store)
    if write_evidence not in (None, "zero", "known", "uncertain"):
        raise CycleError("workflow_stage_result_invalid")
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
        if claim_ticket is not None:
            from lib.workflow_resources import assert_current
            if row['stage_run_id']!=claim_ticket['stageRunId']:
                raise CycleError('workflow_stage_fence_stale')
            assert_current(store,row['stage_run_id'],claim_ticket['ownerId'],claim_ticket['fence'])
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
        total_writes = row["platform_writes"] + platform_writes
        store.db.execute(
            "UPDATE workflow_stage_run SET state=?,output_generation_id=?,platform_writes=?,counts_json=?,finished_at=?,"
            "error_code=? WHERE run_id=? AND stage=?",
            (state, generation_id, total_writes,
             encoded({**json.loads(row['counts_json'] or '{}'),'items':item_count,
                      **({'writeEvidence':write_evidence} if write_evidence else {}),
                      **({'identityAccount':identity_account} if identity_account else {})}),
             now,error_code,run_id,stage),
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
    active=store.db.execute("SELECT run_id FROM workflow_run WHERE market=? AND state IN ('queued','running','stop_requested') "
                            "ORDER BY started_at DESC LIMIT 1",(market,)).fetchone()
    current = run_payload(store, active['run_id'] if active else rows[0]["run_id"]) if active or rows else None
    history = [run_payload(store, row["run_id"]) for row in rows]
    return {
        "schemaVersion": "bdhub.operations-workflow.v1", "market": market,
        "setting": setting(store, market), "current": current, "history": history,
        "platformWrites": sum(
            stage["platformWrites"] for run in history for stage in run["stages"]
        ),
    }
