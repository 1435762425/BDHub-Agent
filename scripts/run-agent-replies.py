#!/usr/bin/env python3
"""Scheduled Agent replies for reviewed policy actions; no work outside the reply window."""
import argparse,fcntl,importlib.util,json,signal,sys,time
from datetime import datetime,timezone,timedelta
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.dont_write_bytecode=True;sys.path.insert(0,str(ROOT/'scripts'))
from lib.cycle_auto_reply import AutoReplies
from lib.reply_events import DeepSeekClassifier,backfill,classify,load_policy
from lib.second_cycle import CycleError,CycleStore,digest
from lib.template_library import agent_setting,agent_template_map
STOP=False;BEIJING=timezone(timedelta(hours=8))
def stop(*_):
 global STOP;STOP=True
def inside(setting,stamp):
 now=datetime.fromtimestamp(stamp,BEIJING);minutes=now.hour*60+now.minute
 def m(v):h,n=map(int,v.split(':'));return h*60+n
 return m(setting['replyStart'])<=minutes<m(setting['replyEnd'])
def close_no_reply(store,plan,turn,pending):
 with store.tx():
  row=store.db.execute('SELECT rowid FROM inbox_event WHERE plan_id=? AND cid=? AND message_id=?',(plan,turn['cid'],turn['message_id'])).fetchone();watermark=row[0] if row else 0
  store.db.execute("UPDATE inbox_pending SET state='no_reply' WHERE plan_id=? AND creator_id=? AND revision=?",(plan,turn['creator_id'],pending['revision']))
  store.db.execute("UPDATE relationship SET inbox_until=0,revision=revision+1 WHERE plan_id=? AND creator_id=? AND mode='auto'",(plan,turn['creator_id']))
  store.db.execute('INSERT INTO service_cursor VALUES(?,?,?) ON CONFLICT(plan_id,creator_id) DO UPDATE SET event_rowid=max(event_rowid,excluded.event_rowid)',(plan,turn['creator_id'],watermark))
def open_human(store,plan,turn,pending,reason):
 with store.tx():
  old=store.db.execute("SELECT id FROM service_case WHERE plan_id=? AND creator_id=? AND state='open'",(plan,turn['creator_id'])).fetchone()
  case=old[0] if old else 'case-'+digest([plan,turn['creator_id'],pending['revision']])[:24]
  if old:store.db.execute('UPDATE service_case SET assessment_revision=?,reason=?,updated=? WHERE id=?',(pending['revision'],reason or 'agent_handoff',store.clock(),case))
  else:store.db.execute("INSERT INTO service_case VALUES(?,?,?,'open',?,?,?,?, 'not_sent')",(case,plan,turn['creator_id'],pending['revision'],reason or 'agent_handoff',store.clock(),store.clock()))
  store.db.execute('INSERT OR IGNORE INTO service_case_turn VALUES(?,?)',(case,turn['turn_id']))
  store.db.execute("UPDATE inbox_pending SET state='human' WHERE plan_id=? AND creator_id=?",(plan,turn['creator_id']))
  store.db.execute("UPDATE relationship SET mode='human',revision=revision+1 WHERE plan_id=? AND creator_id=?",(plan,turn['creator_id']))
def send_dispatch_active(store):
 active=store.db.execute("SELECT 1 FROM cycle_delivery_part WHERE state IN ('inflight','accepted') LIMIT 1").fetchone() if store.db.execute("SELECT 1 FROM sqlite_master WHERE name='cycle_delivery_part'").fetchone() else None
 if active:return True
 if not store.db.execute("SELECT 1 FROM sqlite_master WHERE name='cycle_bulk_runtime'").fetchone():return False
 row=store.db.execute("SELECT f.state,r.phase FROM cycle_bulk_freeze f JOIN cycle_bulk_runtime r ON r.batch_id=f.batch_id WHERE f.state IN ('starting','running','stop_requested') ORDER BY f.created_at DESC LIMIT 1").fetchone()
 return bool(row and row['phase']=='cohort')
def reviewed_action(store,turn):
 row=store.db.execute('SELECT correct_action FROM turn_review WHERE turn_id=? ORDER BY revision DESC LIMIT 1',(turn['turn_id'],)).fetchone()
 return {'action':row[0],'intentCode':'reviewed_'+row[0]} if row else None
