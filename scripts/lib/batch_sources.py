"""Task-owned whole-batch PID queue. Daily IM quota is not a preparation limit."""
import json,math,time
from decimal import Decimal
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
from lib.second_cycle import CycleError,assess_offer,digest,encoded
from lib.cycle_kalodata import PATH,parse_page

SCHEMA='''
CREATE TABLE IF NOT EXISTS batch_source_plan(task_id TEXT PRIMARY KEY,window_start TEXT NOT NULL,window_end TEXT NOT NULL,expected_per_pid INTEGER NOT NULL,max_pages INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS batch_source_job(id TEXT PRIMARY KEY,task_id TEXT NOT NULL,pid TEXT NOT NULL,offer TEXT NOT NULL,state TEXT NOT NULL DEFAULT 'queued',cursor TEXT NOT NULL DEFAULT '',fence INTEGER NOT NULL DEFAULT 0,lease_until REAL NOT NULL DEFAULT 0,error TEXT,next_attempt REAL NOT NULL DEFAULT 0,UNIQUE(task_id,pid));
CREATE TABLE IF NOT EXISTS batch_source_page(job_id TEXT NOT NULL,cursor TEXT NOT NULL,payload TEXT NOT NULL,imported INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(job_id,cursor));
CREATE TABLE IF NOT EXISTS batch_source_identity(task_id TEXT NOT NULL,outbox_id TEXT NOT NULL,PRIMARY KEY(task_id,outbox_id));
'''
AUTHORIZED='full_preparation_no_messages'
class BatchSources:
 def __init__(self,service,*,initialize=True):
  self.service=service;self.db=service.db;self.tasks=service.tasks
  if initialize:self.db.executescript(SCHEMA)
 def permitted(self,id):
  task=self.tasks.get(id);row=self.db.execute('SELECT payload FROM batch_policy WHERE task_id=?',(id,)).fetchone()
  return task['state']=='preparing' and row and json.loads(row[0]).get('authorizationScope')==AUTHORIZED
 def plan(self,id,offers,candidate_count,*,expected_per_pid=10,max_pages=2):
  from lib.batch_task_service import in_scope
  if type(candidate_count) is not int or candidate_count<0 or type(expected_per_pid) is not int or not 1<=expected_per_pid<=100 or type(max_pages) is not int or not 1<=max_pages<=20:raise CycleError('invalid_preparation_capacity')
  with self.tasks.tx():
   if not self.permitted(id):return {'created':0,'reason':'task_preparation_not_authorized'}
   task=self.tasks.get(id);spec=task['spec']
   end=datetime.fromtimestamp(self.tasks.clock(),ZoneInfo('Asia/Shanghai')).date()-timedelta(days=2)
   self.db.execute('INSERT OR IGNORE INTO batch_source_plan VALUES(?,?,?,?,?)',(id,str(end-timedelta(days=13)),str(end),expected_per_pid,max_pages))
   config=dict(self.db.execute('SELECT * FROM batch_source_plan WHERE task_id=?',(id,)).fetchone())
   pending=self.db.execute("SELECT count(*) FROM batch_source_job WHERE task_id=? AND state IN ('queued','running','awaiting_identity','blocked')",(id,)).fetchone()[0]
   deficit=max(0,spec['target']+spec['reserve']-candidate_count)
   # Plan the whole missing target now. The estimate is not counted as a ready creator.
   needed=max(0,math.ceil(deficit/config['expected_per_pid'])-pending)
   current={r[0] for r in self.db.execute('SELECT pid FROM batch_source_job WHERE task_id=?',(id,))}
   eligible={}
   for o in offers:
    if o['pid'] in current or not in_scope(spec,o) or not assess_offer(o,self.tasks.clock())['eligible']:continue
    prior=eligible.get(o['pid'])
    if prior is None or (Decimal(o['creatorPercent']),o['offerKey'])>(Decimal(prior['creatorPercent']),prior['offerKey']):eligible[o['pid']]=o
   ranked=sorted(eligible.values(),key=lambda o:(not assess_offer(o,self.tasks.clock())['ratingPreferred'],o['pid']))
   chosen=ranked[:needed]
   for o in chosen:
    job='batch-source-'+digest([id,o['pid'],config['window_start'],config['window_end']])[:28]
    self.db.execute('INSERT INTO batch_source_job(id,task_id,pid,offer) VALUES(?,?,?,?)',(job,id,o['pid'],encoded(o)))
   if chosen:self.tasks.event(id,'source_batch_planned',{'pids':len(chosen),'requiredCreators':spec['target']+spec['reserve'],'candidateGap':deficit,'estimatedCreatorsPerPid':config['expected_per_pid']})
   return {'created':len(chosen),'plannedPidCount':pending+len(chosen),'unplannedEstimate':max(0,needed-len(chosen))*config['expected_per_pid'],'candidateGap':deficit}
 def claim(self,id):
  with self.tasks.tx():
   if not self.permitted(id):return None
   if self.db.execute("SELECT 1 FROM batch_source_job WHERE task_id=? AND state='blocked'",(id,)).fetchone():return None
   row=self.db.execute("SELECT * FROM batch_source_job WHERE task_id=? AND ((state='queued' AND next_attempt<=?) OR (state='running' AND lease_until<=?)) ORDER BY rowid LIMIT 1",(id,self.tasks.clock(),self.tasks.clock())).fetchone()
   if not row:return None
   self.db.execute("UPDATE batch_source_job SET state='running',fence=fence+1,lease_until=? WHERE id=?",(self.tasks.clock()+120,row['id']))
   r=dict(self.db.execute('SELECT * FROM batch_source_job WHERE id=?',(row['id'],)).fetchone());c=dict(self.db.execute('SELECT * FROM batch_source_plan WHERE task_id=?',(id,)).fetchone())
   return r|{'offer_key':json.loads(r['offer'])['offerKey'],'window_start':c['window_start'],'window_end':c['window_end'],'max_pages':c['max_pages']}
 def _owned(self,c):
  r=self.db.execute('SELECT * FROM batch_source_job WHERE id=?',(c['id'],)).fetchone()
  if not r or r['state']!='running' or r['fence']!=c['fence'] or r['cursor']!=c['cursor'] or r['lease_until']<=self.tasks.clock():raise CycleError('lease_lost')
  return r
 def once(self,id,provider,import_edges):
  claim=self.claim(id)
  if not claim:return {'status':'idle','networkRequests':0}
  calls=0
  try:
   saved=self.db.execute('SELECT payload FROM batch_source_page WHERE job_id=? AND cursor=?',(claim['id'],claim['cursor'])).fetchone()
   if saved:receipt=json.loads(saved[0])
   else:
    payload={'startDate':claim['window_start'],'endDate':claim['window_end'],'authority':True,'pageSize':50,'pageNo':int(claim['cursor'] or '1'),'sort':[{'field':'revenue','type':'DESC'}],'id':claim['pid']}
    self.service.heartbeat();calls=1;body=provider.request(PATH,payload)
    receipt=parse_page(body,claim,self.tasks.clock(),max_pages=claim['max_pages'])
    with self.tasks.tx():
     self._owned(claim)
     if receipt['rowsReceived'] and any(json.loads(r[0])['rowsFingerprint']==receipt['rowsFingerprint'] for r in self.db.execute('SELECT payload FROM batch_source_page WHERE job_id=?',(claim['id'],))):raise CycleError('kalodata_repeated_page')
     self.db.execute('INSERT INTO batch_source_page(job_id,cursor,payload) VALUES(?,?,?)',(claim['id'],claim['cursor'],encoded(receipt)))
   if not self.permitted(id):
    with self.tasks.tx():self._owned(claim);self.db.execute("UPDATE batch_source_job SET state='queued',lease_until=0 WHERE id=?",(claim['id'],))
    return {'status':'paused_receipt_saved','networkRequests':calls}
   # Receipts survive a process failure between the two independent local stores.
   prior={e['sourceId'] for r in self.db.execute('SELECT payload FROM batch_source_page WHERE job_id=? AND cursor<>?',(claim['id'],claim['cursor'])) for e in json.loads(r[0])['edges']}
   edges=[e for e in receipt['edges'] if e['sourceId'] not in prior]
   import_edges(edges)
   with self.tasks.tx():
    self._owned(claim)
    self.db.execute('UPDATE batch_source_page SET imported=1 WHERE job_id=? AND cursor=?',(claim['id'],claim['cursor']))
    self.db.execute('UPDATE batch_source_job SET state=?,cursor=?,lease_until=0,error=NULL WHERE id=?',('awaiting_identity' if receipt['done'] and (edges or prior) else 'completed' if receipt['done'] else 'queued',receipt['nextCursor'],claim['id']))
   return {'status':'page_saved','networkRequests':calls,'addedEdges':len(edges),'coverage':receipt['coverage']}
  except Exception as error:
   code=str(error) if isinstance(error,CycleError) else 'kalodata_read_failed'
   fatal=code in ('kalodata_auth_required','kalodata_business_rejected','kalodata_repeated_page','kalodata_rows_invalid','kalodata_page_bound')
   if not fatal:code='kalodata_read_failed' if code!='lease_lost' else code
   with self.tasks.tx():
    row=self.db.execute('SELECT * FROM batch_source_job WHERE id=?',(claim['id'],)).fetchone()
    if row and row['state']=='running' and row['fence']==claim['fence']:
     self.db.execute('UPDATE batch_source_job SET state=?,error=?,lease_until=0,next_attempt=? WHERE id=?',('blocked' if fatal else 'queued',code,self.tasks.clock()+60,claim['id']))
   return {'status':'blocked' if fatal else 'retry_wait','error':code,'networkRequests':calls}
 def edges(self,id):
  return [e for row in self.db.execute('SELECT p.payload FROM batch_source_page p JOIN batch_source_job j ON j.id=p.job_id WHERE j.task_id=? AND p.imported=1',(id,)) for e in json.loads(row[0])['edges']]
 def status(self,id):
  counts={r[0]:r[1] for r in self.db.execute('SELECT state,count(*) FROM batch_source_job WHERE task_id=? GROUP BY state',(id,))}
  errors=[r[0] for r in self.db.execute('SELECT DISTINCT error FROM batch_source_job WHERE task_id=? AND error IS NOT NULL',(id,))]
  pages=self.db.execute('SELECT count(*) FROM batch_source_page p JOIN batch_source_job j ON j.id=p.job_id WHERE j.task_id=?',(id,)).fetchone()[0]
  return {'pids':sum(counts.values()),'states':counts,'pages':pages,'edges':self.db.execute("SELECT count(DISTINCT json_extract(e.value,'$.sourceId')) FROM batch_source_page p JOIN batch_source_job j ON j.id=p.job_id, json_each(p.payload,'$.edges') e WHERE j.task_id=? AND p.imported=1",(id,)).fetchone()[0],'errors':errors,'dailyQuotaUsed':0}
