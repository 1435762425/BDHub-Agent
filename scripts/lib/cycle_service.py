"""Content versions and durable service routing. No outbound transport or implicit reply."""
import json,re
from lib.second_cycle import CycleError,digest,encoded
SCHEMA='''CREATE TABLE IF NOT EXISTS service_cursor(plan_id TEXT NOT NULL,creator_id TEXT NOT NULL,event_rowid INTEGER NOT NULL,PRIMARY KEY(plan_id,creator_id));
CREATE TABLE IF NOT EXISTS service_resolution(case_id TEXT NOT NULL,revision INTEGER NOT NULL,note TEXT NOT NULL,created REAL NOT NULL,PRIMARY KEY(case_id,revision));
CREATE TABLE IF NOT EXISTS inbox_content_version(plan_id TEXT NOT NULL,cid TEXT NOT NULL,message_id TEXT NOT NULL,hash TEXT NOT NULL,payload TEXT NOT NULL,observed REAL NOT NULL,PRIMARY KEY(plan_id,cid,message_id,hash));
CREATE TABLE IF NOT EXISTS inbox_content_head(plan_id TEXT NOT NULL,cid TEXT NOT NULL,message_id TEXT NOT NULL,hash TEXT NOT NULL,PRIMARY KEY(plan_id,cid,message_id));
CREATE TABLE IF NOT EXISTS service_assessment(plan_id TEXT NOT NULL,creator_id TEXT NOT NULL,pending_revision INTEGER NOT NULL,context_hash TEXT NOT NULL,context_json TEXT NOT NULL,decision TEXT NOT NULL,created REAL NOT NULL,PRIMARY KEY(plan_id,creator_id,pending_revision));
CREATE TABLE IF NOT EXISTS service_case(id TEXT PRIMARY KEY,plan_id TEXT NOT NULL,creator_id TEXT NOT NULL,state TEXT NOT NULL,assessment_revision INTEGER NOT NULL,reason TEXT NOT NULL,created REAL NOT NULL,updated REAL NOT NULL,ack_state TEXT NOT NULL DEFAULT 'not_sent');
CREATE UNIQUE INDEX IF NOT EXISTS service_one_open_case ON service_case(plan_id,creator_id) WHERE state='open';'''

# Routing only. These rules do not generate or approve external replies.
RULES=[('do_not_contact',r'\b(non (?:(?:mi|ci) (?:contattare|contattate|scrivere|scrivete|disturbare|disturbate)|contattatemi|scrivetemi|disturbatemi)(?: più)?|non voglio (?:più )?(?:messaggi|essere contattat[oa])|no (?:me )?(?:contactes|escribas|molestes)(?: m[aá]s)?|stop (?:messaging|contacting) me|do not contact me)\b'),
('link_issue',r'\b(link|collegamento|enlace)\b.*\b(non funziona|scadut[oa]|errore|no funciona|caducad[oa])\b'),
('catalog_request',r'\b(catalogo|cataloghi|cat[aá]logo|catalogue|catalog|altri (?:link|prodotti)|otros (?:enlaces|productos))\b'),
('refund_sample',r'\b(rimbors|rimborso|reembols|refund)\w*'),
('merchant_replacement',r'\b(rott[oa]|danneggiat[oa]|esaurito|finito|rot[oa]|dañad[oa]|agotad[oa]|broken|damaged|used up)\b'),
('sample_request',r'\b(campione|campioni|muestra|muestras|sample|samples)\b'),
('commission_question',r'\b(commission[ei]|comisi[oó]n|commission)\b'),
('boost_question',r'\bboost\b')]

