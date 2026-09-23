"""Market scoped local controls and status for non IT continuous send workers."""
from __future__ import annotations

import json
import re
from pathlib import Path

from lib.lead_pool import pool
from lib.market_registry import require_operational
from lib.market_send_worker import launch, state as worker_state
from lib.second_cycle import CycleError, encoded
from lib.send_batch import NEW_CONTACT_LIMIT, window_state
from lib.template_library import require_send_template_approval, resolve_send_template, send_templates

REQUEST_ID=re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{7,119}")

def _plan(store,market):
 row=store.db.execute("SELECT id FROM plan WHERE institution='bjn-local-research' AND market=? AND state='active'",(market,)).fetchone()
 if not row:raise CycleError('plan_paused')
 return row[0]

def control(store,market):
 plan=_plan(store,market);row=store.db.execute('SELECT * FROM continuous_send_control WHERE plan_id=?',(plan,)).fetchone()
 if not row:return {'planId':plan,'automaticEnabled':False,'runRequested':False,'stopRequested':False,'window':['16:30','24:00'],'template':'standard','revision':0,'updatedAt':0}
 return {'planId':plan,'automaticEnabled':bool(row['automatic_enabled']),'runRequested':bool(row['run_requested']),'stopRequested':bool(row['stop_requested']),'window':[row['window_start'],row['window_end']],'template':row['template_id'],'revision':row['revision'],'updatedAt':row['updated_at']}

def _time(value):
 if not isinstance(value,str) or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d|24:00',value):raise CycleError('continuous_send_setting_invalid')
 return value

def _unknown_deliveries(store,plan,worker):
 active=bool(worker.get('running')) and worker.get('state') in ('starting','confirmed','sending')
 return [{'deliveryId':row['id'],'creatorId':row['creator_id'],'pid':row['pid']} for row in store.db.execute("""SELECT DISTINCT d.id,d.creator_id,d.pid FROM cycle_delivery d
 LEFT JOIN cycle_conversation_intent c ON c.delivery_id=d.id
 WHERE d.plan_id=? AND (d.state='unknown' OR EXISTS(
 SELECT 1 FROM cycle_delivery_part p WHERE p.delivery_id=d.id AND p.state='unknown') OR
 (?=0 AND (c.state IN ('inflight','received') OR EXISTS(
 SELECT 1 FROM cycle_delivery_part p WHERE p.delivery_id=d.id AND p.state IN ('inflight','accepted')))))
 ORDER BY d.created LIMIT 100""",(plan,int(active)))]

def mutate(store,root,market,*,action,request_id,expected_revision,changes=None):
 definition=require_operational(root,market)
 if definition['runtimeState']!='ready':raise CycleError('market_runtime_unavailable')
 if action not in ('save','start','stop') or not isinstance(request_id,str) or not REQUEST_ID.fullmatch(request_id) or type(expected_revision) is not int or expected_revision<0:raise CycleError('continuous_send_request_invalid')
 changes=changes or {};allowed={'automaticEnabled','window','template'}
 if market not in ('br','my','uk') and (action=='start' or changes.get('automaticEnabled') is True):
  raise CycleError('market_send_runtime_unavailable')
 if action=='save':
  if not changes or set(changes)-allowed:raise CycleError('continuous_send_setting_invalid')
  if 'automaticEnabled' in changes and type(changes['automaticEnabled']) is not bool:raise CycleError('continuous_send_setting_invalid')
  if 'window' in changes:
   if not isinstance(changes['window'],list) or len(changes['window'])!=2:raise CycleError('continuous_send_setting_invalid')
   changes['window']=[_time(value) for value in changes['window']]
  if 'template' in changes:resolve_send_template(store,changes['template'],market=market)
 elif changes:raise CycleError('continuous_send_request_invalid')
 if action=='start' or action=='save' and changes.get('automaticEnabled') is True:
  require_send_template_approval(store,root,market)
 payload=encoded({'market':market,'action':action,'changes':changes});current=control(store,market);plan=current['planId']
 with store.tx():
  prior=store.db.execute('SELECT * FROM continuous_send_control_request WHERE request_id=?',(request_id,)).fetchone()
  if prior:
   if prior['plan_id']!=plan or prior['payload_json']!=payload:raise CycleError('continuous_send_request_conflict')
   return control(store,market)|{'duplicate':True}
  current=control(store,market)
  if current['revision']!=expected_revision:raise CycleError('continuous_send_revision_conflict')
  value={**current,**changes};run=value['runRequested'];stop=value['stopRequested']
  if action=='start':run,stop=True,False
  elif action=='stop':run,stop=False,True
  elif action=='save' and changes.get('automaticEnabled') is False and not current['runRequested']:stop=True
  revision=expected_revision+1;now=store.clock()
  store.db.execute('''INSERT INTO continuous_send_control VALUES(?,?,?,?,?,?,?,?,?)
   ON CONFLICT(plan_id) DO UPDATE SET automatic_enabled=excluded.automatic_enabled,
   run_requested=excluded.run_requested,stop_requested=excluded.stop_requested,
   window_start=excluded.window_start,window_end=excluded.window_end,template_id=excluded.template_id,
   revision=excluded.revision,updated_at=excluded.updated_at''',(plan,int(value['automaticEnabled']),int(run),int(stop),value['window'][0],value['window'][1],value['template'],revision,now))
  store.db.execute('INSERT INTO continuous_send_control_request VALUES(?,?,?,?,?,?,?)',(request_id,plan,expected_revision,action,payload,revision,now))
 return control(store,market)|{'duplicate':False}

