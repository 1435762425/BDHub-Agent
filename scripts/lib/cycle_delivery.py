"""Plan-owned card/text outbox. External effects require explicit runtime permits."""
import json,re,uuid
from datetime import datetime,timedelta,timezone
from lib.outreach_policy import MARKETING_COOLDOWN_SECONDS,last_contact_by_creator
from lib.second_cycle import CycleError,digest,encoded,assess_offer
# No local rolling cap on new contacts (user decision 2026-09-24): the platform's daily agency quota decides.
# Set an integer to restore a local cap.  Only the platform's explicit quota receipt (send status 3, check
# code 100, im_limit_reached; seen for BR/UK/MY on 2026-09-24/25) stops new contacts until the next Beijing
# day.  Any other rejection ends only that creator's delivery and keeps its evidence; the same non-quota
# signature repeating is held separately as a systemic refusal, never reported as a confirmed quota.
NEW_CONTACT_LIMIT=None
PLATFORM_REJECTION_HOLD=1
QUOTA_REJECTION=(3,100,'im_limit_reached')
REPEATED_REJECTION_HOLD=3
CARD_ABSENCE_READS=2
CREATE_REFUSED_CODE=201
CARD_ABSENCE_SPAN_SECONDS=300
BEIJING=timezone(timedelta(hours=8))
def rejection_class(native_status,check_code,check_message):
 return 'quota' if (native_status,check_code,check_message)==QUOTA_REJECTION else 'other'
def new_contact_hold(store,plan):
 """Why new contacts wait today: 'platform_quota', 'platform_rejection_repeated', or None."""
 if not store.db.execute("SELECT 1 FROM sqlite_master WHERE name='cycle_platform_signal'").fetchone():return None
 day=datetime.fromtimestamp(store.clock(),BEIJING).replace(hour=0,minute=0,second=0,microsecond=0).timestamp()
 rows=store.db.execute("SELECT s.native_status,s.check_code,s.check_message,count(*) FROM cycle_platform_signal s "
                       "JOIN cycle_delivery d ON d.id=s.delivery_id WHERE d.plan_id=? AND s.outcome='rejected' AND s.at>=? "
                       "GROUP BY 1,2,3",(plan,day)).fetchall()
 if any(rejection_class(*row[:3])=='quota' for row in rows):return 'platform_quota'
 if any(row[3]>=REPEATED_REJECTION_HOLD for row in rows):return 'platform_rejection_repeated'
 return None
def platform_rejections_today(store,plan):
 """Held rejection signals (compatibility count for PLATFORM_REJECTION_HOLD callers)."""
 return PLATFORM_REJECTION_HOLD if new_contact_hold(store,plan) else 0
def capacity_for_candidate(store,plan,c):
 d={'plan_id':plan,'creator_id':c['creatorId'],'oec':c['oecId']};r=store.db.execute('SELECT unlocked FROM relationship WHERE plan_id=? AND creator_id=?',(d['plan_id'],d['creator_id'])).fetchone()
 if not r:return False
 if r[0]:return True
 old=store.db.execute('SELECT reserved FROM cycle_contact_reservation WHERE plan_id=? AND oec=?',(d['plan_id'],d['oec'])).fetchone()
 if old and old[0]>store.clock()-86400:return True
 if NEW_CONTACT_LIMIT is not None:
  count=store.db.execute("SELECT count(*) FROM (SELECT oec FROM cycle_contact_reservation WHERE plan_id=? AND reserved>? UNION SELECT d.oec FROM cycle_delivery d JOIN cycle_delivery_part p ON p.delivery_id=d.id WHERE d.plan_id=? AND p.kind='card' AND p.started>?)",(d['plan_id'],store.clock()-86400,d['plan_id'],store.clock()-86400)).fetchone()[0]
  if count>=NEW_CONTACT_LIMIT:return False
 return platform_rejections_today(store,d['plan_id'])<PLATFORM_REJECTION_HOLD

