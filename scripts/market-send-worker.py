#!/usr/bin/env python3
"""Continuous market sender using the market-scoped durable delivery path."""
import argparse,fcntl,json,os,signal,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.dont_write_bytecode=True;sys.path.insert(0,str(ROOT/'scripts'))
from lib.market_send_canary import run
from lib.operations_workflow import setting
from lib.second_cycle import CycleStore
from lib.send_batch import window_state

STOP=False
def stop(*_):
 global STOP;STOP=True

def state_path(market):return ROOT/f'var/market-send-worker-{market}.json'
def write(market,value):
 path=state_path(market);tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n');tmp.replace(path)

def expected_wait_state(code):
 if code in ('market_send_candidate_missing','outreach_allocation_stale','outreach_recipient_already_allocated'):return 'waiting_pool'
 # Stay alive and re-read the original request every five minutes: the readback confirms the card or, after two
 # reads that find it absent, isolates the creator.  The scheduler does not relaunch a sender stopped on unknown.
 if code in ('market_send_result_unknown','market_send_conversation_result_unknown'):return 'waiting_reconciliation'
 # A platform refusal is settled and counted by the new-contact gate, which then holds until the next day.
 if code in ('new_contact_capacity_reached','it_delivery_send_rejected'):return 'waiting_capacity'
 if code in ('ProfileBusyError','live_guard_busy','delivery_executor_busy'):return 'waiting_account'
 return None

def inbox_waiting(market):
 try:
  value=json.loads((ROOT/f'var/market-inbox-{market}.json').read_text())
  return value.get('state')=='waiting_account' and time.time()-float(value.get('checkedAt') or 0)<30
 except (OSError,ValueError,TypeError):return False

def next_delay(*,sent,waiting,interval,inbox_waiting_now=False):
 return 3 if sent and inbox_waiting_now else 0.25 if sent else 3 if waiting=='waiting_account' else \
  300 if waiting in ('waiting_capacity','waiting_reconciliation') else interval

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--market',required=True,choices=('br','my','uk'));p.add_argument('--once',action='store_true');p.add_argument('--interval',type=int,default=30);a=p.parse_args()
 if not 5<=a.interval<=300:p.error('interval 5..300')
 signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
 lock=(ROOT/f'var/market-send-worker-{a.market}.lock').open('a')
 try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 except BlockingIOError:raise SystemExit('market_send_worker_busy')
 while not STOP:
  waiting=None;sent=False
  with CycleStore(ROOT/'var/second-cycle.sqlite') as store:
   control=setting(store,a.market)
   from lib.market_send_control import control as send_control
   send=send_control(store,a.market)
   from lib.account_identity import current_generation
   from lib.market_accounts import load_config
   account=load_config(ROOT)['markets'][a.market]['roles']['communications']
   generation=current_generation(store,a.market,account)
   canary=a.market=='my' and (generation or {}).get('capabilities',{}).get('message_send',{}).get('state')!='verified'
  if send['stopRequested'] or not (control['continuousSendEnabled'] or send['automaticEnabled'] or send['runRequested']):
   write(a.market,{'running':False,'state':'off','pid':os.getpid(),'checkedAt':time.time()});break
  window=window_state(send['window'],time.time())
  if canary and not send['runRequested']:
   write(a.market,{'running':True,'state':'first_send_requires_page_start','pid':os.getpid(),'checkedAt':time.time()})
  elif not window['open']:
   write(a.market,{'running':True,'state':'waiting_window','pid':os.getpid(),'checkedAt':time.time()})
  else:
   request=f"market-continuous-{a.market}-{int(time.time())}"
   try:
    started=time.monotonic()
    result=run(ROOT,a.market,request,canary=canary,page_control=True)
    waiting='waiting_reconciliation' if result.get('state')=='waiting_reconciliation' else None
    sent=result.get('state')=='confirmed' and result.get('realSends',0)>0
    write(a.market,{'running':True,'state':result.get('state') or result.get('stopped') or 'idle','pid':os.getpid(),
                    'checkedAt':time.time(),'runDurationMs':round((time.monotonic()-started)*1000,1),'result':result})
   except Exception as error:
    code=getattr(error,'code',None) or (str(error) if isinstance(error,ValueError) else type(error).__name__);waiting=expected_wait_state(code)
    if waiting:write(a.market,{'running':True,'state':waiting,'pid':os.getpid(),'checkedAt':time.time(),'reason':code})
    else:write(a.market,{'running':False,'state':'attention','pid':os.getpid(),'checkedAt':time.time(),'error':code});return 2
  if a.once:break
  if sent and inbox_waiting(a.market):
   handoff_until=time.monotonic()+8
   while not STOP and inbox_waiting(a.market) and time.monotonic()<handoff_until:
    time.sleep(0.25)
  delay=next_delay(sent=sent,waiting=waiting,interval=a.interval,inbox_waiting_now=inbox_waiting(a.market))
  until=time.monotonic()+delay
  while not STOP and time.monotonic()<until:
   time.sleep(max(0,min(0.25,until-time.monotonic())))
 return 0
if __name__=='__main__':raise SystemExit(main())
