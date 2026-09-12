"""One relationship bundle: verified cards, then the fixed text, never a replay."""
from __future__ import annotations
import hashlib
import uuid
from lib.italy_im_delivery import ItalyImDeliveryError,ItalyVerifiedProductCard,CARD_CONTENT
from lib.second_live_trial import LiveTrialError


def run_trial(store,trial_id,*,runtime_factory,validate_item,owner=None,max_steps=30):
    """runtime_factory yields adapter, reads, write_gate and validate_card.

    Approval is checked before opening runtime or credentials. The runtime's gate
    follows the MX sender-ID FIFO contract, while the item lease owns all cards
    and its final text. All external calls pass through a persisted permission.
    """
    owner=owner or 'second-runner-'+uuid.uuid4().hex
    initial=store.get_trial(trial_id)
    if not initial['approved']:
        return {'status':'awaiting_approval','trial':initial,'runtimeOpened':False}
    runtime_opened=False
    for _ in range(max_steps):
        claim=store.claim(trial_id,owner,lease_seconds=300)
        if claim is None:return {'status':'stopped','trial':store.get_trial(trial_id),'runtimeOpened':runtime_opened}
        item=claim['item'];frozen=next(row for row in initial['snapshot']['items'] if row['itemId']==claim['itemId']);action=claim['nextAction'];component=claim.get('component')
        active_attempt=None
        try:
            if not claim['readOnly']:validate_item(frozen)
            runtime_opened=True
            with runtime_factory(frozen,read_only=claim['readOnly']) as runtime:
                adapter,reads=runtime['adapter'],runtime['reads']
                def permit(scope):
                    nonlocal active_attempt
                    if scope.get('market')!='it' or scope.get('account')!='acc6' or scope.get('oecId')!=frozen['oecId']:
                        raise LiveTrialError('dispatch_scope_mismatch')
                    validate_item(frozen)
                    kind=scope.get('componentKind')
                    if scope['stage']=='create_conversation':stage='create_conversation'
                    elif kind=='card' and component and component['componentKind']=='card':
                        stage='send_card';card=component['card']
                        if (scope.get('productId'),scope.get('listId'),scope.get('campaignId'),scope.get('bindingSha256'))!=(card['product_id'],card['list_id'],card['campaign_id'],card['binding_sha256']):raise LiveTrialError('dispatch_scope_mismatch')
                    elif kind=='text' and action=='send_message':stage='send_message'
                    else:raise LiveTrialError('dispatch_scope_mismatch')
                    if stage!='create_conversation' and scope.get('conversationId')!=item['conversationId']:raise LiveTrialError('dispatch_scope_mismatch')
                    attempt=store.begin_attempt(claim['itemId'],owner,claim['fence'],stage,scope['requestRef'],
                                                component_id=component['componentId'] if stage=='send_card' else None)
                    active_attempt=attempt
                    return {**attempt,'stage':scope['stage'],'requestRef':scope['requestRef'],'componentKind':kind,
                            **{key:scope[key] for key in ('productId','listId','campaignId','bindingSha256') if key in scope}}

                if action=='create_conversation':
                    ref=str(uuid.uuid4())
                    with runtime['write_gate']() as mark:
                        def allowed(scope):
                            permission=permit(scope)
                            if permission['dispatchAllowed']:mark()
                            return permission
                        receipt=adapter.create_once(frozen['oecId'],ref,before_dispatch=allowed)
                    # Persist the returned CID before the independent identity read.
                    store.record_conversation(claim['itemId'],owner,claim['fence'],active_attempt['attemptId'],receipt['conversationId'],receipt['evidenceRef'],verified=False)
                    conversation=reads.conversation(receipt['conversationId'],frozen['oecId'])
                    store.confirm(claim['itemId'],owner,claim['fence'],{'kind':'conversation_confirmed','conversationId':conversation.conversation_id,'recipientOecId':frozen['oecId'],'evidenceRef':conversation.evidence_ref})
                elif action=='verify_conversation':
                    if not item.get('conversationId'):
                        return {'status':'result_unknown','trial':store.get_trial(trial_id),'runtimeOpened':True}
                    conversation=reads.conversation(item['conversationId'],frozen['oecId'])
                    store.confirm(claim['itemId'],owner,claim['fence'],{'kind':'conversation_confirmed','conversationId':conversation.conversation_id,'recipientOecId':frozen['oecId'],'evidenceRef':conversation.evidence_ref})
                elif action in ('send_card','send_message','verify_card','verify_message'):
                    conversation=reads.conversation(item['conversationId'],frozen['oecId'])
                    is_card=action.endswith('card')
                    card=ItalyVerifiedProductCard(**component['card']) if is_card else None
                    if action.startswith('send'):
                        if card is not None:card=runtime['validate_card'](card)
                        elif runtime.get('validate_text'):runtime['validate_text']()
                        ref=str(uuid.uuid4())
                        with runtime['write_gate']() as mark:
                            def allowed(scope):
                                permission=permit(scope)
                                if permission['dispatchAllowed']:mark()
                                return permission
                            receipt=adapter.send_card_once(conversation,card,ref,before_dispatch=allowed) if is_card else adapter.send_once(conversation,frozen['textIt'],ref,before_dispatch=allowed)
                        store.record_send_receipt(claim['itemId'],owner,claim['fence'],active_attempt['attemptId'],receipt)
                    else:
                        original=next((a for a in item['attempts'] if a['stage']=='send_message'),None)
                        ref=component['requestRef'] if component else (original or {}).get('requestRef')
                        receipt=component.get('sendReceipt') if component else item.get('sendReceipt')
                        if not ref:return {'status':'result_unknown','trial':store.get_trial(trial_id),'runtimeOpened':True}
                    evidence=adapter.readback_card(conversation,card,ref,message_id=(receipt or {}).get('messageId')) if is_card else adapter.readback(conversation,frozen['textIt'],ref,message_id=(receipt or {}).get('messageId'))
                    if evidence.get('status')!='confirmed':
                        store.mark_verification_unresolved(claim['itemId'],owner,claim['fence'],'readback_unconfirmed')
                        return {'status':'result_unknown','trial':store.get_trial(trial_id),'runtimeOpened':True}
                    confirmation={'kind':'card_confirmed' if is_card else 'message_confirmed','conversationId':conversation.conversation_id,
                                  'recipientOecId':frozen['oecId'],'requestRef':ref,'messageId':evidence['messageId'],
                                  'textSha256':hashlib.sha256((CARD_CONTENT if is_card else frozen['textIt']).encode()).hexdigest(),'evidenceRef':evidence['evidenceRef']}
                    if is_card:confirmation.update(componentId=component['componentId'],productId=card.product_id,listId=card.list_id,campaignId=card.campaign_id,bindingSha256=card.binding_sha256)
                    store.confirm(claim['itemId'],owner,claim['fence'],confirmation)
                else:return {'status':'awaiting_result','trial':store.get_trial(trial_id),'runtimeOpened':True}
        except BaseException as error:
            code=getattr(error,'code','execution_interrupted')
            if not isinstance(code,str) or not code.replace('_','').isalnum():code='execution_interrupted'
            if active_attempt:
                not_submitted=isinstance(error,ItalyImDeliveryError) and error.outcome=='not_submitted'
                try:store.error(claim['itemId'],owner,claim['fence'],active_attempt['attemptId'],code[:64],definitely_not_sent=not_submitted,
                                evidence_ref='local:before-http-submission' if not_submitted else None)
                except LiveTrialError:pass
            elif claim['readOnly']:
                try:store.mark_verification_unresolved(claim['itemId'],owner,claim['fence'],code[:64])
                except LiveTrialError:pass
            if isinstance(error,(KeyboardInterrupt,SystemExit)):raise
            return {'status':'blocked','errorCode':getattr(error,'code','execution_interrupted'),'trial':store.get_trial(trial_id),'runtimeOpened':runtime_opened}
        current=store.get_trial(trial_id)
        if all(row['state']=='confirmed' for row in current['items']):return {'status':'confirmed','trial':current,'runtimeOpened':True}
        if current['blockedByUnknown']:return {'status':'result_unknown','trial':current,'runtimeOpened':True}
    return {'status':'step_limit','trial':store.get_trial(trial_id),'runtimeOpened':True}
