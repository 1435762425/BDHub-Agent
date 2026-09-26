#!/usr/bin/env python3
"""Scheduled versioned Agent replies; no work outside the reply window."""
import argparse,fcntl,json,re,signal,sys,time
from datetime import datetime,timezone,timedelta
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.dont_write_bytecode=True;sys.path.insert(0,str(ROOT/'scripts'))
from lib.cycle_auto_reply import REPLY_CHECK_INTERVAL,AutoReplies,reply_blocker
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
def near_send_window(window,buffer,stamp):
 """True within ``buffer`` minutes of the second-send window; compared on a 24h circle so a guard running past
 midnight (24:00 + 30 min) still covers 00:00-00:30."""
 now=datetime.fromtimestamp(stamp,BEIJING);minutes=now.hour*60+now.minute
 def m(v):h,n=map(int,v.split(':'));return h*60+n
 start,end=m(window[0])-buffer,m(window[1])+buffer
 return any(start<=minutes+shift<end for shift in (-1440,0,1440))
def send_window(store,market):
 """The window the second-send worker uses now, which may have moved after the Agent schedule was saved."""
 if market=='it':
  from lib.continuous_send import control
  return control(store,ROOT)['window']
 from lib.market_send_control import control
 return control(store,market)['window']
def authorized_request(value):
 if value is None:return None
 if not isinstance(value,str) or not REQUEST_ID.fullmatch(value):raise CycleError('agent_reply_authorization_invalid')
 return value