SCHEMA='''CREATE TABLE IF NOT EXISTS cycle_platform_signal(id INTEGER PRIMARY KEY AUTOINCREMENT,delivery_id TEXT NOT NULL,at REAL NOT NULL,outcome TEXT,code TEXT,native_status INTEGER,check_code INTEGER,check_message TEXT,response_ref TEXT);
CREATE TABLE IF NOT EXISTS cycle_conversation_intent(delivery_id TEXT PRIMARY KEY,request_ref TEXT NOT NULL UNIQUE,state TEXT NOT NULL,cid TEXT,receipt TEXT);
CREATE TABLE IF NOT EXISTS cycle_contact_reservation(plan_id TEXT NOT NULL,oec TEXT NOT NULL,reserved REAL NOT NULL,PRIMARY KEY(plan_id,oec));
CREATE TABLE IF NOT EXISTS cycle_delivery_check(delivery_id TEXT NOT NULL,kind TEXT NOT NULL,checked REAL NOT NULL,payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS cycle_delivery(id TEXT PRIMARY KEY,plan_id TEXT NOT NULL,creator_id TEXT NOT NULL,oec TEXT NOT NULL,pid TEXT NOT NULL,source_id TEXT NOT NULL,snapshot TEXT NOT NULL,created REAL NOT NULL,expires REAL NOT NULL,state TEXT NOT NULL DEFAULT 'ready',UNIQUE(plan_id,creator_id,pid,source_id));
CREATE UNIQUE INDEX IF NOT EXISTS cycle_one_active_delivery ON cycle_delivery(plan_id,creator_id) WHERE state IN ('ready','running','unknown');
CREATE TABLE IF NOT EXISTS cycle_delivery_part(delivery_id TEXT NOT NULL,kind TEXT NOT NULL,request_ref TEXT NOT NULL UNIQUE,state TEXT NOT NULL DEFAULT 'ready',receipt TEXT,confirmation TEXT,started REAL,PRIMARY KEY(delivery_id,kind));
CREATE TRIGGER IF NOT EXISTS cycle_delivery_frozen BEFORE UPDATE OF snapshot,source_id,creator_id,oec,pid ON cycle_delivery BEGIN SELECT RAISE(ABORT,'immutable delivery'); END;'''
class Deliveries:
 def __init__(self,store,*,concurrent_recipient_limit=1):
  if concurrent_recipient_limit not in (1,2,4):raise CycleError('invalid_recipient_concurrency')
  self.s=store;self.concurrent_recipient_limit=concurrent_recipient_limit;store.db.executescript(SCHEMA)
 def get(self,id):
  row=self.s.db.execute('SELECT * FROM cycle_delivery WHERE id=?',(id,)).fetchone()
  if not row:raise CycleError('delivery_missing')
  return {**dict(row),'snapshot':json.loads(row['snapshot']),'parts':[dict(p) for p in self.s.db.execute("SELECT * FROM cycle_delivery_part WHERE delivery_id=? ORDER BY CASE kind WHEN 'card' THEN 0 ELSE 1 END",(id,))]}
 def prepare(self,plan,candidate):
  source=candidate['source']['sourceId'];id='delivery-'+digest([plan,candidate['creatorId'],candidate['pid'],source])[:32]
  with self.s.tx():
   prior=self.s.db.execute('SELECT snapshot FROM cycle_delivery WHERE id=?',(id,)).fetchone()
   if prior:
    if json.loads(prior[0])!=candidate:raise CycleError('delivery_source_changed')
    return self.get(id)
   self._eligible(plan,candidate)
   self.s.db.execute('INSERT INTO cycle_delivery VALUES(?,?,?,?,?,?,?,?,?,?)',(id,plan,candidate['creatorId'],candidate['oecId'],candidate['pid'],source,encoded(candidate),self.s.clock(),self.s.clock()+1800,'ready'))
   for kind in ('card','text'):self.s.db.execute('INSERT INTO cycle_delivery_part(delivery_id,kind,request_ref) VALUES(?,?,?)',(id,kind,str(uuid.uuid4())))
   if candidate.get('sendAllocation'):
    if not self.candidate_capacity_available(plan,candidate):raise CycleError('new_contact_capacity_reached')
    from lib.outreach_allocation import claim
    claim(self.s,plan,candidate,id)
  return self.get(id)
 def _eligible(self,plan,c,*,current_delivery_id=None,allow_invitation_text=False):
  from lib.outreach_policy import marketing_isolated
  if marketing_isolated(self.s.db,plan,c['creatorId'],c['oecId']):raise CycleError('marketing_isolated')
  p=self.s._plan(plan);r=self.s.db.execute('SELECT * FROM relationship WHERE plan_id=? AND creator_id=?',(plan,c['creatorId'])).fetchone()
  if p['state']!='active' or p['revision']!=c['planRevision']:raise CycleError('plan_changed')
  from lib.invitation_continuation import allowed
  continuation=allow_invitation_text and current_delivery_id and allowed(self.s,current_delivery_id)
  if not r or r['oec']!=c['oecId'] or r['mode']!='auto' or r['rejected'] or \
     (r['inbox_until'] or r['revision']!=c['controlRevision']) and not continuation:raise CycleError('relationship_changed')
  from lib.video_window import current as video_current
  if c.get('source',{}).get('sourceClass')=='B' and not video_current(c['source'].get('videoReleasedAt'),self.s.clock()):raise CycleError('video_lead_expired')
  if not assess_offer(c['offer'],self.s.clock())['eligible']:raise CycleError('offer_not_eligible')
  if self.s.db.execute("SELECT 1 FROM sqlite_master WHERE name='cycle_card_creation'").fetchone() and self.s.db.execute("SELECT 1 FROM cycle_card_creation WHERE plan_id=? AND offer_json=? AND state='invalidated'",(plan,encoded(c['offer']))).fetchone():raise CycleError('offer_currently_ineligible')
  current={o['offerKey']:o for _,o in self.s._offers(plan)}
  if digest(current.get(c['offer']['offerKey']))!=digest(c['offer']):raise CycleError('offer_changed')
  if c['message']['version']!=4 or c['message']['deliveryOrder']!='card_then_text':raise CycleError('message_not_v4')
  prior=last_contact_by_creator(self.s.db,plan,creator_id=c['creatorId'],current_delivery_id=current_delivery_id).get(c['creatorId'])
  if prior is not None and self.s.clock()-prior<MARKETING_COOLDOWN_SECONDS:raise CycleError('marketing_cooldown')
 def begin(self,id,kind,*,authorized_snapshot_hash,recipient_verified=False,allowance_verified=False):
  # Authorization is supplied by the plan runtime, never inferred from existence of a review.
  with self.s.tx():
   d=self.get(id);c=d['snapshot']
   if authorized_snapshot_hash!=digest(c):raise CycleError('execution_authorization_missing')
   if not recipient_verified or not allowance_verified:raise CycleError('execution_evidence_missing')
   self._eligible(d['plan_id'],c,current_delivery_id=id,allow_invitation_text=kind=='text')
   if self.s.clock()>=d['expires']:raise CycleError('delivery_expired')
   if self.s.db.execute("SELECT 1 FROM cycle_delivery WHERE plan_id=? AND state='unknown'",(d['plan_id'],)).fetchone():raise CycleError('delivery_unknown')
   if self.s.db.execute("SELECT 1 FROM sqlite_master WHERE name='service_reply'").fetchone() and self.s.db.execute("SELECT 1 FROM service_reply WHERE plan_id=? AND state IN ('inflight','accepted','unknown')",(d['plan_id'],)).fetchone():raise CycleError('reply_reconciliation_required')
   part=next((p for p in d['parts'] if p['kind']==kind),None)
   if not part or part['state']!='ready':raise CycleError('part_not_ready')
   # A quarantined delivery keeps its unknown part as evidence but no longer occupies the dispatch slot.
   pending=self.s.db.execute("SELECT d.creator_id FROM cycle_delivery_part p JOIN cycle_delivery d ON d.id=p.delivery_id WHERE d.plan_id=? AND d.state<>'quarantined_unknown' AND p.state IN ('inflight','accepted','unknown')",(d['plan_id'],)).fetchall()
   if len(pending)>=self.concurrent_recipient_limit or any(r['creator_id']==d['creator_id'] for r in pending):raise CycleError('verify_before_dispatch')
   if kind=='text' and d['parts'][0]['state']!='confirmed':raise CycleError('card_not_confirmed')
   self.s.db.execute("UPDATE cycle_delivery_part SET state='inflight',started=? WHERE delivery_id=? AND kind=?",(self.s.clock(),id,kind));self.s.db.execute("UPDATE cycle_delivery SET state='running' WHERE id=?",(id,))
  return {'dispatchAllowed':True,'requestRef':part['request_ref'],'kind':kind}
 def interrupted_by_inquiry(self,id):
  with self.s.tx():
   d=self.get(id)
   if d['state']=='unknown' or not self.s.db.execute("SELECT 1 FROM sqlite_master WHERE name='inbox_pending'").fetchone():return False
   from lib.invitation_continuation import allowed,ACTIVE_PENDING
   if allowed(self.s,id):return False
   pending=self.s.db.execute('SELECT state FROM inbox_pending WHERE plan_id=? AND creator_id=?',(d['plan_id'],d['creator_id'])).fetchone()
   if not pending or pending[0] not in ACTIVE_PENDING|{'human','reviewed_ready'} or d['parts'][0]['state']!='confirmed' or d['parts'][1]['started'] is not None:return False
   self.s.db.execute("UPDATE cycle_delivery_part SET state='cancelled' WHERE delivery_id=? AND kind='text' AND started IS NULL",(id,));self.s.db.execute("UPDATE cycle_delivery SET state='partial_delivery' WHERE id=?",(id,));return True
 def cancel_pending_text(self,id,reason):
  """Keep the confirmed card, but settle text that never reached the platform."""
  if not isinstance(reason,str) or not re.fullmatch(r'[A-Za-z0-9_]{1,100}',reason):raise CycleError('delivery_cancel_reason_invalid')
  with self.s.tx():
   d=self.get(id);card,text=d['parts']
   if card['kind']!='card' or card['state']!='confirmed' or text['kind']!='text' or \
      text['state']!='ready' or text['started'] is not None or text['receipt'] or text['confirmation']:
    raise CycleError('delivery_partial_cancel_not_safe')
   self.s.db.execute("UPDATE cycle_delivery_part SET state='cancelled' WHERE delivery_id=? AND kind='text'",(id,))
   self.s.db.execute("UPDATE cycle_delivery SET state='partial_delivery' WHERE id=?",(id,))
   self.s.db.execute('INSERT INTO cycle_delivery_check VALUES(?,?,?,?)',
     (id,'text',self.s.clock(),encoded({'status':'failed_known','reason':reason,'platformWrites':0})))
  return self.get(id)
 def cancel_expired_unsubmitted(self,plan,modes):
  """Settle frozen deliveries that expired with nothing left in flight (across a pause, after the day's quota ran out,
  or while an unknown card waited for readback) so a sender never picks them up again or sends them late.  A delivery
  whose card is confirmed and whose text never started keeps the card as partial_delivery.  Anything that may have
  reached the platform -- an unconfirmed conversation create, a started or unknown component -- is left for verification."""
  marks=','.join('?'*len(modes));settled=[]
  for row in self.s.db.execute(f"""SELECT d.id,d.state FROM cycle_delivery d LEFT JOIN cycle_conversation_intent c ON c.delivery_id=d.id
    WHERE d.plan_id=? AND d.expires<=? AND (d.state='running' OR d.state='ready' AND (c.state IS NULL OR c.state IN ('ready','confirmed')))
      AND json_extract(d.snapshot,'$.executionMode') IN ({marks}) ORDER BY d.created""",(plan,self.s.clock(),*modes)).fetchall():
   try:
    if row[1]=='ready':self.cancel_unsubmitted(row[0],'delivery_expired')
    else:self.cancel_pending_text(row[0],'delivery_expired')
   except CycleError:continue
   settled.append(row[0])
  return settled
 def cancel_unsubmitted(self,id,reason):
  """Settle a known preflight exclusion only when no component could have reached the platform."""
  if not isinstance(reason,str) or not re.fullmatch(r'[A-Za-z0-9_]{1,100}',reason):raise CycleError('delivery_cancel_reason_invalid')
  with self.s.tx():
   d=self.get(id)
   if d['state']=='cancelled':return d
   intent=self.conversation_intent(id)
   if d['state'] not in ('ready','running') or intent and intent['state'] not in ('ready','confirmed'):
    raise CycleError('delivery_cancel_not_safe')
   if any(p['state']!='ready' or p['started'] is not None or p['receipt'] is not None or p['confirmation'] is not None for p in d['parts']):
    raise CycleError('delivery_cancel_not_safe')
   self.s.db.execute("UPDATE cycle_delivery_part SET state='cancelled' WHERE delivery_id=?",(id,))
   self.s.db.execute("UPDATE cycle_delivery SET state='cancelled' WHERE id=?",(id,))
   self.s.db.execute("INSERT INTO cycle_delivery_check VALUES(?,?,?,?)",
     (id,'preflight',self.s.clock(),encoded({'status':'failed_known','reason':reason,'platformWrites':0})))
   self.s.db.execute("DELETE FROM cycle_contact_reservation WHERE plan_id=? AND oec=? AND reserved>=?",
     (d['plan_id'],d['oec'],d['created']))
  return self.get(id)
 def isolate_technical(self,id,kind,reason):
  """Marketing-only isolation; retain unknown parts, real cases and relationship controls."""
  with self.s.tx():
   d=self.get(id)
   if d['state']=='quarantined_unknown':return False
   if d['state']!='unknown':raise CycleError('isolation_scope_changed')
   self.s.db.execute("UPDATE cycle_delivery SET state='quarantined_unknown' WHERE id=?",(id,))
   self.s.db.execute('INSERT INTO cycle_delivery_check VALUES(?,?,?,?)',(id,'technical_isolation',self.s.clock(),encoded(
    {'status':'quarantined_unknown','component':kind,'reason':reason,'marketingBlocked':True,'platformWrites':0})))
  return True
 def quarantine_absent_card(self,id):
  """Isolate a card whose result stayed unknown although at least two history reads, five minutes or more apart,
  found no such message: marketing is blocked without creating a human case.  A read that could not see the
  history is not evidence of absence, and nothing is resent."""
  with self.s.tx():
   d=self.get(id)
   if d['state']!='unknown' or len(d['parts'])!=2:return False
   card,text=d['parts']
   if card['kind']!='card' or card['state']!='unknown' or text['kind']!='text' or text['state']!='ready' or \
      text['started'] is not None or text['receipt'] or text['confirmation']:return False
   reads=[row[0] for row in self.s.db.execute("""SELECT checked FROM cycle_delivery_check WHERE delivery_id=? AND kind='card'
     AND json_extract(payload,'$.reason')='it_delivery_history_not_found' AND json_extract(payload,'$.messageId') IS NULL
     ORDER BY checked""",(id,))]
   if len(reads)<CARD_ABSENCE_READS or reads[-1]-reads[0]<CARD_ABSENCE_SPAN_SECONDS:return False
   self.s.db.execute("UPDATE cycle_delivery SET state='quarantined_unknown' WHERE id=?",(id,))
   self.s.db.execute('INSERT INTO cycle_delivery_check VALUES(?,?,?,?)',(id,'technical_isolation',self.s.clock(),encoded(
     {'status':'quarantined_unknown','component':'card','reason':'card_result_unknown','absentReads':len(reads),
      'firstAbsentAt':reads[0],'lastAbsentAt':reads[-1],'marketingBlocked':True,'platformWrites':0})))
  return True
 def quarantine_refused_creates(self,plan,modes):
  """Isolate creators whose conversation create the platform refused with business code 201 (the old system's
  conversation_business_rejected: no conversation was resolved).  Nothing was sent to them; they move to human
  handling with an open case and are never sent to automatically, and the market stops waiting on them."""
  from lib.cycle_service import Service
  Service(self.s)
  marks=','.join('?'*len(modes));isolated=[]
  for (did,) in self.s.db.execute(f"""SELECT DISTINCT d.id FROM cycle_delivery d JOIN cycle_platform_signal s ON s.delivery_id=d.id
    WHERE d.plan_id=? AND d.state='unknown' AND s.code='it_delivery_create_unknown' AND s.native_status=?
      AND json_extract(d.snapshot,'$.executionMode') IN ({marks})""",(plan,CREATE_REFUSED_CODE,*modes)).fetchall():
   with self.s.tx():
    d=self.get(did);intent=self.conversation_intent(did)
    if d['state']!='unknown' or not intent or intent['state']!='inflight' or intent['cid'] or intent['receipt'] or \
       any(p['state']!='ready' or p['started'] is not None or p['receipt'] or p['confirmation'] for p in d['parts']):continue
    rel=self.s.db.execute('SELECT mode FROM relationship WHERE plan_id=? AND creator_id=? AND oec=?',
                          (d['plan_id'],d['creator_id'],d['oec'])).fetchone()
    if not rel:continue
    self.s.db.execute("UPDATE cycle_delivery SET state='quarantined_unknown' WHERE id=?",(did,))
    if rel['mode']=='auto':
     self.s.db.execute("UPDATE relationship SET mode='human',revision=revision+1 WHERE plan_id=? AND creator_id=?",
                       (d['plan_id'],d['creator_id']))
    now=self.s.clock();case_id='case-'+digest([did,'create_refused'])[:24]
    self.s.db.execute("INSERT OR IGNORE INTO service_case VALUES(?,?,?,'open',0,'conversation_business_rejected',?,?,'not_sent')",
                      (case_id,d['plan_id'],d['creator_id'],now,now))
    self.s.db.execute('INSERT INTO cycle_delivery_check VALUES(?,?,?,?)',(did,'conversation_quarantine',now,encoded(
      {'status':'quarantined_unknown','reason':'conversation_business_rejected','nativeStatus':CREATE_REFUSED_CODE,
       'originalRequestRef':intent['request_ref'],'cardStarted':False,'textStarted':False,'platformWrites':0})))
   isolated.append(did)
  return isolated
 def quarantine_unknown_conversation(self,id,request_id,*,observed_conversations,matching_conversations):
  """Isolate one ambiguous create without altering its original request or parts."""
  if not isinstance(request_id,str) or not re.fullmatch(r'[A-Za-z0-9._:-]{8,120}',request_id):raise CycleError('quarantine_request_invalid')
  if type(observed_conversations) is not int or observed_conversations<0 or matching_conversations!=0:
   raise CycleError('quarantine_evidence_invalid')
  with self.s.tx():
   d=self.get(id)
   prior=self.s.db.execute("SELECT payload FROM cycle_delivery_check WHERE delivery_id=? AND kind='conversation_quarantine' ORDER BY checked DESC LIMIT 1",(id,)).fetchone()
   if prior:
    if json.loads(prior[0]).get('requestId')!=request_id:raise CycleError('quarantine_request_conflict')
    return self.get(id)
   intent=self.conversation_intent(id)
   if d['state']!='unknown' or not intent or intent['state']!='inflight' or intent['cid'] or intent['receipt'] or \
      any(p['state']!='ready' or p['started'] is not None or p['receipt'] or p['confirmation'] for p in d['parts']):
    raise CycleError('quarantine_scope_changed')
   rel=self.s.db.execute('SELECT mode FROM relationship WHERE plan_id=? AND creator_id=? AND oec=?',
                         (d['plan_id'],d['creator_id'],d['oec'])).fetchone()
   if not rel:raise CycleError('relationship_missing')
   self.s.db.execute("UPDATE cycle_delivery SET state='quarantined_unknown' WHERE id=?",(id,))
   now=self.s.clock()
   evidence={'status':'quarantined_unknown','reason':'conversation_create_result_unknown',
             'requestId':request_id,'originalRequestRef':intent['request_ref'],
             'observedConversations':observed_conversations,'matchingConversations':0,
             'platformWrites':0,'cardStarted':False,'textStarted':False}
   self.s.db.execute('INSERT INTO cycle_delivery_check VALUES(?,?,?,?)',
                     (id,'conversation_quarantine',now,encoded(evidence)))
  return self.get(id)
 def reserve_contact(self,id):
  with self.s.tx():
   d=self.get(id)
   if not self.contact_capacity_available(id):raise CycleError('new_contact_capacity_reached')
   r=self.s.db.execute('SELECT unlocked FROM relationship WHERE plan_id=? AND creator_id=?',(d['plan_id'],d['creator_id'])).fetchone()
   if r[0]:return
   old=self.s.db.execute('SELECT reserved FROM cycle_contact_reservation WHERE plan_id=? AND oec=?',(d['plan_id'],d['oec'])).fetchone()
   if old and old[0]>self.s.clock()-86400:return
   self.s.db.execute('INSERT INTO cycle_contact_reservation VALUES(?,?,?) ON CONFLICT(plan_id,oec) DO UPDATE SET reserved=excluded.reserved',(d['plan_id'],d['oec'],self.s.clock()))
 def contact_capacity_available(self,id):
  d=self.get(id);return self.candidate_capacity_available(d['plan_id'],{'creatorId':d['creator_id'],'oecId':d['oec']})
 def candidate_capacity_available(self,plan,c):
  return capacity_for_candidate(self.s,plan,c)
 def conversation_intent(self,id):
  row=self.s.db.execute('SELECT * FROM cycle_conversation_intent WHERE delivery_id=?',(id,)).fetchone()
  return dict(row) if row else None
 def prepare_conversation(self,id):
  with self.s.tx():
   self.s.db.execute("INSERT OR IGNORE INTO cycle_conversation_intent VALUES(?,?,'ready',NULL,NULL)",(id,str(uuid.uuid4())))
  return self.conversation_intent(id)
 def begin_conversation(self,id,approved_hash):
  self.reserve_contact(id)
  with self.s.tx():
   d=self.get(id)
   if approved_hash!=digest(d['snapshot']):raise CycleError('execution_authorization_missing')
   self._eligible(d['plan_id'],d['snapshot'],current_delivery_id=id)
   if self.s.clock()>=d['expires']:raise CycleError('delivery_expired')
   if self.s.db.execute("SELECT 1 FROM cycle_delivery WHERE plan_id=? AND state='unknown'",(d['plan_id'],)).fetchone():raise CycleError('delivery_unknown')
   row=self.conversation_intent(id)
   if not row or row['state']!='ready':raise CycleError('conversation_attempt_not_ready')
   self.s.db.execute("UPDATE cycle_conversation_intent SET state='inflight' WHERE delivery_id=?",(id,))
  return row['request_ref']
 def save_conversation(self,id,receipt):
  with self.s.tx():
   row=self.conversation_intent(id)
   if row['state']!='inflight' or receipt.get('requestRef')!=row['request_ref'] or not str(receipt.get('conversationId','')).isdigit():raise CycleError('conversation_receipt_invalid')
   self.s.db.execute("UPDATE cycle_conversation_intent SET state='received',cid=?,receipt=? WHERE delivery_id=?",(receipt['conversationId'],encoded(receipt),id))
 def confirm_conversation(self,id,cid,oec):
  with self.s.tx():
   d=self.get(id);r=self.conversation_intent(id)
   if not r or r['cid']!=cid or d['oec']!=oec:raise CycleError('conversation_identity_mismatch')
   self.s.db.execute("UPDATE cycle_conversation_intent SET state='confirmed' WHERE delivery_id=?",(id,))
 def receipt(self,id,kind,receipt):
  with self.s.tx():
   p=next(p for p in self.get(id)['parts'] if p['kind']==kind)
   if p['state']!='inflight' or receipt.get('requestRef')!=p['request_ref']:raise CycleError('receipt_mismatch')
   self.s.db.execute("UPDATE cycle_delivery_part SET state='accepted',receipt=? WHERE delivery_id=? AND kind=?",(encoded(receipt),id,kind))
 def rejected(self,id,kind,error):
  if getattr(error,'outcome',None)!='rejected' or getattr(error,'native_status',None) not in (1,2,3,4,5):raise CycleError('rejection_not_verified')
  with self.s.tx():
   self.s.db.execute("UPDATE cycle_delivery_part SET state='rejected' WHERE delivery_id=? AND kind=? AND state IN ('inflight','accepted','unknown')",(id,kind))
   any_sent=self.s.db.execute("SELECT 1 FROM cycle_delivery_part WHERE delivery_id=? AND state='confirmed'",(id,)).fetchone()
   self.s.db.execute('UPDATE cycle_delivery SET state=? WHERE id=?',('partial_delivery' if any_sent else 'rejected',id))
 def unknown(self,id,kind):
  with self.s.tx():
   self.s.db.execute("UPDATE cycle_delivery_part SET state='unknown' WHERE delivery_id=? AND kind=? AND state IN ('inflight','accepted')",(id,kind));self.s.db.execute("UPDATE cycle_delivery SET state='unknown' WHERE id=? AND state NOT IN ('confirmed','quarantined_unknown')",(id,))
 def record_check(self,id,kind,evidence):
  self.s.db.execute('INSERT INTO cycle_delivery_check VALUES(?,?,?,?)',(id,kind,self.s.clock(),encoded(evidence)))
 def confirm(self,id,kind,evidence,*,close_unsubmitted=False):
  with self.s.tx():
   d=self.get(id);p=next(p for p in d['parts'] if p['kind']==kind)
   if p['state']=='confirmed':
    if p['confirmation']!=encoded(evidence):raise CycleError('confirmation_conflict')
    return
   if p['state'] not in ('inflight','accepted','unknown') or evidence.get('status')!='confirmed' or evidence.get('requestRef')!=p['request_ref'] or evidence.get('oecId')!=d['oec'] or evidence.get('kind')!=kind or not evidence.get('messageId') or not evidence.get('evidenceRef'):raise CycleError('confirmation_invalid')
   self.s.db.execute("UPDATE cycle_delivery_part SET state='confirmed',confirmation=? WHERE delivery_id=? AND kind=?",(encoded(evidence),id,kind))
   if close_unsubmitted and kind=='card' and d['state']!='quarantined_unknown' and d['parts'][1]['state']=='ready':
    self.s.db.execute("UPDATE cycle_delivery_part SET state='cancelled' WHERE delivery_id=? AND kind='text' AND started IS NULL",(id,))
    self.s.db.execute("UPDATE cycle_delivery SET state='partial_delivery' WHERE id=?",(id,))
    self.s.db.execute('INSERT INTO cycle_delivery_check VALUES(?,?,?,?)',(id,'text',self.s.clock(),encoded(
      {'status':'not_submitted','reason':'reconciled_card_text_not_submitted','platformWrites':0})))
    return
   remaining=self.s.db.execute("SELECT count(*) FROM cycle_delivery_part WHERE delivery_id=? AND state<>'confirmed'",(id,)).fetchone()[0]
   if d['state']!='quarantined_unknown':
    self.s.db.execute('UPDATE cycle_delivery SET state=? WHERE id=?',('confirmed' if not remaining else 'running',id))

def delivery_status(store,plan):
 if not store.db.execute("SELECT 1 FROM sqlite_master WHERE name='cycle_delivery'").fetchone():return None
 bulk=None
 if store.db.execute("SELECT 1 FROM sqlite_master WHERE name='cycle_bulk'").fetchone():
  b=store.db.execute('SELECT * FROM cycle_bulk WHERE plan_id=? ORDER BY created DESC LIMIT 1',(plan,)).fetchone()
  if b:bulk={'id':b['id'],'state':b['state'],'target':b['target'],'counts':dict(store.db.execute('SELECT state,count(*) FROM cycle_bulk_item WHERE batch_id=? GROUP BY state',(b['id'],)))}
 return {'bulk':bulk,'states':dict(store.db.execute('SELECT state,count(*) FROM cycle_delivery WHERE plan_id=? GROUP BY state',(plan,))),
 'confirmedParts':store.db.execute("SELECT count(*) FROM cycle_delivery_part p JOIN cycle_delivery d ON d.id=p.delivery_id WHERE d.plan_id=? AND p.state='confirmed'",(plan,)).fetchone()[0]}
