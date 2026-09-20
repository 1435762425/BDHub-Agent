"""Automatic policy replies with durable single attempts. No public draft workflow."""
import json,re,uuid
from lib.second_cycle import CycleError,digest,encoded
from lib.cycle_service import Service
SCHEMA='''CREATE TABLE IF NOT EXISTS service_reply_fact_failure(plan_id TEXT NOT NULL,creator_id TEXT NOT NULL,revision INTEGER NOT NULL,attempts INTEGER NOT NULL,PRIMARY KEY(plan_id,creator_id,revision));
CREATE TABLE IF NOT EXISTS service_reply_fact(reply_id TEXT PRIMARY KEY,payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS service_reply_config(plan_id TEXT PRIMARY KEY,enabled INTEGER NOT NULL,authorization TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS service_reply(id TEXT PRIMARY KEY,plan_id TEXT NOT NULL,creator_id TEXT NOT NULL,pending_revision INTEGER NOT NULL,oec TEXT NOT NULL,cid TEXT NOT NULL,kind TEXT NOT NULL,case_id TEXT,text TEXT NOT NULL,context_hash TEXT NOT NULL,state TEXT NOT NULL,request_ref TEXT NOT NULL UNIQUE,receipt TEXT,proof TEXT,created REAL NOT NULL,started REAL);
CREATE TABLE IF NOT EXISTS service_reply_runtime(plan_id TEXT PRIMARY KEY,seen REAL NOT NULL,state TEXT NOT NULL);'''
ACK='Ricevuto, verifichiamo e ti aggiorniamo appena possibile.'
SAMPLE='Per questa collaborazione ti proponiamo di promuovere di nuovo il prodotto: non inviamo un nuovo campione. Se lo hai ancora, puoi usarlo per un nuovo video o LIVE.'
REPLACEMENT='Capito. Puoi contattare direttamente il negozio e chiedere se è possibile ricevere un altro campione o una sostituzione.'
class AutoReplies:
 def __init__(self,store):
  self.s=store;self.service=Service(store);store.db.executescript(SCHEMA)
  if 'control_revision' not in {r[1] for r in store.db.execute('PRAGMA table_info(service_reply)')}:store.db.execute('ALTER TABLE service_reply ADD COLUMN control_revision INTEGER NOT NULL DEFAULT 0')
 def enabled(self,plan):
  r=self.s.db.execute('SELECT enabled FROM service_reply_config WHERE plan_id=?',(plan,)).fetchone();return bool(r and r[0])
 def enable(self,plan,authorization):
  if not authorization.strip():raise CycleError('reply_authorization_required')
  self.s.db.execute('INSERT INTO service_reply_config VALUES(?,1,?) ON CONFLICT(plan_id) DO UPDATE SET enabled=1,authorization=excluded.authorization',(plan,authorization))
 def get(self,id):
  r=self.s.db.execute('SELECT * FROM service_reply WHERE id=?',(id,)).fetchone();return dict(r) if r else None
 def prepare(self,plan,creator,facts=None):
  if not self.enabled(plan) or self.s._plan(plan)['state']!='active':return None
  with self.s.tx():
   p=self.s.db.execute('SELECT * FROM inbox_pending WHERE plan_id=? AND creator_id=?',(plan,creator)).fetchone();r=self.s.db.execute('SELECT * FROM relationship WHERE plan_id=? AND creator_id=?',(plan,creator)).fetchone()
   if not p or p['due_at']>self.s.clock() or r['mode']=='paused':return None
   a=self.s.db.execute('SELECT * FROM service_assessment WHERE plan_id=? AND creator_id=? AND pending_revision=?',(plan,creator,p['revision'])).fetchone()
   if not a:return None
   context=self.service.context(plan,creator);current=[x for x in context if not x['historical']]
   if not current or any(x['content'] is None for x in current):return None
   decision=json.loads(a['decision']);category=decision['category'];case=None;kind='answer'
   if self.s.db.execute("SELECT 1 FROM inbox_checkpoint WHERE plan_id=? AND oec=? AND state='gap'",(plan,r['oec'])).fetchone():return None
   if p['state']=='human':
    case=self.s.db.execute("SELECT * FROM service_case WHERE plan_id=? AND creator_id=? AND state='open'",(plan,creator)).fetchone()
    if not case or case['ack_state']=='confirmed':return None
    text=ACK;kind='handoff_ack'
   elif r['mode']!='auto':return None
   elif category=='sample_request':text=SAMPLE
   elif category=='merchant_replacement':text=REPLACEMENT
   elif category=='commission_question':
    if not facts or 'creatorPercent' not in facts or self.s.clock()-facts['observedAt']>300:return None
    text=f"Con il link che ti abbiamo inviato, la commissione per te è del {facts['creatorPercent']}%."
    combined=' '.join(x['content'].get('text') or '' for x in current).casefold()
    if any(w in combined for w in ('venditore','negozio')):text='Potresti avere una commissione personale concordata con il venditore. '+text
   else:return None
   if category in ('sample_request','merchant_replacement') and not facts:return None
   cids={x['cid'] for x in current}
   if len(cids)!=1:return None
   key=case['id'] if case else p['revision'];id='auto-reply-'+digest([plan,creator,kind,key])[:24];old=self.get(id)
   if old:
    # Only an unsubmitted acknowledgement may follow a newer version of the same open case.
    if old['kind']=='handoff_ack' and old['state'] in ('ready','cancelled') and old['started'] is None:
     self.s.db.execute("UPDATE service_reply SET pending_revision=?,context_hash=?,control_revision=?,state='ready' WHERE id=?",(p['revision'],digest(context),r['revision'],id))
    return self.get(id)
   self.s.db.execute("INSERT INTO service_reply(id,plan_id,creator_id,pending_revision,oec,cid,kind,case_id,text,context_hash,state,request_ref,receipt,proof,created,started) VALUES(?,?,?,?,?,?,?,?,?,?,'ready',?,NULL,NULL,?,NULL)",(id,plan,creator,p['revision'],r['oec'],next(iter(cids)),kind,case['id'] if case else None,text,digest(context),str(uuid.uuid4()),self.s.clock()))
   self.s.db.execute('UPDATE service_reply SET control_revision=? WHERE id=?',(r['revision'],id))
   if facts and category=='commission_question':self.s.db.execute('INSERT OR REPLACE INTO service_reply_fact VALUES(?,?)',(id,encoded(facts)))
   return self.get(id)
 def prepare_manual(self,plan,creator,cid,text,expected_control_revision,request_id):
  if not isinstance(text,str) or not text.strip() or len(text)>4000 or not isinstance(cid,str) or not cid.isdigit() or \
     not isinstance(request_id,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]{7,119}',request_id) or \
     type(expected_control_revision) is not int or expected_control_revision<1:raise CycleError('manual_reply_invalid')
  reply_id='manual-reply-'+digest([plan,creator,request_id])[:24]
  with self.s.tx():
   old=self.get(reply_id)
   if old:
    if old['text']!=text.strip() or old['cid']!=cid or old['kind']!='manual':raise CycleError('manual_reply_request_conflict')
    return old
   rel=self.s.db.execute('SELECT * FROM relationship WHERE plan_id=? AND creator_id=?',(plan,creator)).fetchone()
   cp=self.s.db.execute('SELECT oec FROM inbox_checkpoint WHERE plan_id=? AND cid=?',(plan,cid)).fetchone()
   if not rel or rel['revision']!=expected_control_revision or rel['mode']=='paused' or not cp or cp['oec']!=rel['oec']:raise CycleError('manual_reply_context_changed')
   pending=self.s.db.execute('SELECT revision FROM inbox_pending WHERE plan_id=? AND creator_id=?',(plan,creator)).fetchone()
   context=self.service.context(plan,creator)
   self.s.db.execute("INSERT INTO service_reply(id,plan_id,creator_id,pending_revision,oec,cid,kind,case_id,text,context_hash,state,request_ref,receipt,proof,created,started,control_revision) VALUES(?,?,?,?,?,?,\'manual\',NULL,?,?,\'ready\',?,NULL,NULL,?,NULL,?)",(reply_id,plan,creator,pending[0] if pending else 0,rel['oec'],cid,text.strip(),digest(context),request_id,self.s.clock(),rel['revision']))
   return self.get(reply_id)
 def prepare_manual_card(self,plan,creator,cid,card,expected_control_revision,request_id):
  if not isinstance(card,dict) or not card.get('pid') or not card.get('listId') or not isinstance(cid,str) or not cid.isdigit() or \
     type(expected_control_revision) is not int or expected_control_revision<1 or not isinstance(request_id,str) or \
     not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]{7,119}',request_id):raise CycleError('manual_reply_invalid')
  reply_id='manual-card-'+digest([plan,creator,request_id])[:24];payload=encoded(card)
  with self.s.tx():
   old=self.get(reply_id)
   if old:
    if old['text']!=payload or old['cid']!=cid or old['kind']!='manual_card':raise CycleError('manual_reply_request_conflict')
    return old
   rel=self.s.db.execute('SELECT * FROM relationship WHERE plan_id=? AND creator_id=?',(plan,creator)).fetchone();cp=self.s.db.execute('SELECT oec FROM inbox_checkpoint WHERE plan_id=? AND cid=?',(plan,cid)).fetchone()
   if not rel or rel['revision']!=expected_control_revision or rel['mode']=='paused' or not cp or cp['oec']!=rel['oec']:raise CycleError('manual_reply_context_changed')
   context=self.service.context(plan,creator)
   self.s.db.execute("INSERT INTO service_reply(id,plan_id,creator_id,pending_revision,oec,cid,kind,case_id,text,context_hash,state,request_ref,receipt,proof,created,started,control_revision) VALUES(?,?,?,?,?,?,\'manual_card\',NULL,?,?,\'ready\',?,NULL,NULL,?,NULL,?)",(reply_id,plan,creator,0,rel['oec'],cid,payload,digest(context),request_id,self.s.clock(),rel['revision']))
   return self.get(reply_id)
 def prepare_policy(self,plan,creator,cid,pending_revision,action,template_key,text,turn_id,policy_version):
  if action not in ('sample_self_service','collaboration_ack','link_usage') or not isinstance(text,str) or not text.strip():raise CycleError('reply_policy_invalid')
  with self.s.tx():
   rel=self.s.db.execute('SELECT * FROM relationship WHERE plan_id=? AND creator_id=?',(plan,creator)).fetchone();pending=self.s.db.execute('SELECT * FROM inbox_pending WHERE plan_id=? AND creator_id=?',(plan,creator)).fetchone()
   if not rel or rel['mode']!='auto' or rel['rejected'] or not pending or pending['revision']!=pending_revision:raise CycleError('reply_context_changed')
   context=self.service.context(plan,creator);cids={row['cid'] for row in context if not row['historical']}
   if cids!={cid}:raise CycleError('reply_context_changed')
   reply_id='agent-reply-'+digest([plan,creator,action,turn_id,policy_version])[:24];old=self.get(reply_id)
   if old:return old
   self.s.db.execute("INSERT INTO service_reply(id,plan_id,creator_id,pending_revision,oec,cid,kind,case_id,text,context_hash,state,request_ref,receipt,proof,created,started,control_revision) VALUES(?,?,?,?,?,?,?,NULL,?,?,\'ready\',?,NULL,NULL,?,NULL,?)",(reply_id,plan,creator,pending_revision,rel['oec'],cid,action,text.strip(),digest(context),str(uuid.uuid4()),self.s.clock(),rel['revision']))
   return self.get(reply_id)
 def begin(self,id):
  with self.s.tx():
   q=self.get(id);p=self.s.db.execute('SELECT * FROM inbox_pending WHERE plan_id=? AND creator_id=?',(q['plan_id'],q['creator_id'])).fetchone();r=self.s.db.execute('SELECT * FROM relationship WHERE plan_id=? AND creator_id=?',(q['plan_id'],q['creator_id'])).fetchone()
   if q['state']!='ready':raise CycleError('reply_not_ready')
   manual=q['kind'] in ('manual','manual_card')
   if (not manual and not self.enabled(q['plan_id'])) or self.s._plan(q['plan_id'])['state']!='active' or \
      (not manual and (not p or p['revision']!=q['pending_revision'] or p['due_at']>self.s.clock())) or \
      not r or r['revision']!=q['control_revision'] or r['mode']=='paused' or digest(self.service.context(q['plan_id'],q['creator_id']))!=q['context_hash']:raise CycleError('reply_context_changed')
   if q['kind']=='answer' and r['mode']!='auto':raise CycleError('reply_human_control')
   if q['case_id']:
    case=self.s.db.execute('SELECT * FROM service_case WHERE id=?',(q['case_id'],)).fetchone()
    if not case or case['state']!='open' or case['ack_state']=='confirmed':raise CycleError('handoff_changed')
   if self.s.db.execute("SELECT 1 FROM cycle_delivery WHERE state='unknown' AND plan_id=?",(q['plan_id'],)).fetchone():raise CycleError('delivery_unknown')
   if self.s.db.execute("SELECT 1 FROM service_reply WHERE plan_id=? AND state IN ('inflight','unknown','accepted')",(q['plan_id'],)).fetchone():raise CycleError('reply_unknown')
   self.s.db.execute("UPDATE service_reply SET state='inflight',started=? WHERE id=?",(self.s.clock(),id))
   return {'dispatchAllowed':True,'requestRef':q['request_ref'],'stage':'send_message',
           'componentKind':'card' if q['kind']=='manual_card' else 'text'}
 def accepted(self,id,receipt):
  with self.s.tx():
   q=self.get(id)
   if q['state']!='inflight' or receipt['requestRef']!=q['request_ref']:raise CycleError('reply_receipt_mismatch')
   self.s.db.execute("UPDATE service_reply SET state='accepted',receipt=? WHERE id=?",(encoded(receipt),id))
 def unknown(self,id):self.s.db.execute("UPDATE service_reply SET state='unknown' WHERE id=? AND state IN ('inflight','accepted')",(id,))
 def confirm(self,id,proof):
  with self.s.tx():
   q=self.get(id)
   if q['state']=='confirmed':return
   if q['state'] not in ('inflight','accepted','unknown') or proof.get('status')!='confirmed' or proof.get('requestRef')!=q['request_ref'] or proof.get('conversationId')!=q['cid'] or not proof.get('messageId'):raise CycleError('reply_proof_invalid')
   self.s.db.execute("UPDATE service_reply SET state='confirmed',proof=? WHERE id=?",(encoded(proof),id))
   if q['case_id']:self.s.db.execute("UPDATE service_case SET ack_state='confirmed' WHERE id=?",(q['case_id'],))
   elif q['kind'] in ('manual','manual_card'):pass
   else:
    p=self.s.db.execute('SELECT revision FROM inbox_pending WHERE plan_id=? AND creator_id=?',(q['plan_id'],q['creator_id'])).fetchone()
    if p and p[0]==q['pending_revision']:
     self.s.db.execute("UPDATE inbox_pending SET state='answered' WHERE plan_id=? AND creator_id=?",(q['plan_id'],q['creator_id']))
     self.s.db.execute("UPDATE relationship SET inbox_until=0,revision=revision+1 WHERE plan_id=? AND creator_id=? AND mode='auto'",(q['plan_id'],q['creator_id']))
     rows=self.service.context(q['plan_id'],q['creator_id']);watermark=max((r['eventRowid'] for r in rows),default=0)
     self.s.db.execute('INSERT INTO service_cursor VALUES(?,?,?) ON CONFLICT(plan_id,creator_id) DO UPDATE SET event_rowid=excluded.event_rowid',(q['plan_id'],q['creator_id'],watermark))