def tick():
 with CycleStore(ROOT/'var/second-cycle.sqlite') as store:
  plan=store.db.execute("SELECT id FROM plan WHERE market='it' AND institution='bjn-local-research'").fetchone()[0];setting=agent_setting(store,plan);now=store.clock()
  if not setting['enabled']:return {'state':'disabled','platformWrites':0,'realSends':0}
  if not inside(setting,now):return {'state':'outside_reply_window','platformWrites':0,'realSends':0}
  if send_dispatch_active(store):return {'state':'send_dispatch_active','platformWrites':0,'realSends':0}
  projection=backfill(store);policy=load_policy();templates=agent_template_map(store,policy);replies=AutoReplies(store);run_id='agent-run-'+digest([plan,int(now),setting['revision']])[:24]
  report={'runId':run_id,'state':'running','claimed':0,'noReply':0,'prepared':0,'human':0,'confirmed':0,'unknown':0,'platformWrites':0,'realSends':0}
  store.db.execute("INSERT OR IGNORE INTO agent_reply_run VALUES(?,?,\'running\',?,NULL,0,0,0,0,0,0,NULL)",(run_id,plan,now))
  for pending in store.db.execute("""SELECT p.* FROM inbox_pending p JOIN relationship r
    ON r.plan_id=p.plan_id AND r.creator_id=p.creator_id
    WHERE p.plan_id=? AND p.state IN ('awaiting_classification','review_partial','template_ready','policy_review','facts_ready_for_review','needs_facts')
    AND p.due_at<=? AND r.inbox_until>? AND r.mode='auto' AND r.rejected=0
    AND NOT EXISTS(SELECT 1 FROM service_case c WHERE c.plan_id=p.plan_id AND c.creator_id=p.creator_id AND c.state='open')
    ORDER BY p.due_at LIMIT 20""",(plan,now,now)).fetchall():
   turn=store.db.execute('SELECT * FROM inbound_turn WHERE plan_id=? AND creator_id=? AND historical=0 ORDER BY coalesce(occurred_ms,observed_at*1000) DESC LIMIT 1',(plan,pending['creator_id'])).fetchone()
   if not turn:continue
   decision=reviewed_action(store,turn)
   if not decision:
    row=store.db.execute("SELECT decision_json FROM reply_classification WHERE state='ready' AND provider='deepseek' AND json_extract(input_json,'$.turn.turnId')=? ORDER BY created_at DESC LIMIT 1",(turn['turn_id'],)).fetchone()
    if row:decision=json.loads(row[0])
   if not decision:
    try:decision=classify(store,turn['turn_id'],'agent-'+digest([turn['turn_id'],policy['version']])[:24],DeepSeekClassifier())
    except Exception:open_human(store,plan,turn,pending,'classification_failed');report['human']+=1;continue
   action=decision['action'];report['claimed']+=1
   if action=='no_reply' and setting['actions']['no_reply']:close_no_reply(store,plan,turn,pending);report['noReply']+=1
   elif action in templates and setting['actions'].get(action):
    key,text,revision=templates[action];replies.prepare_policy(plan,turn['creator_id'],turn['cid'],pending['revision'],action,key,text,turn['turn_id'],policy['version']+':template-'+str(revision));report['prepared']+=1
   else:open_human(store,plan,turn,pending,decision.get('intentCode') or 'agent_handoff');report['human']+=1
  q=store.db.execute("SELECT id FROM service_reply WHERE plan_id=? AND state IN ('ready','inflight','accepted','unknown') AND kind IN ('sample_self_service','collaboration_ack','link_usage') ORDER BY CASE state WHEN 'unknown' THEN 0 WHEN 'inflight' THEN 1 ELSE 2 END,created LIMIT 1",(plan,)).fetchone()
  if q:
   spec=importlib.util.spec_from_file_location('agent_reply_runtime',ROOT/'scripts/run-auto-replies.py');runtime=importlib.util.module_from_spec(spec);spec.loader.exec_module(runtime)
   queued=replies.get(q[0]);new_dispatch=queued['state']=='ready'
   state=runtime.run_reply(store,replies,queued);report['realSends']=int(new_dispatch);report['platformWrites']=int(new_dispatch);report['confirmed']+=int(state=='confirmed');report['unknown']+=int(state=='unknown')
  report['state']='completed';store.db.execute("UPDATE agent_reply_run SET state='completed',finished_at=?,claimed=?,no_reply=?,prepared=?,human=?,confirmed=?,unknown=? WHERE run_id=?",(store.clock(),report['claimed'],report['noReply'],report['prepared'],report['human'],report['confirmed'],report['unknown'],run_id));return report|{'replyProjection':projection}
def main():
 p=argparse.ArgumentParser();p.add_argument('--worker',action='store_true');p.add_argument('--interval',type=int,default=60);p.add_argument('--stop',type=Path);a=p.parse_args();signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
 with (ROOT/'var/agent-reply-worker.lock').open('a') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  while not STOP and not (a.stop and a.stop.exists()):
   try:result=tick()
   except Exception as e:result={'state':'failed','error':str(e) if isinstance(e,CycleError) else type(e).__name__,'platformWrites':0,'realSends':0}
   (ROOT/'var/agent-reply-status.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');print(json.dumps(result,ensure_ascii=False),flush=True)
   if not a.worker:break
   for _ in range(max(30,a.interval)):
    if STOP or (a.stop and a.stop.exists()):break
    time.sleep(1)
if __name__=='__main__':raise SystemExit(main())
