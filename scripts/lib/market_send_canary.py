"""One durable card+localized-text market send with exact readback."""
from __future__ import annotations

import json
import sqlite3
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


def _candidate(root,market,store,plan,initial,require_new_conversation=True):
 state=pool(root,market=market,now=store.clock(),limit=200);conversations={row['oecId']:row for row in initial.get('conversations',[])}
 offers=[offer for _,offer in store._offers(plan)];by_pid={}
 for offer in offers:by_pid.setdefault(str(offer['pid']),[]).append(offer)
 with closing(sqlite3.connect((Path(root)/'var/creator-identities.sqlite').resolve().as_uri()+'?mode=ro',uri=True)) as identities,closing(sqlite3.connect((Path(root)/'var/catalog-links.sqlite').resolve().as_uri()+'?mode=ro',uri=True)) as links:
  identities.row_factory=sqlite3.Row;links.row_factory=sqlite3.Row
  for slot in (state.get('pools') or {}).get('ready',[]):
   relation=store.db.execute('SELECT * FROM relationship WHERE plan_id=? AND creator_id=?',(plan,slot['creatorId'])).fetchone()
   if not relation or require_new_conversation and relation['oec'] in conversations:continue
   if store.db.execute('SELECT 1 FROM cycle_delivery WHERE plan_id=? AND creator_id=? AND pid=?',(plan,slot['creatorId'],str(slot['pid']))).fetchone():continue
   person=identities.execute("SELECT current_handle FROM creator_identity WHERE market=? AND creator_id=? AND oec_id=? AND handle_conflict=0",(market,slot['creatorId'],relation['oec'])).fetchone()
   if not person:continue
   current=sorted((offer for offer in by_pid.get(str(slot['pid']),[]) if assess_offer(offer,store.clock())['eligible']),
                  key=lambda offer:(0 if offer.get('catalogSource')=='selected' else 1,-float(offer.get('creatorPercent') or 0),str(offer.get('campaignId') or '')))
   for offer in current:
    binding=links.execute("SELECT * FROM catalog_current_binding WHERE market=? AND catalog_source=? AND pid=? AND campaign_id=? AND state='active'",(market,offer['catalogSource'],offer['pid'],offer['campaignId'])).fetchone()
    if not binding:continue
    card=json.loads(binding['card_payload']);locale={'br':'pt-BR','uk':'en-GB'}[market]
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


def run(root,market,request_id,*,canary=True):
 root=Path(root);pair=load_config(root)['markets'][market];communications=pair['roles']['communications'];report={'market':market,'account':communications,'requestId':request_id,'platformWrites':0,'realSends':0}
 with CycleStore(root/'var/second-cycle.sqlite') as store:
  require_send_template_approval(store,root,market)
  plan=store.db.execute("SELECT id FROM plan WHERE institution='bjn-local-research' AND market=? AND state='active'",(market,)).fetchone()
  if not plan:raise CycleError('plan_missing')
  plan=plan[0]
  with authenticated(root,market,report,canary=canary) as runtime:
   session=runtime['session'];adapter=runtime['adapter'];initial=session.initialize(0);candidate=_candidate(root,market,store,plan,initial,canary)
   if not candidate:raise CycleError('market_send_candidate_missing')
   delivery=Deliveries(store).prepare(plan,candidate);did=delivery['id'];candidate=delivery['snapshot']
   if candidate.get('conversationId'):
    conversation=session.conversation(candidate['conversationId'],candidate['oecId'])
   else:
    conversation_intent=Deliveries(store).prepare_conversation(did)
    with write_gate(root,runtime['auth']) as mark:
     def permit_create(scope):
      if scope.get('market')!=market or scope.get('account')!=communications or scope.get('oecId')!=candidate['oecId']:raise CycleError('conversation_scope_mismatch')
      ref=Deliveries(store).begin_conversation(did,digest(candidate));mark();report['platformWrites']+=1;return {'dispatchAllowed':True,'requestRef':ref,'stage':'create_conversation'}
     receipt=adapter.create_once(candidate['oecId'],conversation_intent['request_ref'],before_dispatch=permit_create)
    Deliveries(store).save_conversation(did,receipt);conversation=session.conversation(receipt['conversationId'],candidate['oecId']);Deliveries(store).confirm_conversation(did,receipt['conversationId'],candidate['oecId'])
   history=session.history_summary(conversation,include_sender_counts=True)
   from lib.continuous_send import _continuous_history_eligible
   relation=store.db.execute('SELECT unlocked FROM relationship WHERE plan_id=? AND creator_id=?',(plan,candidate['creatorId'])).fetchone()
   _continuous_history_eligible(history,store.clock(),bool(relation[0]) if relation else False)
   origin=runtime['partnerHost']+'/api/v1/affiliate/partner/im/product_list/list';card=descriptor(candidate['card'],market,communications,origin)
   for kind in ('card','text'):
    part=next(row for row in Deliveries(store).get(did)['parts'] if row['kind']==kind)
    with write_gate(root,runtime['auth']) as mark:
     def permit(scope,expected=kind):
      if scope.get('market')!=market or scope.get('account')!=communications or scope.get('componentKind')!=expected:raise CycleError('dispatch_scope_mismatch')
      Deliveries(store).reserve_contact(did);value=Deliveries(store).begin(did,expected,authorized_snapshot_hash=digest(candidate),recipient_verified=True,allowance_verified=True);mark();report['platformWrites']+=1;return {**value,'stage':scope['stage'],'componentKind':expected,**{key:scope[key] for key in ('productId','listId','campaignId','bindingSha256') if key in scope}}
     receipt=adapter.send_card_once(conversation,card,part['request_ref'],before_dispatch=permit) if kind=='card' else adapter.send_once(conversation,candidate['message']['textIt'],part['request_ref'],before_dispatch=permit)
    Deliveries(store).receipt(did,kind,receipt)
    proof=adapter.readback_card(conversation,card,part['request_ref'],message_id=receipt.get('messageId')) if kind=='card' else adapter.readback(conversation,candidate['message']['textIt'],part['request_ref'],message_id=receipt.get('messageId'))
    Deliveries(store).record_check(did,kind,proof)
    if proof.get('status')!='confirmed':Deliveries(store).unknown(did,kind);raise CycleError('market_send_result_unknown')
    Deliveries(store).confirm(did,kind,{'status':'confirmed','requestRef':part['request_ref'],'oecId':candidate['oecId'],'kind':kind,'messageId':proof['messageId'],'evidenceRef':proof['evidenceRef']})
   final=Deliveries(store).get(did);report.update(deliveryId=did,state=final['state'],pid=candidate['pid'],creatorId=candidate['creatorId'],realSends=1,unknown=0)
 return report
