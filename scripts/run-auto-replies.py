#!/usr/bin/env python3
"""Automatic Italy policy replies; fresh context, one send attempt, exact readback."""
import argparse,json,sqlite3,sys,time,signal,fcntl,hashlib
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.dont_write_bytecode=True;sys.path.insert(0,str(ROOT/'scripts'))
from lib.second_cycle import CycleStore,CycleError,digest
from lib.cycle_auto_reply import AutoReplies
from lib.cycle_inbox import Inbox
from lib.cycle_reply_facts import ReplyFacts
from lib.cycle_send_runtime import fresh_card
from lib.second_live_runtime import _authenticated,read_sender_binding,live_runtime
STOP=False

def stop(*_):
 global STOP
 STOP=True

def refresh(c):
 with _authenticated({},stopped=lambda:STOP) as (account,identity,headers,auth,maintenance,available):_,proof=fresh_card(c,account,identity,headers,maintenance,lambda:STOP)
 return proof

def escalate(store,plan,creator,revision,reason):
 with store.tx():
  p=store.db.execute('SELECT revision FROM inbox_pending WHERE plan_id=? AND creator_id=?',(plan,creator)).fetchone()
  if not p or p[0]!=revision:return
  old=store.db.execute("SELECT id FROM service_case WHERE plan_id=? AND creator_id=? AND state='open'",(plan,creator)).fetchone()
  if not old:store.db.execute("INSERT INTO service_case VALUES(?,?,?,'open',?,?,?,?, 'not_sent')",('case-'+digest([plan,creator,revision])[:24],plan,creator,revision,reason,time.time(),time.time()))
  store.db.execute("UPDATE inbox_pending SET state='human' WHERE plan_id=? AND creator_id=?",(plan,creator));store.db.execute("UPDATE relationship SET mode='human',revision=revision+1 WHERE plan_id=? AND creator_id=? AND mode='auto'",(plan,creator))

def run_reply(store,replies,q):
 report={};recovering=q['state'] in ('inflight','accepted','unknown');card=None
 if q['kind']=='manual_card':
  from lib.cycle_send_runtime import descriptor
  card=descriptor(json.loads(q['text']))
 with (ROOT/'var/cycle-send.lock').open('a') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  if not recovering:
   fact=store.db.execute('SELECT payload FROM service_reply_fact WHERE reply_id=?',(q['id'],)).fetchone()
   if fact:
    previous=json.loads(fact[0]);current=ReplyFacts(store,refresh).call('get_current_creator_commission',q['plan_id'],q['creator_id'])
    if (current['pid'],current['creatorPercent'])!=(previous['pid'],previous['creatorPercent']):
     store.db.execute("UPDATE service_reply SET state='cancelled' WHERE id=? AND state='ready'",(q['id'],));store.db.execute("UPDATE inbox_pending SET revision=revision+1,state='awaiting_content',due_at=? WHERE plan_id=? AND creator_id=? AND revision=?",(time.time()+60,q['plan_id'],q['creator_id'],q['pending_revision']));return 'facts_changed'
  binding=read_sender_binding(report,stopped=lambda:STOP)
  with live_runtime(binding,report,stopped=lambda:STOP) as rt:
   conv=rt['reads'].conversation(q['cid'],q['oec'])
   if not recovering:
    h=rt['reads'].history_summary(conv,include_contents=True)
    Inbox(store).ingest(q['plan_id'],conv.conversation_id,q['oec'],h);replies.service.capture(q['plan_id'],conv.conversation_id,q['oec'],h['contents'])
    def permit(scope):
     kind='card' if card else 'text'
     if scope.get('oecId')!=q['oec'] or scope.get('conversationId')!=q['cid'] or scope.get('componentKind')!=kind or scope.get('requestRef')!=q['request_ref']:raise CycleError('reply_scope_mismatch')
     if card and (scope.get('productId'),scope.get('listId'),scope.get('bindingSha256'))!=(card.product_id,card.list_id,card.binding_sha256):raise CycleError('reply_scope_mismatch')
     if not card and scope.get('textSha256')!=hashlib.sha256(q['text'].encode()).hexdigest():raise CycleError('reply_scope_mismatch')
     allowed=replies.begin(q['id']);mark();return allowed
    try:
     with rt['write_gate']() as mark:receipt=rt['adapter'].send_card_once(conv,card,q['request_ref'],before_dispatch=permit) if card else rt['adapter'].send_once(conv,q['text'],q['request_ref'],before_dispatch=permit)
     replies.accepted(q['id'],receipt)
    except Exception:
     if replies.get(q['id'])['state']=='inflight':replies.unknown(q['id'])
     else:
      with store.tx():
       store.db.execute("UPDATE service_reply SET state='cancelled' WHERE id=? AND state='ready'",(q['id'],))
       store.db.execute("UPDATE inbox_pending SET revision=revision+1,state='awaiting_content',due_at=? WHERE plan_id=? AND creator_id=? AND revision=?",(time.time()+60,q['plan_id'],q['creator_id'],q['pending_revision']))
     raise
   current=replies.get(q['id']);receipt=json.loads(current['receipt']) if current['receipt'] else {}
   proof=rt['adapter'].readback_card(conv,card,q['request_ref'],message_id=receipt.get('messageId')) if card else rt['adapter'].readback(conv,q['text'],q['request_ref'],message_id=receipt.get('messageId'))
   if proof['status']=='confirmed':replies.confirm(q['id'],proof)
   else:replies.unknown(q['id'])
 return replies.get(q['id'])['state']

