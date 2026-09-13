"""Known-conversation card/text executor; recovery reads never re-dispatch."""
import json
from lib.second_cycle import CycleError,digest
from lib.cycle_delivery import Deliveries

def execute(deliveries,id,runtime_factory,authorize,preflight):
 d=deliveries.get(id);c=d['snapshot']
 for kind in ('card','text'):
  d=deliveries.get(id);part=next(p for p in d['parts'] if p['kind']==kind)
  if part['state']=='confirmed':continue
  recovering=part['state'] in ('inflight','accepted','unknown')
  if not recovering:authorize(c)
  with runtime_factory(c,read_only=recovering) as rt:
   cid=c.get('conversationId')
   if not cid:
    intent=deliveries.prepare_conversation(id)
    if intent['state']=='ready':
     authorize(c)
     try:
      with rt['write_gate']() as mark_create:
       def create_permit(scope):
        if scope.get('oecId')!=c['oecId'] or scope.get('stage')!='create_conversation' or scope.get('requestRef')!=intent['request_ref']:raise CycleError('conversation_scope_mismatch')
        ref=deliveries.begin_conversation(id,digest(c));mark_create();return {'dispatchAllowed':True,'requestRef':ref,'stage':'create_conversation'}
       receipt=rt['adapter'].create_once(c['oecId'],intent['request_ref'],before_dispatch=create_permit)
      deliveries.save_conversation(id,receipt)
     except BaseException:
      if deliveries.conversation_intent(id)['state']=='inflight':deliveries.unknown(id,'card')
      raise
     intent=deliveries.conversation_intent(id)
    if not intent['cid']:return deliveries.get(id)
    cid=intent['cid']
   conv=rt['reads'].conversation(cid,c['oecId'])
   if not c.get('conversationId'):deliveries.confirm_conversation(id,cid,c['oecId'])
   adapter=rt['adapter'];card=rt['card']
   if not recovering:
    authorize(c);deliveries.reserve_contact(id);preflight(c,rt,conv)
    if kind=='card':card=rt['validate_card'](card)
    else:rt['validate_card'](card)
    def permit(scope):
     if scope.get('oecId')!=c['oecId'] or scope.get('conversationId')!=conv.conversation_id or scope.get('requestRef')!=part['request_ref'] or scope.get('componentKind')!=kind:raise CycleError('dispatch_scope_mismatch')
     if scope.get('market')!='it' or scope.get('account')!='acc6':raise CycleError('dispatch_scope_mismatch')
     if kind=='card' and (scope.get('productId'),scope.get('listId'),scope.get('bindingSha256'))!=(card.product_id,card.list_id,card.binding_sha256):raise CycleError('card_binding_changed')
     authorize(c)
     permit=deliveries.begin(id,kind,authorized_snapshot_hash=digest(c),recipient_verified=True,allowance_verified=True)
     mark()
     return {**permit,'stage':scope['stage'],'componentKind':kind,**{k:scope[k] for k in ('productId','listId','campaignId','bindingSha256') if k in scope}}
    try:
     with rt['write_gate']() as mark:
      receipt=adapter.send_card_once(conv,card,part['request_ref'],before_dispatch=permit) if kind=='card' else adapter.send_once(conv,c['message']['textIt'],part['request_ref'],before_dispatch=permit)
     deliveries.receipt(id,kind,receipt)
    except BaseException as error:
     # Once a persisted intent may have been dispatched, only verification may follow.
     latest=next(p for p in deliveries.get(id)['parts'] if p['kind']==kind)
     if latest['state']=='inflight':
      if getattr(error,'outcome',None)=='rejected':deliveries.rejected(id,kind,error)
      else:deliveries.unknown(id,kind)
     raise
   latest=next(p for p in deliveries.get(id)['parts'] if p['kind']==kind)
   receipt=json.loads(latest['receipt']) if latest['receipt'] else {}
   proof=adapter.readback_card(conv,card,part['request_ref'],message_id=receipt.get('messageId')) if kind=='card' else adapter.readback(conv,c['message']['textIt'],part['request_ref'],message_id=receipt.get('messageId'))
   deliveries.record_check(id,kind,proof)
   if proof.get('status')!='confirmed':deliveries.unknown(id,kind);return deliveries.get(id)
   deliveries.confirm(id,kind,{'status':'confirmed','requestRef':part['request_ref'],'oecId':c['oecId'],'kind':kind,'messageId':proof['messageId'],'evidenceRef':proof['evidenceRef']})
 return deliveries.get(id)
