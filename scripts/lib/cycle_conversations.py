"""Resumable metadata-only IM discovery. Never creates conversations or replies."""
import json, sqlite3, time

class ConversationIndex:
 def __init__(self,path,clock=time.time):
  self.clock=clock;self.db=sqlite3.connect(path);self.db.row_factory=sqlite3.Row
  self.db.executescript('''CREATE TABLE IF NOT EXISTS scan(scope TEXT PRIMARY KEY,cursor INTEGER NOT NULL DEFAULT 0,state TEXT NOT NULL DEFAULT 'pending',pages INTEGER NOT NULL DEFAULT 0,updated REAL NOT NULL);
CREATE TABLE IF NOT EXISTS conversation(scope TEXT NOT NULL,cid TEXT NOT NULL,oec TEXT NOT NULL,kind INTEGER NOT NULL,observed REAL NOT NULL,PRIMARY KEY(scope,cid));
CREATE TABLE IF NOT EXISTS scan_page(scope TEXT NOT NULL,cursor INTEGER NOT NULL,next_cursor INTEGER NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(scope,cursor));''')
 def state(self,scope):
  self.db.execute('INSERT OR IGNORE INTO scan(scope,updated) VALUES(?,?)',(scope,self.clock()));self.db.commit()
  return dict(self.db.execute('SELECT * FROM scan WHERE scope=?',(scope,)).fetchone())
 def save(self,scope,cursor,page):
  with self.db:
   current=self.db.execute('SELECT * FROM scan WHERE scope=?',(scope,)).fetchone()
   if not current or current['cursor']!=cursor or current['state']=='complete':raise ValueError('scan_cursor_changed')
   nxt=int(page['nextCursor']);more=page['hasMore']
   if more and (nxt==cursor or self.db.execute('SELECT 1 FROM scan_page WHERE scope=? AND cursor=?',(scope,nxt)).fetchone()):raise ValueError('scan_cursor_cycle')
   for c in page['conversations']:
    old=self.db.execute('SELECT oec,kind FROM conversation WHERE scope=? AND cid=?',(scope,c['conversationId'])).fetchone()
    if old and (old['oec']!=c['oecId'] or old['kind']!=c['conversationType']):raise ValueError('conversation_identity_conflict')
    self.db.execute('INSERT INTO conversation VALUES(?,?,?,?,?) ON CONFLICT(scope,cid) DO UPDATE SET observed=excluded.observed',(scope,c['conversationId'],c['oecId'],c['conversationType'],self.clock()))
   # No ticket, message body or credentials are persisted.
   summary={k:page[k] for k in ('hasMore','nextCursor','invalidConversations','otherMarketConversations')}
   self.db.execute('INSERT INTO scan_page VALUES(?,?,?,?)',(scope,cursor,nxt,json.dumps(summary)))
   self.db.execute('UPDATE scan SET cursor=?,state=?,pages=pages+1,updated=? WHERE scope=?',(nxt,'pending' if more else 'complete',self.clock(),scope))
 def find(self,scope,oec):
  return [dict(r) for r in self.db.execute('SELECT cid AS conversationId,oec AS oecId,kind AS conversationType,observed FROM conversation WHERE scope=? AND oec=?',(scope,oec))]
 def close(self):self.db.close()
