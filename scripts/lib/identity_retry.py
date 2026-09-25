"""One durable technical-failure budget per market/normalized handle.

This ledger records execution policy, not identity truth. Exact Find evidence remains
in the existing discovery/identity stores; account faults never become no-match.
"""
import json
import sqlite3
import time
from contextlib import closing
from pathlib import Path

from lib.second_cycle import CycleStore, digest, encoded

MAX_FAILURES = 3
BACKOFF = (300, 1800)
ACCOUNT_BACKOFF = (60, 900, 3600)
SHARED_REASONS = frozenset({'account_not_startable','probe_initialization_or_validation_error',
    'request_or_signer_error','probe_report_invalid','identity_write_failed','worker_interrupted',
    'probe_report_missing','lease_expired_no_report','attempt_exists_without_final','probe_failed'})


def snapshot(root, market, *, now=None):
    now=time.time() if now is None else now
    path=Path(root)/'var/second-cycle.sqlite'
    result={'enabled':False,'isolated':set(),'deferred':set(),'retryAt':{},'accountWait':None}
    if not path.exists():return result
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as db:
        db.row_factory=sqlite3.Row
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='identity_handle_budget'").fetchone():return result
        result['enabled']=True
        for row in db.execute('SELECT handle,failures,next_at FROM identity_handle_budget WHERE market=?',(market,)):
            if row['failures']>=MAX_FAILURES:result['isolated'].add(row['handle'])
            elif row['next_at']>now:result['deferred'].add(row['handle']);result['retryAt'][row['handle']]=row['next_at']
        wait=db.execute('SELECT * FROM identity_account_wait WHERE market=?',(market,)).fetchone()
        if wait:
            generation=db.execute("SELECT generation_id FROM account_identity_generation WHERE market=? AND account=? AND state='published' ORDER BY published_at DESC LIMIT 1",(market,wait['account'])).fetchone()
            current=generation[0] if generation else ''
            from lib.im_session_owner import identity_account
            try:selected_account=identity_account(root,market)
            except (OSError,KeyError,ValueError):selected_account=wait['account']
            if selected_account==wait['account'] and current==wait['generation'] and wait['next_at']>now:
                result['accountWait']=dict(wait)
    return result


def classify(report, target_ref, *, expected_handle=None, market=None):
    """Return individual/shared/success/unattempted using only the target's Find.

    A remote business response or malformed successful Find is target-scoped. A
    signer, login, captcha, HTTP/server or bootstrap failure pauses the account.
    Profile failures after a confirmed Find do not spend its identity budget.
    """
    if report.get('reason') in SHARED_REASONS:
        shared=report.get('reason')
    else:shared=None
    rows=[r for r in report.get('requests',[]) if r.get('targetRef')==target_ref and r.get('stage')=='find']
    if not rows:return ('shared',shared) if shared else ('unattempted',None)
    r=rows[-1]
    if str(r.get('code'))=='16201010':return 'shared','auth_required'
    if str(r.get('code')) in {'98001002','98001004','100000'}:return 'shared','remote_runtime_error'
    if r.get('verificationRequired') is True:return 'shared','verification_required'
    if r.get('status')!='returned' or r.get('httpStatus')!=200:return 'shared',shared or 'transport_unavailable'
    if r.get('verificationRequired') is not False:return 'shared','read_contract_incomplete'
    if str(r.get('code'))!='0':return 'individual','find_business_error'
    target=next((t for t in report.get('targets',[]) if t.get('targetRef')==target_ref),{})
    if target.get('status')=='unresolved' and target.get('reason')=='no_exact_handle':return 'success',None
    if target.get('currentHandleResolved') is True:
        identity=(target.get('find') or {}).get('identity') or {}
        if expected_handle is None or (str(identity.get('handle') or '').lower()==expected_handle.lower()
                and identity.get('market') in (None,market) and str(identity.get('oecId') or '').isdigit()):
            return 'success',None
    return 'individual','find_target_invalid'


