"""Plan-owned card/text outbox. External effects require explicit runtime permits."""
import json,re,uuid
from lib.outreach_policy import MARKETING_COOLDOWN_SECONDS,last_contact_by_creator
from lib.second_cycle import CycleError,digest,encoded,assess_offer
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
  return self.get(id)
 def _eligible(self,plan,c,*,current_delivery_id=None):
  p=self.s._plan(plan);r=self.s.db.execute('SELECT * FROM relationship WHERE plan_id=? AND creator_id=?',(plan,c['creatorId'])).fetchone()
  if p['state']!='active' or p['revision']!=c['planRevision']:raise CycleError('plan_changed')
  if not r or r['oec']!=c['oecId'] or r['mode']!='auto' or r['rejected'] or r['inbox_until'] or r['revision']!=c['controlRevision']:raise CycleError('relationship_changed')
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
   self._eligible(d['plan_id'],c,current_delivery_id=id)
   if self.s.clock()>=d['expires']:raise CycleError('delivery_expired')
   if self.s.db.execute("SELECT 1 FROM cycle_delivery WHERE plan_id=? AND state='unknown'",(d['plan_id'],)).fetchone():raise CycleError('delivery_unknown')
   if self.s.db.execute("SELECT 1 FROM sqlite_master WHERE name='service_reply'").fetchone() and self.s.db.execute("SELECT 1 FROM service_reply WHERE plan_id=? AND state IN ('inflight','accepted','unknown')",(d['plan_id'],)).fetchone():raise CycleError('reply_reconciliation_required')
   part=next((p for p in d['parts'] if p['kind']==kind),None)
   if not part or part['state']!='ready':raise CycleError('part_not_ready')
   pending=self.s.db.execute("SELECT d.creator_id FROM cycle_delivery_part p JOIN cycle_delivery d ON d.id=p.delivery_id WHERE d.plan_id=? AND p.state IN ('inflight','accepted','unknown')",(d['plan_id'],)).fetchall()
   if len(pending)>=self.concurrent_recipient_limit or any(r['creator_id']==d['creator_id'] for r in pending):raise CycleError('verify_before_dispatch')
   if kind=='text' and d['parts'][0]['state']!='confirmed':raise CycleError('card_not_confirmed')
   self.s.db.execute("UPDATE cycle_delivery_part SET state='inflight',started=? WHERE delivery_id=? AND kind=?",(self.s.clock(),id,kind));self.s.db.execute("UPDATE cycle_delivery SET state='running' WHERE id=?",(id,))
  return {'dispatchAllowed':True,'requestRef':part['request_ref'],'kind':kind}
 def interrupted_by_inquiry(self,id):
  with self.s.tx():
   d=self.get(id)
   if d['state']=='unknown' or not self.s.db.execute("SELECT 1 FROM sqlite_master WHERE name='inbox_pending'").fetchone():return False
   pending=self.s.db.execute('SELECT 1 FROM inbox_pending WHERE plan_id=? AND creator_id=?',(d['plan_id'],d['creator_id'])).fetchone()
   if not pending or d['parts'][0]['state']!='confirmed' or d['parts'][1]['started'] is not None:return False
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
 def cancel_unsubmitted(self,id,reason):
  """Settle a known preflight exclusion only when no component could have reached the platform."""
  if not isinstance(reason,str) or not re.fullmatch(r'[A-Za-z0-9_]{1,100}',reason):raise CycleError('delivery_cancel_reason_invalid')
  with self.s.tx():
   d=self.get(id)
   if d['state']=='cancelled':return d
   if any(p['state']!='ready' or p['started'] is not None or p['receipt'] is not None or p['confirmation'] is not None for p in d['parts']):
    raise CycleError('delivery_cancel_not_safe')
   self.s.db.execute("UPDATE cycle_delivery_part SET state='cancelled' WHERE delivery_id=?",(id,))
   self.s.db.execute("UPDATE cycle_delivery SET state='cancelled' WHERE id=?",(id,))
   self.s.db.execute("INSERT INTO cycle_delivery_check VALUES(?,?,?,?)",
     (id,'preflight',self.s.clock(),encoded({'status':'failed_known','reason':reason,'platformWrites':0})))
   self.s.db.execute("DELETE FROM cycle_contact_reservation WHERE plan_id=? AND oec=? AND reserved>=?",
     (d['plan_id'],d['oec'],d['created']))
  return self.get(id)
 def quarantine_unknown_conversation(self,id,request_id,*,observed_conversations,matching_conversations):
  """Isolate one ambiguous create without altering its original request or parts."""
  if not isinstance(request_id,str) or not re.fullmatch(r'[A-Za-z0-9._:-]{8,120}',request_id):raise CycleError('quarantine_request_invalid')
  if type(observed_conversations) is not int or observed_conversations<0 or matching_conversations!=0:
   raise CycleError('quarantine_evidence_invalid')
  from lib.cycle_service import Service
  Service(self.s)
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
   if rel['mode']=='auto':
    self.s.db.execute("UPDATE relationship SET mode='human',revision=revision+1 WHERE plan_id=? AND creator_id=?",
                      (d['plan_id'],d['creator_id']))
   now=self.s.clock();case_id='case-'+digest([id,'conversation_quarantine'])[:24]
   self.s.db.execute("INSERT OR IGNORE INTO service_case VALUES(?,?,?,'open',0,'conversation_create_unknown',?,?,'not_sent')",
                     (case_id,d['plan_id'],d['creator_id'],now,now))
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
  d=self.get(id);r=self.s.db.execute('SELECT unlocked FROM relationship WHERE plan_id=? AND creator_id=?',(d['plan_id'],d['creator_id'])).fetchone()
  if not r:return False
  if r[0]:return True
  old=self.s.db.execute('SELECT reserved FROM cycle_contact_reservation WHERE plan_id=? AND oec=?',(d['plan_id'],d['oec'])).fetchone()
  if old and old[0]>self.s.clock()-86400:return True
  count=self.s.db.execute("SELECT count(*) FROM (SELECT oec FROM cycle_contact_reservation WHERE plan_id=? AND reserved>? UNION SELECT d.oec FROM cycle_delivery d JOIN cycle_delivery_part p ON p.delivery_id=d.id WHERE d.plan_id=? AND p.kind='card' AND p.started>?)",(d['plan_id'],self.s.clock()-86400,d['plan_id'],self.s.clock()-86400)).fetchone()[0]
  return count<500
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
   if row['state']!='inflight' or not str(receipt.get('conversationId','')).isdigit():raise CycleError('conversation_receipt_invalid')
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
   self.s.db.execute("UPDATE cycle_delivery_part SET state='unknown' WHERE delivery_id=? AND kind=? AND state IN ('inflight','accepted')",(id,kind));self.s.db.execute("UPDATE cycle_delivery SET state='unknown' WHERE id=? AND state<>'confirmed'",(id,))
 def record_check(self,id,kind,evidence):
  self.s.db.execute('INSERT INTO cycle_delivery_check VALUES(?,?,?,?)',(id,kind,self.s.clock(),encoded(evidence)))
 def confirm(self,id,kind,evidence):
  with self.s.tx():
   d=self.get(id);p=next(p for p in d['parts'] if p['kind']==kind)
   if p['state']=='confirmed':
    if p['confirmation']!=encoded(evidence):raise CycleError('confirmation_conflict')
    return
   if p['state'] not in ('inflight','accepted','unknown') or evidence.get('status')!='confirmed' or evidence.get('requestRef')!=p['request_ref'] or evidence.get('oecId')!=d['oec'] or evidence.get('kind')!=kind or not evidence.get('messageId') or not evidence.get('evidenceRef'):raise CycleError('confirmation_invalid')
   self.s.db.execute("UPDATE cycle_delivery_part SET state='confirmed',confirmation=? WHERE delivery_id=? AND kind=?",(encoded(evidence),id,kind))
   remaining=self.s.db.execute("SELECT count(*) FROM cycle_delivery_part WHERE delivery_id=? AND state<>'confirmed'",(id,)).fetchone()[0]
   self.s.db.execute('UPDATE cycle_delivery SET state=? WHERE id=?',('confirmed' if not remaining else 'running',id))

def delivery_status(store,plan):
 if not store.db.execute("SELECT 1 FROM sqlite_master WHERE name='cycle_delivery'").fetchone():return None
 bulk=None
 if store.db.execute("SELECT 1 FROM sqlite_master WHERE name='cycle_bulk'").fetchone():
  b=store.db.execute('SELECT * FROM cycle_bulk WHERE plan_id=? ORDER BY created DESC LIMIT 1',(plan,)).fetchone()
  if b:bulk={'id':b['id'],'state':b['state'],'target':b['target'],'counts':dict(store.db.execute('SELECT state,count(*) FROM cycle_bulk_item WHERE batch_id=? GROUP BY state',(b['id'],)))}
 return {'bulk':bulk,'states':dict(store.db.execute('SELECT state,count(*) FROM cycle_delivery WHERE plan_id=? GROUP BY state',(plan,))),
 'confirmedParts':store.db.execute("SELECT count(*) FROM cycle_delivery_part p JOIN cycle_delivery d ON d.id=p.delivery_id WHERE d.plan_id=? AND p.state='confirmed'",(plan,)).fetchone()[0]}
