#!/usr/bin/env python3
"""Continuous market sender using the market-scoped durable delivery path."""
import argparse,fcntl,json,os,signal,sqlite3,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.dont_write_bytecode=True;sys.path.insert(0,str(ROOT/'scripts'))
from lib.market_registry import market_path_keys
from lib.market_send_canary import run
from lib.operations_workflow import setting
from lib.second_cycle import CycleError,CycleStore
from lib.send_batch import window_state
from lib.market_send_worker import has_pending_reconciliation,write_state

STOP=False
def stop(*_):
 global STOP;STOP=True

def write(market,value):
 write_state(ROOT,market,value)

TRANSIENT_READ_ERRORS=frozenset({'im_transport_error','wall_timeout'})
SUBMISSION_UNKNOWN_ERRORS=frozenset({'it_delivery_send_unknown','it_delivery_create_unknown','it_delivery_wall_timeout'})
TRANSIENT_DELAYS=(3,15,60,300)

def transport_retry_delay(code,failures):
 """Only classified read transport failures receive a bounded in-process retry budget."""
 if code not in TRANSIENT_READ_ERRORS or not 1<=failures<=len(TRANSIENT_DELAYS):return None
 return TRANSIENT_DELAYS[failures-1]

def expected_wait_state(code):
 if code in ('market_send_candidate_missing','outreach_allocation_stale','outreach_recipient_already_allocated'):return 'waiting_pool'
 # Stay alive for bounded original-intent readback; no waiting state grants new send authority.
 if code in ('market_send_result_unknown','market_send_conversation_result_unknown'):return 'waiting_reconciliation'
 if code in ('canary_authorization_unbound','canary_authorization_changed'):return 'waiting_canary_authorization'
 if code=='new_contact_capacity_reached':return 'waiting_capacity'
 # A refusal is settled on its own delivery; the classified new-contact gate decides on the next pass
 # whether it was the day's quota (hold) or one creator's refusal (continue).
 if code=='it_delivery_send_rejected':return 'waiting_platform_refusal'
 if code in ('ProfileBusyError','live_guard_busy','delivery_executor_busy','local_store_busy'):return 'waiting_account'
 return None

def error_code(error):
 # The immutable request already records any submission before a later local write
 # fails. Re-entering run() recovers that original request instead of resending it.
 if isinstance(error,sqlite3.OperationalError) and any(word in str(error).lower() for word in ('locked','busy')):
  return 'local_store_busy'
 return getattr(error,'code',None) or (str(error) if isinstance(error,ValueError) else type(error).__name__)

def inbox_waiting(market):
 try:
  value=json.loads((ROOT/f'var/market-inbox-{market}.json').read_text())
  return value.get('state')=='waiting_account' and time.time()-float(value.get('checkedAt') or 0)<30
 except (OSError,ValueError,TypeError):return False

def next_delay(*,sent,waiting,interval,inbox_waiting_now=False):
 return 3 if sent and inbox_waiting_now else 0.25 if sent else 3 if waiting=='waiting_account' else \
  300 if waiting in ('waiting_capacity','waiting_reconciliation') else interval

def first_send_canary(generation):
 """Until its first card+text is confirmed a market sends only as a single page-started canary."""
 return (generation or {}).get('capabilities',{}).get('message_send',{}).get('state')!='verified'

def stops_after(result):
 """Read-only confirmation consumes the same grant without counting a new real send."""
 return bool(result.get('canaryCompleted') and result.get('canaryStopRequested'))

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--market',required=True,choices=market_path_keys(ROOT));p.add_argument('--once',action='store_true');p.add_argument('--interval',type=int,default=30);a=p.parse_args()
 if not 5<=a.interval<=300:p.error('interval 5..300')
 signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
 with (ROOT/f'var/market-send-worker-{a.market}.lock').open('a') as lock:
  try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  except BlockingIOError:raise SystemExit('market_send_worker_busy')
  return run_loop(a)

