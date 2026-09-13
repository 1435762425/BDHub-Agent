#!/usr/bin/env python3
"""Inspect current creator commission for a confirmed delivery; no reply or card creation."""
import argparse,json,signal
from pathlib import Path
from lib.second_cycle import CycleStore
from lib.cycle_reply_facts import ReplyFacts
from lib.cycle_send_runtime import fresh_card
from lib.second_live_runtime import _authenticated
ROOT=Path(__file__).resolve().parents[1]
def refresh(c):
 report={}
 with _authenticated(report,stopped=lambda:False) as (account,identity,headers,auth,maintenance,available):
  _,proof=fresh_card(c,account,identity,headers,maintenance,lambda:False)
 return proof

def main():
 p=argparse.ArgumentParser();p.add_argument('--delivery-id');p.add_argument('--process-due',action='store_true');a=p.parse_args()
 def deadline(*_):raise TimeoutError('fact_deadline')
 signal.signal(signal.SIGALRM,deadline);signal.setitimer(signal.ITIMER_REAL,55)
 with CycleStore(ROOT/'var/second-cycle.sqlite') as store:
  facts=ReplyFacts(store,refresh)
  if a.process_due:
   plan=store.db.execute("SELECT id FROM plan WHERE market='it' AND institution='bjn-local-research'").fetchone()[0];result={'processed':facts.process_due(plan),'automaticReplies':0}
  else:
   row=store.db.execute("SELECT plan_id,creator_id FROM cycle_delivery WHERE id=? AND state='confirmed'",(a.delivery_id,)).fetchone()
   if not row:raise ValueError('confirmed_delivery_required')
   result=facts.call('get_current_creator_commission',row[0],row[1])
   (ROOT/'var/reply-current-commission-check.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
  print(json.dumps(result,ensure_ascii=False))
 signal.setitimer(signal.ITIMER_REAL,0)
if __name__=='__main__':main()
