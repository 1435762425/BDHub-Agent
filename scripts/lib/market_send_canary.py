"""One durable card+localized-text market send with exact readback."""
from __future__ import annotations

import json
import sqlite3
import time
from contextlib import closing
from dataclasses import asdict
from pathlib import Path

from lib.cycle_delivery import Deliveries
from lib.cycle_send_runtime import descriptor
from lib.lead_pool import pool
from lib.market_accounts import load_config
from lib.market_im_runtime import authenticated,write_gate
from lib.second_cycle import CycleError,CycleStore,assess_offer,digest
from lib.template_library import next_approved_send_template,render_send_template,require_send_template_approval


def _dispatch_allowed(store,market,*,canary=False,page_control=False):
    if canary and not page_control:return
    from lib.market_send_control import control
    from lib.operations_workflow import setting
    from lib.send_batch import window_state
    current=control(store,market)
    operations=setting(store,market)
    if current['stopRequested'] or not (current['runRequested'] or current['automaticEnabled'] or
                                        operations['continuousSendEnabled']):
        raise CycleError('continuous_send_stopped')
    if not window_state(current['window'],store.clock())['open']:
        raise CycleError('outside_send_window')


def _binding_current(root,market,candidate):
    from lib.catalog_binding import offer_fingerprint
    offer=candidate['offer']
    path=Path(root)/'var/catalog-links.sqlite'
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as links:
        row=links.execute('''SELECT state,offer_fingerprint,list_id,card_payload
          FROM catalog_current_binding WHERE market=? AND catalog_source=? AND pid=? AND campaign_id=?''',
          (market,offer['catalogSource'],str(offer['pid']),str(offer['campaignId']))).fetchone()
    if not row or row[0]!='active' or row[1]!=offer_fingerprint(offer) or \
       str(row[2])!=str(candidate['card']['listId']) or digest(json.loads(row[3]))!=digest(candidate['card']):
        raise CycleError('card_binding_changed')


def _unsettled(store,plan,*,canary=False):
    row=store.db.execute("""SELECT * FROM cycle_delivery WHERE plan_id=? AND state IN ('ready','running','unknown')
      AND json_extract(snapshot,'$.executionMode')=? ORDER BY created LIMIT 1""",
      (plan,'market-canary-v1' if canary else 'market-continuous-v1')).fetchone()
    return dict(row) if row else None


def _record_create_failure(store,delivery_id,error):
 intent=Deliveries(store).conversation_intent(delivery_id)
 if not intent or intent['state']!='inflight':return False
 if getattr(error,'response_ref',None):
  store.db.execute('INSERT INTO cycle_platform_signal(delivery_id,at,outcome,code,native_status,check_code,check_message,response_ref) VALUES(?,?,?,?,?,?,?,?)',
    (delivery_id,store.clock(),getattr(error,'outcome',None),getattr(error,'code',None),
     getattr(error,'native_status',None),getattr(error,'check_code',None),None,error.response_ref))
 Deliveries(store).unknown(delivery_id,'card')
 return True


def _preflight_conversation(store,session,plan,candidate,conversation,delivery_id):
    from lib.continuous_send import _continuous_history_eligible,ACTIVE_PENDING_STATES
    from lib.cycle_inbox import Inbox
    from lib.cycle_service import Service
    history=session.history_summary(conversation,include_sender_counts=True,include_events=True,include_contents=True)
    Inbox(store).ingest(plan,conversation.conversation_id,candidate['oecId'],history)
    Service(store).capture(plan,conversation.conversation_id,candidate['oecId'],history.get('contents',[]))
    relation=store.db.execute('SELECT mode,rejected,inbox_until,unlocked FROM relationship WHERE plan_id=? AND creator_id=?',
                              (plan,candidate['creatorId'])).fetchone()
    own=sum(part['state']=='confirmed' for part in Deliveries(store).get(delivery_id)['parts'] if part['kind']=='card')
    _continuous_history_eligible(history,store.clock(),bool(relation['unlocked']) if relation else False,own,
                                 required_messages=1 if own else 2)
    pending=store.db.execute('SELECT state FROM inbox_pending WHERE plan_id=? AND creator_id=?',
                             (plan,candidate['creatorId'])).fetchone()
    case=store.db.execute("SELECT 1 FROM service_case WHERE plan_id=? AND creator_id=? AND state='open'",
                          (plan,candidate['creatorId'])).fetchone()
    if not relation or relation['mode']!='auto' or relation['rejected'] or relation['inbox_until']>store.clock() or \
       case or pending and pending['state'] in ACTIVE_PENDING_STATES:
        raise CycleError('conversation_needs_content_review')


