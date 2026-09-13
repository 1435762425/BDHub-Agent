#!/usr/bin/env python3
"""Durable user-authorized Italy second outreach, one verified card/text group at a time."""
import argparse,json,sqlite3,sys,time,subprocess,fcntl,signal
from contextlib import closing
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.dont_write_bytecode=True;sys.path.insert(0,str(ROOT/'scripts'))
from lib.second_cycle import CycleStore,encoded
from lib.cycle_review import choose_candidates
STOP=False
BULK_STORE=None;BULK_ID=None

def stop(*_):
 global STOP
 STOP=True

def invoke(cmd):
 stage='cohort' if cmd[1].endswith('cycle-send-cohort.py') else cmd[2];start=time.monotonic();code=-1
 BULK_STORE.db.execute('INSERT OR REPLACE INTO cycle_bulk_runtime VALUES(?,?,?,?)',(BULK_ID,__import__('os').getpid(),time.time(),stage))
 try:
  result=subprocess.run(cmd,cwd=ROOT,capture_output=True,text=True,timeout=120 if stage=='cohort' else 65);code=result.returncode;return result
 finally:
  BULK_STORE.db.execute('INSERT INTO cycle_bulk_timing(batch_id,stage,seconds,exit_code,at) VALUES(?,?,?,?,?)',(BULK_ID,stage,time.monotonic()-start,code,time.time()))

