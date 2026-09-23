"""Durable metadata ingestion. History never creates a reply task or send intent."""
from lib.second_cycle import CycleError,encoded
SCHEMA='''CREATE TABLE IF NOT EXISTS inbox_checkpoint(plan_id TEXT NOT NULL,cid TEXT NOT NULL,oec TEXT NOT NULL,baseline_at REAL NOT NULL,checked_at REAL NOT NULL,state TEXT NOT NULL,PRIMARY KEY(plan_id,cid));
CREATE TABLE IF NOT EXISTS inbox_event(plan_id TEXT NOT NULL,cid TEXT NOT NULL,message_id TEXT NOT NULL,oec TEXT NOT NULL,kind TEXT NOT NULL,occurred_ms INTEGER,payload TEXT NOT NULL,historical INTEGER NOT NULL,observed_at REAL NOT NULL,PRIMARY KEY(plan_id,cid,message_id));
CREATE TABLE IF NOT EXISTS inbox_pending(plan_id TEXT NOT NULL,creator_id TEXT NOT NULL,revision INTEGER NOT NULL,due_at REAL NOT NULL,state TEXT NOT NULL,PRIMARY KEY(plan_id,creator_id));'''
KINDS={'ourMessages','creatorReplies','showcaseNotifications','otherOrUnknown'}
REPLY_BATCH_SECONDS=0
REPLY_FREEZE_SECONDS=172800
class Inbox:
 def __init__(self,store):self.s=store;store.db.executescript(SCHEMA)
 def ingest(self,plan,cid,oec,history):
  if history.get('identityVerified') is not True:raise CycleError('unverified_history')
  events=history.get('events');now=self.s.clock()
  if not isinstance(events,list) or type(history.get('hasMore')) is not bool:raise CycleError('invalid_history')
  unique={}
  for event in events:
   if event.get('conversationId')!=cid or event.get('oecId')!=oec or event.get('kind') not in KINDS:raise CycleError('event_identity_mismatch')
   mid=event.get('messageId')
   if not isinstance(mid,str) or not mid.isascii() or not mid.isdigit() or int(mid)<=0:raise CycleError('event_id_missing')
   if mid in unique and unique[mid]!=event:raise CycleError('event_conflict')
   unique[mid]=event
  with self.s.tx():
   db=self.s.db;rel=db.execute('SELECT * FROM relationship WHERE plan_id=? AND oec=?',(plan,oec)).fetchone()
   if not rel:raise CycleError('relationship_missing')
   cp=db.execute('SELECT * FROM inbox_checkpoint WHERE plan_id=? AND cid=?',(plan,cid)).fetchone()
   if cp and cp['oec']!=oec:raise CycleError('checkpoint_identity_conflict')
   baseline=cp['baseline_at'] if cp else now;known_contact=False
   if not cp and db.execute("SELECT 1 FROM sqlite_master WHERE name='cycle_delivery_part'").fetchone():
    sent=db.execute("""SELECT max(p.started) FROM cycle_delivery d JOIN cycle_delivery_part p ON p.delivery_id=d.id
      WHERE d.plan_id=? AND d.oec=? AND p.kind='text' AND p.state='confirmed'""",(plan,oec)).fetchone()[0]
    if sent is not None:baseline=min(baseline,float(sent));known_contact=True
   overlap=False
   for mid,e in unique.items():
    prev=db.execute('SELECT payload FROM inbox_event WHERE plan_id=? AND cid=? AND message_id=?',(plan,cid,mid)).fetchone()
    if prev:
     overlap=True
     if prev[0]!=encoded(e):raise CycleError('event_conflict')
   gap=bool(cp and (cp['state']=='gap' or (history['hasMore'] and not overlap)))
   added=historical=live=showcase_live=0;unlock=False
   for mid,e in unique.items():
    if db.execute('SELECT 1 FROM inbox_event WHERE plan_id=? AND cid=? AND message_id=?',(plan,cid,mid)).fetchone():continue
    stamp=e.get('createTimeRaw');valid=type(stamp) is int and 946684800000<=stamp<=int(now*1000)+300000
    old=(not cp and not known_contact) or not valid or stamp<baseline*1000 or gap
    db.execute('INSERT INTO inbox_event VALUES(?,?,?,?,?,?,?,?,?)',(plan,cid,mid,oec,e['kind'],stamp if valid else None,encoded(e),int(old),now));added+=1;historical+=int(old)
    if e['kind'] in ('creatorReplies','showcaseNotifications'):unlock=True
    if e['kind']=='creatorReplies' and not old:live+=1
    if e['kind']=='showcaseNotifications' and not old:showcase_live+=1
   # Interaction evidence is separate from manual control/rejection. No marketing is dispatched here.
   if unlock and not rel['unlocked']:db.execute('UPDATE relationship SET unlocked=1,revision=revision+1 WHERE plan_id=? AND creator_id=?',(plan,rel['creator_id']))
   if gap:
    db.execute('UPDATE relationship SET inbox_until=?,revision=revision+1 WHERE plan_id=? AND creator_id=? AND inbox_until=0',(now+REPLY_FREEZE_SECONDS,plan,rel['creator_id']))
   if live:
    db.execute('UPDATE relationship SET inbox_until=?,revision=revision+1 WHERE plan_id=? AND creator_id=?',(max(rel['inbox_until'],now+REPLY_FREEZE_SECONDS),plan,rel['creator_id']))
    db.execute("INSERT INTO inbox_pending VALUES(?,?,1,?,'awaiting_classification') ON CONFLICT(plan_id,creator_id) DO UPDATE SET revision=revision+1,due_at=excluded.due_at,state='awaiting_classification'",(plan,rel['creator_id'],now+REPLY_BATCH_SECONDS))
   state='gap' if gap else 'tracking'
   db.execute('INSERT INTO inbox_checkpoint VALUES(?,?,?,?,?,?) ON CONFLICT(plan_id,cid) DO UPDATE SET checked_at=excluded.checked_at,state=excluded.state',(plan,cid,oec,baseline,now,state))
  if showcase_live and self.s.db.execute("SELECT 1 FROM sqlite_master WHERE name='creator_collaboration_current'").fetchone():
   from lib.collaboration_status import observe_showcase
   observe_showcase(self.s,rel['creator_id'],{'conversationId':cid,'count':showcase_live,'observedAt':now},plan_id=plan)
  return dict(added=added,historical=historical,liveReplies=live,state=state,realSends=0)

