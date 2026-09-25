"""Market-scoped OECID/Profile resolution from current Kalodata lead evidence."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import time
from contextlib import closing
from datetime import datetime,timezone
from pathlib import Path

from lib.creator_identity import CreatorIdentityStore
from lib.market_accounts import load_config
from lib.second_cycle import CycleError,CycleStore,digest

INIT_RETRY_SECONDS=5.0
from lib.identity_retry import snapshot as retry_snapshot, record as retry_record, classify as classify_retry


def _plan(store,market):
 row=store.db.execute("SELECT id FROM plan WHERE institution='bjn-local-research' AND market=? AND state='active'",(market,)).fetchone()
 if not row:raise CycleError('plan_missing')
 return row[0]


def pending(root,market,limit=50):
 if type(limit) is not int or not 1<=limit<=500:raise ValueError('market_identity_limit_invalid')
 with CycleStore(Path(root)/'var/second-cycle.sqlite',readonly=True) as store:
  plan=_plan(store,market)
  policy=retry_snapshot(root,market)
  if policy['accountWait']:return {'market':market,'planId':plan,'items':[],'accountWait':policy['accountWait']}
  excluded=sorted(policy['isolated']|policy['deferred'])
  rows=store.db.execute("""SELECT x.source_id,x.source_handle,x.source_rank,x.pid
    FROM current_identity_source x
    LEFT JOIN cycle_identity_resolution r ON r.plan_id=x.plan_id AND r.source_id=x.source_id
    LEFT JOIN cycle_identity_outcome o ON o.plan_id=x.plan_id AND o.source_id=x.source_id
    WHERE x.plan_id=? AND r.source_id IS NULL AND (o.status IS NULL OR o.status NOT IN ('completed','unresolved'))
    AND lower(x.source_handle) NOT IN (SELECT value FROM json_each(?))
    GROUP BY lower(x.source_handle) ORDER BY min(x.source_rank),x.source_id LIMIT ?""",(plan,json.dumps(excluded),limit)).fetchall()
  seen=set();result=[]
  for row in rows:
   handle=str(row['source_handle']).lower()
   if handle in seen:continue
   seen.add(handle);result.append({'sourceId':row['source_id'],'handle':handle,'pid':row['pid']})
   if len(result)>=limit:break
  return {'market':market,'planId':plan,'items':result,'technicalIsolated':len(policy['isolated']),'deferred':len(policy['deferred'])}


def reuse_judgments(root,market):
 """Reuse exact market/handle terminal judgments for new A/B edges without a new Find."""
 with CycleStore(Path(root)/'var/second-cycle.sqlite') as store,store.tx():
  plan=_plan(store,market)
  current=store.db.execute("SELECT x.source_id,x.source_handle FROM current_identity_source x LEFT JOIN cycle_identity_outcome o ON o.plan_id=x.plan_id AND o.source_id=x.source_id WHERE x.plan_id=? AND (o.status IS NULL OR o.status NOT IN ('completed','unresolved'))",(plan,)).fetchall()
  judgments={}
  for row in store.db.execute("""SELECT x.source_handle,o.status,r.creator_id,r.oec,r.evidence_ref
    FROM source_edge_index x JOIN cycle_identity_outcome o ON o.plan_id=x.plan_id AND o.source_id=x.source_id
    LEFT JOIN cycle_identity_resolution r ON r.plan_id=x.plan_id AND r.source_id=x.source_id
    WHERE x.plan_id=? AND o.status IN ('completed','unresolved')""",(plan,)):
   key=row['source_handle'].lower();prior=judgments.get(key)
   if row['status']=='completed' and row['creator_id'] and row['evidence_ref']:
    if prior and prior['status']=='completed' and (prior['creator_id'],prior['oec'])!=(row['creator_id'],row['oec']):raise CycleError('identity_conflict')
    judgments[key]=row
   elif prior is None:judgments[key]=row
  for row in current:
   proof=judgments.get(row['source_handle'].lower())
   if proof is None:continue
   if proof['status']=='completed':
    if not proof['creator_id'] or not proof['evidence_ref']:continue
    store.db.execute('INSERT OR IGNORE INTO cycle_identity_resolution VALUES(?,?,?,?,?)',
                     (plan,row['source_id'],proof['creator_id'],proof['oec'],proof['evidence_ref']))
   store.db.execute('INSERT INTO cycle_identity_outcome VALUES(?,?,?) ON CONFLICT(plan_id,source_id) DO UPDATE SET status=excluded.status',
                    (plan,row['source_id'],proof['status']))


def _receipt(report,target_ref):
 row=next((item for item in report.get('requests',[]) if item.get('targetRef')==target_ref and item.get('stage')=='find'),None)
 if not row or row.get('status')!='returned' or row.get('httpStatus')!=200 or row.get('code')!='0' or row.get('verificationRequired') is not False:
  raise CycleError('market_identity_find_receipt_invalid')
 return row


def _followup_blocked(report,target_ref):
 """Whether a confirmed Find target was later blocked by Profile or another read stage."""
 for row in report.get('requests',[]):
  if not isinstance(row,dict) or row.get('targetRef')!=target_ref or row.get('stage')=='find':continue
  if row.get('status')!='returned' or row.get('httpStatus')!=200 or row.get('code')!='0' or \
    row.get('verificationRequired') is not False:
   return True
 return False


def validated_report_targets(report,requested,market,account):
 """Keep confirmed prefix results from a blocked cohort and leave every other intent pending."""
 if report.get('market')!=market or report.get('account')!=account or report.get('status') not in {'completed','blocked'} or \
   report.get('identityFileUnchanged') is not True or report.get('oldDatabaseWrites')!=0 or report.get('realSends')!=0:
  raise CycleError('market_identity_report_invalid')
 indexed={row['ref']:row for row in requested};targets=report.get('targets') or []
 if not isinstance(targets,list):raise CycleError('market_identity_report_invalid')
 target_refs=[row.get('targetRef') for row in targets if isinstance(row,dict)]
 if len(target_refs)!=len(targets) or len(set(target_refs))!=len(target_refs) or not set(target_refs)<=set(indexed):
  raise CycleError('market_identity_report_invalid')
 if report.get('status')=='completed' and set(target_refs)!=set(indexed):
  raise CycleError('market_identity_report_invalid')
 valid=[];blocked=set(indexed)-set(target_refs)
 for target in targets:
  ref=target['targetRef'];source=indexed[ref]
  try:receipt=_receipt(report,ref)
  except CycleError:
   if report.get('status')!='blocked':raise
   blocked.add(ref);continue
  if target.get('status')=='unresolved' and target.get('reason')=='no_exact_handle':
   valid.append((target,source,receipt));continue
  find=target.get('find') or {};identity=find.get('identity') or {};returned=str(identity.get('handle') or '').lower();oec=str(identity.get('oecId') or '')
  if target.get('status') not in {'identity_verified','completed',None} or target.get('currentHandleResolved') is not True or \
    returned!=source['handle'] or identity.get('market') not in (None,market) or not oec.isdigit():
   if report.get('status')!='blocked':raise CycleError('market_identity_report_invalid')
   blocked.add(ref);continue
  valid.append((target,source,receipt))
  # A profile canary may prove Find/OECID first and then receive a known non-zero response from the
  # Profile endpoint.  Preserve the confirmed Find result, but keep the same target visibly blocked
  # for the profile capability.  Treating this as an invalid report discarded valid OECID evidence
  # and forced the next run to repeat the Find request.
  if report.get('status')=='blocked' and _followup_blocked(report,ref):blocked.add(ref)
 if report.get('status')=='blocked' and not blocked:raise CycleError('market_identity_report_invalid')
 blocked_codes={str(row.get('code')) for row in report.get('requests',[]) if isinstance(row,dict) and row.get('targetRef') in blocked}
 code='market_identity_auth_required' if '16201010' in blocked_codes else 'market_identity_blocked'
 return valid,blocked,code


def _apply(root,market,report,requested):
 account=load_config(root)['markets'][market]['roles']['communications']
 valid,blocked,block_code=validated_report_targets(report,requested,market,account)
 observed=report.get('finishedAt') or datetime.now(timezone.utc).isoformat();resolved={};unresolved=set()
 with CreatorIdentityStore(Path(root)/'var/creator-identities.sqlite') as identities:
  for target,source,receipt in valid:
   ref=target['targetRef'];handle=source['handle']
   if target.get('status')=='unresolved' and target.get('reason')=='no_exact_handle':
    unresolved.add(handle);continue
   if target.get('status') not in {'identity_verified','completed',None} or target.get('currentHandleResolved') is not True:
    raise CycleError('market_identity_report_invalid')
   find=target.get('find') or {};identity=find.get('identity') or {};returned=str(identity.get('handle') or '').lower();oec=str(identity.get('oecId') or '')
   if returned!=handle or identity.get('market') not in (None,market) or not oec.isdigit():
    raise CycleError('market_identity_report_invalid')
   evidence='market-profile-probe:'+hashlib.sha256(json.dumps(report,sort_keys=True,ensure_ascii=False).encode()).hexdigest()+':'+ref
   lead=identities.record_handle_lead(market,handle,observed,evidence+':lead',external_source='kalodata_source',external_id=source['sourceId'],payload={'pid':source['pid']})
   outcome=identities.resolve_handle_lead(lead['leadId'],oec,observed,evidence+':find',exact_find={
    'market':market,'queriedHandle':handle,'returnedHandle':returned,'oecId':oec,
    'httpStatus':receipt['httpStatus'],'code':0,'verificationRequired':False})
   resolved[handle]=(outcome['identity']['creatorId'],oec,evidence+':find')
 with CycleStore(Path(root)/'var/second-cycle.sqlite') as store:
  plan=_plan(store,market);bound=0;missed=0;bound_ids=[]
  with store.tx():
   for handle in unresolved:
    for row in store.db.execute("SELECT source_id FROM source_edge_index WHERE plan_id=? AND source_handle=?",(plan,handle)).fetchall():
     store.db.execute("INSERT INTO cycle_identity_outcome VALUES(?,?,'unresolved') ON CONFLICT(plan_id,source_id) DO UPDATE SET status='unresolved'",(plan,row[0]));missed+=1
   for handle,(creator,oec,evidence) in resolved.items():
    relationship=store.db.execute('SELECT * FROM relationship WHERE plan_id=? AND (creator_id=? OR oec=?)',(plan,creator,oec)).fetchone()
    if relationship and (relationship['creator_id']!=creator or relationship['oec']!=oec):raise CycleError('identity_conflict')
    store.db.execute('INSERT OR IGNORE INTO relationship(plan_id,creator_id,oec) VALUES(?,?,?)',(plan,creator,oec))
    for row in store.db.execute("SELECT source_id FROM source_edge_index WHERE plan_id=? AND source_handle=?",(plan,handle)).fetchall():
     old=store.db.execute('SELECT creator_id,oec FROM cycle_identity_resolution WHERE plan_id=? AND source_id=?',(plan,row[0])).fetchone()
     if old and (old['creator_id']!=creator or old['oec']!=oec):raise CycleError('resolution_conflict')
     store.db.execute('INSERT OR IGNORE INTO cycle_identity_resolution VALUES(?,?,?,?,?)',(plan,row[0],creator,oec,evidence))
     store.db.execute("INSERT INTO cycle_identity_outcome VALUES(?,?,'completed') ON CONFLICT(plan_id,source_id) DO UPDATE SET status='completed'",(plan,row[0]));bound+=not bool(old)
     if not old:bound_ids.append(row[0])
  if bound_ids:store.project_current_offers(plan,source_ids=bound_ids)
 return {'resolvedHandles':len(resolved),'unresolvedHandles':len(unresolved),'newBindings':bound,'unresolvedEdges':missed,
         'profileVerified':sum(target.get('status')=='completed' for target,_,_ in valid),
         'blockedHandles':len(blocked),'blockCode':block_code if blocked else None}


def _probe_chunk(root,market,account,chunk,payload_targets,token,runner,profile_canary):
 folder=root/f'var/market-identity-{market}-{token}';targets=folder/'targets.private.json';output=folder/'output'
 folder.mkdir(parents=True,exist_ok=False,mode=0o700)
 payload={'market':market,'identityOnly':not profile_canary,
          **({'profileTypeSets':[[2]]} if profile_canary else {}),'targets':payload_targets}
 targets.write_text(json.dumps(payload,ensure_ascii=False));targets.chmod(0o600)
 child=runner([str(root/'.venv/bin/python'),str(root/'scripts/probe-italy-profile.py'),'--market',market,'--account',account,
               '--targets',str(targets),'--output',str(output)],cwd=str(root),capture_output=True,text=True,timeout=200)
 evidence=output/'report.private.json'
 if not evidence.exists():raise CycleError('market_identity_probe_missing')
 private=json.loads(evidence.read_text(encoding='utf-8'))
 return child,evidence,private


def _initialization_failed(private):
 return private.get('status')=='blocked' and private.get('reason')=='probe_initialization_or_validation_error' and not private.get('requests')


def _probe_with_init_retry(root,market,account,chunk,payload_targets,tokens,runner,profile_canary,sleep):
 child,evidence,private=_probe_chunk(root,market,account,chunk,payload_targets,tokens[0],runner,profile_canary)
 evidence_paths=[evidence];retried=False
 if _initialization_failed(private):
  sleep(INIT_RETRY_SECONDS)
  child,evidence,private=_probe_chunk(root,market,account,chunk,payload_targets,tokens[1],runner,profile_canary)
  evidence_paths.append(evidence);retried=True
 return child,evidence_paths,private,retried


def run(root,market,limit=3,*,profile_canary=False,runner=subprocess.run,clock=time.time,sleep=time.sleep):
 from lib.identity_retry import locked
 with locked(root,market):
  return _run(root,market,limit,profile_canary=profile_canary,runner=runner,clock=clock,sleep=sleep)


def _run(root,market,limit=3,*,profile_canary=False,runner=subprocess.run,clock=time.time,sleep=time.sleep):
 root=Path(root)
 if not retry_snapshot(root,market)['enabled']:raise CycleError('identity_retry_schema_required')
 from lib.identity_retry import recover_started,project_isolated
 account=load_config(root)['markets'][market]['roles']['communications']
 recover_started(root,market,account,_apply)
 reuse_judgments(root,market);project_isolated(root,market);scope=pending(root,market,limit);items=scope['items']
 report={'market':market,'targets':len(items),'resolvedHandles':0,'unresolvedHandles':0,'newBindings':0,
         'networkRuns':0,'platformWrites':0,'realSends':0,'profileCanary':bool(profile_canary),'profileVerified':0,
         'blockedHandles':0,'initRetries':0,'evidence':[],'sliceComplete':True,'coverage':'bounded_identity_slice'}
 if not items:return report|{'stopped':'account_wait' if scope.get('accountWait') else 'nothing_pending','queue':scope}
 account=load_config(root)['markets'][market]['roles']['communications']
 for offset in range(0,len(items),3):
  if retry_snapshot(root,market,now=clock())['accountWait']:break
  chunk=items[offset:offset+3];token=digest([market,clock(),offset,chunk])[:16]
  payload_targets=[{'ref':'market_identity_'+digest([market,row['sourceId']])[:24],'handle':row['handle'],'externalId':row['sourceId']} for row in chunk]
  requested=[{'ref':row['ref'],**source} for row,source in zip(payload_targets,chunk)]
  tokens=[token,digest([market,clock(),offset,chunk,'init-retry'])[:16]]
  for row in chunk:retry_record(root,market,account,row['handle'],token+':started','started',None,evidence={
   'requested':requested,'report':f'var/market-identity-{market}-{tokens[0]}/output/report.private.json',
   'retryReport':f'var/market-identity-{market}-{tokens[1]}/output/report.private.json'},now=clock())
  try:
   child,evidence_paths,private,retried=_probe_with_init_retry(root,market,account,chunk,payload_targets,tokens,runner,profile_canary,sleep)
   applied=_apply(root,market,private,requested)
  except (CycleError,subprocess.TimeoutExpired,ValueError,OSError) as error:
   for row in chunk:retry_record(root,market,account,row['handle'],token,'shared',getattr(error,'code',None) or type(error).__name__,now=clock())
   report['accountWait']=retry_snapshot(root,market,now=clock())['accountWait'];break
  report['networkRuns']+=1;report['resolvedHandles']+=applied['resolvedHandles'];report['unresolvedHandles']+=applied['unresolvedHandles'];report['newBindings']+=applied['newBindings'];report['profileVerified']+=applied['profileVerified']
  report['evidence'].extend(str(path.relative_to(root)) for path in evidence_paths)
  report['initRetries']+=retried;report['blockedHandles']+=applied['blockedHandles']
  for row in requested:
   category,reason=classify_retry(private,row['ref'],expected_handle=row['handle'],market=market)
   retry_record(root,market,account,row['handle'],token,category,reason,evidence=str(evidence_paths[-1].relative_to(root)),now=clock())
  if applied['blockCode']=='market_identity_auth_required':
   report['authRequired']=True;report['stopped']='market_identity_auth_required';break
  if child.returncode not in (0,2):
   retry_record(root,market,account,chunk[0]['handle'],token+':exit','shared','market_identity_probe_failed',now=clock());break
 project_isolated(root,market)
 report['queue']=pending(root,market,1)
 return report
