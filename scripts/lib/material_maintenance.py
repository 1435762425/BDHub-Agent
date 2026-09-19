"""Source-specific TapLink maintenance scheduler.  All scheduled link runs are read-only."""
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

BEIJING=ZoneInfo('Asia/Shanghai')
CYCLES={
 'campaign': {'job':'campaign_material_refresh','cadence':'daily'},
 'selected': {'job':'selected_taplink_verify','cadence':'weekly'},
}

def status_path(root):return Path(root)/'var/material-maintenance-status.json'
def run_path(root):return Path(root)/'var/material-maintenance-worker.json'
def stop_path(root):return Path(root)/'var/material-maintenance.stop'

def _read(path,default):
 try:
  value=json.loads(Path(path).read_text(encoding='utf-8'))
  return value if isinstance(value,dict) else default
 except (OSError,ValueError):return default

def _write(path,value):
 path=Path(path);path.parent.mkdir(parents=True,exist_ok=True);tmp=path.with_suffix(path.suffix+'.tmp')
 tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');tmp.replace(path)

def scheduler_state(root):
 run=_read(run_path(root),{});pid=run.get('pid');alive=False
 if type(pid) is int and pid>0:
  try:os.kill(pid,0);alive=True
  except OSError:pass
 progress=_read(status_path(root),{})
 return {'running':alive,'stopping':stop_path(root).exists() and alive,
         'pid':pid if type(pid) is int else None,'startedAt':run.get('startedAt'),
         'phase':progress.get('phase'),'cycle':progress.get('cycle'),
         'checkedAt':progress.get('checkedAt'),'lastSuccess':progress.get('lastSuccess') or {},
         'lastAttempt':progress.get('lastAttempt') or {},'nextDue':progress.get('nextDue') or {},
         'error':progress.get('error')}

def next_due(setting,cycle,last_success,now):
 local=datetime.fromtimestamp(now,BEIJING);hour,minute=map(int,setting['at'].split(':'))
 day=local.date();period=7*86400 if cycle=='selected' else 86400
 if cycle=='selected':day=day+timedelta(days=(setting.get('weekday',0)-day.weekday())%7)
 slot=datetime(day.year,day.month,day.day,hour,minute,tzinfo=BEIJING).timestamp()
 if slot>now:
  if not last_success:return slot
  slot-=period
 if last_success and last_success>=slot:return slot+period
 return slot

def _safe_config(job):
 if job=='campaignCollect':return {'maxRequests':150,'passes':200}
 return {'readLimit':200,'creates':0,'lanes':9,'qps':12}

def _success(job,run):
 if not run or run.get('running') or run.get('platformWrites') is True:return False
 progress=run.get('progress') or {}
 return progress.get('status')=='done' if job=='campaignCollect' else progress.get('phase')=='done'

def _initial():return {'phase':None,'cycle':None,'job':None,'jobStartedAt':None,'checkedAt':0,
                       'lastSuccess':{},'lastAttempt':{},'nextDue':{},'retryAt':0,'error':None}

def tick(root,*,now=None,starter=None,reader=None):
 """Advance one scheduler step.  It may launch only verified read-only jobs; never creates/deletes links."""
 root=Path(root);stamp=time.time() if now is None else now
 from lib.jobs import load
 from lib.job_run import start as real_start,state as real_state
 starter=starter or real_start;reader=reader or real_state
 config=load(root);current={**_initial(),**_read(status_path(root),{})}
 current['lastSuccess']=dict(current.get('lastSuccess') or {});current['lastAttempt']=dict(current.get('lastAttempt') or {})
 current['checkedAt']=stamp;current['error']=None

 def launch(cycle,job,phase):
  record=starter(root,job,_safe_config(job))
  current.update(phase=phase,cycle=cycle,job=job,jobStartedAt=record['startedAt'],retryAt=0)
  current['lastAttempt'][cycle]=stamp

 phase=current.get('phase');job=current.get('job');cycle=current.get('cycle')
 if phase and job and cycle:
  run=reader(root,job)
  if run and run.get('running'):
   pass
  elif run and run.get('startedAt')==current.get('jobStartedAt') and _success(job,run):
   if phase=='campaign_collect':
    try:launch('campaign','linksCampaign','campaign_links')
    except ValueError as error:current.update(phase=None,cycle=None,job=None,jobStartedAt=None,retryAt=stamp+3600,error=str(error))
   else:
    current['lastSuccess'][cycle]=stamp;current.update(phase=None,cycle=None,job=None,jobStartedAt=None,retryAt=0)
  else:
   current.update(phase=None,cycle=None,job=None,jobStartedAt=None,retryAt=stamp+3600,error='maintenance_job_incomplete')
 else:
  for cycle_name in ('campaign','selected'):
   job_id=CYCLES[cycle_name]['job'];setting=config['jobs'][job_id]
   if not setting['enabled']:continue
   due=next_due(setting,cycle_name,current['lastSuccess'].get(cycle_name),stamp)
   if due>stamp or float(current.get('retryAt') or 0)>stamp:continue
   launch(cycle_name,'campaignCollect' if cycle_name=='campaign' else 'links',
          'campaign_collect' if cycle_name=='campaign' else 'selected_links');break
  
 current['nextDue']={name:next_due(config['jobs'][spec['job']],name,current['lastSuccess'].get(name),stamp)
                     for name,spec in CYCLES.items() if config['jobs'][spec['job']]['enabled']}
 _write(status_path(root),current);return current

def start_scheduler(root,*,clock=time.time,spawn=subprocess.Popen):
 root=Path(root);state=scheduler_state(root)
 if state['running']:raise ValueError('scheduler_already_running')
 try:stop_path(root).unlink()
 except OSError:pass
 log=root/'var/material-maintenance.log';log.parent.mkdir(parents=True,exist_ok=True)
 with log.open('a',encoding='utf-8') as handle:
  child=spawn([str(root/'.venv/bin/python'),str(root/'scripts/material-maintenance.py'),'--worker'],
              cwd=str(root),stdin=subprocess.DEVNULL,stdout=handle,stderr=subprocess.STDOUT,
              start_new_session=True,env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'})
 _write(run_path(root),{'pid':child.pid,'startedAt':clock(),'log':str(log)})
 return scheduler_state(root)

def stop_scheduler(root):
 state=scheduler_state(root)
 if not state['running']:raise ValueError('scheduler_not_running')
 _write(stop_path(root),{'requestedAt':time.time()});return scheduler_state(root)
