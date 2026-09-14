"""Task-scoped names, exact current card checks and existing guarded create protocol.

Catalog links are prepared at product level first: a task's TapLink material readiness
depends on the PID plan, never on how many creators the roster has reached.
"""
import importlib.util,json,sqlite3,subprocess,sys,time,uuid
from contextlib import closing
from pathlib import Path
from lib.batch_sources import BatchSources
from lib.batch_task_service import read_local_preparation
from lib.batch_materials import BatchMaterials
from lib.second_cycle import CycleStore,CycleError,digest,assess_offer
from lib.cycle_materials import Materials,name_key
from lib.cycle_card_creation import CardCreation
from lib.product_stock_policy import mark_full_managed

def run_card_process(root,service,task_id,intent_id,action):
 queue=BatchSources(service)
 if action not in ('execute-one','verify-one') or not queue.permitted(task_id):raise CycleError('task_paused')
 folder=root/'var/batch-materials';folder.mkdir(exist_ok=True);path=folder/(uuid.uuid4().hex+'.json')
 child=subprocess.Popen([sys.executable,str(root/'scripts/create-cycle-card.py'),action,'--id',intent_id,'--report',str(path)],cwd=root,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
 deadline=time.monotonic()+180;last_heartbeat=0
 while child.poll() is None:
  if time.monotonic()-last_heartbeat>10:service.heartbeat();last_heartbeat=time.monotonic()
  if not queue.permitted(task_id) or time.monotonic()>deadline:
   child.terminate()
   try:child.wait(timeout=5)
   except subprocess.TimeoutExpired:child.kill();child.wait()
   break
  time.sleep(.2)

def full_managed_pids(root):
 """PIDs proven full-managed by the official high-opportunity "global only" source run."""
 path=Path(root)/'var/global-source.sqlite'
 if not path.exists():return {}
 with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as db:
  db.execute('BEGIN')
  rows=db.execute("SELECT p.pid,r.id FROM global_source_product p JOIN global_source_run r ON r.id=p.run_id WHERE json_extract(r.scope,'$.source')='opportunity_global_only'").fetchall()
 return {str(pid):'opportunity_global_only:'+str(run) for pid,run in rows}

def plan_links(service,task,root,offers):
 """Register the task's product plans for link preparation; no roster and no platform write."""
 BatchMaterials(service)  # durable schema, safe to re-enter
 from lib.catalog_prepare import CatalogPreparation
 prep=CatalogPreparation(root)
 try:
  known={r[0] for r in prep.db.execute('SELECT pid FROM catalog_prepare_item')}
 finally:prep.close()
 rows=[]
 for o in offers:
  if str(o['pid']) not in known:continue
  rows.append({'material_key':digest([o['pid'],o.get('campaignId'),o.get('catalogSource')]),'pid':str(o['pid']),
               'campaign_id':str(o.get('campaignId') or ''),'catalog_source':o.get('catalogSource') or 'selected','offer':o})
 with service.tasks.tx():
  if service.tasks.get(task['id'])['state']!='preparing':return 0
  for r in rows:
   service.db.execute('INSERT OR IGNORE INTO batch_material_link(task_id,material_key,pid,campaign_id,catalog_source,offer,state,updated) VALUES(?,?,?,?,?,?,?,?)',
                      (task['id'],r['material_key'],r['pid'],r['campaign_id'],r['catalog_source'],json.dumps(r['offer'],ensure_ascii=False,sort_keys=True),'pending',service.tasks.clock()))
 return len(rows)

def refresh_link_states(service,task_id,root):
 """Read-only join of task plans against verified catalog links; never creates a link."""
 from lib.catalog_prepare import CatalogPreparation
 prep=CatalogPreparation(root);states={}
 try:
  with service.tasks.tx():
   for r in service.db.execute('SELECT * FROM batch_material_link WHERE task_id=?',(task_id,)):
    offer=json.loads(r['offer']);status=prep.offer_status(offer)
    service.db.execute('UPDATE batch_material_link SET state=?,reason=?,card=?,intent_id=?,updated=? WHERE task_id=? AND material_key=?',
                       (status['state'],status.get('reason'),json.dumps(status['card'],ensure_ascii=False,sort_keys=True) if status.get('card') else None,
                        status.get('intentId'),service.tasks.clock(),task_id,r['material_key']))
    states[r['material_key']]=status
 finally:prep.close()
 return states

def link_status(service,task_id):
 rows=[dict(r) for r in service.db.execute('SELECT state,reason,count(*) AS n FROM batch_material_link WHERE task_id=? GROUP BY state,reason',(task_id,))]
 ready=sum(r['n'] for r in rows if r['state']=='ready');total=sum(r['n'] for r in rows)
 reasons={r['reason']:r['n'] for r in rows if r['state']!='ready' and r['reason']}
 return {'total':total,'ready':ready,'pending':total-ready,'reasons':reasons}

def advance_links(service,task,root,clock=None):
 """Prepare the task's catalog links from product facts only; creator roster is irrelevant here."""
 root=Path(root);queue=BatchSources(service)
 if not queue.permitted(task['id']):return
 with CycleStore(root/'var/second-cycle.sqlite',clock) if clock else CycleStore(root/'var/second-cycle.sqlite') as store:
  plan=store.db.execute('SELECT id FROM plan WHERE institution=? AND market=?',(task['spec']['institution'],task['spec']['market'])).fetchone()
  if not plan:return
  if store._plan(plan[0])['state']!='active':return
  managed=full_managed_pids(root)
  # Full-managed status comes from the official source evidence, not from a missing stock number.
  offers=[mark_full_managed(o,managed[o['pid']]) if o['pid'] in managed else o for _,o in store._offers(plan[0]) if assess_offer(o,store.clock())['eligible']]
 plan_links(service,task,root,offers)
 refresh_link_states(service,task['id'],root)

def advance_materials(service,task,root,clock=None):
 root=Path(root);queue=BatchSources(service)
 if not queue.permitted(task['id']):return
 jobs=BatchMaterials(service)
 # Reconcile task-owned writes even if this product is no longer in the roster.
 with CycleStore(root/'var/second-cycle.sqlite') as pending_store:
  ledger=CardCreation(pending_store)
  for row in service.db.execute('SELECT material_key,intent_id FROM batch_material_job WHERE task_id=? AND intent_id IS NOT NULL',(task['id'],)).fetchall():
   old=ledger.get(row['intent_id'])
   if old['state'] in ('started','response_saved','unknown'):
    run_card_process(root,service,task['id'],old['id'],'verify-one')
    jobs.save(task['id'],row['material_key'],'waiting',error='task_card_creation_unresolved' if ledger.get(old['id'])['state']!='verified' else None)
    return
 # Product-level catalog links advance on their own queue, before any roster threshold.
 advance_links(service,task,root,clock)
 report,members=read_local_preparation(root,task['spec'])
 if report['candidateGap'] or not members:
  # No roster yet: the link queue above still advanced; creator-dependent work waits.
  return
 with CycleStore(root/'var/second-cycle.sqlite',clock) if clock else CycleStore(root/'var/second-cycle.sqlite') as store:
  plan=store.db.execute('SELECT id FROM plan WHERE institution=? AND market=?',(task['spec']['institution'],task['spec']['market'])).fetchone()[0]
  if store._plan(plan)['state']!='active':return
  jobs.plan(task,members,[o for _,o in store._offers(plan)])
  pending=jobs.pending(task['id'],members)
  if not pending:return
  materials=Materials(store);offers=[json.loads(j['offer']) for j in pending]
  from lib.draft_provider import call_model
  try:
   if not queue.permitted(task['id']):return
   materials.prepare_names(list({name_key(o):o for o in offers}.values()),call_model)
  except Exception:
   for j in pending:jobs.save(task['id'],j['material_key'],'waiting',error='product_name_preparation_unresolved')
   return
  from lib.catalog_prepare import CatalogPreparation
  catalog=CatalogPreparation(root)
  try:
   for j,o in zip(pending,offers):
    if not queue.permitted(task['id']):return
    try:
     # One judgement path: offer_status applies the confirmed rule and accepts either a link this
     # project created and verified or an acceptable existing platform link. A reused link's
     # creator share need not equal what a brand-new link would be given.
     status=catalog.offer_status(o)
     if status['state']=='ready':jobs.save(task['id'],j['material_key'],'ready',status['card']);continue
     jobs.save(task['id'],j['material_key'],'waiting',error=status.get('reason') or 'catalog_link_pending')
    except Exception:
     jobs.save(task['id'],j['material_key'],'waiting',error='catalog_link_read_unresolved')
  finally:
   catalog.close()
