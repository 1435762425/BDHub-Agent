"""Market-scoped V2 Agent text delivery with one durable intent and exact readback."""
import hashlib
import json
from pathlib import Path

from lib.cycle_inbox import Inbox
from lib.cycle_service import Service
from lib.market_im_runtime import authenticated,write_gate
from lib.second_cycle import CycleError
from lib.template_library import agent_setting


def _window_open(setting,stamp):
 from datetime import datetime,timedelta,timezone
 local=datetime.fromtimestamp(stamp,timezone(timedelta(hours=8)))
 minute=local.hour*60+local.minute
 start=sum(int(part)*factor for part,factor in zip(setting['replyStart'].split(':'),(60,1)))
 end=sum(int(part)*factor for part,factor in zip(setting['replyEnd'].split(':'),(60,1)))
 return start<=minute<end


def run_reply(root,store,replies,reply,market,*,pilot=False,authorized_now=False,stopped=lambda:False,read_started=lambda:None):
 root=Path(root);report={'market':market,'platformWrites':0,'realSends':0}
 if reply['kind'] not in ('agent_generated_v2','agent_request_detail_v2','agent_handoff_v2'):
  raise CycleError('market_agent_reply_kind_invalid')
 recovering=reply['state'] in ('inflight','accepted','unknown','isolated')
 if not recovering and not agent_setting(store,reply['plan_id'])['enabled']:
  raise CycleError('agent_reply_disabled')
 if recovering:
  # Only the frozen original sender may read back its own reply; never a replacement account.
  from lib.market_accounts import load_config
  if not reply.get('sender_account') or not reply.get('sender_identity'):raise CycleError('reply_original_sender_unknown')
  if load_config(root)['markets'][market]['roles']['communications']!=reply['sender_account']:
   raise CycleError('reply_original_account_changed')
 with authenticated(root,market,report,canary=pilot,read_only=recovering,
                    capability='agent_reply',stopped=stopped) as runtime:
  session=runtime['session'];adapter=runtime['adapter']
  sender={'account':str(runtime['auth'].account_name),'identity':str(runtime['auth'].im_id)}
  if recovering:
   if sender['account']!=reply['sender_account']:raise CycleError('reply_original_account_changed')
   if sender['identity']!=reply['sender_identity']:raise CycleError('reply_original_identity_changed')
   from lib.italy_im_delivery import ItalyImDeliveryAdapter
   adapter=ItalyImDeliveryAdapter(runtime['auth'],session)
  read_started()  # The first platform read of this attempt: a later failure is a spent check.
  conversation=session.conversation(reply['cid'],reply['oec'])
  if not recovering:
   history=session.history_summary(conversation,include_events=True,include_contents=True)
   Inbox(store).ingest(reply['plan_id'],reply['cid'],reply['oec'],history)
   Service(store).capture(reply['plan_id'],reply['cid'],reply['oec'],history.get('contents',[]))
   if stopped():raise CycleError('agent_reply_stopped')
   with write_gate(root,runtime['auth'],stopped=stopped) as mark:
    def permit(scope):
     current=agent_setting(store,reply['plan_id'])
     if stopped() or not current['enabled'] or not authorized_now and not _window_open(current,store.clock()):
      raise CycleError('agent_reply_stopped')
     if scope.get('market')!=market or scope.get('oecId')!=reply['oec'] or \
        scope.get('conversationId')!=reply['cid'] or scope.get('componentKind')!='text' or \
        scope.get('requestRef')!=reply['request_ref'] or \
        scope.get('textSha256')!=hashlib.sha256(reply['text'].encode()).hexdigest():
      raise CycleError('reply_scope_mismatch')
     allowed=replies.begin(reply['id'],sender);mark();report['platformWrites']+=1;return allowed
    try:
     receipt=adapter.send_once(conversation,reply['text'],reply['request_ref'],before_dispatch=permit)
     replies.accepted(reply['id'],receipt)
    except Exception as error:
     latest=replies.get(reply['id'])
     # The adapter wraps before_dispatch refusals. Only a proven unsubmitted reply may recover
     # the original local reason; accepted/unknown replies remain on original-intent reads.
     original=getattr(error,'__context__',None)
     if latest['state']=='ready' and latest['started'] is None and not latest['receipt'] and not latest['proof'] and \
          getattr(error,'code',None)=='it_delivery_dispatch_not_allowed' and isinstance(original,CycleError):
      error=original
     if latest['state']=='inflight':replies.unknown(reply['id'])
     elif latest['state']=='ready' and latest['started'] is None and not latest['receipt'] and not latest['proof'] and \
          str(error) in ('reply_context_changed','reply_human_control','handoff_changed'):
      with store.tx():
       store.db.execute("UPDATE service_reply SET state='cancelled',proof=? WHERE id=? AND state='ready' AND started IS NULL",
                        (json.dumps({'status':'cancelled','reason':str(error),'platformWrites':0}),reply['id']))
      return report|{'state':'cancelled'}
     raise
  current=replies.get(reply['id']);receipt=json.loads(current['receipt']) if current['receipt'] else {}
  proof=adapter.readback(conversation,reply['text'],reply['request_ref'],message_id=receipt.get('messageId'))
  if proof['status']=='confirmed':replies.confirm(reply['id'],proof)
  else:replies.unknown(reply['id'])
  report['state']=replies.get(reply['id'])['state'];report['realSends']=int(report['state']=='confirmed' and not recovering)
  if pilot and report['state']=='confirmed':
   from lib.account_identity import promote_capabilities
   promote_capabilities(store,market=market,account=runtime['account'].name,
                        capabilities=['agent_reply'],evidence_ref='service-reply:'+reply['id'])
 return report
