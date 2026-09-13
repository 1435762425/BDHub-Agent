#!/usr/bin/env python3
"""Demand-driven names/card preparation for the authorized active outreach plan."""
import argparse,json,sqlite3,subprocess,sys,time,signal,os
from contextlib import closing
CHILD=None
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def main():
 p=argparse.ArgumentParser();p.add_argument('--report',type=Path,required=True);a=p.parse_args()
 if not a.report.resolve().is_relative_to(ROOT/'var') or a.report.exists():p.error('new local report required')
 base=a.report.with_suffix('');result={'steps':[],'realSends':0,'scope':'current qualified offers, cached product names, one card intent'}
 def interrupted(*_):
  if CHILD and CHILD.poll() is None:os.killpg(CHILD.pid,signal.SIGTERM)
  raise SystemExit(130)
 signal.signal(signal.SIGTERM,interrupted)
 def run(script,args,label):
  global CHILD
  report=Path(str(base)+'-'+label+'.json')
  CHILD=subprocess.Popen([sys.executable,str(ROOT/'scripts'/script),*args,'--report',str(report)],cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,start_new_session=True)
  try:stdout,stderr=CHILD.communicate(timeout=100)
  except subprocess.TimeoutExpired:
   os.killpg(CHILD.pid,signal.SIGTERM);CHILD.communicate(timeout=5);raise RuntimeError('material_stage_timeout')
  r=type('Result',(),{'returncode':CHILD.returncode,'stderr':stderr})()
  CHILD=None
  data=json.loads(report.read_text()) if report.exists() else {}
  result['steps'].append({'stage':label,'report':str(report),'exitCode':r.returncode,'errorCode':'account_busy' if 'BlockingIOError' in r.stderr else 'stage_failed' if r.returncode else None});a.report.write_text(json.dumps(result,indent=2))
  if r.returncode or data.get('error') or label in ('create','reconcile') and data.get('status') not in ('verified','reused','already_verified'):raise RuntimeError('material_stage_failed')
  return data
 try:
  with closing(sqlite3.connect((ROOT/'var/second-cycle.sqlite').as_uri()+'?mode=ro',uri=True)) as db, db:
   unresolved=db.execute("SELECT id FROM cycle_card_creation WHERE state IN ('started','response_saved','unknown') ORDER BY created_at LIMIT 1").fetchone()
  if unresolved:run('create-cycle-card.py',['verify-one','--id',unresolved[0]],'reconcile');result['state']='reconciliation_only'
  else:
   run('prepare-cycle-materials.py',['--generate-names','--check-cards'],'inspect')
   data=run('create-cycle-card.py',['prepare'],'prepare')
   candidates=[x for x in data.get('intents',[]) if x['state']=='prepared']
   if candidates:run('create-cycle-card.py',['execute-one','--id',candidates[0]['id']],'create')
   result['state']='completed'
 except Exception as e:result.update(state='waiting',error='material_stage_failed' if isinstance(e,RuntimeError) else type(e).__name__)
 a.report.write_text(json.dumps(result,indent=2));print(json.dumps({'state':result['state'],'steps':len(result['steps']),'error':result.get('error')}))
if __name__=='__main__':main()
