#!/usr/bin/env python3
"""Durable local handoff/reconciliation; discovery worker performs authorized reads."""
import argparse,json
from pathlib import Path
from lib.second_cycle import CycleStore
from lib.creator_discovery import CreatorDiscoveryStore
from lib.cycle_identity import IdentityBridge
ROOT=Path(__file__).resolve().parents[1]
def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['submit','reconcile']);a=p.parse_args()
 with CycleStore(ROOT/'var/second-cycle.sqlite') as store, CreatorDiscoveryStore(ROOT/'var') as discovery:
  plan=store.db.execute("SELECT id FROM plan WHERE institution='bjn-local-research' AND market='it'").fetchone()[0]
  bridge=IdentityBridge(store,discovery,ROOT/'var/creator-identities.sqlite')
  if a.action=='submit':bridge.freeze(plan);print(json.dumps({'submittedBatches':bridge.dispatch(plan)},ensure_ascii=False))
  else:print(json.dumps(bridge.reconcile(plan),ensure_ascii=False))
if __name__=='__main__':main()