def _candidate(root,market,store,plan,initial,require_new_conversation=True):
 state=pool(root,market=market,now=store.clock(),limit=None,cache_eligible_seconds=30)
 conversations={row['oecId']:row for row in initial.get('conversations',[])}
 offers_by_pid={}
 for _,offer in store._offers(plan):offers_by_pid.setdefault(str(offer['pid']),[]).append(offer)
 with closing(sqlite3.connect((Path(root)/'var/creator-identities.sqlite').resolve().as_uri()+'?mode=ro',uri=True)) as identities,closing(sqlite3.connect((Path(root)/'var/catalog-links.sqlite').resolve().as_uri()+'?mode=ro',uri=True)) as links:
  identities.row_factory=sqlite3.Row;links.row_factory=sqlite3.Row
  for slot in (state.get('pools') or {}).get('ready',[]):
   relation=store.db.execute('SELECT * FROM relationship WHERE plan_id=? AND creator_id=?',(plan,slot['creatorId'])).fetchone()
   if not relation or require_new_conversation and relation['oec'] in conversations:continue
   if store.db.execute('SELECT 1 FROM cycle_delivery WHERE plan_id=? AND creator_id=? AND pid=?',(plan,slot['creatorId'],str(slot['pid']))).fetchone():continue
   person=identities.execute("SELECT current_handle FROM creator_identity WHERE market=? AND creator_id=? AND oec_id=? AND handle_conflict=0",(market,slot['creatorId'],relation['oec'])).fetchone()
   if not person:continue
   current=sorted((offer for offer in offers_by_pid.get(str(slot['pid']),[]) if assess_offer(offer,store.clock())['eligible']),
                  key=lambda offer:(0 if offer.get('catalogSource')=='selected' else 1,-float(offer.get('creatorPercent') or 0),str(offer.get('campaignId') or '')))
   for offer in current:
    binding=links.execute("SELECT * FROM catalog_current_binding WHERE market=? AND catalog_source=? AND pid=? AND campaign_id=? AND state='active'",(market,offer['catalogSource'],offer['pid'],offer['campaignId'])).fetchone()
    if not binding:continue
    card=json.loads(binding['card_payload']);locale={'br':'pt-BR','my':'ms-MY','uk':'en-GB'}[market]
    name_row=store.db.execute("SELECT payload FROM cycle_product_name WHERE pid=? AND locale=? ORDER BY rowid DESC LIMIT 1",(offer['pid'],locale)).fetchone()
    if not name_row:continue
    name=json.loads(name_row[0]);template=next_approved_send_template(store,root,plan,slot['creatorId'],market)
    if template is None:continue
    message=render_send_template(template,name,offer,person[0],market)
    source=store.db.execute("SELECT x.source_id FROM source_edge_index x JOIN cycle_identity_resolution r ON r.plan_id=x.plan_id AND r.source_id=x.source_id WHERE x.plan_id=? AND x.pid=? AND r.creator_id=? ORDER BY x.source_rank LIMIT 1",(plan,offer['pid'],slot['creatorId'])).fetchone()
    if not source:continue
    return {'creatorId':slot['creatorId'],'oecId':relation['oec'],'handle':person[0],'pid':str(offer['pid']),
     'source':{'sourceId':source[0]},'offer':offer,'offerFingerprint':digest(offer),'name':name,'card':card,
     'message':message,'planRevision':store._plan(plan)['revision'],'controlRevision':relation['revision'],
     'executionMode':'market-canary-v1' if require_new_conversation else 'market-continuous-v1','market':market,
     'conversationId':(conversations.get(relation['oec']) or {}).get('conversationId')}
 return None


