#!/usr/bin/env python3
"""Idempotently import retained identity failure evidence into the shared retry ledger."""
import argparse,json,sqlite3,sys
from collections import Counter
from contextlib import closing
from datetime import datetime
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.dont_write_bytecode=True;sys.path.insert(0,str(ROOT/'scripts'))
from lib.identity_retry import classify,record,snapshot,project_isolated
from lib.second_cycle import digest

def backfill(root):
 root=Path(root);events=[];stats=Counter();seen=set()
 path=root/'var/creator-discovery.sqlite'
 if path.exists():
  with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as db:
   for id,batch,handle in db.execute("SELECT id,batch_id,handle FROM discovery_item WHERE status='blocked'"):
    paths=list((root/'var/creator-discovery'/batch/id).glob('attempt-*/report.private.json'))
    if not paths:stats['legacyMissingEvidence']+=1
    for p in paths:
     events.append((p,'it','acc6',{id:handle},None))
 for path in (root/'var').glob('market-identity-*/output/report.private.json'):
  folder=path.parent.parent;parts=folder.name.split('-');market=parts[2];token='-'.join(parts[3:])
  if market not in ('br','my','uk'):continue
  try:
   targets=json.loads((folder/'targets.private.json').read_text())['targets']
   lookup={t['ref']:t['handle'] for t in targets}
  except (OSError,ValueError,KeyError,TypeError):stats['legacyMissingEvidence']+=1;continue
  events.append((path,market,{'br':'acc1','my':'acc8','uk':'acc11'}[market],lookup,token))
 # Old waits are dated at their original read, so imports do not fabricate a fresh outage.
 ordered=[]
 for path,market,account,lookup,token in events:
  try:
   value=json.loads(path.read_text());stamp=datetime.fromisoformat(value['finishedAt'].replace('Z','+00:00')).timestamp()
   if value.get('market')!=market or value.get('account')!=account:raise ValueError('scope')
   ordered.append((stamp,path,market,account,lookup,token,value))
  except (OSError,ValueError,KeyError,TypeError):stats['invalidEvidence']+=1
 for stamp,path,market,account,lookup,token,value in sorted(ordered,key=lambda r:(r[0],str(r[1]))):
  if not snapshot(root,market)['enabled']:raise ValueError('identity_retry_schema_required')
  event=token or value.get('cohortEvidenceSha256') or digest(value)
  for ref,handle in lookup.items():
   category,reason=classify(value,ref,expected_handle=handle,market=market)
   if category not in ('individual','shared','success'):continue
   key=(market,event,handle)
   if key in seen:continue
   seen.add(key);record(root,market,account,handle,event,category,reason,evidence=str(path.relative_to(root)),now=stamp);stats[category]+=1
   # Correct only the label produced by the first v22 importer for missing
   # verification fields; budgets, original reports and attempt IDs stay fixed.
   if category=='shared' and reason in ('request_or_signer_error','transport_unavailable','read_contract_incomplete'):
    with closing(sqlite3.connect(root/'var/second-cycle.sqlite')) as db,db:
     changed=db.execute("UPDATE identity_retry_event SET reason=? WHERE id=? AND category='shared' AND reason='verification_required'",(reason,digest([market,event,handle.strip().lstrip('@').lower()]))).rowcount
     if changed:
      stats['reasonCorrections']+=changed
      db.execute("UPDATE identity_account_wait SET reason=? WHERE market=? AND last_event=? AND reason='verification_required'",(reason,market,event))
 for market in ('it','br','my','uk'):stats['projected']+=project_isolated(root,market)
 return {'schema':'bdhub.identity-retry-backfill.v1','counts':dict(stats),'platformWrites':0,'realSends':0}

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['backfill']);p.add_argument('--confirm',action='store_true');a=p.parse_args()
 if not a.confirm:p.error('--confirm required for local ledger backfill')
 print(json.dumps(backfill(ROOT),ensure_ascii=False))
if __name__=='__main__':main()
