"""Scheduler and stage adapter for the durable automated-operations workflow."""
from __future__ import annotations

import json
import os
import re
import signal
import sqlite3
import subprocess
import threading
import time
from contextlib import closing
from concurrent.futures import FIRST_COMPLETED,ThreadPoolExecutor,wait
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from lib.operations_workflow import create_run,finish_stage,retry_failed_stage,status as workflow_status
from lib.process_liveness import pid_alive
from lib.second_cycle import CycleError, CycleStore, digest, encoded
from lib.workflow_dispatch import claim_ready
from lib.workflow_resources import heartbeat,record_late_result,release


BEIJING = ZoneInfo('Asia/Shanghai')


def run_path(root):return Path(root)/'var/operations-scheduler.json'
def status_path(root):return Path(root)/'var/operations-scheduler-status.json'
def stop_path(root):return Path(root)/'var/operations-scheduler.stop'


def _read(path,default):
    try:
        value=json.loads(Path(path).read_text(encoding='utf-8'))
        return value if isinstance(value,dict) else default
    except (OSError,ValueError):return default


def _write(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True);temporary=path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');temporary.replace(path)


def _project_read_models(root,targets,progress):
    projector=root/'.venv/bin/python'
    if not projector.exists():return
    for market,view in sorted(set(targets)):
        try:
            result=subprocess.run([str(projector),str(root/'scripts/project-market-read-model.py'),'project',
                                   '--market',market,'--view',view],cwd=str(root),capture_output=True,timeout=60,
                                  env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'})
            if result.returncode:progress.setdefault('projectionErrors',[]).append(f'{market}:{view}')
        except (OSError,subprocess.TimeoutExpired):
            progress.setdefault('projectionErrors',[]).append(f'{market}:{view}')


def _report_platform_writes(payload):
    """Read direct or nested write counts from a CLI evidence report."""
    if not isinstance(payload,dict):return 0
    direct=payload.get('platformWrites')
    direct=direct if type(direct) is int and direct>=0 else 0
    nested=0;steps=payload.get('steps')
    for step in steps if isinstance(steps,list) else []:
        result=step.get('result') if isinstance(step,dict) else None
        value=result.get('platformWrites') if isinstance(result,dict) else None
        if type(value) is int and value>=0:nested+=value
    return max(direct,nested)


FAILED_REPORT_STATES=frozenset({'blocked','failed','partial','needs_human','stopped'})


def _explicit_writes(payload):
    """True only when the report states its platform writes as a valid integer (directly or for every step)."""
    if not isinstance(payload,dict):return False
    value=payload.get('platformWrites')
    if type(value) is int and value>=0:return True
    steps=payload.get('steps')
    if isinstance(steps,list) and steps:
        return all(isinstance(step,dict) and isinstance(step.get('result'),dict) and
                   type(step['result'].get('platformWrites')) is int and step['result']['platformWrites']>=0 for step in steps)
    return False


def _nested_failure(payload):
    """The first failed inner step of a batch report, even when the batch itself exited 0."""
    steps=payload.get('steps') if isinstance(payload,dict) else None
    for step in steps if isinstance(steps,list) else []:
        if not isinstance(step,dict):return 'nested_step_invalid'
        # Only batch sub-steps (label/exitCode/result) are judged here; other reports use "steps" for
        # progress, e.g. campaign-collect's per-pass {"status":"paused",...} entries, which are not failures.
        if not {'label','exitCode','result'}&step.keys():continue
        result=step.get('result')
        if step.get('exitCode') not in (0,None) or not isinstance(result,dict) or str(result.get('state') or '') in FAILED_REPORT_STATES:
            label=str(step.get('label') or 'step')
            return f"nested_{label}_{(result or {}).get('state') or 'exit_'+str(step.get('exitCode'))}"[:120]
    return None


def _zero_write_contract(args):
    """Commands whose CLI contract performs no platform write; a missing count there proves zero."""
    script=args[0];action=args[1] if len(args)>1 else None
    if script in {'scripts/collect-global-opportunity.py','scripts/sync-cycle-catalog.py','scripts/campaign-collect.py',
                  'scripts/catalog-names.py','scripts/market-identity.py','scripts/rolling-leads.py','scripts/lead-pool.py',
                  'scripts/second-cycle-identities.py','scripts/identity-batch.py','scripts/account-maintenance-worker.py'}:
        return True
    if script=='scripts/select-global-products.py':return action in {'prepare','verify','status'}
    if script=='scripts/campaign-join.py':return action=='status'
    if script=='scripts/catalog-clean.py':return action in {'refresh','classify','check-bindings','status','verify'}
    if script=='scripts/catalog-link-batch.py':return '--creates' in args and args[args.index('--creates')+1]=='0'
    return False


def _report_item_count(payload):
    """Extract the final durable queue size without summing repeated batch passes."""
    if not isinstance(payload,dict):return 0
    for key in ('finalSummary','summary'):
        summary=payload.get(key)
        value=summary.get('total') if isinstance(summary,dict) else None
        if type(value) is int and value>=0:return value
    return 0


def _child_error(stderr):
    """Keep only a stable error code; never persist subprocess stderr or credentials."""
    if not isinstance(stderr,str):return None
    for line in reversed(stderr.splitlines()):
        match=re.search(r'(?:[A-Za-z_]+Error): ([A-Za-z][A-Za-z0-9_]{2,79})\s*$',line)
        if match:return match.group(1)
    return None


def with_write_evidence(result,ledger):
    """Classify one stage result as proven zero, known or uncertain platform writes.

    Only a stage whose every released child returned a readable result can prove zero.
    Lost stdout, timeouts and exceptions after a child was released stay uncertain,
    and the largest write count seen in any report is kept.
    """
    result=dict(result)
    writes=result.get('platformWrites',0)
    writes=writes if type(writes) is int and writes>=0 else 0
    uncertain=result.get('writeEvidence')=='uncertain'
    if ledger is not None:
        writes=max(writes,ledger['writes']);uncertain=uncertain or ledger['uncertain']
    elif result.get('writeEvidence') not in ('zero','known','uncertain'):
        uncertain=True  # Executors without a ledger cannot prove what their children wrote.
    result['platformWrites']=writes
    result['writeEvidence']='uncertain' if uncertain else 'known' if writes else 'zero'
    return result


def collecting_source_run(root,market,account):
    """Resume a frozen source read before starting another run with the same scope."""
    path=Path(root)/('var/global-source.sqlite' if market=='it' else f'var/global-source-{market}.sqlite')
    if not path.exists():return None
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as db:
        rows=db.execute("SELECT id,scope FROM global_source_run WHERE state='collecting' ORDER BY created,id").fetchall()
    matches=[]
    for run_id,raw in rows:
        try:scope=json.loads(raw)
        except (TypeError,ValueError):continue
        if scope.get('market')==market and scope.get('account')==account:
            matches.append({'runId':run_id,'partitioned':scope.get('partitionMode')=='category_l1_v1'})
    if len(matches)>1:raise CycleError('multiple_collecting_source_runs')
    return matches[0] if matches else None


def accepted_partial_selection(root,market):
    """Reuse an operator-accepted snapshot until its frozen selection queue is drained."""
    root=Path(root);source=root/('var/global-source.sqlite' if market=='it' else f'var/global-source-{market}.sqlite')
    selection=root/('var/global-selection.sqlite' if market=='it' else f'var/global-selection-{market}.sqlite')
    if not source.exists():return None
    try:
        with closing(sqlite3.connect(source.resolve().as_uri()+'?mode=ro',uri=True)) as db:
            row=db.execute("""SELECT r.id,(SELECT count(*) FROM global_source_product p WHERE p.run_id=r.id)
                FROM global_source_head h JOIN global_source_run r ON r.id=h.run_id
                WHERE json_extract(r.scope,'$.market')=? AND r.state='accepted_partial'
                  AND r.terminal_reason='operator_accepted_partial' AND r.identity_unchanged=1""",(market,)).fetchone()
        if not row:return None
        states={};intake=None
        if selection.exists():
            with closing(sqlite3.connect(selection.resolve().as_uri()+'?mode=ro',uri=True)) as db:
                intake=db.execute('SELECT id FROM intake_run WHERE source_run=? ORDER BY created DESC LIMIT 1',(row[0],)).fetchone()
                if intake:states=dict(db.execute('SELECT state,count(*) FROM intake_item WHERE run_id=? GROUP BY state',(intake[0],)).fetchall())
        active=sum(int(states.get(key) or 0) for key in ('pending','submitting','awaiting_verification','result_unknown','needs_review'))
        if intake and active==0:return None
        return {'sourceRun':row[0],'products':int(row[1]),'intakeRun':intake[0] if intake else None,'states':states,
                'reason':'operator_accepted_partial_pending_selection'}
    except sqlite3.Error:return None


def selection_auth_unknown(root,market):
    path=Path(root)/('var/global-selection.sqlite' if market=='it' else f'var/global-selection-{market}.sqlite')
    if not path.exists():return None
    try:
        with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as db:
            row=db.execute("""SELECT count(*),max(json_extract(payload,'$.attemptedAt')) FROM intake_item
                WHERE state='result_unknown' AND json_extract(payload,'$.receipt.http')=200
                  AND json_extract(payload,'$.receipt.code')=16201010
                  AND coalesce(json_extract(payload,'$.receipt.ambiguous'),0)=0""").fetchone()
        return {'count':int(row[0]),'latestAttempt':row[1]} if row and row[0] else None
    except sqlite3.Error:return None


SELECTION_UNRESOLVED_STATES=('submitting','awaiting_verification','result_unknown','needs_review')
# Errors the fast selector raises for its own unresolved PIDs after readback; any other error,
# or a login failure, still stops every new write that depends on the supply account.
SELECTION_PID_ERRORS=frozenset({'parallel_selection_requires_review','parallel_readback_unconfirmed',
                                'selection_receipt_requires_review','global_selection_unresolved'})
# More unresolved PIDs than this is treated as a systemic fault, not a single-PID unknown.
SELECTION_UNRESOLVED_TOLERANCE=3


def selection_unresolved(root,market):
    """PIDs whose original selection intent is still unresolved; they are never re-submitted."""
    path=Path(root)/('var/global-selection.sqlite' if market=='it' else f'var/global-selection-{market}.sqlite')
    if not path.exists():return None
    try:
        with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as db:
            marks=','.join('?'*len(SELECTION_UNRESOLVED_STATES))
            return sorted({r[0] for r in db.execute(f'SELECT pid FROM intake_item WHERE state IN ({marks})',SELECTION_UNRESOLVED_STATES)})
    except sqlite3.Error:return None


def selection_gap(root,market,result):
    """The frozen PID-level gap when this outcome may continue, else None (account/systemic stop)."""
    payload=result.get('payload') or {}
    code=payload.get('error') or (result.get('errorCode') if result['state']!='completed' else None)
    if result['state'] not in ('completed','failed') or code not in (None,*SELECTION_PID_ERRORS):return None
    if selection_auth_unknown(root,market):return None
    pids=selection_unresolved(root,market)
    reported=sum(int((payload.get('states') or {}).get(key) or 0) for key in SELECTION_UNRESOLVED_STATES)
    # The ledger must name every unresolved PID the child reported; otherwise the scope is unknown.
    if not pids or len(pids)<reported or len(pids)>SELECTION_UNRESOLVED_TOLERANCE:return None
    return {'unresolvedPids':pids,'reason':code or 'global_selection_unresolved'}


def scheduler_state(root):
    run=_read(run_path(root),{});pid=run.get('pid');alive=pid_alive(pid)
    progress=_read(status_path(root),{})
    return {'running':alive,'stopping':alive and stop_path(root).exists(),'pid':pid if type(pid) is int else None,
      'startedAt':run.get('startedAt'),'phase':progress.get('stage'),'cycle':progress.get('runId'),
      'checkedAt':progress.get('checkedAt'),'lastSuccess':progress.get('lastSuccess') or {},
      'lastAttempt':progress.get('lastAttempt') or {},
      'nextDue':{key:value for key,value in (progress.get('nextDue') or {}).items()
                 if type(value) in (int,float) and value>=0},
      'error':progress.get('error')}


def start_scheduler(root,*,clock=time.time,spawn=subprocess.Popen):
    root=Path(root);state=scheduler_state(root)
    if state['running']:raise ValueError('scheduler_already_running')
    try:stop_path(root).unlink()
    except OSError:pass
    log=root/'var/operations-scheduler.log';log.parent.mkdir(parents=True,exist_ok=True)
    with log.open('a',encoding='utf-8') as handle:
        child=spawn([str(root/'.venv/bin/python'),str(root/'scripts/operations-scheduler.py'),'--worker'],
          cwd=str(root),stdin=subprocess.DEVNULL,stdout=handle,stderr=subprocess.STDOUT,
          start_new_session=True,env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'})
    _write(run_path(root),{'pid':child.pid,'startedAt':clock(),'log':str(log)})
    return scheduler_state(root)


def stop_scheduler(root):
    state=scheduler_state(root)
    if not state['running']:raise ValueError('scheduler_not_running')
    _write(stop_path(root),{'requestedAt':time.time()});return scheduler_state(root)


def due_slot(root,now,automation):
    from lib.jobs import load
    jobs=load(root)['jobs'];local=datetime.fromtimestamp(now,BEIJING)
    weekly_day=jobs['taplink_clean'].get('weekday',0)
    selected=jobs['taplink_clean'] if local.weekday()==weekly_day and automation.get('fullCatalogWeeklyEnabled') else jobs['campaign_catalog_update']
    hour,minute=map(int,selected['at'].split(':'))
    return local.replace(hour=hour,minute=minute,second=0,microsecond=0).timestamp()


def _day_start(stamp):
    local=datetime.fromtimestamp(stamp,BEIJING)
    return local.replace(hour=0,minute=0,second=0,microsecond=0).timestamp()


def _source_success(store,market,source):
    row=store.db.execute("""SELECT max(s.finished_at) FROM workflow_stage_run s
      JOIN workflow_run r ON r.run_id=s.run_id
      WHERE r.market=? AND s.stage='catalog' AND s.state='completed'
        AND EXISTS(SELECT 1 FROM json_each(r.applicable_sources_json) WHERE value=?)""",(market,source)).fetchone()
    return float(row[0]) if row and row[0] is not None else None


def _scheduled_sources(root,store,market,stamp,automation,policy):
    """Return only sources whose calendar cadence is due; no daily duplicate workflow."""
    from lib.jobs import load
    from lib.market_registry import supports
    jobs=load(root)['jobs'];local=datetime.fromtimestamp(stamp,BEIJING);sources=[];due_times={}
    campaign_hour,campaign_minute=map(int,jobs['campaign_catalog_update']['at'].split(':'))
    last_campaign=_source_success(store,market,'campaign')
    if last_campaign is None:
        campaign_due=local.replace(hour=campaign_hour,minute=campaign_minute,second=0,microsecond=0).timestamp()
    else:
        prior=datetime.fromtimestamp(last_campaign,BEIJING)
        campaign_due=(prior+timedelta(days=policy['campaignRefreshDays'])).replace(
            hour=campaign_hour,minute=campaign_minute,second=0,microsecond=0).timestamp()
    due_times['campaign']=campaign_due
    if stamp>=campaign_due:sources.append('campaign')
    if automation.get('fullCatalogWeeklyEnabled') and supports(root,market,'fullManagedCatalog'):
        clean_hour,clean_minute=map(int,jobs['taplink_clean']['at'].split(':'))
        weekly_day=jobs['taplink_clean'].get('weekday',0)
        days_back=(local.weekday()-weekly_day)%7
        selected_due=(local-timedelta(days=days_back)).replace(hour=clean_hour,minute=clean_minute,second=0,microsecond=0)
        if selected_due.timestamp()>stamp:selected_due-=timedelta(days=7)
        selected_due=selected_due.timestamp();last_selected=_source_success(store,market,'selected')
        if last_selected is not None and last_selected>=selected_due:
            selected_due+=7*86400
        from lib.operations_policy import full_catalog_collection_mode
        mode=full_catalog_collection_mode(root,market,stamp,policy=policy)
        discovery_due=mode['nextDiscoveryAt']
        selected_due=min(selected_due,discovery_due)
        due_times['selectedDiscovery']=discovery_due
        due_times['selected']=selected_due
        if stamp>=selected_due:sources.insert(0,'selected')
    return sources,due_times


class SubprocessStageExecutor:
    """Production adapter. Every command is fixed argv; no shell or user text is interpolated."""
    def __init__(self,root,runner=subprocess.run,clock=time.time):
        self.root=Path(root);self.runner=runner;self.clock=clock;self._claims=threading.local()

    def _ledger(self):
        """Per-stage write evidence across every child call; None outside a scheduled stage."""
        return getattr(self._claims,'ledger',None)

    def _call(self,args,label,timeout=14400):
        ledger=self._ledger();launched=ledger['launched'] if ledger is not None else 0
        try:result=self._call_child(args,label,timeout)
        except BaseException:
            # A child released by this call may have written before the timeout/error: never zero.
            if ledger is not None and ledger['launched']>launched:ledger['uncertain']=True
            raise
        if ledger is not None:
            ledger['writes']+=result.get('platformWrites',0)
            if result.get('writeEvidence')=='uncertain':ledger['uncertain']=True
        return result

    def _call_child(self,args,label,timeout):
        ledger=self._ledger()
        stamp=time.strftime('%Y%m%d-%H%M%S')
        report=self.root/f'var/workflow-{label}-{stamp}-{digest(args)[:8]}.json'
        command=[str(self.root/'.venv/bin/python'),str(self.root/args[0]),*args[1:]]
        if args[0] in {'scripts/catalog-clean.py','scripts/campaign-collect.py',
                       'scripts/catalog-link-batch.py','scripts/identity-batch.py'} and '--report' not in command:
            command+=['--report',str(report)]
        if self.runner is subprocess.run and getattr(self._claims,'ticket',None):
            ticket=self._claims.ticket
            read_fd,write_fd=os.pipe()
            wrapper=('import os,sys; fd=int(sys.argv[1]); ready=os.read(fd,1); os.close(fd); '
                     'os.execv(sys.argv[2],sys.argv[2:]) if ready==b"1" else os._exit(125)')
            try:
                process=subprocess.Popen([command[0],'-c',wrapper,str(read_fd),*command],
                    cwd=str(self.root),stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                    text=True,start_new_session=True,pass_fds=(read_fd,),
                    env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'})
            finally:os.close(read_fd)
            try:
                with CycleStore(self.root/'var/second-cycle.sqlite') as claim_store:
                    with claim_store.tx():
                        claim_store.db.execute("UPDATE workflow_stage_claim SET worker_pid=? WHERE stage_run_id=? AND owner_id=? AND fence=?",
                            (process.pid,ticket['stageRunId'],ticket['ownerId'],ticket['fence']))
                        if claim_store.db.execute('SELECT changes()').fetchone()[0]!=1:
                            raise CycleError('workflow_stage_fence_stale')
                os.write(write_fd,b'1')
                if ledger is not None:ledger['launched']+=1
                try:stdout,stderr=process.communicate(timeout=timeout)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid,signal.SIGTERM)
                    try:process.communicate(timeout=10)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid,signal.SIGKILL);process.communicate()
                    raise
                child=subprocess.CompletedProcess(command,process.returncode,stdout,stderr)
            except BaseException:
                if process.poll() is None:
                    try:os.killpg(process.pid,signal.SIGTERM)
                    except ProcessLookupError:pass
                    process.communicate()
                raise
            finally:
                os.close(write_fd)
                # The child is gone: the claim's live process is the supervisor again, so a lapsed
                # lease is not mistaken for a dead owner while the result is still being settled.
                if process.poll() is not None:
                    try:
                        from lib.workflow_resources import adopt_worker
                        with CycleStore(self.root/'var/second-cycle.sqlite') as claim_store:
                            adopt_worker(claim_store,ticket['stageRunId'],ticket['ownerId'],ticket['fence'],os.getpid())
                    except (CycleError,sqlite3.Error,OSError):pass
        else:
            if ledger is not None:ledger['launched']+=1
            child=self.runner(command,cwd=str(self.root),capture_output=True,text=True,timeout=timeout,
                              env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'})
        lines=[line for line in (child.stdout or '').splitlines() if line.strip()]
        try:payload=json.loads(lines[-1]) if lines else None
        except ValueError:payload=None
        evidence={}
        if report.exists():
            try:evidence=json.loads(report.read_text(encoding='utf-8'))
            except (OSError,ValueError):evidence={}
        if not isinstance(payload,dict) or not payload:
            # The child ran but its result is unreadable: keep report writes, never prove zero.
            return {'state':'failed','itemCount':0,'complete':False,'platformWrites':_report_platform_writes(evidence),
                    'errorCode':f'{label}_report_invalid','writeEvidence':'uncertain',
                    'payload':{'report':str(report.relative_to(self.root)) if report.exists() else None}}
        if args[0]=='scripts/rolling-leads.py' and not child.returncode and payload.get('sliceComplete') is True:
            valid=payload.get('market') in ('it','br','my','uk') and payload.get('platformWrites')==0 and payload.get('realSends')==0
            if valid:
                paused=payload.get('stopped')=='automation_paused'
                return {'state':'stopped' if paused else 'completed','itemCount':payload.get('completed',0),
                        'complete':not paused,'platformWrites':0,'payload':payload,'scope':{}}
        if args[0]=='scripts/market-identity.py' and not child.returncode and payload.get('sliceComplete') is True:
            if '--market' in args and payload.get('market')==args[args.index('--market')+1] and payload.get('market') in ('it','br','my','uk') and payload.get('platformWrites')==0 and payload.get('realSends')==0:
                return {'state':'completed','complete':True,'itemCount':payload.get('newBindings',0),
                        'platformWrites':0,'payload':payload,'scope':{'coverage':'bounded_identity_slice'}}
        reported_state=str(evidence.get('state') or evidence.get('status') or payload.get('state') or payload.get('status') or '')
        writes=max(_report_platform_writes(evidence),_report_platform_writes(payload))
        # A write-capable child that does not state its writes cannot prove zero.
        uncertain='uncertain' in (payload.get('writeEvidence'),evidence.get('writeEvidence')) or not (
            _explicit_writes(evidence) or _explicit_writes(payload) or _zero_write_contract(args))
        evidence_flag={'writeEvidence':'uncertain'} if uncertain else {}
        nested=_nested_failure(evidence) or _nested_failure(payload)
        items=max(_report_item_count(evidence),_report_item_count(payload))
        stopped=payload.get('stopped') or evidence.get('stopped')
        errors=payload.get('errors') or evidence.get('errors')
        invalid_stop=stopped not in (None,'queue_empty','nothing_missing','kalodata_daily_quota_exhausted') and not (args[0]=='scripts/market-identity.py' and stopped=='nothing_pending')
        if child.returncode or reported_state in FAILED_REPORT_STATES or nested or \
                payload.get('error') or evidence.get('error') or invalid_stop or errors:
            code=str(payload.get('error') or evidence.get('error') or nested or
                     (stopped if invalid_stop else None) or _child_error(child.stderr) or f'{label}_failed')
            # Keep the child's progress (states/pending) so callers can decide termination.
            progress={key:value for key,value in (payload|evidence).items() if key in ('states','summary','finalSummary')}
            return {'state':'needs_human' if reported_state=='needs_human' or 'maintenance' in code or 'auth' in code else 'failed',
                    'itemCount':items,'complete':False,'platformWrites':writes,**evidence_flag,
                    'errorCode':code[:120],'payload':{'report':str(report.relative_to(self.root)) if report.exists() else None,**progress}}
        return {'state':'completed','itemCount':items,'complete':True,'platformWrites':writes,**evidence_flag,
                'payload':evidence or payload,'scope':{}}

    def _relogin_market_account(self,store,market,role,run_id,reason):
        """Recover the failed account through the single auth entry and wait for that one chain to finish.

        Concurrent reporters of the same lapse (inbox, SDK, another stage) join the same intent; this stage
        succeeds only when a generation newer than the one that failed has been published."""
        if store is None:return {'state':'failed','errorCode':'selection_relogin_store_missing'}
        from lib.account_identity import current_generation
        from lib.login_recovery import request_recovery
        from lib.market_accounts import load_config
        account=load_config(self.root)['markets'][market]['roles'][role];prior=current_generation(store,market,account)
        requested=request_recovery(store,self.root,market,role,source=f'scheduler:{reason}',spawn=lambda _root:None)
        if requested['state']=='not_requested':return {'state':'failed','errorCode':requested.get('reason') or 'auth_recovery_not_requested'}
        intent_id=requested['intentId'];sleep=getattr(self,'sleep',time.sleep);deadline=time.monotonic()+660;row=None
        while intent_id:
            row=store.db.execute('SELECT state,error_code FROM account_maintenance_intent WHERE intent_id=?',(intent_id,)).fetchone()
            if row is None or row['state'] not in ('queued','draining','running'):break
            if time.monotonic()>=deadline:return {'state':'failed','errorCode':'auth_recovery_wait_timeout'}
            # Another account's maintenance may hold the global queue; the worker then claims nothing yet.
            if row['state']=='queued':self._call(['scripts/account-maintenance-worker.py'],'global-selection-relogin',timeout=660)
            sleep(5)
        current=current_generation(store,market,account)
        if not current or not prior or current['publishedAt']<=prior['publishedAt']:
            return {'state':'failed','errorCode':(row['error_code'] if row else None) or 'selection_relogin_not_published'}
        return {'state':'completed','generationId':current['generationId']}

    def _relogin_selection_account(self,store,market,run_id,auth):
        return self._relogin_market_account(store,market,'supply',run_id,auth.get('latestAttempt'))

    def execute(self,store,run,stage,jobs):
        sources=run['applicableSources'];enabled=jobs['jobs'];market=run.get('market','it')
        market_flag=[] if market=='it' else ['--market',market]
        if stage=='taplink_clean':
            if market!='it':
                # Inventory reconciliation and remote deletes stay IT-only; other markets get the
                # read-only check of their own current bindings and local withdrawal of explicit invalids.
                checked=self._call(['scripts/catalog-clean.py','check-bindings','--market',market],'taplink-binding-check')
                if checked['state']!='completed':return checked
                summary=(checked.get('payload') or {}).get('summary') or {}
                return {'state':'completed','itemCount':int(summary.get('checked') or 0),'complete':True,'platformWrites':0,
                        'scope':{'market':market,'mode':'binding_check_read_only','remoteDelete':'not_enabled'},
                        'payload':checked.get('payload') or {}}
            total=0;writes=0;last={}
            for action in ('refresh','classify','delete'):
                args=['scripts/catalog-clean.py',action,'--lanes','9','--qps','12'] if action=='refresh' else ['scripts/catalog-clean.py',action]
                if action=='delete':args+=['--max-deletes','600']
                last=self._call(args,'taplink-clean-'+action);writes+=last.get('platformWrites',0)
                if last['state']!='completed':return last|{'platformWrites':writes}
                total=max(total,int(((last.get('payload') or {}).get('summary') or {}).get('total') or 0))
            return {'state':'completed','itemCount':total,'complete':True,'platformWrites':writes,
                    'scope':{'sources':sources},'payload':last.get('payload') or {}}
        if stage=='catalog':
            outputs=[];count=0;writes=0;selection_gaps=[]
            if 'selected' in sources:
                frozen=accepted_partial_selection(self.root,market)
                if frozen:
                    result={'state':'completed','itemCount':frozen['products'],'complete':True,'platformWrites':0,
                            'scope':{'market':market,'coverage':'operator_accepted_partial'},
                            'payload':{'state':'accepted_partial','published':True,'reusedPublishedSnapshot':frozen}}
                else:
                    started=run.get('startedAt') or self.clock()
                    from lib.operations_policy import full_catalog_collection_mode,published_plain_source,selected_source_run_id
                    rid=selected_source_run_id(market,run['runId'],started)
                    from lib.market_accounts import catalog_read_account
                    published=published_plain_source(self.root,market,rid)
                    if published:
                        result={'state':'completed','itemCount':published['products'],'complete':True,'platformWrites':0,
                                'scope':{'collectionMode':{'mode':'reuse','reason':'same_run_published'}},
                                'payload':{'state':'completed','published':True,'products':published['products'],
                                           'reusedPublishedSource':published}}
                    else:
                        collection_mode=full_catalog_collection_mode(self.root,market,self.clock())
                        source_path=self.root/('var/global-source.sqlite' if market=='it' else f'var/global-source-{market}.sqlite')
                        existing=(collecting_source_run(self.root,market,
                            catalog_read_account(self.root,market=market))
                            if source_path.exists() and (self.root/'config/market-accounts.json').exists() else None)
                        if existing:
                            rid=existing['runId']
                            collection_mode={**collection_mode,'mode':'category' if existing['partitioned'] else 'plain',
                                             'resumedExisting':rid}
                        if collection_mode['mode']=='reuse':
                            from lib.operations_policy import current_published_source
                            published=current_published_source(self.root,market)
                            result={'state':'completed','itemCount':published['products'],'complete':True,
                                    'platformWrites':0,'payload':{'state':'completed','published':True,
                                     'products':published['products'],'reusedPublishedSource':published}}
                        else:
                            collect=['scripts/collect-global-opportunity.py',*market_flag,'--run-id',rid,'--pages','40','--worker']
                            if collection_mode['mode']=='category':collect.append('--by-category')
                            result=self._call(collect,'global-catalog')
                            if result['state']!='completed':return result
                            collected=result.get('payload') or {}
                            if (collected.get('state')!='completed' and not (collected.get('state')=='accepted_partial' and collected.get('coverageOverlay'))) or collected.get('published') is not True:
                                return {**result,'state':'failed','complete':False,'errorCode':'global_catalog_not_published'}
                        result.setdefault('scope',{})['collectionMode']=collection_mode
                outputs.append(result);count+=int((result.get('payload') or {}).get('products') or 0)
                prepared=self._call(['scripts/select-global-products.py','prepare',*market_flag],'global-selection-prepare')
                if prepared is None:pass
                else:
                    outputs.append(prepared)
                    if prepared['state']!='completed':return prepared|{'platformWrites':writes}
                verified=self._call(['scripts/select-global-products.py','verify',*market_flag,'--limit','600'],'global-selection-verify')
                if verified is None:pass
                else:
                    outputs.append(verified)
                    if verified['state']!='completed':return verified|{'platformWrites':writes}
                verify_payload=(verified or {}).get('payload') or {};verify_states=verify_payload.get('states') or {}
                verify_unresolved=sum(int(verify_states.get(key) or 0) for key in
                  ('submitting','awaiting_verification','result_unknown','needs_review'))
                if verify_payload.get('error') or verify_unresolved:
                    auth=selection_auth_unknown(self.root,market)
                    if auth:
                        relogin=self._relogin_selection_account(store,market,run['runId'],auth)
                        if relogin['state']!='completed':return {'state':'needs_human','itemCount':0,'complete':False,
                          'platformWrites':writes,'errorCode':relogin['errorCode'],'scope':{'sources':sources},'payload':{}}
                    recovered=self._call(['scripts/select-global-products.py','execute-fast',*market_flag,
                      '--limit','100','--native-listing','--reconcile-rejections','--skip-after-readback',
                      '--lanes','8','--qps','8','--group-size','100'],
                      'global-selection-recover')
                    outputs.append(recovered);writes+=recovered.get('platformWrites',0)
                    recovered_payload=recovered.get('payload') or {};recovered_states=recovered_payload.get('states') or {}
                    recovered_unresolved=sum(int(recovered_states.get(key) or 0) for key in
                      ('submitting','awaiting_verification','result_unknown','needs_review'))
                    if recovered['state']!='completed' or recovered_payload.get('error') or recovered_unresolved:
                        gap=selection_gap(self.root,market,recovered)
                        if gap is None:
                            return {**recovered,'state':'needs_human','complete':False,
                                    'errorCode':str(recovered_payload.get('error') or recovered.get('errorCode') or 'global_selection_unresolved')[:120],
                                    'platformWrites':writes}
                        selection_gaps.append(gap)
                if True:
                    last_pending=None
                    for _ in range(40):
                        selected=self._call(['scripts/select-global-products.py','execute-fast',*market_flag,'--limit','300',
                          '--native-listing','--reconcile-rejections','--skip-after-readback',
                          '--lanes','8','--qps','8','--group-size','100'],
                                            'global-selection')
                        outputs.append(selected);writes+=selected.get('platformWrites',0)
                        if selected['state']!='completed':
                            gap=selection_gap(self.root,market,selected)
                            if gap is None:return selected|{'platformWrites':writes}
                            # Only these PIDs wait on their original intent; the rest keep selecting,
                            # as long as the child's own progress shows there is something left to do.
                            selection_gaps.append(gap)
                            fail_states=(selected.get('payload') or {}).get('states')
                            left=fail_states.get('pending') if isinstance(fail_states,dict) else None
                            if type(left) is not int or left<0:
                                return {**selected,'state':'needs_human','complete':False,'platformWrites':writes,
                                        'errorCode':'global_selection_progress_unknown'}
                            if left==0:break
                            if last_pending is not None and left>=last_pending:
                                return {**selected,'state':'needs_human','complete':False,'platformWrites':writes,
                                        'errorCode':'global_selection_no_progress'}
                            last_pending=left;continue
                        payload=selected.get('payload') or {};states=payload.get('states') or {};error=payload.get('error')
                        auth=selection_auth_unknown(self.root,market)
                        if auth:
                            relogin=self._relogin_selection_account(store,market,run['runId'],auth)
                            if relogin['state']!='completed':return {'state':'needs_human','itemCount':count,'complete':False,
                              'platformWrites':writes,'errorCode':relogin['errorCode'],'scope':{'sources':sources},'payload':payload}
                            selected=self._call(['scripts/select-global-products.py','execute-fast',*market_flag,
                              '--limit','100','--native-listing','--reconcile-rejections','--skip-after-readback',
                              '--lanes','8','--qps','8','--group-size','100'],'global-selection-auth-recover')
                            outputs.append(selected);writes+=selected.get('platformWrites',0)
                            payload=selected.get('payload') or {};states=payload.get('states') or {};error=payload.get('error')
                        if error:
                            return {**selected,'state':'needs_human','complete':False,
                                    'errorCode':str(error)[:120],'platformWrites':writes}
                        unresolved=sum(int(states.get(key) or 0) for key in
                          ('submitting','awaiting_verification','result_unknown','needs_review'))
                        if unresolved:
                            gap=selection_gap(self.root,market,selected)
                            if gap is None:
                                return {**selected,'state':'needs_human','complete':False,
                                        'errorCode':'global_selection_unresolved','platformWrites':writes}
                            selection_gaps.append(gap)
                        if not int(states.get('pending') or 0):break
                    else:
                        return {'state':'failed','itemCount':count,'complete':False,'platformWrites':writes,
                                'errorCode':'global_selection_iteration_limit','scope':{'sources':sources},'payload':{}}
                selected_run=self.root/f"var/cycle-catalog-{market}-selected-{time.strftime('%Y%m%d')}-{digest([run['runId'],'selected-catalog'])[:10]}.json"
                for pass_no in range(20):
                    synced=self._call(['scripts/sync-cycle-catalog.py',*market_flag,'--source','selected','--run',str(selected_run),'--max-requests','150'],f'selected-catalog-{pass_no:02d}')
                    outputs.append(synced)
                    if synced['state']!='completed':return synced|{'platformWrites':writes}
                    sync_payload=synced.get('payload') or {}
                    if sync_payload.get('status') in {'completed','already_completed'}:break
                else:return {'state':'failed','itemCount':count,'complete':False,'platformWrites':writes,
                            'errorCode':'selected_catalog_iteration_limit','scope':{'sources':sources},'payload':{}}
            if 'campaign' in sources:
                join_market_flag=['--market',market]
                # New applications and joined-goods refresh have independent outcomes.
                # In particular, unknown membership never suppresses the already joined catalog.
                joined=self._call(['scripts/campaign-join.py','join-all',*join_market_flag,'--confirm'],'campaign-join')
                writes+=joined.get('platformWrites',0)
                outputs.append(joined)
                result=self._call(['scripts/campaign-collect.py',*market_flag,'--max-requests','150','--passes','200','--screen'],
                                  'campaign-catalog')
                if result['state']!='completed':return result|{'platformWrites':writes}
                evidence=result.get('payload') or {}
                if evidence.get('status')!='completed' or not (evidence.get('screening') or {}).get('recorded'):
                    return {**result,'state':'failed','complete':False,'errorCode':'campaign_catalog_not_published',
                            'platformWrites':writes}
                outputs.append(result);campaign_count=int(evidence.get('offers') or 0);count+=campaign_count
                verification_status=self._call(['scripts/campaign-join.py','status','--market',market],
                                               'campaign-verification-status')
                outputs.append(verification_status)
                pending=(verification_status.get('payload') or {}).get('activeVerification')
                if pending:
                    # Prepare confirmed goods before spending any special verification budget.
                    # This subpass stays in the current catalog generation and resource claim.
                    # The later normal taplink stage reuses its already prepared bindings.
                    prepared=self.execute(store,run|{'applicableSources':['campaign']},'taplink_prepare',jobs)
                    writes+=prepared.get('platformWrites',0);outputs.append(prepared)
                    if prepared['state']!='completed':return prepared|{'platformWrites':writes}
                    verified=self._call(['scripts/campaign-join.py','verify','--market',market,'--bounded'],
                                        'campaign-bounded-verification')
                    outputs.append(verified)
                    if (verified.get('payload') or {}).get('joinedSettled'):
                        refreshed=self._call(['scripts/campaign-collect.py',*market_flag,
                            '--max-requests','150','--passes','200','--screen'],'campaign-confirmed-catalog')
                        outputs.append(refreshed)
                        evidence=refreshed.get('payload') or {}
                        if refreshed['state']!='completed' or evidence.get('status')!='completed' or not (
                                evidence.get('screening') or {}).get('recorded'):
                            return refreshed|{'state':'failed','complete':False,'platformWrites':writes,
                                              'errorCode':'campaign_catalog_not_published'}
                        count+=int(evidence.get('offers') or 0)-campaign_count
                    verification_status=verified
                membership=verification_status.get('payload') or {}
                campaign_scope={'state':joined['state'], 'errorCode':joined.get('errorCode'),
                    'joinedCatalogComplete':True,
                    'membershipReadable':verification_status['state']=='completed',
                    'unresolved':membership.get('unresolved',[]),
                    'stoppedUnknown':membership.get('stoppedUnknown',[]),
                    'accountBlocked':(joined.get('payload') or {}).get('accountBlocked',False)}
            scope={'sources':sources,'campaignApplications':campaign_scope if 'campaign' in sources else None}
            if selection_gaps:
                # Published with its coverage gap: unresolved PIDs are neither selected nor filtered.
                scope['selectionGap']={'unresolvedPids':selection_gaps[-1]['unresolvedPids'],
                                       'reasons':sorted({gap['reason'] for gap in selection_gaps}),'coverage':'partial_selection'}
            return {'state':'completed','itemCount':count,'complete':True,'platformWrites':writes,
                    'scope':scope,'payload':{'sources':outputs}}
        if stage=='taplink_prepare':
            writes=0;outputs=[];count=0
            for route in sources:
                if market!='it':
                    seeded=self._call(['scripts/catalog-link-batch.py','--route',route,'--limit','200','--passes','0',
                      '--creates','0','--lanes','9','--qps','12','--seed',
                      *(['--scope','pool'] if route=='selected' else []),*market_flag],'taplink-seed-'+route)
                    outputs.append(seeded)
                    if seeded['state']!='completed':return seeded|{'platformWrites':writes}
                else:
                    prepared=self._call(['scripts/catalog-link-batch.py','--route',route,'--limit','200','--passes','80',
                      '--creates','0','--lanes','9','--qps','12','--seed'],'taplink-read-'+route)
                    outputs.append(prepared)
                    if prepared['state']!='completed':return prepared|{'platformWrites':writes}
                names=self._call(['scripts/catalog-names.py','prepare','--all','--market',market],'taplink-short-names-'+route)
                outputs.append(names);name_payload=names.get('payload') or {}
                if names['state']!='completed' or int(name_payload.get('missing') or 0)>0:
                    return {**names,'state':'needs_human','complete':False,
                            'errorCode':'catalog_short_names_incomplete','platformWrites':writes}
                if market!='it':
                    prepared=self._call(['scripts/catalog-link-batch.py','--route',route,'--limit','200','--passes','80',
                      '--creates','0','--lanes','9','--qps','12',*market_flag],'taplink-read-'+route)
                    outputs.append(prepared)
                    if prepared['state']!='completed':return prepared|{'platformWrites':writes}
                created=self._call(['scripts/catalog-link-batch.py','--route',route,'--limit','200','--passes','0',
                  '--creates','200','--lanes','9','--qps','12',*market_flag],'taplink-create-'+route)
                writes+=created.get('platformWrites',0);outputs.append(created)
                if created['state']!='completed':return created|{'platformWrites':writes}
                count+=max(prepared.get('itemCount',0),created.get('itemCount',0))
            return {'state':'completed','itemCount':count,
                    'complete':True,'platformWrites':writes,'scope':{'sources':sources},'payload':{'routes':outputs}}
        if stage=='kalodata':
            args=['scripts/rolling-leads.py','run','--market',market,'--fragments','8']
            if run.get('triggerSource')=='schedule':args.append('--scheduled')
            result=self._call(args,'rolling-leads');payload=result.get('payload') or {}
            if result['state']!='completed':return result
            if payload.get('sliceComplete') is not True or payload.get('market')!=market or any(
                type(payload.get(k)) is not int or payload[k]<0 for k in ('completed','A','B','fragments','networkRequests')) or                     payload['completed']!=payload['A']+payload['B'] or payload['completed']>payload['fragments']:
                return result|{'state':'failed','complete':False,'errorCode':'rolling_leads_scope_invalid'}
            return {'state':'completed','complete':True,'platformWrites':0,'itemCount':payload['completed'],
                    'scope':{'sources':sources,'coverage':'bounded_query_slice','aCompleted':payload['A'],
                             'bCompleted':payload['B'],'remaining':payload.get('queue')},'payload':payload}
        if stage=='oecid':
            from lib.im_session_owner import identity_account
            executing=identity_account(self.root,market)
            return self._execute_oecid(store,run,market,sources,executing)|{'identityAccount':executing}
        if stage=='send_pool':
            result=self._call(['scripts/lead-pool.py','status','--limit','1','--market',market],'send-pool')
            result['itemCount']=int(((result.get('payload') or {}).get('counts') or {}).get('positions') or 0);return result
        raise CycleError('workflow_stage_invalid')

    def _execute_oecid(self,store,run,market,sources,executing):
        from lib.market_accounts import load_config as account_config
        identity_role='supply' if executing==account_config(self.root)['markets'][market]['roles']['supply'] else 'communications'
        if market=='it' and identity_role=='supply':
            reconciled=self._call(['scripts/second-cycle-identities.py','reconcile'],'oecid-legacy-reconcile')
            if reconciled['state']!='completed':return reconciled
        if market!='it' or identity_role=='supply':
            total=0;outputs=[];result=None;left=0;auth_recoveries=0
            for _ in range(500):
                result=self._call(['scripts/market-identity.py','run','--market',market,'--limit','50'],'oecid')
                outputs.append(result)
                bounded=result.get('payload') or {}
                if result['state']=='completed' and bounded.get('sliceComplete'):
                    if bounded.get('authRequired'):
                        outputs.append(self._relogin_market_account(store,market,identity_role,run['runId'],'bounded-oecid-auth'))
                    return result|{'scope':{'sources':sources,'coverage':'bounded_identity_slice',
                        'queue':bounded.get('queue'),'blockedHandles':bounded.get('blockedHandles',0)}}
                if result['state']!='completed':
                    if result.get('errorCode')=='market_identity_auth_required' and auth_recoveries<2:
                        relogin=self._relogin_market_account(store,market,identity_role,run['runId'],
                                                            f'oecid-{auth_recoveries}')
                        if relogin['state']=='completed':auth_recoveries+=1;outputs.append(relogin);continue
                    return result|{'itemCount':total,'scope':{'sources':sources,'pending':None,
                                                             'authRecoveries':auth_recoveries}}
                total+=int((result.get('payload') or {}).get('newBindings') or 0)
                pending=self._call(['scripts/market-identity.py','status','--market',market,'--limit','1'],'oecid-status')
                if pending['state']!='completed':return pending|{'itemCount':total,'scope':{'sources':sources,'pending':None}}
                left=len((pending.get('payload') or {}).get('items') or [])
                if not left:break
                if not int((result.get('payload') or {}).get('resolvedHandles') or 0) and not int((result.get('payload') or {}).get('unresolvedHandles') or 0):
                    return {**result,'state':'needs_human','complete':False,'itemCount':total,
                            'errorCode':'identity_queue_stalled','scope':{'sources':sources,'pending':left}}
            else:return {'state':'failed','itemCount':total,'complete':False,'platformWrites':0,
                        'errorCode':'identity_iteration_limit','scope':{'sources':sources,'pending':left},'payload':{}}
            return {'state':'completed','itemCount':total,'complete':True,'platformWrites':0,
                    'scope':{'sources':sources,'pending':0,'authRecoveries':auth_recoveries},
                    'payload':{'passes':outputs}}
        submitted=0
        for _ in range(500):
            handoff=self._call(['scripts/second-cycle-identities.py','submit'],'oecid-submit')
            if handoff['state']!='completed':return handoff
            batches=(handoff.get('payload') or {}).get('submittedBatches')
            if not isinstance(batches,list):
                return {**handoff,'state':'failed','complete':False,'errorCode':'identity_handoff_invalid'}
            submitted+=len(batches)
            if not batches:break
        else:
            return {'state':'failed','itemCount':0,'complete':False,'platformWrites':0,
                    'errorCode':'identity_handoff_limit','scope':{'sources':sources},'payload':{}}
        reconciled=self._call(['scripts/second-cycle-identities.py','reconcile'],'oecid-reconcile')
        if reconciled['state']!='completed':return reconciled
        result=self._call(['scripts/identity-batch.py','--limit','200','--cohort-size','50','--rounds','4'],'oecid')
        payload=result.get('payload') or {};pending=int(payload.get('pending') or 0)
        if payload.get('sliceComplete') and (payload.get('accountWait') or {}).get('reason')=='auth_required':
            self._relogin_market_account(store,market,identity_role,run['runId'],'bounded-oecid-auth')
        result['itemCount']=int(payload.get('found') or 0) if payload.get('sliceComplete') else int(payload.get('claimed') or 0)
        result['scope']={'sources':sources,'handoffBatches':submitted,'pending':pending,'coverage':payload.get('coverage'),'claimed':payload.get('claimed'),'notFound':payload.get('notFound'),'technicalIsolatedLeads':payload.get('technicalIsolatedLeads'),'stopReason':payload.get('stopReason'),'accountWait':payload.get('accountWait')}
        if result['state']=='completed' and pending and not payload.get('sliceComplete'):
            reason=str(payload.get('stopReason') or 'pending')
            result.update(state='needs_human',complete=False,errorCode=('identity_'+reason)[:120])
        return result


def _create_lead_continuation(root,store,market,stamp,blocked_run=None,from_stage='kalodata'):
    from lib.rolling_leads import identity_hold
    hold=identity_hold(root,market)
    run=create_run(store,market=market,trigger_source='schedule',scheduled_at=stamp,
                   request_id=f'{market}:rolling-leads:{int(stamp)}',sources=['campaign'],from_stage=from_stage)
    with store.tx():
        if hold:
            store.db.execute("UPDATE workflow_stage_run SET state='skipped',finished_at=?,error_code='identity_lane_waiting_recovery' WHERE run_id=? AND stage='oecid' AND state='waiting_upstream'",(stamp,run['runId']))
        if hold or blocked_run:
            store.db.execute('INSERT OR IGNORE INTO workflow_checkpoint VALUES(?,?,?,?,?)',
                             (run['runId'],'kalodata','independent_supply',encoded({'blockedRunId':blocked_run,'identityHold':hold}),stamp))
    from lib.operations_workflow import run_payload
    return run_payload(store,run['runId'])


def _collect_ready_runs(root,store,jobs,stamp,progress):
    """Re-read durable controls and due times; do not reuse a long-running batch snapshot."""
    projected=[]
    from lib.market_registry import enabled_market_keys
    markets=enabled_market_keys(root);snapshots={market:workflow_status(store,market) for market in markets}
    from lib.operations_workflow import run_payload
    for market,snapshot in snapshots.items():
        existing=store.db.execute("SELECT run_id FROM workflow_run WHERE market=? AND state IN ('queued','running','stop_requested') ORDER BY started_at,run_id LIMIT 1",(market,)).fetchone()
        if existing:snapshot['current']=run_payload(store,existing[0])
    it_automation=snapshots.get('it',{'setting':{'automaticOperationsEnabled':False}})['setting']
    any_automation=any(snapshot['setting']['automaticOperationsEnabled'] for snapshot in snapshots.values())
    _background(root,store,jobs,it_automation,stamp,maintenance_enabled=any_automation)
    from lib.operations_policy import load_policy
    policy=load_policy(root);active=[]
    for market,snapshot in snapshots.items():
        automation=snapshot['setting'];current=snapshot['current'];sources,due_times=_scheduled_sources(root,store,market,stamp,automation,policy)
        from lib.rolling_leads import due_at,automatic_enabled
        lead_due=(due_at(root,market,at=stamp,refresh_scope=not current or current['state'] not in ('queued','running','stop_requested'))
                  if automation['automaticOperationsEnabled'] and automatic_enabled(root,market) else None)
        from lib.identity_retry import due_at as identity_due_at
        identity_due=identity_due_at(root,market,now=stamp) if automation['automaticOperationsEnabled'] else None
        if identity_due is not None:due_times['identity']=identity_due
        only_identity=identity_due is not None and identity_due<=stamp and (lead_due is None or lead_due>stamp)
        if only_identity:lead_due=identity_due
        continuation_stage='oecid' if only_identity else 'kalodata'
        if lead_due is not None:due_times['leads']=lead_due
        key='workflow' if market=='it' else f'workflow:{market}'
        future=[value for value in due_times.values() if value>stamp]
        progress['nextDue'][key]=min(future) if future else min(due_times.values())
        if lead_due is not None and lead_due<=stamp and (not current or current['state'] in ('completed','stopped')) and not sources:
            current=_create_lead_continuation(root,store,market,stamp,from_stage=continuation_stage)
        if current and current['state']=='failed' and lead_due is not None and lead_due<=stamp and any(
                stage['stage']=='oecid' and stage['state']=='failed' for stage in current['stages']):
            current=_create_lead_continuation(root,store,market,stamp,current['runId'],from_stage=continuation_stage)
        if current and current['state']=='failed' and current['triggerSource']=='schedule':
            if not automation['automaticOperationsEnabled']:continue
            retry=retry_failed_stage(store,current['runId'],now=stamp)
            if retry['state']=='resumed':current=workflow_status(store,market)['current']
            elif retry['state']=='waiting':
                progress['nextDue'][key]=retry['nextAt'];continue
            else:
                progress['error']=progress['error'] or retry.get('reason') or 'workflow_retry_exhausted'
                continue
        if current and current['state']=='needs_human':
            progress['error']=progress['error'] or current.get('errorCode') or 'workflow_needs_human'
            # A failed query implementation itself remains blocked; a failed supply/identity
            # stage cannot prevent independent reads of already valid material.
            failed_stage=next((s for s in current['stages'] if s['state'] in ('failed','needs_human')),None)
            if lead_due is not None and lead_due<=stamp and automation['automaticOperationsEnabled'] and (
                    not failed_stage or failed_stage['stage']!='kalodata' or any(s['stage']=='catalog' and s['state']!='skipped' for s in current['stages'])):
                current=_create_lead_continuation(root,store,market,stamp,current['runId'],from_stage=continuation_stage)
            else:continue
        if not current or current['state'] not in ('queued','running','stop_requested'):
            if automation['automaticOperationsEnabled'] and sources:
                scheduled=min(due_times[source] for source in sources)
                attempts=store.db.execute("SELECT count(*) FROM workflow_run WHERE market=? AND trigger_source='schedule' AND scheduled_at=?",
                                          (market,scheduled)).fetchone()[0]
                same_failure=bool(current and current['state']=='failed' and current['scheduledAt']==scheduled)
                retry_at=(current['finishedAt'] or stamp)+3600 if same_failure else stamp
                if same_failure and (attempts>=4 or stamp<retry_at):
                    progress['nextDue']['workflow' if market=='it' else f'workflow:{market}']=retry_at if attempts<4 else None
                    progress['error']=progress['error'] or current.get('errorCode') or 'workflow_retry_exhausted'
                else:
                    request_id=f'{market}:schedule:{int(scheduled)}:retry:{attempts}' if attempts else None
                    current=create_run(store,market=market,trigger_source='schedule',scheduled_at=scheduled,
                                       sources=sources,request_id=request_id)
        if current and current['triggerSource']=='schedule' and current['state']!='stop_requested' and not automation['automaticOperationsEnabled']:
            continue
        if current and current['state'] in ('queued','running','stop_requested'):active.append(current)
    for row in active:
        if row['state']!='stop_requested':continue
        with store.tx():
            store.db.execute("UPDATE workflow_stage_run SET state='stopped',finished_at=? WHERE run_id=? AND state IN ('queued','waiting_upstream')",(stamp,row['runId']))
            if not store.db.execute("SELECT 1 FROM workflow_stage_run WHERE run_id=? AND state='running' LIMIT 1",(row['runId'],)).fetchone():
                store.db.execute("UPDATE workflow_run SET state='stopped',finished_at=? WHERE run_id=?",(stamp,row['runId']))
        projected.append((row['market'],'operations'))
    active=[row for row in active if row['state']!='stop_requested']
    return active,policy,projected


def tick(root,*,now=None,executor=None,refill=False,clock=None,wait_seconds=30,stopped=lambda:False):
    """Run one dispatch cycle; resident mode refills free slots while other stages are pending.

    ``refill=False`` keeps --once bounded to its initial claims. No new authorization is created.
    Running children finish their original stage on shutdown; claims remain supervised until then.
    """
    if not 0<wait_seconds<=30:raise ValueError('scheduler_wait_invalid')
    current_time=clock or (time.time if now is None else lambda:float(now))
    root=Path(root);stamp=current_time();executor=executor or SubprocessStageExecutor(root)
    database=root/'var/second-cycle.sqlite'
    previous=_read(status_path(root),{})
    progress={'checkedAt':stamp,'runId':None,'stage':None,'lastSuccess':previous.get('lastSuccess') or {},
              'lastAttempt':previous.get('lastAttempt') or {},'nextDue':{},'error':None,'runningStages':[]}
    if not database.exists():progress['error']='workflow_database_missing';_write(status_path(root),progress);return progress
    if stopped() or stop_path(root).exists():
        progress['state']='stopping';_write(status_path(root),progress);return progress
    from lib.jobs import load
    jobs=load(root)
    projected=[]
    with CycleStore(database,**({'clock':clock} if clock else {})) as store:
        active,policy,updates=_collect_ready_runs(root,store,jobs,stamp,progress);projected.extend(updates)
        owner_id=f'scheduler-{os.getpid()}-{int(stamp*1000)}'
        selection=claim_ready(store,root,active,policy,owner_id,max_parallel=min(14,len(active) or 1),worker_pid=os.getpid())
        tasks=selection['claimed']
        if selection['recovered']:progress['recoveredStageRuns']=selection['recovered']
        if not tasks:
            _project_read_models(root,projected,progress)
            _write(status_path(root),progress);return progress
        first=tasks[0];progress.update(runId=first['run']['runId'],market=first['run']['market'],stage=first['stage']['stage'],
                                     parallelMarkets=[task['run']['market'] for task in tasks])

        def execute_one(task,job_config):
            with CycleStore(database,**({'clock':clock} if clock else {})) as stage_store:
                tracked=isinstance(executor,SubprocessStageExecutor)
                if tracked:
                    executor._claims.ticket=task['ticket']|{'ownerId':owner_id}
                    executor._claims.ledger={'launched':0,'writes':0,'uncertain':False}
                try:
                    try:result=executor.execute(stage_store,task['run'],task['stage']['stage'],job_config)
                    except Exception as error:
                        # Writes already reported by finished children stay in the ledger.
                        result={'state':'failed','itemCount':0,'complete':False,'platformWrites':0,'scope':{},'payload':{},
                                'errorCode':str(error) if isinstance(error,(CycleError,ValueError)) else type(error).__name__}
                        if not tracked:result['writeEvidence']='uncertain'
                    return with_write_evidence(result,executor._claims.ledger if tracked else None)
                finally:
                    if tracked:executor._claims.ticket=None;executor._claims.ledger=None

        # Read-model projection runs beside the supervision loop (each target may take up to 60 s):
        # heartbeats and settlement never wait for it.
        projector=[None]
        def project_async():
            nonlocal projected
            if not projected or (projector[0] is not None and projector[0].is_alive()):return
            targets,projected=projected,[]
            projector[0]=threading.Thread(target=_project_read_models,args=(root,targets,progress),daemon=True)
            projector[0].start()

        with ThreadPoolExecutor(max_workers=14 if refill else len(tasks)) as pool:
            pending={}
            def submit(tasks,job_config):
                for task in tasks:
                    market=task['run']['market'];stage=task['stage']['stage']
                    progress['lastAttempt'][stage if market=='it' else f'{market}:{stage}']=current_time()
                    pending[pool.submit(execute_one,task,job_config)]=task
            submit(tasks,jobs)
            _write(status_path(root),progress)
            while pending:
                done,_=wait(tuple(pending),timeout=wait_seconds,return_when=FIRST_COMPLETED)
                stamp=current_time();progress['checkedAt']=stamp
                # A fresh heartbeat/status must advance even when no future has completed.
                for future,task in pending.items():
                    if future in done:continue
                    ticket=task['ticket']
                    try:heartbeat(store,ticket['stageRunId'],owner_id,ticket['fence'],lease_seconds=300)
                    except (CycleError,sqlite3.Error) as error:progress['error']=str(error)[:120]
                for future in done:
                    task=pending.pop(future);run=task['run'];stage=task['stage']['stage'];ticket=task['ticket']
                    try:result=future.result()
                    except BaseException as error:
                        result={'state':'failed','itemCount':0,'complete':False,'platformWrites':0,'writeEvidence':'uncertain',
                                'scope':{},'payload':{},'errorCode':str(error) if isinstance(error,(CycleError,ValueError)) else type(error).__name__}
                    try:
                        finish_stage(store,run['runId'],stage,state=result['state'],item_count=result.get('itemCount',0),
                          scope=result.get('scope'),payload=result.get('payload'),complete=result.get('complete',True),
                          platform_writes=result.get('platformWrites',0),error_code=result.get('errorCode'),
                          write_evidence=result.get('writeEvidence'),identity_account=result.get('identityAccount'),
                          claim_ticket=ticket|{'ownerId':owner_id})
                        release(store,ticket['stageRunId'],owner_id,ticket['fence'])
                    except (CycleError,sqlite3.Error) as error:
                        progress['error']=str(error)[:120]
                        if str(error)=='workflow_stage_fence_stale':
                            # Recovered by another owner meanwhile: keep this result as evidence.
                            try:record_late_result(store,ticket['stageRunId'],owner_id,ticket['fence'],result)
                            except (CycleError,sqlite3.Error):pass
                        continue
                    market=run['market'];key=stage if market=='it' else f'{market}:{stage}'
                    if result['state'] in ('completed','quota_exhausted','skipped'):progress['lastSuccess'][key]=stamp
                    elif progress['error'] is None:progress['error']=result.get('errorCode') or result['state']
                    projected.append((market,'operations'))
                    if stage in ('catalog','taplink_prepare'):projected.append((market,'catalog'))
                if not stopped() and not stop_path(root).exists():
                    try:
                        jobs=load(root)
                        if refill and pending and len(pending)<14:
                            active,policy,updates=_collect_ready_runs(root,store,jobs,stamp,progress)
                            projected.extend(updates)
                            selection=claim_ready(store,root,active,policy,owner_id,
                                max_parallel=max(1,14-len(pending)),worker_pid=os.getpid())
                            submit(selection['claimed'],jobs)
                            if selection['recovered']:
                                progress.setdefault('recoveredStageRuns',[]).extend(selection['recovered'])
                        else:
                            from lib.market_registry import enabled_market_keys
                            snapshots={m:workflow_status(store,m) for m in enabled_market_keys(root)}
                            automation=snapshots.get('it',{'setting':{'automaticOperationsEnabled':False}})['setting']
                            _background(root,store,jobs,automation,stamp,
                                maintenance_enabled=any(v['setting']['automaticOperationsEnabled'] for v in snapshots.values()))
                    except (CycleError,sqlite3.Error,OSError,ValueError) as error:progress['error']=str(error)[:120]
                else:progress['state']='stopping'
                progress['runningStages']=[{'runId':t['run']['runId'],'market':t['run']['market'],
                    'stage':t['stage']['stage'],'fence':t['ticket']['fence']} for t in pending.values()]
                _write(status_path(root),progress)
                project_async()
        if projector[0] is not None:projector[0].join()
        if projected:_project_read_models(root,projected,progress)
    _write(status_path(root),progress);return progress


def _background(root,store,jobs,automation,stamp,maintenance_enabled=None):
    """Keep independent monitors/runtimes alive without treating them as serial workflow stages."""
    enabled=jobs['jobs']
    from lib.im_session_owner import enabled as sdk_enabled,launch as launch_session
    from lib.job_run import start as start_job,state as job_state
    if sdk_enabled(root,'it'):
        if (automation['automaticOperationsEnabled'] or enabled['inbox_monitor']['enabled']) and not (Path(root)/'var/job-inbox.stop').exists():
            try:launch_session(root,'it')
            except OSError:pass
    elif (automation['automaticOperationsEnabled'] or enabled['inbox_monitor']['enabled']) and not (job_state(root,'inbox') or {}).get('running'):
        try:start_job(root,'inbox',{'limit':12,'interval':30})
        except (ValueError,OSError):pass
    from lib.template_library import agent_setting
    plan=store.db.execute("SELECT id FROM plan WHERE institution='bjn-local-research' AND market='it'").fetchone()
    if plan and agent_setting(store,plan[0])['enabled'] and not (job_state(root,'agentReply') or {}).get('running'):
        try:start_job(root,'agentReply',{'interval':60})
        except (ValueError,OSError):pass
    tables={row[0] for row in store.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if {'continuous_send_control','cycle_delivery','cycle_delivery_part'}<=tables:
        from lib.continuous_send import control as send_control,launch_worker,worker_state
        control=send_control(store,root)
        if (enabled['continuous_send']['enabled'] or control['automaticEnabled'] or control['runRequested']) and \
           (control['automaticEnabled'] or control['runRequested']) and \
           not control['stopRequested'] and not worker_state(root)['running']:
            launch_worker(root)
    from lib.operations_workflow import setting as market_setting
    from lib.market_registry import enabled_market_keys
    from lib.market_send_worker import launch as launch_market_sender,state as market_sender_state
    for market in enabled_market_keys(root):
        if market=='it':continue
        plan_row=store.db.execute("SELECT id FROM plan WHERE market=? AND institution='bjn-local-research' AND state='active'",(market,)).fetchone()
        if not plan_row:continue
        current=market_setting(store,market)
        sender_control=__import__('lib.market_send_control',fromlist=['control']).control(store,market)
        agent_enabled=agent_setting(store,plan_row[0])['enabled']
        # Inbox is part of the authorized send/reply loop even when the broader
        # catalog automation switch is off (MY is currently operated this way).
        if current['automaticOperationsEnabled'] or agent_enabled or (
            not sender_control['stopRequested'] and
            (current['continuousSendEnabled'] or sender_control['automaticEnabled'] or sender_control['runRequested'])):
            inbox_state=_read(Path(root)/f'var/market-inbox-{market}.json',{})
            inbox_pid=inbox_state.get('pid')
            inbox_alive=pid_alive(inbox_pid)
            if sdk_enabled(root,market):
                try:launch_session(root,market)
                except OSError:pass
            elif not inbox_alive:
                log=Path(root)/f'var/market-inbox-{market}.log'
                try:
                    with log.open('a',encoding='utf-8') as handle:
                        child=subprocess.Popen([str(Path(root)/'.venv/bin/python'),str(Path(root)/'scripts/poll-market-inbox.py'),
                                          '--market',market,'--worker'],cwd=str(root),stdin=subprocess.DEVNULL,
                                          stdout=handle,stderr=subprocess.STDOUT,start_new_session=True,
                                          env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'})
                    _write(Path(root)/f'var/market-inbox-{market}.json',
                           {'pid':child.pid,'running':True,'state':'starting','startedAt':time.time()})
                except OSError:pass
        if agent_enabled:
            reply_state=_read(Path(root)/f'var/agent-reply-status-{market}.json',{})
            reply_pid=reply_state.get('pid')
            reply_alive=pid_alive(reply_pid)
            if not reply_alive:
                log=Path(root)/f'var/agent-reply-{market}.log'
                try:
                    with log.open('a',encoding='utf-8') as handle:
                        child=subprocess.Popen([str(Path(root)/'.venv/bin/python'),str(Path(root)/'scripts/run-agent-replies.py'),
                            '--worker','--market',market],cwd=str(root),stdin=subprocess.DEVNULL,
                            stdout=handle,stderr=subprocess.STDOUT,start_new_session=True,
                            env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'})
                    _write(Path(root)/f'var/agent-reply-status-{market}.json',
                           {'pid':child.pid,'state':'starting','checkedAt':time.time()})
                except OSError:pass
        if (current['continuousSendEnabled'] or sender_control['automaticEnabled'] or sender_control['runRequested']) and \
           not sender_control['stopRequested'] and not market_sender_state(root,market)['running']:
            sender=market_sender_state(root,market)
            # Restart keeps the existing authorization and enters bounded original-intent reads.
            cooling=sender.get('state')=='attention' and stamp-float(sender.get('checkedAt') or stamp)<300
            if not cooling:
                try:launch_market_sender(root,market)
                except OSError:pass
    if (automation['automaticOperationsEnabled'] if maintenance_enabled is None else maintenance_enabled):
        from lib.account_identity import assignments,current_generation,next_due,identity_baseline,request_maintenance,recover_abandoned,status as account_status
        abandoned=recover_abandoned(store,now=stamp)
        for intent_id in abandoned:
            prior=store.db.execute('SELECT * FROM account_maintenance_intent WHERE intent_id=?',(intent_id,)).fetchone()
            published=current_generation(store,prior['market'],prior['account'])
            if published and published['publishedAt'] and published['publishedAt']>=(prior['started_at'] or stamp):
                continue
            request_id='maintenance-recovery-'+digest([intent_id,prior['operation']])[:24]
            request_maintenance(store,root,market=prior['market'],account=prior['account'],
                                operation=prior['operation'],request_id=request_id,scheduled_at=stamp+300)
        try:rows=assignments(root)
        except (OSError,ValueError):rows=[]
        for row in rows:
            generation=current_generation(store,row['market'],row['account'])
            due=next_due(identity_baseline(store,generation),row['role'],stamp)
            if stamp<due:continue
            request_id=f"scheduled-{row['account']}-{int(due)}"
            try:request_maintenance(store,root,market=row['market'],account=row['account'],operation='refresh',request_id=request_id,scheduled_at=due)
            except CycleError as error:
                if str(error) not in ('account_maintenance_active',):raise
        queue=account_status(store,root)['queue'] if rows else []
        if any(item['state']=='queued' and item['scheduledAt']<=stamp for item in queue) and \
           not any(item['state'] in ('draining','running') for item in queue):
            log=Path(root)/'var/account-maintenance.log'
            try:
                with log.open('a',encoding='utf-8') as handle:
                    subprocess.Popen([str(Path(root)/'.venv/bin/python'),str(Path(root)/'scripts/account-maintenance-worker.py')],
                      cwd=str(root),stdin=subprocess.DEVNULL,stdout=handle,stderr=subprocess.STDOUT,
                      start_new_session=True,env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'})
            except OSError:pass
