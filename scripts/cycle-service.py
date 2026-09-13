#!/usr/bin/env python3
"""Local service reads and explicit human case completion. No outbound APIs."""
import json,sys,sqlite3
from contextlib import closing
from pathlib import Path
from lib.second_cycle import CycleStore,CycleError
from lib.cycle_service import Service,service_status
ROOT=Path(__file__).resolve().parents[1]
def main():
 try:
  raw=sys.stdin.read(20001)
  if len(raw.encode())>20000:raise CycleError('input_too_large')
  req=json.loads(raw);action=req.get('action')
  if action not in ('status','resolve'):raise CycleError('invalid_action')
  with CycleStore(ROOT/'var/second-cycle.sqlite',readonly=action=='status') as store:
   plan=store.db.execute("SELECT id FROM plan WHERE market='it' AND institution='bjn-local-research'").fetchone()[0]
   if action=='status':
    result=service_status(store,plan)
    identity_path=ROOT/'var/creator-identities.sqlite'
    if identity_path.exists():
     with closing(sqlite3.connect(identity_path.as_uri()+'?mode=ro',uri=True)) as ids, ids:
      for case in result['cases']:
       row=ids.execute("SELECT current_handle FROM creator_identity WHERE market='it' AND creator_id=? AND oec_id=?",(case['creator_id'],case['oec'])).fetchone()
       case['handle']=row[0] if row else None
   else:
    if set(req)!={'action','caseId','expectedRevision','expectedControlRevision','note'} or type(req['expectedRevision']) is not int or type(req['expectedControlRevision']) is not int:raise CycleError('invalid_input')
    result=Service(store).resolve_case(plan,req['caseId'],req['expectedRevision'],req['expectedControlRevision'],req['note'])
   print(json.dumps(result,ensure_ascii=False))
 except Exception as e:print(json.dumps({'error':str(e) if isinstance(e,CycleError) else 'service_unavailable'}));return 1
 return 0
if __name__=='__main__':raise SystemExit(main())
