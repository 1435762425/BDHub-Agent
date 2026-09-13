"""Read-only reply tools with small outputs; internal agency commission is never exported."""
import json
from lib.second_cycle import CycleError,digest,encoded
SCHEMA='''CREATE TABLE IF NOT EXISTS service_fact_result(plan_id TEXT NOT NULL,creator_id TEXT NOT NULL,pending_revision INTEGER NOT NULL,tool TEXT NOT NULL,payload TEXT NOT NULL,observed REAL NOT NULL,PRIMARY KEY(plan_id,creator_id,pending_revision,tool));'''
TOOLS=frozenset({'get_relationship_product_context','get_current_creator_commission'})
class ReplyFacts:
 def __init__(self,store,refresh):self.s=store;self.refresh=refresh;store.db.executescript(SCHEMA)
 def product(self,plan,creator):
  # Explicit source is the latest completely confirmed group, not a sample record or a guessed PID.
  rows=self.s.db.execute("SELECT snapshot FROM cycle_delivery WHERE plan_id=? AND creator_id=? AND state='confirmed' ORDER BY created DESC LIMIT 2",(plan,creator)).fetchall()
  if not rows:raise CycleError('no_confirmed_product_context')
  candidates=[json.loads(r[0]) for r in rows]
  if len({c['pid'] for c in candidates})>1:raise CycleError('ambiguous_product_context')
  return candidates[0]
 def call(self,tool,plan,creator):
  if tool not in TOOLS:raise CycleError('reply_tool_not_allowed')
  c=self.product(plan,creator)
  result={'pid':c['pid'],'shortName':c['name']['shortNameIt'],'source':'confirmed_delivery','sourceId':c['source']['sourceId'],'productContextRequiresReview':True}
  if tool=='get_current_creator_commission':
   proof=self.refresh(c)
   if proof.get('state')!='verified_read_only' or proof.get('pid')!=c['pid'] or proof.get('listId')!=c['card']['listId']:raise CycleError('reply_fact_binding_mismatch')
   result.update(creatorPercent=proof['creatorPercent'],observedAt=proof['checkedAt'],evidenceRefs=proof['evidenceRefs'])
  else:result.update(observedAt=self.s.clock())
  return result
 def process_due(self,plan):
  if self.s._plan(plan)['state']!='active':return []
  rows=self.s.db.execute("SELECT * FROM inbox_pending WHERE plan_id=? AND state='needs_facts'",(plan,)).fetchall();results=[]
  for p in rows[:3]:
   rel=self.s.db.execute('SELECT * FROM relationship WHERE plan_id=? AND creator_id=?',(plan,p['creator_id'])).fetchone()
   if rel['mode']!='auto' or rel['rejected']:continue
   assessment=self.s.db.execute('SELECT decision FROM service_assessment WHERE plan_id=? AND creator_id=? AND pending_revision=?',(plan,p['creator_id'],p['revision'])).fetchone()
   if not assessment:continue
   tools=json.loads(assessment[0])['requiredTools']
   try:facts={t:self.call(t,plan,p['creator_id']) for t in tools}
   except Exception as e:results.append({'state':'waiting_facts','reason':str(e) if isinstance(e,CycleError) else 'fact_read_unavailable'});continue
   with self.s.tx():
    current=self.s.db.execute('SELECT * FROM inbox_pending WHERE plan_id=? AND creator_id=?',(plan,p['creator_id'])).fetchone();control=self.s.db.execute('SELECT revision FROM relationship WHERE plan_id=? AND creator_id=?',(plan,p['creator_id'])).fetchone()
    if self.s._plan(plan)['state']!='active' or current['revision']!=p['revision'] or current['state']!='needs_facts' or control[0]!=rel['revision']:raise CycleError('reply_facts_context_changed')
    for tool,value in facts.items():self.s.db.execute('INSERT OR REPLACE INTO service_fact_result VALUES(?,?,?,?,?,?)',(plan,p['creator_id'],p['revision'],tool,encoded(value),self.s.clock()))
    self.s.db.execute("UPDATE inbox_pending SET state='facts_ready_for_review' WHERE plan_id=? AND creator_id=?",(plan,p['creator_id']))
   results.append({'state':'facts_ready_for_review','automaticReply':False})
  return results
