"""IT service-reply transport shared by manual actions and the V2 Agent."""
from __future__ import annotations

import fcntl
import hashlib
import json
import time
from pathlib import Path

from lib.cycle_inbox import Inbox
from lib.cycle_reply_facts import ReplyFacts
from lib.cycle_send_runtime import fresh_card
from lib.second_live_runtime import _authenticated,live_runtime,read_sender_binding
from lib.second_cycle import CycleError

ROOT=Path(__file__).resolve().parents[2]


def refresh_card(candidate,*,stopped=lambda:False):
    with _authenticated({},stopped=stopped) as (account,identity,headers,auth,maintenance,available):
        _,proof=fresh_card(candidate,account,identity,headers,maintenance,stopped)
    return proof


def run_reply(store,replies,reply,*,root=ROOT,authorized_now=False,stopped=lambda:False,read_started=lambda:None,
              read_binding=read_sender_binding,live=live_runtime,refresh=None):
    """Send once if ready; otherwise read back only the original requestRef."""
    root=Path(root);report={};recovering=reply['state'] in ('inflight','accepted','unknown','isolated')
    card=None
    if reply['kind']=='manual_card':
        from lib.cycle_send_runtime import descriptor
        card=descriptor(json.loads(reply['text']))
    if refresh is None:refresh=lambda candidate:refresh_card(candidate,stopped=stopped)
    with (root/'var/cycle-send.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if not recovering:
            fact=store.db.execute('SELECT payload FROM service_reply_fact WHERE reply_id=?',(reply['id'],)).fetchone()
            if fact:
                previous=json.loads(fact[0])
                current=ReplyFacts(store,refresh).call('get_current_creator_commission',reply['plan_id'],reply['creator_id'])
                if (current['pid'],current['creatorPercent'])!=(previous['pid'],previous['creatorPercent']):
                    with store.tx():
                        store.db.execute("UPDATE service_reply SET state='cancelled' WHERE id=? AND state='ready'",(reply['id'],))
                        store.db.execute("UPDATE inbox_pending SET revision=revision+1,state='awaiting_content',due_at=? "
                                         "WHERE plan_id=? AND creator_id=? AND revision=?",
                                         (time.time()+60,reply['plan_id'],reply['creator_id'],reply['pending_revision']))
                    return 'facts_changed'
        binding=read_binding(report,stopped=stopped)
        if recovering:
            # The original sender binding (account, IM id, market, partner) must still be the reader.
            if not reply.get('sender_identity'):raise CycleError('reply_original_sender_unknown')
            if binding!=reply['sender_identity']:raise CycleError('reply_original_identity_changed')
        from lib.market_accounts import load_config
        sender={'account':load_config(root)['markets']['it']['roles']['communications'],'identity':binding}
        with live(binding,report,stopped=stopped,read_only=recovering) as runtime:
            read_started()  # The first platform read of this attempt: a later failure is a spent check.
            conversation=runtime['reads'].conversation(reply['cid'],reply['oec'])
            if not recovering:
                history=runtime['reads'].history_summary(conversation,include_contents=True)
                Inbox(store).ingest(reply['plan_id'],conversation.conversation_id,reply['oec'],history)
                replies.service.capture(reply['plan_id'],conversation.conversation_id,reply['oec'],history['contents'])
                def permit(scope):
                    kind='card' if card else 'text'
                    if reply['kind'] in ('agent_generated_v2','agent_request_detail_v2','agent_handoff_v2'):
                        from lib.template_library import agent_setting
                        from lib.market_agent_reply import _window_open
                        setting=agent_setting(store,reply['plan_id'])
                        if stopped() or not setting['enabled'] or not authorized_now and not _window_open(setting,store.clock()):
                            raise CycleError('agent_reply_stopped')
                    if scope.get('oecId')!=reply['oec'] or scope.get('conversationId')!=reply['cid'] or \
                            scope.get('componentKind')!=kind or scope.get('requestRef')!=reply['request_ref']:
                        raise CycleError('reply_scope_mismatch')
                    if card and (scope.get('productId'),scope.get('listId'),scope.get('bindingSha256'))!=\
                            (card.product_id,card.list_id,card.binding_sha256):
                        raise CycleError('reply_scope_mismatch')
                    if not card and scope.get('textSha256')!=hashlib.sha256(reply['text'].encode()).hexdigest():
                        raise CycleError('reply_scope_mismatch')
                    allowed=replies.begin(reply['id'],sender);mark();return allowed
                try:
                    with runtime['write_gate']() as mark:
                        receipt=(runtime['adapter'].send_card_once(conversation,card,reply['request_ref'],before_dispatch=permit)
                                 if card else runtime['adapter'].send_once(conversation,reply['text'],reply['request_ref'],before_dispatch=permit))
                    replies.accepted(reply['id'],receipt)
                except Exception:
                    if replies.get(reply['id'])['state']=='inflight':replies.unknown(reply['id'])
                    else:
                        with store.tx():
                            store.db.execute("UPDATE service_reply SET state='cancelled' WHERE id=? AND state='ready'",(reply['id'],))
                            if reply['kind']!='agent_handoff_v2':
                                store.db.execute("UPDATE inbox_pending SET revision=revision+1,state='awaiting_content',due_at=? "
                                                 "WHERE plan_id=? AND creator_id=? AND revision=?",
                                                 (time.time()+60,reply['plan_id'],reply['creator_id'],reply['pending_revision']))
                    raise
            current=replies.get(reply['id']);receipt=json.loads(current['receipt']) if current['receipt'] else {}
            proof=(runtime['adapter'].readback_card(conversation,card,reply['request_ref'],message_id=receipt.get('messageId'))
                   if card else runtime['adapter'].readback(conversation,reply['text'],reply['request_ref'],message_id=receipt.get('messageId')))
            if proof['status']=='confirmed':replies.confirm(reply['id'],proof)
            else:replies.unknown(reply['id'])
    return replies.get(reply['id'])['state']
