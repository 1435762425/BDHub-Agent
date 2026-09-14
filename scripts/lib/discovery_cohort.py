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

def run_cohort(worker,limit=20,lanes=3):
    if type(lanes) is not int or lanes not in (3,6,9):raise CreatorDiscoveryError("invalid_request")
    store=worker.store;store.heartbeat(worker.owner)
    path=store.var_dir/'second-cycle.sqlite'
    if not path.exists():return None
    group=store.recover_cohort(worker.owner)
    if group is None:
        with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name='cycle_identity_outbox'").fetchone():return None
            batches=[r[0] for r in db.execute("SELECT o.batch_id FROM cycle_identity_outbox o JOIN plan p ON p.id=o.plan_id WHERE p.state='active' AND p.market='it' AND o.batch_id IS NOT NULL AND o.settled=0")]
        group=store.claim_cohort(worker.owner,batches,limit)
    if not group:return None
    folder=store.output_root/'cohorts'/group['id'];output=folder/'output';target_file=folder/'targets.private.json'
    started=time.monotonic();items=group['items']
    try:
        report=worker._final(output/'report.private.json')
        if report is None and not group.get('recovering'):
            folder.mkdir(parents=True,exist_ok=True,mode=0o700)
            with target_file.open('x') as f:
                target_file.chmod(0o600)
                json.dump({'market':'it','identityOnly':True,'cohortId':group['id'],'httpLanes':lanes,'targets':[{'ref':i['id'],'handle':i['handle'],'externalId':i['id']} for i in items]},f)
            worker.executor(target_file,output)
            report=worker._final(output/'report.private.json')
        if report:
            refs={i['id'] for i in items}
            if any(t.get('targetRef') not in refs for t in report.get('targets',[])) or any(r.get('targetRef') not in refs for r in report.get('requests',[])):raise CreatorDiscoveryError('probe_report_invalid')
        for item in items:
            current=store._db.execute('SELECT status,lease_owner FROM discovery_item WHERE id=?',(item['id'],)).fetchone()
            if current['status']!='running' or current['lease_owner']!=worker.owner:continue
            part=slice_report(report,item) if report else None
            if part is None:
                store.defer_busy(item,worker.owner);continue
            _,single,_=worker._paths(item);single.mkdir(parents=True,exist_ok=True,mode=0o700)
            evidence=single/'report.private.json';payload=_json(part)
            if evidence.exists() and evidence.read_text()!=payload:raise CreatorDiscoveryError('probe_report_invalid')
            if not evidence.exists():evidence.write_text(payload);evidence.chmod(0o600)
            worker._settle(item,part)
        with store.transaction():store._db.execute("UPDATE discovery_cohort SET state='completed' WHERE id=?",(group['id'],))
        return {'id':group['id'],'recovered':bool(group.get('recovering')),'targets':len(items),'seconds':round(time.monotonic()-started,3),'report':str(output/'report.private.json')}
    except BaseException:
        # Keep cohort ownership/evidence for recovery; the probe supervisor closes its child.
        raise