def route(contents):
 if not contents or any(c['format']!='text' or not c.get('text','').strip() for c in contents):return {'category':'unsupported_input','action':'human','requiredTools':[]}
 text='\n'.join(c['text'] for c in contents).casefold();found=[name for name,pattern in RULES if re.search(pattern,text)]
 if 'do_not_contact' in found:return {'category':'do_not_contact','action':'suppress_marketing','requiredTools':[]}
 if len(found)>1:return {'category':'multiple_requests','action':'human','intents':found,'requiredTools':[]}
 if found:
  name=found[0]
  if name in ('link_issue','catalog_request','refund_sample'):action='human';tools=[]
  elif name=='commission_question':action='needs_facts';tools=['get_current_creator_commission']
  elif name=='boost_question':action='human';tools=[]
  else:action='policy_review';tools=['get_relationship_product_context']
  return {'category':name,'action':action,'requiredTools':tools}
 # Broad and unrecognised text goes to review, never to an invented commercial answer.
 if re.fullmatch(r'(?:grazie|grazie mille|ok|okay|va bene|perfetto|gracias|thanks|thank you)[! .😊👍🙏]*',text.strip()):return {'category':'acknowledgement','action':'review_no_followup','requiredTools':[]}
 return {'category':'unclassified','action':'human','requiredTools':[]}

