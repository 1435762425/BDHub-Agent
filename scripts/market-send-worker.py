#!/usr/bin/env python3
"""Continuous market sender using the market-scoped durable delivery path."""
import argparse,json,os,signal,sys,time
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
 if code=='market_send_candidate_missing':return 'waiting_pool'
 if code in ('ProfileBusyError','live_guard_busy'):return 'waiting_account'
 return None

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--market',required=True,choices=('br','my','uk'));p.add_argument('--once',action='store_true');p.add_argument('--interval',type=int,default=30);a=p.parse_args()
 if not 5<=a.interval<=300:p.error('interval 5..300')
 signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
 while not STOP:
  waiting=None
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
    result=run(ROOT,a.market,request,canary=canary,page_control=True);write(a.market,{'running':True,'state':result.get('state') or result.get('stopped') or 'idle','pid':os.getpid(),'checkedAt':time.time(),'result':result})
   except Exception as error:
    code=str(error) if isinstance(error,ValueError) else type(error).__name__;waiting=expected_wait_state(code)
    if waiting:write(a.market,{'running':True,'state':waiting,'pid':os.getpid(),'checkedAt':time.time(),'reason':code})
    else:write(a.market,{'running':False,'state':'attention','pid':os.getpid(),'checkedAt':time.time(),'error':code});return 2
  if a.once:break
  delay=3 if waiting=='waiting_account' else a.interval
  for _ in range(delay):
   if STOP:break
   time.sleep(1)
 return 0
if __name__=='__main__':raise SystemExit(main())
