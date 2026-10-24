"""Lifecycle and original-intent recovery helpers for market send workers."""
import fcntl,json,os,subprocess,time
from pathlib import Path
from lib.runtime_files import atomic_json,launch_guard

def state_path(root,market):return Path(root)/f'var/market-send-worker-{market}.json'

def has_pending_reconciliation(store,market):
 """Only submitted original intents keep a stopped sender alive for readback."""
 modes=('continuous-v1',) if market=='it' else ('market-first-v1','market-canary-v1','market-continuous-v1')
 schema=[tuple(row) for row in store.db.execute("SELECT type,name FROM sqlite_master WHERE type IN ('table','index')")]
 tables={name for kind,name in schema if kind=='table'}
 if not {'plan','cycle_delivery','cycle_delivery_part'}<=tables:return False
 # Large historical ledgers otherwise favor a full plan index over the existing tiny active set.
 active_index=' INDEXED BY cycle_one_active_delivery' if ('index','cycle_one_active_delivery') in schema else ''
 conversation="OR EXISTS(SELECT 1 FROM cycle_conversation_intent i WHERE i.delivery_id=d.id AND i.state IN ('inflight','received'))" if 'cycle_conversation_intent' in tables else ''
 return store.db.execute(f"""SELECT 1 FROM cycle_delivery d{active_index} JOIN plan p ON p.id=d.plan_id
   WHERE p.institution='bjn-local-research' AND p.market=? AND d.state IN ('ready','running','unknown')
     AND json_extract(d.snapshot,'$.executionMode') IN (SELECT value FROM json_each(?))
     AND (d.state='unknown' OR EXISTS(SELECT 1 FROM cycle_delivery_part c WHERE c.delivery_id=d.id
       AND c.state IN ('inflight','accepted','unknown')) {conversation}) LIMIT 1""",
   (market,json.dumps(modes))).fetchone() is not None

def _launch_guard(root,market):
 return launch_guard(Path(root)/f'var/market-send-launch-{market}.lock')

def _publish(root,market,value):
 atomic_json(state_path(root,market),value)

def write_state(root,market,value):
 # A fast child cannot publish ready before its launcher later overwrites it as starting.
 with _launch_guard(root,market):_publish(root,market,value)

def state(root,market):
 try:value=json.loads(state_path(root,market).read_text(encoding='utf-8'))
 except (OSError,ValueError):return {'running':False,'state':'off','pid':None}
 from lib.process_liveness import record_alive
 alive=record_alive(value)
 return value|{'running':alive and value.get('running') is True}
def launch(root,market):
 root=Path(root)
 with _launch_guard(root,market):
  current=state(root,market)
  if current['running']:return current
  # A stale/missing status record does not grant a second process permission to launch.
  with (root/f'var/market-send-worker-{market}.lock').open('a') as worker_lock:
   try:fcntl.flock(worker_lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
   except BlockingIOError:return current|{'running':True,'reason':'worker_lock_busy'}
   fcntl.flock(worker_lock,fcntl.LOCK_UN)
  log=root/f'var/market-send-worker-{market}.log'
  with log.open('a',encoding='utf-8') as handle:
   child=subprocess.Popen([str(root/'.venv/bin/python'),str(root/'scripts/market-send-worker.py'),'--market',market],cwd=str(root),stdin=subprocess.DEVNULL,stdout=handle,stderr=subprocess.STDOUT,start_new_session=True,env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'})
  value={'running':True,'state':'starting','pid':child.pid,'startedAt':time.time()}
  _publish(root,market,value)
  return value
