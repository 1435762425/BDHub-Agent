"""Durable handoff from Kalodata source edges to existing identity discovery."""
import json,sqlite3
from contextlib import closing
from pathlib import Path
from lib.second_cycle import CycleError,digest,encoded,identifier
from lib.creator_discovery import preview
SCHEMA='''
CREATE TABLE IF NOT EXISTS cycle_identity_outbox(id TEXT PRIMARY KEY,plan_id TEXT NOT NULL,payload TEXT NOT NULL,batch_id TEXT,settled INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS cycle_identity_handoff(plan_id TEXT NOT NULL,source_id TEXT NOT NULL,outbox_id TEXT NOT NULL,PRIMARY KEY(plan_id,source_id));
CREATE TABLE IF NOT EXISTS cycle_identity_resolution(plan_id TEXT NOT NULL,source_id TEXT NOT NULL,creator_id TEXT NOT NULL,oec TEXT NOT NULL,evidence_ref TEXT NOT NULL,PRIMARY KEY(plan_id,source_id));
'''
class IdentityBridge:
 def __init__(self,store,discovery,identity_path):
  self.store=store;self.discovery=discovery;self.identity_path=Path(identity_path);store.db.executescript(SCHEMA)
  if 'settled' not in {r[1] for r in store.db.execute('PRAGMA table_info(cycle_identity_outbox)')}:
   store.db.execute('ALTER TABLE cycle_identity_outbox ADD COLUMN settled INTEGER NOT NULL DEFAULT 0')
 def freeze(self,plan,*,source_ids=None):
  with self.store.tx():
   if self.store._plan(plan)['market']!='it':raise CycleError('identity_market_not_enabled')
   if self.store._plan(plan)['state']!='active':raise CycleError('plan_paused')
   rows=self.store.db.execute("SELECT e.source_id,e.payload FROM source_edge e LEFT JOIN cycle_identity_handoff h USING(plan_id,source_id) WHERE e.plan_id=? AND h.source_id IS NULL AND json_extract(e.payload,'$.sourceKind')='kalodata_http' AND json_extract(e.payload,'$.creatorId') IS NULL AND (? IS NULL OR e.source_id IN (SELECT value FROM json_each(?))) ORDER BY e.source_id LIMIT 500",(plan,encoded(source_ids) if source_ids is not None else None,encoded(source_ids) if source_ids is not None else None)).fetchall()
   if not rows:return None
   edges=[{'sourceId':r['source_id'],'handle':json.loads(r['payload'])['sourceHandle']} for r in rows]
   payload={'edges':edges,'handles':sorted(set(e['handle'] for e in edges))};oid='cycle-identity-'+digest([plan,payload])[:32]
   self.store.db.execute('INSERT INTO cycle_identity_outbox(id,plan_id,payload,batch_id) VALUES(?,?,?,NULL)',(oid,plan,encoded(payload)))
   for e in edges:self.store.db.execute('INSERT INTO cycle_identity_handoff VALUES(?,?,?)',(plan,e['sourceId'],oid))
   return oid
 def dispatch(self,plan,*,outbox_ids=None):
  if self.store._plan(plan)['market']!='it':raise CycleError('identity_market_not_enabled')
  result=[]
  for row in self.store.db.execute('SELECT * FROM cycle_identity_outbox WHERE plan_id=? AND batch_id IS NULL AND (? IS NULL OR id IN (SELECT value FROM json_each(?))) ORDER BY rowid',(plan,encoded(outbox_ids) if outbox_ids is not None else None,encoded(outbox_ids) if outbox_ids is not None else None)).fetchall():
   if self.store._plan(plan)['state']!='active':break
   payload=json.loads(row['payload']);text='\n'.join(payload['handles']);label='Kalodata 二发线索身份解析'
   v=preview('it',label,text);r=self.discovery.submit('it',label,text,v['previewHash'],row['id'])
   with self.store.tx():self.store.db.execute('UPDATE cycle_identity_outbox SET batch_id=? WHERE id=? AND batch_id IS NULL',(r['id'],row['id']))
   result.append(r['id'])
  return result
 def reconcile(self,plan,*,outbox_ids=None):
  if self.store._plan(plan)['market']!='it':raise CycleError('identity_market_not_enabled')
  bound=0;states={};finished=[]
  with closing(sqlite3.connect(self.identity_path.resolve().as_uri()+'?mode=ro',uri=True)) as identities, identities:
   identities.row_factory=sqlite3.Row
   for box in self.store.db.execute('SELECT * FROM cycle_identity_outbox WHERE plan_id=? AND batch_id IS NOT NULL AND settled=0 AND (? IS NULL OR id IN (SELECT value FROM json_each(?)))',(plan,encoded(outbox_ids) if outbox_ids is not None else None,encoded(outbox_ids) if outbox_ids is not None else None)).fetchall():
    detail=self.discovery.detail(box['batch_id']);states[box['batch_id']]=detail['batch']['counts']
    matched={r['handle']:r for r in detail['items']}
    if not detail['batch']['counts']['queued'] and not detail['batch']['counts']['running']:finished.append(box['id'])
    for edge in json.loads(box['payload'])['edges']:
     item=matched.get(edge['handle'])
     if not item:continue
     with self.store.tx():
      self.store.db.execute('INSERT INTO cycle_identity_outcome VALUES(?,?,?) ON CONFLICT(plan_id,source_id) DO UPDATE SET status=excluded.status',(plan,edge['sourceId'],item['status']))
     if not (item['status']=='completed' or item['outcome']=='identity_only') or not item['creatorId'] or not item['oecId']:continue
     # The discovery marker is written only after exact Find and unchanged-identity checks.
     import re
     proofs=identities.execute("SELECT evidence_ref FROM identity_observation WHERE creator_id=? AND market='it' AND oec_id=?",(item['creatorId'],item['oecId'])).fetchall()
     pattern=re.compile(re.escape(f"creator-discovery:{item['id']}:")+r'[a-f0-9]{64}:discovery-result$')
     proofs=[r[0] for r in proofs if pattern.fullmatch(r[0])]
     if len(proofs)!=1:raise CycleError('identity_proof_missing')
     proof=proofs[0]
     with self.store.tx():
      old=self.store.db.execute('SELECT * FROM cycle_identity_resolution WHERE plan_id=? AND source_id=?',(plan,edge['sourceId'])).fetchone()
      if old:
       if old['creator_id']!=item['creatorId'] or old['oec']!=item['oecId']:raise CycleError('resolution_conflict')
       continue
      relationship=self.store.db.execute('SELECT * FROM relationship WHERE plan_id=? AND (creator_id=? OR oec=?)',(plan,item['creatorId'],item['oecId'])).fetchone()
      if relationship and (relationship['creator_id']!=item['creatorId'] or relationship['oec']!=item['oecId']):raise CycleError('identity_conflict')
      self.store.db.execute('INSERT OR IGNORE INTO relationship(plan_id,creator_id,oec) VALUES(?,?,?)',(plan,item['creatorId'],item['oecId']))
      self.store.db.execute('INSERT INTO cycle_identity_resolution VALUES(?,?,?,?,?)',(plan,edge['sourceId'],item['creatorId'],item['oecId'],proof));bound+=1
  if states:
   self.store.project_current_offers(plan)
   with self.store.tx():
    for oid in finished:self.store.db.execute('UPDATE cycle_identity_outbox SET settled=1 WHERE id=?',(oid,))
  return {'newBindings':bound,'batches':states}
