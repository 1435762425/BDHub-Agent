"""Task-scoped product materials. One name/card binding serves many creators."""
import json
from lib.second_cycle import digest,encoded,assess_offer,CycleError
from lib.batch_tasks import preparation_gate
SCHEMA='''CREATE TABLE IF NOT EXISTS batch_material_job(task_id TEXT NOT NULL,material_key TEXT NOT NULL,offer TEXT NOT NULL,state TEXT NOT NULL DEFAULT 'queued',card TEXT,error TEXT,attempts INTEGER NOT NULL DEFAULT 0,intent_id TEXT,PRIMARY KEY(task_id,material_key));'''
class BatchMaterials:
 def __init__(self,service):
  self.s=service;self.db=service.db;self.db.executescript(SCHEMA)
  if 'intent_id' not in {r[1] for r in self.db.execute('PRAGMA table_info(batch_material_job)')}:self.db.execute('ALTER TABLE batch_material_job ADD COLUMN intent_id TEXT')
 def plan(self,task,members,offers):
  current={o['offerKey']:o for o in offers}
  with self.s.tasks.tx():
   if self.s.tasks.get(task['id'])['state']!='preparing':return
   for m in members:
    o=current.get(m['offerKey'])
    if not o or digest(o)!=m['offerFingerprint']:continue
    self.db.execute('INSERT OR IGNORE INTO batch_material_job(task_id,material_key,offer) VALUES(?,?,?)',(task['id'],m['materialKey'],encoded(o)))
 def bind_intent(self,id,key,intent):
  with self.s.tasks.tx():
   if self.s.tasks.get(id)['state']!='preparing':raise CycleError('task_paused')
   self.db.execute('UPDATE batch_material_job SET intent_id=? WHERE task_id=? AND material_key=?',(intent,id,key))
 def pending(self,id,members):
  keys=list({m['materialKey'] for m in members})
  return [dict(r) for r in self.db.execute("SELECT * FROM batch_material_job WHERE task_id=? AND material_key IN (SELECT value FROM json_each(?)) AND state<>'ready' ORDER BY attempts,rowid LIMIT 5",(id,encoded(keys)))]
 def save(self,id,key,state,card=None,error=None):
  with self.s.tasks.tx():
   if self.s.tasks.get(id)['state']!='preparing':return
   if state=='ready':
    row=self.db.execute('SELECT offer FROM batch_material_job WHERE task_id=? AND material_key=?',(id,key)).fetchone();o=json.loads(row[0])
    if not card or card.get('state')!='verified_read_only' or not card.get('evidenceRefs') or any(card.get(k)!=o.get(v) for k,v in [('pid','pid'),('sourceCampaignId','campaignId'),('creatorPercent','creatorPercent')]) or not str(card.get('listId','')).isdigit() or card.get('wireCampaignId')!=('0' if o.get('catalogSource')=='selected' else o.get('campaignId')) or not isinstance(card.get('checkedAt'),(int,float)):raise CycleError('task_card_binding_mismatch')
   self.db.execute('UPDATE batch_material_job SET state=?,card=?,error=?,attempts=attempts+1 WHERE task_id=? AND material_key=?',(state,encoded(card) if card else None,error,id,key))

def apply_materials(service,id,report,members):
 if not service.db.execute("SELECT 1 FROM sqlite_master WHERE name='batch_material_job'").fetchone():return report,members
 jobs={r['material_key']:dict(r) for r in service.db.execute('SELECT * FROM batch_material_job WHERE task_id=?',(id,))}
 for m in members:
  j=jobs.get(m['materialKey'])
  if j and j['state']=='ready' and digest(json.loads(j['offer']))==m['offerFingerprint']:
   m['cardObservation']=json.loads(j['card']);m['checks']['taplink']=True;m['livePreparationVerified']=True
 gate=preparation_gate(report['target'],members,reserve=report.get('reserve'))
 report=report|{'verifiedReady':gate['ready'],'state':'ready' if gate['complete'] else 'preparing','blockers':[b for b in report['blockers'] if b!='task_taplink_adapter_pending']}
 missing={m['materialKey'] for m in members if not m['checks']['taplink']}
 if missing:report['blockers'].append('task_materials_pending')
 for error in sorted({jobs[k]['error'] for k in missing if k in jobs and jobs[k]['error']}):report['blockers'].append(error)
 report['materialJobs']={'total':len({m['materialKey'] for m in members}),'ready':len({m['materialKey'] for m in members if m['checks']['taplink']}),'pending':len(missing)}
 report['stages']=[s|{'done':gate['ready']} if s['key']=='taplink' else s for s in report['stages']]
 return report,members
