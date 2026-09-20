"""Continuous second outreach backed by immutable ``cycle_delivery`` intents.

There is no user batch and no hidden reuse of ``cycle_bulk*``.  Every claimed creator is fully
revalidated, its final card/text/control revisions are frozen into one delivery snapshot, and only
that delivery may execute or be reconciled.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import sqlite3
import subprocess
import time
from contextlib import closing, contextmanager
from dataclasses import asdict
from pathlib import Path

from lib.cycle_delivery import Deliveries
from lib.cycle_executor import execute
from lib.cycle_review import choose_candidates
from lib.cycle_send_runtime import descriptor
from lib.lead_pool import pool
from lib.second_cycle import CycleError, digest, encoded
from lib.send_batch import NEW_CONTACT_LIMIT, capacity, load_config, window_state, _window_arg
from lib.template_library import render_send_template, resolve_send_template, send_templates


ROOT = Path(__file__).resolve().parents[2]
REQUEST_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{7,119}")
ACTIVE_RUNTIME = {"waiting_window", "sending", "waiting_capacity", "waiting_reconciliation", "paused"}
_spec = importlib.util.spec_from_file_location('continuous_send_history', ROOT/'scripts/cycle-send.py')
_history = importlib.util.module_from_spec(_spec);_spec.loader.exec_module(_history)


def _required(store):
    required={"continuous_send_control","continuous_send_control_request","continuous_send_runtime",
              "cycle_delivery","cycle_delivery_part"}
    present={row[0] for row in store.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not required<=present:raise CycleError('continuous_send_schema_migration_required')


def _plan(store):
    row=store.db.execute("SELECT id FROM plan WHERE institution='bjn-local-research' AND market='it' AND state='active'").fetchone()
    if not row:raise CycleError('plan_paused')
    return row[0]


def control(store,root):
    _required(store);plan=_plan(store);row=store.db.execute('SELECT * FROM continuous_send_control WHERE plan_id=?',(plan,)).fetchone()
    if not row:
        config=load_config(root)
        return {'planId':plan,'automaticEnabled':False,'runRequested':False,'stopRequested':False,
                'window':[config['window'][0],config['window'][1]],'template':config['template'],
                'revision':0,'updatedAt':0}
    return {'planId':plan,'automaticEnabled':bool(row['automatic_enabled']),
            'runRequested':bool(row['run_requested']),'stopRequested':bool(row['stop_requested']),
            'window':[row['window_start'],row['window_end']],'template':row['template_id'],
            'revision':row['revision'],'updatedAt':row['updated_at']}


def _request(value):
    if not isinstance(value,str) or not REQUEST_ID.fullmatch(value):raise CycleError('continuous_send_request_invalid')
    return value


def mutate_control(store,root,*,action,request_id,expected_revision,changes=None):
    _required(store);request_id=_request(request_id)
    if action not in {'save','start','stop'} or type(expected_revision) is not int or expected_revision<0:
        raise CycleError('continuous_send_request_invalid')
    current=control(store,root);changes=changes or {};allowed={'automaticEnabled','window','template'}
    if action=='save':
        if not changes or set(changes)-allowed:raise CycleError('continuous_send_setting_invalid')
        if 'automaticEnabled' in changes and type(changes['automaticEnabled']) is not bool:raise CycleError('continuous_send_setting_invalid')
        if 'window' in changes:changes['window']=list(_window_arg(changes['window']))
        if 'template' in changes:
            resolve_send_template(store,changes['template'])
    elif changes:raise CycleError('continuous_send_request_invalid')
    payload=encoded({'action':action,'changes':changes})
    plan=current['planId']
    with store.tx():
        prior=store.db.execute('SELECT * FROM continuous_send_control_request WHERE request_id=?',(request_id,)).fetchone()
        if prior:
            if prior['payload_json']!=payload:raise CycleError('continuous_send_request_conflict')
            return control(store,root)|{'duplicate':True}
        current=control(store,root)
        if current['revision']!=expected_revision:raise CycleError('continuous_send_revision_conflict')
        value={**current,**changes};run=value['runRequested'];stop=value['stopRequested']
        if action=='start':run,stop=True,False
        elif action=='stop':run,stop=False,True
        elif action=='save' and changes.get('automaticEnabled') is False and not current['runRequested']:stop=True
        revision=expected_revision+1;now=store.clock()
        store.db.execute('''INSERT INTO continuous_send_control VALUES(?,?,?,?,?,?,?,?,?)
          ON CONFLICT(plan_id) DO UPDATE SET automatic_enabled=excluded.automatic_enabled,
          run_requested=excluded.run_requested,stop_requested=excluded.stop_requested,
          window_start=excluded.window_start,window_end=excluded.window_end,
          template_id=excluded.template_id,revision=excluded.revision,updated_at=excluded.updated_at''',
          (plan,int(value['automaticEnabled']),int(run),int(stop),value['window'][0],value['window'][1],
           value['template'],revision,now))
        store.db.execute('INSERT INTO continuous_send_control_request VALUES(?,?,?,?,?,?,?)',
          (request_id,plan,expected_revision,action,payload,revision,now))
    return control(store,root)|{'duplicate':False}


def _runtime(store,plan):
    row=store.db.execute('SELECT * FROM continuous_send_runtime WHERE plan_id=?',(plan,)).fetchone()
    if not row:return {'state':'off','currentDeliveryId':None,'currentCreatorId':None,'currentPid':None,
      'confirmedToday':0,'failedKnown':0,'unknown':0,'startedAt':None,'seenAt':None,
      'lastSuccessAt':None,'stoppedAt':None,'stopReason':None,'workerPid':None}
    return {'state':row['state'],'currentDeliveryId':row['current_delivery_id'],
      'currentCreatorId':row['current_creator_id'],'currentPid':row['current_pid'],
      'confirmedToday':row['confirmed_today'],'failedKnown':row['failed_known'],'unknown':row['unknown'],
      'startedAt':row['started_at'],'seenAt':row['seen_at'],'lastSuccessAt':row['last_success_at'],
      'stoppedAt':row['stopped_at'],'stopReason':row['stop_reason'],'workerPid':row['worker_pid']}


def publish_runtime(store,plan,state,*,delivery=None,stop_reason=None,confirmed_delta=0,failed_delta=0,
                    unknown=None,worker_pid=None):
    now=store.clock();creator=delivery.get('creator_id') if delivery else None;pid=delivery.get('pid') if delivery else None
    did=delivery.get('id') if delivery else None
    store.db.execute('''INSERT INTO continuous_send_runtime VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
      ON CONFLICT(plan_id) DO UPDATE SET state=excluded.state,
      current_delivery_id=excluded.current_delivery_id,current_creator_id=excluded.current_creator_id,
      current_pid=excluded.current_pid,confirmed_today=continuous_send_runtime.confirmed_today+?,
      failed_known=continuous_send_runtime.failed_known+?,unknown=COALESCE(?,continuous_send_runtime.unknown),
      seen_at=excluded.seen_at,last_success_at=CASE WHEN ?>0 THEN excluded.seen_at ELSE continuous_send_runtime.last_success_at END,
      stopped_at=excluded.stopped_at,stop_reason=excluded.stop_reason,worker_pid=COALESCE(excluded.worker_pid,continuous_send_runtime.worker_pid)''',
      (plan,state,did,creator,pid,confirmed_delta,failed_delta,int(unknown or 0),now,now,
       now if confirmed_delta else None,now if state in ('stopped','off') else None,stop_reason,worker_pid,
       confirmed_delta,failed_delta,unknown,confirmed_delta))
    return _runtime(store,plan)


def _identities(root):
    return sqlite3.connect((Path(root)/'var/creator-identities.sqlite').resolve().as_uri()+'?mode=ro',uri=True)


def _candidate(root,store,plan,control_value):
    state=pool(root,now=store.clock(),limit=200)
    positions=[(row['creatorId'],row['pid']) for row in (state.get('pools') or {}).get('ready',[])]
    if not positions:return None,state
    with closing(_identities(root)) as ids:
        def person(creator,oec):
            row=ids.execute("SELECT current_handle FROM creator_identity WHERE market='it' AND creator_id=? AND oec_id=? AND handle_conflict=0",(creator,oec)).fetchone()
            return {'handle':row[0]} if row else None
        candidates,_=choose_candidates(store,plan,person,len(positions),positions=positions)
    if not candidates:return None,state
    candidate=json.loads(encoded(candidates[0]));spec=resolve_send_template(store,control_value['template'])
    candidate['message']=render_send_template(spec,candidate['name'],candidate['offer'],candidate['handle'])
    candidate['message'].setdefault('templateRevision',spec['revision'])
    candidate['executionMode']='continuous-v1';candidate['continuousControlRevision']=control_value['revision']
    candidate['continuousClaimKey']=digest([plan,candidate['creatorId'],candidate['pid'],candidate['source']['sourceId'],control_value['revision']])
    with closing(sqlite3.connect((Path(root)/'var/it-conversations.sqlite').resolve().as_uri()+'?mode=ro',uri=True)) as idx:
        rows=idx.execute("SELECT cid FROM conversation WHERE scope='it:acc6' AND oec=? AND kind=2",(candidate['oecId'],)).fetchall()
    if len(rows)>1:raise CycleError('ambiguous_conversation')
    candidate['conversationId']=rows[0][0] if rows else None
    return candidate,state


def _local_card(root,store,plan,candidate):
    current={offer['offerKey']:offer for _,offer in store._offers(plan)};offer=current.get(candidate['offer']['offerKey'])
    if not offer or digest(offer)!=candidate['offerFingerprint']:raise CycleError('material_stale')
    from lib.catalog_binding import offer_fingerprint
    path=Path(root)/'var/catalog-links.sqlite'
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as links:
        links.row_factory=sqlite3.Row
        row=links.execute("SELECT offer_fingerprint,list_id,state FROM catalog_current_binding WHERE market='it' AND catalog_source=? AND pid=? AND campaign_id=?",
          (offer.get('catalogSource'),str(offer['pid']),str(offer.get('campaignId') or ''))).fetchone()
    # ``candidate.offerFingerprint`` is the immutable full-offer digest used by cycle_review;
    # ``catalog_current_binding.offer_fingerprint`` is the six-field material fingerprint.  They
    # intentionally prove different contracts and must not be compared to each other.
    if not row or row['state']!='active' or row['offer_fingerprint']!=offer_fingerprint(offer) or \
       str(row['list_id'])!=str(candidate['card']['listId']):
        raise CycleError('material_stale')
    return descriptor(candidate['card'])


def _active_delivery(store,plan):
    row=store.db.execute("SELECT * FROM cycle_delivery WHERE plan_id=? AND state IN ('ready','running','unknown') AND json_extract(snapshot,'$.executionMode')='continuous-v1' ORDER BY created LIMIT 1",(plan,)).fetchone()
    return dict(row) if row else None


def _legacy_batch_active(store):
    return bool(store.db.execute("SELECT 1 FROM cycle_bulk_freeze WHERE state IN ('starting','running','stop_requested','waiting_reconciliation')").fetchone()) if store.db.execute("SELECT 1 FROM sqlite_master WHERE name='cycle_bulk_freeze'").fetchone() else False


def execute_once(root,store,*,authenticated=None,authorized_now=None):
    """Advance at most one delivery. Tests can inject an authenticated context; status calls never enter."""
    root=Path(root);_required(store);plan=_plan(store);cfg=control(store,root);now=store.clock()
    if authorized_now is not None:_request(authorized_now)
    if cfg['stopRequested'] or not (cfg['runRequested'] or cfg['automaticEnabled']):
        return publish_runtime(store,plan,'off',stop_reason='disabled')
    window=window_state(cfg['window'],now)
    if not window['open'] and authorized_now is None:
        return publish_runtime(store,plan,'waiting_window',stop_reason='outside_send_window')
    if _legacy_batch_active(store):return publish_runtime(store,plan,'paused',stop_reason='legacy_batch_active')
    active=_active_delivery(store,plan);candidate=None
    if active:
        delivery=Deliveries(store).get(active['id']);candidate=delivery['snapshot']
        if candidate.get('authorizedNowRequestId')!=authorized_now:
            raise CycleError('continuous_send_authorization_changed')
    else:
        candidate,pool_state=_candidate(root,store,plan,cfg)
        if candidate is None:return publish_runtime(store,plan,'paused',stop_reason='send_pool_empty')
        if authorized_now is not None:candidate['authorizedNowRequestId']=authorized_now
    from lib.second_live_runtime import _authenticated,live_runtime,sender_binding_sha256
    transport_report={} if authorized_now is None else {'authorizedSendRequestId':authorized_now}
    auth_context=authenticated or _authenticated(transport_report,stopped=lambda:control(store,root)['stopRequested'])
    with auth_context as context:
        account,identity,headers,auth,maintenance,available=context
        if active is None:
            card=_local_card(root,store,plan,candidate);candidate['nativeCard']=asdict(card)
            candidate['senderBindingHash']=sender_binding_sha256(auth)
            delivery=Deliveries(store).prepare(plan,candidate);active={'id':delivery['id'],'creator_id':delivery['creator_id'],'pid':delivery['pid']}
        else:delivery=Deliveries(store).get(active['id'])
        deliveries=Deliveries(store);publish_runtime(store,plan,'sending',delivery=active,worker_pid=os.getpid())
        @contextmanager
        def runtime(c,read_only=False):
            previous=descriptor(c['card'])
            def validator(*_):
                current=_local_card(root,store,plan,c)
                if current.binding_sha256!=previous.binding_sha256:raise CycleError('card_binding_changed')
                return current
            # Reuse the exact report populated by _authenticated: it carries the verified market
            # send capability.  A fresh empty dict would erase that proof and make every real send
            # fail closed as live_market_send_unavailable after authentication succeeded.
            with live_runtime(c['senderBindingHash'],transport_report,authenticated_context=context,card_validator=validator,
                              stopped=lambda:control(store,root)['stopRequested'],send_interval=0.75) as rt:
                yield {**rt,'card':previous}
        def authorize(c):
            current=control(store,root)
            if current['stopRequested'] or not (current['runRequested'] or current['automaticEnabled']):raise CycleError('continuous_send_stopped')
            if c.get('continuousControlRevision')>current['revision'] or c.get('executionMode')!='continuous-v1':raise CycleError('continuous_send_scope_changed')
            if c.get('authorizedNowRequestId')!=authorized_now:raise CycleError('continuous_send_authorization_changed')
        def preflight(c,rt,conversation):
            history=rt['reads'].history_summary(conversation,include_sender_counts=True,include_events=True,include_contents=True)
            from lib.cycle_inbox import Inbox
            from lib.cycle_service import Service
            Inbox(store).ingest(plan,conversation.conversation_id,c['oecId'],history)
            Service(store).capture(plan,conversation.conversation_id,c['oecId'],history.get('contents',[]))
            with closing(sqlite3.connect(root/'var/it-conversations.sqlite')) as index,index:
                prior=index.execute("SELECT oec FROM conversation WHERE scope='it:acc6' AND cid=?",(conversation.conversation_id,)).fetchone()
                if prior and prior[0]!=c['oecId']:raise CycleError('conversation_index_conflict')
                index.execute("INSERT OR IGNORE INTO conversation VALUES('it:acc6',?,?,2,?)",(conversation.conversation_id,c['oecId'],store.clock()))
            confirmed=sum(part['state']=='confirmed' for part in deliveries.get(delivery['id'])['parts'])
            _history.history_eligible(history,store.clock(),confirmed)
        try:
            result=execute(deliveries,delivery['id'],runtime,authorize,preflight,verify_only=delivery['state']=='unknown')
        except BaseException as error:
            latest=deliveries.get(delivery['id']);code=getattr(error,'code',None) or (str(error) if isinstance(error,CycleError) else type(error).__name__)
            if getattr(error,'response_ref',None):
                store.db.execute('INSERT INTO cycle_platform_signal(delivery_id,at,outcome,code,native_status,check_code,check_message,response_ref) VALUES(?,?,?,?,?,?,?,?)',
                  (delivery['id'],store.clock(),*[getattr(error,key,None) for key in ('outcome','code','native_status','check_code','check_message','response_ref')]))
            if latest['state']=='unknown':return publish_runtime(store,plan,'waiting_reconciliation',delivery=active,stop_reason=code,unknown=1)
            if getattr(error,'check_code',None) is not None and error.check_code<0:
                return publish_runtime(store,plan,'sending',delivery=None,stop_reason='recipient_limit',failed_delta=1)
            return publish_runtime(store,plan,'paused',delivery=active,stop_reason=code,failed_delta=1)
    if result['state']=='confirmed':return publish_runtime(store,plan,'sending',delivery=None,confirmed_delta=1,unknown=0)
    if result['state']=='unknown':return publish_runtime(store,plan,'waiting_reconciliation',delivery=active,stop_reason='result_unknown',unknown=1)
    return publish_runtime(store,plan,'paused',delivery=active,stop_reason=result['state'])


def _today_confirmed(store,plan,now):
    from datetime import datetime,timezone,timedelta
    zone=timezone(timedelta(hours=8));local=datetime.fromtimestamp(now,zone);start=local.replace(hour=0,minute=0,second=0,microsecond=0).timestamp()
    return store.db.execute("SELECT count(DISTINCT d.id) FROM cycle_delivery d JOIN cycle_delivery_part p ON p.delivery_id=d.id WHERE d.plan_id=? AND d.state='confirmed' AND p.kind='card' AND p.started>=?",(plan,start)).fetchone()[0]


def status(root,store=None):
    root=Path(root);owned=store is None;store=store or __import__('lib.second_cycle',fromlist=['CycleStore']).CycleStore(root/'var/second-cycle.sqlite')
    try:
        _required(store);plan=_plan(store);cfg=control(store,root);runtime=_runtime(store,plan);now=store.clock()
        worker=worker_state(root);runtime['workerPid']=worker['pid'] if worker['running'] else None
        runtime['confirmedToday']=_today_confirmed(store,plan,now)
        recent=store.db.execute("SELECT count(DISTINCT d.id) FROM cycle_delivery d JOIN cycle_delivery_part p ON p.delivery_id=d.id WHERE d.plan_id=? AND d.state='confirmed' AND p.kind='card' AND p.started>?",(plan,now-300)).fetchone()[0]
        runtime['speedPerMinute']=round(recent/5,2)
        unknown=[{'deliveryId':row['id'],'creatorId':row['creator_id'],'pid':row['pid']} for row in store.db.execute("SELECT id,creator_id,pid FROM cycle_delivery WHERE plan_id=? AND state='unknown' AND json_extract(snapshot,'$.executionMode')='continuous-v1'",(plan,))]
        sample=None
        try:
            preview,pool_state=_candidate(root,store,plan,cfg);remaining=int((pool_state.get('layers') or {}).get('ready') or 0)
            if preview:
                sample={'handle':preview['handle'],'pid':str(preview['pid']),
                        'productName':(preview.get('name') or {}).get('mentionIt') or '',
                        'creatorCommission':str((preview.get('offer') or {}).get('creatorPercent') or ''),
                        'listId':str((preview.get('card') or {}).get('listId') or ''),
                        'messageIt':preview['message']['textIt'],'messageZh':preview['message'].get('translationZh') or '',
                        'templateRevision':preview['message'].get('templateRevision')}
        except Exception:remaining=None
        return {'schemaVersion':'bdhub.continuous-send.v1','market':'it','account':'acc6','control':cfg,
                'runtime':runtime,'window':window_state(cfg['window'],now),'capacity':capacity(root,now=now),
                'poolRemaining':remaining,'sample':sample,'unknownDeliveries':unknown,'templates':send_templates(store),
                'legacyBatchRetired':True,'platformWrites':0,'realSends':0}
    finally:
        if owned:store.close()


def launch_worker(root):
    root=Path(root);existing=worker_state(root)
    if existing['running']:return existing['pid']
    log=root/'var/continuous-send.log';log.parent.mkdir(parents=True,exist_ok=True)
    with log.open('a',encoding='utf-8') as handle:
        child=subprocess.Popen([str(root/'.venv/bin/python'),str(root/'scripts/continuous-send-worker.py')],cwd=str(root),
          stdin=subprocess.DEVNULL,stdout=handle,stderr=subprocess.STDOUT,start_new_session=True,
          env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'})
    path=root/'var/continuous-send-worker.json';temporary=path.with_suffix('.tmp')
    temporary.write_text(json.dumps({'pid':child.pid,'startedAt':time.time()},ensure_ascii=False)+'\n');temporary.replace(path)
    return child.pid

def worker_state(root):
    try:value=json.loads((Path(root)/'var/continuous-send-worker.json').read_text(encoding='utf-8'))
    except (OSError,ValueError):value={}
    pid=value.get('pid');running=False
    if type(pid) is int and pid>0:
        try:os.kill(pid,0);running=True
        except OSError:pass
    return {'pid':pid if type(pid) is int else None,'running':running,'startedAt':value.get('startedAt')}
