"""Bounded single-account Find cohorts with durable per-target evidence."""
import json,sqlite3,time
from contextlib import closing
from lib.creator_discovery import _hash,_json,CreatorDiscoveryError

def slice_report(report,item):
    targets=[t for t in report.get('targets',[]) if t.get('targetRef')==item['id']]
    requests=[r for r in report.get('requests',[]) if r.get('targetRef')==item['id']]
    if len(targets)>1:raise CreatorDiscoveryError('probe_report_invalid')
    if not requests:return None
    value={**report,'targets':targets,'requests':requests,'cohortEvidenceSha256':_hash(report),
           'cohortStatus':report['status'],'counters':{'request_count':sum(len(r.get('attempts',[])) or 1 for r in requests)}}
    # A later sibling failure does not erase this target's independently verified read.
    if targets and report.get('identityFileUnchanged') is True and targets[0].get('status') in ('identity_verified','unresolved') and all(r.get('status')=='returned' and r.get('httpStatus')==200 and r.get('code')=='0' and r.get('verificationRequired') is False for r in requests):
        value['status']='completed';value.pop('reason',None)
    return value

def run_cohort(worker,limit=20,lanes=3,soak_id=None,only_batch=None,use_production_policy=False,skip_judged=False):
    if type(lanes) is not int or lanes not in (3,6,9):raise CreatorDiscoveryError("invalid_request")
    store=worker.store;store.heartbeat(worker.owner)
    from lib.identity_retry import snapshot,record
    policy=snapshot(store.var_dir.parent,'it',now=store.now())
    if policy['accountWait']:return {'targets':0,'accountWait':policy['accountWait']}
    soak=None;service=None;published=None
    if use_production_policy and not soak_id:
        from lib.identity_acceptance import production_policy
        published=production_policy(store.var_dir.parent)
        if published['acceptanceId']:lanes=published['lanes']
    if soak_id:
        from lib.batch_task_service import TaskService
        from lib.identity_soak import IdentitySoak
        service=TaskService(store.var_dir/'batch-tasks.sqlite');soak=IdentitySoak(service)
        if not soak.allowed(soak_id):
            state=soak.config(soak_id)['state'];service.close();return {'soakState':state,'targets':0}
        lanes=9
    path=store.var_dir/'second-cycle.sqlite'
    if not path.exists():return None
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='cycle_identity_outbox'").fetchone():return None
        batches=[r[0] for r in db.execute("SELECT o.batch_id FROM cycle_identity_outbox o JOIN plan p ON p.id=o.plan_id WHERE p.state='active' AND p.market='it' AND o.batch_id IS NOT NULL AND o.settled=0")]
    if soak_id:
        boxes=[r[0] for r in service.db.execute('SELECT outbox_id FROM batch_source_identity WHERE task_id=?',(soak.config(soak_id)['task_id'],))]
        with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as db:
            scoped={r[0] for r in db.execute('SELECT batch_id FROM cycle_identity_outbox WHERE id IN (SELECT value FROM json_each(?))',(_json(boxes),))}
        batches=[b for b in batches if b in scoped]
    exact={only_batch} if isinstance(only_batch,str) else set(only_batch or [])
    if exact:batches=[b for b in batches if b in exact]
    group=store.recover_cohort(worker.owner,batches)
    if group is None:
        group=store.claim_cohort(worker.owner,batches,limit,skip_judged=skip_judged)
    if not group:
        if service:service.close()
        return {'soakState':'waiting_supply','targets':0} if soak_id else None
    folder=store.output_root/'cohorts'/group['id'];output=folder/'output';target_file=folder/'targets.private.json'
    started=time.monotonic();items=group['items']
    try:
        report=worker._final(output/'report.private.json')
        if report is None and not group.get('recovering'):
            folder.mkdir(parents=True,exist_ok=True,mode=0o700)
            with target_file.open('x') as f:
                target_file.chmod(0o600)
                json.dump({'market':'it','identityOnly':True,'cohortId':group['id'],'httpLanes':lanes,**({'soakRun':soak_id} if soak_id else {}),**({'runtimeAcceptance':published['acceptanceId']} if published and published['acceptanceId'] else {}),'targets':[{'ref':i['id'],'handle':i['handle'],'externalId':i['id']} for i in items]},f)
            worker.executor(target_file,output)
            report=worker._final(output/'report.private.json')
        if report:
            refs={i['id'] for i in items}
            if any(t.get('targetRef') not in refs for t in report.get('targets',[])) or any(r.get('targetRef') not in refs for r in report.get('requests',[])):raise CreatorDiscoveryError('probe_report_invalid')
        for item in items:
            current=store._db.execute('SELECT * FROM discovery_item WHERE id=?',(item['id'],)).fetchone()
            if current['status']!='running' or current['lease_owner']!=worker.owner:continue
            # 取证目录由**库里**的 attempt_no 决定，不用轮次快照里的旧值（恢复时两者会不一致）。
            item=dict(current)
            part=slice_report(report,item) if report else None
            if part is None:
                if not report or report.get('status')!='completed':
                    record(store.var_dir.parent,'it','acc6',item['handle'],_hash(report) if report else group['id'],'shared',(report or {}).get('reason') or 'probe_incomplete',evidence=str(output/'report.private.json'),now=store.now())
                store.defer_busy(item,worker.owner,consume_attempt=False);continue
            _,single,_=worker._paths(item);single.mkdir(parents=True,exist_ok=True,mode=0o700)
            evidence=single/'report.private.json';payload=_json(part)
            if evidence.exists() and evidence.read_text()!=payload:
                # 同一次尝试的取证不该变；变了说明这是**又一次尝试**（重试/恢复），新的 Find 回执本来就
                # 与上一次不同。把旧的一份改名留档（不删、不覆盖），再写新的——直接报错会把重试永远卡死。
                evidence.replace(single/f"report.private.superseded-{int(time.time())}.json")
            if not evidence.exists():evidence.write_text(payload);evidence.chmod(0o600)
            worker._settle(item,part)
        if soak_id and report is None and target_file.exists() and json.loads(target_file.read_text()).get('soakRun')==soak_id:
            incomplete=json.loads((output/'report.private.json').read_text()) if (output/'report.private.json').exists() else {}
            soak.record(soak_id,group['id'],{**incomplete,'soakRun':soak_id,'account':'acc6','qps':12,'status':'incomplete'},[])
        if soak_id and report and report.get('soakRun')==soak_id:
            confirmed=[]
            for item in items:
                r=store._db.execute('SELECT status,oec_id FROM discovery_item WHERE id=?',(item['id'],)).fetchone()
                if r['status']=='completed' and r['oec_id']:confirmed.append(r['oec_id'])
            soak.record(soak_id,group['id'],report,confirmed)
        with store.transaction():store._db.execute("UPDATE discovery_cohort SET state='completed' WHERE id=?",(group['id'],))
        return {'id':group['id'],'recovered':bool(group.get('recovering')),'targets':len(items),'seconds':round(time.monotonic()-started,3),'report':str(output/'report.private.json'),'accountWait':snapshot(store.var_dir.parent,'it',now=store.now())['accountWait']}
    except BaseException:
        # Keep cohort ownership/evidence for recovery; the probe supervisor closes its child.
        raise
    finally:
        if service:service.close()
