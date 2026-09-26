"""Persistent workflow stage claims with resource slots, leases and fencing."""
from __future__ import annotations

import os,re,time
from lib.second_cycle import CycleError

OWNER=re.compile(r'[A-Za-z0-9][A-Za-z0-9._:-]{7,119}')

def _required(store):
 tables={row[0] for row in store.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
 if not {'workflow_stage_claim','workflow_resource_slot','workflow_claim_sequence'}<=tables:raise CycleError('workflow_resource_migration_required')

def claim(store,stage_run_id,owner_id,resources,*,lease_seconds=60,worker_pid=None,input_generation_id=None):
 _required(store)
 if not isinstance(stage_run_id,str) or not isinstance(owner_id,str) or not OWNER.fullmatch(owner_id) or not isinstance(resources,(list,tuple)) or not resources or type(lease_seconds) not in (int,float) or not 5<=lease_seconds<=3600 or input_generation_id is not None and not isinstance(input_generation_id,str):raise CycleError('workflow_resource_claim_invalid')
 normalized=[]
 for key,slots in resources:
  if not isinstance(key,str) or not key or len(key)>120 or type(slots) is not int or not 1<=slots<=32:raise CycleError('workflow_resource_claim_invalid')
  normalized.append((key,slots))
 if len({key for key,_ in normalized})!=len(normalized):raise CycleError('workflow_resource_claim_invalid')
 now=store.clock();pid=os.getpid() if worker_pid is None else worker_pid
 with store.tx():
  existing=store.db.execute('SELECT * FROM workflow_stage_claim WHERE stage_run_id=?',(stage_run_id,)).fetchone()
  if existing:
   if existing['owner_id']!=owner_id:raise CycleError('workflow_stage_already_claimed')
   return {'stageRunId':stage_run_id,'ownerId':owner_id,'fence':existing['fence'],'leaseUntil':existing['lease_until'],'duplicate':True}
  stage=store.db.execute("SELECT run_id,state FROM workflow_stage_run WHERE stage_run_id=?",(stage_run_id,)).fetchone()
  if not stage or stage['state']!='queued':raise CycleError('workflow_stage_not_claimable')
  run=store.db.execute('SELECT state FROM workflow_run WHERE run_id=?',(stage['run_id'],)).fetchone()
  if not run or run['state'] not in ('queued','running'):raise CycleError('workflow_run_not_claimable')
  allocations=[]
  for key,slots in normalized:
   # An expired lease remains occupied until recovery confirms the owner is dead.
   taken={row[0] for row in store.db.execute('SELECT slot_no FROM workflow_resource_slot WHERE resource_key=?',(key,))}
   slot=next((number for number in range(slots) if number not in taken),None)
   if slot is None:raise CycleError('workflow_resource_busy')
   allocations.append((key,slot))
  store.db.execute('INSERT INTO workflow_claim_sequence(created_at) VALUES(?)',(now,));fence=store.db.execute('SELECT last_insert_rowid()').fetchone()[0];lease=now+lease_seconds
  store.db.execute('INSERT INTO workflow_stage_claim VALUES(?,?,?,?,?,?,?)',(stage_run_id,owner_id,fence,lease,now,pid,now))
  for key,slot in allocations:store.db.execute('INSERT INTO workflow_resource_slot VALUES(?,?,?,?,?,?)',(key,slot,stage_run_id,fence,lease,now))
  store.db.execute("UPDATE workflow_stage_run SET state='running',started_at=coalesce(started_at,?),input_generation_id=?,error_code=NULL WHERE stage_run_id=? AND state='queued'",(now,input_generation_id,stage_run_id))
  if store.db.execute('SELECT changes()').fetchone()[0]!=1:raise CycleError('workflow_stage_claim_race')
  store.db.execute("UPDATE workflow_run SET state='running' WHERE run_id=? AND state='queued'",(stage['run_id'],))
 return {'stageRunId':stage_run_id,'ownerId':owner_id,'fence':fence,'leaseUntil':lease,'resources':[{'key':key,'slot':slot} for key,slot in allocations],'duplicate':False}

def heartbeat(store,stage_run_id,owner_id,fence,*,lease_seconds=60):
 _required(store);now=store.clock();lease=now+lease_seconds
 with store.tx():
  store.db.execute('UPDATE workflow_stage_claim SET lease_until=?,heartbeat_at=? WHERE stage_run_id=? AND owner_id=? AND fence=?',(lease,now,stage_run_id,owner_id,fence))
  if store.db.execute('SELECT changes()').fetchone()[0]!=1:raise CycleError('workflow_stage_fence_stale')
  store.db.execute('UPDATE workflow_resource_slot SET lease_until=?,heartbeat_at=? WHERE owner_stage_run_id=? AND fence=?',(lease,now,stage_run_id,fence))
 return lease

def assert_current(store,stage_run_id,owner_id,fence):
 _required(store);row=store.db.execute('SELECT lease_until FROM workflow_stage_claim WHERE stage_run_id=? AND owner_id=? AND fence=?',(stage_run_id,owner_id,fence)).fetchone()
 if not row or row[0]<store.clock():raise CycleError('workflow_stage_fence_stale')
 return True

def assert_owned(store,stage_run_id,owner_id,fence):
 """Settlement check: this owner's fence still holds the claim, even if its lease has lapsed.

 A lapsed lease keeps its claim and slots until recover_expired removes them (only once the recorded
 worker is dead), so an unrecovered fence proves nobody else has started this stage."""
 _required(store)
 if not store.db.execute('SELECT 1 FROM workflow_stage_claim WHERE stage_run_id=? AND owner_id=? AND fence=?',(stage_run_id,owner_id,fence)).fetchone():
  raise CycleError('workflow_stage_fence_stale')
 return True

LATE_SQL=('CREATE TABLE IF NOT EXISTS workflow_late_result(stage_run_id TEXT NOT NULL,fence INTEGER NOT NULL,owner_id TEXT NOT NULL,'
          'result_json TEXT NOT NULL,recorded_at REAL NOT NULL,PRIMARY KEY(stage_run_id,fence))')

def record_late_result(store,stage_run_id,owner_id,fence,result):
 """Keep a result whose claim was already recovered: evidence to reconcile, never a publication."""
 import json
 with store.tx():
  store.db.execute(LATE_SQL)
  store.db.execute('INSERT OR IGNORE INTO workflow_late_result VALUES(?,?,?,?,?)',
                   (stage_run_id,fence,owner_id,json.dumps(result,ensure_ascii=False,sort_keys=True,default=str),store.clock()))

def adopt_worker(store,stage_run_id,owner_id,fence,pid):
 """Point the claim back at the supervising process once a child has exited."""
 with store.tx():
  store.db.execute('UPDATE workflow_stage_claim SET worker_pid=? WHERE stage_run_id=? AND owner_id=? AND fence=?',(pid,stage_run_id,owner_id,fence))

def lend_slot(store,stage_run_id,owner_id,fence,key):
 """Give one held slot back while this stage waits on something that does not need it (H14).

 The claim, its fence and every other slot stay; returns True when a slot was actually lent."""
 _required(store)
 with store.tx():
  if not store.db.execute('SELECT 1 FROM workflow_stage_claim WHERE stage_run_id=? AND owner_id=? AND fence=?',(stage_run_id,owner_id,fence)).fetchone():
   raise CycleError('workflow_stage_fence_stale')
  store.db.execute('DELETE FROM workflow_resource_slot WHERE owner_stage_run_id=? AND fence=? AND resource_key=?',(stage_run_id,fence,key))
  return store.db.execute('SELECT changes()').fetchone()[0]>0

def reclaim_slot(store,stage_run_id,owner_id,fence,key,slots):
 """Take a lent slot back if one is free now; False means it is still occupied by another stage."""
 _required(store)
 with store.tx():
  claim_row=store.db.execute('SELECT lease_until FROM workflow_stage_claim WHERE stage_run_id=? AND owner_id=? AND fence=?',(stage_run_id,owner_id,fence)).fetchone()
  if not claim_row:raise CycleError('workflow_stage_fence_stale')
  if store.db.execute('SELECT 1 FROM workflow_resource_slot WHERE owner_stage_run_id=? AND fence=? AND resource_key=?',(stage_run_id,fence,key)).fetchone():
   return True
  taken={row[0] for row in store.db.execute('SELECT slot_no FROM workflow_resource_slot WHERE resource_key=?',(key,))}
  slot=next((number for number in range(slots) if number not in taken),None)
  if slot is None:return False
  store.db.execute('INSERT INTO workflow_resource_slot VALUES(?,?,?,?,?,?)',(key,slot,stage_run_id,fence,claim_row[0],store.clock()))
  return True

def release(store,stage_run_id,owner_id,fence):
 _required(store)
 with store.tx():
  row=store.db.execute('SELECT 1 FROM workflow_stage_claim WHERE stage_run_id=? AND owner_id=? AND fence=?',(stage_run_id,owner_id,fence)).fetchone()
  if not row:raise CycleError('workflow_stage_fence_stale')
  store.db.execute('DELETE FROM workflow_resource_slot WHERE owner_stage_run_id=? AND fence=?',(stage_run_id,fence))
  store.db.execute('DELETE FROM workflow_stage_claim WHERE stage_run_id=? AND owner_id=? AND fence=?',(stage_run_id,owner_id,fence))

def recover_expired(store,*,pid_alive=None):
 _required(store);now=store.clock();alive=pid_alive or (lambda pid:_pid_alive(pid));recovered=[]
 with store.tx():
  for row in list(store.db.execute('SELECT * FROM workflow_stage_claim WHERE lease_until<? ORDER BY lease_until,stage_run_id',(now,))):
   if row['worker_pid'] and alive(row['worker_pid']):continue
   stage=store.db.execute('SELECT run_id,stage,state,counts_json FROM workflow_stage_run WHERE stage_run_id=?',(row['stage_run_id'],)).fetchone()
   if stage and stage['state']=='running':
    from lib.operations_workflow import WRITE_CAPABLE_STAGES
    if stage['stage'] in WRITE_CAPABLE_STAGES:
     # Its children may have written before the owner died and nothing proves zero: never re-run on its own.
     import json
     counts={**json.loads(stage['counts_json'] or '{}'),'writeEvidence':'uncertain'}
     store.db.execute("UPDATE workflow_stage_run SET state='needs_human',finished_at=?,counts_json=?,error_code='recovery_claim_expired_write_unknown' WHERE stage_run_id=?",
                      (now,json.dumps(counts,sort_keys=True,separators=(',',':')),row['stage_run_id']))
     store.db.execute("UPDATE workflow_run SET state='needs_human',finished_at=?,error_code='recovery_claim_expired_write_unknown' WHERE run_id=? AND state IN ('queued','running','stop_requested')",(now,stage['run_id']))
    else:
     store.db.execute("UPDATE workflow_stage_run SET state='queued',started_at=NULL,error_code='recovery_claim_expired' WHERE stage_run_id=?",(row['stage_run_id'],))
    recovered.append(row['stage_run_id'])
   store.db.execute('DELETE FROM workflow_resource_slot WHERE owner_stage_run_id=? AND fence=?',(row['stage_run_id'],row['fence']))
   store.db.execute('DELETE FROM workflow_stage_claim WHERE stage_run_id=? AND fence=?',(row['stage_run_id'],row['fence']))
  store.db.execute('DELETE FROM workflow_resource_slot WHERE NOT EXISTS (SELECT 1 FROM workflow_stage_claim c WHERE c.stage_run_id=workflow_resource_slot.owner_stage_run_id AND c.fence=workflow_resource_slot.fence)')
 return recovered

def _pid_alive(pid):
 from lib.process_liveness import pid_alive
 return pid_alive(pid)
