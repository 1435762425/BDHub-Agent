"""正式send-worker的有界零浏览器HTTP试验；只消费冻结组件并保留原事实源。"""
from datetime import datetime,timezone
import json
import os
import time
import hashlib
import resource
import sys
from uuid import uuid4

from sqlalchemy import text

from bdhub import config,scheduled_relogin
from bdhub.account_lifecycle import resolve_account_policy
from bdhub.enrich.profile_lease import ProfileLease
from bdhub.hub.engine import get_engine,check_schema
from bdhub.hub.markets import require_capability
from bdhub.hub.repo.send_tasks import SendTaskRepo
from bdhub.hub.repo.send_workers import SendWorkerRepo
from bdhub.hub.repo.im import OutboundMessageProjector
from bdhub.hub.repo.outreach import OutreachStore
from bdhub.hub.repo.send_attribution import SendAttributionRepo
from bdhub.imbase.account_binding import resolve_bound_profile
from bdhub.send.component_sender import ComponentOutcome
from bdhub.send.http_auth import bootstrap_direct_http
from bdhub.send.http_session import HttpImSession
from bdhub.send.http_protocol import HttpSendRejected
from bdhub.send.http_canary import FixedOutcomeSender
from bdhub.send.http_write_gate import shared_write_gate
from bdhub.send.runner import SendTaskRunner


class HttpOutcomeRunner(SendTaskRunner):
    """HTTP已经执行完；只复用落账，缺CID的建会话unknown不能再走原生resolver。"""
    def _send_claim(self,claim):
        if not isinstance(self.sender,FixedOutcomeSender):raise ValueError('http_fixed_outcome_required')
        outcome=self.sender.outcome
        if outcome.status=='sent' and (not self.sender.cid or not outcome.transport.get('readback_verified')):
            raise ValueError('http_sent_evidence_required')
        return self._with_resolver(outcome,{'method':'pure_http','classification':
            'verified_prepared' if outcome.transport.get('oec_verified') else 'prepare_failed'}),self.sender.cid or None


def assert_trial(task,account,revision,run_id):
    trial=(task.get('template_snapshot') or {}).get('_http_trial') or {}
    if (task['status']!='running' or task['revision']!=revision or task['preflight_revision']!=revision
            or task['send_mode'] not in ('text_only','card_only') or task['bd_market']!='mx'
            or (task.get('template_snapshot') or {}).get('_execution_transport')!='pure_http'
            or trial.get('run_id')!=run_id or account not in trial.get('accounts',[])
            or datetime.fromisoformat(trial['expires_at'])<=datetime.now(timezone.utc)):
        raise ValueError('http_trial_state_changed')
    if task['send_mode']=='card_only' and (not trial.get('product_card') or trial['product_card'].get('product_id')!=task.get('default_pid') or trial['limit']>3):
        raise ValueError('http_card_trial_binding_missing')
    if trial.get('resolve_new_conversations') and (task['send_mode']!='text_only' or (trial.get('mode')!='production' and (trial['limit']>3 or len(trial['accounts'])!=1))):
        raise ValueError('http_cold_trial_scope_invalid')
    if trial.get('mode')=='production' and (task['send_mode']!='text_only' or not 1<=trial['limit']<=500 or not 1<=len(trial['accounts'])<=4):
        raise ValueError('http_batch_scope_invalid')
    return trial


def dispatch_guard(repo,workers,engine,claim,*,account,revision,run_id,stopped):
    task=repo.get_task(claim['task']['task_id'])
    assert_trial(task,account,revision,run_id)
    state=workers.get('mx:'+account)
    if stopped() or state['status'] not in ('ready','busy') or state['process_id']!=os.getpid():
        raise ValueError('http_trial_stopped_before_dispatch')
    with engine.connect() as c:
        count=c.execute(text('''select count(*) from send_task_component x join send_task_lead l using(lead_id)
            join send_component_attempt a using(component_id)
            where x.component_id=:component and a.attempt_id=:attempt and x.status='sending' and a.status='sending'
            and not l.manual_excluded and l.oec_id=:oec and l.rendered_text_snapshot is not distinct from :body
            and l.pid is not distinct from :pid
            and not exists(select 1 from creator_im_state s where s.bd_market='mx'
              and s.creator_identity_key in ('oec:'||l.oec_id,l.creator_identity_key,'handle:'||lower(l.raw_handle)) and s.status='rejected')'''),
            {'component':claim['component']['component_id'],'attempt':claim['attempt']['attempt_id'],
             'oec':claim['lead']['oec_id'],'body':claim['lead']['rendered_text_snapshot'],'pid':claim['lead'].get('pid')}).scalar_one()
    if count!=1:raise ValueError('http_trial_claim_changed')