def send_dispatch_active(store,market='it'):
 tables={row[0] for row in store.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
 if 'cycle_delivery_part' in tables and {'cycle_delivery','plan'}<=tables:
  active=store.db.execute("""SELECT 1 FROM cycle_delivery_part p
    JOIN cycle_delivery d ON d.id=p.delivery_id JOIN plan n ON n.id=d.plan_id
    WHERE n.market=? AND p.state IN ('inflight','accepted') LIMIT 1""",(market,)).fetchone()
 elif 'cycle_delivery_part' in tables:
  active=store.db.execute("SELECT 1 FROM cycle_delivery_part WHERE state IN ('inflight','accepted') LIMIT 1").fetchone()
 else:active=None
 if active:return True
 if market!='it':return False
 if not store.db.execute("SELECT 1 FROM sqlite_master WHERE name='cycle_bulk_runtime'").fetchone():return False
 row=store.db.execute("SELECT f.state,r.phase FROM cycle_bulk_freeze f JOIN cycle_bulk_runtime r ON r.batch_id=f.batch_id WHERE f.state IN ('starting','running','stop_requested') ORDER BY f.created_at DESC LIMIT 1").fetchone()
 return bool(row and row['phase']=='cohort')

def decision_retry_ready(store,plan,turn_id,now):
 """One bad model input cannot monopolize the market on every worker tick."""
 attempts=store.db.execute("""SELECT state,created_at FROM agent_reply_decision_v2
   WHERE plan_id=? AND turn_id=? AND mode='production' ORDER BY created_at DESC""",
   (plan,turn_id)).fetchall()
 attempts=[row for row in attempts if row['state']!='not_sent']  # Never-sent calls cost no attempt.
 if not attempts:return True
 if any(row['state']=='ready' for row in attempts):return True
 if len(attempts)>=3:return False
 return now-attempts[0]['created_at']>=3600

def run_existing(store,replies,reply,market,stage,authorized_now=None,read_started=lambda:None):
 recovering=reply['state'] in ('inflight','accepted','unknown','isolated')
 if market=='it':
  from lib.reply_transport import run_reply
  state=run_reply(store,replies,reply,root=ROOT,authorized_now=authorized_now is not None,stopped=lambda:STOP,read_started=read_started)
  return {'state':state,'platformWrites':int(not recovering and state in ('confirmed','unknown')),
          'realSends':int(not recovering and state=='confirmed')}
 from lib.market_agent_reply import run_reply as market_run_reply
 return market_run_reply(ROOT,store,replies,reply,market,pilot=stage=='pilot_running',
                         authorized_now=authorized_now is not None,stopped=lambda:STOP,read_started=read_started)
def recover_unresolved(store,plan,market,stage):
 """Bounded original-intent reads for submitted replies; exhausted ones are isolated (§9.18).

 Returns a tick result while any reply still holds the market, or None once every unresolved
 reply is isolated so other creators can continue.  Never generates or submits anything."""
 rows=store.db.execute("""SELECT id FROM service_reply WHERE plan_id=?
   AND state IN ('inflight','accepted','unknown')
   AND (kind IN ('agent_generated_v2','agent_request_detail_v2','agent_handoff_v2') OR ?='it')
   ORDER BY created""",(plan,market)).fetchall()
 if not rows:return None
 replies=AutoReplies(store);waiting=[];isolated=0;deferred=[]
 # Exact evidence already stored by normal inbox reading settles first, without a platform read.
 if replies.settle_observed(plan):
  rows=[row for row in rows if replies.get(row['id'])['state'] in ('inflight','accepted','unknown')]
  if not rows:return None
 for row in rows:
  step=replies.recovery_step(row['id'])
  if step['action']=='isolate':replies.isolate(row['id']);isolated+=1;continue
  if step['action']=='wait':waiting.append(step['nextAt']);continue
  if step['action']!='check':continue
  reply=replies.record_check(row['id']);started=[]
  try:state=run_existing(store,replies,reply,market,stage,read_started=lambda:started.append(True))['state']
  except CycleError as error:state=replies.get(row['id'])['state'];code=str(error)
  except Exception as error:
   # Only a read that never reached the platform (send lock, account unavailable) is refunded; a read
   # that started and then failed is a spent check. The submission deadline bounds the market hold
   # either way, and later replies are still visited.
   if not started:replies.refund_check(row['id'])
   deferred.append(type(error).__name__)
   waiting.append(min(store.clock()+REPLY_CHECK_INTERVAL,AutoReplies.deadline(reply)));continue
  else:code=None
  return {'state':'original_intent_rechecked','replyState':state,'attempt':step['attempt'],
          **({'error':code} if code else {}),'isolated':isolated,'platformWrites':0,'realSends':0}
 if waiting:
  return {'state':'reply_check_deferred' if deferred else 'waiting_reply_check','nextAt':min(waiting),'isolated':isolated,
          **({'error':deferred[0]} if deferred else {}),'platformWrites':0,'realSends':0}
 return None
def pending_rows(store,plan,now,limit=20):
 if type(limit) is not int or not 1<=limit<=20:raise CycleError('agent_pending_limit_invalid')
 rows=store.db.execute("""SELECT p.* FROM inbox_pending p JOIN relationship r
   ON r.plan_id=p.plan_id AND r.creator_id=p.creator_id
   WHERE p.plan_id=? AND p.state IN ('awaiting_content','awaiting_classification','review_partial','template_ready','policy_review','facts_ready_for_review','needs_facts')
   AND p.due_at<=? AND r.mode='auto' AND r.rejected=0
   AND NOT EXISTS(SELECT 1 FROM service_case c WHERE c.plan_id=p.plan_id AND c.creator_id=p.creator_id AND c.state='open')
   ORDER BY p.due_at""",(plan,now))
 tables={row[0] for row in store.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
 # A creator whose submitted reply is isolated keeps new inbound stored but gets no automatic reply.
 isolated={r[0] for r in store.db.execute("SELECT creator_id FROM service_reply WHERE plan_id=? AND state='isolated'",(plan,))} \
   if 'service_reply' in tables else set()
 rows=[row for row in rows if row['creator_id'] not in isolated]
 result=[]
 scoped='service_message_resolution' in tables
 if scoped:
  from lib.reply_scope import unresolved,freeze,retry_ready
  from lib.agent_reply_v2 import guide
  guide_revision=guide(ROOT,store,plan)["revision"]
  ordered=[]
  for row in rows:
   scope=unresolved(store,plan,row['creator_id'])
   if not scope:continue
   # Missing content, multiple CIDs and gaps cannot monopolize the runnable queue.
   if any(not r['contentHash'] for r in scope) or len({r['conversationId'] for r in scope})!=1:continue
   if store.db.execute("SELECT 1 FROM inbox_checkpoint WHERE plan_id=? AND oec=(SELECT oec FROM relationship WHERE plan_id=? AND creator_id=?) AND state IN ('gap','backfilling')",(plan,plan,row['creator_id'])).fetchone():continue
   if not retry_ready(store,plan,row['creator_id'],freeze(scope),now,guide_revision):continue
   ordered.append((min(r['at'] for r in scope),row['creator_id'],dict(row)|{'scopeTurnId':scope[-1]['turnId']}))
  return [v[2] for v in sorted(ordered,key=lambda v:(v[0],v[1]))[:limit]]
 for pending in rows:
  if 'inbound_turn' in tables:
   turn=store.db.execute('SELECT turn_id,cid,oec,occurred_ms,observed_at FROM inbound_turn '
                         'WHERE plan_id=? AND creator_id=? AND historical=0 '
                         'ORDER BY coalesce(occurred_ms,observed_at*1000) DESC,message_id DESC LIMIT 1',
                         (plan,pending['creator_id'])).fetchone()
   if turn:
    from lib.observed_messages import replied_after
    stamp=turn['occurred_ms']/1000 if turn['occurred_ms'] else turn['observed_at']
    if replied_after(store.db,plan,turn['cid'],turn['oec'],stamp):continue
    if 'agent_reply_decision_v2' in tables and not decision_retry_ready(store,plan,turn['turn_id'],now):continue
  result.append(pending)
  if len(result)>=limit:break
 return result
def tick(authorized_now=None,market="it"):
 authorized_now=authorized_request(authorized_now)
 with CycleStore(ROOT/'var/second-cycle.sqlite') as store:
  plan_row=store.db.execute("SELECT id FROM plan WHERE market=? AND institution='bjn-local-research'",(market,)).fetchone()
  if not plan_row:raise CycleError('plan_missing')
  plan=plan_row[0];setting=agent_setting(store,plan);now=store.clock()
  stage=rollout_stage(store,plan,market)
  if store.db.execute("SELECT 1 FROM service_reply WHERE plan_id=? AND state='isolated' LIMIT 1",(plan,)).fetchone():
   AutoReplies(store).settle_observed(plan)  # Isolated replies are not re-read, only settled from stored evidence.
  recovered=recover_unresolved(store,plan,market,stage)
  if recovered:return recovered
  if not setting['enabled']:return {'state':'disabled','platformWrites':0,'realSends':0}
  if send_dispatch_active(store,market):return {'state':'send_dispatch_active','platformWrites':0,'realSends':0}
  if stage in ('pilot_required','pilot_complete'):
   return {'state':'first_send_requires_page_start' if stage=='pilot_required' else 'pilot_complete_waiting_resume',
           'platformWrites':0,'realSends':0}
  if not inside(setting,now) and authorized_now is None:return {'state':'outside_reply_window','platformWrites':0,'realSends':0}
  # Replies never share time with the second send: scheduled runs wait until the send window and its buffer pass.
  if authorized_now is None and near_send_window(send_window(store,market),setting['bufferMinutes'],now):
   return {'state':'waiting_send_window','platformWrites':0,'realSends':0}
  blocker=reply_blocker(store.db,plan)
  if blocker:return {'state':'waiting_dispatch','blockedBy':blocker,'platformWrites':0,'realSends':0}
  projection=backfill(store);replies=AutoReplies(store);run_id='agent-run-'+digest([plan,int(now),setting['revision'],authorized_now])[:24]
  report={'runId':run_id,'state':'running','triggerSource':'user_authorized_now' if authorized_now else 'reply_window',
   'authorizationRequestId':authorized_now,'claimed':0,'noReply':0,'prepared':0,'human':0,'confirmed':0,
   'unknown':0,'deferred':0,'platformWrites':0,'realSends':0}
  store.db.execute("INSERT OR IGNORE INTO agent_reply_run VALUES(?,?,\'running\',?,NULL,0,0,0,0,0,0,NULL)",(run_id,plan,now))
  try:
   outstanding=store.db.execute("SELECT 1 FROM service_reply WHERE plan_id=? AND kind IN ('agent_generated_v2','agent_request_detail_v2','agent_handoff_v2') AND state='ready' LIMIT 1",(plan,)).fetchone()
   candidates=[] if outstanding else pending_rows(store,plan,now)
   if stage=='pilot_running':candidates=candidates[:1]
   for pending in candidates:
    # Skip bounded technical failures, but never build a backlog of stale ready drafts.
    current_setting=agent_setting(store,plan)
    if STOP or not current_setting['enabled'] or authorized_now is None and (
        not inside(current_setting,store.clock()) or
        near_send_window(send_window(store,market),current_setting['bufferMinutes'],store.clock())):
     break
    turn=store.db.execute('SELECT * FROM inbound_turn WHERE plan_id=? AND creator_id=? AND historical=0 ORDER BY coalesce(occurred_ms,observed_at*1000) DESC,message_id DESC LIMIT 1',(plan,pending['creator_id'])).fetchone()
    if isinstance(pending,dict) and pending.get('scopeTurnId'):
     turn=store.db.execute('SELECT * FROM inbound_turn WHERE turn_id=?',(pending['scopeTurnId'],)).fetchone()
    if not turn:continue
    if not (isinstance(pending,dict) and pending.get('scopeTurnId')) and not decision_retry_ready(store,plan,turn['turn_id'],now):
     report['deferred']+=1;continue
    try:
     context=production_context(ROOT,store,plan,market,turn['turn_id'],include_current_invitation=True,live=True)
     generated=generate(ROOT,store,plan,market,context,mode='production')
     applied=apply_production(store,plan,context,generated)
    except CycleError as error:
     if str(error)=='agent_model_service_paused':
      report['modelServicePaused']=True;break  # Nobody else is called while the shared service is paused.
     report['deferred']+=1;continue
    report['claimed']+=1
    report['noReply']+=int(applied['route']=='no_reply')
    report['human']+=int(applied['route']=='handoff')
    report['prepared']+=int(applied['replyId'] is not None)
    if applied['replyId'] is not None:break  # Send the first ready reply before another model call.
   q=store.db.execute("SELECT id FROM service_reply WHERE plan_id=? AND state='ready' AND kind IN ('agent_generated_v2','agent_request_detail_v2','agent_handoff_v2') ORDER BY created LIMIT 1",(plan,)).fetchone()
   if q:
    result=run_existing(store,replies,replies.get(q[0]),market,stage,authorized_now)
    report['realSends']=result['realSends'];report['platformWrites']=result['platformWrites']
    report['confirmed']+=int(result['state']=='confirmed');report['unknown']+=int(result['state']=='unknown')
  except BaseException as error:
   store.db.execute("UPDATE agent_reply_run SET state='failed',finished_at=? WHERE run_id=?",(store.clock(),run_id))
   raise
  report['state']='completed'
  store.db.execute("UPDATE agent_reply_run SET state='completed',finished_at=?,claimed=?,no_reply=?,prepared=?,human=?,confirmed=?,unknown=? WHERE run_id=?",(store.clock(),report['claimed'],report['noReply'],report['prepared'],report['human'],report['confirmed'],report['unknown'],run_id))
  return report|{'replyProjection':projection}
def worker_delay(result,interval):
 """Continue useful work promptly; wait/failed/unknown states retain bounded polling."""
 progressed=result.get('state')=='completed' and any(int(result.get(key) or 0)>0 for key in ('claimed','confirmed','noReply','human'))
 reconciled=result.get('state')=='original_intent_rechecked' and result.get('replyState')=='confirmed'
 return 1 if progressed or reconciled else max(30,interval)

def main():
 p=argparse.ArgumentParser();p.add_argument('--worker',action='store_true');p.add_argument('--interval',type=int,default=60);p.add_argument('--stop',type=Path);p.add_argument('--authorized-now');p.add_argument('--market',default='it');a=p.parse_args();signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
 if a.worker and a.authorized_now:p.error('--authorized-now cannot be used with --worker')
 with (ROOT/'var'/('agent-reply-worker.lock' if a.market=='it' else f'agent-reply-worker-{a.market}.lock')).open('a') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  if a.worker:
   from lib.runtime_release import register
   register(ROOT,f'agent-reply-{a.market}')
  while not STOP and not (a.stop and a.stop.exists()):
   try:result=tick(a.authorized_now,a.market)
   except Exception as e:result={'state':'failed','error':str(e) if isinstance(e,CycleError) else type(e).__name__,'platformWrites':0,'realSends':0}
   status_path=ROOT/'var'/('agent-reply-status.json' if a.market=='it' else f'agent-reply-status-{a.market}.json')
   status_path.write_text(json.dumps(result|{'pid':__import__('os').getpid(),'checkedAt':time.time()},ensure_ascii=False,indent=2)+'\n')
   print(json.dumps(result,ensure_ascii=False),flush=True)
   if not a.worker:break
   if a.market!='it' and result.get('state')=='disabled':break
   a.authorized_now=None
   for _ in range(worker_delay(result,a.interval)):
    if STOP or (a.stop and a.stop.exists()):break
    time.sleep(1)
if __name__=='__main__':raise SystemExit(main())
