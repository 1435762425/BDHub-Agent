"""Scheduler and stage adapter for the durable automated-operations workflow."""
from __future__ import annotations

import json
import os
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from lib.operations_workflow import (STAGES, create_run, finish_stage, run_payload, setting,
                                     start_stage, status as workflow_status)
from lib.second_cycle import CycleError, CycleStore, digest


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
    monday=local.weekday()==0
    selected=jobs['taplink_clean'] if monday else jobs['campaign_catalog_update']
    hour,minute=map(int,selected['at'].split(':'))
    return local.replace(hour=hour,minute=minute,second=0,microsecond=0).timestamp()


def _day_start(stamp):
    local=datetime.fromtimestamp(stamp,BEIJING)
    return local.replace(hour=0,minute=0,second=0,microsecond=0).timestamp()


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
        if child.returncode or reported_state in {'blocked','failed','partial','needs_human'}:
            code=str(payload.get('error') or evidence.get('error') or f'{label}_failed')
            return {'state':'needs_human' if reported_state=='needs_human' or 'maintenance' in code or 'auth' in code else 'failed',
                    'itemCount':0,'complete':False,'platformWrites':int(evidence.get('platformWrites') or 0),
                    'errorCode':code[:120],'payload':{'report':str(report.relative_to(self.root)) if report.exists() else None}}
        return {'state':'completed','itemCount':0,'complete':True,
                'platformWrites':int(evidence.get('platformWrites') or payload.get('platformWrites') or 0),
                'payload':evidence or payload,'scope':{}}

    def execute(self,store,run,stage,jobs):
        sources=run['applicableSources'];enabled=jobs['jobs']
        if stage=='taplink_clean':
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
                rid='it-global-'+time.strftime('%Y%m%d')+'-'+digest([run['runId'],'selected'])[:12]
                result=self._call(['scripts/collect-global-opportunity.py','--run-id',rid,'--pages','40','--worker'],
                                  'global-catalog')
                if result['state']!='completed':return result
                if (result.get('payload') or {}).get('state')!='completed' or (result.get('payload') or {}).get('published') is not True:
                    return {**result,'state':'failed','complete':False,'errorCode':'global_catalog_not_published'}
                outputs.append(result);count+=int((result.get('payload') or {}).get('products') or 0)
            if 'campaign' in sources:
                join_status=self._call(['scripts/campaign-join.py','status'],'campaign-join-status')
                if join_status['state']!='completed':return join_status|{'platformWrites':writes}
                join_payload=join_status.get('payload') or {}
                if join_payload.get('available') and join_payload.get('unresolved'):
                    verified=self._call(['scripts/campaign-join.py','verify'],'campaign-join-verify')
                    if verified['state']!='completed':return verified|{'platformWrites':writes}
                    verified_payload=verified.get('payload') or {}
                    if verified_payload.get('unresolved'):
                        return {**verified,'state':'needs_human','complete':False,
                                'errorCode':'campaign_join_result_unknown','platformWrites':writes}
                joined=self._call(['scripts/campaign-join.py','join-all','--confirm'],'campaign-join')
                writes+=joined.get('platformWrites',0);joined_payload=joined.get('payload') or {}
                if joined['state']!='completed':return joined|{'platformWrites':writes}
                if joined_payload.get('state')=='needs_verification' or joined_payload.get('unresolved'):
                    return {**joined,'state':'needs_human','complete':False,
                            'errorCode':'campaign_join_result_unknown','platformWrites':writes}
                outputs.append(joined)
                result=self._call(['scripts/campaign-collect.py','--max-requests','150','--passes','200','--screen'],
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
            writes=0;outputs=[]
            for route in sources:
                prepared=self._call(['scripts/catalog-link-batch.py','--route',route,'--limit','200','--passes','80',
                  '--creates','0','--lanes','9','--qps','12','--seed'],'taplink-read-'+route)
                outputs.append(prepared)
                if prepared['state']!='completed':return prepared|{'platformWrites':writes}
                names=self._call(['scripts/catalog-names.py','prepare','--all'],'taplink-short-names-'+route)
                outputs.append(names);name_payload=names.get('payload') or {}
                if names['state']!='completed' or int(name_payload.get('missing') or 0)>0:
                    return {**names,'state':'needs_human','complete':False,
                            'errorCode':'catalog_short_names_incomplete','platformWrites':writes}
                created=self._call(['scripts/catalog-link-batch.py','--route',route,'--limit','200','--passes','0',
                  '--creates','200','--lanes','9','--qps','12'],'taplink-create-'+route)
                writes+=created.get('platformWrites',0);outputs.append(created)
                if created['state']!='completed':return created|{'platformWrites':writes}
            return {'state':'completed','itemCount':sum(item.get('itemCount',0) for item in outputs),
                    'complete':True,'platformWrites':writes,'scope':{'sources':sources},'payload':{'routes':outputs}}
        if stage=='kalodata':
            sales=self._call(['scripts/leads-run.py','--limit','5000','--max-pages','20'],'kalodata-sales')
            stopped=str((sales.get('payload') or {}).get('stopped') or sales.get('errorCode') or '')
            state='quota_exhausted' if stopped=='kalodata_daily_quota_exhausted' else sales['state']
            completed=int((sales.get('payload') or {}).get('done') or 0)
            if state!='completed':return {**sales,'state':state,'complete':False,'itemCount':completed,'scope':{'sources':sources,'aCompleted':completed}}
            initialized=self._call(['scripts/kalodata-video-crawl.py','init'],'kalodata-video-init')
            generation=(initialized.get('payload') or {}).get('generationId')
            if initialized['state']!='completed' or not generation:return {**initialized,'state':'failed','complete':False,'errorCode':'video_generation_missing'}
            videos=self._call(['scripts/kalodata-video-crawl.py','run','--generation',str(generation)],'kalodata-video-run')
            video_error=str((videos.get('payload') or {}).get('error') or videos.get('errorCode') or '')
            video_state='quota_exhausted' if video_error=='kalodata_daily_quota_exhausted' else videos['state']
            video_done=int((((videos.get('payload') or {}).get('status') or {}).get('counts') or {}).get('done') or 0)
            return {**videos,'state':video_state,'complete':video_state=='completed','itemCount':completed+video_done,
                    'scope':{'sources':sources,'aCompleted':completed,'bGeneration':generation,'bCompleted':video_done}}
        if stage=='oecid':
            result=self._call(['scripts/identity-batch.py','--limit','200000','--cohort-size','50'],'oecid')
            result['itemCount']=int((result.get('payload') or {}).get('claimed') or 0);return result
        if stage=='send_pool':
            result=self._call(['scripts/lead-pool.py','status','--limit','1'],'send-pool')
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
    with CycleStore(database) as store:
        snapshot=workflow_status(store);automation=snapshot['setting'];current=snapshot['current']
        _background(root,store,jobs,automation,stamp)
        due=due_slot(root,stamp,automation);progress['nextDue']={'workflow':due}
        if not current or current['state'] not in ('queued','running','stop_requested'):
            today=_day_start(stamp)
            exists=store.db.execute('SELECT 1 FROM workflow_run WHERE market=? AND scheduled_at>=?',('it',today)).fetchone()
            if automation['automaticOperationsEnabled'] and stamp>=due and not exists:
                current=create_run(store,market='it',trigger_source='schedule',scheduled_at=due)
        if not current or current['state'] not in ('queued','running','stop_requested'):
            _write(status_path(root),progress);return progress
        progress['runId']=current['runId']
        if current['state']=='stop_requested':
            with store.tx():
                store.db.execute("UPDATE workflow_stage_run SET state='stopped',finished_at=? WHERE run_id=? AND state IN ('queued','waiting_upstream')",(stamp,current['runId']))
                store.db.execute("UPDATE workflow_run SET state='stopped',finished_at=? WHERE run_id=?",(stamp,current['runId']))
            progress['stage']='stopped';_write(status_path(root),progress);return progress
        stage=next((row for row in current['stages'] if row['state']=='queued'),None)
        if not stage:_write(status_path(root),progress);return progress
        progress['stage']=stage['stage'];progress['lastAttempt'][stage['stage']]=stamp;_write(status_path(root),progress)
        previous=next((row['outputGenerationId'] for row in reversed(current['stages'][:stage['position']]) if row['outputGenerationId']),None)
        start_stage(store,current['runId'],stage['stage'],input_generation_id=previous)
        try:result=executor.execute(store,current,stage['stage'],jobs)
        except BaseException as error:
            result={'state':'failed','itemCount':0,'complete':False,'platformWrites':0,
                    'scope':{},'payload':{},'errorCode':str(error) if isinstance(error,(CycleError,ValueError)) else type(error).__name__}
        finish_stage(store,current['runId'],stage['stage'],state=result['state'],item_count=result.get('itemCount',0),
          scope=result.get('scope'),payload=result.get('payload'),complete=result.get('complete',True),
          platform_writes=result.get('platformWrites',0),error_code=result.get('errorCode'))
        if result['state'] in ('completed','quota_exhausted','skipped'):progress['lastSuccess'][stage['stage']]=stamp
        else:progress['error']=result.get('errorCode') or result['state']
    _write(status_path(root),progress);return progress


def _background(root,store,jobs,automation,stamp):
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
    if automation['automaticOperationsEnabled']:
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
