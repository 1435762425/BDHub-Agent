"""Real positive-sales candidate acceptance and evidence-backed production promotion."""
import json,sqlite3,math
from pathlib import Path
from contextlib import closing
from lib.second_cycle import encoded,digest,CycleStore
from lib.batch_task_service import TaskService,read_local_preparation
from lib.creator_discovery import preview
SCHEMA='''
CREATE TABLE IF NOT EXISTS identity_acceptance(id TEXT PRIMARY KEY,task_id TEXT NOT NULL,soak_id TEXT NOT NULL,batch_id TEXT,state TEXT NOT NULL,payload TEXT NOT NULL,started REAL NOT NULL,result TEXT);
CREATE TABLE IF NOT EXISTS identity_runtime_policy(account TEXT PRIMARY KEY,qps INTEGER NOT NULL,lanes INTEGER NOT NULL,acceptance_id TEXT NOT NULL,result_hash TEXT NOT NULL,published REAL NOT NULL);
'''
CRITERIA={'size':500,'minimumSuccessRatio':0.99,'minimumIdentityMatchRatio':0.99,'maxVerificationRatio':0.05,'p95Seconds':10,'noRateLimit':True,'noIdentityMismatch':True}

def evaluate(samples,records):
 counts=dict(total=len(samples),processed=0,successful=0,matched=0,unmatched=0,errors=0,identityMismatch=0,rateLimits=0,unresolvedVerification=0,verificationEvents=0,requestAttempts=0)
 durations=[]
 for sample in samples:
  item=records.get(sample['itemId'])
  if not item or item['status'] not in ('completed','unresolved','blocked'):continue
  counts['processed']+=1;r=item.get('report') or {};requests=r.get('requests',[])
  bound=r.get('account')=='acc6' and r.get('market')=='it' and r.get('qps')==12 and r.get('identityFileUnchanged') is True
  success=bound and bool(requests) and all(q.get('targetRef')==sample['itemId'] and q.get('status')=='returned' and q.get('httpStatus')==200 and q.get('code')=='0' and q.get('verificationRequired') is False for q in requests)
  targets=r.get('targets',[])
  exact=success and item['status']=='completed' and item.get('oec')==sample['oec'] and len(targets)==1 and targets[0].get('requestedHandle')==sample['handle'] and targets[0].get('oecId')==sample['oec']
  counts['successful']+=int(success);counts['matched']+=int(exact);counts['unmatched']+=int(success and item['status']=='unresolved');counts['errors']+=int(not success)
  if item.get('oec') and item['oec']!=sample['oec']:counts['identityMismatch']+=1
  for q in requests:
   attempts=q.get('attempts',[]);counts['requestAttempts']+=len(attempts) or 1
   counts['rateLimits']+=sum(a.get('httpStatus')==429 for a in attempts) or int(q.get('httpStatus')==429)
   counts['unresolvedVerification']+=int(bool(q.get('verificationRequired')) or (not success and any(a.get('verificationRequired') for a in attempts)))
   counts['verificationEvents']+=len(q.get('verificationAttempts',[]))
   if isinstance(q.get('durationMs'),(int,float)):durations.append(q['durationMs']/1000)
 p95=sorted(durations)[math.ceil(len(durations)*.95)-1] if durations else None
 counts.update(successRatio=counts['successful']/counts['processed'] if counts['processed'] else None,p95Seconds=p95)
 passed=counts['processed']==500 and counts['total']==500 and counts['successful']>=495 and counts['matched']>=495 and counts['identityMismatch']==0 and counts['rateLimits']==0 and counts['unresolvedVerification']==0 and counts['verificationEvents']<=25 and p95 is not None and p95<=10
 counts['passed']=passed
 counts['final']=counts['processed']==500 or counts['identityMismatch']>0 or counts['rateLimits']>0
 return counts