def run_trial(*,account_name,market,task_id,revision,run_id):
    from bdhub.send.worker import _select_account,_claim_liveness,_stop_requested_context
    require_capability(market,'send')
    if market!='mx':raise ValueError('http_trial_mx_only')
    cfg=config.load();account=_select_account(cfg,account_name)
    policy=resolve_account_policy(account)
    engine=get_engine(cfg);check_schema(engine)
    repo=SendTaskRepo(engine);workers=SendWorkerRepo(engine);wid='mx:'+account_name
    assert_trial(repo.get_task(task_id),account_name,revision,run_id)
    lease=ProfileLease(resolve_bound_profile(account),account=account_name,market=market,operation='http_send_trial')
    owner=lease.acquire();session=None
    summary={'run_id':run_id,'task_id':task_id,'account':account_name,'browser_initializations':0,
             'execution_id':uuid4().hex,'started_at':datetime.now(timezone.utc).isoformat(),'results':[]}
    try:
        workers.register(wid,market,account_name,os.getpid())
        if repo.recover_orphaned_attempt_for_worker(wid) is not None:
            repo.pause_task(task_id,actor='http_trial')
            raise ValueError('http_trial_orphaned_attempt')
        with _stop_requested_context() as stopped, _claim_liveness(workers,wid):
            cold_context={} if assert_trial(repo.get_task(task_id),account_name,revision,run_id).get('resolve_new_conversations') else None
            auth_kwargs={'conversation_context':cold_context} if cold_context is not None else {}
            packet=bootstrap_direct_http(account,summary,use_environment_proxy=False,**auth_kwargs)
            summary['im_identity_group']=hashlib.sha256(str(packet.sender_id).encode()).hexdigest()[:16]
            session=HttpImSession(packet,use_environment_proxy=False)
            workers.heartbeat(wid,status='ready')
            while not stopped():
                if repo.get_task(task_id)['status']!='running':break
                state=workers.get(wid)
                if state['status'] not in ('ready','busy'):break
                if scheduled_relogin.maintenance_due(account,initialize=False,ignore_retry_throttle=True):
                    raise ValueError('http_trial_maintenance_due')
                if time.monotonic()>session.expires_at-60:
                    refresh={}
                    summary.setdefault('auth_refreshes',[]).append(refresh)
                    new_packet=bootstrap_direct_http(account,refresh,use_environment_proxy=False,**auth_kwargs)
                    if new_packet.sender_id!=packet.sender_id:raise ValueError('http_trial_refreshed_identity_changed')
                    session.close();packet=new_packet
                    session=HttpImSession(packet,use_environment_proxy=False)
                claim=repo.claim_next_component_for_worker(market=market,account_name=account_name,worker_id=wid,
                                                           task_id=task_id,http_trial_run_id=run_id)
                if claim is None:break
                workers.heartbeat(wid,status='busy',task_id=task_id,lead_id=claim['lead']['lead_id'])
                report={'transport_kind':'pure_http','run_id':run_id,'component_id':claim['component']['component_id'],
                        'browser_initializations':0,'http_send_posts':0,'started_at':datetime.now(timezone.utc).isoformat()}
                cid=str(claim['component'].get('conversation_id') or '')
                started=time.monotonic()
                try:
                    trial=(repo.get_task(task_id).get('template_snapshot') or {})['_http_trial']
                    if not cid and trial.get('resolve_new_conversations'):
                        from bdhub.send.http_conversation import create_once
                        def before_create():
                            dispatch_guard(repo,workers,engine,claim,account=account_name,revision=revision,run_id=run_id,stopped=stopped)
                            report['conversation_intent_id']=repo.begin_http_conversation(claim,
                                identity_group=summary['im_identity_group'],run_id=run_id)
                        with shared_write_gate(packet.sender_id,interval=float(trial.get('send_interval_seconds',policy.im_send_interval_seconds)),stopped=stopped) as mark_create:
                            def record_create():
                                before_create();mark_create()
                            result=create_once(session,cold_context,claim['lead']['oec_id'],report,before_request=record_create)
                        cid=result['conversation_id']
                        repo.finish_http_conversation(claim,intent_id=report['conversation_intent_id'],result=result)
                        report.update(conversation_create_resolved=True,conversation_is_new=result['is_new'])
                    card=None
                    if claim['component']['component_kind']=='card':
                        from bdhub.send.http_card import ProductCard,lookup_product_card
                        card=ProductCard(**trial['product_card'])
                        if claim['lead']['pid']!=card.product_id:raise ValueError('http_card_claim_pid_changed')
                        card,_=lookup_product_card(account,card.product_id,expected=card)
                    conversation=session.conversation(cid,claim['lead']['oec_id'])
                    report['oec_verified']=True
                    if card:
                        from bdhub.send.http_card import build_card_packet
                        fresh=build_card_packet(packet,conversation,card,handle=claim['lead']['raw_handle'],sequence=session.next_sequence(),client_id=str(uuid4()))
                        report['product_card_verified']=True
                    else:
                        fresh=session.prepare_send(conversation,claim['lead']['rendered_text_snapshot'],handle=claim['lead']['raw_handle'])
                    if trial.get('resolve_new_conversations'):
                        from bdhub.send.http_conversation import history_before_send
                        empty=history_before_send(session,fresh,report)
                        if report.get('conversation_is_new') and not empty:raise ValueError('http_new_conversation_history_not_empty')
                        report['first_contact_verified']=bool(report.get('conversation_is_new') and empty)
                    report['client_message_id']=fresh.client_id.decode()
                    report['prepare_seconds']=round(time.monotonic()-started,3)
                    trial=(repo.get_task(task_id).get('template_snapshot') or {})['_http_trial']
                    interval=float(trial.get('send_interval_seconds',policy.im_send_interval_seconds))
                    report['configured_send_interval_seconds']=interval
                    queued_at=time.monotonic()
                    with shared_write_gate(packet.sender_id,interval=interval,stopped=stopped) as mark:
                        report['write_queue_wait_seconds']=round(time.monotonic()-queued_at,3)
                        def before_dispatch():
                            dispatch_guard(repo,workers,engine,claim,account=account_name,revision=revision,run_id=run_id,stopped=stopped)
                            mark()
                        server_id=session.dispatch_once(fresh,report,before_dispatch=before_dispatch)
                    server_id=session.verify_sent(fresh,server_id,report)
                    outcome=ComponentOutcome('sent',server_id,None,dict(report))
                except HttpSendRejected as error:
                    report['error_code']=str(error)
                    report['platform_rejection_status']=error.status
                    # 1–5为原生明确拒绝（含仅自己可见），不能认送达，也不自动重发。
                    outcome=ComponentOutcome('failed_terminal',None,'http_platform_rejected',dict(report))
                except Exception as error:
                    report['error_code']=str(error) if isinstance(error,ValueError) else 'http_trial_transport_error'
                    recovered=None
                    if report['http_send_posts'] and (report.get('send_receipt') or {}).get('outer_status')==500:
                        try:recovered=session.recover_outer_error(fresh,report)
                        except Exception:report['recovery_error']='http_readback_unconfirmed'
                    if recovered:
                        outcome=ComponentOutcome('sent',recovered,None,dict(report))
                    else:
                        uncertain=bool(report['http_send_posts'] or (report.get('conversation_intent_id') and not report.get('conversation_create_resolved')))
                        outcome=ComponentOutcome('unknown' if uncertain else 'failed_retryable',None,
                            'http_trial_result_unknown' if uncertain else 'http_trial_prepare_failed',dict(report))
                claim['component']['conversation_id']=cid
                report.update(outcome=outcome.status,writeback_pending=True)
                summary['results'].append(report)
                runner=HttpOutcomeRunner(task_repo=repo,sender=FixedOutcomeSender(outcome,cid),
                    attribution_repo=SendAttributionRepo(engine),outreach_store=OutreachStore(engine),
                    message_projector=OutboundMessageProjector(engine))
                runner.process_claim(claim,task_id=task_id,market=market,send_account=account_name)
                report.update(outcome=outcome.status,finished_at=datetime.now(timezone.utc).isoformat(),
                              processing_seconds=round(time.monotonic()-started,3),writeback_pending=False)
                print(json.dumps(report),flush=True)
                if outcome.status!='sent':
                    repo.pause_task(task_id,actor='http_trial')
                    if report['http_send_posts']==0 and not report.get('conversation_intent_id') and report.get('error_code') in ('http_trial_state_changed','http_trial_stopped_before_dispatch','http_write_gate_timeout'):
                        repo.release_http_trial_undispatched(task_id,report['component_id'])
                        report['outcome']='not_dispatched_requeued'
                    workers.pause(wid,code=outcome.error_code,detail=None)
                    break
                repo.pause_http_trial_if_drained(task_id,run_id)
                workers.heartbeat(wid,status='ready')
        return 0 if summary['results'] and all(r['outcome']=='sent' for r in summary['results']) else 2
    except Exception as error:
        summary['error']=str(error) if isinstance(error,ValueError) else type(error).__name__
        if repo.get_task(task_id)['status']=='running':repo.pause_task(task_id,actor='http_trial')
        return 2
    finally:
        try:
            if session is not None:session.close()
            workers.stop(wid)
            repo.pause_http_trial_if_drained(task_id,run_id)
        finally:
            lease.release(owner.token)
            summary['finished_at']=datetime.now(timezone.utc).isoformat()
            usage=resource.getrusage(resource.RUSAGE_SELF)
            summary['resource_usage']={'max_rss_mib':round(usage.ru_maxrss/(1024*1024 if sys.platform=='darwin' else 1024),2),
                                      'user_cpu_seconds':round(usage.ru_utime,3),'system_cpu_seconds':round(usage.ru_stime,3)}
            output=config.ROOT/'outputs/im-http-batch-20260906';output.mkdir(parents=True,exist_ok=True)
            (output/(run_id+'-'+account_name+'-'+summary['execution_id']+'.json')).write_text(json.dumps(summary,indent=2),encoding='utf-8')
            (output/(run_id+'-'+account_name+'.json')).write_text(json.dumps(summary,indent=2),encoding='utf-8')
            print(json.dumps({'trial_finished':run_id,'account':account_name,'processed':len(summary['results']),
                              'error':summary.get('error')}),flush=True)
