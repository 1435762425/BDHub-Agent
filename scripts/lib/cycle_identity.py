"""Durable handoff from Kalodata source edges to existing identity discovery."""
import json,sqlite3
from contextlib import closing
from pathlib import Path
from lib.second_cycle import CycleError,digest,encoded,identifier
from lib.creator_discovery import BLOCKED_MAX_ATTEMPTS, preview
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
  if self.store._plan(plan)['market']!='it':raise CycleError('identity_market_not_enabled')
  if self.store._plan(plan)['state']!='active':raise CycleError('plan_paused')
  heads=self._source_heads(plan)
  tables={r[0] for r in self.store.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
  current={'lead_query_head','lead_query_selection'}<=tables and self.store.db.execute('SELECT 1 FROM lead_query_head WHERE plan_id=? LIMIT 1',(plan,)).fetchone()
  # CROSS JOIN fixes the loop order.  With ordinary JOIN SQLite chose q -> every source_edge -> s,
  # turning a few thousand current rows into tens of millions of JSON parses while holding the
  # write transaction.  The current head must drive its bounded selection before the edge lookup.
  if source_ids is not None:
   sql="""SELECT e.source_id,e.payload FROM source_edge e
    LEFT JOIN cycle_identity_handoff h ON h.plan_id=e.plan_id AND h.source_id=e.source_id
    WHERE e.plan_id=? AND h.source_id IS NULL AND (? IS NULL OR e.source_id IN (SELECT value FROM json_each(?)))
    AND json_extract(e.payload,'$.creatorId') IS NULL ORDER BY e.source_id LIMIT 500"""
  elif 'current_identity_source' in {r[0] for r in self.store.db.execute("SELECT name FROM sqlite_master WHERE type='view'")}:
   sql="""WITH current_sources AS MATERIALIZED (SELECT * FROM current_identity_source WHERE plan_id=?)
    SELECT e.source_id,e.payload FROM current_sources x
    CROSS JOIN source_edge e
    LEFT JOIN cycle_identity_handoff h ON h.plan_id=x.plan_id AND h.source_id=x.source_id
    WHERE e.plan_id=x.plan_id AND e.source_id=x.source_id AND h.source_id IS NULL AND json_extract(e.payload,'$.creatorId') IS NULL
    AND (? IS NULL OR x.source_id IN (SELECT value FROM json_each(?)))
    ORDER BY x.source_rank,x.source_id LIMIT 500"""
  else:
   sql="""SELECT e.source_id,e.payload FROM lead_query_head q
    CROSS JOIN lead_query_selection s
    CROSS JOIN source_edge e
    LEFT JOIN cycle_identity_handoff h ON h.plan_id=e.plan_id AND h.source_id=e.source_id
    WHERE s.query_id=q.query_id AND e.plan_id=q.plan_id AND e.source_id=s.source_id
    AND e.plan_id=? AND h.source_id IS NULL AND json_extract(e.payload,'$.creatorId') IS NULL
    AND (? IS NULL OR e.source_id IN (SELECT value FROM json_each(?)))
    ORDER BY s.source_rank,e.source_id LIMIT 500""" if current else """SELECT e.source_id,e.payload FROM source_edge e
    LEFT JOIN cycle_identity_handoff h ON h.plan_id=e.plan_id AND h.source_id=e.source_id
    WHERE e.plan_id=? AND h.source_id IS NULL AND json_extract(e.payload,'$.sourceKind')='kalodata_http'
    AND json_extract(e.payload,'$.creatorId') IS NULL
    AND (? IS NULL OR e.source_id IN (SELECT value FROM json_each(?))) ORDER BY e.source_id LIMIT 500"""
  rows=self.store.db.execute(sql,(plan,encoded(source_ids) if source_ids is not None else None,encoded(source_ids) if source_ids is not None else None)).fetchall()
  if not rows:return None
  with self.store.tx():
   if self.store._plan(plan)['state']!='active':raise CycleError('plan_paused')
   if heads!=self._source_heads(plan):return None
   # Another consumer may have handed these exact source edges off while we read.
   rows=[r for r in rows if not self.store.db.execute('SELECT 1 FROM cycle_identity_handoff WHERE plan_id=? AND source_id=?',(plan,r['source_id'])).fetchone()]
   if not rows:return None
   edges=[{'sourceId':r['source_id'],'handle':json.loads(r['payload'])['sourceHandle'].strip().lstrip('@').lower()} for r in rows]
   payload={'edges':edges,'handles':sorted(set(e['handle'] for e in edges))};oid='cycle-identity-'+digest([plan,payload])[:32]
   self.store.db.execute('INSERT INTO cycle_identity_outbox(id,plan_id,payload,batch_id) VALUES(?,?,?,NULL)',(oid,plan,encoded(payload)))
   for e in edges:self.store.db.execute('INSERT INTO cycle_identity_handoff VALUES(?,?,?)',(plan,e['sourceId'],oid))
   return oid
 def _source_heads(self,plan):
  tables={r[0] for r in self.store.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
  a=tuple(tuple(r) for r in self.store.db.execute('SELECT pid,query_id FROM lead_query_head WHERE plan_id=? ORDER BY pid',(plan,))) if 'lead_query_head' in tables else ()
  b=()
  if 'kalodata_video_head' in tables:
   columns={r[1] for r in self.store.db.execute('PRAGMA table_info(kalodata_video_head)')}
   query='SELECT pid,run_id FROM kalodata_video_head'+(' WHERE market=?' if 'market' in columns else '')+' ORDER BY pid'
   b=tuple(tuple(r) for r in self.store.db.execute(query,(self.store._plan(plan)['market'],) if 'market' in columns else ()))
  return a,b
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
 def _reopen_blocked(self,plan):
  """把"因为被挡住而被结算掉"的批次重新打开，让队列能重试它们（幂等）。

  旧规则下 outbox 只要写进了 outcome 就结算，而「被挡住」（请求/签名失败、账号起不来、远端挡回）
  也算 outcome —— 结果是那批人从此不在队列的候选范围里，补 OECID 一直空转、卡片上永远显示待补
  （2026-09-15 实测：40 个批次里 39 个被这样结算掉）。被挡住是"没拿到答案"，不是结论。
  """
  try:
   from lib.identity_retry import snapshot
   policy=snapshot(self.identity_path.parent.parent,'it',now=self.store.clock())
   detail=self.discovery._db.execute("SELECT batch_id FROM discovery_item WHERE status='blocked' AND attempt_no<? GROUP BY batch_id",(2147483647 if policy['enabled'] else BLOCKED_MAX_ATTEMPTS,)).fetchall()
  except Exception:
   return 0
  reopened=0
  with self.store.tx():
   for (batch_id,) in detail:
    row=self.store.db.execute('SELECT id FROM cycle_identity_outbox WHERE plan_id=? AND batch_id=? AND settled=1',(plan,batch_id)).fetchone()
    if not row:continue
    self.store.db.execute('UPDATE cycle_identity_outbox SET settled=0 WHERE id=?',(row[0],));reopened+=1
  return reopened

 def reconcile(self,plan,*,outbox_ids=None):
  if self.store._plan(plan)['market']!='it':raise CycleError('identity_market_not_enabled')
  self._reopen_blocked(plan)
  bound=0;states={};finished=[];bound_ids=[]
  with closing(sqlite3.connect(self.identity_path.resolve().as_uri()+'?mode=ro',uri=True)) as identities, identities:
   identities.row_factory=sqlite3.Row
   from lib.identity_retry import snapshot
   policy=snapshot(self.identity_path.parent.parent,'it',now=self.store.clock())
   judgments={}
   def judgment(handle,current):
    """Reuse one exact handle judgment across every current source edge.

    A Find answers who a handle is; it is not scoped to a PID or to the outbox that first asked.
    New lead rebuilds therefore must consume an earlier terminal judgment locally instead of either
    repeating the platform request or leaving the new edge forever queued behind ``--skip-judged``.
    A resolved identity is stronger than an unresolved row, but conflicting resolved identities are
    never guessed through.
    """
    if handle in judgments:return judgments[handle]
    rows=[dict(r) for r in self.discovery._db.execute(
     "SELECT * FROM discovery_item WHERE handle=? AND (status IN ('completed','unresolved') OR outcome='identity_only') "
     "ORDER BY CASE status WHEN 'completed' THEN 0 ELSE 1 END,finished_at DESC,rowid DESC",(handle,)).fetchall()]
    completed=[r for r in rows if (r['status']=='completed' or r['outcome']=='identity_only') and r['creator_id'] and r['oec_id']]
    if completed:
     identities_seen={(r['creator_id'],r['oec_id']) for r in completed}
     if len(identities_seen)!=1:raise CycleError('identity_conflict')
     # Prefer the current batch's item when it is terminal; otherwise use the newest proof-bearing
     # item. The proof remains the original immutable discovery evidence.
     completed.sort(key=lambda r:(r['id']!=(current or {}).get('id'),-(r['finished_at'] is not None),r['id']))
     import re
     for candidate in completed:
      proofs=identities.execute("SELECT evidence_ref FROM identity_observation WHERE creator_id=? AND market='it' AND oec_id=?",(candidate['creator_id'],candidate['oec_id'])).fetchall()
      pattern=re.compile(re.escape(f"creator-discovery:{candidate['id']}:")+r'[a-f0-9]{64}:discovery-result$')
      proofs=[r[0] for r in proofs if pattern.fullmatch(r[0])]
      if len(proofs)==1:
       judgments[handle]=({**candidate,'status':'completed','creatorId':candidate['creator_id'],'oecId':candidate['oec_id']},proofs[0])
       return judgments[handle]
     raise CycleError('identity_proof_missing')
    unresolved=next((r for r in rows if r['status']=='unresolved'),None)
    if not unresolved and handle in policy['isolated']:
     judgments[handle]=({'status':'technical_isolated','outcome':None,'creatorId':None,'oecId':None},None)
    else:judgments[handle]=(unresolved,None) if unresolved else (current,None)
    return judgments[handle]
   for box in self.store.db.execute('SELECT * FROM cycle_identity_outbox WHERE plan_id=? AND batch_id IS NOT NULL AND settled=0 AND (? IS NULL OR id IN (SELECT value FROM json_each(?)))',(plan,encoded(outbox_ids) if outbox_ids is not None else None,encoded(outbox_ids) if outbox_ids is not None else None)).fetchall():
    detail=self.discovery.detail(box['batch_id']);states[box['batch_id']]=detail['batch']['counts']
    matched={r['handle']:r for r in detail['items']}
    counts=detail['batch']['counts']
    # 队列空 + 没有在跑 + **没有被挡住还能重试的**，才算这一批有结论了。被挡住的项要退避重试，
    # 所以它没结论之前不能把 outbox 结算掉——否则那批人从此出不了候选范围。
    all_terminal=True
    for edge in json.loads(box['payload'])['edges']:
     item,proof=judgment(edge['handle'],matched.get(edge['handle']))
     if not item:
      all_terminal=False
      continue
     terminal=item['status'] in ('completed','unresolved','technical_isolated')
     all_terminal=all_terminal and terminal
     with self.store.tx():
      self.store.db.execute('INSERT INTO cycle_identity_outcome VALUES(?,?,?) ON CONFLICT(plan_id,source_id) DO UPDATE SET status=excluded.status',(plan,edge['sourceId'],item['status']))
     if not (item['status']=='completed' or item['outcome']=='identity_only') or not item['creatorId'] or not item['oecId']:continue
     with self.store.tx():
      old=self.store.db.execute('SELECT * FROM cycle_identity_resolution WHERE plan_id=? AND source_id=?',(plan,edge['sourceId'])).fetchone()
      if old:
       if old['creator_id']!=item['creatorId'] or old['oec']!=item['oecId']:raise CycleError('resolution_conflict')
       bound_ids.append(edge['sourceId'])
       continue
      relationship=self.store.db.execute('SELECT * FROM relationship WHERE plan_id=? AND (creator_id=? OR oec=?)',(plan,item['creatorId'],item['oecId'])).fetchone()
      if relationship and (relationship['creator_id']!=item['creatorId'] or relationship['oec']!=item['oecId']):raise CycleError('identity_conflict')
      self.store.db.execute('INSERT OR IGNORE INTO relationship(plan_id,creator_id,oec) VALUES(?,?,?)',(plan,item['creatorId'],item['oecId']))
      self.store.db.execute('INSERT INTO cycle_identity_resolution VALUES(?,?,?,?,?)',(plan,edge['sourceId'],item['creatorId'],item['oecId'],proof));bound+=1;bound_ids.append(edge['sourceId'])
    if all_terminal or (not counts['queued'] and not counts['running'] and not counts.get('retryableBlocked')):finished.append(box['id'])
  if states:
   if bound_ids:self.store.project_current_offers(plan,source_ids=bound_ids)
   with self.store.tx():
    for oid in finished:self.store.db.execute('UPDATE cycle_identity_outbox SET settled=1 WHERE id=?',(oid,))
  return {'newBindings':bound,'batches':states}
