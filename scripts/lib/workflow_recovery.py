"""Evidence-bound recovery of a published selected catalog and its stopped duplicate."""
import json
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from lib.global_screen import fingerprint,load as screen_rules
from lib.second_cycle import CycleError,digest,encoded
from lib.operations_workflow import run_payload

KEY='recovery:published_selected_catalog'
TZ=ZoneInfo('Asia/Shanghai')
ISOLATED='isolated_unverified'


def _isolation(isolate_pids):
    """PIDs the operator chose to keep out of this round: their evidence stays, the round just does not use them."""
    if not isolate_pids:return frozenset()
    if not isinstance(isolate_pids,(list,tuple,set,frozenset)) or len(isolate_pids)>20 or len(set(isolate_pids))!=len(isolate_pids) or \
       any(not isinstance(pid,str) or not pid.isdigit() or len(pid)>32 for pid in isolate_pids):
        raise CycleError('workflow_isolation_scope_invalid')
    return frozenset(isolate_pids)


def source_id(run):
    day=datetime.fromtimestamp(run['started_at'],TZ).strftime('%Y%m%d')
    return f"{run['market']}-global-{day}-"+digest([run['run_id'],'selected'])[:12]


def _run(store,run_id):
    row=store.db.execute('SELECT * FROM workflow_run WHERE run_id=?',(run_id,)).fetchone()
    if not row:raise CycleError('workflow_run_missing')
    return row


def _catalog(store,run):
    stages=list(store.db.execute('SELECT * FROM workflow_stage_run WHERE run_id=? ORDER BY position',(run['run_id'],)))
    stage=next((row for row in stages if row['stage']=='catalog'),None)
    if not stage or stage['output_generation_id'] is not None or \
            any(row['state'] not in ('completed','skipped') for row in stages[:stage['position']]) or \
            any(row['state']!='waiting_upstream' for row in stages[stage['position']+1:]):
        raise CycleError('workflow_recovery_barrier_changed')
    upstream=next((row['output_generation_id'] for row in reversed(stages[:stage['position']]) if row['output_generation_id']),None)
    if stage['input_generation_id']!=upstream:raise CycleError('workflow_recovery_barrier_changed')
    return stage