def record(root,market,account,handle,event_id,category,reason,*,evidence=None,now=None):
    """Idempotent outcome accounting; exact event IDs survive report replay."""
    now=time.time() if now is None else now;handle=handle.strip().lstrip('@').lower()
    if not snapshot(root,market,now=now)['enabled']:return
    with CycleStore(Path(root)/'var/second-cycle.sqlite') as store,store.tx():
        key=digest([market,event_id,handle])
        if store.db.execute('SELECT 1 FROM identity_retry_event WHERE id=?',(key,)).fetchone():return
        store.db.execute('INSERT INTO identity_retry_event VALUES(?,?,?,?,?,?,?,?)',
                         (key,market,handle,account,category,reason,now,encoded({'evidence':evidence,'eventId':event_id})))
        if category=='individual':
            prior=store.db.execute('SELECT failures FROM identity_handle_budget WHERE market=? AND handle=?',(market,handle)).fetchone()
            count=(prior[0] if prior else 0)+1
            due=now+BACKOFF[min(count-1,len(BACKOFF)-1)] if count<MAX_FAILURES else 0
            store.db.execute('INSERT INTO identity_handle_budget VALUES(?,?,?,?,?,?,?) ON CONFLICT(market,handle) DO UPDATE SET failures=excluded.failures,next_at=excluded.next_at,last_event=excluded.last_event,reason=excluded.reason,updated=excluded.updated',
                             (market,handle,count,due,key,reason,now))
        elif category=='shared':
            generation=store.db.execute("SELECT generation_id FROM account_identity_generation WHERE market=? AND account=? AND state='published' AND published_at<=? ORDER BY published_at DESC LIMIT 1",(market,account,now)).fetchone()
            generation=generation[0] if generation else ''
            # A whole cohort shares one account incident, not one per target.
            old=store.db.execute('SELECT * FROM identity_account_wait WHERE market=?',(market,)).fetchone()
            if old and (old['last_event']==event_id or old['updated']>now):return
            count=old['failures']+1 if old and old['generation']==generation else 1
            store.db.execute('INSERT INTO identity_account_wait VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(market) DO UPDATE SET account=excluded.account,generation=excluded.generation,failures=excluded.failures,next_at=excluded.next_at,last_event=excluded.last_event,reason=excluded.reason,updated=excluded.updated',
                             (market,account,generation,count,now+ACCOUNT_BACKOFF[min(count-1,2)],event_id,reason,now))
        elif category=='success':
            # A later healthy Find resets account escalation, never an individual budget.
            # A healthy sibling must not clear a fault already observed in the same cohort.
            store.db.execute("UPDATE identity_account_wait SET failures=0,next_at=0,reason=NULL,updated=? WHERE market=? AND last_event<>? AND updated<=?",(now,market,event_id,now))


def discovery_outcome(worker,item,report):
    root=worker.store.var_dir.parent
    if not snapshot(root,'it',now=worker.store.now())['enabled']:return
    event=report.get('cohortEvidenceSha256') or digest(report)
    if not hasattr(worker,'_retry_events'):worker._retry_events={}
    worker._retry_events[item['id']]=event
    category,reason=classify(report,item['id'],expected_handle=item['handle'],market='it')
    if category=='success':return  # existing validated settlement is the identity authority
    from lib.im_session_owner import identity_account
    actual=report.get('account','acc6')
    if report.get('schema')!='bdhub.italy-profile-probe.v3' or report.get('market')!='it' or actual not in ('acc6','acc9'):category,reason='shared','probe_report_invalid'
    event=report.get('cohortEvidenceSha256') or digest(report)
    record(root,'it',actual,item['handle'],event,category,reason,evidence='discovery:'+item['id'],now=worker.store.now())


def due_at(root,market,*,now=None):
    now=time.time() if now is None else now;state=snapshot(root,market,now=now)
    if not state['enabled']:return None
    if state['accountWait']:return state['accountWait']['next_at']
    path=Path(root)/'var/second-cycle.sqlite'
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as db:
        row=db.execute("""SELECT min(CASE WHEN b.next_at>? THEN b.next_at ELSE ? END)
          FROM current_identity_source x JOIN plan p ON p.id=x.plan_id
          LEFT JOIN cycle_identity_resolution r ON r.plan_id=x.plan_id AND r.source_id=x.source_id
          LEFT JOIN cycle_identity_outcome o ON o.plan_id=x.plan_id AND o.source_id=x.source_id
          LEFT JOIN identity_handle_budget b ON b.market=p.market AND b.handle=lower(x.source_handle)
          WHERE p.market=? AND p.state='active' AND r.source_id IS NULL
          AND (o.status IS NULL OR o.status NOT IN ('completed','unresolved')) AND coalesce(b.failures,0)<?
          AND NOT EXISTS(SELECT 1 FROM source_edge_index old
            LEFT JOIN cycle_identity_resolution prior ON prior.plan_id=old.plan_id AND prior.source_id=old.source_id
            LEFT JOIN cycle_identity_outcome outcome ON outcome.plan_id=old.plan_id AND outcome.source_id=old.source_id
            WHERE old.plan_id=x.plan_id AND old.source_handle=lower(x.source_handle)
              AND (prior.oec IS NOT NULL OR outcome.status='unresolved'))""",
          (now,now,market,MAX_FAILURES)).fetchone()
        return row[0] if row else None