def status(root,store,market):
 root=Path(root);definition=require_operational(root,market);plan=_plan(store,market);cfg=control(store,market);now=store.clock();worker=worker_state(root,market)
 confirmed=store.db.execute("SELECT count(*) FROM cycle_delivery WHERE plan_id=? AND state='confirmed'",(plan,)).fetchone()[0]
 unknown=_unknown_deliveries(store,plan,worker)
 failed=store.db.execute("SELECT count(*) FROM cycle_delivery WHERE plan_id=? AND state IN ('rejected','failed_known')",(plan,)).fetchone()[0]
 from datetime import datetime,timedelta,timezone
 day_start=datetime.fromtimestamp(now,timezone(timedelta(hours=8))).replace(hour=0,minute=0,second=0,microsecond=0).timestamp()
 today=store.db.execute("""SELECT count(*) FROM cycle_delivery d JOIN cycle_delivery_part p ON p.delivery_id=d.id
 WHERE d.plan_id=? AND d.state='confirmed' AND p.kind='text' AND p.started>=?""",(plan,day_start)).fetchone()[0]
 last_success=store.db.execute("""SELECT max(p.started) FROM cycle_delivery d JOIN cycle_delivery_part p ON p.delivery_id=d.id
 WHERE d.plan_id=? AND d.state='confirmed' AND p.kind='text'""",(plan,)).fetchone()[0]
 recent_confirmed=store.db.execute("""SELECT count(*) FROM cycle_delivery d JOIN cycle_delivery_part p ON p.delivery_id=d.id
 WHERE d.plan_id=? AND d.state='confirmed' AND p.kind='text' AND p.started>=?""",(plan,now-300)).fetchone()[0]
 used=store.db.execute("""SELECT count(*) FROM (SELECT oec FROM cycle_contact_reservation WHERE plan_id=? AND reserved>?
 UNION SELECT d.oec FROM cycle_delivery d JOIN cycle_delivery_part p ON p.delivery_id=d.id
 WHERE d.plan_id=? AND p.kind='card' AND p.started>?)""",(plan,now-86400,plan,now-86400)).fetchone()[0]
 try:remaining=int((pool(root,market=market,now=now,limit=1).get('layers') or {}).get('ready') or 0)
 except Exception:remaining=None
 runtime={'state':'waiting_reconciliation' if unknown else 'sending' if worker.get('running') and worker.get('state')=='confirmed' else worker.get('state','off'),'currentDeliveryId':(worker.get('result') or {}).get('deliveryId'),'currentCreatorId':(worker.get('result') or {}).get('creatorId'),'currentPid':(worker.get('result') or {}).get('pid'),'confirmedToday':today,'confirmedTotal':confirmed,'failedKnown':failed,'unknown':len(unknown),'startedAt':worker.get('startedAt'),'seenAt':worker.get('checkedAt'),'lastSuccessAt':last_success,'stoppedAt':None,'stopReason':worker.get('error') or worker.get('reason'),'workerPid':worker.get('pid') if worker.get('running') else None,'speedPerMinute':round(recent_confirmed/5,2)}
 return {'schemaVersion':'bdhub.continuous-send.v1','market':market,'account':definition['accounts']['communications'],'control':cfg,'runtime':runtime,'window':window_state(cfg['window'],now),'capacity':{'windowSeconds':86400,'limit':NEW_CONTACT_LIMIT,'used':used,'remaining':max(0,NEW_CONTACT_LIMIT-used)},'poolRemaining':remaining,'sample':None,'unknownDeliveries':unknown,'templates':send_templates(store,market=market),'legacyBatchRetired':True,'platformWrites':0,'realSends':0}

def launch_worker(root,market):
 if market not in ('br','my','uk'):raise CycleError('market_send_runtime_unavailable')
 return launch(root,market)