def selected_catalog_evidence(store,root,market,run_id,duplicate_run_id,isolate_pids=()):
    root=Path(root);isolate=_isolation(isolate_pids)
    if market!='it' or run_id==duplicate_run_id:raise CycleError('workflow_recovery_scope_invalid')
    run=_run(store,run_id);duplicate=_run(store,duplicate_run_id)
    if any(row['market']!=market or row['applicable_sources_json']!='["selected"]' or
           row['trigger_source']!='schedule' or row['stop_requested_at'] is not None
           for row in (run,duplicate)):
        raise CycleError('workflow_recovery_scope_invalid')
    if run['state']!='needs_human' or run['error_code'] not in ('parallel_selection_requires_review','global_selection_unresolved'):
        raise CycleError('workflow_recovery_state_invalid')
    if duplicate['state']!='needs_human' or duplicate['error_code']!='workflow_retry_requires_review' or \
            duplicate['started_at']<=run['started_at'] or \
            (duplicate['scheduled_at'],duplicate['config_revision'])!=(run['scheduled_at'],run['config_revision']):
        raise CycleError('workflow_duplicate_scope_changed')
    stage=_catalog(store,run);other=_catalog(store,duplicate)
    if stage['state']!='needs_human' or stage['error_code']!=run['error_code'] or \
            other['state']!='failed' or other['error_code']!='global_catalog_not_published' or other['platform_writes']!=0:
        raise CycleError('workflow_recovery_state_invalid')
    active=store.db.execute("SELECT 1 FROM workflow_run WHERE market=? AND state IN ('queued','running','stop_requested')",(market,)).fetchone()
    claimed=store.db.execute('SELECT 1 FROM workflow_stage_claim c JOIN workflow_stage_run s USING(stage_run_id) '
                             'JOIN workflow_run r USING(run_id) WHERE r.market=?',(market,)).fetchone()
    if active or claimed:raise CycleError('workflow_recovery_claim_active')
    # The run's switches must be on again.  The revision may have moved on: pausing production on 2026-09-24 turned
    # automatic operations off and on, which changes only the revision, not what this run depends on.
    setting=store.db.execute('SELECT * FROM market_automation_setting WHERE market=?',(market,)).fetchone()
    if not setting or not setting['automatic_operations_enabled'] or not setting['full_catalog_weekly_enabled'] or \
            setting['revision']<run['config_revision']:
        raise CycleError('workflow_recovery_setting_changed')
    source_path=root/'var/global-source.sqlite';selection_path=root/'var/global-selection.sqlite'
    with closing(sqlite3.connect(source_path.resolve().as_uri()+'?mode=ro',uri=True)) as db:
        db.row_factory=sqlite3.Row
        source=db.execute('SELECT * FROM global_source_run WHERE id=?',(source_id(run),)).fetchone()
        stopped=db.execute('SELECT * FROM global_source_run WHERE id=?',(source_id(duplicate),)).fetchone()
        heads=list(db.execute("SELECT r.* FROM global_source_head h JOIN global_source_run r ON r.id=h.run_id WHERE json_extract(r.scope,'$.market')=?",(market,)))
        if not source or source['state']!='completed' or not source['identity_unchanged'] or len(heads)!=1:
            raise CycleError('workflow_published_source_unverified')
        head=heads[0];scope=json.loads(source['scope']);coverage=json.loads(head['scope'])
        if scope.get('partitionMode')=='category_l1_v1' or scope.get('market')!=market or scope.get('account')!='acc9' or \
                head['state']!='completed' or not head['identity_unchanged'] or \
                (coverage.get('coverageOverlay') or {}).get('refreshRunId')!=source['id'] or \
                coverage.get('institutionFingerprint')!=scope.get('institutionFingerprint') or \
                coverage.get('account')!=scope.get('account'):
            raise CycleError('workflow_published_source_unverified')
        if not stopped or stopped['state']!='stopped' or stopped['terminal_reason']!='operator_stopped_duplicate_plain_collection' or \
                json.loads(stopped['scope'])!=scope or db.execute('SELECT 1 FROM global_source_head WHERE run_id=?',(stopped['id'],)).fetchone():
            raise CycleError('workflow_duplicate_source_unverified')
        products=db.execute('SELECT count(*) FROM global_source_product WHERE run_id=?',(head['id'],)).fetchone()[0]
        if not products:raise CycleError('workflow_published_source_empty')
    config=screen_rules(root)
    intake_id='select-'+digest([market,head['id'],config,fingerprint(config),'user-300-inclusive'])[:24]
    counts={};items=[];isolated=[]
    with closing(sqlite3.connect(selection_path.resolve().as_uri()+'?mode=ro',uri=True)) as db:
        db.row_factory=sqlite3.Row
        intake=db.execute('SELECT * FROM intake_run WHERE id=?',(intake_id,)).fetchone()
        if not intake or intake['source_run']!=head['id'] or json.loads(intake['rules'])!=config:
            raise CycleError('workflow_selection_scope_changed')
        for item in db.execute('SELECT pid,state,payload FROM intake_item WHERE run_id=? ORDER BY pid',(intake_id,)):
            state=item['state'];payload=json.loads(item['payload'])
            if state not in ('confirmed','already_selected','filtered','skipped_unknown',ISOLATED):
                raise CycleError('workflow_selection_unresolved')
            cid=str(((payload.get('campaign') or {}).get('campaign') or {}).get('campaign_id') or '')
            observed=sorted({str(e.get('campaignId')) for e in payload.get('selectionEvidence') or []})
            if state=='confirmed' and (not cid or cid not in observed):
                if item['pid'] not in isolate:raise CycleError('workflow_selection_receipt_unverified')
                state=ISOLATED
            if state==ISOLATED:
                if item['pid'] not in isolate:raise CycleError('workflow_isolation_scope_invalid')
                isolated.append({'pid':item['pid'],'frozenCampaignId':cid,'evidenceCampaignIds':observed})
            counts[state]=counts.get(state,0)+1
            payload={key:value for key,value in payload.items() if key!='isolation'}
            if state=='skipped_unknown':
                reads=[r for r in payload.get('readbackAbsences') or [] if r.get('selectedPoolAbsent') is True]
                if payload.get('reason')!='unresolved_after_two_delayed_readbacks' or len(reads)<2 or \
                        reads[-1]['at']-reads[0]['at']<30 or not payload.get('receipt'):
                    raise CycleError('workflow_unknown_skip_unverified')
            items.append([item['pid'],state,digest(payload)])
    if isolate-{row['pid'] for row in isolated}:raise CycleError('workflow_isolation_scope_invalid')
    evidence={'market':market,'runId':run_id,'duplicateRunId':duplicate_run_id,'isolated':isolated,
              'sourceRunId':source['id'],'headRunId':head['id'],'sourceScopeHash':digest(scope),
              'intakeRunId':intake_id,'selectionStates':counts,'selectionFingerprint':digest(items),
              'products':products,'previousPlatformWrites':stage['platform_writes'],
              'previousError':stage['error_code'],'duplicateError':other['error_code']}
    return evidence|{'evidenceHash':digest(evidence),'platformWrites':0}