from contextlib import contextmanager

@contextmanager
def locked(root,market):
    import fcntl
    from lib.second_cycle import CycleError
    path=Path(root)/'var'/('identity-execution-'+market+'.lock');path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise CycleError('identity_executor_busy') from None
        try:yield
        finally:fcntl.flock(lock,fcntl.LOCK_UN)


def project_isolated(root,market):
    """Project the policy into current source outcomes without rewriting identity truth."""
    state=snapshot(root,market)
    if not state['isolated']:return 0
    with CycleStore(Path(root)/'var/second-cycle.sqlite') as store:
        rows=store.db.execute("""SELECT x.plan_id,x.source_id FROM current_identity_source x
          JOIN plan p ON p.id=x.plan_id LEFT JOIN cycle_identity_resolution r USING(plan_id,source_id)
          LEFT JOIN cycle_identity_outcome o USING(plan_id,source_id)
          WHERE p.market=? AND lower(x.source_handle) IN (SELECT value FROM json_each(?))
          AND r.source_id IS NULL AND (o.status IS NULL OR o.status NOT IN ('completed','unresolved','technical_isolated'))""",
          (market,json.dumps(sorted(state['isolated'])))).fetchall()
        for offset in range(0,len(rows),100):
            with store.tx():
                for plan,source in rows[offset:offset+100]:
                    store.db.execute("INSERT INTO cycle_identity_outcome VALUES(?,?,'technical_isolated') ON CONFLICT(plan_id,source_id) DO UPDATE SET status='technical_isolated' WHERE status NOT IN ('completed','unresolved')",(plan,source))
        return len(rows)


def recover_started(root,market,account,apply):
    """Read the original saved probe after a process crash, never create a replacement request first."""
    if not snapshot(root,market)['enabled']:return
    path=Path(root)/'var/second-cycle.sqlite'
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as db:
        rows=db.execute("""SELECT s.handle,s.payload,s.account FROM identity_retry_event s WHERE s.market=? AND s.category='started'
          AND NOT EXISTS(SELECT 1 FROM identity_retry_event f WHERE f.market=s.market AND f.handle=s.handle
            AND json_extract(f.payload,'$.eventId')=substr(json_extract(s.payload,'$.eventId'),1,length(json_extract(s.payload,'$.eventId'))-8))""",(market,)).fetchall()
    groups={}
    for handle,payload,original_account in rows:
        payload=json.loads(payload);groups.setdefault((payload['eventId'][:-8],original_account),[]).append((handle,payload.get('evidence')))
    for (token,original_account),items in groups.items():
        account=original_account
        evidence=items[0][1]
        if not isinstance(evidence,dict):
            for handle,_ in items:record(root,market,account,handle,token,'shared','probe_interrupted')
            continue
        file=Path(root)/evidence['report']
        # Initialization retry used another immutable folder. Prefer its final report.
        retry=Path(root)/evidence['retryReport']
        if retry.exists():file=retry
        if not file.resolve().is_relative_to((Path(root)/'var').resolve()):raise ValueError('identity_evidence_scope_invalid')
        try:
            report=json.loads(file.read_text())
            import inspect
            if 'expected_account' in inspect.signature(apply).parameters:apply(root,market,report,evidence['requested'],expected_account=original_account)
            else:apply(root,market,report,evidence['requested'])
        except (OSError,ValueError,RuntimeError) as error:
            for handle,_ in items:record(root,market,account,handle,token,'shared','probe_interrupted',evidence=str(file))
            continue
        for handle,_ in items:
            ref=next(r['ref'] for r in evidence['requested'] if r['handle']==handle)
            category,reason=classify(report,ref,expected_handle=handle,market=market)
            record(root,market,account,handle,token,category,reason,evidence=str(file))
