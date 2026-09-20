#!/usr/bin/env python3
"""Local explicit sync command. Owns only the new reader process; no platform writes."""
import fcntl,hashlib,json,os,re,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.dont_write_bytecode=True;sys.path.insert(0,str(ROOT/'scripts'))
from lib.global_source import GlobalSources

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
    current=s.db.execute("SELECT id,state,scope FROM global_source_run WHERE state='collecting' ORDER BY created LIMIT 1").fetchone()
    if busy:
     result={'state':'already_running','runId':current['id'] if current else None,'executionAllowed':False}
    else:
     if current:run_id=current['id'];by_category=json.loads(current['scope']).get('partitionMode')=='category_l1_v1'
     else:
      run_id='it-global-'+time.strftime('%Y%m%d')+'-'+hashlib.sha256(request['requestId'].encode()).hexdigest()[:20]
      by_category=True
     fcntl.flock(worker_lock,fcntl.LOCK_UN)
     with (ROOT/'var/global-source-worker.log').open('a') as log:
      command=[str(ROOT/'.venv/bin/python'),str(ROOT/'scripts/collect-global-opportunity.py'),'--run-id',run_id,'--pages','40','--worker']
      if by_category:command.append('--by-category')
      child=subprocess.Popen(command,cwd=ROOT,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True,env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'})
     (ROOT/'var/global-source-worker.pid').write_text(str(child.pid));result={'state':'reader_started','runId':run_id,'executionAllowed':False}
    payload=json.dumps(result);s.db.execute('INSERT INTO global_source_control_request VALUES(?,?)',(request['requestId'],payload));print(payload)
  finally:s.close()
if __name__=='__main__':
 try:main()
 except Exception:print(json.dumps({'error':'source_sync_unavailable'}));sys.exit(1)
