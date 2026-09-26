#!/usr/bin/env python3
"""Aggregate the operations home from durable local ledgers; GET never starts work."""
import argparse
import json
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1];sys.dont_write_bytecode=True;sys.path.insert(0,str(ROOT/'scripts'))
from lib.account_identity import status as account_status  # noqa:E402
from lib.continuous_send import status as continuous_status  # noqa:E402
from lib.jobs import status as jobs_status  # noqa:E402
from lib.operations_workflow import launch_setting,save_setting,status as workflow_status  # noqa:E402
from lib.second_cycle import CycleError,CycleStore  # noqa:E402
from lib.template_library import agent_setting,send_template_reviews  # noqa:E402

LABELS={'catalog':'货盘','taplink_prepare':'TapLink','kalodata':'Kalodata','oecid':'OECID','send_pool':'发送池'}

def _idle_continuous(store,market,account):
 from lib.market_send_control import status
 return status(ROOT,store,market)

def home(store,market='it'):
 workflow=workflow_status(store,market);current=workflow['current']
 all_accounts=account_status(store,ROOT);accounts={**all_accounts,
  'accounts':[row for row in all_accounts['accounts'] if row['market']==market],
  'queue':[row for row in all_accounts['queue'] if row['market']==market]}
 continuous=continuous_status(ROOT,store,include_preview=False) if market=='it' else _idle_continuous(store,
  market,next(row['account'] for row in accounts['accounts']
              if row['market']==market and row['role']=='communications'))
 jobs=jobs_status(ROOT,market);plan=store.db.execute("SELECT id FROM plan WHERE institution='bjn-local-research' AND market=?",(market,)).fetchone()[0]
 agent=agent_setting(store,plan);latest_agent=store.db.execute('SELECT * FROM agent_reply_run WHERE plan_id=? ORDER BY started_at DESC LIMIT 1',(plan,)).fetchone()
 by_stage={row['stage']:row for row in (current or {}).get('stages',[])}
 stages=[]
 for key,label in LABELS.items():
  row=by_stage.get(key);state=row['state'] if row else ('disabled' if not workflow['setting']['automaticOperationsEnabled'] else 'waiting_upstream')
  last=store.db.execute("""SELECT max(s.finished_at) FROM workflow_stage_run s JOIN workflow_run r ON r.run_id=s.run_id
    WHERE r.market=? AND s.stage=? AND s.state IN ('completed','quota_exhausted')""",(market,key)).fetchone()[0]
  stages.append({'id':key,'label':label,'state':state,'counts':row['counts'] if row else {},
   'processed':int((row['counts'] if row else {}).get('items') or 0),
   'lastSuccessAt':last,
   'nextAt':None,'checkpoint':row['checkpoint'] if row else {},'stopReason':row['errorCode'] if row else None,
   'platformWrites':row['platformWrites'] if row else 0,'generationId':row['outputGenerationId'] if row else None})
 stages.append({'id':'continuous_send','label':'持续二发','state':continuous['runtime']['state'],
  'counts':{'confirmedToday':continuous['runtime']['confirmedToday'],'failedKnown':continuous['runtime']['failedKnown'],'unknown':continuous['runtime']['unknown']},
  'processed':continuous['runtime']['confirmedToday'],'lastSuccessAt':continuous['runtime']['lastSuccessAt'],
  'nextAt':None,'checkpoint':{'deliveryId':continuous['runtime']['currentDeliveryId']},
  'stopReason':continuous['runtime']['stopReason'],'platformWrites':0,'generationId':None})
 agent_counts={key:int(latest_agent[key] or 0) for key in ('claimed','no_reply','prepared','human','confirmed','unknown')} if latest_agent else {}
 stages.append({'id':'agent_reply','label':'Agent 回复','state':'disabled' if not agent['enabled'] else latest_agent['state'] if latest_agent else 'queued',
  'counts':agent_counts,'processed':latest_agent['confirmed'] if latest_agent else 0,
  'lastSuccessAt':latest_agent['finished_at'] if latest_agent and latest_agent['state']=='completed' else None,
  'nextAt':None,'checkpoint':{},'stopReason':latest_agent['error'] if latest_agent else None,
  'platformWrites':0,'generationId':None})
 issues=[]
 template_review=send_template_reviews(store,ROOT,market)
 if not template_review['ready']:
  issues.append({'kind':'template','id':'send-template-review','title':'二发话术待审核',
   'reason':f"已通过 {template_review['approved']}/{template_review['total']}，至少需要 {template_review['minimumApproved']} 条"})
 for stage in stages:
  if stage['state'] in ('failed','needs_human','waiting_reconciliation','paused') and stage['stopReason'] not in (None,'send_pool_empty','outside_send_window'):
   issues.append({'kind':'stage','id':stage['id'],'title':stage['label'],'reason':stage['stopReason'] or stage['state']})
 if store.db.execute("SELECT 1 FROM sqlite_master WHERE name='inbox_checkpoint'").fetchone():
  old=store.db.execute('SELECT min(checked_at),count(*) FROM inbox_checkpoint WHERE plan_id=?',(plan,)).fetchone()
  if old[0] is not None and store.clock()-old[0]>300:
   issues.append({'kind':'inbox','id':f'{market}-inbox-lag','title':'收信覆盖落后',
                  'reason':f'{old[1]} 个会话中最旧检查已过去 {int((store.clock()-old[0])/60)} 分钟'})
  elif old[1]==0 and store.db.execute("SELECT 1 FROM cycle_delivery WHERE plan_id=? AND state='confirmed' LIMIT 1",(plan,)).fetchone():
   issues.append({'kind':'inbox','id':f'{market}-inbox-baseline','title':'收信尚未建立基线',
                  'reason':'已确认外发，但未见该市场的收信 checkpoint'})
 for row in accounts['queue']:
  if row['market']!=market:continue
  if row['state']=='needs_human':issues.append({'kind':'account','id':row['intentId'],'title':row['account'].upper(),'reason':row['errorCode']})
 if market!='it':
  assigned=[row for row in accounts['accounts'] if row['market']==market]
  verified={name for row in assigned for name,value in ((row.get('generation') or {}).get('capabilities') or {}).items()
            if value.get('state')=='verified'}
  required={'campaign','campaign_join','catalog_read','inbox_read','message_send','oecid_find','taplink'}
  from lib.market_registry import supports
  if supports(ROOT,market,'fullManagedCatalog'):required.update({'full_managed_catalog','product_select'})
  missing=sorted(required-verified)
  if missing:issues.append({'kind':'activation','id':f'{market}-activation','title':'市场接入尚未闭环',
   'reason':'待验证能力：'+', '.join(missing)})
 human=store.db.execute("SELECT count(*) FROM service_case WHERE plan_id=? AND state='open'",(plan,)).fetchone()[0] if store.db.execute("SELECT 1 FROM sqlite_master WHERE name='service_case'").fetchone() else 0
 if human:issues.append({'kind':'conversation','id':'human-conversations','title':'人工会话','reason':f'{human} 条待处理'})
 return {'schemaVersion':'bdhub.operations-home.v1','market':market,'setting':workflow['setting'],
  'workflow':{'runId':current['runId'] if current else None,'state':current['state'] if current else 'idle',
              'startedAt':current['startedAt'] if current else None,'finishedAt':current['finishedAt'] if current else None},
  'stages':stages,'issues':issues,'jobs':jobs,'accounts':accounts,'continuousSend':continuous,
  'agent':{'enabled':agent['enabled'],'revision':agent['revision'],'replyWindow':[agent['replyStart'],agent['replyEnd']]},
  'templateReview':template_review,
  'readOnly':True,'platformWrites':0,'realSends':0}

