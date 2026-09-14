"""Real read-only HTTP and exact identity handoff for task-owned source jobs."""
from contextlib import contextmanager
from pathlib import Path
import fcntl,importlib.util,json,sys
from lib.batch_sources import BatchSources
from lib.batch_task_service import read_local_preparation
from lib.second_cycle import CycleStore,CycleError,encoded
from lib.creator_discovery import CreatorDiscoveryStore
from lib.cycle_identity import IdentityBridge

@contextmanager
def kalodata_provider(root):
 legacy=root.parent/'01-BDSystem-V2';sys.dont_write_bytecode=True
 if str(legacy) not in sys.path:sys.path.insert(0,str(legacy))
 spec=importlib.util.spec_from_file_location('batch_kalodata_http',root/'scripts/second-cycle-worker.py')
 mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
 with (legacy/'data/research/kalodata/.browser.lock').open('rb') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  with mod.HttpProvider() as provider:yield provider

def advance_sources(service,task,root,provider_factory=kalodata_provider):
 root=Path(root);queue=BatchSources(service);id=task['id']
 if not queue.permitted(id):return
 report,_=read_local_preparation(root,task['spec'])
 with CycleStore(root/'var/second-cycle.sqlite') as cycle:
  plan=cycle.db.execute('SELECT id FROM plan WHERE institution=? AND market=?',(task['spec']['institution'],task['spec']['market'])).fetchone()
  if not plan or cycle._plan(plan[0])['state']!='active':return
  plan=plan[0]
  # Scope the handoff to this task's imported source receipts, never all plan edges.
  with CreatorDiscoveryStore(root/'var') as discovery:
   bridge=IdentityBridge(cycle,discovery,root/'var/creator-identities.sqlite')
   ids=[e['sourceId'] for e in queue.edges(id)]
   if ids and queue.permitted(id):
    while queue.permitted(id):
     oid=bridge.freeze(plan,source_ids=ids)
     if not oid:break
     service.db.execute('INSERT OR IGNORE INTO batch_source_identity VALUES(?,?)',(id,oid))
    # Recover a crash after handoff commit but before task registration.
    for r in cycle.db.execute('SELECT DISTINCT outbox_id FROM cycle_identity_handoff WHERE plan_id=? AND source_id IN (SELECT value FROM json_each(?))',(plan,json.dumps(ids))):
     service.db.execute('INSERT OR IGNORE INTO batch_source_identity VALUES(?,?)',(id,r[0]))
    boxes=[r[0] for r in service.db.execute('SELECT outbox_id FROM batch_source_identity WHERE task_id=?',(id,))]
    if queue.permitted(id):bridge.dispatch(plan,outbox_ids=boxes);bridge.reconcile(plan,outbox_ids=boxes)
   # A PID is finished only after every returned handle has a terminal identity result.
   for j in service.db.execute("SELECT id FROM batch_source_job WHERE task_id=? AND state='awaiting_identity'",(id,)).fetchall():
    edge_ids={e['sourceId'] for r in service.db.execute('SELECT payload FROM batch_source_page WHERE job_id=? AND imported=1',(j[0],)) for e in json.loads(r[0])['edges']}
    terminal={r[0] for r in cycle.db.execute("SELECT source_id FROM cycle_identity_resolution WHERE plan_id=? UNION SELECT source_id FROM cycle_identity_outcome WHERE plan_id=? AND status IN ('unresolved','blocked')",(plan,plan))}
    if edge_ids<=terminal:service.db.execute("UPDATE batch_source_job SET state='completed' WHERE id=? AND state='awaiting_identity'",(j[0],))
  report,_=read_local_preparation(root,task['spec'])
  queue.plan(id,[o for _,o in cycle._offers(plan)],report['candidates'])
  if not report['candidateGap']:return
  states=queue.status(id)['states']
  if states.get('blocked') or not (states.get('queued') or states.get('running')):return
  def persist(edges):
   # Identical response replay after a crash is harmless; conflicting facts still fail.
   for e in edges:
    prior=cycle.db.execute('SELECT payload FROM source_edge WHERE plan_id=? AND source_id=?',(plan,e['sourceId'])).fetchone()
    if prior and prior[0]!=encoded(e):raise CycleError('source_receipt_conflict')
   cycle.import_edges(plan,edges)
  try:
   with provider_factory(root) as provider:
    for _ in range(10):
     if not queue.permitted(id):break
     outcome=queue.once(id,provider,persist)
     if outcome['status'] in ('idle','blocked','retry_wait','paused_receipt_saved'):break
  except BlockingIOError:return # Existing reader owns the session; next local tick retries.
