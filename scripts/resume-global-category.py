#!/usr/bin/env python3
"""Finish one existing category run using only its frozen scope and proven repair paths."""
import argparse,fcntl,json,os,re,subprocess,sys,time
from contextlib import closing
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.dont_write_bytecode=True
sys.path.insert(0,str(ROOT/'scripts'))
from lib.global_source import GlobalSources,GlobalSourceError


def _state(run_id):
 with closing(GlobalSources(ROOT/'var/global-source.sqlite',readonly=True)) as source:
  value=source.status(run_id)
  if value['market']!='it' or value.get('partitionMode')!='category_l1_v1':
   raise GlobalSourceError('category_resume_scope_invalid')
  return value


def _publish(value,action,returncode):
 path=ROOT/'var/it-category-completion.json';temp=path.with_suffix('.tmp')
 report={key:value.get(key) for key in ('id','state','products','pages','categoriesCompleted','categoryCount','reason','published')}
 report.update(action=action,returnCode=returncode,pid=os.getpid(),checkedAt=time.time(),platformWrites=0)
 temp.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n');temp.replace(path)
 print(json.dumps(report,ensure_ascii=False),flush=True)


def _call(run_id,action):
 args=[str(ROOT/'.venv/bin/python'),str(ROOT/'scripts/collect-global-opportunity.py'),
       '--market','it','--run-id',run_id,'--by-category','--pages','40']
 if action=='collect':args.append('--worker')
 elif action=='repair':args.append('--repair-partial-category')
 elif action=='accept':args.append('--accept-stable-duplicates')
 else:raise GlobalSourceError('category_resume_action_invalid')
 with (ROOT/'var/it-category-completion.log').open('a',encoding='utf-8') as log:
  child=subprocess.run(args,cwd=ROOT,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,
                       env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'})
 return child.returncode


def repair_decision(scope):
 if not scope['pages']:return 'total_drift_without_duplicate_pages'
 if scope['attempt']>4:return 'repair_exhausted'
 return 'repair'


def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--run-id',required=True);a=p.parse_args()
 if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',a.run_id):p.error('invalid run id')
 lock=ROOT/'var/it-category-completion.lock'
 with lock.open('a') as handle:
  try:fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
  except BlockingIOError:raise SystemExit('category_completion_busy')
  for _ in range(80):
   state=_state(a.run_id)
   if state['state']=='completed' and state['published']:
    _publish(state,'complete',0);return 0
   if state['state']=='collecting':
    action='collect'
   elif state['state']=='partial' and state['reason']=='category_endpoint_total_mismatch':
    with closing(GlobalSources(ROOT/'var/global-source.sqlite',readonly=True)) as source:
     decision=repair_decision(source.partial_repair_scope(a.run_id))
    if decision!='repair':
     _publish(state,decision,2);return 2
    action=decision
   else:
    _publish(state,'attention',2);return 2
   code=_call(a.run_id,action);after=_state(a.run_id);_publish(after,action,code)
   if code and after['state']=='collecting':time.sleep(60)
   if after['state']=='partial' and action=='repair':
    code=_call(a.run_id,'accept');after=_state(a.run_id);_publish(after,'accept',code)
   if after['state'] not in ('collecting','partial','completed'):
    return 2
  _publish(_state(a.run_id),'round_limit',2);return 2

if __name__=='__main__':raise SystemExit(main())