class Service:
 def __init__(self,store):self.s=store;store.db.executescript(SCHEMA)
 def capture(self,plan,cid,oec,contents):
  changed=0
  with self.s.tx():
   for c in contents:
    if set(c)!={'messageId','format','text','nativeType','rawSha256'} or c['format'] not in ('text','attachment_or_unsupported'):raise CycleError('content_invalid')
    event=self.s.db.execute('SELECT rowid AS event_rowid,* FROM inbox_event WHERE plan_id=? AND cid=? AND message_id=?',(plan,cid,c['messageId'])).fetchone()
    if not event or event['oec']!=oec:raise CycleError('content_identity_unverified')
    # Outbound text/cards are already held by delivery state; service stores incoming only.
    if event['kind']!='creatorReplies':continue
    key=digest(c);prior=self.s.db.execute('SELECT hash FROM inbox_content_head WHERE plan_id=? AND cid=? AND message_id=?',(plan,cid,c['messageId'])).fetchone()
    if prior and prior[0]==key:continue
    self.s.db.execute('INSERT OR IGNORE INTO inbox_content_version VALUES(?,?,?,?,?,?)',(plan,cid,c['messageId'],key,encoded(c),self.s.clock()))
    self.s.db.execute('INSERT INTO inbox_content_head VALUES(?,?,?,?) ON CONFLICT(plan_id,cid,message_id) DO UPDATE SET hash=excluded.hash',(plan,cid,c['messageId'],key));changed+=1
    # An edit to a live message invalidates the current service version; history never activates service.
    if prior and not event['historical']:
     self.s.db.execute('UPDATE service_cursor SET event_rowid=min(event_rowid,?) WHERE plan_id=? AND creator_id=(SELECT creator_id FROM relationship WHERE plan_id=? AND oec=?)',(event['event_rowid']-1,plan,plan,oec))
     rel=self.s.db.execute('SELECT * FROM relationship WHERE plan_id=? AND oec=?',(plan,oec)).fetchone()
     self.s.db.execute("UPDATE inbox_pending SET revision=revision+1,due_at=?,state='awaiting_content' WHERE plan_id=? AND creator_id=?",(self.s.clock()+60,plan,rel['creator_id']))
     self.s.db.execute('UPDATE relationship SET inbox_until=?,revision=revision+1 WHERE plan_id=? AND creator_id=?',(self.s.clock()+60,plan,rel['creator_id']))
  return changed
 def context(self,plan,creator):
  rel=self.s.db.execute('SELECT * FROM relationship WHERE plan_id=? AND creator_id=?',(plan,creator)).fetchone()
  rows=self.s.db.execute('''SELECT e.rowid event_rowid,e.cid,e.message_id,e.occurred_ms,e.historical,v.payload,h.hash FROM inbox_event e
 LEFT JOIN inbox_content_head h ON h.plan_id=e.plan_id AND h.cid=e.cid AND h.message_id=e.message_id
 LEFT JOIN inbox_content_version v ON v.plan_id=h.plan_id AND v.cid=h.cid AND v.message_id=h.message_id AND v.hash=h.hash
 WHERE e.plan_id=? AND e.oec=? AND e.kind='creatorReplies' ORDER BY e.occurred_ms,e.message_id''',(plan,rel['oec'])).fetchall()
  return [{'eventRowid':r['event_rowid'],'cid':r['cid'],'messageId':r['message_id'],'occurredMs':r['occurred_ms'],'historical':bool(r['historical']),'content':json.loads(r['payload']) if r['payload'] else None,'contentHash':r['hash']} for r in rows]
 def process(self,plan,creator,expected_revision):
  with self.s.tx():
   if self.s._plan(plan)['state']!='active':return {'state':'plan_paused'}
   pending=self.s.db.execute('SELECT * FROM inbox_pending WHERE plan_id=? AND creator_id=?',(plan,creator)).fetchone()
   if not pending or pending['revision']!=expected_revision:raise CycleError('inbox_version_changed')
   if pending['due_at']>self.s.clock():return {'state':'debouncing'}
   existing=self.s.db.execute('SELECT decision FROM service_assessment WHERE plan_id=? AND creator_id=? AND pending_revision=?',(plan,creator,expected_revision)).fetchone()
   if existing:return json.loads(existing[0])
   context=self.context(plan,creator);cursor=self.s.db.execute('SELECT event_rowid FROM service_cursor WHERE plan_id=? AND creator_id=?',(plan,creator)).fetchone();watermark=cursor[0] if cursor else 0;live=[r for r in context if not r['historical'] and r['eventRowid']>watermark]
   if not live or any(r['content'] is None for r in live):return {'state':'awaiting_content'}
   decision=route([r['content'] for r in live]);decision.update(engine='deterministic_policy_router_v1',automaticReply=False)
   self.s.db.execute('INSERT INTO service_assessment VALUES(?,?,?,?,?,?,?)',(plan,creator,expected_revision,digest(context),encoded(context),encoded(decision),self.s.clock()))
   action=decision['action']
   if action=='suppress_marketing':self.s.db.execute("UPDATE relationship SET rejected=1,revision=revision+1 WHERE plan_id=? AND creator_id=?",(plan,creator))
   if action in ('human','suppress_marketing'):
    now=self.s.clock();old=self.s.db.execute("SELECT id FROM service_case WHERE plan_id=? AND creator_id=? AND state='open'",(plan,creator)).fetchone()
    if old:self.s.db.execute('UPDATE service_case SET assessment_revision=?,reason=?,updated=? WHERE id=?',(expected_revision,decision['category'],now,old[0]))
    else:self.s.db.execute("INSERT INTO service_case VALUES(?,?,?,'open',?,?,?,?, 'not_sent')",('case-'+digest([plan,creator,now])[:24],plan,creator,expected_revision,decision['category'],now,now))
    self.s.db.execute("UPDATE relationship SET mode='human',revision=revision+1 WHERE plan_id=? AND creator_id=? AND mode='auto'",(plan,creator))
   if action=='review_no_followup' and not self.s.db.execute("SELECT 1 FROM service_case WHERE plan_id=? AND creator_id=? AND state='open'",(plan,creator)).fetchone():
    self.s.db.execute('INSERT INTO service_cursor VALUES(?,?,?) ON CONFLICT(plan_id,creator_id) DO UPDATE SET event_rowid=excluded.event_rowid',(plan,creator,max(r['eventRowid'] for r in live)))
    self.s.db.execute("UPDATE relationship SET inbox_until=0,revision=revision+1 WHERE plan_id=? AND creator_id=? AND mode='auto'",(plan,creator));action='resolved_no_reply'
   self.s.db.execute('UPDATE inbox_pending SET state=? WHERE plan_id=? AND creator_id=?',(action,plan,creator))
   return decision
 def process_due(self,plan):
  rows=self.s.db.execute("SELECT creator_id,revision FROM inbox_pending WHERE plan_id=? AND state='awaiting_content' AND due_at<=?",(plan,self.s.clock())).fetchall()
  return [self.process(plan,r['creator_id'],r['revision']) for r in rows]

 def resolve_case(self,plan,case_id,expected_revision,expected_control_revision,note):
  if not isinstance(note,str) or not note.strip() or len(note)>4000:raise CycleError('resolution_note_required')
  with self.s.tx():
   case=self.s.db.execute("SELECT * FROM service_case WHERE id=? AND plan_id=?",(case_id,plan)).fetchone()
   if not case:raise CycleError('case_missing')
   if case['state']=='resolved':
    prior=self.s.db.execute('SELECT note FROM service_resolution WHERE case_id=? AND revision=?',(case_id,expected_revision)).fetchone()
    if not prior or prior[0]!=note:raise CycleError('case_resolution_conflict')
    return {'state':'resolved','duplicate':True}
   pending=self.s.db.execute('SELECT * FROM inbox_pending WHERE plan_id=? AND creator_id=?',(plan,case['creator_id'])).fetchone()
   rel=self.s.db.execute('SELECT * FROM relationship WHERE plan_id=? AND creator_id=?',(plan,case['creator_id'])).fetchone()
   if case['assessment_revision']!=expected_revision or not pending or pending['revision']!=expected_revision or rel['revision']!=expected_control_revision:raise CycleError('case_changed_review_latest')
   if rel['mode']!='human':raise CycleError('relationship_control_changed')
   context=self.context(plan,case['creator_id']);watermark=max((r['eventRowid'] for r in context),default=0)
   self.s.db.execute('INSERT INTO service_resolution VALUES(?,?,?,?)',(case_id,expected_revision,note,self.s.clock()))
   self.s.db.execute("UPDATE service_case SET state='resolved',updated=? WHERE id=?",(self.s.clock(),case_id))
   self.s.db.execute("UPDATE inbox_pending SET state='resolved_by_human' WHERE plan_id=? AND creator_id=?",(plan,case['creator_id']))
   self.s.db.execute('INSERT INTO service_cursor VALUES(?,?,?) ON CONFLICT(plan_id,creator_id) DO UPDATE SET event_rowid=excluded.event_rowid',(plan,case['creator_id'],watermark))
   self.s.db.execute("UPDATE relationship SET mode='auto',inbox_until=0,revision=revision+1 WHERE plan_id=? AND creator_id=?",(plan,case['creator_id']))
   return {'state':'resolved','duplicate':False,'automaticReply':False}