def main():
 global BULK_STORE,BULK_ID
 p=argparse.ArgumentParser();p.add_argument('--batch-id',required=True);p.add_argument('--target',type=int,default=100);p.add_argument('--once',action='store_true');p.add_argument('--lanes',type=int,choices=(1,2,4),default=1);a=p.parse_args()
 if not a.batch_id.replace('-','').isalnum() or not 1<=a.target<=500:p.error('invalid scope')
 signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
 out=ROOT/'var/bulk-second';out.mkdir(exist_ok=True)
 with (out/'worker.lock').open('a') as lock,CycleStore(ROOT/'var/second-cycle.sqlite') as s:
  fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  s.db.executescript("""CREATE TABLE IF NOT EXISTS cycle_bulk(id TEXT PRIMARY KEY,plan_id TEXT NOT NULL,target INTEGER NOT NULL,authorization TEXT NOT NULL,state TEXT NOT NULL,created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS cycle_bulk_item(batch_id TEXT NOT NULL,creator_id TEXT NOT NULL,handle TEXT NOT NULL,state TEXT NOT NULL,delivery_id TEXT,reason TEXT,retry_at REAL NOT NULL DEFAULT 0,PRIMARY KEY(batch_id,creator_id));""")
  if 'retry_count' not in {r[1] for r in s.db.execute('PRAGMA table_info(cycle_bulk_item)')}:s.db.execute('ALTER TABLE cycle_bulk_item ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0')
  s.db.executescript("CREATE TABLE IF NOT EXISTS cycle_bulk_runtime(batch_id TEXT PRIMARY KEY,pid INTEGER,seen REAL,phase TEXT); CREATE TABLE IF NOT EXISTS cycle_bulk_timing(id INTEGER PRIMARY KEY AUTOINCREMENT,batch_id TEXT,stage TEXT,seconds REAL,exit_code INTEGER,at REAL);")
  BULK_STORE=s;BULK_ID=a.batch_id
  plan=s.db.execute("SELECT id FROM plan WHERE institution='bjn-local-research' AND market='it'").fetchone()[0]
  auth={'source':'current_user_request','scope':'Italy second outreach using eligible PID leads, fixed v4 templates, verified cards; do not replay confirmed groups','requested':'先大批量的把二发发一下','maxPeople':a.target,'institutionNewContactRollingCap':500}
  prior=s.db.execute('SELECT * FROM cycle_bulk WHERE id=?',(a.batch_id,)).fetchone()
  if prior and (prior['target']!=a.target or prior['authorization']!=encoded(auth)):raise SystemExit('bulk_scope_conflict')
  s.db.execute("INSERT OR IGNORE INTO cycle_bulk VALUES(?,?,?,?,'running',?)",(a.batch_id,plan,a.target,encoded(auth),time.time()))
  while not STOP:
   s.db.execute('INSERT OR REPLACE INTO cycle_bulk_runtime VALUES(?,?,?,?)',(a.batch_id,__import__('os').getpid(),time.time(),'planning'))
   state=s.db.execute('SELECT state FROM cycle_bulk WHERE id=?',(a.batch_id,)).fetchone()[0]
   if state not in ('running','waiting_supply') or s._plan(plan)['state']!='active':break
   unknown=s.db.execute("SELECT 1 FROM cycle_delivery WHERE plan_id=? AND state='unknown'",(plan,)).fetchone()
   if unknown:s.db.execute("UPDATE cycle_bulk SET state='waiting_reconciliation' WHERE id=?",(a.batch_id,));break
   done=s.db.execute("SELECT count(*) FROM cycle_bulk_item WHERE batch_id=? AND state IN ('confirmed','contacted_inquiry')",(a.batch_id,)).fetchone()[0]
   if done>=a.target:s.db.execute("UPDATE cycle_bulk SET state='completed' WHERE id=?",(a.batch_id,));break
   with closing(sqlite3.connect((ROOT/'var/creator-identities.sqlite').as_uri()+'?mode=ro',uri=True)) as ids, ids:
    def person(c,o):
     r=ids.execute("SELECT current_handle FROM creator_identity WHERE market='it' AND creator_id=? AND oec_id=? AND handle_conflict=0",(c,o)).fetchone();return {'handle':r[0]} if r else None
    candidates,_=choose_candidates(s,plan,person,100)
   for c in candidates:
    s.db.execute("INSERT OR IGNORE INTO cycle_bulk_item(batch_id,creator_id,handle,state) VALUES(?,?,?,'pending')",(a.batch_id,c['creatorId'],c['handle']))
   item=s.db.execute("SELECT * FROM cycle_bulk_item WHERE batch_id=? AND state IN ('pending','preparing','sending') AND retry_at<=? ORDER BY CASE state WHEN 'sending' THEN 0 WHEN 'preparing' THEN 1 ELSE 2 END,rowid LIMIT 1",(a.batch_id,time.time())).fetchone()
   if not item:
    s.db.execute("UPDATE cycle_bulk SET state='waiting_supply' WHERE id=?",(a.batch_id,));s.db.execute("UPDATE cycle_bulk_runtime SET phase='waiting_supply' WHERE batch_id=?",(a.batch_id,))
    if a.once:break
    time.sleep(10);continue
   s.db.execute("UPDATE cycle_bulk SET state='running' WHERE id=?",(a.batch_id,))
   if a.lanes>1:
    try:
     r=invoke([sys.executable,str(ROOT/'scripts/cycle-send-cohort.py'),'--batch-id',a.batch_id,'--lanes',str(a.lanes),'--limit','8'])
     result=json.loads(r.stdout.strip().splitlines()[-1]) if r.stdout.strip() else {'error':'cohort_failed'}
     print(json.dumps(result),flush=True)
     if r.returncode:time.sleep(3)
    except subprocess.TimeoutExpired:
     # Child kill leaves persistent parts intact; next iteration recovers before new writes.
     print(json.dumps({'error':'cohort_timeout_reconcile_before_send'}),flush=True)
    if a.once:break
    time.sleep(1)  # Release the account/cycle locks so inbound and reply workers can run.
    continue
   try:
    did=item['delivery_id']
    if not did:
     # Recover a preparation written just before process loss, rather than prepare another intent.
     found=s.db.execute("SELECT id FROM cycle_delivery WHERE plan_id=? AND creator_id=? AND state IN ('ready','running','unknown') ORDER BY created DESC LIMIT 1",(plan,item['creator_id'])).fetchone()
     did=found[0] if found else None
    if not did:
     s.db.execute("UPDATE cycle_bulk_item SET state='preparing' WHERE batch_id=? AND creator_id=?",(a.batch_id,item['creator_id']))
     cmd=[sys.executable,str(ROOT/'scripts/cycle-send.py'),'prepare','--handle',item['handle']]
     r=invoke(cmd)
     if r.returncode:raise RuntimeError(json.loads(r.stdout.strip().splitlines()[-1]).get('error','cycle_send_failed') if r.stdout.strip() else 'cycle_send_failed')
     did=json.loads(r.stdout.strip().splitlines()[-1])['deliveryId']
    s.db.execute("UPDATE cycle_bulk_item SET delivery_id=?,state='sending' WHERE batch_id=? AND creator_id=?",(did,a.batch_id,item['creator_id']))
    proposal=json.loads((ROOT/'var/cycle-send'/f'{did}.json').read_text())
    r=invoke([sys.executable,str(ROOT/'scripts/cycle-send.py'),'run','--delivery-id',did,'--approved-hash',proposal['snapshotHash']])
    delivery=s.db.execute('SELECT state FROM cycle_delivery WHERE id=?',(did,)).fetchone()[0]
    if delivery=='confirmed':
     s.db.execute("UPDATE cycle_bulk_item SET state='confirmed',reason=NULL WHERE batch_id=? AND creator_id=?",(a.batch_id,item['creator_id']));print(json.dumps({'confirmed':done+1,'target':a.target}),flush=True)
    elif delivery=='unknown':s.db.execute("UPDATE cycle_bulk SET state='waiting_reconciliation' WHERE id=?",(a.batch_id,));break
    elif s.db.execute("SELECT 1 FROM cycle_platform_signal WHERE delivery_id=? AND outcome='rejected'",(did,)).fetchone():
     s.db.execute("UPDATE cycle_bulk_item SET state='platform_rejected',reason='native_rejection_recorded' WHERE batch_id=? AND creator_id=?",(a.batch_id,item['creator_id']));s.db.execute("UPDATE cycle_bulk SET state='platform_rejected' WHERE id=?",(a.batch_id,));break
    elif r.returncode:raise RuntimeError(json.loads(r.stdout.strip().splitlines()[-1]).get('error','cycle_send_failed') if r.stdout.strip() else 'cycle_send_failed')
   except Exception as e:
    raw=str(e)
    if raw=='new_contact_capacity_reached':
     s.db.execute("UPDATE cycle_bulk SET state='local_capacity_reached' WHERE id=?",(a.batch_id,));break
    if did and raw in ('conversation_needs_content_review','relationship_changed'):
     from lib.cycle_delivery import Deliveries
     if Deliveries(s).interrupted_by_inquiry(did):
      s.db.execute("UPDATE cycle_bulk_item SET state='contacted_inquiry',reason=NULL WHERE batch_id=? AND creator_id=?",(a.batch_id,item['creator_id']));continue
    busy=any(k in raw for k in ('live_guard_busy','BlockingIOError','Resource temporarily unavailable'))
    import re
    transient=any(k in raw for k in ('ReadTimeout','ConnectTimeout','TimeoutError')) and item['retry_count']<3
    retry=busy or transient
    reason='account_busy' if busy else raw if re.fullmatch(r'[A-Za-z0-9_]{1,100}',raw) else 'eligibility_or_execution_needs_review'
    s.db.execute('UPDATE cycle_bulk_item SET state=?,reason=?,retry_at=? WHERE batch_id=? AND creator_id=?',('sending' if did and retry else 'pending' if retry else 'needs_review',reason,time.time()+30 if retry else 0,a.batch_id,item['creator_id']))
    if transient:s.db.execute('UPDATE cycle_bulk_item SET retry_count=retry_count+1 WHERE batch_id=? AND creator_id=?',(a.batch_id,item['creator_id']))
    # Detailed local trace is retained outside product UI; never print it to task output.
    (out/(item['creator_id']+'.error.txt')).write_text(raw)
   if a.once:break
   time.sleep(2)
  counts=dict(s.db.execute('SELECT state,count(*) FROM cycle_bulk_item WHERE batch_id=? GROUP BY state',(a.batch_id,)))
  print(json.dumps({'batchId':a.batch_id,'counts':counts,'state':s.db.execute('SELECT state FROM cycle_bulk WHERE id=?',(a.batch_id,)).fetchone()[0]}),flush=True)
if __name__=='__main__':main()
