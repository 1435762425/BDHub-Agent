"""Task-scoped names, exact current card checks and existing guarded create protocol."""
import importlib.util,json,subprocess,sys,time,uuid
from pathlib import Path
from lib.batch_sources import BatchSources
from lib.batch_task_service import read_local_preparation
from lib.batch_materials import BatchMaterials
from lib.second_cycle import CycleStore,CycleError,digest,assess_offer
from lib.cycle_materials import Materials,name_key
from lib.cycle_card_creation import CardCreation
from lib.cycle_catalog import read_current_offer
from lib.product_stock_policy import full_managed,mark_full_managed

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

def advance_materials(service,task,root):
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
 report,members=read_local_preparation(root,task['spec'])
 if report['candidateGap'] or not members:return # Finish the complete roster before preparing materials.
 with CycleStore(root/'var/second-cycle.sqlite') as store:
  plan=store.db.execute('SELECT id FROM plan WHERE institution=? AND market=?',(task['spec']['institution'],task['spec']['market'])).fetchone()[0]
  if store._plan(plan)['state']!='active':return
  jobs.plan(task,members,[o for _,o in store._offers(plan)])
  pending=jobs.pending(task['id'],members)
  if not pending:return
  materials=Materials(store);ledger=CardCreation(store);offers=[json.loads(j['offer']) for j in pending]
  from lib.draft_provider import call_model
  try:
   if not queue.permitted(task['id']):return
   materials.prepare_names(list({name_key(o):o for o in offers}.values()),call_model)
  except Exception:
   for j in pending:jobs.save(task['id'],j['material_key'],'waiting',error='product_name_preparation_unresolved')
   return
  spec=importlib.util.spec_from_file_location('batch_card_inspection',root/'scripts/prepare-cycle-materials.py');inspection=importlib.util.module_from_spec(spec);spec.loader.exec_module(inspection)
  from lib.global_source_transport import opportunity_reader
  from lib.cycle_catalog import SELECTED,CAMPAIGNS,PRODUCTS
  # Same verified identity and HTTP verification support; no write path in this reader.
  audit={}
  with opportunity_reader(audit,stopped=lambda:not queue.permitted(task['id']),extra_read_endpoints={(inspection.CARD,'GET'),(inspection.MEMBERS,'GET'),(CAMPAIGNS,'GET'),(PRODUCTS,'GET')}) as transport:
   from bdhub.research.catalog_rules import link_rules_for,engine_for
   def request(method,path,extra,body=None):
    service.heartbeat()
    r=transport._xhr(method=method,path=path,params=transport._params()|extra,payload=body,write=False)
    data=transport.require_read(r);return data,digest(data)
   for j,o in zip(pending,offers):
    if not queue.permitted(task['id']):return
    try:
     unresolved=store.db.execute("SELECT id FROM cycle_card_creation WHERE plan_id=? AND pid=? AND state IN ('started','response_saved','unknown')",(plan,o['pid'])).fetchone()
     if unresolved:
      jobs.save(task['id'],j['material_key'],'needs_creation');continue
     rule=link_rules_for('it',o['catalogSource'])[0]
     if digest(rule)!=o.get('commissionRuleFingerprint'):raise CycleError('commission_rule_changed')
     fresh=read_current_offer(o,rule,engine_for(rule).calculate,request,time.time)
     if full_managed(o):fresh=mark_full_managed(fresh,o['managementEvidenceRef'])
     if not assess_offer(fresh,time.time())['eligible'] or fresh['creatorPercent']!=o['creatorPercent']:raise CycleError('task_offer_changed')
     card=inspection.inspect_card(o,lambda path,extra:request('GET',path,extra))
     if card['state']=='verified_read_only':jobs.save(task['id'],j['material_key'],'ready',card);continue
     jobs.save(task['id'],j['material_key'],'needs_creation')
    except Exception as e:
     code=str(e) if isinstance(e,CycleError) and str(e) in ('commission_rule_changed','task_offer_changed') else 'task_card_read_unresolved'
     jobs.save(task['id'],j['material_key'],'waiting',error=code)
  # Release the read identity guard before using the existing guarded writer.
  ledger=CardCreation(store)
  for j,o in zip(pending,offers):
   if not queue.permitted(task['id']):return
   row=service.db.execute('SELECT state FROM batch_material_job WHERE task_id=? AND material_key=?',(task['id'],j['material_key'])).fetchone()
   if row[0]!='needs_creation':continue
   try:
    unresolved=store.db.execute("SELECT id FROM cycle_card_creation WHERE plan_id=? AND pid=? AND state IN ('started','response_saved','unknown')",(plan,o['pid'])).fetchone()
    intent=ledger.get(unresolved[0]) if unresolved else ledger.prepare(plan,o,materials.name(o)['shortNameIt'])
    if intent['state']=='verified':
     jobs.save(task['id'],j['material_key'],'waiting',error='task_card_read_unresolved');continue
    if intent['state']=='invalidated':raise CycleError('task_offer_changed')
    action='execute-one' if intent['state']=='prepared' else 'verify-one'
    jobs.bind_intent(task['id'],j['material_key'],intent['id'])
    run_card_process(root,service,task['id'],intent['id'],action)
    refreshed=ledger.get(intent['id'])
    if refreshed['state']=='verified' and digest(json.loads(refreshed['offer_json']))==digest(o):jobs.save(task['id'],j['material_key'],'ready',json.loads(refreshed['readback']))
    else:jobs.save(task['id'],j['material_key'],'waiting',error='task_card_creation_unresolved')
   except Exception as e:
    jobs.save(task['id'],j['material_key'],'waiting',error='task_offer_changed' if isinstance(e,CycleError) and str(e)=='task_offer_changed' else 'task_card_creation_unresolved')