class Acceptance:
 def __init__(self,service):self.s=service;self.db=service.db;self.db.executescript(SCHEMA)
 def start(self,root,discovery):
  root=Path(root);task=self.s.listing()['tasks'][0];id='accept-'+digest([task['id'],'user-positive-500-at-12qps'])[:28]
  prior=self.db.execute('SELECT * FROM identity_acceptance WHERE id=?',(id,)).fetchone()
  if prior and prior['batch_id']:return self.get(id)
  if prior:payload=json.loads(prior['payload'])
  else:
   _,members=read_local_preparation(root,task['spec']);tested={r[0] for r in self.db.execute('SELECT oec FROM identity_soak_proof')}
   with closing(sqlite3.connect((root/'var/creator-identities.sqlite').as_uri()+'?mode=ro',uri=True)) as ids:
    handles={r[0]:r[1] for r in ids.execute("SELECT oec_id,current_handle FROM creator_identity WHERE market='it' AND handle_conflict=0 AND current_handle IS NOT NULL")}
   selected=[];seen=set()
   with CycleStore(root/'var/second-cycle.sqlite',readonly=True) as cycle:
    plan=cycle.db.execute('SELECT id FROM plan WHERE institution=? AND market=?',(task['spec']['institution'],task['spec']['market'])).fetchone()[0]
    for m in members:
     handle=handles.get(m['oec']);edge=cycle.db.execute('SELECT payload FROM source_edge WHERE plan_id=? AND source_id=?',(plan,m['sourceId'])).fetchone()
     if m['oec'] in tested or not handle or handle in seen or not edge:continue
     source=json.loads(edge[0])
     if source.get('sourceKind')!='kalodata_http' or type(source.get('units')) is not int or source['units']<=0:continue
     selected.append({'oec':m['oec'],'handle':handle,'pid':m['pid'],'sourceId':m['sourceId'],'sourceEvidenceRef':source['evidenceRef'],'units':source['units']});seen.add(handle)
     if len(selected)==500:break
   if len(selected)!=500:raise ValueError('insufficient_real_untested_candidates')
   payload={'samples':selected,'criteria':CRITERIA,'plan':plan,'account':'acc6','qps':12,'lanes':9,'purpose':'real positive-sales candidates not yet validated at 12 QPS','sourceTaskSpec':task['spec']}
   with self.s.tasks.tx():self.db.execute("INSERT INTO identity_acceptance VALUES(?,?,?,NULL,'preparing',?,?,NULL)",(id,task['id'],task['stabilityTest']['id'],encoded(payload),self.s.tasks.clock()))
  label='二发正销量真实线索 · 12 QPS 500人上线验收';text='\n'.join(m['handle'] for m in payload['samples']);card=preview('it',label,text);batch=discovery.submit('it',label,text,card['previewHash'],id)
  items={m['handle']:m['id'] for m in discovery.detail(batch['id'])['items']}
  for m in payload['samples']:m['itemId']=items[m['handle']]
  box='acceptance-box-'+digest(id)[:28]
  # Read-only validation context: no rewriting existing source-to-identity bindings.
  with CycleStore(root/'var/second-cycle.sqlite') as cycle:
   with cycle.tx():cycle.db.execute('INSERT OR IGNORE INTO cycle_identity_outbox(id,plan_id,payload,batch_id,settled) VALUES(?,?,?,?,0)',(box,payload['plan'],encoded({'edges':[],'handles':[m['handle'] for m in payload['samples']],'acceptanceId':id}),batch['id']))
  with self.s.tasks.tx():
   self.db.execute('INSERT OR IGNORE INTO batch_source_identity VALUES(?,?)',(task['id'],box))
   self.db.execute("UPDATE identity_acceptance SET batch_id=?,payload=?,state='running' WHERE id=?",(batch['id'],encoded(payload),id))
   self.s.tasks.event(task['id'],'identity_release_validation_started',{'id':id,'uniquePeople':500,'qps':12,'criteria':CRITERIA,'zeroSalesExcluded':True})
  return self.get(id)
 def get(self,id):
  row=self.db.execute('SELECT * FROM identity_acceptance WHERE id=?',(id,)).fetchone()
  if not row:raise ValueError('acceptance_missing')
  return dict(row)|{'payload':json.loads(row['payload'])}
 def assess(self,id,root,discovery):
  record=self.get(id)
  if record['state'] in ('passed','failed'):return json.loads(record['result'])
  samples=record['payload']['samples'];items={i['id']:i for i in discovery.detail(record['batch_id'])['items']};evidence={}
  for m in samples:
   item=items[m['itemId']]
   if item['status'] not in ('completed','unresolved','blocked'):continue
   row=discovery._db.execute('SELECT * FROM discovery_item WHERE id=?',(item['id'],)).fetchone()
   path=Path(root)/'var/creator-discovery'/record['batch_id']/item['id']/f"attempt-{row['attempt_no']}"/'report.private.json'
   evidence[item['id']]={'status':item['status'],'oec':item.get('oecId'),'report':json.loads(path.read_text()) if path.exists() else {}}
  metrics=evaluate(samples,evidence);metrics['elapsedSeconds']=round(self.s.tasks.clock()-record['started'],2)
  with self.s.tasks.tx():
   self.db.execute('UPDATE identity_acceptance SET result=? WHERE id=?',(encoded(metrics),id))
   if metrics['final'] and record['state']=='running':
    self.db.execute('UPDATE identity_acceptance SET state=? WHERE id=?',('passed' if metrics['passed'] else 'failed',id))
    if metrics['passed']:
     self.db.execute('INSERT INTO identity_runtime_policy VALUES(?,?,?,?,?,?) ON CONFLICT(account) DO UPDATE SET qps=excluded.qps,lanes=excluded.lanes,acceptance_id=excluded.acceptance_id,result_hash=excluded.result_hash,published=excluded.published',('acc6',12,9,id,digest(metrics),self.s.tasks.clock()))
     self.s.tasks.event(record['task_id'],'identity_12qps_published',{'acceptanceId':id,'metrics':metrics})
    else:
     self.db.execute("UPDATE identity_soak_run SET state='attention',stop_reason='release_validation_failed' WHERE id=?",(record['soak_id'],))
     self.s.tasks.event(record['task_id'],'identity_release_validation_failed',{'acceptanceId':id,'metrics':metrics})
  return metrics

