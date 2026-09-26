"""Durable metadata ingestion. History never creates a reply task or send intent."""
from lib.second_cycle import CycleError,encoded
SCHEMA='''CREATE TABLE IF NOT EXISTS inbox_checkpoint(plan_id TEXT NOT NULL,cid TEXT NOT NULL,oec TEXT NOT NULL,baseline_at REAL NOT NULL,checked_at REAL NOT NULL,state TEXT NOT NULL,PRIMARY KEY(plan_id,cid));
CREATE TABLE IF NOT EXISTS inbox_event(plan_id TEXT NOT NULL,cid TEXT NOT NULL,message_id TEXT NOT NULL,oec TEXT NOT NULL,kind TEXT NOT NULL,occurred_ms INTEGER,payload TEXT NOT NULL,historical INTEGER NOT NULL,observed_at REAL NOT NULL,PRIMARY KEY(plan_id,cid,message_id));
CREATE TABLE IF NOT EXISTS inbox_pending(plan_id TEXT NOT NULL,creator_id TEXT NOT NULL,revision INTEGER NOT NULL,due_at REAL NOT NULL,state TEXT NOT NULL,PRIMARY KEY(plan_id,creator_id));
CREATE TABLE IF NOT EXISTS inbox_backfill(plan_id TEXT NOT NULL,cid TEXT NOT NULL,oec TEXT NOT NULL,account TEXT NOT NULL,im_id TEXT NOT NULL,cursor TEXT NOT NULL,event_rowid INTEGER NOT NULL,started_at REAL NOT NULL,rounds INTEGER NOT NULL,updated_at REAL NOT NULL,PRIMARY KEY(plan_id,cid));
CREATE INDEX IF NOT EXISTS inbox_checkpoint_oec ON inbox_checkpoint(plan_id,oec,state);'''
# Readers ask "does this creator's conversation have a gap?" by oec; without this index every
# question scans the plan's checkpoints (G18).
# A checkpoint whose newest messages were read but not yet joined to what was known before the read
# began. Its events are stored but not answered, and later rounds resume from the saved native cursor.
HOLD_STATES=('gap','backfilling')
KINDS={'ourMessages','creatorReplies','showcaseNotifications','otherOrUnknown'}
REPLY_BATCH_SECONDS=0
REPLY_FREEZE_SECONDS=172800
class Inbox:
 def __init__(self,store):
  self.s=store;store.db.executescript(SCHEMA)
  if 'segment_top' not in {r[1] for r in store.db.execute('PRAGMA table_info(inbox_backfill)')}:
   with store.tx():  # Re-checked under the write lock: concurrent first starts add it once.
    if 'segment_top' not in {r[1] for r in store.db.execute('PRAGMA table_info(inbox_backfill)')}:
     store.db.execute('ALTER TABLE inbox_backfill ADD COLUMN segment_top TEXT')
 def ingest(self,plan,cid,oec,history):
  if history.get('identityVerified') is not True:raise CycleError('unverified_history')
  events=history.get('events');now=self.s.clock();backfill=history.get('backfill')
  if backfill is not None and (not isinstance(backfill,dict) or backfill.get('coverage') not in ('partial','complete') or
     type(backfill.get('watermark')) is not int or backfill['watermark']<0):raise CycleError('invalid_history')
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
   if not cp:
    from lib.invitation_continuation import confirmed_contact_start
    sent=confirmed_contact_start(db,plan,cid,oec)
    if sent is not None:baseline=min(baseline,float(sent));known_contact=True
   overlap=False
   for mid,e in unique.items():
    prev=db.execute('SELECT payload FROM inbox_event WHERE plan_id=? AND cid=? AND message_id=?',(plan,cid,mid)).fetchone()
    if prev:
     overlap=True
     if prev[0]!=encoded(e):raise CycleError('event_conflict')
   watermark=db.execute('SELECT coalesce(max(rowid),0) FROM inbox_event').fetchone()[0]
   auto_hold=False
   if cp and cp['state']!='gap' and (backfill is not None or cp['state']=='backfilling'):
    # Coverage is proven by the resumable reader, not by one page; other readers only add facts.
    gap=False;holding=not (backfill is not None and backfill['coverage']=='complete')
   elif cp and cp['state']=='tracking' and history['hasMore'] and not overlap:
    # A single-page reader cannot join the stored range: hold with the pre-read watermark so the
    # resumable reader can prove coverage later, instead of an unrecoverable gap.
    gap=False;holding=True;auto_hold=True
   else:
    gap=bool(cp and cp['state']=='gap');holding=False
   completing=bool(cp and cp['state']=='backfilling' and not holding)
   added=historical=live=showcase_live=0;unlock=False;interaction_events=[]
   for mid,e in unique.items():
    if db.execute('SELECT 1 FROM inbox_event WHERE plan_id=? AND cid=? AND message_id=?',(plan,cid,mid)).fetchone():continue
    stamp=e.get('createTimeRaw');valid=type(stamp) is int and 946684800000<=stamp<=int(now*1000)+300000
    old=(not cp and not known_contact) or not valid or stamp<baseline*1000 or gap
    db.execute('INSERT INTO inbox_event VALUES(?,?,?,?,?,?,?,?,?)',(plan,cid,mid,oec,e['kind'],stamp if valid else None,encoded(e),int(old),now));added+=1;historical+=int(old)
    if e['kind'] in ('creatorReplies','showcaseNotifications'):unlock=True
    if holding:continue
    if e['kind']=='creatorReplies' and not old:live+=1
    if e['kind']=='showcaseNotifications' and not old:showcase_live+=1
    if not old and e['kind'] in ('creatorReplies','showcaseNotifications'):
     interaction_events.append({'kind':e['kind'],'message_id':mid,'occurred_ms':stamp})
   if completing:
    # The unbroken chain now reaches pre-read messages: answer what arrived while it was held, once.
    saved=db.execute('SELECT event_rowid FROM inbox_backfill WHERE plan_id=? AND cid=?',(plan,cid)).fetchone()
    since=saved[0] if saved else backfill['watermark'];live=showcase_live=0;interaction_events=[]
    for row in db.execute("""SELECT message_id,kind,occurred_ms FROM inbox_event WHERE plan_id=? AND cid=? AND historical=0
      AND rowid>? AND kind IN ('creatorReplies','showcaseNotifications') ORDER BY coalesce(occurred_ms,0),message_id""",(plan,cid,since)):
     live+=row['kind']=='creatorReplies';showcase_live+=row['kind']=='showcaseNotifications'
     interaction_events.append({'kind':row['kind'],'message_id':row['message_id'],'occurred_ms':row['occurred_ms']})
   # Interaction evidence is separate from manual control/rejection. No marketing is dispatched here.
   if unlock and not rel['unlocked']:db.execute('UPDATE relationship SET unlocked=1,revision=revision+1 WHERE plan_id=? AND creator_id=?',(plan,rel['creator_id']))
   if gap or holding:
    db.execute('UPDATE relationship SET inbox_until=?,revision=revision+1 WHERE plan_id=? AND creator_id=? AND inbox_until=0',(now+REPLY_FREEZE_SECONDS,plan,rel['creator_id']))
   if holding and backfill is not None:
    if not all(isinstance(backfill.get(k),str) and backfill[k] for k in ('account','imId','cursor')) or backfill.get('oec')!=oec:
     raise CycleError('invalid_history')
    # segment_top: newest message of the unbroken chain that ends at ``cursor``. A later round may
    # jump to the cursor only after reading down to exactly this message.
    db.execute("""INSERT INTO inbox_backfill(plan_id,cid,oec,account,im_id,cursor,event_rowid,started_at,rounds,updated_at,segment_top) VALUES(?,?,?,?,?,?,?,?,1,?,?) ON CONFLICT(plan_id,cid) DO UPDATE SET
      oec=excluded.oec,account=excluded.account,im_id=excluded.im_id,cursor=excluded.cursor,segment_top=excluded.segment_top,
      rounds=rounds+1,updated_at=excluded.updated_at""",
      (plan,cid,oec,backfill['account'],backfill['imId'],backfill['cursor'],backfill['watermark'],now,now,backfill.get('segmentTop')))
   if auto_hold:
    # No reader identity: any resumable reader restarts from the newest page, keeping this watermark.
    db.execute("INSERT OR IGNORE INTO inbox_backfill(plan_id,cid,oec,account,im_id,cursor,event_rowid,started_at,rounds,updated_at) VALUES(?,?,?,'','','0',?,?,0,?)",(plan,cid,oec,watermark,now,now))
   if completing or backfill is not None and not holding:db.execute('DELETE FROM inbox_backfill WHERE plan_id=? AND cid=?',(plan,cid))
   if live:
    db.execute('UPDATE relationship SET inbox_until=?,revision=revision+1 WHERE plan_id=? AND creator_id=?',(max(rel['inbox_until'],now+REPLY_FREEZE_SECONDS),plan,rel['creator_id']))
    db.execute("INSERT INTO inbox_pending VALUES(?,?,1,?,'awaiting_classification') ON CONFLICT(plan_id,creator_id) DO UPDATE SET revision=revision+1,due_at=excluded.due_at,state='awaiting_classification'",(plan,rel['creator_id'],now+REPLY_BATCH_SECONDS))
   state='gap' if gap else 'backfilling' if holding else 'tracking'
   db.execute('INSERT INTO inbox_checkpoint VALUES(?,?,?,?,?,?) ON CONFLICT(plan_id,cid) DO UPDATE SET checked_at=excluded.checked_at,state=excluded.state',(plan,cid,oec,baseline,now,state))
   if not gap and interaction_events:
    from lib.invitation_continuation import record
    record(self.s,plan,cid,oec,rel,interaction_events)
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
 'backfilling':db.execute("SELECT count(*) FROM inbox_checkpoint WHERE plan_id=? AND state='backfilling'",(plan,)).fetchone()[0],
 'pendingContent':db.execute('SELECT count(*) FROM inbox_pending WHERE plan_id=?',(plan,)).fetchone()[0],
 'lastCheckedAt':db.execute('SELECT max(checked_at) FROM inbox_checkpoint WHERE plan_id=?',(plan,)).fetchone()[0],
 'oldestCheckedAt':oldest,'oldestAgeSeconds':max(0,int(store.clock()-oldest)) if oldest else None,
 'automaticRepliesEnabled':enabled}
