"""User-authorized ACC6/12 QPS validation on 1000 additional distinct candidates."""
import json,sqlite3,time
from contextlib import closing
from pathlib import Path
from lib.second_cycle import digest,encoded
from lib.batch_tasks import BatchError,normalize_spec
SCHEMA='''
CREATE TABLE IF NOT EXISTS identity_soak_run(id TEXT PRIMARY KEY,task_id TEXT NOT NULL,state TEXT NOT NULL,config TEXT NOT NULL,started REAL NOT NULL,fail_streak INTEGER NOT NULL DEFAULT 0,stop_reason TEXT);
CREATE TABLE IF NOT EXISTS identity_soak_member(run_id TEXT NOT NULL,oec TEXT NOT NULL,role TEXT NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(run_id,oec));
CREATE TABLE IF NOT EXISTS identity_soak_group(run_id TEXT NOT NULL,cohort_id TEXT NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(run_id,cohort_id));
CREATE TABLE IF NOT EXISTS identity_soak_proof(run_id TEXT NOT NULL,oec TEXT NOT NULL,cohort_id TEXT NOT NULL,PRIMARY KEY(run_id,oec));
'''
class IdentitySoak:
 def __init__(self,service):self.s=service;self.db=service.db;self.db.executescript(SCHEMA)
 def start(self,task_id,*,expected_revision):
  id='soak-'+digest([task_id,'user-12qps-extra1000-total2100'])[:28]
  with self.s.tasks.tx():
   old=self.db.execute('SELECT * FROM identity_soak_run WHERE id=?',(id,)).fetchone()
   if old:return id
   t=self.s.tasks.get(task_id)
   if t['revision']!=expected_revision or t['state'] not in ('preparing','ready'):raise BatchError('revision_conflict')
   if t['spec']['target']+t['spec']['reserve']!=1100 or t['spec']['market']!='it':raise BatchError('unexpected_soak_baseline')
   baseline=[json.loads(r[0]) for r in self.db.execute('SELECT payload FROM batch_member WHERE task_id=?',(task_id,))]
   before=t['spec'];raw={k:v for k,v in before.items() if k not in ('timezone',)};raw.update(target=2000,reserve=100)
   after=normalize_spec(raw)
   cfg={'account':'acc6','market':'it','qps':12,'lanes':9,'cohortSize':20,'newTarget':1000,'baselineTarget':1100,'totalTarget':2100,'beforeSpec':before,'afterSpec':after,'errorCohortLimit':3,'messagesEnabled':False,'multiAccount':False}
   self.db.execute("INSERT INTO identity_soak_run(id,task_id,state,config,started) VALUES(?,?,'active',?,?)",(id,task_id,encoded(cfg),self.s.tasks.clock()))
   for m in baseline:self.db.execute("INSERT INTO identity_soak_member VALUES(?,?,'baseline',?)",(id,m['oec'],encoded(m)))
   self.db.execute("UPDATE batch_task SET spec=?,spec_hash=?,state='preparing',revision=revision+1 WHERE id=?",(encoded(after),digest(after),task_id))
   self.db.execute('UPDATE batch_preparation_run SET next_run=0 WHERE task_id=?',(task_id,))
   self.s.tasks.event(task_id,'soak_extension_confirmed',{'runId':id,'before':before,'after':after,'baselineCaptured':len(baseline),'newValidationTarget':1000,'userInstruction':'单账号12QPS，新增1000，总计2100，暂不启用多账号'})
  return id
 def config(self,id):
  r=self.db.execute('SELECT * FROM identity_soak_run WHERE id=?',(id,)).fetchone()
  if not r:raise BatchError('soak_missing')
  return dict(r)|{'config':json.loads(r['config'])}
 def allowed(self,id):
  r=self.config(id)
  return r['state']=='active' and self.s.tasks.get(r['task_id'])['state']=='preparing'
 def record(self,id,cohort,report,confirmed_oecs):
  cfg=self.config(id)['config']
  if report.get('soakRun')!=id or report.get('account')!=cfg['account'] or report.get('qps')!=12:raise BatchError('soak_evidence_mismatch')
  req=report.get('requests',[]);errors=sum(bool(r.get('status')=='error' or (r.get('status')=='returned' and (r.get('httpStatus')!=200 or r.get('code')!='0' or r.get('verificationRequired')))) for r in req)
  limited=any(r.get('httpStatus')==429 or any(a.get('httpStatus')==429 for a in r.get('attempts',[])) for r in req)
  failed=report.get('status')!='completed' and not (not req and report.get('errorType')=='BlockingIOError')
  metrics={'requests':report.get('counters',{}).get('request_count',0),'targets':len(report.get('targets',[])),'errors':errors,'unmatched':sum(t.get('reason')=='no_exact_handle' for t in report.get('targets',[])),'challenges':report.get('counters',{}).get('challenge_count',0),'matched':len(confirmed_oecs),'configuredQps':12,'startedAt':report.get('startedAt'),'finishedAt':report.get('finishedAt'),'requestStartTimes':report.get('requestStartTimes',[]),'reportHash':digest(report),'state':report.get('status')}
  with self.s.tasks.tx():
   if self.db.execute('SELECT 1 FROM identity_soak_group WHERE run_id=? AND cohort_id=?',(id,cohort)).fetchone():return
   self.db.execute('INSERT INTO identity_soak_group VALUES(?,?,?)',(id,cohort,encoded(metrics)))
   evidenced={t.get('oecId') for t in report.get('targets',[]) if t.get('status')=='identity_verified'}
   if report.get('identityFileUnchanged') is True:
    for oec in set(confirmed_oecs)&evidenced:self.db.execute('INSERT OR IGNORE INTO identity_soak_proof VALUES(?,?,?)',(id,oec,cohort))
   streak=self.config(id)['fail_streak'] if report.get('status')=='incomplete' else self.config(id)['fail_streak']+1 if failed else 0
   self.db.execute('UPDATE identity_soak_run SET fail_streak=? WHERE id=?',(streak,id))
   if limited or streak>=cfg['errorCohortLimit']:
    reason='platform_rate_limit' if limited else 'consecutive_cohort_errors'
    self.db.execute("UPDATE identity_soak_run SET state='attention',stop_reason=? WHERE id=?",(reason,id))
    self.s.tasks.event(self.config(id)['task_id'],'soak_guard_stopped',{'runId':id,'reason':reason})
 def checkpoint(self,id,members):
  r=self.config(id);cfg=r['config'];proofs={x[0] for x in self.db.execute('SELECT oec FROM identity_soak_proof WHERE run_id=?',(id,))}
  with self.s.tasks.tx():
   known={x[0] for x in self.db.execute('SELECT oec FROM identity_soak_member WHERE run_id=?',(id,))}
   baseline=self.db.execute("SELECT count(*) FROM identity_soak_member WHERE run_id=? AND role='baseline'",(id,)).fetchone()[0]
   new=self.db.execute("SELECT count(*) FROM identity_soak_member WHERE run_id=? AND role='validation'",(id,)).fetchone()[0]
   for m in members:
    if m['oec'] in known or m['oec'] not in proofs:continue
    role='baseline' if baseline<cfg['baselineTarget'] else 'validation'
    if role=='validation' and new>=cfg['newTarget']:continue
    self.db.execute('INSERT INTO identity_soak_member VALUES(?,?,?,?)',(id,m['oec'],role,encoded(m)));known.add(m['oec'])
    if role=='baseline':baseline+=1
    else:new+=1
   current={m['oec'] for m in members}
   baseline_oecs={x[0] for x in self.db.execute("SELECT oec FROM identity_soak_member WHERE run_id=? AND role='baseline'",(id,))}
   valid_new=len((current-baseline_oecs)&proofs)
   if len(current)>=cfg['totalTarget'] and valid_new>=cfg['newTarget'] and r['state']=='active':
    self.db.execute("UPDATE identity_soak_run SET state='completed' WHERE id=?",(id,));self.s.tasks.event(r['task_id'],'soak_completed',{'newCandidates':valid_new,'totalCandidates':len(current)})