def production_policy(root,account='acc6',market='it'):
 default={'qps':3,'lanes':3,'acceptanceId':None}
 if market!='it':return default
 path=Path(root)/'var/batch-tasks.sqlite'
 if not path.exists():return default
 with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as c:
  if not c.execute("SELECT 1 FROM sqlite_master WHERE name='identity_runtime_policy'").fetchone():return default
  row=c.execute("SELECT p.qps,p.lanes,p.acceptance_id,p.result_hash,a.result FROM identity_runtime_policy p JOIN identity_acceptance a ON a.id=p.acceptance_id WHERE p.account=? AND a.state='passed'",(account,)).fetchone()
  if not row:return default
  metrics=json.loads(row[4])
  if row[0]!=12 or row[1]!=9 or not metrics.get('passed') or digest(metrics)!=row[3]:raise ValueError('published_identity_policy_invalid')
  return {'qps':12,'lanes':9,'acceptanceId':row[2]}

def acceptance_status(service,task):
 if not service.db.execute("SELECT 1 FROM sqlite_master WHERE name='identity_acceptance'").fetchone():return None
 row=service.db.execute('SELECT id,state,result,started FROM identity_acceptance WHERE task_id=? ORDER BY started DESC LIMIT 1',(task,)).fetchone()
 return {'id':row[0],'state':row[1],'metrics':json.loads(row[2]) if row[2] else None,'startedAt':row[3],'target':500,'qps':12} if row else None
