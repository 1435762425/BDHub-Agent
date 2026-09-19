#!/usr/bin/env python3
"""Local explicit sync command. Owns only the new reader process; no platform writes."""
import fcntl,hashlib,json,os,re,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.dont_write_bytecode=True;sys.path.insert(0,str(ROOT/'scripts'))
from lib.global_source import GlobalSources
from lib.market_accounts import catalog_read_account,catalog_scope

def main():
 request=json.loads(sys.stdin.read(2049))
 if not isinstance(request,dict) or set(request)!={'action','requestId'} or request['action']!='sync' or not isinstance(request['requestId'],str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',request['requestId']):raise ValueError('invalid_sync_request')
 with (ROOT/'var/global-source-control.lock').open('a') as control:
  fcntl.flock(control,fcntl.LOCK_EX)
  s=GlobalSources(ROOT/'var/global-source.sqlite')
  try:
   s.db.execute('CREATE TABLE IF NOT EXISTS global_source_control_request(request_id TEXT PRIMARY KEY,payload TEXT NOT NULL)')
   old=s.db.execute('SELECT payload FROM global_source_control_request WHERE request_id=?',(request['requestId'],)).fetchone()
   if old:print(old[0]);return
   with (ROOT/'var/global-source-worker.lock').open('a') as worker_lock:
    try:fcntl.flock(worker_lock,fcntl.LOCK_EX|fcntl.LOCK_NB);busy=False
    except BlockingIOError:busy=True
    current=s.db.execute("SELECT id,state FROM global_source_run WHERE state='collecting' ORDER BY created LIMIT 1").fetchone()
    if busy:
     result={'state':'already_running','runId':current['id'] if current else None,'executionAllowed':False}
    else:
     if current:run_id=current['id']
     else:
      run_id='it-global-'+time.strftime('%Y%m%d')+'-'+hashlib.sha256(request['requestId'].encode()).hexdigest()[:20]
      scope=catalog_scope(ROOT,catalog_read_account(ROOT));s.start(run_id,scope)
     fcntl.flock(worker_lock,fcntl.LOCK_UN)
     with (ROOT/'var/global-source-worker.log').open('a') as log:
      child=subprocess.Popen([str(ROOT/'.venv/bin/python'),str(ROOT/'scripts/collect-global-opportunity.py'),'--run-id',run_id,'--pages','15','--worker'],cwd=ROOT,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True,env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'})
     (ROOT/'var/global-source-worker.pid').write_text(str(child.pid));result={'state':'reader_started','runId':run_id,'executionAllowed':False}
    payload=json.dumps(result);s.db.execute('INSERT INTO global_source_control_request VALUES(?,?)',(request['requestId'],payload));print(payload)
  finally:s.close()
if __name__=='__main__':
 try:main()
 except Exception:print(json.dumps({'error':'source_sync_unavailable'}));sys.exit(1)