def read_soak(root,id,cohort,account,*,check_members=False):
 with closing(sqlite3.connect((Path(root)/'var/batch-tasks.sqlite').as_uri()+'?mode=ro',uri=True)) as c:
  row=c.execute('SELECT s.state,s.config,t.state FROM identity_soak_run s JOIN batch_task t ON t.id=s.task_id WHERE s.id=?',(id,)).fetchone()
  if not row or row[0]!='active' or row[2]!='preparing':raise ValueError('soak_not_active')
  cfg=json.loads(row[1])
  if cfg['account']!=account or account!='acc6' or cfg['qps']!=12 or not cohort:raise ValueError('soak_scope_mismatch')
  if check_members:
   task=c.execute('SELECT task_id FROM identity_soak_run WHERE id=?',(id,)).fetchone()[0]
   boxes=[r[0] for r in c.execute('SELECT outbox_id FROM batch_source_identity WHERE task_id=?',(task,))]
   with closing(sqlite3.connect((Path(root)/'var/second-cycle.sqlite').as_uri()+'?mode=ro',uri=True)) as source:
    allowed={r[0] for r in source.execute('SELECT o.batch_id FROM cycle_identity_outbox o JOIN plan p ON p.id=o.plan_id WHERE o.id IN (SELECT value FROM json_each(?)) AND p.institution=? AND p.market=?',(encoded(boxes),cfg['afterSpec']['institution'],cfg['market']))}
   with closing(sqlite3.connect((Path(root)/'var/creator-discovery.sqlite').as_uri()+'?mode=ro',uri=True)) as discovery:
    group=discovery.execute("SELECT payload FROM discovery_cohort WHERE id=? AND state='running'",(cohort,)).fetchone()
   if not group or any(m['batch_id'] not in allowed for m in json.loads(group[0])):raise ValueError('soak_cohort_scope_mismatch')
  return cfg

