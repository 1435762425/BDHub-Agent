#!/usr/bin/env python3
"""Validate 500 real positive-sales leads and publish ACC6/12 only on passing evidence."""
import sys,json,time,os,signal,subprocess,importlib.util,fcntl
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.dont_write_bytecode=True;sys.path.insert(0,str(ROOT/'scripts'))
from lib.batch_task_service import TaskService
from lib.creator_discovery import CreatorDiscoveryStore,CreatorDiscoveryWorker
from lib.identity_acceptance import Acceptance
from lib.discovery_cohort import run_cohort
spec=importlib.util.spec_from_file_location('acceptance_shared',ROOT/'scripts/creator-profile-refresh.py');shared=importlib.util.module_from_spec(spec);spec.loader.exec_module(shared)

def main():
 with TaskServiceContext() as service,CreatorDiscoveryStore(ROOT/'var') as discovery,CreatorDiscoveryWorker(discovery) as worker:
  ledger=Acceptance(service);test=ledger.start(ROOT,discovery);print(json.dumps({'acceptanceId':test['id'],'samples':500,'criteria':test['payload']['criteria']}),flush=True)
  if test['state'] in ('passed','failed'):return
  deadline=time.monotonic()+1800
  while time.monotonic()<deadline:
   result=run_cohort(worker,20,9,soak_id=test['soak_id'],only_batch=test['batch_id'])
   shared.reconcile_cycle(ROOT/'var',discovery)
   metrics=ledger.assess(test['id'],ROOT,discovery);print(json.dumps({'validation':test['id'],'metrics':metrics}),flush=True)
   if metrics['final']:return
   if result and result.get('soakState')=='attention':return
   time.sleep(1 if result and result.get('targets') else 10)
  print(json.dumps({'state':'incomplete','reason':'bounded_validation_time_exceeded'}),flush=True)
class TaskServiceContext:
 def __enter__(self):self.service=TaskService(ROOT/'var/batch-tasks.sqlite');return self.service
 def __exit__(self,*a):self.service.close()
if __name__=='__main__':
 pidfile=ROOT/'var/identity-worker.pid'
 if pidfile.exists():
  try:os.kill(int(pidfile.read_text()),0)
  except ProcessLookupError:pass
  else:raise SystemExit('Stop identity worker before running the acceptance worker; it resumes automatically.')
 lock=(ROOT/'var/identity-release-validation.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 signal.signal(signal.SIGTERM,lambda *_:(_ for _ in ()).throw(KeyboardInterrupt()))
 try:main()
 finally:
  service=TaskService(ROOT/'var/batch-tasks.sqlite')
  rows=service.db.execute("SELECT id FROM identity_soak_run WHERE state='active' ORDER BY started DESC LIMIT 1").fetchall();soak=rows[0][0] if rows else None;service.close()
  args=['worker','--interval','1','--cohort-size','20']+(['--cohort-lanes','9','--soak-run',soak] if soak else [])
  with (ROOT/'var/identity-worker.log').open('a') as log:
   p=subprocess.Popen([sys.executable,str(ROOT/'scripts/creator-profile-refresh.py'),*args],cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,start_new_session=True,env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'})
  pidfile.write_text(str(p.pid));print(json.dumps({'identityWorkerRestored':p.pid}),flush=True)