def run_loop(a):
 from lib.runtime_release import register
 register(ROOT,f'market-send-{a.market}')
 transport_failures=0
 while not STOP:
  waiting=None;sent=False;retry_delay=None
  try:
   with CycleStore(ROOT/'var/second-cycle.sqlite') as store:
    from lib.market_send_control import control as send_control,_control_for_plan,settle_confirmed_canaries
    from lib.account_identity import current_generation
    from lib.market_accounts import load_config
    account=load_config(ROOT)['markets'][a.market]['roles']['communications']
    plan=store.db.execute("SELECT id,state FROM plan WHERE institution='bjn-local-research' AND market=?",(a.market,)).fetchone()
    if not plan:raise CycleError('plan_missing')
    plan_active=plan['state']=='active'
    completed=settle_confirmed_canaries(store,a.market,account) if plan_active else []
    control=setting(store,a.market);send=send_control(store,a.market) if plan_active else _control_for_plan(store,plan['id'])
    generation=current_generation(store,a.market,account)
    canary=first_send_canary(generation)
    recovering=has_pending_reconciliation(store,a.market)
  except sqlite3.OperationalError as error:
   # Opening the shared ledger while many processes start can hit a lock; wait instead of crashing.
   if 'locked' not in str(error) and 'busy' not in str(error):raise
   write(a.market,{'running':True,'state':'waiting_account','pid':os.getpid(),'checkedAt':time.time(),'reason':'local_store_busy'})
   until=time.monotonic()+3
   while not STOP and time.monotonic()<until:time.sleep(0.25)
   continue
  if not recovering and (not plan_active or send['stopRequested'] or not (control['continuousSendEnabled'] or send['automaticEnabled'] or send['runRequested'])):
   write(a.market,{'running':False,'state':'canary_confirmed' if completed else 'off','pid':os.getpid(),'checkedAt':time.time()});break
  window=window_state(send['window'],time.time())
  if not recovering and canary and not send['runRequested']:
   write(a.market,{'running':True,'state':'first_send_requires_page_start','pid':os.getpid(),'checkedAt':time.time()})
  elif not recovering and not window['open']:
   write(a.market,{'running':True,'state':'waiting_window','pid':os.getpid(),'checkedAt':time.time()})
  else:
   request=f"market-continuous-{a.market}-{int(time.time())}"
   try:
    started=time.monotonic()
    # Recovery has no authority to create an intent or submit either unsent component.
    result=run(ROOT,a.market,request,canary=canary,page_control=True,reconcile_only=recovering)
    transport_failures=0
    waiting='waiting_reconciliation' if result.get('state')=='waiting_reconciliation' else None
    sent=result.get('state')=='confirmed' and result.get('realSends',0)>0
    write(a.market,{'running':True,'state':result.get('state') or result.get('stopped') or 'idle','pid':os.getpid(),
                    'checkedAt':time.time(),'runDurationMs':round((time.monotonic()-started)*1000,1),'result':result})
    if stops_after(result):
     write(a.market,{'running':False,'state':'canary_confirmed','pid':os.getpid(),'checkedAt':time.time(),'result':result})
     break
   except Exception as error:
    code=error_code(error);waiting=expected_wait_state(code)
    if code in SUBMISSION_UNKNOWN_ERRORS:
     with CycleStore(ROOT/'var/second-cycle.sqlite',readonly=True) as store:
      if has_pending_reconciliation(store,a.market):waiting='waiting_reconciliation'
    if code in TRANSIENT_READ_ERRORS:
     transport_failures+=1;retry_delay=transport_retry_delay(code,transport_failures)
     if retry_delay is not None:waiting='waiting_transport'
    else:transport_failures=0
    if waiting:write(a.market,{'running':True,'state':waiting,'pid':os.getpid(),'checkedAt':time.time(),'reason':code})
    else:
     # The state file is overwritten by the next launch; the log keeps why this worker stopped.
     print(json.dumps({'event':'attention_exit','market':a.market,'error':code,'detail':str(error)[:300],'cause':repr(error.__context__)[:300] if error.__context__ else None,'at':time.time()},ensure_ascii=False),file=sys.stderr,flush=True)
     write(a.market,{'running':False,'state':'attention','pid':os.getpid(),'checkedAt':time.time(),'error':code});return 2
  if a.once:break
  if sent and inbox_waiting(a.market):
   handoff_until=time.monotonic()+8
   while not STOP and inbox_waiting(a.market) and time.monotonic()<handoff_until:
    time.sleep(0.25)
  delay=retry_delay if retry_delay is not None else next_delay(sent=sent,waiting=waiting,interval=a.interval,inbox_waiting_now=inbox_waiting(a.market))
  until=time.monotonic()+delay
  while not STOP and time.monotonic()<until:
   time.sleep(max(0,min(0.25,until-time.monotonic())))
 return 0
if __name__=='__main__':raise SystemExit(main())