def main():
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('action',choices=('status','save'));parser.add_argument('--json');args=parser.parse_args()
 saved=None;launchable=None
 try:
  body=json.loads(args.json or '{}');market=body.get('market','it')
  with CycleStore(ROOT/'var/second-cycle.sqlite',readonly=args.action=='status') as store:
   if args.action=='save':
    saved=save_setting(store,market,body.get('requestId'),body.get('expectedRevision'),body.get('changes'))
    launchable=launch_setting(store,market,saved)
   try:result=home(store,market)
   except Exception as error:
    if saved is None:raise
    # The setting is committed: a failed re-read may not be reported as "not saved".
    result=None;refresh_error=str(error) if isinstance(error,CycleError) else type(error).__name__
  launch_errors=[]
  # A replay or a superseded commit starts nothing and never clears the scheduler stop file.
  if launchable and (launchable['automaticOperationsEnabled'] or launchable['continuousSendEnabled']):
   def attempt(name,action):
    try:action()
    except Exception as error:launch_errors.append({'worker':name,'error':str(error) if isinstance(error,(CycleError,ValueError)) else type(error).__name__})
   from lib.operations_scheduler import scheduler_state,start_scheduler
   attempt('scheduler',lambda:None if scheduler_state(ROOT)['running'] else start_scheduler(ROOT))
   if launchable['continuousSendEnabled']:
    if market=='it':
     from lib.continuous_send import launch_worker
     attempt('continuous_send',lambda:launch_worker(ROOT))
    else:
     from lib.market_send_worker import launch
     attempt('market_send',lambda:launch(ROOT,market))
  if args.action=='save':
   commit={'requestId':body.get('requestId'),'committed':True,'duplicate':bool(saved.get('duplicate')),
           'originalAvailable':saved.get('originalAvailable') is not False,
           'setting':{k:saved[k] for k in ('market','automaticOperationsEnabled','fullCatalogWeeklyEnabled','continuousSendEnabled','revision','updatedAt')},
           'launchErrors':launch_errors}
   if result is None:
    print(json.dumps({'error':'operations_home_refresh_failed','refreshError':refresh_error,'commit':commit},ensure_ascii=False));return 2
   result=result|{'commit':commit}
  print(json.dumps(result,ensure_ascii=False));return 0
 except (CycleError,ValueError,TypeError,json.JSONDecodeError) as error:
  print(json.dumps({'error':str(error),**({'commit':{'committed':True}} if saved is not None else {})},ensure_ascii=False));return 2

if __name__=='__main__':raise SystemExit(main())