def tick(enable=False):
 with CycleStore(ROOT/'var/second-cycle.sqlite') as s:
  replies=AutoReplies(s);plan=s.db.execute("SELECT id FROM plan WHERE market='it' AND institution='bjn-local-research'").fetchone()[0]
  if enable:replies.enable(plan,'User: 自动回复接起来; current confirmed business policy; historical imports excluded')
  if not replies.enabled(plan) or s._plan(plan)['state']!='active':return {'state':'paused'}
  report={'state':'running','automaticRepliesEnabled':True};s.db.execute('INSERT OR REPLACE INTO service_reply_runtime VALUES(?,?,?)',(plan,time.time(),'running'))
  facts=ReplyFacts(s,refresh)
  for p in s.db.execute("SELECT * FROM inbox_pending WHERE plan_id=? AND state IN ('needs_facts','facts_ready_for_review','policy_review','human') AND due_at<=? ORDER BY due_at LIMIT 3",(plan,time.time())).fetchall():
   values=None
   if p['state']!='human':
    assessment=s.db.execute('SELECT decision FROM service_assessment WHERE plan_id=? AND creator_id=? AND pending_revision=?',(plan,p['creator_id'],p['revision'])).fetchone()
    if not assessment:continue
    tool='get_current_creator_commission' if json.loads(assessment[0])['category']=='commission_question' else 'get_relationship_product_context'
    try:values=facts.call(tool,plan,p['creator_id'])
    except Exception as e:
     code=getattr(e,'code',None) or (str(e) if isinstance(e,CycleError) else type(e).__name__)
     if code in ('live_guard_busy','BlockingIOError'):continue
     s.db.execute('INSERT INTO service_reply_fact_failure VALUES(?,?,?,1) ON CONFLICT(plan_id,creator_id,revision) DO UPDATE SET attempts=attempts+1',(plan,p['creator_id'],p['revision']))
     failures=s.db.execute('SELECT attempts FROM service_reply_fact_failure WHERE plan_id=? AND creator_id=? AND revision=?',(plan,p['creator_id'],p['revision'])).fetchone()[0]
     if code in ('no_confirmed_product_context','ambiguous_product_context') or failures>=3:escalate(s,plan,p['creator_id'],p['revision'],'product_context_needs_review')
     else:continue
   replies.prepare(plan,p['creator_id'],values)
  q=s.db.execute("SELECT id FROM service_reply WHERE plan_id=? AND state IN ('ready','inflight','accepted','unknown') ORDER BY CASE state WHEN 'unknown' THEN 0 WHEN 'inflight' THEN 1 ELSE 2 END,created LIMIT 1",(plan,)).fetchone()
  if q:report['result']=run_reply(s,replies,replies.get(q[0]))
  report['counts']=dict(s.db.execute('SELECT state,count(*) FROM service_reply WHERE plan_id=? GROUP BY state',(plan,)))
  return report

def main():
 p=argparse.ArgumentParser();p.add_argument('--worker',action='store_true');p.add_argument('--enable',action='store_true');a=p.parse_args()
 signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
 def deadline(*_):raise TimeoutError('reply_read_deadline')
 signal.signal(signal.SIGALRM,deadline)
 with (ROOT/'var/auto-reply-worker.lock').open('a') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  enable=a.enable
  while not STOP:
   try:
    signal.setitimer(signal.ITIMER_REAL,55);report=tick(enable);enable=False
   except Exception as e:report={'state':'waiting','errorCode':getattr(e,'code',type(e).__name__)}
   finally:signal.setitimer(signal.ITIMER_REAL,0)
   report['pid']=__import__('os').getpid();report['checkedAt']=time.time();(ROOT/'var/auto-reply-status.json').write_text(json.dumps(report,indent=2));print(json.dumps(report),flush=True)
   if not a.worker:break
   for _ in range(15):
    if STOP:break
    time.sleep(1)
if __name__=='__main__':main()
