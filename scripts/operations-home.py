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
from lib.operations_workflow import save_setting,status as workflow_status  # noqa:E402
from lib.second_cycle import CycleError,CycleStore  # noqa:E402
from lib.template_library import agent_setting,send_template_reviews  # noqa:E402

LABELS={'catalog':'货盘','taplink_prepare':'TapLink','kalodata':'Kalodata','oecid':'OECID','send_pool':'发送池'}

def _idle_continuous(store,market,account):
 from lib.market_send_worker import state as worker_state
 from lib.operations_workflow import setting
 control=setting(store,market);worker=worker_state(ROOT,market)
 plan=store.db.execute("SELECT id FROM plan WHERE institution='bjn-local-research' AND market=?",(market,)).fetchone()
 confirmed=store.db.execute("SELECT count(*) FROM cycle_delivery WHERE plan_id=? AND state='confirmed'",(plan[0],)).fetchone()[0] if plan else 0
 unknown=store.db.execute("SELECT count(*) FROM cycle_delivery WHERE plan_id=? AND state='unknown'",(plan[0],)).fetchone()[0] if plan else 0
 return {'schemaVersion':'bdhub.continuous-send.v1','market':market,'account':account,
  'control':{'automaticEnabled':control['continuousSendEnabled'],'runRequested':False,'stopRequested':False,'revision':control['revision']},
  'runtime':{'state':worker.get('state','off'),'currentDeliveryId':(worker.get('result') or {}).get('deliveryId'),'confirmedToday':confirmed,'failedKnown':0,'unknown':unknown,
             'lastSuccessAt':worker.get('checkedAt') if confirmed else None,'stopReason':worker.get('error'),'speedPerMinute':0},
  'poolRemaining':0,'unknownDeliveries':[],'legacyBatchRetired':True,'platformWrites':0,'realSends':0}

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
  stages.append({'id':key,'label':label,'state':state,'counts':row['counts'] if row else {},
   'processed':sum(value for value in (row['counts'] if row else {}).values() if isinstance(value,int)),
   'lastSuccessAt':row['finishedAt'] if row and row['state'] in ('completed','quota_exhausted') else None,
   'nextAt':None,'checkpoint':row['checkpoint'] if row else {},'stopReason':row['errorCode'] if row else None,
   'platformWrites':row['platformWrites'] if row else 0,'generationId':row['outputGenerationId'] if row else None})
 stages.append({'id':'continuous_send','label':'持续二发','state':continuous['runtime']['state'],
  'counts':{'confirmedToday':continuous['runtime']['confirmedToday'],'failedKnown':continuous['runtime']['failedKnown'],'unknown':continuous['runtime']['unknown']},
  'processed':continuous['runtime']['confirmedToday'],'lastSuccessAt':continuous['runtime']['lastSuccessAt'],
  'nextAt':None,'checkpoint':{'deliveryId':continuous['runtime']['currentDeliveryId']},
  'stopReason':continuous['runtime']['stopReason'],'platformWrites':0,'generationId':None})
 stages.append({'id':'agent_reply','label':'Agent 回复','state':latest_agent['state'] if latest_agent else ('disabled' if not agent['enabled'] else 'queued'),
  'counts':dict(latest_agent) if latest_agent else {},'processed':latest_agent['confirmed'] if latest_agent else 0,
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
 try:
  body=json.loads(args.json or '{}');market=body.get('market','it')
  with CycleStore(ROOT/'var/second-cycle.sqlite',readonly=args.action=='status') as store:
   if args.action=='save':saved=save_setting(store,market,body.get('requestId'),body.get('expectedRevision'),body.get('changes'))
   result=home(store,market)
  if args.action=='save' and (saved['automaticOperationsEnabled'] or saved['continuousSendEnabled']):
   from lib.operations_scheduler import scheduler_state,start_scheduler
   if not scheduler_state(ROOT)['running']:start_scheduler(ROOT)
   if saved['continuousSendEnabled']:
    if market=='it':
     from lib.continuous_send import launch_worker
     launch_worker(ROOT)
    else:
     from lib.market_send_worker import launch
     launch(ROOT,market)
  print(json.dumps(result,ensure_ascii=False));return 0
 except (CycleError,ValueError,TypeError,json.JSONDecodeError) as error:
  print(json.dumps({'error':str(error)},ensure_ascii=False));return 2

if __name__=='__main__':raise SystemExit(main())