def status(service,task_id):
 c=service.db
 if not c.execute("SELECT 1 FROM sqlite_master WHERE name='identity_soak_run'").fetchone():return None
 row=c.execute('SELECT * FROM identity_soak_run WHERE task_id=? ORDER BY started DESC LIMIT 1',(task_id,)).fetchone()
 if not row:return None
 cfg=json.loads(row['config']);counts={r[0]:r[1] for r in c.execute('SELECT role,count(*) FROM identity_soak_member WHERE run_id=? GROUP BY role',(row['id'],))}
 groups=[json.loads(r[0]) for r in c.execute('SELECT payload FROM identity_soak_group WHERE run_id=? ORDER BY rowid',(row['id'],))]
 current={r[0] for r in c.execute('SELECT oec FROM batch_member WHERE task_id=?',(task_id,))}
 baseline={r[0] for r in c.execute("SELECT oec FROM identity_soak_member WHERE run_id=? AND role='baseline'",(row['id'],))}
 proofs={r[0] for r in c.execute('SELECT oec FROM identity_soak_proof WHERE run_id=?',(row['id'],))}
 timed=[sorted(g.get('requestStartTimes',[])) for g in groups if len(g.get('requestStartTimes',[]))>1]
 duration=sum(ts[-1]-ts[0] for ts in timed)
 actual_qps=round(sum(len(ts)-1 for ts in timed)/duration,2) if duration>0 else None
 quota=bool(c.execute("SELECT 1 FROM batch_source_job WHERE task_id=? AND state='blocked' AND error='kalodata_daily_quota_exhausted'",(task_id,)).fetchone()) if c.execute("SELECT 1 FROM sqlite_master WHERE name='batch_source_job'").fetchone() else False
 next_check=c.execute("SELECT min(next_attempt) FROM batch_source_job WHERE task_id=? AND state='blocked' AND error='kalodata_daily_quota_exhausted'",(task_id,)).fetchone()[0] if quota else None
 return {'nextSourceQuotaCheck':next_check,'sourceQuotaExhausted':quota,'id':row['id'],'state':row['state'],'account':'acc6','configuredQps':12,'httpLanes':9,'actualQps':actual_qps,'baselineCandidates':len(current&baseline),'baselineCaptured':len(baseline),'newCandidates':min(1000,len((current-baseline)&proofs)),'newTarget':1000,'totalTarget':2100,'elapsedSeconds':round(service.tasks.clock()-row['started']),'cohorts':len(groups),'requests':sum(g['requests'] for g in groups),'errors':sum(g['errors'] for g in groups),'unmatched':sum(g['unmatched'] for g in groups),'challenges':sum(g['challenges'] for g in groups),'stopReason':row['stop_reason'],'multiAccount':False}