def run(root,market,request_id,*,canary=True,page_control=False,reconcile_only=False):
 root=Path(root);pair=load_config(root)['markets'][market];communications=pair['roles']['communications']
 report={'market':market,'account':communications,'requestId':request_id,'platformWrites':0,'realSends':0}
 checkpoint=time.monotonic();timing={}
 def checkpoint_time(name):
  nonlocal checkpoint
  now=time.monotonic();timing[name]=round((now-checkpoint)*1000,1);checkpoint=now
 with CycleStore(root/'var/second-cycle.sqlite') as store:
  require_send_template_approval(store,root,market)
  plan=store.db.execute("SELECT id FROM plan WHERE institution='bjn-local-research' AND market=? AND state='active'",(market,)).fetchone()
  if not plan:raise CycleError('plan_missing')
  plan=plan[0]
  if reconcile_only:
   active=_unsettled(store,plan,canary=False) or _unsettled(store,plan,canary=True)
   if active is None:return report|{'state':'nothing_to_reconcile'}
   canary=json.loads(active['snapshot']).get('executionMode')=='market-canary-v1'
  else:
   _dispatch_allowed(store,market,canary=canary,page_control=page_control)
   active=_unsettled(store,plan,canary=canary)
  if store.db.execute("SELECT 1 FROM cycle_delivery WHERE plan_id=? AND state='unknown' AND id<>? LIMIT 1",(plan,active['id'] if active else '')).fetchone():
   raise CycleError('market_send_result_unknown')
  checkpoint_time('preAuth')
  with authenticated(root,market,report,canary=canary) as runtime:
   checkpoint_time('auth')
   session=runtime['session'];adapter=runtime['adapter'];initial=session.initialize(0)
   checkpoint_time('initial')
   if active:
    delivery=Deliveries(store).get(active['id']);candidate=delivery['snapshot'];did=delivery['id']
   else:
    candidate=_candidate(root,market,store,plan,initial,canary)
    if not candidate:raise CycleError('market_send_candidate_missing')
    _binding_current(root,market,candidate)
    delivery=Deliveries(store).prepare(plan,candidate);did=delivery['id'];candidate=delivery['snapshot']
   checkpoint_time('candidate')
   if not reconcile_only:_dispatch_allowed(store,market,canary=canary,page_control=page_control)
   intent=Deliveries(store).conversation_intent(did)
   if candidate.get('conversationId'):
    conversation=session.conversation(candidate['conversationId'],candidate['oecId'])
   elif intent and intent['state']=='confirmed':
    conversation=session.conversation(intent['cid'],candidate['oecId'])
   elif intent and intent['state'] in ('inflight','received'):
    raise CycleError('market_send_conversation_result_unknown')
   else:
    if reconcile_only:return report|{'state':'nothing_to_reconcile','deliveryId':did}
    if not Deliveries(store).contact_capacity_available(did):raise CycleError('new_contact_capacity_reached')
    conversation_intent=Deliveries(store).prepare_conversation(did)
    with write_gate(root,runtime['auth'],stopped=lambda: page_control and _stopped(store,market)) as mark:
     def permit_create(scope):
      if scope.get('market')!=market or scope.get('account')!=communications or scope.get('oecId')!=candidate['oecId']:raise CycleError('conversation_scope_mismatch')
      _dispatch_allowed(store,market,canary=canary,page_control=page_control);_binding_current(root,market,candidate)
      ref=Deliveries(store).begin_conversation(did,digest(candidate));mark();report['platformWrites']+=1;return {'dispatchAllowed':True,'requestRef':ref,'stage':'create_conversation'}
     try:receipt=adapter.create_once(candidate['oecId'],conversation_intent['request_ref'],before_dispatch=permit_create)
     except BaseException as error:
      if _record_create_failure(store,did,error):
       raise CycleError('market_send_conversation_result_unknown') from None
      raise
    Deliveries(store).save_conversation(did,receipt);conversation=session.conversation(receipt['conversationId'],candidate['oecId']);Deliveries(store).confirm_conversation(did,receipt['conversationId'],candidate['oecId'])
   checkpoint_time('conversation')
   _preflight_conversation(store,session,plan,candidate,conversation,did)
   checkpoint_time('preflight')
   origin=runtime['partnerHost']+'/api/v1/affiliate/partner/im/product_list/list';card=descriptor(candidate['card'],market,communications,origin)
   if Deliveries(store).interrupted_by_inquiry(did):raise CycleError('market_send_inquiry_interrupted')
   for kind in ('card','text'):
    part=next(row for row in Deliveries(store).get(did)['parts'] if row['kind']==kind)
    if part['state']=='confirmed':continue
    if part['state'] in ('inflight','accepted','unknown'):
     receipt=json.loads(part['receipt']) if part['receipt'] else {}
     proof=adapter.readback_card(conversation,card,part['request_ref'],message_id=receipt.get('messageId')) if kind=='card' else adapter.readback(conversation,candidate['message']['textIt'],part['request_ref'],message_id=receipt.get('messageId'))
     Deliveries(store).record_check(did,kind,proof)
     if proof.get('status')!='confirmed':
      Deliveries(store).unknown(did,kind);raise CycleError('market_send_result_unknown')
     Deliveries(store).confirm(did,kind,{'status':'confirmed','requestRef':part['request_ref'],'oecId':candidate['oecId'],'kind':kind,'messageId':proof['messageId'],'evidenceRef':proof['evidenceRef']})
     continue
    if part['state']!='ready':raise CycleError('market_send_part_unavailable')
    if reconcile_only:break
    if kind=='text':
     try:_preflight_conversation(store,session,plan,candidate,conversation,did)
     except CycleError:
      if Deliveries(store).interrupted_by_inquiry(did):raise CycleError('market_send_inquiry_interrupted') from None
      raise
    if kind=='text' and Deliveries(store).interrupted_by_inquiry(did):raise CycleError('market_send_inquiry_interrupted')
    if kind=='card' and not Deliveries(store).contact_capacity_available(did):raise CycleError('new_contact_capacity_reached')
    with write_gate(root,runtime['auth'],stopped=lambda: page_control and _stopped(store,market)) as mark:
     def permit(scope,expected=kind):
      if scope.get('market')!=market or scope.get('account')!=communications or scope.get('componentKind')!=expected:raise CycleError('dispatch_scope_mismatch')
      _dispatch_allowed(store,market,canary=canary,page_control=page_control);_binding_current(root,market,candidate)
      Deliveries(store).reserve_contact(did);value=Deliveries(store).begin(did,expected,authorized_snapshot_hash=digest(candidate),recipient_verified=True,allowance_verified=True);mark();report['platformWrites']+=1;return {**value,'stage':scope['stage'],'componentKind':expected,**{key:scope[key] for key in ('productId','listId','campaignId','bindingSha256') if key in scope}}
     receipt=adapter.send_card_once(conversation,card,part['request_ref'],before_dispatch=permit) if kind=='card' else adapter.send_once(conversation,candidate['message']['textIt'],part['request_ref'],before_dispatch=permit)
    Deliveries(store).receipt(did,kind,receipt)
    proof=adapter.readback_card(conversation,card,part['request_ref'],message_id=receipt.get('messageId')) if kind=='card' else adapter.readback(conversation,candidate['message']['textIt'],part['request_ref'],message_id=receipt.get('messageId'))
    Deliveries(store).record_check(did,kind,proof)
    if proof.get('status')!='confirmed':Deliveries(store).unknown(did,kind);raise CycleError('market_send_result_unknown')
    Deliveries(store).confirm(did,kind,{'status':'confirmed','requestRef':part['request_ref'],'oecId':candidate['oecId'],'kind':kind,'messageId':proof['messageId'],'evidenceRef':proof['evidenceRef']})
    checkpoint_time(kind)
   final=Deliveries(store).get(did);report.update(deliveryId=did,state=final['state'],pid=candidate['pid'],creatorId=candidate['creatorId'],
      realSends=0 if reconcile_only else int(final['state']=='confirmed'),unknown=int(final['state']=='unknown'),timingMs=timing)
   if canary and (page_control or reconcile_only) and final['state']=='confirmed':
    from lib.account_identity import promote_capabilities
    promote_capabilities(store,market=market,account=communications,capabilities=['message_send'],
                         evidence_ref='cycle-delivery:'+did)
 return report


def _stopped(store,market):
    try:_dispatch_allowed(store,market);return False
    except CycleError:return True