def service_status(store,plan):
 db=store.db
 if not db.execute("SELECT 1 FROM sqlite_master WHERE name='service_case'").fetchone():return {'available':False,'cases':[],'automaticReplies':False}
 cases=[]
 for row in db.execute("SELECT c.*,r.oec,r.revision control_revision FROM service_case c JOIN relationship r ON r.plan_id=c.plan_id AND r.creator_id=c.creator_id WHERE c.plan_id=? AND c.state='open' ORDER BY c.created LIMIT 50",(plan,)):
  case=dict(row);a=db.execute('SELECT context_json,decision FROM service_assessment WHERE plan_id=? AND creator_id=? AND pending_revision=?',(plan,row['creator_id'],row['assessment_revision'])).fetchone()
  context=json.loads(a[0]) if a else []
  case['messages']=[{'messageId':m['messageId'],'text':(m.get('content') or {}).get('text'),'format':(m.get('content') or {}).get('format','not_fetched')} for m in context if not m['historical']][-20:]
  case['overdue']=store.clock()-row['created']>86400;case['decision']=json.loads(a[1]) if a else {};cases.append(case)
 evaluations=db.execute("SELECT count(*) FROM service_agent_evaluation WHERE state='ready' AND mode='historical_shadow'").fetchone()[0] if db.execute("SELECT 1 FROM sqlite_master WHERE name='service_agent_evaluation'").fetchone() else 0
 return {'historicalAiEvaluations':evaluations,'available':True,'cases':cases,'automaticReplies':False,'incomingContents':db.execute('SELECT count(*) FROM inbox_content_head WHERE plan_id=?',(plan,)).fetchone()[0],
 'assessments':db.execute('SELECT count(*) FROM service_assessment WHERE plan_id=?',(plan,)).fetchone()[0],
 'pending':dict(db.execute('SELECT state,count(*) FROM inbox_pending WHERE plan_id=? GROUP BY state',(plan,))),
 'caseCount':db.execute("SELECT count(*) FROM service_case WHERE plan_id=? AND state='open'",(plan,)).fetchone()[0]}