def inbox_status(store,plan):
 db=store.db
 if not db.execute("SELECT 1 FROM sqlite_master WHERE name='inbox_event'").fetchone():return None
 enabled=False
 if db.execute("SELECT 1 FROM sqlite_master WHERE name='service_reply_config'").fetchone():
  config=db.execute('SELECT enabled FROM service_reply_config WHERE plan_id=?',(plan,)).fetchone();enabled=bool(config and config[0])
 oldest=db.execute('SELECT min(checked_at) FROM inbox_checkpoint WHERE plan_id=?',(plan,)).fetchone()[0]
 return {'events':db.execute('SELECT count(*) FROM inbox_event WHERE plan_id=?',(plan,)).fetchone()[0],
 'historicalEvents':db.execute('SELECT count(*) FROM inbox_event WHERE plan_id=? AND historical=1',(plan,)).fetchone()[0],
 'conversations':db.execute('SELECT count(*) FROM inbox_checkpoint WHERE plan_id=?',(plan,)).fetchone()[0],
 'gaps':db.execute("SELECT count(*) FROM inbox_checkpoint WHERE plan_id=? AND state='gap'",(plan,)).fetchone()[0],
 'pendingContent':db.execute('SELECT count(*) FROM inbox_pending WHERE plan_id=?',(plan,)).fetchone()[0],
 'lastCheckedAt':db.execute('SELECT max(checked_at) FROM inbox_checkpoint WHERE plan_id=?',(plan,)).fetchone()[0],
 'oldestCheckedAt':oldest,'oldestAgeSeconds':max(0,int(store.clock()-oldest)) if oldest else None,
 'automaticRepliesEnabled':enabled}
