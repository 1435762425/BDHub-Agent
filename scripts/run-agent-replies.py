#!/usr/bin/env python3
"""Scheduled versioned Agent replies; no work outside the reply window."""
import argparse,fcntl,importlib.util,json,re,signal,sys,time
from datetime import datetime,timezone,timedelta
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.dont_write_bytecode=True;sys.path.insert(0,str(ROOT/'scripts'))
from lib.cycle_auto_reply import AutoReplies
from lib.reply_events import backfill
from lib.agent_reply_v2 import apply_production,generate,production_context,rollout_stage
from lib.second_cycle import CycleError,CycleStore,digest
from lib.template_library import agent_setting
STOP=False;BEIJING=timezone(timedelta(hours=8))
REQUEST_ID=re.compile(r'[A-Za-z0-9][A-Za-z0-9._:-]{7,119}')
def stop(*_):
 global STOP;STOP=True
def inside(setting,stamp):
 now=datetime.fromtimestamp(stamp,BEIJING);minutes=now.hour*60+now.minute
 def m(v):h,n=map(int,v.split(':'));return h*60+n
 return m(setting['replyStart'])<=minutes<m(setting['replyEnd'])
def authorized_request(value):
 if value is None:return None
 if not isinstance(value,str) or not REQUEST_ID.fullmatch(value):raise CycleError('agent_reply_authorization_invalid')
 return value
def send_dispatch_active(store):
 active=store.db.execute("SELECT 1 FROM cycle_delivery_part WHERE state IN ('inflight','accepted') LIMIT 1").fetchone() if store.db.execute("SELECT 1 FROM sqlite_master WHERE name='cycle_delivery_part'").fetchone() else None
 if active:return True
 if not store.db.execute("SELECT 1 FROM sqlite_master WHERE name='cycle_bulk_runtime'").fetchone():return False
 row=store.db.execute("SELECT f.state,r.phase FROM cycle_bulk_freeze f JOIN cycle_bulk_runtime r ON r.batch_id=f.batch_id WHERE f.state IN ('starting','running','stop_requested') ORDER BY f.created_at DESC LIMIT 1").fetchone()
 return bool(row and row['phase']=='cohort')
def pending_rows(store,plan,now):
 return store.db.execute("""SELECT p.* FROM inbox_pending p JOIN relationship r
   ON r.plan_id=p.plan_id AND r.creator_id=p.creator_id
   WHERE p.plan_id=? AND p.state IN ('awaiting_content','awaiting_classification','review_partial','template_ready','policy_review','facts_ready_for_review','needs_facts')
   AND p.due_at<=? AND r.mode='auto' AND r.rejected=0
   AND NOT EXISTS(SELECT 1 FROM service_case c WHERE c.plan_id=p.plan_id AND c.creator_id=p.creator_id AND c.state='open')
   ORDER BY p.due_at LIMIT 20""",(plan,now)).fetchall()
