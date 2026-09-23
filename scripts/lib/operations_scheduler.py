"""Scheduler and stage adapter for the durable automated-operations workflow."""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import time
from contextlib import closing
from concurrent.futures import FIRST_COMPLETED,ThreadPoolExecutor,wait
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from lib.operations_workflow import create_run,finish_stage,status as workflow_status
from lib.second_cycle import CycleError, CycleStore, digest
from lib.workflow_dispatch import claim_ready
from lib.workflow_resources import assert_current,heartbeat,release


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


def _report_item_count(payload):
    """Extract the final durable queue size without summing repeated batch passes."""
    if not isinstance(payload,dict):return 0
    for key in ('finalSummary','summary'):
        summary=payload.get(key)
        value=summary.get('total') if isinstance(summary,dict) else None
        if type(value) is int and value>=0:return value
    return 0


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


def scheduler_state(root):
    run=_read(run_path(root),{});pid=run.get('pid');alive=False
    if type(pid) is int and pid>0:
        try:os.kill(pid,0);alive=True
        except OSError:pass
    progress=_read(status_path(root),{})
    return {'running':alive,'stopping':alive and stop_path(root).exists(),'pid':pid if type(pid) is int else None,
      'startedAt':run.get('startedAt'),'phase':progress.get('stage'),'cycle':progress.get('runId'),
      'checkedAt':progress.get('checkedAt'),'lastSuccess':progress.get('lastSuccess') or {},
      'lastAttempt':progress.get('lastAttempt') or {},'nextDue':progress.get('nextDue') or {},
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
        due_times['selected']=selected_due
        if stamp>=selected_due:sources.insert(0,'selected')
    return sources,due_times


class SubprocessStageExecutor:
    """Production adapter. Every command is fixed argv; no shell or user text is interpolated."""
    def __init__(self,root,runner=subprocess.run,clock=time.time):
        self.root=Path(root);self.runner=runner;self.clock=clock

    def _call(self,args,label,timeout=14400):
        stamp=time.strftime('%Y%m%d-%H%M%S')
        report=self.root/f'var/workflow-{label}-{stamp}-{digest(args)[:8]}.json'
        command=[str(self.root/'.venv/bin/python'),str(self.root/args[0]),*args[1:]]
        if args[0] in {'scripts/catalog-clean.py','scripts/campaign-collect.py',
                       'scripts/catalog-link-batch.py','scripts/identity-batch.py'} and '--report' not in command:
            command+=['--report',str(report)]
        child=self.runner(command,cwd=str(self.root),capture_output=True,text=True,timeout=timeout,
                          env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'})
        lines=[line for line in (child.stdout or '').splitlines() if line.strip()]
        try:payload=json.loads(lines[-1]) if lines else {}
        except ValueError:payload={}
        evidence={}
        if report.exists():
            try:evidence=json.loads(report.read_text(encoding='utf-8'))
            except (OSError,ValueError):evidence={}
        reported_state=str(evidence.get('state') or evidence.get('status') or payload.get('state') or payload.get('status') or '')
        writes=max(_report_platform_writes(evidence),_report_platform_writes(payload))
        items=max(_report_item_count(evidence),_report_item_count(payload))
        if child.returncode or reported_state in {'blocked','failed','partial','needs_human'}:
            code=str(payload.get('error') or evidence.get('error') or f'{label}_failed')
            return {'state':'needs_human' if reported_state=='needs_human' or 'maintenance' in code or 'auth' in code else 'failed',
                    'itemCount':items,'complete':False,'platformWrites':writes,
                    'errorCode':code[:120],'payload':{'report':str(report.relative_to(self.root)) if report.exists() else None}}
        return {'state':'completed','itemCount':items,'complete':True,'platformWrites':writes,
                'payload':evidence or payload,'scope':{}}

    def _relogin_market_account(self,store,market,role,run_id,reason):
        if store is None:return {'state':'failed','errorCode':'selection_relogin_store_missing'}
        from lib.account_identity import current_generation,request_maintenance
        from lib.market_accounts import load_config
        account=load_config(self.root)['markets'][market]['roles'][role];prior=current_generation(store,market,account)
        request_id=f'{role}-auth-{market}-'+digest([run_id,reason,(prior or {}).get('generationId')])[:24]
        try:request_maintenance(store,self.root,market=market,account=account,operation='relogin',request_id=request_id)
        except CycleError as error:
            if str(error)!='account_maintenance_active':return {'state':'failed','errorCode':str(error)}
        worker=self._call(['scripts/account-maintenance-worker.py'],'global-selection-relogin',timeout=660)
        current=current_generation(store,market,account)
        if worker['state']!='completed' or not current or not prior or current['publishedAt']<=prior['publishedAt']:
            return {'state':'failed','errorCode':worker.get('errorCode') or 'selection_relogin_not_published'}
        return {'state':'completed','generationId':current['generationId']}

    def _relogin_selection_account(self,store,market,run_id,auth):
        return self._relogin_market_account(store,market,'supply',run_id,auth.get('latestAttempt'))

    def execute(self,store,run,stage,jobs):
        sources=run['applicableSources'];enabled=jobs['jobs'];market=run.get('market','it')
        market_flag=[] if market=='it' else ['--market',market]
        if stage=='taplink_clean':
            if market!='it':
                return {'state':'skipped','itemCount':0,'complete':True,'platformWrites':0,
                        'scope':{'market':market},'payload':{'reason':'market_taplink_cleanup_not_enabled'}}
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
            outputs=[];count=0;writes=0
            if 'selected' in sources:
                frozen=accepted_partial_selection(self.root,market)
                if frozen:
                    result={'state':'completed','itemCount':frozen['products'],'complete':True,'platformWrites':0,
                            'scope':{'market':market,'coverage':'operator_accepted_partial'},
                            'payload':{'state':'accepted_partial','published':True,'reusedPublishedSnapshot':frozen}}
                else:
                    rid=f'{market}-global-'+time.strftime('%Y%m%d')+'-'+digest([run['runId'],'selected'])[:12]
                    collect=['scripts/collect-global-opportunity.py',*market_flag,'--run-id',rid,'--pages','40','--worker']
                    from lib.operations_policy import full_catalog_collection_mode
                    collection_mode=full_catalog_collection_mode(self.root,market,self.clock())
                    if collection_mode['mode']=='category':collect.append('--by-category')
                    result=self._call(collect,'global-catalog')
                    if result['state']!='completed':return result
                    if (result.get('payload') or {}).get('state')!='completed' or (result.get('payload') or {}).get('published') is not True:
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
                        return {**recovered,'state':'needs_human','complete':False,
                                'errorCode':str(recovered_payload.get('error') or 'global_selection_unresolved')[:120],
                                'platformWrites':writes}
                if True:
                    for _ in range(40):
                        selected=self._call(['scripts/select-global-products.py','execute-fast',*market_flag,'--limit','300',
                          '--native-listing','--reconcile-rejections','--skip-after-readback',
                          '--lanes','8','--qps','8','--group-size','100'],
                                            'global-selection')
                        outputs.append(selected);writes+=selected.get('platformWrites',0)
                        if selected['state']!='completed':return selected|{'platformWrites':writes}
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
                            return {**selected,'state':'needs_human','complete':False,
                                    'errorCode':'global_selection_unresolved','platformWrites':writes}
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
                join_status=self._call(['scripts/campaign-join.py','status',*market_flag],'campaign-join-status')
                if join_status['state']!='completed':return join_status|{'platformWrites':writes}
                join_payload=join_status.get('payload') or {}
                if join_payload.get('available') and join_payload.get('unresolved'):
                    verified=self._call(['scripts/campaign-join.py','verify',*market_flag],'campaign-join-verify')
                    if verified['state']!='completed':return verified|{'platformWrites':writes}
                    verified_payload=verified.get('payload') or {}
                    if verified_payload.get('unresolved'):
                        return {**verified,'state':'needs_human','complete':False,
                                'errorCode':'campaign_join_result_unknown','platformWrites':writes}
                joined=self._call(['scripts/campaign-join.py','join-all',*market_flag,'--confirm'],'campaign-join')
                writes+=joined.get('platformWrites',0);joined_payload=joined.get('payload') or {}
                if joined['state']!='completed':return joined|{'platformWrites':writes}
                if joined_payload.get('state')=='needs_verification' or joined_payload.get('unresolved'):
                    return {**joined,'state':'needs_human','complete':False,
                            'errorCode':'campaign_join_result_unknown','platformWrites':writes}
                outputs.append(joined)
                result=self._call(['scripts/campaign-collect.py',*market_flag,'--max-requests','150','--passes','200','--screen'],
                                  'campaign-catalog')
                if result['state']!='completed':return result|{'platformWrites':writes}
                evidence=result.get('payload') or {}
                if evidence.get('status')!='completed' or not (evidence.get('screening') or {}).get('recorded'):
                    return {**result,'state':'failed','complete':False,'errorCode':'campaign_catalog_not_published',
                            'platformWrites':writes}
                outputs.append(result);count+=int((result.get('payload') or {}).get('offers') or 0)
            return {'state':'completed','itemCount':count,'complete':True,'platformWrites':writes,
                    'scope':{'sources':sources},'payload':{'sources':outputs}}
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
                names=self._call(['scripts/catalog-names.py','prepare','--all',*market_flag],'taplink-short-names-'+route)
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
            sales=self._call(['scripts/leads-run.py',*market_flag,'--limit','5000','--max-pages','20'],'kalodata-sales')
            stopped=str((sales.get('payload') or {}).get('stopped') or sales.get('errorCode') or '')
            state='quota_exhausted' if stopped=='kalodata_daily_quota_exhausted' else sales['state']
            completed=int((sales.get('payload') or {}).get('done') or 0)
            if state!='completed':return {**sales,'state':state,'complete':False,'itemCount':completed,'scope':{'sources':sources,'aCompleted':completed}}
            if market!='it':return {**sales,'state':'completed','complete':True,'itemCount':completed,'scope':{'sources':sources,'aCompleted':completed}}
            initialized=self._call(['scripts/kalodata-video-crawl.py','init'],'kalodata-video-init')
            generation=(initialized.get('payload') or {}).get('generationId')
            if initialized['state']!='completed' or not generation:return {**initialized,'state':'failed','complete':False,'errorCode':'video_generation_missing'}
            videos=self._call(['scripts/kalodata-video-crawl.py','run','--generation',str(generation)],'kalodata-video-run')
            video_error=str((videos.get('payload') or {}).get('error') or videos.get('errorCode') or '')
            video_state='quota_exhausted' if video_error=='kalodata_daily_quota_exhausted' else videos['state']
            video_counts=(((videos.get('payload') or {}).get('status') or {}).get('counts') or {})
            video_done=int(video_counts.get('completed') or video_counts.get('done') or 0)
            return {**videos,'state':video_state,'complete':video_state=='completed','itemCount':completed+video_done,
                    'scope':{'sources':sources,'aCompleted':completed,'bGeneration':generation,'bCompleted':video_done}}
        if stage=='oecid':
            if market!='it':
                total=0;outputs=[];result=None;left=0;auth_recoveries=0
                for _ in range(500):
                    result=self._call(['scripts/market-identity.py','run','--market',market,'--limit','50'],'oecid')
                    outputs.append(result)
                    if result['state']!='completed':
                        if result.get('errorCode')=='market_identity_auth_required' and auth_recoveries<2:
                            relogin=self._relogin_market_account(store,market,'communications',run['runId'],
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
            result=self._call(['scripts/identity-batch.py','--limit','200000','--cohort-size','50'],'oecid')
            payload=result.get('payload') or {};pending=int(payload.get('pending') or 0)
            result['itemCount']=int(payload.get('claimed') or 0)
            result['scope']={'sources':sources,'handoffBatches':submitted,'pending':pending}
            if result['state']=='completed' and pending:
                reason=str(payload.get('stopReason') or 'pending')
                result.update(state='needs_human',complete=False,errorCode=('identity_'+reason)[:120])
            return result
        if stage=='send_pool':
            result=self._call(['scripts/lead-pool.py','status','--limit','1',*market_flag],'send-pool')
            result['itemCount']=int(((result.get('payload') or {}).get('counts') or {}).get('positions') or 0);return result
        raise CycleError('workflow_stage_invalid')


def tick(root,*,now=None,executor=None):
    root=Path(root);stamp=time.time() if now is None else float(now);executor=executor or SubprocessStageExecutor(root)
    database=root/'var/second-cycle.sqlite'
    previous=_read(status_path(root),{})
    progress={'checkedAt':stamp,'runId':None,'stage':None,'lastSuccess':previous.get('lastSuccess') or {},
              'lastAttempt':previous.get('lastAttempt') or {},'nextDue':{},'error':None}
    if not database.exists():progress['error']='workflow_database_missing';_write(status_path(root),progress);return progress
    from lib.jobs import load
    jobs=load(root)
    projected=[]
    with CycleStore(database) as store:
        from lib.market_registry import enabled_market_keys
        markets=enabled_market_keys(root);snapshots={market:workflow_status(store,market) for market in markets}
        it_automation=snapshots.get('it',{'setting':{'automaticOperationsEnabled':False}})['setting']
        any_automation=any(snapshot['setting']['automaticOperationsEnabled'] for snapshot in snapshots.values())
        _background(root,store,jobs,it_automation,stamp,maintenance_enabled=any_automation)
        from lib.operations_policy import load_policy
        policy=load_policy(root);active=[]
        for market,snapshot in snapshots.items():
            automation=snapshot['setting'];current=snapshot['current'];sources,due_times=_scheduled_sources(root,store,market,stamp,automation,policy)
            future=[value for value in due_times.values() if value>stamp]
            progress['nextDue']['workflow' if market=='it' else f'workflow:{market}']=min(future) if future else min(due_times.values())
            if not current or current['state'] not in ('queued','running','stop_requested'):
                if automation['automaticOperationsEnabled'] and sources:
                    scheduled=min(due_times[source] for source in sources)
                    current=create_run(store,market=market,trigger_source='schedule',scheduled_at=scheduled,sources=sources)
            if current and current['state'] in ('queued','running','stop_requested'):active.append(current)
        for row in active:
            if row['state']!='stop_requested':continue
            with store.tx():
                store.db.execute("UPDATE workflow_stage_run SET state='stopped',finished_at=? WHERE run_id=? AND state IN ('queued','waiting_upstream')",(stamp,row['runId']))
                store.db.execute("UPDATE workflow_run SET state='stopped',finished_at=? WHERE run_id=?",(stamp,row['runId']))
            projected.append((row['market'],'operations'))
        active=[row for row in active if row['state']!='stop_requested']
        owner_id=f'scheduler-{os.getpid()}-{int(stamp*1000)}'
        selection=claim_ready(store,root,active,policy,owner_id,max_parallel=min(14,len(active) or 1),worker_pid=os.getpid())
        tasks=selection['claimed']
        if selection['recovered']:progress['recoveredStageRuns']=selection['recovered']
        if not tasks:
            _project_read_models(root,projected,progress)
            _write(status_path(root),progress);return progress
        first=tasks[0];progress.update(runId=first['run']['runId'],market=first['run']['market'],stage=first['stage']['stage'],
                                     parallelMarkets=[task['run']['market'] for task in tasks])
        for task in tasks:
            market=task['run']['market'];stage=task['stage']['stage']
            progress['lastAttempt'][stage if market=='it' else f'{market}:{stage}']=stamp
        _write(status_path(root),progress)

        def execute_one(task):
            with CycleStore(database) as stage_store:
                return executor.execute(stage_store,task['run'],task['stage']['stage'],jobs)

        with ThreadPoolExecutor(max_workers=len(tasks)) as pool:
            pending={pool.submit(execute_one,task):task for task in tasks}
            while pending:
                done,_=wait(tuple(pending),timeout=30,return_when=FIRST_COMPLETED)
                for future in done:
                    task=pending.pop(future);run=task['run'];stage=task['stage']['stage'];ticket=task['ticket']
                    try:result=future.result()
                    except BaseException as error:
                        result={'state':'failed','itemCount':0,'complete':False,'platformWrites':0,
                                'scope':{},'payload':{},'errorCode':str(error) if isinstance(error,(CycleError,ValueError)) else type(error).__name__}
                    try:
                        assert_current(store,ticket['stageRunId'],owner_id,ticket['fence'])
                        finish_stage(store,run['runId'],stage,state=result['state'],item_count=result.get('itemCount',0),
                          scope=result.get('scope'),payload=result.get('payload'),complete=result.get('complete',True),
                          platform_writes=result.get('platformWrites',0),error_code=result.get('errorCode'))
                        release(store,ticket['stageRunId'],owner_id,ticket['fence'])
                    except (CycleError,sqlite3.Error) as error:
                        progress['error']=str(error)[:120];continue
                    market=run['market'];key=stage if market=='it' else f'{market}:{stage}'
                    if result['state'] in ('completed','quota_exhausted','skipped'):progress['lastSuccess'][key]=stamp
                    elif progress['error'] is None:progress['error']=result.get('errorCode') or result['state']
                    projected.append((market,'operations'))
                    if stage in ('catalog','taplink_prepare'):projected.append((market,'catalog'))
                for task in pending.values():
                    ticket=task['ticket']
                    try:heartbeat(store,ticket['stageRunId'],owner_id,ticket['fence'],lease_seconds=300)
                    except (CycleError,sqlite3.Error) as error:progress['error']=str(error)[:120]
                if done:_write(status_path(root),progress)
    _project_read_models(root,projected,progress)
    _write(status_path(root),progress);return progress


def _background(root,store,jobs,automation,stamp,maintenance_enabled=None):
    """Keep independent monitors/runtimes alive without treating them as serial workflow stages."""
    enabled=jobs['jobs']
    from lib.job_run import start as start_job,state as job_state
    if (automation['automaticOperationsEnabled'] or enabled['inbox_monitor']['enabled']) and not (job_state(root,'inbox') or {}).get('running'):
        try:start_job(root,'inbox',{'limit':6,'interval':60})
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
        if (enabled['continuous_send']['enabled'] or control['automaticEnabled']) and control['automaticEnabled'] and \
           not control['stopRequested'] and not worker_state(root)['running']:
            launch_worker(root)
    from lib.operations_workflow import setting as market_setting
    from lib.market_registry import enabled_market_keys
    from lib.market_send_worker import launch as launch_market_sender,state as market_sender_state
    for market in enabled_market_keys(root):
        if market=='it':continue
        current=market_setting(store,market)
        if current['continuousSendEnabled'] and not market_sender_state(root,market)['running']:
            try:launch_market_sender(root,market)
            except OSError:pass
    if (automation['automaticOperationsEnabled'] if maintenance_enabled is None else maintenance_enabled):
        from lib.account_identity import assignments,current_generation,next_due,request_maintenance,status as account_status
        try:rows=assignments(root)
        except (OSError,ValueError):rows=[]
        for row in rows:
            generation=current_generation(store,row['market'],row['account'])
            due=next_due(generation['publishedAt'] if generation else None,row['role'],stamp)
            if stamp<due:continue
            request_id=f"scheduled-{row['account']}-{int(due)}"
            try:request_maintenance(store,root,market=row['market'],account=row['account'],operation='refresh',request_id=request_id,scheduled_at=due)
            except CycleError as error:
                if str(error) not in ('account_maintenance_active',):raise
        queue=account_status(store,root)['queue'] if rows else []
        if any(item['state']=='queued' for item in queue) and not any(item['state'] in ('draining','running') for item in queue):
            log=Path(root)/'var/account-maintenance.log'
            try:
                with log.open('a',encoding='utf-8') as handle:
                    subprocess.Popen([str(Path(root)/'.venv/bin/python'),str(Path(root)/'scripts/account-maintenance-worker.py')],
                      cwd=str(root),stdin=subprocess.DEVNULL,stdout=handle,stderr=subprocess.STDOUT,
                      start_new_session=True,env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'})
            except OSError:pass