def _isolate_items(root,intake_id,isolated,request_id,now):
    """Mark the chosen confirmed items isolated_unverified, keeping their payload, so link preparation (which only
    takes confirmed items) does not build links on an unverified campaign mapping this round."""
    pending=[row['pid'] for row in isolated]
    if not pending:return
    with closing(sqlite3.connect(Path(root)/'var/global-selection.sqlite')) as db,db:
        for pid in pending:
            row=db.execute('SELECT state,payload FROM intake_item WHERE run_id=? AND pid=?',(intake_id,pid)).fetchone()
            if not row or row[0] not in ('confirmed',ISOLATED):raise CycleError('workflow_isolation_scope_changed')
            if row[0]==ISOLATED:continue
            payload=json.loads(row[1])|{'isolation':{'requestId':request_id,'at':now,
                                                     'reason':'selection_receipt_campaign_unverified'}}
            db.execute('UPDATE intake_item SET state=?,payload=?,updated=? WHERE run_id=? AND pid=? AND state=?',
                       (ISOLATED,encoded(payload),now,intake_id,pid,'confirmed'))


def resume_selected_catalog(store,root,market,run_id,duplicate_run_id,request_id,isolate_pids=()):
    if not isinstance(request_id,str) or not 8<=len(request_id)<=120 or not all(c.isalnum() or c in '._:-' for c in request_id):
        raise CycleError('workflow_recovery_request_invalid')
    isolate=_isolation(isolate_pids)
    with store.tx():
        prior=store.db.execute('SELECT value_json FROM workflow_checkpoint WHERE run_id=? AND stage=\'catalog\' AND checkpoint_key=?',(run_id,KEY)).fetchone()
        if prior:
            evidence=json.loads(prior[0])
            if (evidence['requestId'],evidence['market'],evidence['duplicateRunId'])!=(request_id,market,duplicate_run_id) or \
                    {row['pid'] for row in evidence.get('isolated') or []}!=isolate:
                raise CycleError('workflow_recovery_request_conflict')
            return {'duplicate':True,'run':run_payload(store,run_id),'evidence':evidence,'platformWrites':0}
        evidence=selected_catalog_evidence(store,root,market,run_id,duplicate_run_id,isolate)|{'requestId':request_id,'recoveredAt':store.clock()}
        now=store.clock()
        # Isolate first: if the workflow writes below fail and roll back, a retry accepts the isolated items as they are.
        _isolate_items(root,evidence['intakeRunId'],evidence['isolated'],request_id,now)
        for target in (run_id,duplicate_run_id):
            store.db.execute('INSERT INTO workflow_checkpoint VALUES(?,?,?,?,?)',(target,'catalog',KEY,encoded(evidence),now))
        store.db.execute("UPDATE workflow_stage_run SET state='stopped',finished_at=?,checkpoint_json=? "
                         "WHERE run_id=? AND state IN ('failed','waiting_upstream')",
                         (now,encoded({'key':KEY,'value':evidence}),duplicate_run_id))
        store.db.execute("UPDATE workflow_run SET state='stopped',finished_at=?,stop_requested_at=?,error_code='superseded_duplicate_run' WHERE run_id=?",
                         (now,now,duplicate_run_id))
        store.db.execute("UPDATE workflow_stage_run SET state='queued',started_at=NULL,finished_at=NULL,error_code=NULL,checkpoint_json=? "
                         "WHERE run_id=? AND stage='catalog'",(encoded({'key':KEY,'value':evidence}),run_id))
        store.db.execute("UPDATE workflow_run SET state='running',finished_at=NULL,error_code=NULL WHERE run_id=?",(run_id,))
    return {'duplicate':False,'run':run_payload(store,run_id),'evidence':evidence,'platformWrites':0}