def tick(authorized_now=None,market="it"):
 authorized_now=authorized_request(authorized_now)
 with CycleStore(ROOT/'var/second-cycle.sqlite') as store:
  plan_row=store.db.execute("SELECT id FROM plan WHERE market=? AND institution='bjn-local-research'",(market,)).fetchone()
  if not plan_row:raise CycleError('plan_missing')
  plan=plan_row[0];setting=agent_setting(store,plan);now=store.clock()
  if market!='it':return {'state':'market_agent_transport_pending','market':market,'platformWrites':0,'realSends':0}
  if not setting['enabled']:return {'state':'disabled','platformWrites':0,'realSends':0}
  stage=rollout_stage(store,plan,market)
  if send_dispatch_active(store):return {'state':'send_dispatch_active','platformWrites':0,'realSends':0}
  if stage in ('pilot_required','pilot_complete'):
   unresolved=store.db.execute("SELECT id FROM service_reply WHERE plan_id=? AND state IN ('inflight','accepted','unknown') ORDER BY created LIMIT 1",(plan,)).fetchone()
   if unresolved:
    replies=AutoReplies(store)
    spec=importlib.util.spec_from_file_location('agent_reply_runtime',ROOT/'scripts/run-auto-replies.py')
    runtime=importlib.util.module_from_spec(spec);spec.loader.exec_module(runtime)
    state=runtime.run_reply(store,replies,replies.get(unresolved[0]))
    return {'state':'original_intent_rechecked','replyState':state,'platformWrites':0,'realSends':0}
   return {'state':'first_send_requires_page_start' if stage=='pilot_required' else 'pilot_complete_waiting_resume',
           'platformWrites':0,'realSends':0}
  if not inside(setting,now) and authorized_now is None:return {'state':'outside_reply_window','platformWrites':0,'realSends':0}
  projection=backfill(store);replies=AutoReplies(store);run_id='agent-run-'+digest([plan,int(now),setting['revision'],authorized_now])[:24]
  report={'runId':run_id,'state':'running','triggerSource':'user_authorized_now' if authorized_now else 'reply_window',
   'authorizationRequestId':authorized_now,'claimed':0,'noReply':0,'prepared':0,'human':0,'confirmed':0,'unknown':0,'platformWrites':0,'realSends':0}
  store.db.execute("INSERT OR IGNORE INTO agent_reply_run VALUES(?,?,\'running\',?,NULL,0,0,0,0,0,0,NULL)",(run_id,plan,now))
  outstanding=store.db.execute("SELECT 1 FROM service_reply WHERE plan_id=? AND kind IN ('agent_generated_v2','agent_request_detail_v2','agent_handoff_v2') AND state IN ('ready','inflight','accepted','unknown') LIMIT 1",(plan,)).fetchone()
  candidates=[] if stage=='pilot_running' and outstanding else pending_rows(store,plan,now)
  if stage=='pilot_running':candidates=candidates[:1]
  for pending in candidates:
   turn=store.db.execute('SELECT * FROM inbound_turn WHERE plan_id=? AND creator_id=? AND historical=0 ORDER BY coalesce(occurred_ms,observed_at*1000) DESC,message_id DESC LIMIT 1',(plan,pending['creator_id'])).fetchone()
   if not turn:continue
   context=production_context(ROOT,store,plan,market,turn['turn_id'])
   generated=generate(ROOT,store,plan,market,context,mode='production')
   applied=apply_production(store,plan,context,generated)
   report['claimed']+=1
   report['noReply']+=int(applied['route']=='no_reply')
   report['human']+=int(applied['route']=='handoff')
   report['prepared']+=int(applied['replyId'] is not None)
  q=store.db.execute("SELECT id FROM service_reply WHERE plan_id=? AND state IN ('ready','inflight','accepted','unknown') AND (kind IN ('agent_generated_v2','agent_request_detail_v2','agent_handoff_v2') OR state IN ('inflight','accepted','unknown')) ORDER BY CASE state WHEN 'unknown' THEN 0 WHEN 'inflight' THEN 1 ELSE 2 END,created LIMIT 1",(plan,)).fetchone()
  if q:
   spec=importlib.util.spec_from_file_location('agent_reply_runtime',ROOT/'scripts/run-auto-replies.py');runtime=importlib.util.module_from_spec(spec);spec.loader.exec_module(runtime)
   queued=replies.get(q[0]);new_dispatch=queued['state']=='ready'
   state=runtime.run_reply(store,replies,queued);report['realSends']=int(new_dispatch);report['platformWrites']=int(new_dispatch);report['confirmed']+=int(state=='confirmed');report['unknown']+=int(state=='unknown')
  report['state']='completed';store.db.execute("UPDATE agent_reply_run SET state='completed',finished_at=?,claimed=?,no_reply=?,prepared=?,human=?,confirmed=?,unknown=? WHERE run_id=?",(store.clock(),report['claimed'],report['noReply'],report['prepared'],report['human'],report['confirmed'],report['unknown'],run_id));return report|{'replyProjection':projection}
def main():
 p=argparse.ArgumentParser();p.add_argument('--worker',action='store_true');p.add_argument('--interval',type=int,default=60);p.add_argument('--stop',type=Path);p.add_argument('--authorized-now');p.add_argument('--market',default='it');a=p.parse_args();signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
 if a.worker and a.authorized_now:p.error('--authorized-now cannot be used with --worker')
 with (ROOT/'var/agent-reply-worker.lock').open('a') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  while not STOP and not (a.stop and a.stop.exists()):
   try:result=tick(a.authorized_now,a.market)
   except Exception as e:result={'state':'failed','error':str(e) if isinstance(e,CycleError) else type(e).__name__,'platformWrites':0,'realSends':0}
   (ROOT/'var/agent-reply-status.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');print(json.dumps(result,ensure_ascii=False),flush=True)
   if not a.worker:break
   a.authorized_now=None
   for _ in range(max(30,a.interval)):
    if STOP or (a.stop and a.stop.exists()):break
    time.sleep(1)
if __name__=='__main__':raise SystemExit(main())
